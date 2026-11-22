"""Drive a terminal program through a pseudo-terminal.

The classic CLI and the TUI only show their approval prompts and
slash-command replies on a real terminal. This gives them one, types
into it, and reads the screen back as plain text.

Standard library only.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios
import time

from pathlib import Path


ROWS = 50
COLUMNS = 160
READ_CHUNK = 65536
POLL_SECONDS = 0.1

# Escape sequences: colours, cursor moves, mode switches and titles.
ANSI_RE = re.compile(
    r"\x1b\[[0-9;?<>=!]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
    r"|\x1b[()][0-9A-Za-z]"
)

# Programs ask the terminal where the cursor is and wait for a reply.
CURSOR_QUERY = b"\x1b[6n"
CURSOR_REPLY = b"\x1b[1;1R"


class TermError(RuntimeError):
    pass


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text).replace("\r", "")


class Terminal:
    def __init__(self, command: list[str], cwd: Path, log_path: Path):
        self._log = open(log_path, "w")
        self._raw = b""

        self._pid, self._fd = pty.fork()

        if self._pid == 0:
            os.chdir(cwd)
            os.environ["TERM"] = "xterm-256color"
            os.environ["COLUMNS"] = str(COLUMNS)
            os.environ["LINES"] = str(ROWS)
            os.execvp(command[0], command)

        size = struct.pack("HHHH", ROWS, COLUMNS, 0, 0)
        fcntl.ioctl(self._fd, termios.TIOCSWINSZ, size)

    @property
    def text(self) -> str:
        return strip_ansi(self._raw.decode("utf-8", "replace"))

    def _pump(self) -> None:
        ready, _, _ = select.select([self._fd], [], [], POLL_SECONDS)

        if not ready:
            return

        try:
            chunk = os.read(self._fd, READ_CHUNK)
        except OSError:
            return

        if not chunk:
            return

        if CURSOR_QUERY in chunk:
            os.write(self._fd, CURSOR_REPLY)

        self._raw += chunk
        self._log.write(strip_ansi(chunk.decode("utf-8", "replace")))
        self._log.flush()

    def mark(self) -> int:
        """A position in the screen text, to match only what follows."""

        return len(self.text)

    def expect(self, needle: str, timeout: float, since: int = 0) -> str:
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            self._pump()

            # A full-screen program redraws with gaps between words.
            # Matching ignores spacing so both styles are found.
            seen = self.text[since:]

            if squash(needle) in squash(seen):
                return seen

        tail = self.text[since:][-1500:]

        raise TermError(f"did not see {needle!r}. Last output:\n{tail}")

    def settle(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds

        while time.monotonic() < deadline:
            self._pump()

    def type(self, text: str) -> None:
        os.write(self._fd, text.encode())

    def enter(self, text: str) -> None:
        self.type(text)

        # A short gap lets the program finish reading the text before
        # the Enter key, as it would from a person.
        self.settle(0.3)
        self.type("\r")

    def close(self) -> None:
        try:
            os.kill(self._pid, signal.SIGTERM)
            self.settle(0.5)
            os.kill(self._pid, signal.SIGKILL)
        except OSError:
            pass

        try:
            os.waitpid(self._pid, 0)
        except OSError:
            pass

        os.close(self._fd)
        self._log.close()


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)

"""An `ssh` process whose input and output carry one conversation.

Screen sharing talks RFB over such a link, and the file mount talks
SFTP. Both need the same three things: read an exact count of bytes
with a limit on silence, write, and close without leaving a process
behind.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import os
import select
import subprocess
import time


SSH_BINARY = "ssh"

# How long a link may stay silent before it is given up on. A sleeping
# machine never answers and never closes the connection.
READ_SECONDS = 30
READ_CHUNK = 1 << 16
STOP_SECONDS = 2
ERROR_WAIT_SECONDS = 0.5
ERROR_CHARS = 400


class LinkError(Exception):
    """The `ssh` link to a target failed. The message says why."""


class SshLink:
    """An `ssh` process whose input and output are a port on a target."""

    def __init__(self, arguments: list[str], timeout: float = READ_SECONDS):
        self._timeout = timeout

        try:
            self._process = subprocess.Popen(
                [SSH_BINARY, *arguments],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as error:
            raise LinkError(
                f"the `{SSH_BINARY}` program could not be started: {error}"
            ) from None

    def _last_words(self) -> str:
        """What `ssh` itself said, when the link has closed."""

        stream = self._process.stderr

        if stream is None:
            return ""

        try:
            ready, _, _ = select.select(
                [stream],
                [],
                [],
                ERROR_WAIT_SECONDS,
            )
        except (OSError, ValueError):
            return ""

        if not ready:
            return ""

        said = os.read(stream.fileno(), READ_CHUNK)

        return said.decode("utf-8", "replace").strip()[-ERROR_CHARS:]

    def _closed(self) -> LinkError:
        return LinkError(self._last_words() or "the link closed")

    def _wait_for_bytes(self, descriptor: int, wait: float) -> bool:
        # `select` refuses a descriptor past its own limit, which a
        # busy Hermes can reach. That is a failed link, not a crash.
        try:
            ready, _, _ = select.select([descriptor], [], [], wait)
        except (OSError, ValueError) as error:
            raise LinkError(f"the link could not be read: {error}") from None

        return bool(ready)

    def read_exact(self, count: int, until: float | None = None) -> bytes:
        """Read `count` bytes.

        Two limits apply. The link may not stay silent for longer than
        its timeout, however large the read. And when `until` is
        given, the whole read must be done by then.
        """

        # A link closed from this side has no descriptor left to ask.
        try:
            descriptor = self._process.stdout.fileno()
        except ValueError:
            raise LinkError("the link is closed") from None

        quiet_until = time.monotonic() + self._timeout
        parts = []
        missing = count

        while missing:
            now = time.monotonic()

            if now >= quiet_until:
                raise LinkError(
                    f"no answer in {self._timeout:.0f} seconds"
                )

            if until is not None and now >= until:
                raise LinkError("the answer did not finish in time")

            limit = quiet_until if until is None else min(quiet_until, until)

            if not self._wait_for_bytes(descriptor, limit - now):
                continue

            chunk = os.read(descriptor, min(missing, READ_CHUNK))

            if not chunk:
                raise self._closed()

            parts.append(chunk)
            missing -= len(chunk)
            quiet_until = time.monotonic() + self._timeout

        return b"".join(parts)

    def write(self, data: bytes) -> None:
        view = memoryview(data)

        try:
            descriptor = self._process.stdin.fileno()

            while view:
                sent = os.write(descriptor, view)
                view = view[sent:]
        except ValueError:
            raise LinkError("the link is closed") from None
        except OSError:
            raise self._closed() from None

    def close(self) -> None:
        process = self._process

        if process.poll() is None:
            process.terminate()

            try:
                process.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

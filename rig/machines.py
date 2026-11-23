"""The machine that plays the hands: a container, or a real laptop.

Both classes offer the same handful of moves, so each test is written
once and runs against either:

- `start()` and `remove()` set the machine up and clean it up,
- `exec()`, `has()` and `read()` look at it directly, around the
  plugin, so a test can check what really happened there,
- `stop()`, `resume()`, `freeze()` and `thaw()` fake an outage.

The container is stopped and paused for real. A real laptop is never
touched to fake an outage. The rig cuts a relay in front of it.

Standard library only.
"""

from __future__ import annotations

import re
import shlex
import time

from pathlib import Path

from relay import Relay
from shell import LOCALHOST
from shell import must
from shell import run
from shell import wait_for_port


KIND_CONTAINER = "container"
KIND_REAL = "real"

IMAGE = "hermes-hands-rig/hands:dev"
DESK_IMAGE = "hermes-hands-rig/desk:dev"
DESK_BOOT_SECONDS = 3
CONTAINER = "hermes-hands-rig-hands"
CONTAINER_USER = "dev"
CONTAINER_HOME = "/home/dev"
CONTAINER_ROOT = "/home/dev/Development"
CONTAINER_SSH_PORT = 2222
CONTAINER_BOOT_SECONDS = 20

RELAY_PORT = 2223
SSH_PORT = 22
DIRECT_SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
ACCEPTED_KEY_RE = re.compile(r"Server accepts key: (\S+)")
LOGIN_RE = re.compile(r"^([^@\s]+)@([^@\s:]+)$")

# Everything the rig writes on a real laptop goes under this one
# folder in the user's home, and the folder is removed at the end.
REAL_ROOT_NAME = ".cache/hermes-hands-rig"
REAL_SEED_SCRIPT = f"""\
set -eu
root="$HOME/{REAL_ROOT_NAME}"
rm -rf "$root/sample"
mkdir -p "$root/sample"
cd "$root/sample"
printf '# sample\\n\\nA throwaway folder for the hermes-hands rig.\\n' \\
  > README.md
printf 'alpha\\nbravo\\ncharlie\\n' > notes.txt
printf '%s\\n' "$HOME"
uname -n
cd "$root"
pwd -P
"""

# A bare `cd` goes home, and the folder is then named from there. So
# the delete can never be handed a path built from a variable that
# might be empty. Hermes' install scan also refuses a tree that pairs
# a recursive delete with the home variable, and it reads this file.
REAL_REMOVE_SCRIPT = f"cd && rm -rf -- {REAL_ROOT_NAME}"

# What the plugin's own setup step reads, asked for the plain way so
# the two can be compared.
PLAIN_PATH_COMMAND = 'printf "%s\\n" "$PATH"'
INTERACTIVE_PATH_COMMAND = (
    '"${SHELL:-sh}" -ic \'printf "%s\\n" "$PATH"\' 2>/dev/null </dev/null'
)

HANDSTOOL_REPLY = "handstool ok"

# Hermes puts a label ahead of a tool's output, so the PATH line does
# not start the line it is on.
SESSION_PATH_RE = re.compile(r"PATH=(\S+)")


def session_path(text: str) -> list[str]:
    """The PATH a session printed, as a list of folders."""

    match = SESSION_PATH_RE.search(text)

    if match is None:
        return []

    return match.group(1).split(":")


class ContainerHands:
    """A Debian container with sshd, standing in for the laptop."""

    kind = KIND_CONTAINER
    label = "the hands container"
    results_prefix = ""
    has_systemd = False
    has_screen = False
    image = IMAGE
    quote_path = True

    user = CONTAINER_USER
    home = CONTAINER_HOME
    root = CONTAINER_ROOT
    sample_dir = f"{CONTAINER_ROOT}/sample"
    ssh_host = LOCALHOST
    ssh_port = CONTAINER_SSH_PORT
    path_claim = "a tool that only .zshrc adds to PATH is found"

    def __init__(self, build_dir: Path, keys_dir: Path):
        self.build_dir = build_dir
        self.keys_dir = keys_dir
        self.key = keys_dir / "id"
        self.known_hosts = keys_dir / "known_hosts"
        self.node = ""

    def plan(self) -> tuple[str, ...]:
        return (
            "build the hands container image",
            "start the hands container with a fresh key pair",
        )

    def scrub_pairs(self) -> list[tuple[str, str]]:
        return []

    def exec(self, command: str) -> str:
        return must([
            "docker", "exec", "--user", self.user, CONTAINER,
            "sh", "-c", command,
        ])

    def has(self, path: str) -> bool:
        done = run(["docker", "exec", CONTAINER, "test", "-e", path])

        return done.returncode == 0

    def read(self, path: str) -> str | None:
        done = run([
            "docker", "exec", "--user", self.user, CONTAINER,
            "cat", path,
        ])

        if done.returncode != 0:
            return None

        return done.stdout

    def path_verdict(self, text: str) -> tuple[bool, str]:
        return HANDSTOOL_REPLY in text, ""

    def _make_keys(self) -> str:
        # A fresh pair and an empty known_hosts for each run. The
        # container gets new host keys each time it is created.
        public = self.keys_dir / "id.pub"

        for stale in (self.key, public, self.known_hosts):
            stale.unlink(missing_ok=True)

        must([
            "ssh-keygen", "-q", "-t", "ed25519", "-N", "",
            "-C", "hermes-hands-rig", "-f", str(self.key),
        ])

        return public.read_text().strip()

    def build(self) -> None:
        must(["docker", "build", "--quiet", "--tag", IMAGE,
              str(self.build_dir)])

    def start(self) -> None:
        public_key = self._make_keys()

        self.build()

        run(["docker", "rm", "--force", CONTAINER])

        must([
            "docker", "run", "--detach",
            "--name", CONTAINER,
            "--publish", f"{LOCALHOST}:{self.ssh_port}:22",
            "--env", f"HANDS_LOGIN_KEY={public_key}",
            self.image,
        ])

        wait_for_port(self.ssh_port, CONTAINER_BOOT_SECONDS)

        # sshd accepts the port a moment before the home is seeded.
        time.sleep(1)

        self.node = self.exec("uname -n").strip()

    def stop(self) -> None:
        must(["docker", "stop", "--time", "1", CONTAINER])

    def resume(self) -> None:
        must(["docker", "start", CONTAINER])
        wait_for_port(self.ssh_port, CONTAINER_BOOT_SECONDS)

    def freeze(self) -> None:
        # A paused container keeps its port open and answers nothing,
        # which is how a sleeping laptop looks from the network.
        must(["docker", "pause", CONTAINER])

    def thaw(self) -> None:
        # Also called to tidy up after a failed test, when the
        # container may not be paused at all.
        run(["docker", "unpause", CONTAINER])

    def remove(self) -> None:
        run(["docker", "rm", "--force", CONTAINER])


class DeskHands(ContainerHands):
    """The hands container with a desktop nobody looks at.

    It has an X display, a terminal, a VNC server on loopback and
    Hermes' own screen driver, so screen control can be tested on a
    real screen without driving a person's.
    """

    label = "the desk container"
    results_prefix = "desk-"
    has_screen = True
    image = DESK_IMAGE

    def __init__(self, build_dir: Path, keys_dir: Path, desk_dir: Path):
        super().__init__(build_dir, keys_dir)

        self.desk_dir = desk_dir

    def plan(self) -> tuple[str, ...]:
        return (
            "build the hands container image, then the desk image on it",
            "start the desk container with a fresh key pair",
        )

    def build(self) -> None:
        super().build()

        must(["docker", "build", "--quiet", "--tag", DESK_IMAGE,
              str(self.desk_dir)])

    def start(self) -> None:
        super().start()

        # The desktop comes up a moment after sshd does.
        time.sleep(DESK_BOOT_SECONDS)


class RealHands:
    """A real machine, reached as the user's own SSH setup reaches it.

    The plugin is pointed at a relay on this host, not at the machine
    itself, so the rig can cut the link. The rig's own looks at the
    machine go straight to it.
    """

    kind = KIND_REAL
    results_prefix = "real-"
    has_systemd = True
    has_screen = False
    quote_path = False

    ssh_host = LOCALHOST
    ssh_port = RELAY_PORT
    path_claim = "PATH has every folder the interactive shell adds"

    def __init__(self, login: str, keys_dir: Path):
        match = LOGIN_RE.match(login)

        if match is None:
            raise SystemExit(f"--real wants USER@HOST, not {login!r}.")

        self.login = login
        self.label = "a real machine over SSH"
        self.user = match.group(1)
        self.host = match.group(2)
        self.known_hosts = keys_dir / "known_hosts"
        self.key = Path()
        self.home = ""
        self.root = ""
        self.sample_dir = ""
        self.node = ""
        self.relay: Relay | None = None

    def plan(self) -> tuple[str, ...]:
        return (
            f"log in to {self.login} and seed ~/{REAL_ROOT_NAME} there",
            f"start a relay on port {RELAY_PORT} in front of its SSH port",
        )

    def scrub_pairs(self) -> list[tuple[str, str]]:
        return [
            (self.root, "<laptop-rig>"),
            (self.home, "<laptop-home>"),
            (self.node, "<laptop>"),
            (self.host, "<laptop>"),
            (self.user, "<laptop-user>"),
        ]

    def _ssh(self, *extra: str) -> list[str]:
        return ["ssh", *DIRECT_SSH_OPTIONS, *extra, self.login]

    def exec(self, command: str) -> str:
        # The login shell may not be POSIX. `sh` always is.
        return must([*self._ssh(), "sh -c " + shlex.quote(command)])

    def has(self, path: str) -> bool:
        done = run([*self._ssh(), "test -e " + shlex.quote(path)])

        return done.returncode == 0

    def read(self, path: str) -> str | None:
        done = run([*self._ssh(), "cat " + shlex.quote(path)])

        if done.returncode != 0:
            return None

        return done.stdout

    def path_verdict(self, text: str) -> tuple[bool, str]:
        plain = self.exec(PLAIN_PATH_COMMAND).strip().split(":")
        lines = self.exec(INTERACTIVE_PATH_COMMAND).strip().splitlines()
        interactive = lines[-1].split(":") if lines else []

        added = [folder for folder in interactive if folder not in plain]
        seen = session_path(text)
        missing = [folder for folder in added if folder not in seen]

        if not added:
            return True, "this machine's interactive shell adds nothing"

        detail = (
            f"the interactive shell adds {len(added)} folders; "
            f"{len(added) - len(missing)} of them are in the session"
        )

        return not missing, detail

    def _accepted_key(self) -> Path:
        """The key file the user's own SSH setup logs in with.

        The plugin is pointed at the relay's address, where the
        user's `Host` block does not match. So the key is named.
        """

        done = run([*self._ssh("-v"), "true"])
        match = ACCEPTED_KEY_RE.search(done.stderr)

        if done.returncode != 0:
            raise RuntimeError(
                f"cannot log in to {self.login} without a prompt:\n"
                f"{done.stderr[-400:]}"
            )

        if match is None:
            raise RuntimeError(
                f"could not tell which key logs in to {self.login}."
            )

        return Path(match.group(1))

    def _upstream(self) -> tuple[str, int]:
        """Where the user's SSH setup really connects for this host."""

        found = {"hostname": self.host, "port": str(SSH_PORT)}

        for line in must(["ssh", "-G", self.login]).splitlines():
            name, _, value = line.partition(" ")

            if name in found:
                found[name] = value

            if name == "proxyjump" and value != "none":
                raise RuntimeError(
                    "the rig's relay cannot follow a ProxyJump."
                )

        return found["hostname"], int(found["port"])

    def start(self) -> None:
        self.known_hosts.unlink(missing_ok=True)
        self.key = self._accepted_key()

        seeded = self.exec(REAL_SEED_SCRIPT).strip().splitlines()
        self.home, self.node, self.root = seeded[-3:]
        self.sample_dir = f"{self.root}/sample"

        host, port = self._upstream()
        self.relay = Relay(RELAY_PORT, host, port)
        self.relay.open()

    def stop(self) -> None:
        self.relay.close()

    def resume(self) -> None:
        self.relay.open()

    def freeze(self) -> None:
        self.relay.freeze()

    def thaw(self) -> None:
        self.relay.thaw()

    def remove(self) -> None:
        if self.relay is not None:
            self.relay.close()

        run([*self._ssh(), "sh -c " + shlex.quote(REAL_REMOVE_SCRIPT)])

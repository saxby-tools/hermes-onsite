"""Shell text for an SSH target: options, setup and command wrappers.

Three workstation problems are handled here, and each is a string
transform so it can be tested without a network:

- A non-interactive SSH shell skips the file where many people build
  PATH. The interactive shell's PATH is read once per connection.
- A sleeping laptop leaves a connection hanging. Keepalives make the
  client give up within seconds.
- A closing lid can cut a write in half. A short sleep inhibitor is
  held while a command runs, where the target allows one.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import shlex

from dataclasses import dataclass

from .config import DEFAULT_SSH_PORT
from .config import PATH_MODE_INTERACTIVE
from .config import TargetSpec


# Three missed keepalives one second apart: a dead peer is noticed in
# about four seconds instead of at the command's own timeout. The
# acceptance bar is five.
KEEPALIVE_INTERVAL = 1
KEEPALIVE_COUNT = 3

# A server or a driver that talks over the link for a whole
# session. A dead link is noticed in 45 seconds.
STDIO_ALIVE_INTERVAL = 15
STDIO_ALIVE_COUNT = 3

INHIBIT_NONE = "none"
INHIBIT_BLOCK = "block"
INHIBIT_DELAY = "delay"
INHIBIT_CAFFEINATE = "caffeinate"
VALID_INHIBIT_MODES = (
    INHIBIT_NONE,
    INHIBIT_BLOCK,
    INHIBIT_DELAY,
    INHIBIT_CAFFEINATE,
)

INHIBIT_WHO = "onsite"
INHIBIT_WHY = "An onsite command is running"

PATH_BEGIN = "__ONSITE_PATH_BEGIN__"
PATH_END = "__ONSITE_PATH_END__"
INHIBIT_MARK = "__ONSITE_INHIBIT__="

# An interactive shell may print a greeting before the PATH line.
# Everything between the markers is kept and the last line is used.
PATH_SCRIPT = (
    f"printf '%s\\n' {PATH_BEGIN}\n"
    '"${SHELL:-sh}" -ic \'printf "%s\\n" "$PATH"\' 2>/dev/null </dev/null\n'
    f"printf '%s\\n' {PATH_END}\n"
)

QUIET_PATH_MARK = "__ONSITE_QUIET_PATH__"

# Many people build PATH in a file only an interactive shell reads,
# so `npx` or `uvx` would not be found. The interactive shell is asked
# for PATH with its output captured and its input closed. Nothing it
# prints can reach the server's own input or output.
QUIET_PATH_LINES = (
    "p=$(\"${SHELL:-sh}\" -ic "
    f"'printf \"\\n{QUIET_PATH_MARK}%s\\n\" \"$PATH\"' 2>/dev/null </dev/null "
    f"| sed -n 's/^{QUIET_PATH_MARK}//p' | tail -n 1); "
    '[ -n "$p" ] && PATH="$p" && export PATH; '
)

# A remote login may only take a delay lock, or none. Each mode is
# tried once here, so a command is never wrapped in a lock that the
# target will refuse.
INHIBIT_SCRIPT = (
    "if command -v systemd-inhibit >/dev/null 2>&1; then\n"
    "  if systemd-inhibit --what=sleep --mode=block true "
    ">/dev/null 2>&1; then\n"
    f"    echo {INHIBIT_MARK}{INHIBIT_BLOCK}\n"
    "  elif systemd-inhibit --what=sleep --mode=delay true "
    ">/dev/null 2>&1; then\n"
    f"    echo {INHIBIT_MARK}{INHIBIT_DELAY}\n"
    "  else\n"
    f"    echo {INHIBIT_MARK}{INHIBIT_NONE}\n"
    "  fi\n"
    "elif command -v caffeinate >/dev/null 2>&1; then\n"
    f"  echo {INHIBIT_MARK}{INHIBIT_CAFFEINATE}\n"
    "else\n"
    f"  echo {INHIBIT_MARK}{INHIBIT_NONE}\n"
    "fi\n"
)


@dataclass(frozen=True)
class Setup:
    """What one look at a fresh connection found."""

    path: str = ""
    inhibit: str = INHIBIT_NONE


def build_ssh_options(spec: TargetSpec) -> list[str]:
    """Options placed ahead of Hermes' own.

    OpenSSH keeps the first value it sees for an option, so these win
    over the longer connect timeout Hermes adds later.
    """

    options = [
        "-o", f"ConnectTimeout={spec.connect_timeout}",
        "-o", f"ServerAliveInterval={KEEPALIVE_INTERVAL}",
        "-o", f"ServerAliveCountMax={KEEPALIVE_COUNT}",
    ]

    if spec.known_hosts:
        options += ["-o", f"UserKnownHostsFile={spec.known_hosts}"]

    if spec.host_key_alias:
        options += ["-o", f"HostKeyAlias={spec.host_key_alias}"]

    return options


def build_stdio_arguments(spec: TargetSpec, script: str) -> list[str]:
    """Everything after `ssh` for a program that talks over the link.

    The program's input and output are the link itself, so nothing
    else may write to it: no terminal, no prompt, no warning line.
    """

    arguments = [
        "-T",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={spec.connect_timeout}",
        "-o", f"ServerAliveInterval={STDIO_ALIVE_INTERVAL}",
        "-o", f"ServerAliveCountMax={STDIO_ALIVE_COUNT}",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "LogLevel=ERROR",
    ]

    if spec.port != DEFAULT_SSH_PORT:
        arguments += ["-p", str(spec.port)]

    if spec.key:
        arguments += ["-i", spec.key]

    if spec.known_hosts:
        arguments += ["-o", f"UserKnownHostsFile={spec.known_hosts}"]

    if spec.host_key_alias:
        arguments += ["-o", f"HostKeyAlias={spec.host_key_alias}"]

    # The login shell on a target may not be POSIX. `sh` always is.
    arguments += [f"{spec.user}@{spec.host}", "sh -c " + shlex.quote(script)]

    return arguments


def build_setup_script(spec: TargetSpec) -> str:
    parts = []

    if spec.path_mode == PATH_MODE_INTERACTIVE:
        parts.append(PATH_SCRIPT)

    if spec.inhibit_sleep:
        parts.append(INHIBIT_SCRIPT)

    if not parts:
        return ""

    # The user's login shell may not be POSIX. `sh` always is.
    return "sh -c " + shlex.quote("".join(parts))


def looks_like_path(value: str) -> bool:
    if not value:
        return False

    if "\n" in value:
        return False

    return value.startswith("/")


def parse_path(output: str) -> str:
    begin = output.find(PATH_BEGIN)
    end = output.rfind(PATH_END)

    if begin < 0:
        return ""

    if end <= begin:
        return ""

    body = output[begin + len(PATH_BEGIN):end]
    lines = [line.strip() for line in body.splitlines() if line.strip()]

    if not lines:
        return ""

    candidate = lines[-1]

    if not looks_like_path(candidate):
        return ""

    return candidate


def parse_inhibit(output: str) -> str:
    for line in output.splitlines():
        if not line.startswith(INHIBIT_MARK):
            continue

        mode = line[len(INHIBIT_MARK):].strip()

        if mode in VALID_INHIBIT_MODES:
            return mode

    return INHIBIT_NONE


def parse_setup(output: str) -> Setup:
    return Setup(path=parse_path(output), inhibit=parse_inhibit(output))


def prefix_path(script: str, path: str) -> str:
    """Set PATH ahead of a login script.

    Hermes snapshots the login shell's exports once and replays them
    for every later command. Setting PATH here puts the borrowed value
    in that snapshot, so it is not set again per command and a later
    `export PATH=...` by the agent still sticks.
    """

    if not path:
        return script

    return f"export PATH={shlex.quote(path)}\n{script}"


def wrap_with_inhibitor(script: str, mode: str) -> str:
    """Run a script under a sleep inhibitor that ends when it does."""

    if mode == INHIBIT_CAFFEINATE:
        return f"exec caffeinate -i bash -c {shlex.quote(script)}"

    if mode not in (INHIBIT_BLOCK, INHIBIT_DELAY):
        return script

    return (
        "exec systemd-inhibit"
        " --what=sleep"
        f" --mode={mode}"
        f" --who={shlex.quote(INHIBIT_WHO)}"
        f" --why={shlex.quote(INHIBIT_WHY)}"
        f" bash -c {shlex.quote(script)}"
    )

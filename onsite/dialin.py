"""A computer the server cannot reach, which dials in to it.

Some computers cannot be reached from the server: a laptop behind a
home router, or on a network that blocks incoming connections. Such a
computer can still open one SSH connection out to the server and
offer its own SSH server back through it. OpenSSH calls that a remote
forward. The server then reaches the computer at a port on its own
loopback address, and everything else in the plugin works as it does
for a computer the server dials.

The plugin adds no listener. The server's own SSH server opens the
port, on loopback only, for as long as the computer holds its
connection. Nothing is installed on the computer: the person runs the
`ssh` program it already has, with the one command built here.

This module does three things. It picks a port for each computer. It
builds the text a person runs or saves on the computer. And it turns
what `ssh` says about a link that is down into plain words.

It imports nothing from Hermes and nothing from the rest of the
plugin, so the settings and the health check can both use it.
"""

from __future__ import annotations

import os
import random
import re
import shlex
import socket
import subprocess

from dataclasses import dataclass
from typing import Any
from typing import Callable
from typing import Mapping
from xml.sax.saxutils import escape


LOOPBACK = "127.0.0.1"
KIND_SSH = "ssh"
SSH_PORT = 22

# The ports handed out. They sit below the range a system gives to
# its own outgoing connections (32768 and up on Linux, 49152 and up
# on macOS), so a busy server does not take one by chance. The first
# 1024 ports need root and are never used.
PORT_FIRST = 20000
PORT_LAST = 29999
MIN_PORT = 1024
MAX_PORT = 65535

# How often a free port is drawn before giving up, and how long one
# look at a port may take.
PICK_TRIES = 64
FREE_CHECK_SECONDS = 0.25

# The computer notices a dead link in 45 seconds and stops, so that
# whatever keeps the link running can dial again.
ALIVE_INTERVAL = 15
ALIVE_COUNT = 3
RETRY_SECONDS = 5

# How long one try at reaching the server may take. Without it a
# computer on a network that drops packets waits two minutes a try.
CONNECT_SECONDS = 10

SYSTEM_LINUX = "linux"
SYSTEM_MAC = "macos"
SYSTEMS = (SYSTEM_LINUX, SYSTEM_MAC)

SERVICE_NAME = "onsite-dial-in"
UNIT_FILE = f"{SERVICE_NAME}.service"
UNIT_PATH = f"~/.config/systemd/user/{UNIT_FILE}"
AGENT_LABEL = "io.github.saxby-tools.onsite.dial-in"
AGENT_PATH = f"~/Library/LaunchAgents/{AGENT_LABEL}.plist"

# A person's own account name and server address, and nothing a shell
# or `ssh` would read as anything else. The text is pasted into a
# terminal, so it is held to this shape and not merely quoted.
SERVER_RE = re.compile(
    r"(?:(?P<user>[A-Za-z0-9_][A-Za-z0-9._-]{0,63})@)?"
    r"(?P<host>[A-Za-z0-9][A-Za-z0-9._-]{0,252})"
    r"(?::(?P<port>[0-9]{1,5}))?"
)
SERVER_SHAPE = (
    "the server is named as USER@ADDRESS, with letters, digits, dots "
    "and hyphens only"
)

UNIT_TEMPLATE = """\
[Unit]
Description=onsite: keep this computer connected to {server}

[Service]
ExecStart={command}
Restart=always
RestartSec={retry}

[Install]
WantedBy=default.target
"""

AGENT_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{arguments}
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
"""

LINGER_NOTE = (
    "It runs while you are logged in to that computer. To keep it up "
    "after you log out, run `loginctl enable-linger` there once."
)
AGENT_NOTE = (
    "It runs while you are logged in to that computer, and starts "
    "again by itself ten seconds after the link drops."
)

# Where the two systems keep the programs the saved files start. A
# service has no PATH to look `ssh` up in.
ENV_PROGRAM = "/usr/bin/env"
MAC_SSH_PROGRAM = "/usr/bin/ssh"

KEYGEN = "ssh-keygen"
KEYGEN_SECONDS = 5
KEY_COMMENT = "#"

# What `ssh` says when a login through the forwarded port fails, and
# what that means for a computer that dials in. Most were read off a
# real run. They are looked for in this order, and the first found
# wins: a key that does not match is said even when other words are
# in the same text.
SIGNS_OTHER_KEY = (
    "host key verification failed",
    "host identification has changed",
)
SIGN_REFUSED = "connection refused"
# With the bracket: the SSH server's own refusal of a login. The bare
# words are also what a firewall says, or a program that may not run.
SIGN_KEY_REFUSED = "permission denied ("
SIGNS_SILENT = ("timed out", "no answer in", "not responding")
# The port took the connection and no SSH server spoke on it.
SIGNS_NO_SSH_BEHIND = ("kex_exchange_identification", "banner exchange")
# A link that was up and went while it was in use.
SIGNS_DROPPED = (
    "closed by remote host",
    "broken pipe",
    "connection reset",
    "connection closed",
)


class DialInError(ValueError):
    """Something about a dial-in computer is not usable. The message
    says what."""


@dataclass(frozen=True)
class Server:
    """The server, as the computer logs in to it.

    The account is empty when the person left it out. Their own SSH
    setup on the computer then names it, as it does when they type
    `ssh server`.
    """

    user: str
    host: str
    port: int = SSH_PORT

    @property
    def login(self) -> str:
        """The word `ssh` is given to say where to log in."""

        if not self.user:
            return self.host

        return f"{self.user}@{self.host}"


def parse_server(text: Any) -> Server:
    """`you@server.example`, `you@server.example:2222` or `server`."""

    if not isinstance(text, str):
        raise DialInError(SERVER_SHAPE)

    found = SERVER_RE.fullmatch(text.strip())

    if found is None:
        raise DialInError(f"{SERVER_SHAPE}, not `{text.strip()}`")

    port = int(found.group("port") or SSH_PORT)

    if port < 1:
        raise DialInError("the server's port must be between 1 and 65535")

    if port > MAX_PORT:
        raise DialInError("the server's port must be between 1 and 65535")

    return Server(found.group("user") or "", found.group("host"), port)


def format_server(server: Server) -> str:
    if server.port != SSH_PORT:
        return f"{server.login}:{server.port}"

    return server.login


def check_port(port: Any) -> int:
    """A port a computer may dial in on, or an error that says why
    not."""

    # `True` is a number to Python and not to a person.
    if isinstance(port, bool):
        raise DialInError("the port must be a number")

    if not isinstance(port, int):
        raise DialInError("the port must be a number")

    if port < MIN_PORT:
        raise DialInError(f"the port must be {MIN_PORT} or higher")

    if port > MAX_PORT:
        raise DialInError(f"the port must be {MAX_PORT} or lower")

    return port


def port_is_free(port: int) -> bool:
    """Whether nothing on the server answers on a loopback port now.

    The port is asked, not taken: no listener is opened here.
    """

    try:
        found = socket.create_connection(
            (LOOPBACK, port),
            FREE_CHECK_SECONDS,
        )
    except ConnectionRefusedError:
        return True
    except OSError:
        # Something is there, or the answer is not clear. Either way
        # the port is not handed out.
        return False

    found.close()

    return False


def draw_port() -> int:
    return random.SystemRandom().randint(PORT_FIRST, PORT_LAST)


def pick_port(
    taken: set[int],
    is_free: Callable[[int], bool] | None = None,
    draw: Callable[[], int] | None = None,
) -> int:
    """A port no other target has and nothing answers on.

    It is drawn at random. A fixed first port would be the same on
    every server, and two profiles on one server cannot read each
    other's settings to stay apart.
    """

    is_free = is_free or port_is_free
    draw = draw or draw_port

    for _ in range(PICK_TRIES):
        port = draw()

        if port in taken:
            continue

        if not is_free(port):
            continue

        return port

    raise DialInError(
        f"no free port was found between {PORT_FIRST} and {PORT_LAST}. "
        "Name one yourself."
    )


def read_port(block: Any) -> int | None:
    if not isinstance(block, Mapping):
        return None

    port = block.get("port")

    if isinstance(port, bool):
        return None

    if not isinstance(port, int):
        return None

    return port


def used_ports(targets: Mapping[str, Any], skip: str) -> set[int]:
    """The ports the other targets in a settings block name."""

    ports = set()

    for name, block in targets.items():
        port = read_port(block)

        if name == skip:
            continue

        if port is None:
            continue

        ports.add(port)

    return ports


def saved_port(targets: Mapping[str, Any], name: str) -> int | None:
    """The port a target already dials in on, if it does."""

    block = targets.get(name)

    if not isinstance(block, Mapping):
        return None

    if not block.get("dial_in"):
        return None

    return read_port(block)


def complete(
    name: str,
    block: Mapping[str, Any],
    targets: Mapping[str, Any],
    pick: Callable[[set[int]], int] | None = None,
) -> dict[str, Any]:
    """A dial-in target's settings, with what the plugin decides.

    The person gives the account on the computer and how the computer
    reaches the server. The plugin adds the loopback address and a
    port. A port the block names is kept, and so is the one the
    target was saved with: the computer's own command names it, and a
    new one would cut the link.
    """

    # The two the plugin decides come first, so the block reads in
    # the order of any other target's when it is written out.
    done: dict[str, Any] = {"kind": KIND_SSH, "host": LOOPBACK}

    done.update(block)

    done["kind"] = KIND_SSH
    done["host"] = LOOPBACK

    if read_port(done) is not None:
        return done

    # A port that is there and is not a number is a slip of the hand.
    # Picking another in its place would change, without a word, the
    # port the computer's own command names.
    if done.get("port") is not None:
        raise DialInError("the port must be a number")

    kept = saved_port(targets, name)

    if kept is not None:
        done["port"] = kept
        return done

    pick = pick or pick_port
    done["port"] = pick(used_ports(targets, name))

    return done


def link_arguments(
    server: Server,
    port: int,
    unattended: bool = False,
) -> list[str]:
    """The `ssh` call the computer runs to offer itself to the server.

    `ExitOnForwardFailure` stops a link that could not open its port.
    Without it `ssh` stays up with no forward and looks healthy.
    `ControlPath=none` gives the link a connection of its own. A
    person whose SSH setup shares connections would otherwise hand
    the forward to another `ssh` and this one would end at once.
    """

    arguments = ["ssh", "-N"]

    # Something that keeps the link running has nobody to ask.
    if unattended:
        arguments += ["-o", "BatchMode=yes"]

    arguments += [
        "-o", "ExitOnForwardFailure=yes",
        "-o", f"ConnectTimeout={CONNECT_SECONDS}",
        "-o", f"ServerAliveInterval={ALIVE_INTERVAL}",
        "-o", f"ServerAliveCountMax={ALIVE_COUNT}",
        "-o", "ControlPath=none",
        "-R", f"{LOOPBACK}:{check_port(port)}:{LOOPBACK}:{SSH_PORT}",
    ]

    if server.port != SSH_PORT:
        arguments += ["-p", str(server.port)]

    arguments.append(server.login)

    return arguments


def link_command(server: Server, port: int) -> str:
    """The one line a person runs on the computer."""

    return shlex.join(link_arguments(server, port))


def key_options(port: int) -> str:
    """What to put in front of the computer's key on the server, so
    that key can hold the link and do nothing else.

    `restrict` takes everything away. Forwarding is given back for
    this one port: to listen on it, and to connect to it and nowhere
    else. The forced command ends any attempt to run something. Each
    part was tried against OpenSSH 10.
    """

    spot = f"{LOOPBACK}:{check_port(port)}"

    return (
        "restrict,port-forwarding,"
        f'permitlisten="{spot}",permitopen="{spot}",'
        'command="exit 1"'
    )


def systemd_unit(server: Server, port: int) -> str:
    """A user service that keeps the link up on a Linux computer."""

    arguments = link_arguments(server, port, unattended=True)

    return UNIT_TEMPLATE.format(
        server=server.host,
        command=shlex.join([ENV_PROGRAM, *arguments]),
        retry=RETRY_SECONDS,
    )


def launchd_agent(server: Server, port: int) -> str:
    """A launch agent that keeps the link up on a Mac."""

    arguments = link_arguments(server, port, unattended=True)
    arguments[0] = MAC_SSH_PROGRAM

    lines = [f"    <string>{escape(word)}</string>" for word in arguments]

    return AGENT_TEMPLATE.format(
        label=AGENT_LABEL,
        arguments="\n".join(lines),
    )


def keep_running(system: str, server: Server, port: int) -> dict[str, Any]:
    """What a person saves and runs to keep the link up across
    restarts of the computer. The plugin never does it for them."""

    if system == SYSTEM_LINUX:
        return {
            "system": system,
            "path": UNIT_PATH,
            "text": systemd_unit(server, port),
            "start": [
                "systemctl --user daemon-reload",
                f"systemctl --user enable --now {UNIT_FILE}",
            ],
            "stop": [f"systemctl --user disable --now {UNIT_FILE}"],
            "note": LINGER_NOTE,
        }

    if system == SYSTEM_MAC:
        return {
            "system": system,
            "path": AGENT_PATH,
            "text": launchd_agent(server, port),
            "start": [f"launchctl bootstrap gui/$(id -u) {AGENT_PATH}"],
            "stop": [f"launchctl bootout gui/$(id -u)/{AGENT_LABEL}"],
            "note": AGENT_NOTE,
        }

    choices = ", ".join(SYSTEMS)

    raise DialInError(f"the system is one of: {choices}")


def instructions(name: str, server_text: str, port: int) -> dict[str, Any]:
    """Everything a person needs to connect one computer."""

    server = parse_server(server_text)

    return {
        "target": name,
        "port": check_port(port),
        "server": format_server(server),
        "command": link_command(server, port),
        "key_options": key_options(port),
        "keep": {
            system: keep_running(system, server, port)
            for system in SYSTEMS
        },
    }


def read_keys(alias: str, file: str) -> list[str]:
    """The keys one known-hosts file holds for a name. With no file
    named, the account's usual one is asked."""

    command = [KEYGEN, "-l", "-F", alias]

    # `ssh` reads `~` in such a path as the home folder. `ssh-keygen`
    # does not, so it is spelled out for it.
    if file:
        command += ["-f", os.path.expanduser(file)]

    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=KEYGEN_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    keys = []

    for line in done.stdout.splitlines():
        words = line.split()

        if line.startswith(KEY_COMMENT):
            continue

        if len(words) < 3:
            continue

        # `name TYPE fingerprint`. The name is the alias again.
        keys.append(f"{words[1]} {words[2]}")

    return keys


def keys_on_file(alias: str, known_hosts: str = "") -> list[str]:
    """The host keys filed for a computer, as `ssh` prints them.

    A person can hold them against the computer's own, to see that the
    first thing to answer on the port was their computer. Nothing is
    returned when no key is filed yet or the question cannot be asked.
    A `known_hosts` setting may name several files, as `ssh` allows.
    """

    files = known_hosts.split() or [""]
    keys = []

    for file in files:
        keys += read_keys(alias, file)

    return keys


def has_sign(said: str, signs: tuple[str, ...]) -> bool:
    return any(sign in said for sign in signs)


def explain(name: str, port: int, detail: str) -> str:
    """Why a computer that dials in cannot be reached, in plain words.

    The server only ever talks to a port on itself, so what `ssh`
    says is about that port. "Connection refused" there does not mean
    the computer refused anything. It means the computer is not
    connected. Text that is not known is passed on as it is.
    """

    said = detail.lower()

    if has_sign(said, SIGNS_OTHER_KEY):
        return (
            f"something else answers on port {port}, or `{name}` has "
            "a new host key. It is not the key first filed, so nothing "
            "was sent to it. To take the new key for a computer that "
            f"was set up afresh: ssh-keygen -R onsite-{name}"
        )

    if SIGN_REFUSED in said:
        return (
            "it has not connected to this server, or its link dropped. "
            f"Nothing answers on port {port}"
        )

    if SIGN_KEY_REFUSED in said:
        return "it is connected but does not accept this server's key"

    if has_sign(said, SIGNS_SILENT):
        return (
            "its link is up but it does not answer. It may be asleep "
            "or off the network"
        )

    if has_sign(said, SIGNS_NO_SSH_BEHIND):
        return (
            f"port {port} answers, but not as an SSH server. The "
            "computer's own SSH server may be off, or another program "
            "on this server has the port"
        )

    if has_sign(said, SIGNS_DROPPED):
        return "its link to this server dropped while it was in use"

    return detail

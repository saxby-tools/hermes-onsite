"""Set-up text for a machine that dials out to the host.

Some machines cannot be reached from the host: a laptop behind a home
router, or on a network that blocks inbound connections. Such a
machine can still open one connection out to the host and offer its
own SSH server back through it. OpenSSH calls that a remote forward.
The host then reaches the machine at a port on its own loopback
address, and everything else in the plugin works as it does for a
machine the host dials.

No listener is added by the plugin. The host's own SSH server opens
the port, on loopback only. Nothing new is installed on the machine:
it runs the `ssh` program it already has.

This module only builds text for a person to run. It touches nothing.
"""

from __future__ import annotations

from .config import KIND_SSH


LOOPBACK = "127.0.0.1"
SSH_PORT = 22

DEFAULT_PORT = 22022
MIN_PORT = 1024
MAX_PORT = 65535

# The machine notices a dead link in 45 seconds and dials again.
ALIVE_INTERVAL = 15
ALIVE_COUNT = 3
RETRY_SECONDS = 5

SERVICE_NAME = "onsite-dial-out"

UNIT_TEMPLATE = """\
[Unit]
Description=Offer this machine's SSH server to a Hermes host
After=network-online.target

[Service]
ExecStart={command}
Restart=always
RestartSec={retry}

[Install]
WantedBy=default.target
"""


class DialOutError(ValueError):
    """The set-up text could not be built. The message says why."""


def check_port(port: int) -> int:
    if port < MIN_PORT:
        raise DialOutError(f"the port must be {MIN_PORT} or higher")

    if port > MAX_PORT:
        raise DialOutError(f"the port must be {MAX_PORT} or lower")

    return port


def check_server(server: str) -> str:
    user, separator, host = server.partition("@")

    if not separator or not user or not host:
        raise DialOutError(
            f"the host is named as USER@ADDRESS, not `{server}`"
        )

    # It ends up on a command line a person pastes into a shell.
    if any(char.isspace() or char in "'\";&|$`" for char in server):
        raise DialOutError(f"`{server}` is not a plain USER@ADDRESS")

    return server


def tunnel_arguments(server: str, port: int) -> list[str]:
    """The `ssh` call the machine runs to offer itself to the host."""

    return [
        "ssh",
        "-N",
        "-o", "ExitOnForwardFailure=yes",
        "-o", f"ServerAliveInterval={ALIVE_INTERVAL}",
        "-o", f"ServerAliveCountMax={ALIVE_COUNT}",
        "-R", f"{LOOPBACK}:{port}:{LOOPBACK}:{SSH_PORT}",
        server,
    ]


def tunnel_command(server: str, port: int) -> str:
    return " ".join(tunnel_arguments(check_server(server), check_port(port)))


def service_unit(server: str, port: int) -> str:
    """A user service that keeps the link up on a Linux machine."""

    arguments = tunnel_arguments(check_server(server), check_port(port))
    command = " ".join(["/usr/bin/env", *arguments])

    return UNIT_TEMPLATE.format(command=command, retry=RETRY_SECONDS)


def target_settings(user: str, port: int) -> dict:
    """The target block that points the plugin at the forwarded port."""

    return {
        "kind": KIND_SSH,
        "host": LOOPBACK,
        "port": check_port(port),
        "user": user,
    }

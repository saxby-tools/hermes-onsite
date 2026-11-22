"""Run a program and wait for a port. Shared by the rig's modules.

Standard library only.
"""

from __future__ import annotations

import socket
import subprocess
import time


LOCALHOST = "127.0.0.1"
PORT_POLL_SECONDS = 0.2


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        **kwargs,
    )


def must(command: list[str], **kwargs) -> str:
    done = run(command, **kwargs)

    if done.returncode != 0:
        joined = " ".join(command)

        raise RuntimeError(f"`{joined}` failed:\n{done.stdout}{done.stderr}")

    return done.stdout


def wait_for_port(port: int, seconds: float) -> None:
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        try:
            with socket.create_connection((LOCALHOST, port), timeout=1):
                return
        except OSError:
            time.sleep(PORT_POLL_SECONDS)

    raise RuntimeError(f"nothing is listening on port {port}")

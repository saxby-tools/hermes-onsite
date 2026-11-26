"""Run one script on a target, over a connection of its own.

For questions that are asked once and answered in text, such as what
desktop a target has. A session's own commands do not come through
here.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import shlex
import subprocess

from .config import TargetSpec
from .health import build_oneshot_command
from .health import last_line


class RemoteError(RuntimeError):
    """The script could not be run. The message says why."""


def run_remote(spec: TargetSpec, script: str, timeout: int) -> bytes:
    # The login shell on a target may not be POSIX. `sh` always is.
    remote = "sh -c " + shlex.quote(script)

    try:
        done = subprocess.run(
            build_oneshot_command(spec, remote),
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RemoteError(
            f"{spec.label} did not answer in {timeout}s"
        ) from None
    except OSError as error:
        raise RemoteError(f"cannot run ssh: {error}") from None

    if done.returncode != 0:
        detail = last_line(done.stderr.decode("utf-8", "replace"))

        raise RemoteError(detail or f"ssh exited {done.returncode}")

    return done.stdout

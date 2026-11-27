"""One module per kind of target.

Hermes chooses its file-path rules from an environment's class name:
a name holding `ssh` gets remote rules, one holding `local` gets host
rules, and one holding `docker` gets a container's. The three classes
here are named with that in mind. Rename them with care.
"""

from __future__ import annotations

from .. import environments
from .. import mounts
from ..compat import EnvironmentConnectionError
from ..compat import data_dir
from ..compat import terminal_persistence
from ..config import KIND_ENVIRONMENT
from ..config import KIND_LOCAL
from ..config import TargetSpec
from ..health import Gate
from ..health import TargetDown
from .docker import OnsiteDockerEnvironment
from .local import OnsiteLocalEnvironment
from .ssh import OnsiteSshEnvironment


RETRY_HINT = (
    "The target machine is not reachable. No command was run. Wake or "
    "reconnect it and call the tool again. To use another machine for "
    "this session, the user can type `/onsite <target>`."
)

SANDBOX_HINT = (
    "The sandbox could not be made ready. No command was run. Tell "
    "the user what it said. Do not work around it."
)


def build_ssh(
    spec: TargetSpec,
    gate: Gate,
    cwd: str,
    timeout: int,
    host_cwd: str,
):
    _, persistent = terminal_persistence()

    # A live probe is cheaper than waiting for the connection itself
    # to time out, and it keeps the failure inside five seconds.
    try:
        gate.admit_fresh()
    except TargetDown as down:
        raise EnvironmentConnectionError(
            str(down),
            retry_hint=RETRY_HINT,
        ) from down

    try:
        return OnsiteSshEnvironment(
            spec=spec,
            gate=gate,
            start_cwd=cwd,
            host_cwd=host_cwd,
            timeout=timeout,
            persistent=persistent,
        )
    except EnvironmentConnectionError as error:
        gate.trip(error.reason)

        raise EnvironmentConnectionError(
            f"onsite target {spec.label}: {error.reason}",
            retry_hint=RETRY_HINT,
        ) from error


def build_sandbox(spec: TargetSpec, timeout: int, host_cwd: str):
    """An environment's sandbox for the session in `host_cwd`.

    The folder says which session it is. A session that was not
    opened in one of the environment's folders has no sandbox, and is
    told how to get one. It is never run somewhere else instead.
    """

    session = environments.session_of(spec, host_cwd)

    if session is None:
        raise EnvironmentConnectionError(
            f"{spec.label} only runs a session opened in one of its "
            "own folders. Start one with `hermes onsite env new "
            f"{spec.name}` on the server, or with New session beside "
            "it in the desktop app.",
            retry_hint=SANDBOX_HINT,
        )

    # Exports last between commands as they do on the Hermes server.
    persistent, _ = terminal_persistence()

    try:
        return OnsiteDockerEnvironment(
            spec=spec,
            session=session,
            profile=mounts.profile_name(data_dir()),
            timeout=timeout,
            persistent=persistent,
        )
    except environments.SandboxError as error:
        raise EnvironmentConnectionError(
            str(error),
            retry_hint=SANDBOX_HINT,
        ) from error


def build_environment(
    spec: TargetSpec,
    gate: Gate,
    cwd: str,
    timeout: int,
    host_cwd: str = "",
):
    if spec.kind == KIND_LOCAL:
        persistent, _ = terminal_persistence()

        return OnsiteLocalEnvironment(
            spec=spec,
            start_cwd=cwd,
            timeout=timeout,
            persistent=persistent,
        )

    if spec.kind == KIND_ENVIRONMENT:
        return build_sandbox(spec, timeout, host_cwd)

    return build_ssh(spec, gate, cwd, timeout, host_cwd)

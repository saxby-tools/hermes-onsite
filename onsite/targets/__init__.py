"""One module per kind of target.

Hermes chooses its file-path rules from an environment's class name:
a name holding `ssh` gets remote rules, and one holding `local` gets
host rules. The two classes here are named with that in mind. Rename
them with care.
"""

from __future__ import annotations

from ..compat import EnvironmentConnectionError
from ..compat import terminal_persistence
from ..config import KIND_LOCAL
from ..config import TargetSpec
from ..health import Gate
from ..health import TargetDown
from ..health import describe_failure
from .local import OnsiteLocalEnvironment
from .ssh import OnsiteSshEnvironment


RETRY_HINT = (
    "The target machine is not reachable. No command was run. Wake or "
    "reconnect it and call the tool again. To use another machine for "
    "this session, the user can type `/onsite <target>`."
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
        reason = describe_failure(spec, error.reason)

        gate.trip(reason)

        raise EnvironmentConnectionError(
            f"onsite target {spec.label}: {reason}",
            retry_hint=RETRY_HINT,
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

    return build_ssh(spec, gate, cwd, timeout, host_cwd)

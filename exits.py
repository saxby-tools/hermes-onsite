"""Name how a command ended.

Every command ends in one of four states:

- success: it ran and exited 0.
- nonzero: it ran and exited with another status.
- killed: Hermes stopped it, or a signal did.
- no_status: the link to the target was lost. The command may have
  run, may be running still, or may never have started. It is not
  retried, because a retry could repeat a write.

This module imports nothing from Hermes.
"""

from __future__ import annotations

from typing import Any


STATE_SUCCESS = "success"
STATE_NONZERO = "nonzero"
STATE_KILLED = "killed"
STATE_NO_STATUS = "no_status"

# OpenSSH's own exit status for a failed or dropped connection.
SSH_TRANSPORT_EXIT = 255

# A shell reports death by signal N as 128 + N.
SIGNAL_EXIT_FLOOR = 129
SIGNAL_EXIT_CEILING = 192

RESULT_KEY = "hands"

VERDICT_LOST = "lost"
VERDICT_UNSURE = "unsure"
VERDICT_FINE = "fine"

# Text the SSH client prints when the link fails. With one of these in
# the output and no exit marker, the link is the cause.
SSH_FAILURE_MARKS = (
    "ssh: connect to host",
    "ssh: could not resolve hostname",
    "connection refused",
    "connection timed out",
    "operation timed out",
    "connection reset by peer",
    "connection closed by",
    "closed by remote host",
    "broken pipe",
    "not responding",
    "no route to host",
    "network is unreachable",
    "kex_exchange_identification",
    "mux_client_request_session",
    "permission denied (publickey",
    "host key verification failed",
)


def exit_code(result: dict[str, Any]) -> int:
    try:
        return int(result.get("returncode") or 0)
    except (TypeError, ValueError):
        return 0


def was_stopped_by_hermes(result: dict[str, Any]) -> bool:
    if result.get("hermes_timed_out"):
        return True

    return bool(result.get("hermes_interrupted"))


def has_failure_mark(output: str) -> bool:
    lowered = output.lower()

    return any(mark in lowered for mark in SSH_FAILURE_MARKS)


def transport_verdict(result: dict[str, Any]) -> str:
    """Decide whether a result shows a lost link.

    Hermes wraps each command so it prints a marker with its final
    folder. A command that ran to its end leaves that marker, so its
    exit status is real even when it is 255.
    """

    if exit_code(result) != SSH_TRANSPORT_EXIT:
        return VERDICT_FINE

    if result.get("cwd_observed"):
        return VERDICT_FINE

    if was_stopped_by_hermes(result):
        return VERDICT_FINE

    if has_failure_mark(str(result.get("output") or "")):
        return VERDICT_LOST

    # A command may itself exit 255 before the marker prints. The
    # caller settles this with a live probe.
    return VERDICT_UNSURE


def classify(result: dict[str, Any], link_lost: bool = False) -> str:
    if link_lost:
        return STATE_NO_STATUS

    if was_stopped_by_hermes(result):
        return STATE_KILLED

    code = exit_code(result)

    if code == 0:
        return STATE_SUCCESS

    if SIGNAL_EXIT_FLOOR <= code <= SIGNAL_EXIT_CEILING:
        return STATE_KILLED

    return STATE_NONZERO


def no_status_note(label: str, detail: str) -> str:
    reason = detail or "the connection was lost"

    return (
        f"[hands: no exit status from {label}: {reason}. "
        "The command may have run, may still be running, or may not "
        "have started. It was not retried.]"
    )


def annotate(
    result: dict[str, Any],
    target: str,
    state: str,
    note: str = "",
) -> dict[str, Any]:
    """Record the state on the result. A lost link is also spelled out
    in the output, because that is the part the model reads."""

    result[RESULT_KEY] = {"target": target, "state": state}

    if not note:
        return result

    output = str(result.get("output") or "").rstrip("\n")
    result["output"] = f"{output}\n{note}" if output else note

    return result


def refusal(target: str, message: str) -> dict[str, Any]:
    """The result for a command that was never sent to its target."""

    result = {
        "output": f"[hands: {message}]",
        "returncode": SSH_TRANSPORT_EXIT,
    }

    return annotate(result, target, STATE_NO_STATUS)

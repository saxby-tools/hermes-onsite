"""What the status endpoint answers, as plain functions.

The desktop half asks where a session runs and what the agent has
proposed. It also edits the list of machines, tests one, and says
where a machine's disk is mounted on the host, so a session can be
started in one of its folders. For a machine that dials in, it
hands over the command to run there. Each is answered here so it can be
tested with no web server. `dashboard/plugin_api.py` only wraps them.

This module imports nothing from Hermes.
"""

from __future__ import annotations

from typing import Any

from . import dialin
from . import messages
from . import mounts
from .config import ConfigError
from .config import KIND_SSH
from .config import KNOWN_TARGET_KEYS
from .config import parse_config
from .health import probe_target
from .mounts import under_mount
from .proposals import Proposal
from .routing import REASON_MOUNT


API_VERSION = 1

MAX_IDS = 6
MAX_ID_CHARS = 512
MAX_CWD_CHARS = 4096
MAX_NAME_CHARS = 32


def clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""

    return value.strip()[:limit]


def read_ids(body: Any) -> list[str]:
    """The names the caller has for its session, most specific first.

    The desktop app knows a session by more than one id, and which one
    matches Hermes' own differs by version. It sends all it has.
    """

    if not isinstance(body, dict):
        return []

    raw = body.get("session_ids")

    if not isinstance(raw, list):
        raw = [body.get("session_id")]

    ids = [clean_text(item, MAX_ID_CHARS) for item in raw[:MAX_IDS]]

    return [item for item in ids if item]


def read_cwd(body: Any) -> str:
    if not isinstance(body, dict):
        return ""

    return clean_text(body.get("cwd"), MAX_CWD_CHARS)


def find_session_id(runtime, ids: list[str]) -> str:
    """The first id the plugin has a pin or a folder for."""

    for name in ids:
        if runtime.find_pin(session_id=name) is not None:
            return name

        if runtime.folders.find(name):
            return name

    return ids[0] if ids else ""


def wants_rows(body: Any) -> bool:
    """Whether the caller asked for every target, not only its own.

    Listing them costs one login to each. The desktop half only asks
    while its pane is open. A caller that says nothing gets the list,
    as every desktop half before this choice existed expects.
    """

    if not isinstance(body, dict):
        return True

    return body.get("rows") is not False


def answer_of(payload: dict[str, Any]) -> dict[str, Any]:
    """What a status answer already knows about its own target."""

    if not payload.get("ok"):
        return {}

    return {
        payload["target"]: {
            "connected": payload["connected"],
            "detail": payload["detail"],
            "latency_ms": payload["latency_ms"],
        },
    }


def probe_row(runtime, spec, known: dict[str, Any]) -> dict[str, Any]:
    # The session's own target was probed a moment ago for the same
    # answer. A second login would only tell the same thing again.
    if spec.name in known:
        return known[spec.name]

    probe = runtime.gate_for(spec).check()

    return {
        "connected": probe.ok,
        "detail": probe.detail,
        "latency_ms": probe.elapsed_ms,
    }


def mount_problem(runtime, spec, connected: bool = True) -> str:
    """Why a target's files cannot be shown on the host, or nothing.

    Asking mounts the disk if it is not mounted, so the folder picker
    and the file tree can reach it by the time a person looks.

    A target that does not answer is not asked to mount. The try
    would only time out, and the failure would then be remembered
    past the moment the target comes back. The state already says it
    is away.
    """

    if spec.kind != KIND_SSH:
        return ""

    # What the last try said still holds for a fault of set-up, such
    # as a server with no `sshfs`. It is said again, not asked again.
    if not connected:
        return runtime.mount_failure(spec.name)

    try:
        runtime.mount(spec)
    except mounts.MountError as error:
        return str(error)

    return ""


def session_mount_problem(
    runtime,
    payload: dict[str, Any],
    folder: str = "",
) -> str:
    """Why the session's own folder cannot be shown, or nothing.

    A session opened under a mount is in a folder that is only there
    while the mount is. A restart of the host takes every mount down,
    and so does a target that went away for a while. The chip asks
    this every few seconds, so the mount is back before a person has
    to go looking for it.

    `folder` is where the app says the session is. A session that has
    had its first message is routed by where it started, not by its
    folder, so the reason alone would miss it.
    """

    if not payload.get("ok"):
        return ""

    # A machine can be removed between the answer and this line.
    try:
        spec = runtime.load_config().target(payload["target"])
    except (ConfigError, KeyError):
        return ""

    by_reason = payload.get("reason") == REASON_MOUNT

    if not by_reason and not under_mount(folder, spec):
        return ""

    return mount_problem(runtime, spec, bool(payload.get("connected")))


def row_state(runtime, spec, known: dict[str, Any]) -> dict[str, Any]:
    """A target's state for the pane, and why its files cannot be
    shown if they cannot. The probe comes first: a target that does
    not answer is not asked to mount."""

    probe = probe_row(runtime, spec, known)

    return {
        "mount_error": mount_problem(runtime, spec, probe["connected"]),
        **probe,
    }


def describe_targets(
    runtime,
    known: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    try:
        config = runtime.load_config()
    except ConfigError:
        return []

    rows = []

    for spec in config.targets.values():
        rows.append({
            "name": spec.name,
            "kind": spec.kind,
            "label": spec.label,
            "default": spec.name == config.default,
            # The pane says "not connected" of a computer that dials
            # in, and points at its command, where it would say "not
            # reachable" of one the server dials.
            "dial_in": bool(spec.dial_in),
            **row_state(runtime, spec, known or {}),
        })

    return rows


def status_payload(runtime, body: Any, with_targets: bool = False) -> dict:
    # A chip that named a target here would be wrong about where the
    # next command runs. No target is asked anything either.
    if not runtime.in_use():
        return {
            "ok": False,
            "error": messages.NOT_IN_USE,
            "api_version": API_VERSION,
        }

    ids = read_ids(body)
    session_id = find_session_id(runtime, ids)

    payload = runtime.status(session_id=session_id, cwd=read_cwd(body))
    payload["api_version"] = API_VERSION
    payload["run_allowed"] = runtime.run_allowed()
    payload["mount_error"] = session_mount_problem(
        runtime,
        payload,
        read_cwd(body),
    )

    if with_targets:
        payload["rows"] = describe_targets(runtime, answer_of(payload))

    return payload


def proposal_to_json(proposal: Proposal) -> dict[str, Any]:
    return {
        "number": proposal.number,
        "command": proposal.command,
        "why": proposal.why,
        "status": proposal.status,
    }


def proposals_payload(runtime, body: Any) -> dict:
    for name in read_ids(body):
        found = runtime.proposals.list(name)

        if found:
            return {
                "api_version": API_VERSION,
                "items": [proposal_to_json(item) for item in found],
            }

    return {"api_version": API_VERSION, "items": []}



def failure(message: str) -> dict:
    return {"ok": False, "api_version": API_VERSION, "error": message}


def read_name(body: Any) -> str:
    if not isinstance(body, dict):
        return ""

    return clean_text(body.get("name"), MAX_NAME_CHARS)


def clean_target(raw: Any) -> dict:
    """A target block as a form sent it, less its blanks.

    A form sends every field, filled or not. An empty one means "not
    set", which in the settings is a key that is absent.
    """

    if not isinstance(raw, dict):
        return {}

    block = {}

    for key in sorted(KNOWN_TARGET_KEYS):
        value = raw.get(key)

        if value is None:
            continue

        if isinstance(value, str) and not value.strip():
            continue

        if isinstance(value, (list, dict)) and not value:
            continue

        block[key] = value.strip() if isinstance(value, str) else value

    return block


def complete_target(name: str, block: dict, targets: dict) -> dict:
    """A target block with what the plugin decides filled in.

    A form for a computer that dials in sends the account and how
    the computer reaches the server. The loopback address and a free
    port are the plugin's to add. Any other block is left as it is.
    """

    if not block.get("dial_in"):
        return block

    return dialin.complete(name, block, targets)


def targets_payload(runtime) -> dict:
    """The machines as written in the settings, for a form to edit."""

    settings = runtime.raw_settings()

    return {
        "ok": True,
        "api_version": API_VERSION,
        "default": settings.get("default"),
        "targets": settings["targets"],
        "problem": runtime.config_problem() or "",
    }


def target_as_set_up(runtime, name: str):
    """A machine as the settings have it now, or nothing."""

    try:
        return runtime.load_config().targets.get(name)
    except ConfigError:
        return None


def let_go_of_old_mount(runtime, before, name: str) -> None:
    """Take down the mount a machine had before its settings changed.

    On Linux nothing else would. The new settings name another place
    or no machine at all, so the old place is never looked at again,
    and the machine's whole disk would stay mounted there unseen.
    """

    if before is None or before.kind != KIND_SSH:
        return

    after = target_as_set_up(runtime, name)
    kept = after is not None and after.kind == KIND_SSH

    if kept and after.mount == before.mount:
        return

    try:
        runtime.unmount(before)
    except (mounts.MountError, OSError):
        return


def save_target_payload(runtime, body: Any) -> dict:
    name = read_name(body)

    if not name:
        return failure("Give the machine a name.")

    before = target_as_set_up(runtime, name)
    settings = runtime.raw_settings()

    try:
        settings["targets"][name] = complete_target(
            name,
            clean_target(body.get("target")),
            settings["targets"],
        )
    except dialin.DialInError as error:
        return failure(str(error))

    if body.get("default") or not settings.get("default"):
        settings["default"] = name

    try:
        runtime.save_settings(settings)
    except ConfigError as error:
        return failure("; ".join(error.problems))
    except RuntimeError as error:
        return failure(str(error))

    let_go_of_old_mount(runtime, before, name)

    return targets_payload(runtime)


def remove_target_payload(runtime, body: Any) -> dict:
    name = read_name(body)
    settings = runtime.raw_settings()

    if name not in settings["targets"]:
        return failure(f"`{name}` is not one of the machines.")

    if settings.get("default") == name:
        return failure(
            f"`{name}` is the default. Make another machine the default "
            "first."
        )

    before = target_as_set_up(runtime, name)

    del settings["targets"][name]

    try:
        runtime.save_settings(settings)
    except ConfigError as error:
        return failure("; ".join(error.problems))
    except RuntimeError as error:
        return failure(str(error))

    let_go_of_old_mount(runtime, before, name)

    return targets_payload(runtime)


def test_target_payload(runtime, body: Any) -> dict:
    """Try a machine as the form describes it, saved or not."""

    name = read_name(body) or "machine"
    block = clean_target(body.get("target") if isinstance(body, dict) else None)

    # A form for a machine that dials in sends no address, as it does
    # on a save. Its port is the one it was saved with.
    try:
        block = complete_target(
            name,
            block,
            runtime.raw_settings()["targets"],
        )
        config = parse_config({"default": name, "targets": {name: block}})
    except dialin.DialInError as error:
        return failure(str(error))
    except ConfigError as error:
        return failure("; ".join(error.problems))

    probe = probe_target(config.target(name))

    return {
        "ok": True,
        "api_version": API_VERSION,
        "connected": probe.ok,
        "detail": probe.detail,
        "latency_ms": probe.elapsed_ms,
    }


def find_ssh_target(runtime, body: Any):
    """The SSH target a request names, or a failure to send back."""

    name = clean_text(
        body.get("target") if isinstance(body, dict) else "",
        MAX_NAME_CHARS,
    )

    try:
        config = runtime.load_config()
    except ConfigError as error:
        return None, failure("; ".join(error.problems))

    if name not in config.targets:
        return None, failure(f"`{name}` is not one of the machines.")

    spec = config.target(name)

    if spec.kind != KIND_SSH:
        return None, failure(
            f"`{name}` is the Hermes server. Its folders open the usual "
            "way."
        )

    return spec, None


def dial_in_payload(runtime, body: Any) -> dict:
    """What a person runs on a computer that dials in, and whether
    the computer has arrived.

    The Machines page asks this again every few seconds while its
    steps are on screen, so the page turns to "connected" by itself
    when the command is run.
    """

    spec, problem = find_ssh_target(runtime, body)

    if problem is not None:
        return problem

    if not spec.dial_in:
        return failure(
            f"`{spec.name}` does not dial in. The server logs in to it."
        )

    probe = runtime.gate_for(spec).check()

    return {
        "ok": True,
        "api_version": API_VERSION,
        "label": spec.label,
        "connected": probe.ok,
        "detail": probe.detail,
        "latency_ms": probe.elapsed_ms,
        "host_keys": dialin.keys_on_file(
            spec.host_key_alias,
            spec.known_hosts,
        ),
        **dialin.instructions(spec.name, spec.dial_in, spec.port),
    }


def read_folder(body: Any) -> str:
    """The folder asked for, or nothing for the target's start folder."""

    return clean_text(body.get("path"), MAX_CWD_CHARS)


def open_payload(runtime, body: Any) -> dict:
    """Where a folder on a machine is on the host, so a session can be
    started in it. With no folder named, the machine's start folder."""

    spec, problem = find_ssh_target(runtime, body)

    if problem is not None:
        return problem

    try:
        folder, shown = runtime.host_folder(spec, read_folder(body))
    except mounts.MountError as error:
        return failure(str(error))

    return {
        "ok": True,
        "api_version": API_VERSION,
        "target": spec.name,
        "cwd": str(folder),
        "path": shown,
    }


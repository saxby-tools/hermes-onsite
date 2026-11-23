"""What the status endpoint answers, as plain functions.

The desktop half asks where a session runs and what the agent has
proposed. It also edits the list of machines, tests one, and browses a
machine's folders to open one. Each is answered here so it can be
tested with no web server. `dashboard/plugin_api.py` only wraps them.

This module imports nothing from Hermes.
"""

from __future__ import annotations

from typing import Any

from . import mirror
from .config import ConfigError
from .config import KIND_SSH
from .config import KNOWN_TARGET_KEYS
from .config import parse_config
from .health import probe_target
from .proposals import Proposal


API_VERSION = 1

MAX_IDS = 6
MAX_ID_CHARS = 512
MAX_CWD_CHARS = 4096
MAX_NAME_CHARS = 32
MAX_FOLDERS = 500

HOME_FOLDER = "~"


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


def describe_targets(runtime) -> list[dict[str, Any]]:
    try:
        config = runtime.load_config()
    except ConfigError:
        return []

    rows = []

    for spec in config.targets.values():
        probe = runtime.gate_for(spec).check()

        rows.append({
            "name": spec.name,
            "kind": spec.kind,
            "label": spec.label,
            "connected": probe.ok,
            "detail": probe.detail,
            "latency_ms": probe.elapsed_ms,
            "default": spec.name == config.default,
        })

    return rows


def status_payload(runtime, body: Any, with_targets: bool = False) -> dict:
    ids = read_ids(body)
    session_id = find_session_id(runtime, ids)

    payload = runtime.status(session_id=session_id, cwd=read_cwd(body))
    payload["api_version"] = API_VERSION

    if with_targets:
        payload["rows"] = describe_targets(runtime)

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


def save_target_payload(runtime, body: Any) -> dict:
    name = read_name(body)

    if not name:
        return failure("Give the machine a name.")

    settings = runtime.raw_settings()
    settings["targets"][name] = clean_target(body.get("target"))

    if body.get("default") or not settings.get("default"):
        settings["default"] = name

    try:
        runtime.save_settings(settings)
    except ConfigError as error:
        return failure("; ".join(error.problems))
    except RuntimeError as error:
        return failure(str(error))

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

    del settings["targets"][name]

    try:
        runtime.save_settings(settings)
    except ConfigError as error:
        return failure("; ".join(error.problems))
    except RuntimeError as error:
        return failure(str(error))

    return targets_payload(runtime)


def test_target_payload(runtime, body: Any) -> dict:
    """Try a machine as the form describes it, saved or not."""

    name = read_name(body) or "machine"
    block = clean_target(body.get("target") if isinstance(body, dict) else None)

    try:
        config = parse_config({"default": name, "targets": {name: block}})
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
            f"`{name}` is this machine. Its folders open the usual way."
        )

    return spec, None


def read_folder(body: Any) -> str:
    folder = clean_text(body.get("path"), MAX_CWD_CHARS)

    return folder or HOME_FOLDER


def parent_of(folder: str) -> str:
    if folder == "/":
        return ""

    return folder.rsplit("/", 1)[0] or "/"


def browse_payload(runtime, body: Any) -> dict:
    """The folders inside one folder on a machine."""

    spec, problem = find_ssh_target(runtime, body)

    if problem is not None:
        return problem

    try:
        listing = mirror.list_children(spec, read_folder(body))
    except mirror.MirrorError as error:
        return failure(str(error))

    return {
        "ok": True,
        "api_version": API_VERSION,
        "target": spec.name,
        "path": listing.home,
        "parent": parent_of(listing.home),
        "folders": list(listing.folders[:MAX_FOLDERS]),
        "more": len(listing.folders) > MAX_FOLDERS,
    }


def open_payload(runtime, body: Any) -> dict:
    """Give a folder on a machine a place on the host to open it from."""

    spec, problem = find_ssh_target(runtime, body)

    if problem is not None:
        return problem

    try:
        folder, result = mirror.open_folder(spec, read_folder(body), True)
    except mirror.MirrorError as error:
        return failure(str(error))

    if result.skipped:
        return failure(
            f"{folder} is taken by something else on this machine."
        )

    return {
        "ok": True,
        "api_version": API_VERSION,
        "target": spec.name,
        "cwd": str(folder),
    }


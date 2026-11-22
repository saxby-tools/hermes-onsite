"""What the status endpoint answers, as plain functions.

The desktop half asks two questions: where does this session run, and
what has the agent proposed. Both are answered here so they can be
tested with no web server. `dashboard/plugin_api.py` only wraps them.

This module imports nothing from Hermes.
"""

from __future__ import annotations

from typing import Any

from .config import ConfigError
from .proposals import Proposal


API_VERSION = 1

MAX_IDS = 6
MAX_ID_CHARS = 512
MAX_CWD_CHARS = 4096


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


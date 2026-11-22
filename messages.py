"""Every piece of text the plugin shows a person or a model.

Kept in one module so the wording is consistent between the CLI, the
TUI, the desktop app and the system prompt, and so it can be tested.

This module imports nothing from Hermes.
"""

from __future__ import annotations

from typing import Any

from .config import HandsConfig
from .config import KIND_LOCAL
from .config import TargetSpec
from .proposals import Proposal
from .routing import REASON_DEFAULT
from .routing import REASON_OVERRIDE
from .routing import REASON_PATH_MAP
from .routing import REASON_PINNED
from .routing import REASON_PREFIX
from .routing import Route


PROMPT_SECTION_ID = "hands"
DIRECTIVE_NAME = "hands-run"

REASON_TEXT = {
    REASON_OVERRIDE: "you set it with /hands",
    REASON_PINNED: "this session started there",
    REASON_PREFIX: "the session folder is under one of its prefixes",
    REASON_PATH_MAP: "the session folder is in its path map",
    REASON_DEFAULT: "no folder rule matched, so the default was used",
}

SWITCH_HINT = (
    "Switch with /hands <target>. Go back to the rules with /hands auto."
)

FOLDER_UNKNOWN = (
    "This session has not said which folder it is in yet. The target "
    "is settled at its first message, so this may change."
)

INJECTION_HINT = (
    "This client does not let a plugin hand the agent a message. To "
    "allow it, set `plugins.entries.hermes-hands.allow_gateway_injection: "
    "true` in config.yaml. Until then, ask the agent to run it:"
)


def describe_state(status: dict[str, Any]) -> str:
    if not status.get("connected"):
        detail = status.get("detail") or "no answer"

        return f"NOT REACHABLE ({detail})"

    if status.get("kind") == KIND_LOCAL:
        return "ready"

    return f"connected ({status.get('latency_ms', 0)} ms)"


def format_status(status: dict[str, Any]) -> str:
    """The reply to `/hands` with no argument."""

    if not status.get("ok"):
        return str(status.get("error") or "hermes-hands is not set up.")

    reason = REASON_TEXT.get(status.get("reason", ""), "")
    names = []

    for name in status.get("targets") or []:
        if name == status.get("default"):
            names.append(f"{name} (default)")
            continue

        names.append(name)

    lines = [
        f"Hands:   {status['label']}",
        f"State:   {describe_state(status)}",
        f"Folder:  {status['cwd']}",
        f"Why:     {reason}",
        f"Targets: {', '.join(names)}",
    ]

    if not status.get("folder_known", True):
        lines.append(FOLDER_UNKNOWN)

    lines.append(SWITCH_HINT)

    return "\n".join(lines)


def format_switch(
    route: Route,
    spec: TargetSpec,
    status: dict[str, Any],
    cleared: bool,
) -> str:
    """The reply to `/hands <target>`."""

    lead = f"This session now runs on {spec.label}."

    if cleared:
        lead = f"Override cleared. The folder rules pick {spec.label}."

    lines = [
        lead,
        f"The next tool call starts in {route.cwd}.",
    ]

    if not status.get("connected"):
        detail = status.get("detail") or "no answer"
        lines.append(f"Warning: it is not reachable right now ({detail}).")

    lines.append(
        "The agent was not told. Say so in your next message if it "
        "matters, since paths differ between machines."
    )

    return "\n".join(lines)


def format_block(proposal: Proposal) -> str:
    """The numbered block a person reads before running a command."""

    lines = [
        f"Proposed command #{proposal.number}",
        "",
        f"    $ {proposal.command}",
        "",
    ]

    if proposal.why:
        lines.append(f"Why: {proposal.why}")

    lines.append(f"Run it with /run {proposal.number}")

    return "\n".join(lines)


def format_proposal_result(proposal: Proposal, with_card: bool) -> str:
    """What `propose_command` returns to the model."""

    lines = [
        f"Proposed command #{proposal.number} is saved. It has NOT run.",
        "Show the user the block below as it is, then stop and wait.",
        "",
    ]

    # Only the desktop app draws the directive as a card. Anywhere
    # else it would show as stray text.
    if with_card:
        lines.append(f'::{DIRECTIVE_NAME}{{n="{proposal.number}"}}')
        lines.append("")

    lines.append(format_block(proposal))

    return "\n".join(lines)


def format_run_request(proposal: Proposal) -> str:
    """The message `/run N` hands to the agent.

    The command goes through the terminal tool like any other, so
    Hermes' approval prompt still applies to it.
    """

    return "\n".join([
        f"Run proposed command #{proposal.number} now with the terminal "
        "tool. Run it exactly as written, change nothing, and report "
        "what it printed.",
        "",
        "```",
        proposal.command,
        "```",
    ])


def format_run_sent(proposal: Proposal) -> str:
    return (
        f"Asked the agent to run #{proposal.number} through the terminal "
        "tool. Hermes' approval prompt still applies."
    )


def format_run_blocked(proposal: Proposal) -> str:
    return "\n".join([
        INJECTION_HINT,
        "",
        f"    run this: {proposal.command}",
    ])


def format_no_proposal(number: int, pending: list[Proposal]) -> str:
    if not pending:
        return (
            f"There is no proposed command #{number}. Nothing has been "
            "proposed in this session yet."
        )

    known = ", ".join(f"#{item.number}" for item in pending)

    return f"There is no proposed command #{number}. Known: {known}."


def format_run_usage(pending: list[Proposal]) -> str:
    if not pending:
        return "Usage: /run <number>. Nothing has been proposed yet."

    lines = ["Usage: /run <number>. Proposed so far:"]
    lines += [f"  #{item.number}  {item.command}" for item in pending]

    return "\n".join(lines)


def describe_target(spec: TargetSpec) -> str:
    if spec.kind == KIND_LOCAL:
        return (
            f"`{spec.name}`, the same machine Hermes runs on, as the "
            "user Hermes runs as"
        )

    return (
        f"`{spec.name}`, a remote machine reached over SSH "
        f"({spec.host}), as user `{spec.user}`"
    )


def format_prompt_section(route: Route, config: HandsConfig) -> str:
    """The per-session note in the system prompt.

    Hermes' own backend note is cached across sessions and cannot say
    which machine this session uses. This one can.
    """

    spec = config.target(route.target)
    start = "its home folder" if route.cwd == "~" else f"`{route.cwd}`"

    return "\n".join([
        "## Hands",
        "",
        "Your `terminal`, `read_file`, `write_file`, `patch` and "
        f"`search_files` tools run on {describe_target(spec)}.",
        f"They start in {start}. Paths you pass must be paths on that "
        "machine.",
        "",
        "A tool result that starts with `[hands:` means the machine "
        "could not be reached. Tell the user. Do not retry in a loop "
        "and do not work around it. The user can move the session "
        "with `/hands <target>`.",
        "",
        "When a command should be the user's call, use "
        "`propose_command` instead of running it. The user runs it "
        "with `/run <number>`.",
    ])


def format_cli_status(
    folder: str,
    status: dict[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    """The output of `hermes hands status`."""

    if not status.get("ok"):
        return str(status.get("error") or "hermes-hands is not set up.")

    reason = REASON_TEXT.get(status.get("reason", ""), "")

    lines = [
        f"Folder    {folder}",
        f"Target    {status['label']}",
        f"State     {describe_state(status)}",
        f"Start in  {status['cwd']}",
        f"Why       {reason}",
        "",
        "Targets",
    ]

    width = max(len(row["name"]) for row in rows)

    for row in rows:
        mark = "*" if row["name"] == status.get("default") else " "
        name = row["name"].ljust(width)

        lines.append(f" {mark} {name}  {row['kind']:<5}  {row['state']}")

    lines.append("")
    lines.append("* is the default target.")

    return "\n".join(lines)

"""Every piece of text the plugin shows a person or a model.

Kept in one module so the wording is consistent between the CLI, the
TUI, the desktop app and the system prompt, and so it can be tested.

This module imports nothing from Hermes.
"""

from __future__ import annotations

from typing import Any

from .config import OnsiteConfig
from .config import KIND_LOCAL
from .config import TargetSpec
from .dialout import SERVICE_NAME
from .proposals import Proposal
from .routing import REASON_DEFAULT
from .routing import REASON_MIRROR
from .routing import REASON_OVERRIDE
from .routing import REASON_PATH_MAP
from .routing import REASON_PINNED
from .routing import REASON_PREFIX
from .routing import Route


PROMPT_SECTION_ID = "onsite"
DIRECTIVE_NAME = "onsite-run"

REASON_TEXT = {
    REASON_OVERRIDE: "you set it with /onsite",
    REASON_PINNED: "this session started there",
    REASON_PREFIX: "the session folder is under one of its prefixes",
    REASON_PATH_MAP: "the session folder is in its path map",
    REASON_MIRROR: "the session folder is in its mirror",
    REASON_DEFAULT: "no folder rule matched, so the default was used",
}

MAX_SKIPPED_SHOWN = 5
SCREEN_PLUGIN = "onsite-screen"

MIRROR_NOT_SET = (
    "No target has a `mirror` folder. Set one in config.yaml, or "
    "run `hermes onsite init` with `--mirror NAME=FOLDER`."
)
OPEN_USAGE = (
    "Name the folder as TARGET:FOLDER, such as laptop:~/work."
)

SWITCH_HINT = (
    "Switch with /onsite <target>. Go back to the rules with /onsite auto."
)

FOLDER_UNKNOWN = (
    "This session has not said which folder it is in yet. The target "
    "is settled at its first message, so this may change."
)

INJECTION_HINT = (
    "This client does not let a plugin hand the agent a message. To "
    "allow it, set `plugins.entries.onsite.allow_gateway_injection: "
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
    """The reply to `/onsite` with no argument."""

    if not status.get("ok"):
        return str(status.get("error") or "onsite is not set up.")

    reason = REASON_TEXT.get(status.get("reason", ""), "")
    names = []

    for name in status.get("targets") or []:
        if name == status.get("default"):
            names.append(f"{name} (default)")
            continue

        names.append(name)

    lines = [
        f"Onsite:  {status['label']}",
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
    """The reply to `/onsite <target>`."""

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


def format_prompt_section(route: Route, config: OnsiteConfig) -> str:
    """The per-session note in the system prompt.

    Hermes' own backend note is cached across sessions and cannot say
    which machine this session uses. This one can.
    """

    spec = config.target(route.target)
    start = "its home folder" if route.cwd == "~" else f"`{route.cwd}`"

    return "\n".join([
        "## Onsite",
        "",
        "Your `terminal`, `read_file`, `write_file`, `patch` and "
        f"`search_files` tools run on {describe_target(spec)}.",
        f"They start in {start}. Paths you pass must be paths on that "
        "machine.",
        "",
        "A tool result that starts with `[onsite:` means the machine "
        "could not be reached. Tell the user. Do not retry in a loop "
        "and do not work around it. The user can move the session "
        "with `/onsite <target>`.",
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
    """The output of `hermes onsite status`."""

    if not status.get("ok"):
        return str(status.get("error") or "onsite is not set up.")

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


def format_skipped(mode: str, skipped: tuple[str, ...]) -> list[str]:
    if not skipped:
        return []

    lines = [
        f"{mode} left alone, because something else has the name or "
        "it would land outside the mirror:",
    ]

    for folder in skipped[:MAX_SKIPPED_SHOWN]:
        lines.append(f"  {folder}")

    hidden = len(skipped) - MAX_SKIPPED_SHOWN

    if hidden > 0:
        lines.append(f"  and {hidden} more")

    return lines


def format_mirror_result(
    mode: str,
    spec: TargetSpec,
    depth: int,
    result,
    apply: bool,
) -> str:
    """The output of `hermes onsite mirror` for one target."""

    verb = "made" if apply else "would make"

    lines = [
        f"{mode} {spec.label}: folders under its home, "
        f"{depth} levels deep.",
        f"{mode} {verb} {result.made} empty folders under {result.root}. "
        f"{result.kept} were already there.",
    ]

    lines += format_skipped(mode, result.skipped)

    if not apply:
        lines.append(f"{mode} nothing was made. Add --apply to make them.")

    return "\n".join(lines)


def format_open_result(
    mode: str,
    spec: TargetSpec,
    target_folder: str,
    host_folder: str,
    result,
    apply: bool,
) -> str:
    """The output of `hermes onsite open`."""

    if result.skipped:
        return "\n".join(format_skipped(mode, result.skipped))

    if not apply:
        return "\n".join([
            f"{mode} {target_folder} on {spec.label} would be opened at:",
            host_folder,
            f"{mode} nothing was made. Add --apply to make it.",
        ])

    return "\n".join([
        f"{mode} {target_folder} on {spec.label} is opened at:",
        host_folder,
        f"{mode} start a session in that folder to work there.",
    ])


def format_dial_out(
    mode: str,
    name: str,
    port: int,
    command: str,
    unit: str,
    settings_yaml: str,
    apply: bool,
) -> str:
    """The output of `hermes onsite dial-out`."""

    lines = [
        f"{mode} `{name}` will reach this host by dialing out.",
        "",
        "1. On that machine, open the link. It must be able to log in",
        "   to this host over SSH with a key and no prompt:",
        "",
        f"   {command}",
        "",
        "2. To keep the link up on a Linux machine, save this as",
        f"   ~/.config/systemd/user/{SERVICE_NAME}.service and turn",
        "   it on with your service manager:",
        "",
    ]

    lines += [f"   {line}".rstrip() for line in unit.splitlines()]

    lines += [
        "",
        f"3. This host then reaches `{name}` at port {port} on its own",
        "   loopback address. The target's settings:",
        "",
    ]

    lines += [f"   {line}".rstrip() for line in settings_yaml.splitlines()]
    lines.append("")

    if apply:
        lines.append(f"{mode} the target's settings were written.")
    else:
        lines.append(
            f"{mode} nothing was written. Add --apply to write the "
            "target's settings."
        )

    return "\n".join(lines)


def format_mcp_refused(tool_name: str, owner: str, current: str) -> str:
    """What the model is told when it reaches for another machine."""

    return (
        f"`{tool_name}` runs on `{owner}`, and this session runs on "
        f"`{current}`. Nothing was run. Do not work around this. Tell "
        f"the user that they can move the session with `/onsite {owner}`."
    )


def format_mcp_add(
    mode: str,
    spec: TargetSpec,
    server: str,
    started_by: str,
    command: str,
    apply: bool,
) -> str:
    """The output of `hermes onsite mcp`."""

    lines = [
        f"{mode} MCP server `{server}` on {spec.label}.",
        f"{mode} started there by: {started_by}",
        f"{mode} its tools are listed as `mcp__{server}__...`. They "
        "run on that machine,",
        f"{mode} behind Hermes' own approval prompts, and only in a "
        f"session on `{spec.name}`.",
    ]

    if apply:
        lines.append(f"{mode} handing over to `hermes mcp add`:")
        return "\n".join(lines)

    lines += [
        "",
        f"{mode} the call that adds it:",
        command,
        "",
        f"{mode} nothing was added. Add --apply before `--` to add it.",
    ]

    return "\n".join(lines)


def format_screen_report(spec: TargetSpec, seat, problems, notes) -> str:
    """The output of `hermes onsite screen`."""

    desktop = seat.variables.get("XDG_CURRENT_DESKTOP", "")
    described = seat.kind

    if desktop:
        described = f"{seat.kind} ({desktop})"

    lines = [
        f"Screen of {spec.label}",
        f"  System   {seat.system or 'unknown'}",
        f"  Desktop  {described}",
        f"  Driver   {seat.driver or 'not installed'}",
        "",
    ]

    if problems:
        lines.append("It cannot be driven yet:")
        lines += [f"  - {problem}" for problem in problems]
    else:
        lines.append("It can be driven.")

    if notes:
        lines.append("")
        lines.append("Know before you rely on it:")
        lines += [f"  - {note}" for note in notes]

    return "\n".join(lines)


def format_screen_install(
    mode: str,
    link: str,
    source: str,
    in_place: bool,
    blocked: bool,
    apply: bool,
) -> str:
    """The output of `hermes onsite screen-install`."""

    if blocked:
        return (
            f"{mode} {link} is already there and is something else. "
            "Move it aside first. Nothing was changed."
        )

    lines = []

    if in_place:
        lines.append(f"{mode} the screen backend is already at {link}.")
    elif apply:
        lines.append(f"{mode} linked {link}")
        lines.append(f"{mode}     to {source}.")
    else:
        lines.append(f"{mode} would link {link}")
        lines.append(f"{mode}         to {source}.")
        lines.append(f"{mode} nothing was changed. Add --apply to link it.")

    lines += [
        "",
        "Then choose it as this profile's screen backend:",
        f"  hermes config set computer_use.backend {SCREEN_PLUGIN}",
        "",
        "A session on this machine keeps this machine's screen. A "
        "session on a target",
        "gets that target's screen. Check a target with "
        "`hermes onsite screen <target>`.",
    ]

    return "\n".join(lines)


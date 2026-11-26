"""Every piece of text the plugin shows a person or a model.

Kept in one module so the wording is consistent between the CLI, the
TUI, the desktop app and the system prompt, and so it can be tested.

This module imports nothing from Hermes.
"""

from __future__ import annotations

from typing import Any

from .config import OnsiteConfig
from .config import KIND_LOCAL
from .config import SCREEN_DRIVER
from .config import SCREEN_SHARING
from .config import TargetSpec
from .moves import Move
from .proposals import Proposal
from .rfb import SECURITY_PASSWORD
from .routing import REASON_DEFAULT
from .routing import REASON_MOUNT
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
    REASON_MOUNT: "the session folder is on its disk",
    REASON_DEFAULT: "no folder rule matched, so the default was used",
}

# Hermes adds a move note to the user's own message. These words tell
# the agent the user did not type it. The rig looks for them too.
MOVE_NOTE_OPENING = "Note from the onsite plugin, not typed by the user"
SCREEN_PLUGIN = "onsite-screen"

ROUTE_WORDS = {
    SCREEN_DRIVER: "Hermes' driver",
    SCREEN_SHARING: "screen sharing, by position only",
}

# What the agent reads with a picture taken through screen sharing.
SHARING_CAPTURE_NOTE = (
    "this is the whole screen through screen sharing, which shows "
    "pixels only: there are no numbered elements. Act with "
    "coordinate=[x, y] read from this picture"
)
SHARING_NO_ELEMENTS = (
    "this screen is reached through screen sharing, which shows "
    "pixels only, so there is no element to name. Pass "
    "coordinate=[x, y] read from a capture."
)
SHARING_NO_WINDOWS = (
    "this screen is reached through screen sharing, which cannot "
    "list or focus apps. Click the window in a capture instead."
)

NOTHING_TO_MOUNT = (
    "No such target to mount. Only a target reached over SSH has a "
    "disk to mount. `hermes onsite status` lists the targets."
)
OPEN_USAGE = (
    "Name a target, or a folder on one as TARGET:FOLDER, such as "
    "laptop:~/work."
)

SWITCH_HINT = (
    "Switch with /onsite <target>. Go back to the rules with /onsite auto."
)

FOLDER_UNKNOWN = (
    "This session has not said which folder it is in yet. The target "
    "is settled at its first message, so this may change."
)

NOT_IN_USE = (
    "onsite is set up but not in use. This profile's terminal backend "
    "is not `onsite`, so every command runs on the host. On the host, "
    "run `hermes config set terminal.backend onsite`. It applies to "
    "sessions started after that."
)

INJECTION_HINT = (
    "This client does not let a plugin hand the agent a message. To "
    "allow it, run `hermes config set "
    "plugins.entries.onsite.allow_gateway_injection true` on the "
    "host. Until then, ask the agent to run it:"
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

    lines.append("The agent is told with your next message.")

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


def format_move_note(
    move: Move,
    config: OnsiteConfig,
    screen_note: bool = False,
) -> str:
    """What an agent reads, once, after `/onsite` moved its session.

    Hermes adds this to the user's next message, so it opens by saying
    who wrote it. It is one paragraph: the agent's instructions still
    name the old machine, and this has to read as the newer word.

    With `screen_note`, it also says that the screen did not move.
    Hermes keeps a session's screen backend once it is built, and an
    agent told that its tools moved would assume its screen did too.
    """

    spec = config.target(move.after)
    start = "its home folder" if move.cwd == "~" else f"`{move.cwd}`"

    lead = f"The user moved this session to `{move.after}` with `/onsite`."

    if move.cleared:
        lead = (
            f"The user sent this session back to `{move.after}`, the "
            "machine its folder picks, with `/onsite auto`."
        )

    sentences = [
        f"[{MOVE_NOTE_OPENING}.",
        lead,
        "Your `terminal`, `read_file`, `write_file`, `patch` and "
        f"`search_files` tools now run on {describe_target(spec)}.",
        f"They start in {start}.",
        f"Until now they ran on `{move.before}`.",
        "This replaces what your instructions say about the machine.",
        "Paths, files and command output from earlier in this chat "
        f"belong to `{move.before}` and may not exist on "
        f"`{move.after}`.",
    ]

    if screen_note:
        sentences.append(
            "The `computer_use` tool did not move: if you have used it "
            "in this chat, it still sees and acts on the screen of "
            f"`{move.before}`. Tell the user to start a new chat to "
            f"use the screen of `{move.after}`."
        )

    return " ".join(sentences) + "]"


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


def format_mounted(spec: TargetSpec, root: str, start: str) -> str:
    """The output of `hermes onsite mount` for one target that is up."""

    lines = [f"{spec.label} is mounted at:", f"  {root}"]

    if start:
        lines += ["A new session on it starts in:", f"  {start}"]

    return "\n".join(lines)


def format_opened(spec: TargetSpec, target_folder: str, host: str) -> str:
    """The output of `hermes onsite open`."""

    return "\n".join([
        f"{target_folder} on {spec.label} is here on this host:",
        host,
        "Start a session in that folder to work there.",
    ])


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


def format_sharing_failed(
    spec: TargetSpec,
    reason: str,
    driver_missing: bool,
) -> str:
    """Why a session could not start on a target's screen sharing."""

    lines = [
        f"the screen of {spec.label} cannot be reached through screen "
        f"sharing on port {spec.screen_port}: {reason}.",
    ]

    if driver_missing:
        lines.append(
            "Hermes' screen driver is not installed there either. "
            "Turn on screen sharing on that machine, or install the "
            "driver there with `hermes computer-use install`."
        )

    lines.append(f"Check with `hermes onsite screen {spec.name}`.")

    return " ".join(lines)


def format_sharing_lost(spec: TargetSpec, reason: str) -> str:
    return (
        f"the screen of {spec.label} stopped answering: {reason}. "
        "Nothing more was sent. The next request opens the link again."
    )


def describe_sharing(spec: TargetSpec, sharing) -> str:
    """One line on whether a target's screen sharing answered."""

    if not sharing.ok:
        return f"no answer on port {spec.screen_port}"

    hello = sharing.hello
    asked = "no password asked"

    if hello.security == SECURITY_PASSWORD:
        asked = "password accepted"

    return (
        f"answers on port {spec.screen_port}: {hello.width} by "
        f"{hello.height}, {asked}"
    )


def format_screen_report(
    spec: TargetSpec,
    seat,
    sharing,
    route: str,
    problems,
    notes,
) -> str:
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
        f"  Sharing  {describe_sharing(spec, sharing)}",
        f"  Route    {ROUTE_WORDS[route]} (`screen: {spec.screen}`)",
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


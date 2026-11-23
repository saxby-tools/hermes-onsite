"""Hooks into the tool loop.

Three jobs, and none reroutes a tool call.

The first makes sure the file tools resolve paths against the same
folder, on the same machine, as the terminal tool. The reasons are in
`compat.anchor_file_tools`.

The second refuses an MCP tool that runs on one target when the
session runs on another. Hermes loads MCP servers for a whole
profile, so every session can see them. A session must still never
act on a second machine by itself. See `mcptools`.

The third tells an agent, once, that `/onsite` moved its session.
Hermes adds the note to the user's next message. See `moves`.

A session-open hook was tried, to learn a session's folder before
its first message. Hermes fires that hook without the folder in
scope, so it could not help and was removed.
"""

from __future__ import annotations

import logging

from . import messages
from . import mcptools
from .config import ConfigError
from .runtime import Runtime


logger = logging.getLogger(__name__)

HOOK_NAME = "pre_tool_call"
TURN_HOOK_NAME = "pre_llm_call"
ACTION_BLOCK = "block"
FILE_TOOLS = frozenset({
    "read_file",
    "write_file",
    "patch",
    "search_files",
})


def anchor(tool_name: str, task_id: str) -> None:
    from .compat import anchor_file_tools

    try:
        anchor_file_tools(task_id or "default")
    except Exception:
        # A target that is down is reported by the tool call itself,
        # with the target's name. Failing here would hide that.
        logger.debug("onsite: could not anchor %s", tool_name, exc_info=True)


def session_target(runtime: Runtime, config) -> str:
    from . import compat

    route, _, _ = runtime.locate(
        config,
        key=compat.environment_key(),
        session_id=compat.current_session_id(),
        cwd=compat.current_session_cwd(),
    )

    return route.target


def refuse_foreign_mcp(runtime: Runtime, tool_name: str):
    """A block for an MCP tool on another machine, or None to let it run.

    When the session's machine cannot be worked out, the call goes
    ahead. The tool is named for the machine it runs on, and Hermes'
    own gates still stand in front of it.
    """

    try:
        config = runtime.load_config()
    except ConfigError:
        return None

    owner = mcptools.owner_of(tool_name, config)

    if owner is None:
        return None

    try:
        current = session_target(runtime, config)
    except Exception:
        logger.debug("onsite: no session target for %s", tool_name,
                     exc_info=True)
        return None

    if current == owner:
        return None

    return {
        "action": ACTION_BLOCK,
        "message": messages.format_mcp_refused(tool_name, owner, current),
    }


def build_hook(runtime: Runtime):
    def before_tool_call(tool_name: str = "", task_id: str = "", **kwargs):
        if tool_name in FILE_TOOLS:
            anchor(tool_name, task_id)
            return None

        if tool_name.startswith(mcptools.TOOL_PREFIX):
            return refuse_foreign_mcp(runtime, tool_name)

        return None

    return before_tool_call


def session_names() -> list[str]:
    """The ids Hermes itself has for the session in this turn.

    The hook is handed two ids. These two more are asked for because
    the slash command may have filed the move under either.
    """

    from . import compat

    names = []

    for read in (compat.environment_key, compat.current_session_id):
        try:
            names.append(str(read() or ""))
        except Exception:
            logger.debug("onsite: no session name", exc_info=True)

    return names


def screen_is_ours() -> bool:
    """Whether this profile's screen follows a session's machine."""

    from . import compat

    try:
        return compat.screen_backend_name() == messages.SCREEN_PLUGIN
    except Exception:
        logger.debug("onsite: no screen backend name", exc_info=True)

        return False


def build_turn_hook(runtime: Runtime):
    def before_turn(session_id: str = "", task_id: str = "", **kwargs):
        move = runtime.moves.take(
            task_id or "",
            session_id or "",
            *session_names(),
        )

        if move is None:
            return None

        try:
            config = runtime.load_config()
        except ConfigError:
            return None

        # The target can be removed between the move and the turn.
        # The terminal tool reports that itself, in its own words.
        if move.after not in config.targets:
            return None

        note = messages.format_move_note(move, config, screen_is_ours())

        return {"context": note}

    return before_turn


def register(ctx, runtime: Runtime) -> None:
    ctx.register_hook(HOOK_NAME, build_hook(runtime))
    ctx.register_hook(TURN_HOOK_NAME, build_turn_hook(runtime))

"""Hooks into the tool loop.

One hook, and it changes no tool call. It only makes sure the file
tools resolve paths against the same folder, on the same machine, as
the terminal tool. The reasons are in `compat.anchor_file_tools`.
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)

HOOK_NAME = "pre_tool_call"
FILE_TOOLS = frozenset({
    "read_file",
    "write_file",
    "patch",
    "search_files",
})


def before_tool_call(tool_name: str = "", task_id: str = "", **kwargs):
    if tool_name not in FILE_TOOLS:
        return None

    from .compat import anchor_file_tools

    try:
        anchor_file_tools(task_id or "default")
    except Exception:
        # A target that is down is reported by the tool call itself,
        # with the target's name. Failing here would hide that.
        logger.debug("hands: could not anchor %s", tool_name, exc_info=True)

    return None


def register(ctx) -> None:
    ctx.register_hook(HOOK_NAME, before_tool_call)

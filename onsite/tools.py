"""The model-facing tool: propose a command for the user to run."""

from __future__ import annotations

import json

from . import messages
from .runtime import Runtime


TOOL_NAME = "propose_command"
TOOLSET = "onsite"
DESKTOP_SURFACE = "desktop"

DESCRIPTION = (
    "Propose a shell command for the user to run, instead of running "
    "it yourself. Use it when a command should be the user's call: it "
    "reaches outside the project, it cannot be undone, or the user "
    "asked to see commands first. The command is saved with a number "
    "and is NOT run. The user runs it with `/run <number>`, and it "
    "then goes through the normal terminal tool and its approval "
    "prompt."
)

SCHEMA = {
    "name": TOOL_NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The exact shell command to propose.",
            },
            "why": {
                "type": "string",
                "description": "One short sentence on what it does.",
            },
        },
        "required": ["command"],
    },
}


def error(message: str) -> str:
    return json.dumps({"error": message})


def build_handler(runtime: Runtime):
    def propose_command(args: dict, **kwargs) -> str:
        from .compat import current_session_id
        from .compat import current_session_key
        from .compat import current_surface

        command = str(args.get("command") or "").strip()

        if not command:
            return error("`command` is required and must not be empty.")

        session_id = str(kwargs.get("session_id") or current_session_id())
        session_key = current_session_key()

        proposal = runtime.proposals.add(
            session_key or session_id,
            command,
            str(args.get("why") or "").strip(),
            ids=(session_key, session_id),
        )

        return messages.format_proposal_result(
            proposal,
            with_card=current_surface() == DESKTOP_SURFACE,
        )

    return propose_command


def register(ctx, runtime: Runtime) -> None:
    ctx.register_tool(
        TOOL_NAME,
        TOOLSET,
        SCHEMA,
        build_handler(runtime),
        description=DESCRIPTION,
    )

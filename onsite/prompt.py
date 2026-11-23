"""The per-session note in the system prompt.

Hermes describes a remote backend once and caches the text across
sessions. With one target per session, that text cannot name the
machine. This section is rendered for each new session, so it can.
"""

from __future__ import annotations

import logging

from . import messages
from . import routing
from .config import ConfigError
from .runtime import Runtime


logger = logging.getLogger(__name__)

POSITION = "after_memory"
MAX_CHARS = 1500


def build_section(runtime: Runtime):
    def render(info) -> str:
        from .compat import current_session_cwd

        try:
            config = runtime.load_config()
        except ConfigError:
            # The terminal tool is unavailable in this state and says
            # why. A prompt note about it would only add noise.
            return ""

        from .compat import current_session_key

        session_id = str(info.get("session_id") or "")
        folder = current_session_cwd() or str(info.get("cwd") or "")

        runtime.folders.note(folder, session_id, current_session_key())

        pin = runtime.find_pin(session_id=session_id)
        pinned = None if pin is None else pin.target

        route = routing.resolve(folder, config, pinned)

        return messages.format_prompt_section(route, config)

    return render


def register(ctx, runtime: Runtime) -> None:
    ctx.register_system_prompt_section(
        messages.PROMPT_SECTION_ID,
        build_section(runtime),
        position=POSITION,
        max_chars=MAX_CHARS,
    )

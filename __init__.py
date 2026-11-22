"""hermes-hands: a remote Hermes agent acts on the machine you sit at.

The agent's loop, memory and skills stay on the host. Shell commands
and file edits run on a named target, picked per session from the
session's folder. See README.md.

Nothing from Hermes is imported at module level. The loader gives a
plugin ten seconds to import and register, and the pure modules here
are tested without Hermes installed.
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


def register(ctx) -> None:
    from . import commands
    from . import hooks
    from . import prompt
    from . import tools
    from .provider import build_provider
    from .runtime import build_runtime

    runtime = build_runtime()

    # The provider is registered even when the settings are broken.
    # Otherwise `hermes hands init`, which fixes them, would not load.
    ctx.register_terminal_environment_provider(build_provider(runtime))

    hooks.register(ctx)
    commands.register(ctx, runtime)
    tools.register(ctx, runtime)
    prompt.register(ctx, runtime)

    runtime.log_config_problems()

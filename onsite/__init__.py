"""onsite: a remote Hermes agent acts on the machine you sit at.

The agent's loop, memory and skills stay on the host. Shell commands
and file edits run on a named target, picked per session from the
session's folder. See README.md.

Nothing from Hermes is imported at module level. The loader gives a
plugin ten seconds to import and register, and the pure modules here
are tested without Hermes installed.
"""

from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)

# What `os.name` reads on Windows.
UNSUPPORTED_HOST = "nt"

# The runtime of the current load. The dashboard routes read it so
# they share this process's circuit breakers. See
# dashboard/plugin_api.py.
RUNTIME = None


def get_runtime():
    """The runtime Hermes registered, or a new one when this copy of
    the package was loaded by the dashboard alone."""

    global RUNTIME

    if RUNTIME is None:
        from .runtime import build_runtime

        RUNTIME = build_runtime()

    return RUNTIME


def refuse_unsupported_host() -> None:
    """Stop with a plain message on a server onsite has never run on.

    Hermes itself runs on Windows. This plugin has not been run or
    tested there, and its SSH timings assume a link that stays open,
    which Windows' own SSH client does not keep. A clear refusal is
    kinder than a backend that half works.
    """

    if os.name != UNSUPPORTED_HOST:
        return

    raise RuntimeError(
        "onsite does not support a Hermes server on Windows yet. It "
        "runs on Linux and macOS servers. Disable it here with "
        "`hermes plugins disable onsite`."
    )


def register(ctx) -> None:
    global RUNTIME

    refuse_unsupported_host()

    from . import commands
    from . import compat
    from . import hooks
    from . import prompt
    from . import tools
    from .provider import build_provider
    from .runtime import build_runtime

    compat.bind(ctx)

    runtime = build_runtime()
    RUNTIME = runtime

    # The provider is registered even when the settings are broken.
    # Otherwise `hermes onsite init`, which fixes them, would not load.
    ctx.register_terminal_environment_provider(build_provider(runtime))

    hooks.register(ctx, runtime)
    commands.register(ctx, runtime)
    tools.register(ctx, runtime)
    prompt.register(ctx, runtime)

    runtime.log_config_problems()

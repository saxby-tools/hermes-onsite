"""Screen control that follows an onsite session to its machine.

Hermes picks one screen backend for a whole profile. This one hands
each session the screen of the machine that session runs on: the
host's own screen for a session on the host, a target's screen for a
session on a target. The work is done in the onsite plugin.
This folder exists because Hermes finds a screen backend by the name
of its folder, which must be the value of `computer_use.backend`.

Nothing here reroutes a tool call. Hermes' own `computer_use` tool,
its approval prompt and its hard blocks run as they always do.
"""

from __future__ import annotations

import importlib
import sys

from pathlib import Path

from tools.computer_use.backend import ComputerUseBackend
from tools.computer_use.backend import ComputerUseProvider


NAME = "onsite-screen"
DISPLAY_NAME = "onsite (the session's own machine)"

MAIN_PLUGIN = "hermes_plugins.onsite"
# This folder ships inside the onsite plugin and is linked
# into place, so the plugin itself is one folder up.
MAIN_MANIFEST = Path(__file__).resolve().parent.parent / "plugin.yaml"
BACKEND_MODULE = "screenbackend"

NOT_ENABLED = (
    "the onsite plugin is not enabled in this profile, so there "
    "is no machine to pick a screen for. Enable it, or set "
    "`computer_use.backend` back to `cua`."
)


class OnsiteScreenProvider(ComputerUseProvider):
    name = NAME
    display_name = DISPLAY_NAME

    def is_available(self) -> bool:
        # Cheap, as Hermes asks: nothing is dialed until a session
        # first uses its screen. Whether onsite is enabled is
        # settled then, with a message that says what to do.
        return MAIN_MANIFEST.is_file()

    def create_backend(self, *, permission_mode: str) -> ComputerUseBackend:
        package = sys.modules.get(MAIN_PLUGIN)

        if package is None:
            raise RuntimeError(NOT_ENABLED)

        module = importlib.import_module(f"{MAIN_PLUGIN}.{BACKEND_MODULE}")

        return module.build_backend(package.get_runtime(), permission_mode)


def register(ctx) -> None:
    ctx.register_computer_use_provider(OnsiteScreenProvider())

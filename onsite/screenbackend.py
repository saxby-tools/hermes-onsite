"""Screen control on the session's own machine.

Hermes drives a screen through a driver program it talks to over the
program's input and output. Here the driver runs on a target and that
conversation travels over SSH. Everything above the wire is Hermes'
own code, reused as it is: how a window is picked, how an element is
named, how an action is verified. Only three things are replaced,
because they assume the driver is a program on the host:

- how the driver is started,
- the fallback that runs the driver's command-line form,
- the checks, at start, that a driver is installed on the host.

Hermes' approval prompt and its hard blocks sit above all of this and
are untouched. No approval is added, skipped or answered here.

These are private Hermes classes, not plugin API. `check_internals`
names every one this file leans on and stops with a plain message if
a Hermes release moves one. Nothing here is imported until a session
first uses its screen, so a moved class cannot stop the plugin from
loading.

The plugin never installs a driver on a target. `screen.read_seat`
says what is missing, and the owner decides.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os

from tools.computer_use import cua_backend
from tools.computer_use.cua_backend import CuaDriverBackend
from tools.computer_use.cua_backend_parse import _z_index_uninformative
from tools.computer_use.cua_backend_session import _CuaDriverSession

from . import compat
from . import screen
from .config import ConfigError
from .config import KIND_LOCAL
from .config import TargetSpec
from .remote import build_stdio_arguments
from .runtime import Runtime


logger = logging.getLogger(__name__)

SSH_BINARY = "ssh"

MODE_STANDARD = "standard"
MODE_BOUNDED = "bounded"

SDK_EXTRA = "computer-use"

PHASE_CONNECT = "ssh-connect"
PHASE_INITIALIZE = "mcp-initialize"
PHASE_DISCOVER = "capability-discovery"
PHASE_READY = "ready"

# What the `ssh` program itself needs from the host. The driver gets
# its own variables on the target, from `screen.build_driver_script`.
LOCAL_VARIABLES = ("PATH", "HOME", "USER", "LANG", "SSH_AUTH_SOCK")

SESSION_MEMBERS = (
    "_populate_capabilities",
    "_call_tool_via_cli",
    "_lifecycle_coro",
    "set_transport_reset_callback",
    "start",
    "call_tool",
)
BACKEND_MEMBERS = (
    "_best_effort",
    "_handle_transport_reset",
    "list_windows",
    "set_config",
    "set_agent_cursor_enabled",
    "stop",
)
MODULE_MEMBERS = ("_computer_use_max_image_dimension",)


def check_internals() -> None:
    """Stop with a plain message if Hermes moved what this file uses."""

    missing = [
        f"_CuaDriverSession.{name}"
        for name in SESSION_MEMBERS
        if not hasattr(_CuaDriverSession, name)
    ]
    missing += [
        f"CuaDriverBackend.{name}"
        for name in BACKEND_MEMBERS
        if not hasattr(CuaDriverBackend, name)
    ]
    missing += [
        f"cua_backend.{name}"
        for name in MODULE_MEMBERS
        if not hasattr(cua_backend, name)
    ]

    if not missing:
        return

    raise RuntimeError(
        "this Hermes release changed the screen driver code that "
        "onsite builds on: " + ", ".join(missing) + ". Set "
        "`computer_use.backend` back to `cua` until the plugin is "
        "updated."
    )


def local_environment() -> dict:
    return {
        name: os.environ[name]
        for name in LOCAL_VARIABLES
        if os.environ.get(name)
    }


def native_wayland() -> bool:
    """The owner's opt-in for input on Wayland, as Hermes reads it."""

    from hermes_cli.config import load_config_readonly

    settings = (load_config_readonly() or {}).get("computer_use") or {}

    return bool(settings.get("native_wayland", False))


def ensure_sdk() -> None:
    """Make the MCP client importable, the way Hermes' own backend does."""

    try:
        from pm import ensure_import
    except ImportError:
        return

    ensure_import(SDK_EXTRA)
    importlib.invalidate_caches()


class TargetDriverSession(_CuaDriverSession):
    """Hermes' driver session, with the driver on a target."""

    def __init__(self, bridge, arguments: list[str], label: str):
        super().__init__(bridge, None)

        self._onsite_arguments = arguments
        self._onsite_label = label

    async def _lifecycle_coro(self) -> None:
        """Own the link to the driver for as long as the session lives.

        The same steps as Hermes' own, less the search for a driver
        on the host. The flags it sets are the ones the rest of the
        session reads.
        """

        from mcp import ClientSession
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._shutdown_event = asyncio.Event()
        self._startup_phase = PHASE_CONNECT

        try:
            params = StdioServerParameters(
                command=SSH_BINARY,
                args=self._onsite_arguments,
                env=local_environment(),
            )

            async with stdio_client(params) as (read, write):
                self._startup_phase = PHASE_INITIALIZE

                async with ClientSession(read, write) as session:
                    await session.initialize()

                    self._startup_phase = PHASE_DISCOVER
                    await self._populate_capabilities(session)

                    self._session = session
                    self._startup_phase = PHASE_READY
                    self._ready_event.set()

                    await self._shutdown_event.wait()
        except BaseException as error:
            self._setup_error = error
            self._ready_event.set()
            raise
        finally:
            # A link that dies for any reason can be opened again.
            self._session = None
            self._started = False

    def _call_tool_via_cli(self, name: str, args: dict, timeout: float):
        # Hermes' fallback runs the driver on the host and reads a
        # file the driver names. Neither holds for a target, and a
        # file path from another machine must never be read here.
        raise RuntimeError(
            f"the driver on {self._onsite_label} gave no answer to "
            f"`{name}`, and it has no second way to be asked"
        )


class TargetScreenBackend(CuaDriverBackend):
    """Hermes' own screen backend, pointed at a target's screen."""

    def __init__(self, spec: TargetSpec, permission_mode: str):
        # Hermes' other modes start a private driver on the host with
        # a file from the host. Neither exists on a target.
        super().__init__(MODE_STANDARD)

        self._onsite_spec = spec
        self._onsite_mode = permission_mode

    def _onsite_start(self) -> None:
        if self._onsite_mode == MODE_BOUNDED:
            raise screen.ScreenError(
                "`computer_use.permission_mode: bounded` reads a file "
                "on the host and is not available on a target"
            )

        seat = screen.read_seat(self._onsite_spec)
        problems = screen.find_problems(seat)

        if problems:
            raise screen.ScreenError(" ".join(problems))

        ensure_sdk()

        script = screen.build_driver_script(seat, native_wayland())

        self._session = TargetDriverSession(
            self._bridge,
            build_stdio_arguments(self._onsite_spec, script),
            self._onsite_spec.label,
        )
        self._session.set_transport_reset_callback(
            self._handle_transport_reset,
        )
        self._session.start()

        self._best_effort(
            "start_session failed (continuing anonymous)",
            self._session.call_tool,
            "start_session",
            {"session": self._session_id},
        )

        limit = cua_backend._computer_use_max_image_dimension()

        if limit:
            self._best_effort(
                "set_config(max_image_dimension) failed",
                self.set_config,
                max_image_dimension=limit,
            )

        # Nobody at the target asked for a second cursor on screen.
        self._best_effort(
            "set_agent_cursor_enabled failed",
            self.set_agent_cursor_enabled,
            False,
            cursor_id=self._session_id,
        )

    def start(self) -> None:
        try:
            self._onsite_start()
        except Exception as error:
            # Hermes does not stop a backend whose start failed, and
            # the session's thread would be left behind.
            self._best_effort("stop after a failed start", self.stop)

            raise RuntimeError(
                f"the screen of {self._onsite_spec.label} cannot be "
                f"driven: {error}"
            ) from None

    def is_available(self) -> bool:
        return True

    def list_windows(self) -> list:
        """The target's windows, in an order the host cannot second-guess.

        When every window has the same rank, Hermes asks the display
        of the machine it runs on which window is in front. That is
        the wrong machine. Ranks taken from the driver's own order
        keep that question from being asked.
        """

        windows = super().list_windows()

        if not _z_index_uninformative(windows):
            return windows

        count = len(windows)

        return [
            {**window, "z_index": count - position}
            for position, window in enumerate(windows)
        ]


def session_spec(runtime: Runtime) -> TargetSpec:
    """The target of the session whose tool call is running now."""

    try:
        config = runtime.load_config()
    except ConfigError as error:
        raise RuntimeError(str(error)) from None

    route, _, _ = runtime.locate(
        config,
        key=compat.environment_key(),
        session_id=compat.current_session_id(),
        cwd=compat.current_session_cwd(),
    )

    return config.target(route.target)


def build_backend(runtime: Runtime, permission_mode: str):
    """A screen backend for this session's machine.

    Called on the thread of the tool call, where the session is still
    in scope. The target is settled here and kept, because the
    driver's own thread cannot see which session it serves.
    """

    check_internals()

    spec = session_spec(runtime)

    # A session on the host keeps Hermes' own backend and the host's
    # own screen, exactly as if this plugin were not selected.
    if spec.kind == KIND_LOCAL:
        return CuaDriverBackend(permission_mode=permission_mode)

    logger.info("onsite: screen control for this session is on %s", spec.label)

    return TargetScreenBackend(spec, permission_mode)

"""The plugin's shared state and the decisions made on it.

One Runtime serves the provider, the commands, the tool and the
status endpoint, so they cannot disagree about which target a session
uses.

Everything Hermes-specific is passed in. That keeps the routing and
pinning rules testable without Hermes installed.
"""

from __future__ import annotations

import logging
import threading

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable

from . import routing
from .config import ConfigError
from .config import HandsConfig
from .config import TargetSpec
from .config import is_absolute
from .config import parse_config
from .health import Gate
from .pins import Pins
from .pins import SOURCE_OVERRIDE
from .pins import SOURCE_ROUTE
from .pins import is_session_key
from .proposals import Proposals
from .store import JsonStore


logger = logging.getLogger(__name__)

PINS_FILE = "pins.json"
PROPOSALS_FILE = "proposals.json"

CLEAR_OVERRIDE = "auto"


@dataclass(frozen=True)
class SessionReader:
    """How to ask Hermes about the session a call is running for."""

    cwd: Callable[[], str]
    session_id: Callable[[], str]


class UnknownTarget(ValueError):
    def __init__(self, name: str, known: list[str]):
        self.name = name
        self.known = known

        choices = ", ".join(known)

        super().__init__(f"`{name}` is not a target. Targets: {choices}.")


class Runtime:
    def __init__(
        self,
        load_settings: Callable[[], Any],
        data_dir: Path | None,
        session: SessionReader,
        build_environment: Callable[..., Any],
    ):
        self._load_settings = load_settings
        self._session = session
        self._build_environment = build_environment
        self._gates: dict[tuple, Gate] = {}
        self._gates_lock = threading.Lock()

        pins_path = None if data_dir is None else data_dir / PINS_FILE
        proposals_path = (
            None if data_dir is None else data_dir / PROPOSALS_FILE
        )

        self.pins = Pins(JsonStore(pins_path))
        self.proposals = Proposals(JsonStore(proposals_path))

    def load_config(self) -> HandsConfig:
        """Read the settings fresh, so an edit applies to the next
        session with no restart."""

        return parse_config(self._load_settings())

    def config_problem(self) -> str | None:
        try:
            self.load_config()
        except ConfigError as error:
            return str(error)

        return None

    def log_config_problems(self) -> None:
        problem = self.config_problem()

        if problem is None:
            return

        logger.error("%s", problem)

    def gate_for(self, spec: TargetSpec) -> Gate:
        # Keyed on where the target points, not on its name alone, so
        # an edited host does not inherit the old host's breaker.
        key = (spec.name, spec.kind, spec.host, spec.user, spec.port)

        with self._gates_lock:
            gate = self._gates.get(key)

            if gate is None:
                gate = Gate(spec)
                self._gates[key] = gate

            return gate

    def plan(
        self,
        key: str,
        cwd: str,
        config: HandsConfig,
        session_id: str = "",
    ) -> routing.Route:
        """Pick the target for an environment and pin a new session."""

        pin = self.pins.get(key)

        if pin is not None and pin.target in config.targets:
            reason = routing.REASON_PINNED

            if pin.is_override:
                reason = routing.REASON_OVERRIDE

            return routing.resolve(cwd, config, pin.target, reason)

        route = routing.route(cwd, config)

        # Sessionless work shares one key. Pinning it would send the
        # next cron job wherever the last one happened to run.
        if is_session_key(key):
            self.pins.set(key, route.target, SOURCE_ROUTE, cwd, session_id)

        return route

    def create_environment(self, cwd: str, timeout: int, task_id: str):
        config = self.load_config()
        session_cwd = self._session.cwd()

        # The session's folder is the truth. The `cwd` argument is
        # only the backend's start folder, and for a CLI or TUI
        # session it is a placeholder.
        folder = session_cwd

        if not folder and is_absolute(cwd):
            folder = cwd

        route = self.plan(task_id, folder, config, self._session.session_id())
        spec = config.target(route.target)

        logger.info(
            "hands: %s runs on %s in %s (%s)",
            task_id,
            spec.label,
            route.cwd,
            route.reason,
        )

        return self._build_environment(
            spec,
            self.gate_for(spec),
            route.cwd,
            timeout,
        )

    def switch(
        self,
        key: str,
        target: str,
        cwd: str,
        session_id: str = "",
    ) -> routing.Route:
        """`/hands <target>`: pin a session, or clear with `auto`."""

        config = self.load_config()

        if target == CLEAR_OVERRIDE:
            self.pins.clear(key)

            return routing.route(cwd, config)

        if target not in config.targets:
            raise UnknownTarget(target, list(config.targets))

        self.pins.set(key, target, SOURCE_OVERRIDE, cwd, session_id)

        return routing.resolve(cwd, config, target, routing.REASON_OVERRIDE)

    def find_pin(self, key: str = "", session_id: str = ""):
        return self.pins.get(key) or self.pins.find(session_id)

    def status(
        self,
        key: str = "",
        session_id: str = "",
        cwd: str = "",
    ) -> dict[str, Any]:
        """Where a session runs and whether that target answers now.

        The answer comes from a live probe on every call. It is never
        read from a cache.
        """

        try:
            config = self.load_config()
        except ConfigError as error:
            return {"ok": False, "error": str(error)}

        pin = self.find_pin(key, session_id)
        folder = cwd

        if not folder and pin is not None:
            folder = pin.cwd

        route = routing.route(folder, config)

        if pin is not None and pin.target in config.targets:
            reason = routing.REASON_PINNED

            if pin.is_override:
                reason = routing.REASON_OVERRIDE

            route = routing.resolve(folder, config, pin.target, reason)

        spec = config.target(route.target)
        probe = self.gate_for(spec).check()

        return {
            "ok": True,
            "target": spec.name,
            "kind": spec.kind,
            "label": spec.label,
            "connected": probe.ok,
            "detail": probe.detail,
            "latency_ms": probe.elapsed_ms,
            "cwd": route.cwd,
            "reason": route.reason,
            "pinned": pin is not None,
            "default": config.default,
            "targets": list(config.targets),
        }


def build_runtime() -> Runtime:
    """Wire a Runtime to the running Hermes."""

    from . import compat
    from .targets import build_environment

    session = SessionReader(
        cwd=compat.current_session_cwd,
        session_id=compat.current_session_id,
    )

    return Runtime(
        load_settings=compat.load_settings,
        data_dir=compat.data_dir(),
        session=session,
        build_environment=build_environment,
    )

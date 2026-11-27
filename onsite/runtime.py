"""The plugin's shared state and the decisions made on it.

One Runtime serves the provider, the commands, the tool and the
status endpoint, so they cannot disagree about which target a session
uses.

Everything Hermes-specific is passed in. That keeps the routing and
pinning rules testable without Hermes installed.
"""

from __future__ import annotations

import copy
import logging
import threading
import time

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any
from typing import Callable

from . import mounts
from . import routing
from .config import ConfigError
from .config import KIND_SSH
from .config import OnsiteConfig
from .config import TargetSpec
from .config import is_absolute
from .config import parse_config
from .health import DECISION_REFUSE
from .health import Gate
from .moves import Move
from .moves import Moves
from .paths import PathError
from .paths import TARGET_ROOT
from .paths import clean_target
from .paths import to_host
from .pins import Folders
from .pins import Pins
from .pins import SOURCE_OVERRIDE
from .pins import SOURCE_ROUTE
from .pins import is_session_key
from .proposals import Proposals
from .store import JsonStore


logger = logging.getLogger(__name__)

PINS_FILE = "pins.json"
FOLDERS_FILE = "folders.json"
PROPOSALS_FILE = "proposals.json"

CLEAR_OVERRIDE = "auto"

# How long a mount that failed is left alone before it is tried again.
MOUNT_RETRY_SECONDS = 30.0

# How a person writes "where a login on the target lands".
HOME_MARK = "~"
HOME_PREFIX = "~/"


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
        save_settings: Callable[[dict], None] | None = None,
        run_allowed: Callable[[], bool] | None = None,
        backend_selected: Callable[[], bool] | None = None,
    ):
        self._load_settings = load_settings
        self._save_settings = save_settings
        self._run_allowed = run_allowed
        self._backend_selected = backend_selected
        self._data_dir = data_dir
        self._session = session
        self._build_environment = build_environment
        self._gates: dict[tuple, Gate] = {}
        self._gates_lock = threading.Lock()
        self._mount_failures: dict[str, tuple[float, str]] = {}
        self._mounts_lock = threading.Lock()

        pins_path = None if data_dir is None else data_dir / PINS_FILE
        folders_path = None if data_dir is None else data_dir / FOLDERS_FILE
        proposals_path = (
            None if data_dir is None else data_dir / PROPOSALS_FILE
        )

        self.moves = Moves()
        self.pins = Pins(JsonStore(pins_path))
        self.folders = Folders(JsonStore(folders_path))
        self.proposals = Proposals(JsonStore(proposals_path))

    def in_use(self) -> bool:
        """Whether Hermes runs this profile's commands through onsite.

        Asked each time. When it is false, every command runs on the
        host with Hermes' own backend, whatever the settings here say,
        so nothing may name a target as the place a session runs.
        """

        if self._backend_selected is None:
            return True

        return bool(self._backend_selected())

    def run_allowed(self) -> bool:
        """Whether `/run` can hand a command to the agent here.

        Asked each time, because the owner can change the setting
        while Hermes runs. With nobody to ask, the answer is yes, and
        `/run` itself still says so if Hermes refuses.
        """

        if self._run_allowed is None:
            return True

        return bool(self._run_allowed())

    def default_mount(self, name: str) -> str:
        """Where a target's disk is mounted when no place is set."""

        if self._data_dir is None:
            return ""

        return str(mounts.default_root(self._data_dir, name))

    def with_default_mounts(self, config: OnsiteConfig) -> OnsiteConfig:
        """Give every SSH target a mount root, so any of its folders
        can be opened with no set-up. A place named in the settings
        is kept as it is."""

        targets = {}

        for name, spec in config.targets.items():
            if spec.kind == KIND_SSH and not spec.mount:
                spec = replace(spec, mount=self.default_mount(name))

            targets[name] = spec

        return OnsiteConfig(default=config.default, targets=targets)

    def mount(self, spec: TargetSpec) -> mounts.Mounted:
        """A target's disk on the host, mounted now if it was not.

        A failure is remembered for a little while. The status chip
        asks every few seconds, and a server that lacks a program
        would otherwise be asked to start it every time.
        """

        if spec.kind != KIND_SSH or not spec.mount:
            raise mounts.MountError(f"{spec.label} has no disk to mount")

        if self._data_dir is None:
            raise mounts.MountError("this process has no data folder")

        now = time.monotonic()

        with self._mounts_lock:
            failed = self._mount_failures.get(spec.name)

        if failed is not None and now - failed[0] < MOUNT_RETRY_SECONDS:
            raise mounts.MountError(failed[1])

        try:
            found = mounts.ensure(spec, Path(spec.mount), self._data_dir)
        except mounts.MountError as error:
            with self._mounts_lock:
                self._mount_failures[spec.name] = (now, str(error))

            raise

        with self._mounts_lock:
            self._mount_failures.pop(spec.name, None)

        return found

    def mount_failure(self, name: str) -> str:
        """What the last try to mount a target said, or nothing if
        the last try worked."""

        with self._mounts_lock:
            failed = self._mount_failures.get(name)

        if failed is None:
            return ""

        return failed[1]

    def heal_mount(self, folder: str) -> None:
        """Mount again the disk a folder is on, if it has gone.

        A Linux mount lets go when its target stops answering, and a
        restart of the host takes every mount down. The desktop app
        puts one back while it is in view. A session in a terminal or
        a chat app has only this, called before a tool runs. It never
        raises: the tool call says in its own words what is wrong.
        """

        try:
            config = self.load_config()
        except ConfigError:
            return

        for spec in config.targets.values():
            if spec.kind != KIND_SSH:
                continue

            if not mounts.under_mount(folder, spec):
                continue

            self.mount_if_gone(spec)

            return

    def mount_if_gone(self, spec: TargetSpec) -> None:
        if mounts.is_mounted(Path(spec.mount), mounts.host_system()):
            return

        # A target the breaker holds shut is not dialed for a mount.
        if self.gate_for(spec).breaker.decide() == DECISION_REFUSE:
            return

        try:
            self.mount(spec)
        except mounts.MountError as error:
            logger.debug("onsite: no mount for %s: %s", spec.name, error)

    def host_folder(
        self,
        spec: TargetSpec,
        target_folder: str = "",
    ) -> tuple[Path, str]:
        """Where a folder on a target is on the host, and its full
        path on the target.

        Nothing given is the target's start folder. `~` and `~/x` are
        under the folder a login on the target lands in. The folder
        has to be there: it is looked for through the mount.
        """

        found = self.mount(spec)
        wanted = target_folder.strip()

        if wanted in ("", HOME_MARK):
            wanted = spec.start if not wanted else ""

        if wanted.startswith(HOME_PREFIX):
            wanted = f"{found.home}/{wanted[len(HOME_PREFIX):]}"

        if not wanted:
            wanted = found.home

        if not wanted or not wanted.startswith(TARGET_ROOT):
            raise mounts.MountError(
                f"{spec.label} did not say where a login starts. "
                "It may be asleep. Give the folder's full path."
            )

        try:
            shown = clean_target(wanted)
        except PathError as error:
            raise mounts.MountError(str(error)) from None

        folder = to_host(found.root, shown)
        seen = mounts.look_for_folder(folder)

        if seen == mounts.FOLDER_MISSING:
            raise mounts.MountError(
                f"{shown} is not a folder on {spec.label}."
            )

        if seen == mounts.FOLDER_AWAY:
            raise mounts.MountError(
                f"{spec.label} is not answering. It may be asleep."
            )

        return folder, shown

    def start_folder(self, spec: TargetSpec) -> Path:
        """The folder on the host a new session on a target opens in:
        the one the settings name, or the target's own."""

        return self.host_folder(spec)[0]

    def unmount(self, spec: TargetSpec) -> None:
        if not spec.mount or self._data_dir is None:
            return

        mounts.stop(spec, Path(spec.mount), self._data_dir)

    def load_config(self) -> OnsiteConfig:
        """Read the settings fresh, so an edit applies to the next
        session with no restart."""

        return self.with_default_mounts(parse_config(self._load_settings()))

    def raw_settings(self) -> dict:
        """The settings as written, for a form that edits them."""

        raw = self._load_settings()

        if not isinstance(raw, Mapping):
            return {"default": None, "targets": {}}

        settings = copy.deepcopy(dict(raw))

        if not isinstance(settings.get("targets"), Mapping):
            settings["targets"] = {}

        settings["targets"] = dict(settings["targets"])

        return settings

    def save_settings(self, settings: dict) -> None:
        """Check a whole settings block, then write it."""

        parse_config(settings)

        if self._save_settings is None:
            raise RuntimeError("these settings cannot be written from here")

        self._save_settings(settings)

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
        config: OnsiteConfig,
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

        session_id = self._session.session_id()

        self.folders.note(folder, task_id, session_id)

        route = self.plan(task_id, folder, config, session_id)
        spec = config.target(route.target)

        logger.info(
            "onsite: %s runs on %s in %s (%s)",
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
            folder,
        )

    def target_in_use(
        self,
        key: str,
        cwd: str,
        config: OnsiteConfig,
    ) -> str:
        """The target a session's next tool call would run on."""

        pin = self.pins.get(key)

        if pin is not None and pin.target in config.targets:
            return pin.target

        return routing.route(cwd, config).target

    def switch(
        self,
        key: str,
        target: str,
        cwd: str,
        session_id: str = "",
        session_key: str = "",
    ) -> routing.Route:
        """`/onsite <target>`: pin a session, or clear with `auto`.

        The move is also held for the session's agent, which is told
        on its next turn. See `moves`.
        """

        config = self.load_config()
        cleared = target == CLEAR_OVERRIDE

        if not cleared and target not in config.targets:
            raise UnknownTarget(target, list(config.targets))

        before = self.target_in_use(key, cwd, config)

        if cleared:
            self.pins.clear(key)
            route = routing.route(cwd, config)

        if not cleared:
            self.pins.set(key, target, SOURCE_OVERRIDE, cwd, session_id)
            route = routing.resolve(
                cwd,
                config,
                target,
                routing.REASON_OVERRIDE,
            )

        # Sessionless work shares one key. A move filed under it would
        # be told to whichever job spoke next.
        own_key = key if is_session_key(key) else ""

        self.moves.note(
            Move(before, route.target, route.cwd, cleared),
            own_key,
            session_id,
            session_key,
        )

        return route

    def find_pin(self, key: str = "", session_id: str = ""):
        return self.pins.get(key) or self.pins.find(session_id)

    def locate(
        self,
        config: OnsiteConfig,
        key: str = "",
        session_id: str = "",
        cwd: str = "",
    ):
        """A session's route, its pin and its folder. Nothing is dialed."""

        pin = self.find_pin(key, session_id)
        folder = cwd or self.folders.find(key, session_id)

        if not folder and pin is not None:
            folder = pin.cwd

        route = routing.route(folder, config)

        if pin is not None and pin.target in config.targets:
            reason = routing.REASON_PINNED

            if pin.is_override:
                reason = routing.REASON_OVERRIDE

            route = routing.resolve(folder, config, pin.target, reason)

        return route, pin, folder

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

        route, pin, folder = self.locate(config, key, session_id, cwd)
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
            "folder_known": bool(folder),
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
        save_settings=compat.save_settings,
        run_allowed=compat.run_allowed,
        backend_selected=compat.backend_selected,
    )

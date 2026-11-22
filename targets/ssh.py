"""The SSH target: commands run on another machine, as its user."""

from __future__ import annotations

import logging
import subprocess
import time

from .. import exits
from .. import remote
from ..compat import SSHEnvironment
from ..config import TargetSpec
from ..config import is_absolute
from ..config import normalize_path
from ..health import Gate
from ..health import TargetDown
from ..health import last_line
from ..routing import HOME
from ..routing import has_prefix_for
from ..routing import is_under
from ..routing import map_path


logger = logging.getLogger(__name__)

SETUP_TIMEOUT_SECONDS = 10
# The setup script only reads, so it is safe to try twice. Nothing
# passed to execute() is ever retried.
SETUP_ATTEMPTS = 2
MASTER_EXIT_TIMEOUT_SECONDS = 2

# A laptop can fall asleep between two tool calls. A command sent
# down a connection to a sleeping peer hangs on the old connection
# and then on a new one, which takes longer than five seconds and
# leaves it unknown whether the command ran. So a link unused for
# this long is probed first. A failed probe costs about three seconds
# and sends nothing.
IDLE_PROBE_SECONDS = 5.0


class HandsSshEnvironment(SSHEnvironment):
    """Hermes' own SSH environment, fitted for a personal workstation.

    The class name must keep `ssh` in it. See targets/__init__.py.
    """

    def __init__(
        self,
        spec: TargetSpec,
        gate: Gate,
        start_cwd: str,
        host_cwd: str,
        timeout: int,
        persistent: bool,
    ):
        self.hands_spec = spec
        self.hands_start_cwd = start_cwd
        self.hands_host_cwd = host_cwd

        self._hands_gate = gate
        self._hands_path = ""
        self._hands_inhibit = remote.INHIBIT_NONE
        self._hands_ready = False
        self._hands_last_ok = time.monotonic()

        super().__init__(
            host=spec.host,
            user=spec.user,
            cwd=start_cwd,
            timeout=timeout,
            port=spec.port,
            key_path=spec.key,
            # Hermes would otherwise copy credentials, skills and
            # cache into ~/.hermes on the user's own machine.
            sync_files=False,
            persistent=persistent,
        )

        self._hands_ready = True
        self._hands_last_ok = time.monotonic()

    @property
    def _hermes_sandbox_home(self) -> str | None:
        return getattr(self, "_remote_home", None)

    @_hermes_sandbox_home.setter
    def _hermes_sandbox_home(self, value) -> None:
        # Hermes would record the folder the session started in as the
        # backend's home. The real home is detected, so that is ignored.
        return

    def _build_ssh_command(self, extra_args=None, send_env=()) -> list:
        command = super()._build_ssh_command(extra_args, send_env)
        options = remote.build_ssh_options(self.hands_spec)

        return [command[0], *options, *command[1:]]

    def _hands_run_setup(self, script: str) -> str:
        for attempt in range(SETUP_ATTEMPTS):
            try:
                done = self._run_ssh(script, timeout=SETUP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "hands: setup of %s timed out (attempt %d)",
                    self.hands_spec.label,
                    attempt + 1,
                )
                continue

            if done.returncode == 0:
                return done.stdout

        return ""

    def _hands_prepare(self) -> None:
        script = remote.build_setup_script(self.hands_spec)

        if not script:
            return

        found = remote.parse_setup(self._hands_run_setup(script))

        self._hands_path = found.path
        self._hands_inhibit = found.inhibit

        if not found.path:
            logger.warning(
                "hands: no PATH could be read from an interactive shell "
                "on %s. Tools set up in a shell rc file may be missing.",
                self.hands_spec.label,
            )

    def init_session(self):
        # This has to happen before Hermes snapshots the login shell,
        # because the borrowed PATH is carried by that snapshot.
        self._hands_prepare()

        super().init_session()

        if self.hands_start_cwd in ("", HOME):
            return

        if self.cwd == self.hands_start_cwd:
            return

        logger.warning(
            "hands: %s is not a folder on %s. The session starts in %s.",
            self.hands_start_cwd,
            self.hands_spec.label,
            self.cwd,
        )

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ):
        if login:
            cmd_string = remote.prefix_path(cmd_string, self._hands_path)

        if not login and self._hands_ready:
            cmd_string = remote.wrap_with_inhibitor(
                cmd_string,
                self._hands_inhibit,
            )

        return super()._run_bash(
            cmd_string,
            login=login,
            timeout=timeout,
            stdin_data=stdin_data,
        )

    def _hands_is_host_only(self, path: str) -> bool:
        """True for the session's host folder when the target has no
        folder that stands for it. Running there would fail at `cd`
        on every call, so the target's own start folder is used."""

        host = self.hands_host_cwd

        if not host:
            return False

        if not is_under(path, normalize_path(host)):
            return False

        return not has_prefix_for(path, self.hands_spec)

    def _hands_cwd(self, cwd: str) -> str:
        """Turn the folder Hermes passes into one that exists here."""

        # Until a session has run a command, Hermes passes the
        # backend's placeholder folder. The session's own start folder
        # is the right answer then.
        if cwd == HOME:
            return ""

        if not is_absolute(cwd):
            return cwd

        # Desktop and editors send the folder the user picked, which
        # is a path on the host.
        path = normalize_path(cwd)
        mapped = map_path(path, self.hands_spec)

        if mapped is not None:
            return mapped

        if self._hands_is_host_only(path):
            return ""

        return cwd

    def _hands_drop_masters(self) -> None:
        """Close shared connections that point at a dead peer, so the
        next call opens a new one instead of hanging on the old."""

        target = f"{self.user}@{self.host}"

        for socket in self._control_sockets():
            if not socket.exists():
                continue

            command = ["ssh", "-o", f"ControlPath={socket}", "-O", "exit"]

            try:
                subprocess.run(
                    [*command, target],
                    capture_output=True,
                    timeout=MASTER_EXIT_TIMEOUT_SECONDS,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                logger.debug("hands: closing %s failed", socket)

    def _hands_link_lost(self, result: dict) -> str | None:
        """The reason the link was lost, or None when it was not."""

        verdict = exits.transport_verdict(result)

        if verdict == exits.VERDICT_FINE:
            return None

        if verdict == exits.VERDICT_LOST:
            detail = last_line(str(result.get("output") or ""))
            self._hands_gate.trip(detail)

            return detail or "the connection was lost"

        # A command can exit 255 on its own. A live probe settles it.
        probe = self._hands_gate.check()

        if probe.ok:
            return None

        return probe.detail or "the connection was lost"

    def _hands_admit(self) -> None:
        idle = time.monotonic() - self._hands_last_ok

        if idle > IDLE_PROBE_SECONDS:
            self._hands_gate.admit_fresh()
            return

        self._hands_gate.admit()

    def execute(self, command: str, cwd: str = "", **kwargs) -> dict:
        name = self.hands_spec.name

        # A refusal is returned, not raised. The terminal tool retries
        # any exception three times, and a command must never be
        # retried.
        try:
            self._hands_admit()
        except TargetDown as down:
            self._hands_drop_masters()

            return exits.refusal(name, str(down))

        result = super().execute(command, self._hands_cwd(cwd), **kwargs)
        lost = self._hands_link_lost(result)

        if lost is None:
            self._hands_last_ok = time.monotonic()

            return exits.annotate(result, name, exits.classify(result))

        self._hands_drop_masters()

        return exits.annotate(
            result,
            name,
            exits.STATE_NO_STATUS,
            exits.no_status_note(self.hands_spec.label, lost),
        )

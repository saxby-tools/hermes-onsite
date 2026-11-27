"""The environment target: commands run in a sandbox on the server.

Not released. See `environments.py` for what a sandbox is and what it
is given.
"""

from __future__ import annotations

import logging

from .. import environments
from .. import exits
from ..compat import BaseEnvironment
from ..compat import bash_argv
from ..compat import popen_bash
from ..config import TargetSpec
from ..routing import HOME


logger = logging.getLogger(__name__)

# What Docker says when a command is sent to a sandbox that has
# stopped or is gone. Docker refuses before it starts anything.
DAEMON_ERROR = "Error response from daemon"
GONE_MARKS = ("is not running", "No such container")


class OnsiteDockerEnvironment(BaseEnvironment):
    """One session's sandbox, on Hermes' own way of running a command.

    Hermes' base class runs each command as a fresh `bash`, keeps the
    folder and the exported variables between them, and stops one
    that runs too long. This class only says where that `bash` runs:
    in the session's container.

    Hermes' own Docker class is not used. It shares the skills, the
    caches and some login files of the Hermes home with its
    container, and a sandbox here is given the session's folder and
    nothing else.

    The class name must keep `docker` in it. See targets/__init__.py.
    """

    # A command left running in the background stays in the sandbox
    # after Hermes lets go of this object, and is still reachable.
    cleanup_stops_processes = False

    def __init__(
        self,
        spec: TargetSpec,
        session: environments.Session,
        profile: str,
        timeout: int,
        persistent: bool,
    ):
        self.onsite_spec = spec
        self.onsite_session = session

        self._onsite_profile = profile
        self._onsite_docker = environments.need_docker()

        sandbox = environments.ensure_sandbox(
            spec,
            session,
            profile,
            docker=self._onsite_docker,
        )

        self._onsite_container = sandbox.container
        self._onsite_home = sandbox.home

        super().__init__(
            cwd=str(session.start),
            timeout=timeout,
            persist_env=persistent,
        )

        self.init_session()

    @property
    def _hermes_sandbox_home(self) -> str:
        return self._onsite_home

    @_hermes_sandbox_home.setter
    def _hermes_sandbox_home(self, value) -> None:
        # Hermes would record the folder the session started in as the
        # backend's home. The real home was asked for, so that is
        # ignored.
        return

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ):
        command = environments.build_exec_command(
            self._onsite_docker,
            self._onsite_container,
            with_stdin=stdin_data is not None,
        )

        return popen_bash([*command, *bash_argv(cmd_string, login)], stdin_data)

    def _onsite_never_ran(self, result: dict) -> bool:
        """Whether Docker refused a command because the sandbox was
        not running. True only when Docker's own words say so and the
        sandbox is in fact not running, so a command that merely
        printed those words is not run twice."""

        if exits.exit_code(result) == 0:
            return False

        output = str(result.get("output") or "")

        if DAEMON_ERROR not in output:
            return False

        if not any(mark in output for mark in GONE_MARKS):
            return False

        state = environments.container_state(
            self._onsite_docker,
            self._onsite_container,
            environments.run_program,
        )

        return state != environments.STATE_RUNNING

    def _onsite_revive(self) -> None:
        """Start the sandbox again, or make a new one, after a restart
        of the server or of Docker took it down."""

        sandbox = environments.ensure_sandbox(
            self.onsite_spec,
            self.onsite_session,
            self._onsite_profile,
            docker=self._onsite_docker,
        )

        self._onsite_home = sandbox.home

        # A new container has none of the files Hermes keeps a
        # session's folder and variables in.
        if sandbox.made:
            self.init_session()

    def execute(self, command: str, cwd: str = "", **kwargs) -> dict:
        name = self.onsite_spec.name

        # Until a session has run a command, Hermes passes the
        # backend's placeholder folder. The session's own start folder
        # is the right answer then.
        if cwd == HOME:
            cwd = ""

        result = super().execute(command, cwd, **kwargs)

        if not self._onsite_never_ran(result):
            return exits.annotate(result, name, exits.classify(result))

        try:
            self._onsite_revive()
        except environments.SandboxError as error:
            return exits.refusal(name, str(error))

        # Docker refused before anything started. This is the
        # command's first run, not a second one.
        result = super().execute(command, cwd, **kwargs)

        return exits.annotate(result, name, exits.classify(result))

    def cleanup(self):
        # The sandbox is left running. It is the session's, not this
        # object's: Hermes drops the object when a session goes quiet
        # and builds another on its next command. A sandbox ends when
        # its session is removed. See `environments.remove_session`.
        self.stop_all_processes()

"""The local target: commands run on the machine Hermes runs on."""

from __future__ import annotations

import os

from .. import exits
from ..compat import LocalEnvironment
from ..config import TargetSpec
from ..routing import HOME


class HandsLocalEnvironment(LocalEnvironment):
    """Hermes' own local environment, started in the session folder.

    The class name must keep `local` in it. See targets/__init__.py.
    """

    def __init__(
        self,
        spec: TargetSpec,
        start_cwd: str,
        timeout: int,
        persistent: bool,
    ):
        self.hands_spec = spec

        super().__init__(
            cwd=os.path.expanduser(start_cwd),
            timeout=timeout,
            persistent=persistent,
        )

    @property
    def _hermes_sandbox_home(self) -> str:
        return os.path.expanduser(HOME)

    @_hermes_sandbox_home.setter
    def _hermes_sandbox_home(self, value) -> None:
        # Hermes would record the folder the session started in as the
        # backend's home. The real home is known, so that is ignored.
        return

    def execute(self, command: str, cwd: str = "", **kwargs) -> dict:
        # Until a session has run a command, Hermes passes the
        # backend's placeholder folder. That would start the session
        # in the home folder instead of the folder it was opened in.
        if cwd == HOME:
            cwd = ""

        result = super().execute(command, cwd, **kwargs)

        return exits.annotate(
            result,
            self.hands_spec.name,
            exits.classify(result),
        )

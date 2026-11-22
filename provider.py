"""The `hands` terminal backend: one provider, many named targets."""

from __future__ import annotations

import logging
import shutil

from typing import Any
from typing import Optional

from .compat import PROMPT_PROBE_TASK_ID
from .compat import TerminalEnvironmentProvider
from .config import BACKEND_NAME
from .config import ConfigError
from .config import KIND_SSH
from .routing import HOME
from .runtime import Runtime


logger = logging.getLogger(__name__)

DISPLAY_NAME = "Hands"
DESCRIPTION = (
    "Run each session on a named machine picked from its folder: "
    "this one, or the one you sit at over SSH."
)
ENV_DESCRIPTION = (
    "the machine hermes-hands picked for this session, named in the "
    "Hands section of this prompt"
)
SSH_MISSING = (
    "hermes-hands: an ssh target is configured but no `ssh` program "
    "is on PATH."
)
PROBE_DECLINED_EXIT = 1


class DeclinedProbe:
    """Stands in for the environment Hermes builds to describe the
    backend in the system prompt.

    Hermes caches that description across sessions. With per-session
    targets, one session's machine would be described to the next. So
    the probe is declined, Hermes falls back to `env_description`,
    and the Hands prompt section names the real target per session.
    """

    cwd = HOME

    def execute(self, command: str, cwd: str = "", **kwargs) -> dict:
        return {"output": "", "returncode": PROBE_DECLINED_EXIT}

    def cleanup(self, *args, **kwargs) -> None:
        return


class HandsProvider(TerminalEnvironmentProvider):
    # Some targets are remote, so the host's OS, home and folder must
    # not be presented to the model as facts about where tools run.
    is_remote = True

    # A container backend has its host-looking folders rewritten and
    # its dangerous-command prompts skipped. Neither may happen here.
    is_container = False

    def __init__(self, runtime: Runtime):
        self._runtime = runtime

    @property
    def name(self) -> str:
        return BACKEND_NAME

    @property
    def display_name(self) -> str:
        return DISPLAY_NAME

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def env_description(self) -> str:
        return ENV_DESCRIPTION

    @property
    def skip_container_guards(self) -> bool:
        # Stated outright. These are real machines, and Hermes'
        # approval prompts are the only gate in front of them.
        return False

    @property
    def default_cwd(self) -> Optional[str]:
        # Each target resolves its own home. A fixed path could not be
        # right for both a Mac and a Linux box.
        return HOME

    def is_available(self) -> bool:
        # Runs during UI paints, so it reads settings and nothing else.
        return self._runtime.config_problem() is None

    def check_requirements(self, config: dict[str, Any]) -> bool:
        try:
            parsed = self._runtime.load_config()
        except ConfigError as error:
            logger.error("%s", error)
            return False

        kinds = {spec.kind for spec in parsed.targets.values()}

        if KIND_SSH not in kinds:
            return True

        if shutil.which("ssh") is None:
            logger.error(SSH_MISSING)
            return False

        return True

    def probe(self) -> tuple[str, str]:
        problem = self._runtime.config_problem()

        if problem is None:
            return ("ready", "")

        return ("needs_setup", problem)

    def setup_instructions(self) -> list[str]:
        return [
            "Run `hermes hands init` to name your targets.",
            "Then `hermes hands status` to check them.",
        ]

    def doctor_checks(self) -> list[tuple[bool, str, str]]:
        problem = self._runtime.config_problem()

        if problem is not None:
            return [(False, "Hands backend", "(settings need fixing)")]

        config = self._runtime.load_config()
        names = ", ".join(config.targets)

        return [(True, "Hands backend", f"(targets: {names})")]

    def create_environment(
        self,
        *,
        cwd: str,
        timeout: int,
        task_id: str = "default",
        image: Optional[str] = None,
        container_config: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ):
        if task_id == PROMPT_PROBE_TASK_ID:
            return DeclinedProbe()

        return self._runtime.create_environment(cwd, timeout, task_id)


def build_provider(runtime: Runtime) -> HandsProvider:
    return HandsProvider(runtime)

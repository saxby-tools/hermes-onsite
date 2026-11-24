"""What the plugin's terminal side imports from Hermes, in one place.

Some of these are public plugin API and some are not. Hermes has no
public way to reuse its local and SSH environments, and rewriting them
would mean rewriting their process, secret and sudo handling. So the
private imports are accepted, kept in this file, and fenced by
`requires_hermes` in plugin.yaml. `hermes plugins validate` warns
about them. That warning is expected.

If Hermes moves one of these, the fix is here. The screen driver's
own private imports are the one exception. They are in
`screenbackend.py`, which is only loaded when a session uses its
screen.
"""

from __future__ import annotations

import logging

from pathlib import Path
from typing import Any

# Public plugin API.
from agent.terminal_env_provider import TerminalEnvironmentProvider
from gateway.session_context import get_session_env
from hermes_cli.config import load_config_readonly
from hermes_constants import get_hermes_home

# Private. See the module docstring.
from tools.environments.base import EnvironmentConnectionError
from tools.environments.local import LocalEnvironment
from tools.environments.ssh import SSHEnvironment

from .config import BACKEND_NAME
from .config import PLUGIN_ID
from .config import SCREEN_PASSWORD_ENV


logger = logging.getLogger(__name__)

DATA_DIR_NAME = "plugin-data"
PLUGINS_DIR_NAME = "plugins"
SESSION_ID_VAR = "HERMES_SESSION_ID"
SESSION_KEY_VAR = "HERMES_SESSION_KEY"

# Hermes' own switch for a plugin that puts a message into a chat.
INJECTION_KEY = "allow_gateway_injection"

# The plugin context of the current load. Session facts are read
# through it, which is the documented way. It is None when only the
# dashboard loaded the package, where there is no session to ask
# about.
_context = None

# Hermes builds a throwaway environment under this id to describe the
# backend in the system prompt.
PROMPT_PROBE_TASK_ID = "prompt-backend-probe"

__all__ = [
    "EnvironmentConnectionError",
    "LocalEnvironment",
    "PROMPT_PROBE_TASK_ID",
    "SSHEnvironment",
    "TerminalEnvironmentProvider",
    "anchor_file_tools",
    "bind",
    "current_session_cwd",
    "current_session_id",
    "current_session_key",
    "current_surface",
    "data_dir",
    "drop_current_environment",
    "environment_key",
    "load_settings",
    "plugins_dir",
    "run_allowed",
    "save_settings",
    "terminal_persistence",
]


def bind(ctx) -> None:
    global _context

    _context = ctx


def data_dir() -> Path:
    return Path(get_hermes_home()) / DATA_DIR_NAME / PLUGIN_ID


def plugins_dir() -> Path:
    """The folder this profile loads its plugins from."""

    return Path(get_hermes_home()) / PLUGINS_DIR_NAME


def load_settings() -> Any:
    """The raw settings block, or None when there is none."""

    config = load_config_readonly()

    try:
        return config["plugins"]["entries"][PLUGIN_ID]["settings"]
    except (KeyError, TypeError):
        return None


def run_allowed() -> bool:
    """Whether this profile lets the plugin hand the agent a message.

    `/run` needs it in the TUI and the desktop app. Hermes reads the
    same key, from the same file, when the message is sent.
    """

    config = load_config_readonly()

    try:
        entry = config["plugins"]["entries"][PLUGIN_ID]
    except (KeyError, TypeError):
        return False

    if not isinstance(entry, dict):
        return False

    return entry.get(INJECTION_KEY) is True


class SettingsLocked(RuntimeError):
    """This install's settings are managed by something else."""


def screen_password() -> str:
    """The VNC password this profile set, or nothing.

    Read through Hermes' own secret lookup and not from the process
    environment. One gateway can serve several profiles, and a plain
    read there could return another profile's password.
    """

    from agent.secret_scope import get_secret

    return get_secret(SCREEN_PASSWORD_ENV) or ""


def screen_backend_name() -> str:
    """The screen backend this profile selects, or nothing."""

    settings = (load_config_readonly() or {}).get("computer_use") or {}
    name = settings.get("backend")

    if not isinstance(name, str):
        return ""

    return name.strip()


def save_settings(settings: dict) -> None:
    """Write the plugin's whole settings block.

    Hermes' own writer for plugin settings merges what it is given
    into what is there, so a target taken out of the block would
    stay in the file. The block is replaced whole here, through the
    same public reader and writer Hermes' bundled plugins use.
    """

    from hermes_cli.config import is_managed
    from hermes_cli.config import read_user_config_raw
    from hermes_cli.config import save_config

    if is_managed():
        raise SettingsLocked(
            "this Hermes install is managed by a package manager, so "
            "its settings are changed there"
        )

    raw = read_user_config_raw()
    plugins = raw.setdefault("plugins", {})
    entries = plugins.setdefault("entries", {})
    entry = entries.setdefault(PLUGIN_ID, {})

    entry["settings"] = settings

    save_config(raw)


def current_session_cwd() -> str:
    if _context is None:
        return ""

    try:
        return _context.session_cwd() or ""
    except Exception:
        # Outside a session Hermes has no folder to report. Routing
        # then falls back to the default target.
        logger.debug("no session folder is readable", exc_info=True)
        return ""


def current_session_id() -> str:
    try:
        return get_session_env(SESSION_ID_VAR, "") or ""
    except Exception:
        logger.debug("no session id is readable", exc_info=True)
        return ""


def current_session_key() -> str:
    try:
        return get_session_env(SESSION_KEY_VAR, "") or ""
    except Exception:
        logger.debug("no session key is readable", exc_info=True)
        return ""


def current_surface() -> str:
    """`cli`, `tui`, `desktop`, `acp` or a chat platform. Empty when
    there is no session."""

    if _context is None:
        return ""

    try:
        return _context.session_surface() or ""
    except Exception:
        logger.debug("no session surface is readable", exc_info=True)
        return ""


def environment_key() -> str:
    """The key Hermes caches the calling session's environment under."""

    from tools.terminal_tool import _resolve_container_task_id

    return _resolve_container_task_id(None)


def forget_session_folder(*ids: str) -> None:
    """Drop the folder Hermes recorded for a session.

    The record is a path on the machine the session was using. After
    a switch it would be handed to the new machine, where it most
    likely does not exist.
    """

    from tools.approval import get_current_session_key
    from tools.terminal_tool import clear_session_cwd

    known = {
        get_current_session_key(default=""),
        current_session_key(),
        current_session_id(),
        *ids,
    }

    for name in known:
        if name:
            clear_session_cwd(name)


def drop_current_environment(*ids: str) -> bool:
    """Discard the calling session's environment so the next tool call
    builds a new one. True when one was live."""

    from tools.terminal_tool_lifecycle import cleanup_vm
    from tools.terminal_tool_lifecycle import get_active_env

    forget_session_folder(*ids)

    key = environment_key()

    if get_active_env(key) is None:
        return False

    cleanup_vm(key)

    return True


def terminal_persistence() -> tuple[bool, bool]:
    """Whether exports carry over between commands: (local, ssh)."""

    try:
        from tools.terminal_tool import _get_env_config

        config = _get_env_config()
    except Exception:
        logger.debug("terminal config is not readable", exc_info=True)
        return True, True

    return (
        bool(config.get("local_persistent", True)),
        bool(config.get("ssh_persistent", True)),
    )


def live_environment(task_id: str):
    """The task's environment, built first if it is not live yet."""

    from tools.terminal_tool_lifecycle import ensure_task_env
    from tools.terminal_tool_lifecycle import get_active_env

    return get_active_env(task_id) or ensure_task_env(task_id)


def anchor_file_tools(task_id: str) -> None:
    """Make the file tools resolve paths where the terminal would.

    Two things in Hermes need help here.

    Hermes picks its path rules from the live environment's class.
    With no environment yet, a session whose first call is a file
    tool gets host rules, and a macOS host rewrites `/home/...`
    before the path is ever sent. So the environment is built first.

    Hermes also records the terminal's folder under the session key,
    while the file tools look it up under the task id. In a gateway
    session those differ, so a relative path would resolve against
    the home folder instead of the folder the session is in. The
    folder is copied across before each file tool runs.
    """

    from tools.approval import get_current_session_key
    from tools.terminal_tool import get_session_cwd
    from tools.terminal_tool import record_session_cwd

    environment = live_environment(task_id)

    if getattr(environment, "_hermes_backend_name", "") != BACKEND_NAME:
        return

    session_key = get_current_session_key(default="") or task_id
    folder = get_session_cwd(session_key) or getattr(environment, "cwd", "")

    if not isinstance(folder, str):
        return

    if not folder.startswith("/"):
        return

    if get_session_cwd(task_id) == folder:
        return

    record_session_cwd(task_id, folder)

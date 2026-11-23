"""MCP servers that run on a target, started over SSH.

Hermes starts a local MCP server by running a command and talking to
it over its input and output. That command can be `ssh`, so the server
itself runs on the target and its tools act there. No tool call is
rerouted and no approval is skipped: to Hermes this is an ordinary
MCP server, behind its ordinary gates.

Hermes loads MCP servers for a whole profile, not for one session. So
a server on a target is named for it, `<target>_<name>`, and a hook
refuses its tools in a session that runs on another machine. A
session never acts on a second machine by itself.

This module builds the command and reads tool names. It runs nothing.
"""

from __future__ import annotations

import re
import shlex

from .config import OnsiteConfig
from .config import KIND_SSH
from .config import PATH_MODE_INTERACTIVE
from .config import TargetSpec
from .remote import QUIET_PATH_LINES
from .remote import build_stdio_arguments


HERMES_BINARY = "hermes"

TOOL_PREFIX = "mcp__"
TOOL_SEPARATOR = "__"
SERVER_SEPARATOR = "_"

SERVER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# Hermes passes a short list of variables to a server it starts. The
# key agent's socket is not on it, so it is asked for by name.
AGENT_ENV = "SSH_AUTH_SOCK=${SSH_AUTH_SOCK}"


class McpError(ValueError):
    """The server could not be described. The message says why."""


def server_prefix(target: str) -> str:
    """How every server on a target starts its name."""

    return target.replace("-", SERVER_SEPARATOR) + SERVER_SEPARATOR


def server_name(spec: TargetSpec, name: str) -> str:
    if not SERVER_NAME_RE.match(name):
        raise McpError(
            f"`{name}` is not a server name. Use lower-case letters, "
            "digits and underscores, starting with a letter."
        )

    return server_prefix(spec.name) + name


def build_remote_script(spec: TargetSpec, words: list[str]) -> str:
    """What runs on the target: set PATH, then become the server."""

    lines = ""

    if spec.path_mode == PATH_MODE_INTERACTIVE:
        lines = QUIET_PATH_LINES

    return lines + "exec " + shlex.join(words)


def build_ssh_arguments(spec: TargetSpec, words: list[str]) -> list[str]:
    """Everything after `ssh` on the command line Hermes will run."""

    if spec.kind != KIND_SSH:
        raise McpError(
            f"`{spec.name}` is this machine. Add its MCP servers with "
            "`hermes mcp add` as usual."
        )

    if not words:
        raise McpError("name the server's command after `--`")

    return build_stdio_arguments(spec, build_remote_script(spec, words))


def add_command(spec: TargetSpec, name: str, words: list[str]) -> list[str]:
    """The `hermes mcp add` call that registers the server."""

    return [
        HERMES_BINARY, "mcp", "add", server_name(spec, name),
        "--env", AGENT_ENV,
        "--command", "ssh",
        # Hermes wants `--args` last. Everything after it is ssh's.
        "--args", *build_ssh_arguments(spec, words),
    ]


def server_of(tool_name: str) -> str:
    """The server part of an MCP tool's name, or "" for other tools."""

    if not tool_name.startswith(TOOL_PREFIX):
        return ""

    rest = tool_name.removeprefix(TOOL_PREFIX)
    server, separator, _ = rest.partition(TOOL_SEPARATOR)

    if not separator:
        return ""

    return server


def owner_of(tool_name: str, config: OnsiteConfig) -> str | None:
    """The target an MCP tool runs on, if its server is named for one."""

    server = server_of(tool_name)

    if not server:
        return None

    best = None

    for spec in config.targets.values():
        if spec.kind != KIND_SSH:
            continue

        prefix = server_prefix(spec.name)

        if not server.startswith(prefix):
            continue

        # `lab_gpu_` beats `lab_` for a server named `lab_gpu_files`.
        if best is not None and len(prefix) <= len(server_prefix(best)):
            continue

        best = spec.name

    return best

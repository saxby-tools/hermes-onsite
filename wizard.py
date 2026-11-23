"""Build a settings block for `hermes hands init`.

Settings come from flags or, on a terminal, from a short interview.
Either way the result goes through the same validation as a block
read from config.yaml, so `init` cannot write one that will not load.

This module imports nothing from Hermes.
"""

from __future__ import annotations

from typing import Any
from typing import Callable

from .config import DEFAULT_SSH_PORT
from .config import HandsConfig
from .config import KIND_LOCAL
from .config import KIND_SSH
from .config import parse_config


DEFAULT_SSH_NAME = "laptop"
DEFAULT_LOCAL_NAME = "mac"

NO_TARGETS = (
    "No targets were given. Pass --ssh and --local, or run "
    "`hermes hands init` on a terminal to be asked."
)

INDENT = "  "


class FlagError(ValueError):
    pass


def split_pair(flag: str, value: str) -> tuple[str, str]:
    name, sep, rest = value.partition("=")

    if not sep or not name or not rest:
        raise FlagError(f"{flag} expects NAME=VALUE, got `{value}`")

    return name, rest


def parse_ssh(value: str) -> tuple[str, dict[str, Any]]:
    """`laptop=dev@host` or `laptop=dev@host:2222`."""

    name, address = split_pair("--ssh", value)
    user, sep, host = address.partition("@")

    if not sep or not user or not host:
        raise FlagError(f"--ssh expects NAME=USER@HOST, got `{value}`")

    block: dict[str, Any] = {"kind": KIND_SSH, "user": user, "host": host}
    host_part, sep, port = host.rpartition(":")

    if not sep:
        return name, block

    if not port.isdigit():
        raise FlagError(f"--ssh port `{port}` is not a number")

    block["host"] = host_part

    if int(port) != DEFAULT_SSH_PORT:
        block["port"] = int(port)

    return name, block


def need_target(targets: dict, flag: str, name: str) -> dict[str, Any]:
    if name not in targets:
        raise FlagError(f"{flag} names `{name}`, which is not a target")

    return targets[name]


def settings_from_flags(
    ssh: list[str],
    local: list[str],
    prefix: list[str],
    path_map: list[str],
    key: list[str],
    known_hosts: list[str],
    default: str | None,
    mirror: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    if not ssh and not local:
        raise FlagError(NO_TARGETS)

    targets: dict[str, dict[str, Any]] = {}

    for value in ssh:
        name, block = parse_ssh(value)
        targets[name] = block

    for name in local:
        targets[name] = {"kind": KIND_LOCAL}

    for value in prefix:
        name, path = split_pair("--prefix", value)
        target = need_target(targets, "--prefix", name)
        target.setdefault("prefixes", []).append(path)

    for value in path_map:
        name, pair = split_pair("--map", value)
        source, target_path = split_pair("--map", pair)
        target = need_target(targets, "--map", name)
        target.setdefault("path_map", {})[source] = target_path

    for value in key:
        name, path = split_pair("--key", value)
        need_target(targets, "--key", name)["key"] = path

    for value in known_hosts:
        name, path = split_pair("--known-hosts", value)
        need_target(targets, "--known-hosts", name)["known_hosts"] = path

    for value in mirror:
        name, path = split_pair("--mirror", value)
        need_target(targets, "--mirror", name)["mirror"] = path

    chosen = default

    if chosen is None and len(local) == 1:
        chosen = local[0]

    return {"default": chosen, "targets": targets}


def ask(
    read: Callable[[str], str],
    question: str,
    default: str = "",
) -> str:
    hint = f" [{default}]" if default else ""
    answer = read(f"{question}{hint}: ").strip()

    return answer or default


def interview(read: Callable[[str], str]) -> dict[str, Any]:
    """Ask for the common setup: one machine over SSH and this one."""

    ssh_name = ask(read, "Name for the machine you sit at", DEFAULT_SSH_NAME)
    host = ask(read, "Its host name or address")
    user = ask(read, "The user to log in as")
    prefix = ask(
        read,
        "Sessions in folders under this path run there",
        f"/home/{user}/",
    )
    source = ask(
        read,
        "A folder on THIS machine that is the same folder there "
        "(blank for none)",
    )

    ssh_block: dict[str, Any] = {
        "kind": KIND_SSH,
        "host": host,
        "user": user,
        "prefixes": [prefix],
    }

    if source:
        destination = ask(read, "Its path on that machine", prefix)
        ssh_block["path_map"] = {source: destination}

    local_name = ask(
        read,
        "Name for this machine, used for everything else",
        DEFAULT_LOCAL_NAME,
    )

    return {
        "default": local_name,
        "targets": {
            ssh_name: ssh_block,
            local_name: {"kind": KIND_LOCAL},
        },
    }


def validate(settings: dict[str, Any]) -> HandsConfig:
    return parse_config(settings)


def render_value(value: Any, depth: int) -> list[str]:
    pad = INDENT * depth

    if isinstance(value, dict):
        lines = []

        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines += render_value(item, depth + 1)
                continue

            lines.append(f"{pad}{key}: {item}")

        return lines

    if isinstance(value, list):
        return [f"{pad}- {item}" for item in value]

    return [f"{pad}{value}"]


def render_yaml(settings: dict[str, Any]) -> str:
    """Show a settings block the way it will sit in config.yaml."""

    wrapped = {
        "plugins": {
            "entries": {
                "hermes-hands": {"settings": settings},
            },
        },
    }

    return "\n".join(render_value(wrapped, 0))

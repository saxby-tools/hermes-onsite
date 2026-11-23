"""Parse and validate the onsite settings.

Targets name real machines, so there are no defaults to fall back on.
A missing or broken block is reported in full, with the fix, instead
of being patched over. This module imports nothing from Hermes, so it
can be tested on its own.
"""

from __future__ import annotations

import posixpath
import re

from dataclasses import dataclass
from typing import Any
from typing import Mapping


PLUGIN_ID = "onsite"
BACKEND_NAME = "onsite"

KIND_LOCAL = "local"
KIND_SSH = "ssh"
VALID_KINDS = (KIND_LOCAL, KIND_SSH)

PATH_MODE_INTERACTIVE = "interactive"
PATH_MODE_NONE = "none"
VALID_PATH_MODES = (PATH_MODE_INTERACTIVE, PATH_MODE_NONE)

DEFAULT_SSH_PORT = 22
DEFAULT_CONNECT_TIMEOUT = 3
MIN_CONNECT_TIMEOUT = 1
MAX_CONNECT_TIMEOUT = 30

DEFAULT_MIRROR_DEPTH = 3
MIN_MIRROR_DEPTH = 1
MAX_MIRROR_DEPTH = 6

# `/onsite auto` clears an override, so no target may take the name.
RESERVED_TARGET_NAMES = ("auto",)

# A machine that dials out is reached at a port on the host's own
# loopback address. See `dialout`.
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
HOST_KEY_ALIAS_PREFIX = "onsite-"
TARGET_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

SETTINGS_PATH = f"plugins.entries.{PLUGIN_ID}.settings"
FIX_HINT = (
    f"Run `hermes onsite init`, or edit `{SETTINGS_PATH}` in config.yaml."
)

KNOWN_TARGET_KEYS = frozenset({
    "kind",
    "host",
    "user",
    "port",
    "key",
    "known_hosts",
    "prefixes",
    "path_map",
    "path",
    "inhibit_sleep",
    "connect_timeout",
    "mirror",
    "mirror_depth",
})


class ConfigError(ValueError):
    """Every problem found in the settings, not only the first."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)

        lines = [f"{PLUGIN_ID}: the settings are not usable."]
        lines += [f"  - {problem}" for problem in self.problems]
        lines.append(FIX_HINT)

        super().__init__("\n".join(lines))


@dataclass(frozen=True)
class TargetSpec:
    """One named machine that can run a session's tool calls."""

    name: str
    kind: str
    host: str = ""
    user: str = ""
    port: int = DEFAULT_SSH_PORT
    key: str = ""
    known_hosts: str = ""
    prefixes: tuple[str, ...] = ()
    path_map: tuple[tuple[str, str], ...] = ()
    path_mode: str = PATH_MODE_INTERACTIVE
    inhibit_sleep: bool = True
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT
    mirror: str = ""
    mirror_depth: int = DEFAULT_MIRROR_DEPTH

    @property
    def host_key_alias(self) -> str:
        """The name this target's host key is filed under, if not its host.

        Every machine that dials out shares the address 127.0.0.1, so
        OpenSSH would file each key under an address and a port. A port
        handed to a different machine would then look like the same
        host with a changed key. Filing by target name keeps each key
        with its machine.
        """

        if self.kind != KIND_SSH:
            return ""

        if self.host not in LOOPBACK_HOSTS:
            return ""

        return f"{HOST_KEY_ALIAS_PREFIX}{self.name}"

    @property
    def label(self) -> str:
        if self.kind == KIND_LOCAL:
            return f"{self.name} (this machine)"

        return f"{self.name} ({self.user}@{self.host})"


@dataclass(frozen=True)
class OnsiteConfig:
    default: str
    targets: Mapping[str, TargetSpec]

    def target(self, name: str) -> TargetSpec:
        return self.targets[name]


def normalize_path(path: str) -> str:
    return posixpath.normpath(path)


def is_absolute(path: Any) -> bool:
    if not isinstance(path, str):
        return False

    return path.startswith("/")


def check_name(name: Any, problems: list[str]) -> bool:
    if not isinstance(name, str):
        problems.append(f"target name {name!r} is not text")
        return False

    if name in RESERVED_TARGET_NAMES:
        problems.append(f"target name `{name}` is reserved")
        return False

    if TARGET_NAME_RE.match(name) is None:
        problems.append(
            f"target name `{name}` must be lowercase letters, digits "
            "and hyphens, starting with a letter"
        )
        return False

    return True


def read_prefixes(name: str, raw: Any, problems: list[str]) -> tuple:
    if raw is None:
        return ()

    if not isinstance(raw, list):
        problems.append(f"`{name}.prefixes` must be a list of paths")
        return ()

    prefixes = []

    for entry in raw:
        if not is_absolute(entry):
            problems.append(
                f"`{name}.prefixes` entry {entry!r} is not an absolute path"
            )
            continue

        prefixes.append(normalize_path(entry))

    return tuple(prefixes)


def read_path_map(name: str, raw: Any, problems: list[str]) -> tuple:
    if raw is None:
        return ()

    if not isinstance(raw, Mapping):
        problems.append(
            f"`{name}.path_map` must map a path on the host to a path "
            "on the target"
        )
        return ()

    pairs = []

    for source, destination in raw.items():
        if not is_absolute(source):
            problems.append(
                f"`{name}.path_map` key {source!r} is not an absolute path"
            )
            continue

        if not is_absolute(destination):
            problems.append(
                f"`{name}.path_map` value {destination!r} is not an "
                "absolute path"
            )
            continue

        pairs.append((normalize_path(source), normalize_path(destination)))

    return tuple(pairs)


def read_port(name: str, raw: Any, problems: list[str]) -> int:
    if raw is None:
        return DEFAULT_SSH_PORT

    if isinstance(raw, bool):
        problems.append(f"`{name}.port` must be a number")
        return DEFAULT_SSH_PORT

    if not isinstance(raw, int):
        problems.append(f"`{name}.port` must be a number")
        return DEFAULT_SSH_PORT

    if raw < 1:
        problems.append(f"`{name}.port` must be between 1 and 65535")
        return DEFAULT_SSH_PORT

    if raw > 65535:
        problems.append(f"`{name}.port` must be between 1 and 65535")
        return DEFAULT_SSH_PORT

    return raw


def read_connect_timeout(name: str, raw: Any, problems: list[str]) -> int:
    if raw is None:
        return DEFAULT_CONNECT_TIMEOUT

    if isinstance(raw, bool):
        problems.append(f"`{name}.connect_timeout` must be whole seconds")
        return DEFAULT_CONNECT_TIMEOUT

    if not isinstance(raw, int):
        problems.append(f"`{name}.connect_timeout` must be whole seconds")
        return DEFAULT_CONNECT_TIMEOUT

    if raw < MIN_CONNECT_TIMEOUT:
        problems.append(
            f"`{name}.connect_timeout` must be at least "
            f"{MIN_CONNECT_TIMEOUT}"
        )
        return DEFAULT_CONNECT_TIMEOUT

    if raw > MAX_CONNECT_TIMEOUT:
        problems.append(
            f"`{name}.connect_timeout` must be at most "
            f"{MAX_CONNECT_TIMEOUT}"
        )
        return DEFAULT_CONNECT_TIMEOUT

    return raw


def read_mirror(
    name: str,
    kind: str,
    raw: Any,
    problems: list[str],
) -> str:
    """The folder on the host that stands in for this target's disk."""

    if raw is None:
        return ""

    if kind != KIND_SSH:
        problems.append(f"`{name}.mirror` is only for an ssh target")
        return ""

    if not is_absolute(raw):
        problems.append(f"`{name}.mirror` must be an absolute path")
        return ""

    mirror = normalize_path(raw)

    # Every folder on the host would then count as this target's.
    if mirror == "/":
        problems.append(f"`{name}.mirror` cannot be the root folder")
        return ""

    return mirror


def read_mirror_depth(name: str, raw: Any, problems: list[str]) -> int:
    if raw is None:
        return DEFAULT_MIRROR_DEPTH

    span = f"between {MIN_MIRROR_DEPTH} and {MAX_MIRROR_DEPTH}"

    if isinstance(raw, bool):
        problems.append(f"`{name}.mirror_depth` must be a number")
        return DEFAULT_MIRROR_DEPTH

    if not isinstance(raw, int):
        problems.append(f"`{name}.mirror_depth` must be a number")
        return DEFAULT_MIRROR_DEPTH

    if raw < MIN_MIRROR_DEPTH:
        problems.append(f"`{name}.mirror_depth` must be {span}")
        return DEFAULT_MIRROR_DEPTH

    if raw > MAX_MIRROR_DEPTH:
        problems.append(f"`{name}.mirror_depth` must be {span}")
        return DEFAULT_MIRROR_DEPTH

    return raw


def read_text(name: str, key: str, raw: Any, problems: list[str]) -> str:
    if raw is None:
        return ""

    if not isinstance(raw, str):
        problems.append(f"`{name}.{key}` must be text")
        return ""

    return raw.strip()


def read_path_mode(name: str, raw: Any, problems: list[str]) -> str:
    if raw is None:
        return PATH_MODE_INTERACTIVE

    if raw not in VALID_PATH_MODES:
        choices = ", ".join(VALID_PATH_MODES)
        problems.append(f"`{name}.path` must be one of: {choices}")
        return PATH_MODE_INTERACTIVE

    return raw


def read_flag(
    name: str,
    key: str,
    raw: Any,
    default: bool,
    problems: list[str],
) -> bool:
    if raw is None:
        return default

    if not isinstance(raw, bool):
        problems.append(f"`{name}.{key}` must be true or false")
        return default

    return raw


def read_target(name: str, raw: Any, problems: list[str]) -> TargetSpec | None:
    if not isinstance(raw, Mapping):
        problems.append(f"target `{name}` must be a block of settings")
        return None

    for key in raw:
        if key not in KNOWN_TARGET_KEYS:
            problems.append(f"`{name}.{key}` is not a known setting")

    kind = raw.get("kind")

    if kind not in VALID_KINDS:
        choices = ", ".join(VALID_KINDS)
        problems.append(f"`{name}.kind` must be one of: {choices}")
        return None

    host = read_text(name, "host", raw.get("host"), problems)
    user = read_text(name, "user", raw.get("user"), problems)

    if kind == KIND_SSH and not host:
        problems.append(f"`{name}.host` is required for an ssh target")

    if kind == KIND_SSH and not user:
        problems.append(f"`{name}.user` is required for an ssh target")

    return TargetSpec(
        name=name,
        kind=kind,
        host=host,
        user=user,
        port=read_port(name, raw.get("port"), problems),
        key=read_text(name, "key", raw.get("key"), problems),
        known_hosts=read_text(
            name,
            "known_hosts",
            raw.get("known_hosts"),
            problems,
        ),
        prefixes=read_prefixes(name, raw.get("prefixes"), problems),
        path_map=read_path_map(name, raw.get("path_map"), problems),
        path_mode=read_path_mode(name, raw.get("path"), problems),
        inhibit_sleep=read_flag(
            name,
            "inhibit_sleep",
            raw.get("inhibit_sleep"),
            True,
            problems,
        ),
        connect_timeout=read_connect_timeout(
            name,
            raw.get("connect_timeout"),
            problems,
        ),
        mirror=read_mirror(name, kind, raw.get("mirror"), problems),
        mirror_depth=read_mirror_depth(
            name,
            raw.get("mirror_depth"),
            problems,
        ),
    )


def read_targets(raw: Any, problems: list[str]) -> dict[str, TargetSpec]:
    if not isinstance(raw, Mapping):
        problems.append("`targets` is missing. At least one is required.")
        return {}

    if not raw:
        problems.append("`targets` is empty. At least one is required.")
        return {}

    targets = {}

    for name, block in raw.items():
        if not check_name(name, problems):
            continue

        spec = read_target(name, block, problems)

        if spec is None:
            continue

        targets[name] = spec

    return targets


def parse_config(raw: Any) -> OnsiteConfig:
    """Return a validated config, or raise ConfigError listing it all."""

    if not isinstance(raw, Mapping):
        raise ConfigError(["no settings were found"])

    problems: list[str] = []
    targets = read_targets(raw.get("targets"), problems)
    default = raw.get("default")

    if not isinstance(default, str):
        problems.append("`default` must name the target used when no "
                        "folder rule matches")

    if isinstance(default, str) and targets and default not in targets:
        problems.append(f"`default` names `{default}`, which is not a target")

    if problems:
        raise ConfigError(problems)

    return OnsiteConfig(default=default, targets=targets)


def target_to_settings(spec: TargetSpec) -> dict:
    block: dict[str, Any] = {"kind": spec.kind}

    if spec.kind == KIND_SSH:
        block["host"] = spec.host
        block["user"] = spec.user

    if spec.kind == KIND_SSH and spec.port != DEFAULT_SSH_PORT:
        block["port"] = spec.port

    if spec.key:
        block["key"] = spec.key

    if spec.known_hosts:
        block["known_hosts"] = spec.known_hosts

    if spec.prefixes:
        block["prefixes"] = list(spec.prefixes)

    if spec.path_map:
        block["path_map"] = dict(spec.path_map)

    if spec.mirror:
        block["mirror"] = spec.mirror

    if spec.mirror and spec.mirror_depth != DEFAULT_MIRROR_DEPTH:
        block["mirror_depth"] = spec.mirror_depth

    return block


def config_to_settings(config: OnsiteConfig) -> dict:
    """The plain block `hermes onsite init` writes to config.yaml."""

    targets = {
        name: target_to_settings(spec)
        for name, spec in config.targets.items()
    }

    return {"default": config.default, "targets": targets}

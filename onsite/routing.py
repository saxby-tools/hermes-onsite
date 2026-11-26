"""Pick the target for a session from its working directory.

The rules, in order:

1. A target set with `/onsite <target>` wins for that session.
2. Otherwise the longest matching path wins. A path matches through a
   target's `prefixes` or through the left side of its `path_map`.
3. Otherwise the default target is used.

A target's `mount` is one more map entry: the folder on the host
where the target's whole disk is mounted. A session opened under it
runs in the real folder on the target.

A brain and a hand rarely agree on paths, so a route also carries the
folder to start in on the target. This module is pure. It imports
nothing from Hermes and touches no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import OnsiteConfig
from .config import KIND_LOCAL
from .config import TargetSpec
from .config import is_absolute
from .config import normalize_path
from .paths import to_target


HOME = "~"
ROOT = "/"

REASON_OVERRIDE = "override"
REASON_PINNED = "pinned"
REASON_PREFIX = "prefix"
REASON_PATH_MAP = "path_map"
REASON_MOUNT = "mount"
REASON_DEFAULT = "default"


@dataclass(frozen=True)
class Route:
    """Where a session runs, the folder to start in, and why."""

    target: str
    cwd: str
    reason: str


@dataclass(frozen=True)
class Candidate:
    length: int
    order: int
    target: str
    cwd: str
    reason: str


def is_under(path: str, prefix: str) -> bool:
    if prefix == "/":
        return True

    if path == prefix:
        return True

    # The slash keeps `/home/dev` from matching `/home/developer`.
    return path.startswith(prefix + "/")


def swap_prefix(path: str, source: str, destination: str) -> str:
    rest = path[len(source):].lstrip("/")

    if not rest:
        return destination

    if destination == "/":
        return "/" + rest

    return f"{destination}/{rest}"


def map_entries(spec: TargetSpec) -> list[tuple[str, str, str]]:
    """Every host folder that stands for a folder on the target."""

    entries = [
        (source, destination, REASON_PATH_MAP)
        for source, destination in spec.path_map
    ]

    if spec.mount:
        entries.append((spec.mount, ROOT, REASON_MOUNT))

    return entries


def map_through(path: str, source: str, destination: str, reason: str) -> str:
    """A host path as the target path one map entry makes of it."""

    # A mount is the target's own disk. Its paths go through the one
    # module that knows a host folder from a target folder.
    if reason == REASON_MOUNT:
        return to_target(Path(source), Path(path)) or destination

    return swap_prefix(path, source, destination)


def map_path(path: str, spec: TargetSpec) -> str | None:
    """Translate a host path through the longest matching map entry."""

    best = None

    for entry in map_entries(spec):
        if not is_under(path, entry[0]):
            continue

        if best is not None and len(entry[0]) <= len(best[0]):
            continue

        best = entry

    if best is None:
        return None

    return map_through(path, *best)


def has_prefix_for(path: str, spec: TargetSpec) -> bool:
    return any(is_under(path, prefix) for prefix in spec.prefixes)


def translate(cwd: str, spec: TargetSpec) -> str:
    """The folder on the target that stands for a folder on the host.

    A folder that does not exist on the target must not be guessed at.
    When nothing maps, the session starts in the target's home.
    """

    if not is_absolute(cwd):
        return HOME

    path = normalize_path(cwd)
    mapped = map_path(path, spec)

    if mapped is not None:
        return mapped

    if spec.kind == KIND_LOCAL:
        return path

    if has_prefix_for(path, spec):
        return path

    return HOME


def collect_candidates(path: str, config: OnsiteConfig) -> list[Candidate]:
    candidates = []

    for order, spec in enumerate(config.targets.values()):
        for prefix in spec.prefixes:
            if not is_under(path, prefix):
                continue

            candidates.append(Candidate(
                length=len(prefix),
                order=order,
                target=spec.name,
                cwd=path,
                reason=REASON_PREFIX,
            ))

        for source, destination, reason in map_entries(spec):
            if not is_under(path, source):
                continue

            candidates.append(Candidate(
                length=len(source),
                order=order,
                target=spec.name,
                cwd=map_through(path, source, destination, reason),
                reason=reason,
            ))

    return candidates


def route(cwd: str, config: OnsiteConfig) -> Route:
    """Rules 2 and 3: the longest match, else the default target."""

    default = config.target(config.default)

    if not is_absolute(cwd):
        return Route(default.name, translate(cwd, default), REASON_DEFAULT)

    path = normalize_path(cwd)
    candidates = collect_candidates(path, config)

    if not candidates:
        return Route(default.name, translate(path, default), REASON_DEFAULT)

    # Longest match first. On a tie the target listed first wins, so
    # the result never depends on dictionary internals.
    best = min(candidates, key=lambda item: (-item.length, item.order))

    return Route(best.target, best.cwd, best.reason)


def resolve(
    cwd: str,
    config: OnsiteConfig,
    pinned: str | None = None,
    pin_reason: str = REASON_PINNED,
) -> Route:
    """Rule 1 on top of `route`: a pinned target wins when it exists."""

    if pinned is None:
        return route(cwd, config)

    # A pin can outlive a config edit. A stale name falls back to the
    # folder rules rather than failing the session.
    if pinned not in config.targets:
        return route(cwd, config)

    spec = config.target(pinned)

    return Route(spec.name, translate(cwd, spec), pin_reason)

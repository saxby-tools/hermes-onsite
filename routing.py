"""Pick the target for a session from its working directory.

The rules, in order:

1. A target set with `/hands <target>` wins for that session.
2. Otherwise the longest matching path wins. A path matches through a
   target's `prefixes` or through the left side of its `path_map`.
3. Otherwise the default target is used.

A brain and a hand rarely agree on paths, so a route also carries the
folder to start in on the target. This module is pure. It imports
nothing from Hermes and touches no network.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import HandsConfig
from .config import KIND_LOCAL
from .config import TargetSpec
from .config import is_absolute
from .config import normalize_path


HOME = "~"

REASON_OVERRIDE = "override"
REASON_PINNED = "pinned"
REASON_PREFIX = "prefix"
REASON_PATH_MAP = "path_map"
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


def map_path(path: str, spec: TargetSpec) -> str | None:
    """Translate a host path through the longest matching map entry."""

    best = None

    for source, destination in spec.path_map:
        if not is_under(path, source):
            continue

        if best is not None and len(source) <= len(best[0]):
            continue

        best = (source, destination)

    if best is None:
        return None

    return swap_prefix(path, best[0], best[1])


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


def collect_candidates(path: str, config: HandsConfig) -> list[Candidate]:
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

        for source, destination in spec.path_map:
            if not is_under(path, source):
                continue

            candidates.append(Candidate(
                length=len(source),
                order=order,
                target=spec.name,
                cwd=swap_prefix(path, source, destination),
                reason=REASON_PATH_MAP,
            ))

    return candidates


def route(cwd: str, config: HandsConfig) -> Route:
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
    config: HandsConfig,
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

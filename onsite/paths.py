"""The one place a host folder and a target folder are told apart.

A target's whole disk is mounted in a folder on the host, the mount
root. Every path is then one of two kinds, and they are never mixed:

- A target path is a string in the form SFTP uses on every system:
  forward slashes, from the root. Linux and macOS give `/home/you`.
  Windows gives `/C:/Users/you`. It is never built from a user name
  or a guess. The target is asked.
- A host path is a `pathlib.Path` on the machine Hermes runs on,
  under the mount root.

Nothing else in the plugin joins path strings by hand. A host that
runs Windows will need its own way to spell a mount root and a drive
letter, and this module is where that change goes.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import posixpath

from pathlib import Path
from pathlib import PurePosixPath


TARGET_ROOT = "/"
TARGET_SEPARATOR = "/"
NUL = "\0"

# Names that would let a path step outside the folder it is under.
CLIMBING_PARTS = ("", ".", "..")


class PathError(ValueError):
    """A path that cannot be a target path. The message says why."""


def clean_target(path: str) -> str:
    """A target path in one spelling: from the root, no dots.

    `..` is resolved here, as text, so it can never reach above the
    root. A relative path is refused: only the target knows what it
    would be relative to.
    """

    if NUL in path:
        raise PathError("a path cannot hold a NUL character")

    if not path.startswith(TARGET_SEPARATOR):
        raise PathError(f"`{path}` does not start at the target's root")

    return posixpath.normpath(path)


def target_parts(path: str) -> tuple[str, ...]:
    """The names in a target path, root first, without the root."""

    return PurePosixPath(clean_target(path)).parts[1:]


def host_part(name: str) -> str:
    """One name of a target path as a name on the host.

    Linux and macOS hosts take every name a target can send, a drive
    such as `C:` included. A Windows host will not, and this is the
    one function that will have to say what it becomes there.
    """

    return name


def to_host(mount_root: Path, target_path: str) -> Path:
    """Where a target path is on the host, under its mount root."""

    names = [host_part(name) for name in target_parts(target_path)]

    return mount_root.joinpath(*names)


def to_target(mount_root: Path, host_path: Path) -> str | None:
    """The target path a host path stands for, or None if it is not
    under the mount root.

    Nothing is resolved on disk. A link inside a mount belongs to the
    target, and following it on the host would give the wrong answer.
    """

    try:
        inside = host_path.relative_to(mount_root)
    except ValueError:
        return None

    if any(name in CLIMBING_PARTS for name in inside.parts):
        return None

    return TARGET_ROOT + TARGET_SEPARATOR.join(inside.parts)


def join_target(folder: str, name: str) -> str:
    """A name inside a target folder. The name must be one name."""

    if name in CLIMBING_PARTS:
        raise PathError(f"`{name}` is not a name inside a folder")

    if TARGET_SEPARATOR in name:
        raise PathError(f"`{name}` holds a path separator")

    if NUL in name:
        raise PathError("a name cannot hold a NUL character")

    if folder == TARGET_ROOT:
        return TARGET_ROOT + name

    return folder + TARGET_SEPARATOR + name


def parent_target(path: str) -> str:
    """The folder a target path is in. The root is its own parent."""

    return posixpath.dirname(path) or TARGET_ROOT

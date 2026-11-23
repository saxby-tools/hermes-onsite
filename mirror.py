"""A names-only copy of a target's folder tree, kept on the host.

Hermes only runs a session in a folder that exists on the host, and
the desktop app's folder picker reads the host's disk. A folder on an
SSH target exists on neither. So the plugin keeps an empty folder on
the host for each folder on the target: the same names, no files. A
session opened in one of them runs in the real folder on the target,
because `routing` treats the mirror as a map to the target's root.

Three rules keep the mirror from being mistaken for the data:

- only folders are made, never a file,
- nothing is ever deleted,
- nothing is made outside the mirror folder, even through a link.

Standard library only. It drives the `ssh` program, as `health` does.
"""

from __future__ import annotations

import shlex
import subprocess

from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

from .config import TargetSpec
from .health import build_oneshot_command
from .health import last_line


LIST_TIMEOUT_SECONDS = 30
CHECK_TIMEOUT_SECONDS = 10

SEPARATOR = b"\0"
HERE = "./"
UNSAFE_PARTS = ("", ".", "..")

# Folders whose names start with a dot are left out. They hold tool
# state, not projects, and there can be hundreds of thousands of them.
LIST_SCRIPT = (
    'cd "$HOME" && printf "%s\\0" "$(pwd -P)" && '
    "find . -mindepth 1 -maxdepth {depth} -type d "
    "-not -path '*/.*' -print0"
)
CHECK_SCRIPT = 'cd {folder} && printf "%s\\0" "$(pwd -P)"'
HOME_PREFIX = "~/"
HOME_ALONE = "~"


class MirrorError(RuntimeError):
    """The mirror could not be read or made. The message says why."""


@dataclass(frozen=True)
class Listing:
    """What a target said: its home folder, and the folders under it."""

    home: str
    folders: tuple[str, ...]


@dataclass(frozen=True)
class Result:
    """What one pass did, or would do on a dry run."""

    root: str
    made: int
    kept: int
    skipped: tuple[str, ...]


def require_mirror(spec: TargetSpec) -> Path:
    if not spec.mirror:
        raise MirrorError(f"target `{spec.name}` has no `mirror` folder set")

    return Path(spec.mirror)


def run_remote(spec: TargetSpec, script: str, timeout: int) -> bytes:
    # The login shell on a target may not be POSIX. `sh` always is.
    remote = "sh -c " + shlex.quote(script)

    try:
        done = subprocess.run(
            build_oneshot_command(spec, remote),
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise MirrorError(
            f"{spec.label} did not answer in {timeout}s"
        ) from None
    except OSError as error:
        raise MirrorError(f"cannot run ssh: {error}") from None

    if done.returncode != 0:
        detail = last_line(done.stderr.decode("utf-8", "replace"))

        raise MirrorError(detail or f"ssh exited {done.returncode}")

    return done.stdout


def split_fields(raw: bytes) -> list[str]:
    fields = raw.split(SEPARATOR)

    return [field.decode("utf-8", "surrogateescape") for field in fields]


def safe_parts(folder: str) -> tuple[str, ...] | None:
    """The parts of a folder path, or None if it could climb out."""

    parts = tuple(part for part in folder.split("/") if part != "")

    if not parts:
        return None

    if any(part in UNSAFE_PARTS for part in parts):
        return None

    return parts


def parse_listing(raw: bytes) -> Listing:
    fields = [field for field in split_fields(raw) if field]

    if not fields:
        raise MirrorError("the target sent an empty folder list")

    home = fields[0]

    if not home.startswith("/"):
        raise MirrorError(f"the target sent a home that is no path: {home!r}")

    folders = []

    for field in fields[1:]:
        name = field.removeprefix(HERE)

        # A name the mirror cannot hold safely is left out, not fixed.
        if safe_parts(name) is None:
            continue

        folders.append(name)

    return Listing(home=home, folders=tuple(sorted(folders)))


def host_folder(mirror: Path, target_folder: str) -> Path:
    """The folder on the host that stands for a folder on the target."""

    if not target_folder.startswith("/"):
        raise MirrorError(f"{target_folder!r} is not an absolute path")

    if target_folder.strip("/") == "":
        return mirror

    parts = safe_parts(target_folder)

    if parts is None:
        raise MirrorError(f"{target_folder!r} cannot be mirrored")

    return mirror.joinpath(*parts)


def plan(mirror: Path, listing: Listing) -> list[Path]:
    home = host_folder(mirror, listing.home)
    folders = [home]

    for name in listing.folders:
        folders.append(home.joinpath(*PurePosixPath(name).parts))

    return folders


def is_inside(folder: Path, root: Path) -> bool:
    """True when making `folder` cannot land outside `root`.

    Both are resolved first, so a link planted inside the mirror
    cannot send a new folder somewhere else on the host.
    """

    resolved = folder.resolve(strict=False)
    resolved_root = root.resolve(strict=False)

    if resolved == resolved_root:
        return True

    return resolved_root in resolved.parents


def make_folders(root: Path, folders: list[Path], apply: bool) -> Result:
    made = 0
    kept = 0
    skipped = []

    for folder in folders:
        if not is_inside(folder, root):
            skipped.append(str(folder))
            continue

        if folder.is_dir():
            kept += 1
            continue

        # A file or a dead link already has this name. It is left be.
        if folder.exists() or folder.is_symlink():
            skipped.append(str(folder))
            continue

        if apply:
            folder.mkdir(parents=True, exist_ok=True)

        made += 1

    return Result(
        root=str(root),
        made=made,
        kept=kept,
        skipped=tuple(skipped),
    )


def read_listing(spec: TargetSpec, depth: int) -> Listing:
    script = LIST_SCRIPT.format(depth=depth)

    return parse_listing(run_remote(spec, script, LIST_TIMEOUT_SECONDS))


def sync(spec: TargetSpec, depth: int, apply: bool) -> Result:
    """Mirror the folders under the target's home, `depth` levels down."""

    mirror = require_mirror(spec)
    listing = read_listing(spec, depth)

    return make_folders(mirror, plan(mirror, listing), apply)


def folder_word(target_folder: str) -> str:
    """A folder as a shell word. A leading `~` still means home."""

    if target_folder == HOME_ALONE:
        return '"$HOME"'

    if target_folder.startswith(HOME_PREFIX):
        rest = target_folder.removeprefix(HOME_PREFIX)

        return '"$HOME"/' + shlex.quote(rest)

    return shlex.quote(target_folder)


def real_folder(spec: TargetSpec, target_folder: str) -> str:
    """The folder's true path on the target, or an error if it is not one."""

    script = CHECK_SCRIPT.format(folder=folder_word(target_folder))

    try:
        raw = run_remote(spec, script, CHECK_TIMEOUT_SECONDS)
    except MirrorError as error:
        raise MirrorError(
            f"{target_folder} is not a folder on {spec.label}: {error}"
        ) from None

    fields = [field for field in split_fields(raw) if field]

    if not fields:
        raise MirrorError(f"{spec.label} did not name {target_folder}")

    return fields[0]


def open_folder(
    spec: TargetSpec,
    target_folder: str,
    apply: bool,
) -> tuple[Path, Result]:
    """Mirror one folder on demand, however deep it is."""

    mirror = require_mirror(spec)
    folder = host_folder(mirror, real_folder(spec, target_folder))

    return folder, make_folders(mirror, [folder], apply)

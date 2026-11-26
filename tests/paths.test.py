"""Host paths and target paths, and the one place they meet."""

from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
from pathlib import PureWindowsPath

import pytest

from onsite import paths


ROOT = Path("/home/dev/.local/share/onsite/mount/work/laptop")


def test_a_target_path_lands_under_the_mount_root():
    found = paths.to_host(ROOT, "/home/you/work")

    assert found == ROOT / "home" / "you" / "work"


def test_the_targets_root_is_the_mount_root_itself():
    assert paths.to_host(ROOT, "/") == ROOT


def test_a_host_path_under_the_mount_maps_back():
    assert paths.to_target(ROOT, ROOT / "home" / "you") == "/home/you"
    assert paths.to_target(ROOT, ROOT) == "/"


def test_a_host_path_outside_the_mount_is_nobodys():
    assert paths.to_target(ROOT, Path("/var/hermes")) is None
    assert paths.to_target(ROOT, Path("/home/you")) is None


def test_the_round_trip_holds_for_awkward_names():
    awkward = (
        "/home/you/a b",
        "/srv/répertoire",
        "/C:/Users/you",
        "/x/.git",
    )

    for path in awkward:
        assert paths.to_target(ROOT, paths.to_host(ROOT, path)) == path


def test_a_windows_targets_drive_is_one_name():
    assert paths.target_parts("/C:/Users/you") == ("C:", "Users", "you")


def test_dots_are_resolved_as_text_and_cannot_pass_the_root():
    assert paths.clean_target("/home/you/../../../etc") == "/etc"
    assert paths.to_host(ROOT, "/../../etc") == ROOT / "etc"


def test_a_relative_target_path_is_refused():
    for path in ("home/you", "~/work", "", "."):
        with pytest.raises(paths.PathError):
            paths.clean_target(path)


def test_a_nul_is_refused():
    with pytest.raises(paths.PathError):
        paths.clean_target("/home/you\0/x")


def test_a_host_path_that_climbs_is_nobodys():
    assert paths.to_target(ROOT, ROOT / ".." / "other") is None


def test_joining_takes_one_name_only():
    assert paths.join_target("/", "home") == "/home"
    assert paths.join_target("/home", "you") == "/home/you"

    for name in ("", ".", "..", "a/b", "a\0b"):
        with pytest.raises(paths.PathError):
            paths.join_target("/home", name)


def test_the_root_is_its_own_parent():
    assert paths.parent_target("/home/you") == "/home"
    assert paths.parent_target("/home") == "/"
    assert paths.parent_target("/") == "/"


def test_the_mapping_never_touches_the_disk():
    # A pure path has no disk to touch. If the mapping called
    # `resolve` or `exists`, these would raise.
    posix_root = PurePosixPath("/mnt/laptop")

    assert paths.to_target(posix_root, posix_root / "home") == "/home"
    assert paths.to_host(posix_root, "/home") == posix_root / "home"


def test_a_windows_host_root_already_maps_plain_names():
    root = PureWindowsPath("C:/ProgramData/hermes/mount/laptop")

    assert paths.to_host(root, "/home/you") == root / "home" / "you"
    assert paths.to_target(root, root / "home" / "you") == "/home/you"

"""The names-only mirror: folders are made, files never are."""

from __future__ import annotations

import pytest

from onsite import config
from onsite import mirror
from onsite import routing


HOME = "/home/dev"
LISTING = b"\0".join([
    HOME.encode(),
    b"./Development",
    b"./Development/site",
    b"./Downloads",
    b"./Music/caf\xc3\xa9",
    b"",
])


def settings(mirror_folder: str, **extra) -> dict:
    laptop = {
        "kind": "ssh",
        "host": "laptop.example",
        "user": "dev",
        "mirror": mirror_folder,
    }
    laptop.update(extra)

    return {
        "default": "mac",
        "targets": {"laptop": laptop, "mac": {"kind": "local"}},
    }


def laptop(mirror_folder: str, **extra) -> config.TargetSpec:
    parsed = config.parse_config(settings(mirror_folder, **extra))

    return parsed.target("laptop")


def files_under(folder) -> list:
    return [path for path in folder.rglob("*") if not path.is_dir()]


# ---- settings ---------------------------------------------------------


def test_a_mirror_is_read_and_written_back():
    spec = laptop("/Users/dev/Onsite/laptop/", mirror_depth=4)

    assert spec.mirror == "/Users/dev/Onsite/laptop"
    assert spec.mirror_depth == 4

    block = config.target_to_settings(spec)

    assert block["mirror"] == "/Users/dev/Onsite/laptop"
    assert block["mirror_depth"] == 4


def test_the_default_depth_is_not_written_back():
    block = config.target_to_settings(laptop("/Users/dev/Onsite/laptop"))

    assert "mirror_depth" not in block


@pytest.mark.parametrize("value, problem", [
    ("Onsite/laptop", "must be an absolute path"),
    ("/", "cannot be the root folder"),
])
def test_a_bad_mirror_folder_is_refused(value, problem):
    with pytest.raises(config.ConfigError, match=problem):
        config.parse_config(settings(value))


@pytest.mark.parametrize("depth", [0, 7, "3", True])
def test_a_bad_depth_is_refused(depth):
    with pytest.raises(config.ConfigError, match="mirror_depth"):
        config.parse_config(settings("/Users/dev/Onsite", mirror_depth=depth))


def test_a_local_target_cannot_have_a_mirror():
    raw = settings("/Users/dev/Onsite")
    raw["targets"]["mac"]["mirror"] = "/Users/dev/Other"

    with pytest.raises(config.ConfigError, match="only for an ssh target"):
        config.parse_config(raw)


# ---- routing ----------------------------------------------------------


def test_a_folder_in_the_mirror_runs_on_the_target():
    parsed = config.parse_config(settings("/Users/dev/Onsite/laptop"))
    found = routing.route(
        "/Users/dev/Onsite/laptop/home/dev/Downloads/site",
        parsed,
    )

    assert found.target == "laptop"
    assert found.cwd == "/home/dev/Downloads/site"
    assert found.reason == routing.REASON_MIRROR


def test_the_mirror_folder_itself_is_the_root_of_the_target():
    parsed = config.parse_config(settings("/Users/dev/Onsite/laptop"))
    found = routing.route("/Users/dev/Onsite/laptop", parsed)

    assert found.target == "laptop"
    assert found.cwd == "/"


def test_a_folder_beside_the_mirror_is_not_in_it():
    parsed = config.parse_config(settings("/Users/dev/Onsite/laptop"))
    found = routing.route("/Users/dev/Onsite/laptop-old/x", parsed)

    assert found.target == "mac"


def test_a_longer_path_map_entry_beats_the_mirror():
    parsed = config.parse_config(settings(
        "/Users/dev/Onsite/laptop",
        path_map={"/Users/dev/Onsite/laptop/mnt": "/media/disk"},
    ))
    found = routing.route("/Users/dev/Onsite/laptop/mnt/photos", parsed)

    assert found.cwd == "/media/disk/photos"
    assert found.reason == routing.REASON_PATH_MAP


# ---- reading what the target says -------------------------------------


def test_a_listing_is_the_home_and_the_folders_under_it():
    listing = mirror.parse_listing(LISTING)

    assert listing.home == HOME
    assert listing.folders == (
        "Development",
        "Development/site",
        "Downloads",
        "Music/café",
    )


@pytest.mark.parametrize("name", [
    b"./../../etc",
    b"./a/../../b",
    b"./a/./b",
])
def test_a_name_that_could_climb_out_is_left_out(name):
    listing = mirror.parse_listing(HOME.encode() + b"\0" + name + b"\0")

    assert listing.folders == ()


@pytest.mark.parametrize("raw, problem", [
    (b"", "empty folder list"),
    (b"not-a-path\0./a\0", "no path"),
])
def test_a_listing_with_no_home_is_an_error(raw, problem):
    with pytest.raises(mirror.MirrorError, match=problem):
        mirror.parse_listing(raw)


def test_a_target_folder_maps_under_the_mirror(tmp_path):
    folder = mirror.host_folder(tmp_path, "/home/dev/Downloads/site")

    assert folder == tmp_path / "home" / "dev" / "Downloads" / "site"
    assert mirror.host_folder(tmp_path, "/") == tmp_path


@pytest.mark.parametrize("folder", ["relative/path", "/home/../etc"])
def test_a_target_folder_that_is_not_safe_is_refused(tmp_path, folder):
    with pytest.raises(mirror.MirrorError):
        mirror.host_folder(tmp_path, folder)


@pytest.mark.parametrize("folder, word", [
    ("~", '"$HOME"'),
    ("~/my work", "\"$HOME\"/'my work'"),
    ("/srv/my work", "'/srv/my work'"),
])
def test_a_leading_tilde_still_means_home(folder, word):
    assert mirror.folder_word(folder) == word


# ---- making the folders -----------------------------------------------


def test_a_dry_run_counts_and_makes_nothing(tmp_path):
    root = tmp_path / "mirror"
    folders = mirror.plan(root, mirror.parse_listing(LISTING))
    result = mirror.make_folders(root, folders, apply=False)

    assert result.made == 5
    assert result.kept == 0
    assert not root.exists()


def test_applying_makes_folders_and_no_files(tmp_path):
    root = tmp_path / "mirror"
    folders = mirror.plan(root, mirror.parse_listing(LISTING))
    result = mirror.make_folders(root, folders, apply=True)

    assert result.made == 5
    assert (root / "home/dev/Development/site").is_dir()
    assert (root / "home/dev/Music/café").is_dir()
    assert files_under(root) == []


def test_a_second_pass_keeps_what_is_there(tmp_path):
    root = tmp_path / "mirror"
    folders = mirror.plan(root, mirror.parse_listing(LISTING))

    mirror.make_folders(root, folders, apply=True)
    again = mirror.make_folders(root, folders, apply=True)

    assert again.made == 0
    assert again.kept == 5


def test_a_file_with_the_same_name_is_left_alone(tmp_path):
    root = tmp_path / "mirror"
    (root / "home/dev").mkdir(parents=True)
    (root / "home/dev/Downloads").write_text("mine")

    folders = mirror.plan(root, mirror.parse_listing(LISTING))
    result = mirror.make_folders(root, folders, apply=True)

    assert result.skipped == (str(root / "home/dev/Downloads"),)
    assert (root / "home/dev/Downloads").read_text() == "mine"


def test_a_link_cannot_send_a_folder_outside_the_mirror(tmp_path):
    root = tmp_path / "mirror"
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "home").mkdir(parents=True)
    (root / "home/dev").symlink_to(outside)

    folders = mirror.plan(root, mirror.parse_listing(LISTING))
    result = mirror.make_folders(root, folders, apply=True)

    assert result.made == 0
    assert len(result.skipped) == 5
    assert list(outside.iterdir()) == []


# ---- the two commands, with the target faked --------------------------


def test_sync_mirrors_what_the_target_lists(tmp_path, monkeypatch):
    root = tmp_path / "mirror"
    scripts = []

    def fake_remote(spec, script, timeout):
        scripts.append(script)

        return LISTING

    monkeypatch.setattr(mirror, "run_remote", fake_remote)

    result = mirror.sync(laptop(str(root)), depth=2, apply=True)

    assert result.made == 5
    assert "-maxdepth 2" in scripts[0]
    assert "-not -path '*/.*'" in scripts[0]


def test_sync_needs_a_mirror_folder():
    spec = config.TargetSpec(name="laptop", kind="ssh")

    with pytest.raises(mirror.MirrorError, match="no `mirror` folder"):
        mirror.sync(spec, depth=2, apply=True)


def test_open_makes_one_folder_at_any_depth(tmp_path, monkeypatch):
    root = tmp_path / "mirror"
    deep = "/home/dev/a/b/c/d/e/f"

    monkeypatch.setattr(
        mirror,
        "run_remote",
        lambda spec, script, timeout: deep.encode() + b"\0",
    )

    spec = laptop(str(root))
    folder, result = mirror.open_folder(spec, "~/a/b/c/d/e/f", True)

    assert folder == root / "home/dev/a/b/c/d/e/f"
    assert folder.is_dir()
    assert result.made == 1


def test_open_says_so_when_the_folder_is_not_there(tmp_path, monkeypatch):
    def fake_remote(spec, script, timeout):
        raise mirror.MirrorError("No such file or directory")

    monkeypatch.setattr(mirror, "run_remote", fake_remote)

    with pytest.raises(mirror.MirrorError, match="is not a folder on"):
        mirror.open_folder(laptop(str(tmp_path)), "/nope", True)

    assert list(tmp_path.iterdir()) == []

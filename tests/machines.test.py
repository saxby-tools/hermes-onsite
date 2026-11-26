"""The routes behind the machines form and a machine's mounted disk."""

from __future__ import annotations

import copy

import pytest

from onsite import api
from onsite import config
from onsite import health
from onsite import mounts
from onsite import runtime


SETTINGS = {
    "default": "mac",
    "targets": {
        "laptop": {
            "kind": "ssh",
            "host": "laptop.example",
            "user": "dev",
            "prefixes": ["/home/dev/"],
        },
        "mac": {"kind": "local"},
    },
}


class Harness:
    """A runtime whose settings live in memory and can be saved."""

    def __init__(self, tmp_path, settings=SETTINGS, writable=True):
        self.settings = copy.deepcopy(settings)
        self.saves = 0

        session = runtime.SessionReader(cwd=lambda: "", session_id=lambda: "")

        self.runtime = runtime.Runtime(
            load_settings=lambda: self.settings,
            data_dir=tmp_path,
            session=session,
            build_environment=lambda *args: object(),
            save_settings=self.save if writable else None,
        )

    def save(self, settings: dict) -> None:
        self.settings = settings
        self.saves += 1


NEW_MACHINE = {
    "kind": "ssh",
    "host": "desk.example",
    "user": "dev",
    "port": 2222,
    "key": "",
    "mount": "   ",
    "prefixes": [],
}


# ---- a mount with no set-up -------------------------------------------


def test_an_ssh_target_gets_a_mount_root_without_asking(tmp_path):
    spec = Harness(tmp_path).runtime.load_config().target("laptop")

    assert spec.mount == str(mounts.default_root(tmp_path, "laptop"))


def test_a_mount_root_named_in_the_settings_is_kept(tmp_path):
    settings = copy.deepcopy(SETTINGS)
    settings["targets"]["laptop"]["mount"] = "/srv/onsite/laptop"

    spec = Harness(tmp_path, settings).runtime.load_config().target("laptop")

    assert spec.mount == "/srv/onsite/laptop"


def test_a_local_target_has_no_disk_to_mount(tmp_path):
    spec = Harness(tmp_path).runtime.load_config().target("mac")

    assert spec.mount == ""


def test_a_folder_under_the_mount_runs_on_the_target(tmp_path):
    harness = Harness(tmp_path)
    folder = mounts.default_root(tmp_path, "laptop") / "srv" / "site"

    status = harness.runtime.status(cwd=str(folder))

    assert status["target"] == "laptop"
    assert status["cwd"] == "/srv/site"
    assert status["reason"] == "mount"


def test_the_mount_root_itself_is_the_targets_root(tmp_path):
    harness = Harness(tmp_path)
    root = mounts.default_root(tmp_path, "laptop")
    status = harness.runtime.status(cwd=str(root))

    assert status["target"] == "laptop"
    assert status["cwd"] == "/"


# ---- the machines form ------------------------------------------------


def test_the_form_is_given_the_settings_as_written(tmp_path):
    payload = api.targets_payload(Harness(tmp_path).runtime)

    assert payload["ok"] is True
    assert payload["default"] == "mac"
    assert payload["targets"]["laptop"]["host"] == "laptop.example"
    assert "mount" not in payload["targets"]["laptop"]
    assert payload["problem"] == ""


def test_the_form_still_loads_when_the_settings_are_broken(tmp_path):
    harness = Harness(tmp_path, {"default": "gone", "targets": {}})
    payload = api.targets_payload(harness.runtime)

    assert payload["ok"] is True
    assert payload["targets"] == {}
    assert "not usable" in payload["problem"]


def test_there_are_no_settings_yet(tmp_path):
    harness = Harness(tmp_path, None)
    payload = api.targets_payload(harness.runtime)

    assert payload["targets"] == {}
    assert payload["default"] is None


def test_saving_a_machine_adds_it_and_drops_the_blank_fields(tmp_path):
    harness = Harness(tmp_path)
    payload = api.save_target_payload(
        harness.runtime, {"name": "desk", "target": NEW_MACHINE},
    )

    assert payload["ok"] is True
    assert harness.saves == 1
    assert harness.settings["targets"]["desk"] == {
        "kind": "ssh",
        "host": "desk.example",
        "user": "dev",
        "port": 2222,
    }
    assert harness.settings["default"] == "mac"


def test_the_first_machine_becomes_the_default(tmp_path):
    harness = Harness(tmp_path, None)
    api.save_target_payload(
        harness.runtime, {"name": "mac", "target": {"kind": "local"}},
    )

    assert harness.settings["default"] == "mac"


def test_a_machine_can_be_made_the_default(tmp_path):
    harness = Harness(tmp_path)
    api.save_target_payload(
        harness.runtime,
        {"name": "desk", "target": NEW_MACHINE, "default": True},
    )

    assert harness.settings["default"] == "desk"


def test_a_machine_that_would_not_load_is_not_saved(tmp_path):
    harness = Harness(tmp_path)
    payload = api.save_target_payload(
        harness.runtime,
        {"name": "desk", "target": {"kind": "ssh", "user": "dev"}},
    )

    assert payload["ok"] is False
    assert "`desk.host` is required" in payload["error"]
    assert harness.saves == 0
    assert "desk" not in harness.settings["targets"]


def test_a_machine_needs_a_name(tmp_path):
    harness = Harness(tmp_path)
    payload = api.save_target_payload(harness.runtime, {"target": NEW_MACHINE})

    assert payload == api.failure("Give the machine a name.")


def test_removing_a_machine_takes_it_out_of_the_settings(tmp_path):
    harness = Harness(tmp_path)
    payload = api.remove_target_payload(harness.runtime, {"name": "laptop"})

    assert payload["ok"] is True
    assert list(harness.settings["targets"]) == ["mac"]


def test_the_default_machine_cannot_be_removed(tmp_path):
    harness = Harness(tmp_path)
    payload = api.remove_target_payload(harness.runtime, {"name": "mac"})

    assert payload["ok"] is False
    assert "is the default" in payload["error"]
    assert harness.saves == 0


def test_an_unknown_machine_cannot_be_removed(tmp_path):
    payload = api.remove_target_payload(
        Harness(tmp_path).runtime, {"name": "ghost"},
    )

    assert payload["ok"] is False


def test_settings_that_cannot_be_written_say_so(tmp_path):
    harness = Harness(tmp_path, writable=False)
    payload = api.save_target_payload(
        harness.runtime, {"name": "desk", "target": NEW_MACHINE},
    )

    assert payload["ok"] is False
    assert "cannot be written" in payload["error"]


# ---- trying a machine before saving it --------------------------------


def test_a_machine_is_tried_as_the_form_describes_it(tmp_path, monkeypatch):
    tried = []

    def fake_probe(spec):
        tried.append((spec.host, spec.port, spec.user))

        return health.ProbeResult(True, "", 41)

    monkeypatch.setattr(api, "probe_target", fake_probe)

    payload = api.test_target_payload(
        Harness(tmp_path).runtime, {"name": "desk", "target": NEW_MACHINE},
    )

    assert payload["connected"] is True
    assert payload["latency_ms"] == 41
    assert tried == [("desk.example", 2222, "dev")]


def test_a_machine_with_no_address_is_not_tried(tmp_path, monkeypatch):
    monkeypatch.setattr(
        api, "probe_target", lambda spec: pytest.fail("must not dial"),
    )

    payload = api.test_target_payload(
        Harness(tmp_path).runtime, {"target": {"kind": "ssh"}},
    )

    assert payload["ok"] is False


# ---- a machine's disk on the host --------------------------------------


def mounted(monkeypatch, tmp_path, home="/home/dev"):
    """Stand in for the mount: a folder on disk that plays the
    target's whole disk, with a few folders in it."""

    root = mounts.default_root(tmp_path, "laptop")
    asked = []

    for folder in ("home/dev/work/site", "srv/data"):
        (root / folder).mkdir(parents=True, exist_ok=True)

    def fake_ensure(spec, mount_root, data_dir, system=None):
        asked.append(spec.name)

        return mounts.Mounted(mount_root, home)

    monkeypatch.setattr(mounts, "ensure", fake_ensure)

    return root, asked


def test_with_no_folder_named_a_machine_opens_where_a_login_lands(
    tmp_path,
    monkeypatch,
):
    root, _ = mounted(monkeypatch, tmp_path)
    payload = api.open_payload(Harness(tmp_path).runtime, {"target": "laptop"})

    assert payload["ok"] is True
    assert payload["cwd"] == str(root / "home" / "dev")
    assert payload["path"] == "/home/dev"


def test_a_start_folder_in_the_settings_wins(tmp_path, monkeypatch):
    root, _ = mounted(monkeypatch, tmp_path)
    settings = copy.deepcopy(SETTINGS)
    settings["targets"]["laptop"]["start"] = "/srv/data"

    payload = api.open_payload(
        Harness(tmp_path, settings).runtime,
        {"target": "laptop"},
    )

    assert payload["cwd"] == str(root / "srv" / "data")


@pytest.mark.parametrize("asked, shown", [
    ("~/work/site", "/home/dev/work/site"),
    ("~", "/home/dev"),
    ("/srv/data", "/srv/data"),
    ("/srv/../srv/data/", "/srv/data"),
])
def test_a_folder_is_found_under_the_mount(tmp_path, monkeypatch, asked, shown):
    root, _ = mounted(monkeypatch, tmp_path)
    payload = api.open_payload(
        Harness(tmp_path).runtime,
        {"target": "laptop", "path": asked},
    )

    assert payload["ok"] is True
    assert payload["path"] == shown
    assert payload["cwd"] == str(root) + shown


def test_a_folder_that_is_not_there_is_not_opened(tmp_path, monkeypatch):
    mounted(monkeypatch, tmp_path)

    payload = api.open_payload(
        Harness(tmp_path).runtime,
        {"target": "laptop", "path": "~/nope"},
    )

    assert payload["ok"] is False
    assert "/home/dev/nope is not a folder on laptop" in payload["error"]


def test_a_target_that_is_away_is_told_apart_from_a_missing_folder(
    tmp_path,
    monkeypatch,
):
    mounted(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mounts,
        "look_for_folder",
        lambda folder: mounts.FOLDER_AWAY,
    )

    payload = api.open_payload(Harness(tmp_path).runtime, {"target": "laptop"})

    assert payload["ok"] is False
    assert "is not answering. It may be asleep." in payload["error"]


def test_a_path_cannot_climb_out_of_the_mount(tmp_path, monkeypatch):
    root, _ = mounted(monkeypatch, tmp_path)
    (tmp_path / "secret").mkdir()

    payload = api.open_payload(
        Harness(tmp_path).runtime,
        {"target": "laptop", "path": "/../../secret"},
    )

    assert payload["ok"] is False
    assert str(root) not in payload.get("cwd", "")


def test_a_target_that_did_not_say_where_a_login_lands(tmp_path, monkeypatch):
    mounted(monkeypatch, tmp_path, home="")

    payload = api.open_payload(Harness(tmp_path).runtime, {"target": "laptop"})

    assert payload["ok"] is False
    assert "did not say where a login starts" in payload["error"]


def test_this_machine_has_no_disk_to_open(tmp_path):
    payload = api.open_payload(Harness(tmp_path).runtime, {"target": "mac"})

    assert payload["ok"] is False
    assert "is the Hermes server" in payload["error"]


def test_a_mount_that_fails_says_why_and_is_not_retried_at_once(
    tmp_path,
    monkeypatch,
):
    tries = []

    def refuse(spec, mount_root, data_dir, system=None):
        tries.append(spec.name)

        raise mounts.MountError("this server has no `sshfs`. Install it")

    monkeypatch.setattr(mounts, "ensure", refuse)

    harness = Harness(tmp_path)

    for _ in range(3):
        payload = api.open_payload(harness.runtime, {"target": "laptop"})

        assert payload["ok"] is False
        assert "has no `sshfs`" in payload["error"]

    assert tries == ["laptop"]


def test_the_machine_list_says_which_disks_could_not_be_mounted(
    tmp_path,
    monkeypatch,
):
    def refuse(spec, mount_root, data_dir, system=None):
        raise mounts.MountError("no `sshfs`")

    monkeypatch.setattr(mounts, "ensure", refuse)

    harness = Harness(tmp_path)
    spec = harness.runtime.load_config().target("laptop")

    harness.runtime._gates[
        (spec.name, spec.kind, spec.host, spec.user, spec.port)
    ] = health.Gate(
        spec,
        probe=lambda target: health.ProbeResult(True, "", 12),
    )

    payload = api.status_payload(harness.runtime, {}, with_targets=True)
    rows = {row["name"]: row for row in payload["rows"]}

    assert rows["laptop"]["mount_error"] == "no `sshfs`"
    assert rows["mac"]["mount_error"] == ""


def test_listing_the_machines_mounts_each_ssh_disk(tmp_path, monkeypatch):
    _, asked = mounted(monkeypatch, tmp_path)
    harness = Harness(tmp_path)
    spec = harness.runtime.load_config().target("laptop")

    harness.runtime._gates[
        (spec.name, spec.kind, spec.host, spec.user, spec.port)
    ] = health.Gate(
        spec,
        probe=lambda target: health.ProbeResult(True, "", 12),
    )

    api.status_payload(harness.runtime, {}, with_targets=True)

    assert asked == ["laptop"]

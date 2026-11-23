"""The routes behind the machines form and the folder browser."""

from __future__ import annotations

import copy

import pytest

from onsite import api
from onsite import config
from onsite import health
from onsite import mirror
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
    "mirror": "   ",
    "prefixes": [],
}


# ---- a mirror with no set-up ------------------------------------------


def test_an_ssh_target_gets_a_mirror_without_asking(tmp_path):
    spec = Harness(tmp_path).runtime.load_config().target("laptop")

    assert spec.mirror == str(tmp_path / "mirror" / "laptop")


def test_a_mirror_named_in_the_settings_is_kept(tmp_path):
    settings = copy.deepcopy(SETTINGS)
    settings["targets"]["laptop"]["mirror"] = "/Users/dev/Onsite/laptop"

    spec = Harness(tmp_path, settings).runtime.load_config().target("laptop")

    assert spec.mirror == "/Users/dev/Onsite/laptop"


def test_a_local_target_gets_no_mirror(tmp_path):
    spec = Harness(tmp_path).runtime.load_config().target("mac")

    assert spec.mirror == ""


def test_a_folder_in_the_default_mirror_runs_on_the_target(tmp_path):
    harness = Harness(tmp_path)
    folder = tmp_path / "mirror" / "laptop" / "srv" / "site"

    status = harness.runtime.status(cwd=str(folder))

    assert status["target"] == "laptop"
    assert status["cwd"] == "/srv/site"
    assert status["reason"] == "mirror"


# ---- the machines form ------------------------------------------------


def test_the_form_is_given_the_settings_as_written(tmp_path):
    payload = api.targets_payload(Harness(tmp_path).runtime)

    assert payload["ok"] is True
    assert payload["default"] == "mac"
    assert payload["targets"]["laptop"]["host"] == "laptop.example"
    assert "mirror" not in payload["targets"]["laptop"]
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


# ---- browsing a machine and opening a folder --------------------------


def test_browsing_lists_the_folders_inside_one(tmp_path, monkeypatch):
    asked = []

    def fake_children(spec, folder):
        asked.append((spec.name, folder))

        return mirror.Listing(home="/home/dev/work", folders=("a", "b"))

    monkeypatch.setattr(mirror, "list_children", fake_children)

    payload = api.browse_payload(
        Harness(tmp_path).runtime, {"target": "laptop", "path": "~/work"},
    )

    assert asked == [("laptop", "~/work")]
    assert payload["path"] == "/home/dev/work"
    assert payload["parent"] == "/home/dev"
    assert payload["folders"] == ["a", "b"]
    assert payload["more"] is False


def test_browsing_starts_at_home(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(
        mirror,
        "list_children",
        lambda spec, folder: asked.append(folder)
        or mirror.Listing(home="/home/dev", folders=()),
    )

    api.browse_payload(Harness(tmp_path).runtime, {"target": "laptop"})

    assert asked == ["~"]


@pytest.mark.parametrize("folder, parent", [
    ("/home/dev", "/home"),
    ("/home", "/"),
    ("/", ""),
])
def test_the_way_up_stops_at_the_root(folder, parent):
    assert api.parent_of(folder) == parent


def test_this_machine_is_not_browsed(tmp_path):
    payload = api.browse_payload(Harness(tmp_path).runtime, {"target": "mac"})

    assert payload["ok"] is False
    assert "is this machine" in payload["error"]


def test_a_machine_that_is_down_says_so(tmp_path, monkeypatch):
    def refuse(spec, folder):
        raise mirror.MirrorError("Connection refused")

    monkeypatch.setattr(mirror, "list_children", refuse)

    payload = api.browse_payload(Harness(tmp_path).runtime, {"target": "laptop"})

    assert payload == api.failure("Connection refused")


def test_opening_a_folder_gives_the_host_a_place_for_it(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mirror,
        "run_remote",
        lambda spec, script, timeout: b"/home/dev/work/site\0",
    )

    payload = api.open_payload(
        Harness(tmp_path).runtime, {"target": "laptop", "path": "~/work/site"},
    )

    place = tmp_path / "mirror" / "laptop" / "home/dev/work/site"

    assert payload["ok"] is True
    assert payload["cwd"] == str(place)
    assert place.is_dir()

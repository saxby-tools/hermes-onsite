"""What the desktop half is told about a session."""

from __future__ import annotations

import pytest

from onsite import api
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


@pytest.fixture(autouse=True)
def mount_asks(monkeypatch):
    """Stand in for the mount. A list of machines asks for each one's
    disk, and a test must never start a real mount to answer."""

    asked = []

    def fake_ensure(spec, mount_root, data_dir, system=None):
        asked.append(spec.name)

        return mounts.Mounted(mount_root, "/home/dev")

    monkeypatch.setattr(mounts, "ensure", fake_ensure)

    return asked


def build(tmp_path, settings=SETTINGS, laptop_up=True):
    session = runtime.SessionReader(cwd=lambda: "", session_id=lambda: "")

    built = runtime.Runtime(
        load_settings=lambda: settings,
        data_dir=tmp_path,
        session=session,
        build_environment=lambda *args: object(),
    )

    if settings is not SETTINGS:
        return built

    spec = built.load_config().target("laptop")
    detail = "" if laptop_up else "connection refused"

    built._gates[
        (spec.name, spec.kind, spec.host, spec.user, spec.port)
    ] = health.Gate(
        spec,
        probe=lambda target: health.ProbeResult(laptop_up, detail, 12),
    )

    return built


def test_status_routes_by_the_folder_the_app_sends(tmp_path):
    payload = api.status_payload(build(tmp_path), {"cwd": "/home/dev/app"})

    assert payload["ok"] is True
    assert payload["target"] == "laptop"
    assert payload["connected"] is True
    assert payload["api_version"] == api.API_VERSION


def test_status_reports_a_dead_target(tmp_path):
    payload = api.status_payload(
        build(tmp_path, laptop_up=False),
        {"cwd": "/home/dev/app"},
    )

    assert payload["connected"] is False
    assert payload["detail"] == "connection refused"


def test_status_lists_every_target_when_asked(tmp_path):
    payload = api.status_payload(
        build(tmp_path),
        {"cwd": "/tmp"},
        with_targets=True,
    )

    assert [row["name"] for row in payload["rows"]] == ["laptop", "mac"]
    assert payload["rows"][1]["default"] is True


def test_a_session_under_a_mount_gets_its_mount_back(tmp_path, mount_asks):
    folder = mounts.default_root(tmp_path, "laptop") / "home" / "dev" / "app"
    payload = api.status_payload(
        build(tmp_path),
        {"cwd": str(folder), "rows": False},
        with_targets=False,
    )

    assert payload["reason"] == "mount"
    assert payload["cwd"] == "/home/dev/app"
    assert payload["mount_error"] == ""
    assert mount_asks == ["laptop"]


def test_a_mount_that_fails_is_named_in_the_status(tmp_path, monkeypatch):
    def refuse(spec, mount_root, data_dir, system=None):
        raise mounts.MountError("this server has no `sshfs`")

    monkeypatch.setattr(mounts, "ensure", refuse)

    payload = api.status_payload(
        build(tmp_path),
        {"cwd": str(mounts.default_root(tmp_path, "laptop") / "srv")},
        with_targets=False,
    )

    assert payload["ok"] is True
    assert payload["mount_error"] == "this server has no `sshfs`"


def test_a_started_session_under_a_mount_gets_it_back_too(
    tmp_path,
    mount_asks,
):
    """After its first message a session is routed by where it
    started. Its folder is still on the mount."""

    built = build(tmp_path)
    folder = mounts.default_root(tmp_path, "laptop") / "home" / "dev" / "app"

    built.pins.set(
        "session:key-1",
        "laptop",
        runtime.SOURCE_ROUTE,
        "/home/dev/app",
        "id-1",
    )

    payload = api.status_payload(
        built,
        {"cwd": str(folder), "session_ids": ["id-1"]},
        with_targets=False,
    )

    assert payload["reason"] != "mount"
    assert payload["target"] == "laptop"
    assert mount_asks == ["laptop"]


def test_a_target_that_is_away_is_not_asked_to_mount(tmp_path, mount_asks):
    folder = mounts.default_root(tmp_path, "laptop") / "home" / "dev" / "app"
    payload = api.status_payload(
        build(tmp_path, laptop_up=False),
        {"cwd": str(folder), "rows": True},
        with_targets=True,
    )

    assert payload["connected"] is False
    assert payload["mount_error"] == ""
    assert payload["rows"][0]["mount_error"] == ""
    assert mount_asks == []


def test_a_fault_of_set_up_is_still_said_while_the_target_is_away(
    tmp_path,
    monkeypatch,
):
    def refuse(spec, mount_root, data_dir, system=None):
        raise mounts.MountError("this server has no `sshfs`")

    monkeypatch.setattr(mounts, "ensure", refuse)

    built = build(tmp_path, laptop_up=False)
    spec = built.load_config().target("laptop")

    with pytest.raises(mounts.MountError):
        built.mount(spec)

    payload = api.status_payload(
        built,
        {"cwd": f"{spec.mount}/home/dev", "rows": True},
        with_targets=True,
    )

    assert payload["mount_error"] == "this server has no `sshfs`"
    assert payload["rows"][0]["mount_error"] == "this server has no `sshfs`"


def saving_runtime(tmp_path, monkeypatch):
    """A runtime whose settings can be saved, and the mounts it was
    asked to take down."""

    store = {
        "default": "mac",
        "targets": {
            "laptop": {
                "kind": "ssh",
                "host": "laptop.example",
                "user": "dev",
                "mount": "/mnt/old/laptop",
            },
            "mac": {"kind": "local"},
        },
    }
    stopped = []
    session = runtime.SessionReader(cwd=lambda: "", session_id=lambda: "")

    built = runtime.Runtime(
        load_settings=lambda: store,
        data_dir=tmp_path,
        session=session,
        build_environment=lambda *args: object(),
        save_settings=lambda new: store.update(new),
    )

    monkeypatch.setattr(
        mounts,
        "stop",
        lambda spec, root, data_dir, system=None: stopped.append(str(root)),
    )

    return built, store, stopped


def test_a_new_mount_place_takes_the_old_mount_down(tmp_path, monkeypatch):
    built, store, stopped = saving_runtime(tmp_path, monkeypatch)
    target = {**store["targets"]["laptop"], "mount": "/mnt/new/laptop"}

    answer = api.save_target_payload(built, {"name": "laptop", "target": target})

    assert answer["ok"] is True
    assert stopped == ["/mnt/old/laptop"]


def test_a_save_that_keeps_the_mount_place_leaves_the_mount_up(
    tmp_path,
    monkeypatch,
):
    built, store, stopped = saving_runtime(tmp_path, monkeypatch)
    target = {**store["targets"]["laptop"], "user": "other"}

    api.save_target_payload(built, {"name": "laptop", "target": target})

    assert stopped == []


def test_removing_a_machine_takes_its_mount_down(tmp_path, monkeypatch):
    built, store, stopped = saving_runtime(tmp_path, monkeypatch)

    answer = api.remove_target_payload(built, {"name": "laptop"})

    assert answer["ok"] is True
    assert stopped == ["/mnt/old/laptop"]


def test_a_folder_beside_a_mount_is_not_on_it(tmp_path):
    built = build(tmp_path)
    spec = built.load_config().target("laptop")

    assert api.under_mount(spec.mount, spec) is True
    assert api.under_mount(f"{spec.mount}/home/dev", spec) is True
    assert api.under_mount(f"{spec.mount}-old/home", spec) is False
    assert api.under_mount("", spec) is False


def test_a_session_elsewhere_mounts_nothing(tmp_path, mount_asks):
    payload = api.status_payload(
        build(tmp_path),
        {"cwd": "/home/dev/app"},
        with_targets=False,
    )

    assert payload["reason"] == "prefix"
    assert payload["mount_error"] == ""
    assert mount_asks == []


def count_probes(built):
    """Count the probes of the laptop from here on."""

    seen = []
    spec = built.load_config().target("laptop")

    built._gates[
        (spec.name, spec.kind, spec.host, spec.user, spec.port)
    ] = health.Gate(
        spec,
        probe=lambda target: (
            seen.append(target.name)
            or health.ProbeResult(True, "", 12)
        ),
    )

    return seen


def test_listing_the_targets_does_not_probe_the_session_target_twice(
    tmp_path,
):
    built = build(tmp_path)
    seen = count_probes(built)

    payload = api.status_payload(
        built,
        {"cwd": "/home/dev/app"},
        with_targets=True,
    )

    assert payload["target"] == "laptop"
    assert payload["rows"][0]["connected"] is True
    assert payload["rows"][0]["latency_ms"] == 12
    assert seen == ["laptop"]


def test_a_status_without_rows_probes_only_the_session_target(tmp_path):
    built = build(tmp_path)
    seen = count_probes(built)

    payload = api.status_payload(built, {"cwd": "/tmp"}, with_targets=False)

    assert payload["target"] == "mac"
    assert "rows" not in payload
    assert seen == []


def test_status_says_whether_run_can_reach_the_agent(tmp_path):
    session = runtime.SessionReader(cwd=lambda: "", session_id=lambda: "")
    answers = [False, True]

    built = runtime.Runtime(
        load_settings=lambda: {"default": "mac", "targets": {
            "mac": {"kind": "local"},
        }},
        data_dir=tmp_path,
        session=session,
        build_environment=lambda *args: object(),
        run_allowed=lambda: answers.pop(0),
    )

    assert api.status_payload(built, {})["run_allowed"] is False
    assert api.status_payload(built, {})["run_allowed"] is True


def test_run_is_taken_as_allowed_when_nobody_can_say(tmp_path):
    assert api.status_payload(build(tmp_path), {})["run_allowed"] is True


def test_no_target_is_named_or_probed_when_another_backend_is_selected(
    tmp_path,
):
    session = runtime.SessionReader(cwd=lambda: "", session_id=lambda: "")
    probes = []

    built = runtime.Runtime(
        load_settings=lambda: SETTINGS,
        data_dir=tmp_path,
        session=session,
        build_environment=lambda *args: object(),
        backend_selected=lambda: False,
    )

    spec = built.load_config().target("laptop")

    built._gates[
        (spec.name, spec.kind, spec.host, spec.user, spec.port)
    ] = health.Gate(
        spec,
        probe=lambda target: (
            probes.append(target.name)
            or health.ProbeResult(True, "", 12)
        ),
    )

    payload = api.status_payload(
        built,
        {"cwd": "/home/dev/app"},
        with_targets=True,
    )

    assert payload["ok"] is False
    assert "terminal.backend onsite" in payload["error"]
    assert "target" not in payload
    assert probes == []


def test_a_runtime_with_nobody_to_ask_counts_as_in_use(tmp_path):
    assert build(tmp_path).in_use() is True


def test_rows_are_listed_unless_the_caller_declines_them():
    assert api.wants_rows(None) is True
    assert api.wants_rows({}) is True
    assert api.wants_rows({"rows": True}) is True
    assert api.wants_rows({"rows": "no"}) is True
    assert api.wants_rows({"rows": False}) is False


def test_status_follows_an_override_found_by_any_session_id(tmp_path):
    built = build(tmp_path)
    built.switch("session:a", "mac", "/home/dev/app", "stored-id")

    payload = api.status_payload(built, {
        "cwd": "/home/dev/app",
        "session_ids": ["runtime-id", "stored-id"],
    })

    assert payload["target"] == "mac"
    assert payload["pinned"] is True


def test_status_survives_a_junk_body(tmp_path):
    for body in (None, [], "text", {"cwd": 7, "session_ids": "x"}):
        payload = api.status_payload(build(tmp_path), body)

        assert payload["ok"] is True
        assert payload["target"] == "mac"


def test_status_passes_broken_settings_through(tmp_path):
    payload = api.status_payload(build(tmp_path, settings=None), {})

    assert payload["ok"] is False
    assert "hermes onsite init" in payload["error"]


def test_proposals_are_found_by_any_session_id(tmp_path):
    built = build(tmp_path)
    built.proposals.add("session:a", "make test", "runs tests", ids=("id-1",))

    payload = api.proposals_payload(built, {"session_ids": ["nope", "id-1"]})

    assert payload["items"] == [{
        "number": 1,
        "command": "make test",
        "why": "runs tests",
        "status": "pending",
    }]


def test_no_session_means_no_proposals(tmp_path):
    assert api.proposals_payload(build(tmp_path), {})["items"] == []
    assert api.proposals_payload(build(tmp_path), None)["items"] == []

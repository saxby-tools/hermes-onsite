"""What the desktop half is told about a session."""

from __future__ import annotations

from onsite import api
from onsite import health
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

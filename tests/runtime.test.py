"""Pinning: a session keeps the target of its first tool call."""

from __future__ import annotations

import pytest

from onsite import config
from onsite import health
from onsite import routing
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
    def __init__(self, tmp_path, settings=SETTINGS):
        self.settings = settings
        self.session_cwd = ""
        self.session_id = "id-1"
        self.built: list[tuple] = []
        self.host_folders: list[str] = []

        session = runtime.SessionReader(
            cwd=lambda: self.session_cwd,
            session_id=lambda: self.session_id,
        )

        self.runtime = runtime.Runtime(
            load_settings=lambda: self.settings,
            data_dir=tmp_path,
            session=session,
            build_environment=self.build,
        )

    def build(self, spec, gate, cwd, timeout, host_cwd=""):
        self.built.append((spec.name, cwd, timeout))
        self.host_folders.append(host_cwd)

        return object()


def test_the_session_folder_decides_not_the_cwd_argument(tmp_path):
    harness = Harness(tmp_path)
    harness.session_cwd = "/home/dev/app"

    harness.runtime.create_environment("~", 180, "session:a")

    assert harness.built == [("laptop", "/home/dev/app", 180)]


def test_the_cwd_argument_is_used_when_there_is_no_session(tmp_path):
    harness = Harness(tmp_path)

    harness.runtime.create_environment("/home/dev/app", 180, "default")

    assert harness.built[0][0] == "laptop"


def test_a_session_keeps_its_first_target(tmp_path):
    harness = Harness(tmp_path)
    harness.session_cwd = "/home/dev/app"
    harness.runtime.create_environment("~", 180, "session:a")

    # Hermes rebuilt the environment after the session moved folders.
    harness.session_cwd = "/Users/dev/Documents"
    harness.runtime.create_environment("~", 180, "session:a")

    assert [item[0] for item in harness.built] == ["laptop", "laptop"]
    assert harness.built[1][1] == routing.HOME


def test_sessionless_work_is_routed_fresh_each_time(tmp_path):
    harness = Harness(tmp_path)

    harness.runtime.create_environment("/home/dev/app", 180, "default")
    harness.runtime.create_environment("/srv/job", 180, "default")

    assert [item[0] for item in harness.built] == ["laptop", "mac"]


def test_an_override_wins_and_can_be_cleared(tmp_path):
    harness = Harness(tmp_path)
    harness.session_cwd = "/home/dev/app"

    route = harness.runtime.switch("session:a", "mac", "/home/dev/app")

    assert route.target == "mac"
    assert route.reason == routing.REASON_OVERRIDE

    harness.runtime.create_environment("~", 180, "session:a")

    assert harness.built[-1][0] == "mac"

    route = harness.runtime.switch("session:a", "auto", "/home/dev/app")

    assert route.target == "laptop"

    harness.runtime.create_environment("~", 180, "session:a")

    assert harness.built[-1][0] == "laptop"


def test_a_switch_is_held_for_the_agent(tmp_path):
    harness = Harness(tmp_path)
    harness.session_cwd = "/home/dev/app"
    harness.runtime.create_environment("~", 180, "session:a")

    harness.runtime.switch("session:a", "mac", "/home/dev/app", "id-9")

    move = harness.runtime.moves.take("id-9")

    assert move.before == "laptop"
    assert move.after == "mac"
    assert move.cleared is False


def test_a_switch_before_the_first_tool_call_knows_the_old_machine(
    tmp_path,
):
    # Nothing is pinned yet, so the folder's own answer is the past.
    harness = Harness(tmp_path)

    harness.runtime.switch("session:a", "mac", "/home/dev/app")

    assert harness.runtime.moves.take("session:a").before == "laptop"


def test_clearing_an_override_is_held_for_the_agent_too(tmp_path):
    harness = Harness(tmp_path)

    harness.runtime.switch("session:a", "mac", "/home/dev/app")
    harness.runtime.moves.take("session:a")
    harness.runtime.switch("session:a", "auto", "/home/dev/app")

    move = harness.runtime.moves.take("session:a")

    assert move.before == "mac"
    assert move.after == "laptop"
    assert move.cleared is True


def test_a_switch_to_the_machine_in_use_tells_the_agent_nothing(tmp_path):
    harness = Harness(tmp_path)

    harness.runtime.switch("session:a", "laptop", "/home/dev/app")

    assert harness.runtime.moves.take("session:a") is None


def test_a_refused_switch_tells_the_agent_nothing(tmp_path):
    harness = Harness(tmp_path)

    with pytest.raises(runtime.UnknownTarget):
        harness.runtime.switch("session:a", "desktop", "/home/dev/app")

    assert harness.runtime.moves.take("session:a") is None


def test_an_unknown_target_is_refused_with_the_choices(tmp_path):
    harness = Harness(tmp_path)

    with pytest.raises(runtime.UnknownTarget) as caught:
        harness.runtime.switch("session:a", "desktop", "/home/dev")

    assert "laptop, mac" in str(caught.value)


def test_broken_settings_stop_environment_creation(tmp_path):
    harness = Harness(tmp_path, settings=None)

    assert "hermes onsite init" in harness.runtime.config_problem()

    with pytest.raises(config.ConfigError):
        harness.runtime.create_environment("~", 180, "session:a")


def test_a_settings_edit_applies_with_no_restart(tmp_path):
    harness = Harness(tmp_path, settings=None)

    assert harness.runtime.config_problem() is not None

    harness.settings = SETTINGS

    assert harness.runtime.config_problem() is None


def test_status_comes_from_a_live_probe_every_time(tmp_path):
    harness = Harness(tmp_path)
    calls = []

    def probe(spec):
        calls.append(spec.name)

        return health.ProbeResult(len(calls) > 1, "refused", 7)

    spec = harness.runtime.load_config().target("laptop")
    harness.runtime._gates[
        (spec.name, spec.kind, spec.host, spec.user, spec.port)
    ] = health.Gate(spec, probe=probe)

    first = harness.runtime.status(cwd="/home/dev/app")
    second = harness.runtime.status(cwd="/home/dev/app")

    assert first["connected"] is False
    assert first["detail"] == "refused"
    assert second["connected"] is True
    assert calls == ["laptop", "laptop"]


def test_status_finds_a_session_by_its_id(tmp_path):
    harness = Harness(tmp_path)
    harness.runtime.switch("session:a", "mac", "/home/dev/app", "id-9")

    status = harness.runtime.status(session_id="id-9")

    assert status["target"] == "mac"
    assert status["pinned"] is True
    assert status["reason"] == routing.REASON_OVERRIDE


def test_status_uses_the_folder_noted_when_the_session_started(tmp_path):
    harness = Harness(tmp_path)
    harness.session_cwd = "/home/dev/app"
    harness.runtime.create_environment("~", 180, "session:a")

    # A slash command has no folder to pass.
    status = harness.runtime.status(key="session:a")

    assert status["target"] == "laptop"
    assert status["cwd"] == "/home/dev/app"
    assert status["folder_known"] is True
    assert harness.host_folders == ["/home/dev/app"]


def test_status_says_when_the_folder_is_not_known(tmp_path):
    harness = Harness(tmp_path)

    assert harness.runtime.status(key="session:new")["folder_known"] is False


def test_status_reports_broken_settings_instead_of_raising(tmp_path):
    harness = Harness(tmp_path, settings={"default": "mac"})

    status = harness.runtime.status()

    assert status["ok"] is False
    assert "targets" in status["error"]


def test_a_changed_host_gets_its_own_breaker(tmp_path):
    harness = Harness(tmp_path)
    old = harness.runtime.load_config().target("laptop")
    first = harness.runtime.gate_for(old)

    moved = config.TargetSpec(
        name="laptop",
        kind=config.KIND_SSH,
        host="other.example",
        user="dev",
    )

    assert harness.runtime.gate_for(moved) is not first
    assert harness.runtime.gate_for(old) is first

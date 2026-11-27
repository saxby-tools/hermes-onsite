"""A dead target fails fast, stays failed, then recovers by itself."""

from __future__ import annotations

import dataclasses
import subprocess

from pathlib import Path

import pytest

from onsite import config
from onsite import health
from onsite import mounts
from onsite import remote
from onsite import runtime


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProbe:
    def __init__(self, ok: bool):
        self.ok = ok
        self.calls = 0

    def __call__(self, spec) -> health.ProbeResult:
        self.calls += 1

        detail = "" if self.ok else "connection refused"

        return health.ProbeResult(self.ok, detail, 1)


SPEC = config.TargetSpec(
    name="laptop",
    kind=config.KIND_SSH,
    host="laptop.example",
    user="dev",
)


def build_gate(probe: FakeProbe, clock: FakeClock) -> health.Gate:
    breaker = health.CircuitBreaker(cooldown=10.0, clock=clock)

    return health.Gate(SPEC, probe=probe, breaker=breaker)


def test_a_closed_breaker_lets_calls_through_without_a_probe():
    probe = FakeProbe(ok=True)
    gate = build_gate(probe, FakeClock())

    gate.admit()

    assert probe.calls == 0


def test_one_failure_opens_the_breaker():
    clock = FakeClock()
    gate = build_gate(FakeProbe(ok=False), clock)

    gate.trip("connection lost")

    assert gate.breaker.state() == health.STATE_OPEN


def test_an_open_breaker_refuses_at_once_and_names_the_target():
    clock = FakeClock()
    probe = FakeProbe(ok=False)
    gate = build_gate(probe, clock)

    gate.trip("connection refused")

    with pytest.raises(health.TargetDown) as caught:
        gate.admit()

    message = str(caught.value)

    assert "laptop (dev@laptop.example)" in message
    assert "connection refused" in message
    assert "No command was run" in message

    # Refusing must not cost a probe. That is the point of a breaker.
    assert probe.calls == 0


def test_after_the_cooldown_one_probe_decides():
    clock = FakeClock()
    probe = FakeProbe(ok=False)
    gate = build_gate(probe, clock)

    gate.trip("down")
    clock.advance(10.0)

    assert gate.breaker.state() == health.STATE_HALF_OPEN

    with pytest.raises(health.TargetDown):
        gate.admit()

    assert probe.calls == 1

    # The failed probe restarted the cooldown.
    assert gate.breaker.state() == health.STATE_OPEN


def test_service_resumes_with_no_restart_once_the_target_is_back():
    clock = FakeClock()
    probe = FakeProbe(ok=False)
    gate = build_gate(probe, clock)

    gate.trip("down")
    clock.advance(10.0)
    probe.ok = True

    gate.admit()

    assert gate.breaker.state() == health.STATE_CLOSED

    gate.admit()

    assert probe.calls == 1


def test_a_fresh_admit_probes_even_when_closed():
    probe = FakeProbe(ok=False)
    gate = build_gate(probe, FakeClock())

    with pytest.raises(health.TargetDown):
        gate.admit_fresh()

    assert probe.calls == 1
    assert gate.breaker.state() == health.STATE_OPEN


def test_the_local_target_is_always_up():
    local = config.TargetSpec(name="mac", kind=config.KIND_LOCAL)

    assert health.probe_target(local).ok is True


def test_the_probe_never_reuses_a_shared_connection():
    command = health.build_probe_command(SPEC)

    assert "ControlPath=none" in command
    assert "BatchMode=yes" in command
    assert command[-2:] == ["dev@laptop.example", "true"]


def test_the_probe_carries_port_and_key_when_set():
    spec = config.TargetSpec(
        name="box",
        kind=config.KIND_SSH,
        host="h",
        user="u",
        port=2222,
        key="/keys/box",
        known_hosts="/keys/known",
    )

    command = health.build_probe_command(spec)

    assert command[command.index("-p") + 1] == "2222"
    assert command[command.index("-i") + 1] == "/keys/box"
    assert "UserKnownHostsFile=/keys/known" in command


def test_a_probe_of_a_closed_port_fails_fast():
    spec = config.TargetSpec(
        name="nobody",
        kind=config.KIND_SSH,
        host="127.0.0.1",
        user="nobody",
        port=1,
        connect_timeout=1,
    )

    result = health.probe_ssh(spec)

    assert result.ok is False
    assert result.elapsed_ms < 5000


# ---- a computer that dials in ------------------------------------------


DIAL_IN = config.TargetSpec(
    name="laptop",
    kind=config.KIND_SSH,
    host="127.0.0.1",
    user="dev",
    port=22022,
    dial_in="you@server.example",
)

REFUSED = "ssh: connect to host 127.0.0.1 port 22022: Connection refused"
NOT_CONNECTED = "it has not connected to this server, or its link dropped"


def ssh_says(monkeypatch, stderr: str) -> None:
    """Stand in for an `ssh` that failed with these words."""

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 255, "", stderr)

    monkeypatch.setattr(health.subprocess, "run", fake_run)


def test_a_dial_in_computer_with_no_link_is_not_called_refused(monkeypatch):
    ssh_says(monkeypatch, REFUSED)

    result = health.probe_target(DIAL_IN)

    assert result.ok is False
    assert NOT_CONNECTED in result.detail
    assert "Connection refused" not in result.detail


def test_a_target_the_server_dials_keeps_what_ssh_said(monkeypatch):
    ssh_says(monkeypatch, REFUSED)

    assert health.probe_target(SPEC).detail == REFUSED


def test_a_frozen_dial_in_computer_is_told_apart_from_no_link(monkeypatch):
    ssh_says(monkeypatch, "Connection timed out during banner exchange")

    detail = health.probe_target(DIAL_IN).detail

    assert "its link is up but it does not answer" in detail


def test_a_tool_call_is_refused_in_the_same_words(monkeypatch):
    ssh_says(monkeypatch, REFUSED)

    gate = health.Gate(DIAL_IN)

    with pytest.raises(health.TargetDown) as caught:
        gate.admit_fresh()

    assert "laptop (dev, dials in) is unreachable" in str(caught.value)
    assert NOT_CONNECTED in str(caught.value)
    assert "No command was run" in str(caught.value)


def test_a_link_lost_under_a_command_is_explained_too():
    gate = health.Gate(DIAL_IN, probe=FakeProbe(ok=True))

    gate.trip(REFUSED)

    assert NOT_CONNECTED in gate.breaker.last_detail


def test_a_port_that_changed_hands_fails_closed_on_every_command_line():
    # Any account on the server can listen on a port the computer let
    # go of. The key filed under the target's name is what tells the
    # computer from a stand-in, so every `ssh` the plugin starts must
    # ask for it, and none may switch the check off.
    wanted = "HostKeyAlias=onsite-laptop"
    whole_lines = [
        health.build_probe_command(DIAL_IN),
        remote.build_link_options(DIAL_IN),
        remote.build_sftp_arguments(DIAL_IN),
        remote.build_forward_arguments(DIAL_IN, 5900),
        remote.build_stdio_arguments(DIAL_IN, "true"),
    ]

    # These go in front of the line Hermes' own SSH code builds for a
    # session's commands, which names the same check itself.
    session_options = remote.build_ssh_options(DIAL_IN)

    for line in whole_lines:
        assert "StrictHostKeyChecking=accept-new" in line

    for line in [*whole_lines, session_options]:
        said = " ".join(line)

        assert wanted in line
        assert "KeyChecking=no" not in said
        assert "/dev/null" not in said

    sshfs = mounts.build_sshfs_command(DIAL_IN, Path("/mnt/laptop"))

    assert wanted in sshfs[-1].split(",")
    assert "port=22022" in sshfs[-1].split(",")
    assert "StrictHostKeyChecking=accept-new" in sshfs[-1].split(",")


def test_every_link_to_a_dial_in_computer_goes_to_its_own_port():
    # Hermes' own SSH environment adds the port to the line the
    # session's commands use, from the `port` the target class hands
    # it. That line needs Hermes to build, so the rig's walk is what
    # covers it.
    lines = [
        health.build_probe_command(DIAL_IN),
        remote.build_link_options(DIAL_IN),
        remote.build_sftp_arguments(DIAL_IN),
        remote.build_forward_arguments(DIAL_IN, 5900),
        remote.build_stdio_arguments(DIAL_IN, "true"),
    ]

    for line in lines:
        assert line[line.index("-p") + 1] == "22022"


def test_a_gate_probes_the_target_as_it_is_set_up_now(tmp_path):
    # A key file set after the gate was made has to reach its probe,
    # or a page that polls the gate never sees the fix.
    session = runtime.SessionReader(cwd=lambda: "", session_id=lambda: "")
    built = runtime.Runtime(
        load_settings=lambda: {},
        data_dir=tmp_path,
        session=session,
        build_environment=lambda *args: object(),
    )

    first = built.gate_for(DIAL_IN)
    again = built.gate_for(dataclasses.replace(DIAL_IN, key="/keys/new"))

    assert again is first
    assert again.spec.key == "/keys/new"

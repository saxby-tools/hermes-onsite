"""A dead target fails fast, stays failed, then recovers by itself."""

from __future__ import annotations

import pytest

from hermes_hands import config
from hermes_hands import health


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

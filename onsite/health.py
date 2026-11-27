"""Health probe and circuit breaker for a target.

A dead target must fail fast and say so. Without a breaker, every tool
call would wait out a full SSH timeout before the model learned the
laptop was asleep. With one, the first failure opens the breaker, the
next calls fail at once, and a later call probes again so service
resumes with no restart.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time

from dataclasses import dataclass
from typing import Callable

from . import dialin
from .config import DEFAULT_SSH_PORT
from .config import KIND_LOCAL
from .config import TargetSpec


logger = logging.getLogger(__name__)

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"

# One transport failure is enough. A second attempt at a sleeping
# laptop teaches nothing and costs the model another timeout.
FAILURE_THRESHOLD = 1
COOLDOWN_SECONDS = 10.0

# The probe has to answer well inside five seconds, so the subprocess
# gets one second more than the SSH connect timeout and no more.
PROBE_GRACE_SECONDS = 1.0
PROBE_COMMAND = "true"

SSH_BINARY = "ssh"

DECISION_PASS = "pass"
DECISION_PROBE = "probe"
DECISION_REFUSE = "refuse"


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    detail: str
    elapsed_ms: int


class TargetDown(RuntimeError):
    """A target cannot take commands right now. Nothing was run."""

    def __init__(self, label: str, detail: str, retry_in: float):
        self.label = label
        self.detail = detail
        self.retry_in = max(0.0, retry_in)

        super().__init__(describe_down(label, detail, self.retry_in))


def describe_down(label: str, detail: str, retry_in: float) -> str:
    wait = int(round(retry_in))
    reason = detail or "no answer"

    return (
        f"onsite target {label} is unreachable: {reason}. "
        "No command was run and nothing was retried. "
        f"The target is checked again in about {wait}s."
    )


class CircuitBreaker:
    """Closed lets calls through. Open refuses them until a cooldown.

    After the cooldown one call is allowed to probe. That is the
    half-open state. The clock is passed in so tests need no sleeps.
    """

    def __init__(
        self,
        cooldown: float = COOLDOWN_SECONDS,
        threshold: int = FAILURE_THRESHOLD,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._cooldown = cooldown
        self._threshold = threshold
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._last_detail = ""

    @property
    def last_detail(self) -> str:
        return self._last_detail

    def retry_in(self) -> float:
        with self._lock:
            if self._opened_at is None:
                return 0.0

            remaining = self._cooldown - (self._clock() - self._opened_at)

            return max(0.0, remaining)

    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return STATE_CLOSED

            if self._clock() - self._opened_at >= self._cooldown:
                return STATE_HALF_OPEN

            return STATE_OPEN

    def decide(self) -> str:
        state = self.state()

        if state == STATE_CLOSED:
            return DECISION_PASS

        if state == STATE_HALF_OPEN:
            return DECISION_PROBE

        return DECISION_REFUSE

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._last_detail = ""

    def record_failure(self, detail: str = "") -> None:
        with self._lock:
            self._failures += 1
            self._last_detail = detail

            if self._failures < self._threshold:
                return

            # A failed probe restarts the cooldown from now.
            self._opened_at = self._clock()


def build_oneshot_command(spec: TargetSpec, remote: str) -> list[str]:
    """One command on a target, over a connection of its own."""

    command = [
        SSH_BINARY,
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={spec.connect_timeout}",
        # A probe that reuses a stale master would report a dead host
        # as alive, so it always opens its own connection.
        "-o", "ControlPath=none",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "LogLevel=ERROR",
    ]

    if spec.port != DEFAULT_SSH_PORT:
        command += ["-p", str(spec.port)]

    if spec.key:
        command += ["-i", spec.key]

    if spec.known_hosts:
        command += ["-o", f"UserKnownHostsFile={spec.known_hosts}"]

    if spec.host_key_alias:
        command += ["-o", f"HostKeyAlias={spec.host_key_alias}"]

    command += [f"{spec.user}@{spec.host}", remote]

    return command


def build_probe_command(spec: TargetSpec) -> list[str]:
    return build_oneshot_command(spec, PROBE_COMMAND)


def last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if not lines:
        return ""

    return lines[-1]


def probe_ssh(spec: TargetSpec) -> ProbeResult:
    started = time.monotonic()
    limit = spec.connect_timeout + PROBE_GRACE_SECONDS

    try:
        done = subprocess.run(
            build_probe_command(spec),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=limit,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        elapsed = int((time.monotonic() - started) * 1000)

        return ProbeResult(False, f"no answer in {limit:.0f}s", elapsed)
    except OSError as error:
        elapsed = int((time.monotonic() - started) * 1000)

        return ProbeResult(False, f"cannot run ssh: {error}", elapsed)

    elapsed = int((time.monotonic() - started) * 1000)

    if done.returncode == 0:
        return ProbeResult(True, "", elapsed)

    detail = last_line(done.stderr) or f"ssh exited {done.returncode}"

    return ProbeResult(False, detail, elapsed)


def describe_failure(spec: TargetSpec, detail: str) -> str:
    """Why a target cannot be reached, in words that fit the target.

    A computer that dials in is reached at a port on the host itself.
    What `ssh` says about that port, such as "Connection refused",
    would send a person looking in the wrong place.
    """

    if not spec.dial_in:
        return detail

    plain = dialin.explain(spec.name, spec.port, detail)

    # What `ssh` itself said is kept where a person who is stuck can
    # find it, in case the plain words read it wrong.
    if plain != detail:
        logger.debug("onsite: %s: ssh said: %s", spec.label, detail)

    return plain


def probe_target(spec: TargetSpec) -> ProbeResult:
    # The local target is the machine this code runs on. If this line
    # runs, it is up.
    if spec.kind == KIND_LOCAL:
        return ProbeResult(True, "", 0)

    result = probe_ssh(spec)

    if result.ok:
        return result

    return ProbeResult(
        False,
        describe_failure(spec, result.detail),
        result.elapsed_ms,
    )


class Gate:
    """One target's breaker plus the probe that feeds it."""

    def __init__(
        self,
        spec: TargetSpec,
        probe: Callable[[TargetSpec], ProbeResult] = probe_target,
        breaker: CircuitBreaker | None = None,
    ):
        self.spec = spec
        self._probe = probe
        self.breaker = breaker or CircuitBreaker()

    def refuse(self) -> TargetDown:
        return TargetDown(
            self.spec.label,
            self.breaker.last_detail,
            self.breaker.retry_in(),
        )

    def check(self) -> ProbeResult:
        """Probe now and feed the result to the breaker."""

        result = self._probe(self.spec)

        if result.ok:
            self.breaker.record_success()
            return result

        self.breaker.record_failure(result.detail)

        return result

    def admit(self) -> None:
        """Let a call through, or raise TargetDown without running it."""

        decision = self.breaker.decide()

        if decision == DECISION_PASS:
            return

        if decision == DECISION_REFUSE:
            raise self.refuse()

        if self.check().ok:
            return

        raise self.refuse()

    def admit_fresh(self) -> None:
        """Like `admit`, but a closed breaker is probed as well.

        Used before a new connection is built, where a live probe is
        cheaper than waiting for the connection itself to time out.
        """

        if self.breaker.decide() == DECISION_REFUSE:
            raise self.refuse()

        if self.check().ok:
            return

        raise self.refuse()

    def trip(self, detail: str) -> None:
        self.breaker.record_failure(describe_failure(self.spec, detail))

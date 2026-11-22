"""Remember which target each session is pinned to.

A session picks its target at its first tool call and keeps it. Only
`/hands <target>` changes it. Hermes rebuilds an idle environment
after a few minutes, so the pick has to outlive the environment, and
it has to be visible to the server process that draws the status chip.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import time

from dataclasses import dataclass
from typing import Any
from typing import Callable

from .store import JsonStore


SOURCE_ROUTE = "route"
SOURCE_OVERRIDE = "override"

# Hermes keys a session's environment as `session:<key>`. Any other
# key is shared by sessionless work, such as cron, and a pin on it
# would leak from one job into the next. Those stay in memory only.
SESSION_MARK = "session:"

MAX_AGE_SECONDS = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class Pin:
    target: str
    source: str
    cwd: str
    session_id: str
    at: float

    @property
    def is_override(self) -> bool:
        return self.source == SOURCE_OVERRIDE


def is_session_key(key: str) -> bool:
    return SESSION_MARK in key


def pin_from_record(record: Any) -> Pin | None:
    if not isinstance(record, dict):
        return None

    target = record.get("target")

    if not isinstance(target, str):
        return None

    return Pin(
        target=target,
        source=str(record.get("source") or SOURCE_ROUTE),
        cwd=str(record.get("cwd") or ""),
        session_id=str(record.get("session_id") or ""),
        at=float(record.get("at") or 0.0),
    )


def pin_to_record(pin: Pin) -> dict[str, Any]:
    return {
        "target": pin.target,
        "source": pin.source,
        "cwd": pin.cwd,
        "session_id": pin.session_id,
        "at": pin.at,
    }


def drop_old(data: dict[str, Any], now: float) -> None:
    for key in list(data):
        pin = pin_from_record(data[key])

        if pin is None:
            del data[key]
            continue

        if now - pin.at > MAX_AGE_SECONDS:
            del data[key]


class Pins:
    def __init__(
        self,
        store: JsonStore,
        clock: Callable[[], float] = time.time,
    ):
        self._store = store
        self._clock = clock
        self._sessionless: dict[str, Pin] = {}

    def get(self, key: str) -> Pin | None:
        if not key:
            return None

        if not is_session_key(key):
            return self._sessionless.get(key)

        return pin_from_record(self._store.read().get(key))

    def find(self, session_id: str) -> Pin | None:
        """Look a pin up by session id, for callers with no session key.

        The desktop app knows a session by its id, while Hermes keys
        the environment by a route key. A pin records both.
        """

        if not session_id:
            return None

        for key, record in self._store.read().items():
            pin = pin_from_record(record)

            if pin is None:
                continue

            if pin.session_id == session_id:
                return pin

            if key.endswith(session_id):
                return pin

        return None

    def set(
        self,
        key: str,
        target: str,
        source: str,
        cwd: str = "",
        session_id: str = "",
    ) -> Pin | None:
        if not key:
            return None

        pin = Pin(target, source, cwd, session_id, self._clock())

        if not is_session_key(key):
            self._sessionless[key] = pin
            return pin

        def change(data: dict[str, Any]) -> None:
            drop_old(data, pin.at)
            data[key] = pin_to_record(pin)

        self._store.update(change)

        return pin

    def clear(self, key: str) -> bool:
        if not key:
            return False

        if not is_session_key(key):
            return self._sessionless.pop(key, None) is not None

        return bool(self._store.update(
            lambda data: data.pop(key, None) is not None
        ))

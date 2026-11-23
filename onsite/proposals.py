"""Keep the commands an agent proposed for the user to run.

`propose_command` adds a numbered entry. `/run N` and the desktop
pane read it back. Numbers count up per session and are never reused,
so `/run 2` always means the same command.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import time

from dataclasses import dataclass
from typing import Any
from typing import Callable

from .store import JsonStore


STATUS_PENDING = "pending"
STATUS_SENT = "sent"

DEFAULT_BUCKET = "default"

MAX_PER_SESSION = 50
MAX_COMMAND_CHARS = 4000
MAX_WHY_CHARS = 400
MAX_AGE_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class Proposal:
    number: int
    command: str
    why: str
    status: str
    at: float


def proposal_from_record(record: Any) -> Proposal | None:
    if not isinstance(record, dict):
        return None

    try:
        number = int(record["number"])
        command = str(record["command"])
    except (KeyError, TypeError, ValueError):
        return None

    return Proposal(
        number=number,
        command=command,
        why=str(record.get("why") or ""),
        status=str(record.get("status") or STATUS_PENDING),
        at=float(record.get("at") or 0.0),
    )


def proposal_to_record(proposal: Proposal) -> dict[str, Any]:
    return {
        "number": proposal.number,
        "command": proposal.command,
        "why": proposal.why,
        "status": proposal.status,
        "at": proposal.at,
    }


def bucket_matches(key: str, bucket: Any, wanted: str) -> bool:
    if key == wanted:
        return True

    if not isinstance(bucket, dict):
        return False

    return wanted in (bucket.get("ids") or [])


def find_bucket(data: dict[str, Any], wanted: str) -> dict[str, Any] | None:
    for key, bucket in data.items():
        if bucket_matches(key, bucket, wanted):
            return bucket

    return None


def drop_old(data: dict[str, Any], now: float) -> None:
    for key in list(data):
        bucket = data[key]

        if not isinstance(bucket, dict):
            del data[key]
            continue

        if now - float(bucket.get("at") or 0.0) > MAX_AGE_SECONDS:
            del data[key]


def clean_ids(ids: tuple[str, ...]) -> list[str]:
    return sorted({item for item in ids if item})


class Proposals:
    def __init__(
        self,
        store: JsonStore,
        clock: Callable[[], float] = time.time,
    ):
        self._store = store
        self._clock = clock

    def add(
        self,
        session: str,
        command: str,
        why: str = "",
        ids: tuple[str, ...] = (),
    ) -> Proposal:
        """Store a command and return it with its number.

        `ids` are other names for the same session, such as its id,
        so a caller that knows only one of them still finds the list.
        """

        key = session or DEFAULT_BUCKET
        now = self._clock()

        def change(data: dict[str, Any]) -> Proposal:
            drop_old(data, now)

            bucket = find_bucket(data, key)

            if bucket is None:
                bucket = {"next": 1, "items": [], "ids": []}
                data[key] = bucket

            proposal = Proposal(
                number=int(bucket.get("next") or 1),
                command=command[:MAX_COMMAND_CHARS],
                why=why[:MAX_WHY_CHARS],
                status=STATUS_PENDING,
                at=now,
            )

            items = list(bucket.get("items") or [])
            items.append(proposal_to_record(proposal))

            bucket["items"] = items[-MAX_PER_SESSION:]
            bucket["next"] = proposal.number + 1
            bucket["at"] = now
            bucket["ids"] = clean_ids((*bucket.get("ids", ()), *ids))

            return proposal

        return self._store.update(change)

    def list(self, session: str) -> list[Proposal]:
        bucket = find_bucket(self._store.read(), session or DEFAULT_BUCKET)

        if bucket is None:
            return []

        found = (
            proposal_from_record(record)
            for record in bucket.get("items") or []
        )

        return [proposal for proposal in found if proposal is not None]

    def get(self, session: str, number: int) -> Proposal | None:
        for proposal in self.list(session):
            if proposal.number == number:
                return proposal

        return None

    def mark(self, session: str, number: int, status: str) -> bool:
        key = session or DEFAULT_BUCKET

        def change(data: dict[str, Any]) -> bool:
            bucket = find_bucket(data, key)

            if bucket is None:
                return False

            for record in bucket.get("items") or []:
                if record.get("number") != number:
                    continue

                record["status"] = status

                return True

            return False

        return bool(self._store.update(change))

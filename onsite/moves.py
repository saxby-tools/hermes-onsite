"""Remember that a session was moved, until its agent has been told.

`/onsite <target>` changes where the next tool call runs. The note in
the agent's instructions was written when the session began, and
Hermes does not rebuild it. Left alone, the agent goes on describing
the old machine and passing paths that only exist there.

A move is held here until the session's next turn, which carries it to
the agent once. It is held in memory only: the slash command and the
turn run in the same process, and a note that outlived a restart
would describe a move the agent may already have seen.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import threading

from dataclasses import dataclass


# A session that is moved and never spoken to again would hold its
# note for good. Past this many, the oldest note is dropped.
MAX_PENDING = 256


@dataclass(frozen=True)
class Move:
    before: str
    after: str
    cwd: str
    cleared: bool = False


class Moves:
    """Pending moves, each filed under every id its session goes by.

    Hermes knows one session by several ids: a stored session id, a
    route key and an environment key. The slash command sees some of
    them and the turn sees others, so a move is filed under all it
    was given and found by any.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[str, Move] = {}

    def _find(self, ids: tuple[str, ...]) -> Move | None:
        for one in ids:
            if not one:
                continue

            move = self._pending.get(one)

            if move is not None:
                return move

        return None

    def _forget(self, move: Move) -> None:
        stale = [
            key
            for key, held in self._pending.items()
            if held is move
        ]

        for key in stale:
            del self._pending[key]

    def _trim(self) -> None:
        while len(self._pending) > MAX_PENDING:
            oldest = next(iter(self._pending))

            self._forget(self._pending[oldest])

    def note(self, move: Move, *ids: str) -> None:
        """File a move. A second move before the agent's next turn is
        folded into the first, so the agent hears about the trip as a
        whole and hears nothing if it ended where it began."""

        with self._lock:
            earlier = self._find(ids)

            if earlier is not None:
                self._forget(earlier)

                move = Move(
                    before=earlier.before,
                    after=move.after,
                    cwd=move.cwd,
                    cleared=move.cleared,
                )

            if move.before == move.after:
                return

            for one in ids:
                if one:
                    self._pending[one] = move

            self._trim()

    def take(self, *ids: str) -> Move | None:
        """Hand a pending move over once, and forget it."""

        with self._lock:
            move = self._find(ids)

            if move is None:
                return None

            self._forget(move)

            return move

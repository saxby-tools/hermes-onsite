"""A small JSON file store shared by every Hermes process.

More than one server process can load the plugin at once: a gateway,
a dashboard, and the server Hermes Desktop starts for itself. State
kept in memory would differ between them, so the little state there
is lives in files.

Only state goes here: which target a session is pinned to, and which
commands were proposed. Status, such as whether a target is up, is
never stored. It always comes from a live probe.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading

from pathlib import Path
from typing import Any
from typing import Callable


FILE_MODE = 0o600
DIR_MODE = 0o700


class JsonStore:
    """Read-modify-write of one JSON object, safe within a process.

    Two processes that write at the same instant can lose one write.
    That is accepted: the writes are rare and user-driven, and a file
    lock would add a failure mode for no real gain.
    """

    def __init__(self, path: Path | None):
        self._path = path
        self._lock = threading.Lock()
        self._memory: dict[str, Any] = {}

    def _load(self) -> dict[str, Any]:
        if self._path is None:
            return dict(self._memory)

        try:
            data = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}

        if not isinstance(data, dict):
            return {}

        return data

    def _save(self, data: dict[str, Any]) -> None:
        if self._path is None:
            self._memory = dict(data)
            return

        self._path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)

        # Write beside the target and rename, so a reader never sees
        # half a file.
        handle, name = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=self._path.name + ".",
        )

        try:
            with os.fdopen(handle, "w") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)

            os.chmod(name, FILE_MODE)
            os.replace(name, self._path)
        except OSError:
            if os.path.exists(name):
                os.unlink(name)

            raise

    def read(self) -> dict[str, Any]:
        with self._lock:
            return self._load()

    def update(self, change: Callable[[dict[str, Any]], Any]) -> Any:
        """Apply `change` to the stored object and save the result."""

        with self._lock:
            data = self._load()
            outcome = change(data)
            self._save(data)

            return outcome

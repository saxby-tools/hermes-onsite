"""A target's files as a file system the mount server can serve.

The SFTP client answers one question at a time over one link. A
mounted folder is asked far more than that: the host's kernel asks
about the same file many times a second, reads it in pieces, and
does not care that the answers come from another machine. This
layer stands between the two:

- It remembers what a file is for a moment, so the same question is
  not sent over the link twice in a row.
- It keeps a file open between reads and writes, so each piece does
  not cost an open and a close.
- It reconnects when the link drops, and does not hammer a machine
  that is asleep.
- It turns the file server's few status codes into errors a file
  system can report.

A target that cannot be reached is an input and output error on the
host. Nothing here waits for it to come back.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import stat as statmod
import threading
import time

from dataclasses import dataclass
from typing import Callable

from . import sftp
from .paths import join_target
from .paths import parent_target


# How long an answer about a file is trusted. Long enough to absorb
# the kernel's bursts, short enough that an edit made on the target
# shows up while a person is still looking.
ATTR_SECONDS = 2.0
MISSING_SECONDS = 1.0

# An open file is kept this long after its last use.
HANDLE_SECONDS = 5.0
MAX_HANDLES = 32

# After the link fails, nothing is sent for this long. A sleeping
# machine would otherwise cost every caller a full connect timeout.
RETRY_SECONDS = 5.0

DEFAULT_FILE_MODE = 0o644

# Why something failed, in the words a file system uses.
NOENT = "noent"
ACCES = "acces"
EXIST = "exist"
NOTEMPTY = "notempty"
NOTDIR = "notdir"
ISDIR = "isdir"
IO = "io"


class FsError(Exception):
    """A file operation failed. `why` is one of the names above."""

    def __init__(self, why: str, text: str = ""):
        super().__init__(text or why)
        self.why = why


@dataclass
class OpenFile:
    handle: bytes
    client: sftp.Client
    last_used: float


def explain(error: sftp.SftpError) -> str:
    """What a file server status means for a file system."""

    if error.code == sftp.FX_NO_SUCH_FILE:
        return NOENT

    if error.code == sftp.FX_PERMISSION_DENIED:
        return ACCES

    return IO


class TargetFiles:
    """The files of one target, read and written over SFTP."""

    def __init__(
        self,
        connect: Callable[[], sftp.Client],
        clock: Callable[[], float] = time.monotonic,
    ):
        self._connect = connect
        self._clock = clock
        self._lock = threading.Lock()
        self._client: sftp.Client | None = None
        self._retry_at = 0.0
        self._last_error = ""
        self._attrs: dict[str, tuple[float, sftp.Attrs | None]] = {}
        self._files: dict[tuple[str, bool], OpenFile] = {}

    # ---- the link ----------------------------------------------------

    def _client_now(self) -> sftp.Client:
        """The live conversation, opened if there is none."""

        with self._lock:
            client = self._client

            if client is not None and client.alive:
                return client

            if self._clock() < self._retry_at:
                raise FsError(IO, self._last_error or "the target is away")

            self._client = None
            self._files.clear()

        try:
            client = self._connect()
        except sftp.SftpError as error:
            self._lost(str(error))

            raise FsError(IO, str(error)) from None

        with self._lock:
            self._client = client

        return client

    def _lost(self, text: str) -> None:
        with self._lock:
            self._retry_at = self._clock() + RETRY_SECONDS
            self._last_error = text
            self._attrs.clear()

    def _fail(self, error: sftp.SftpError) -> FsError:
        if error.code == sftp.FX_NO_LINK:
            self._lost(str(error))

        return FsError(explain(error), str(error))

    def connected(self) -> bool:
        with self._lock:
            return self._client is not None and self._client.alive

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            self._files.clear()
            self._attrs.clear()

        if client is not None:
            client.close()

    # ---- what is remembered ------------------------------------------

    def _remember(self, path: str, found: sftp.Attrs | None) -> None:
        with self._lock:
            self._attrs[path] = (self._clock(), found)

    def _forget(self, *paths: str) -> None:
        with self._lock:
            for path in paths:
                self._attrs.pop(path, None)
                self._attrs.pop(parent_target(path), None)

    def _recall(self, path: str) -> tuple[bool, sftp.Attrs | None]:
        with self._lock:
            kept = self._attrs.get(path)

        if kept is None:
            return False, None

        age = self._clock() - kept[0]
        limit = MISSING_SECONDS if kept[1] is None else ATTR_SECONDS

        if age >= limit:
            return False, None

        return True, kept[1]

    # ---- looking -----------------------------------------------------

    def home(self) -> str:
        """The folder a login on the target starts in."""

        try:
            return self._client_now().realpath(".")
        except sftp.SftpError as error:
            raise self._fail(error) from None

    def lstat(self, path: str) -> sftp.Attrs:
        known, found = self._recall(path)

        if known and found is None:
            raise FsError(NOENT)

        if known:
            return found

        try:
            found = self._client_now().lstat(path)
        except sftp.SftpError as error:
            failure = self._fail(error)

            if failure.why == NOENT:
                self._remember(path, None)

            raise failure from None

        self._remember(path, found)

        return found

    def listdir(self, path: str) -> list[tuple[str, sftp.Attrs]]:
        try:
            rows = self._client_now().listdir(path)
        except sftp.SftpError as error:
            raise self._fail(error) from None

        # The kernel asks about each name right after it lists them.
        for name, found in rows:
            self._remember(join_target(path, name), found)

        return sorted(rows)

    def readlink(self, path: str) -> str:
        try:
            return self._client_now().readlink(path)
        except sftp.SftpError as error:
            raise self._fail(error) from None

    # ---- open files --------------------------------------------------

    def _close_file(self, key: tuple[str, bool]) -> None:
        with self._lock:
            kept = self._files.pop(key, None)

        if kept is not None and kept.client.alive:
            kept.client.close_handle(kept.handle)

    def _tidy_files(self) -> None:
        """Close files nobody has touched for a while."""

        now = self._clock()

        with self._lock:
            stale = [
                key
                for key, kept in self._files.items()
                if now - kept.last_used >= HANDLE_SECONDS
            ]

            if len(self._files) - len(stale) >= MAX_HANDLES:
                oldest = sorted(
                    self._files,
                    key=lambda key: self._files[key].last_used,
                )
                stale = oldest[: len(self._files) - MAX_HANDLES + 1]

        for key in stale:
            self._close_file(key)

    def _keep(self, key, client: sftp.Client, handle: bytes) -> OpenFile:
        kept = OpenFile(handle, client, self._clock())

        with self._lock:
            stale = self._files.get(key)
            self._files[key] = kept

        if stale is not None and stale.client.alive:
            stale.client.close_handle(stale.handle)

        return kept

    def _open_as_owner(self, client: sftp.Client, path: str) -> bytes:
        """Open a file for writing that its own mode forbids.

        A file server lets the owner write to a file it just made
        read-only, because the file is still open. NFS has no open
        files, so a write that comes later arrives as a new open, and
        the target refuses it. `git` does exactly this to every
        object it stores. The mode is lifted for the length of the
        open and put back.
        """

        mode = statmod.S_IMODE(client.lstat(path).mode)

        client.setstat(path, mode=mode | statmod.S_IWUSR)

        try:
            return client.open(path, sftp.OPEN_WRITE)
        finally:
            client.setstat(path, mode=mode)

    def _open(self, path: str, writable: bool) -> OpenFile:
        self._tidy_files()

        client = self._client_now()
        key = (path, writable)

        with self._lock:
            kept = self._files.get(key)

            if kept is not None and kept.client is client:
                kept.last_used = self._clock()

                return kept

        flags = sftp.OPEN_WRITE if writable else sftp.OPEN_READ

        try:
            handle = client.open(path, flags)
        except sftp.SftpError as error:
            if not writable or error.code != sftp.FX_PERMISSION_DENIED:
                raise

            handle = self._open_as_owner(client, path)

        return self._keep(key, client, handle)

    def _close_path(self, path: str) -> None:
        self._close_file((path, False))
        self._close_file((path, True))

    def read(self, path: str, offset: int, size: int) -> bytes:
        try:
            kept = self._open(path, False)

            return kept.client.read(kept.handle, offset, size)
        except sftp.SftpError as error:
            self._close_file((path, False))

            raise self._fail(error) from None

    def write(self, path: str, offset: int, data: bytes) -> None:
        try:
            kept = self._open(path, True)
            kept.client.write(kept.handle, offset, data)
        except sftp.SftpError as error:
            self._close_file((path, True))

            raise self._fail(error) from None

        self._forget(path)

    # ---- changing ----------------------------------------------------

    def _exists(self, path: str) -> bool:
        self._forget(path)

        try:
            self.lstat(path)
        except FsError:
            return False

        return True

    def create(self, path: str, mode: int, exclusive: bool) -> None:
        """Make an empty file. An existing one is kept unless
        `exclusive`, which then fails."""

        flags = sftp.OPEN_WRITE | sftp.OPEN_CREATE

        if exclusive:
            flags |= sftp.OPEN_EXCLUSIVE

        try:
            client = self._client_now()
            handle = client.open(path, flags, mode)

            # Kept open: the writes that fill a new file follow at
            # once, and its mode may not allow a second open.
            self._keep((path, True), client, handle)
        except sftp.SftpError as error:
            failure = self._fail(error)

            if failure.why == IO and exclusive and self._exists(path):
                raise FsError(EXIST) from None

            raise failure from None

        self._forget(path)

    def mkdir(self, path: str, mode: int) -> None:
        try:
            self._client_now().mkdir(path, mode)
        except sftp.SftpError as error:
            failure = self._fail(error)

            # The file server has one word for every kind of refusal.
            if failure.why == IO and self._exists(path):
                raise FsError(EXIST) from None

            raise failure from None

        self._forget(path)

    def symlink(self, path: str, target: str) -> None:
        try:
            self._client_now().symlink(target, path)
        except sftp.SftpError as error:
            failure = self._fail(error)

            if failure.why == IO and self._exists(path):
                raise FsError(EXIST) from None

            raise failure from None

        self._forget(path)

    def remove(self, path: str) -> None:
        self._close_path(path)

        try:
            self._client_now().remove(path)
        except sftp.SftpError as error:
            raise self._fail(error) from None

        self._forget(path)

    def rmdir(self, path: str) -> None:
        try:
            self._client_now().rmdir(path)
        except sftp.SftpError as error:
            failure = self._fail(error)

            if failure.why == IO and self._exists(path):
                raise FsError(NOTEMPTY) from None

            raise failure from None

        self._forget(path)

    def rename(self, old: str, new: str) -> None:
        self._close_path(old)
        self._close_path(new)

        try:
            self._client_now().rename(old, new)
        except sftp.SftpError as error:
            raise self._fail(error) from None

        with self._lock:
            self._attrs.clear()

    def setattr(
        self,
        path: str,
        size: int | None = None,
        mode: int | None = None,
        times: tuple[int, int] | None = None,
    ) -> None:
        if size is None and mode is None and times is None:
            return

        try:
            self._client_now().setstat(path, size, mode, times)
        except sftp.SftpError as error:
            raise self._fail(error) from None

        self._forget(path)

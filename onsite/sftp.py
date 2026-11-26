"""An SFTP client in plain Python, spoken over the `ssh` program.

`ssh user@host -s sftp` starts the file server that every OpenSSH
install ships. This speaks version 3 of its protocol on that pipe:
look, list, read, write, rename, remove. It is what lets a target's
real files be shown on the host with no program added to either
machine.

One request is sent at a time and its answer is read before the
next. That is slower than the protocol allows and simple enough to
trust.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import stat as statmod
import struct
import threading
import time

from dataclasses import dataclass

from .link import LinkError


PROTOCOL_VERSION = 3

# Packet types, as the protocol numbers them.
FXP_INIT = 1
FXP_VERSION = 2
FXP_OPEN = 3
FXP_CLOSE = 4
FXP_READ = 5
FXP_WRITE = 6
FXP_LSTAT = 7
FXP_SETSTAT = 9
FXP_OPENDIR = 11
FXP_READDIR = 12
FXP_REMOVE = 13
FXP_MKDIR = 14
FXP_RMDIR = 15
FXP_REALPATH = 16
FXP_STAT = 17
FXP_RENAME = 18
FXP_READLINK = 19
FXP_SYMLINK = 20
FXP_STATUS = 101
FXP_HANDLE = 102
FXP_DATA = 103
FXP_NAME = 104
FXP_ATTRS = 105
FXP_EXTENDED = 200

# Status codes in a STATUS packet.
FX_OK = 0
FX_EOF = 1
FX_NO_SUCH_FILE = 2
FX_PERMISSION_DENIED = 3
FX_FAILURE = 4
FX_OP_UNSUPPORTED = 8

# Not a protocol code: the link itself failed.
FX_NO_LINK = -1

# How a file is opened.
OPEN_READ = 0x01
OPEN_WRITE = 0x02
OPEN_CREATE = 0x08
OPEN_TRUNCATE = 0x10
OPEN_EXCLUSIVE = 0x20

# Which fields an attribute block carries.
ATTR_SIZE = 0x01
ATTR_OWNER = 0x02
ATTR_MODE = 0x04
ATTR_TIMES = 0x08
ATTR_EXTENDED = 0x80000000

# OpenSSH's own atomic rename. The plain one refuses to replace a
# file, which is what every editor's save does.
POSIX_RENAME = b"posix-rename@openssh.com"

# The most one READ or WRITE may carry. OpenSSH takes far more, and
# other servers take this much.
CHUNK = 32768

CALL_SECONDS = 20
HEADER = struct.Struct(">I")
MAX_PACKET = 1 << 20


class SftpError(Exception):
    """The target's file server refused or could not be asked."""

    def __init__(self, code: int, text: str):
        super().__init__(text or f"sftp status {code}")
        self.code = code


@dataclass(frozen=True)
class Attrs:
    """What the target says about one file."""

    size: int = 0
    mode: int = 0
    mtime: int = 0
    atime: int = 0
    uid: int = 0
    gid: int = 0

    @property
    def is_dir(self) -> bool:
        return statmod.S_ISDIR(self.mode)

    @property
    def is_link(self) -> bool:
        return statmod.S_ISLNK(self.mode)


class Reader:
    """Takes the fields of one packet apart, in order."""

    def __init__(self, data: bytes):
        self._data = data
        self._at = 0

    def _take(self, count: int) -> bytes:
        end = self._at + count

        if end > len(self._data):
            raise SftpError(FX_FAILURE, "the answer was cut short")

        chunk = self._data[self._at:end]
        self._at = end

        return chunk

    def u32(self) -> int:
        return struct.unpack(">I", self._take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self._take(8))[0]

    def string(self) -> bytes:
        return self._take(self.u32())

    def text(self) -> str:
        return self.string().decode("utf-8", "surrogateescape")

    def attrs(self) -> Attrs:
        flags = self.u32()
        size = mode = mtime = atime = uid = gid = 0

        if flags & ATTR_SIZE:
            size = self.u64()

        if flags & ATTR_OWNER:
            uid = self.u32()
            gid = self.u32()

        if flags & ATTR_MODE:
            mode = self.u32()

        if flags & ATTR_TIMES:
            atime = self.u32()
            mtime = self.u32()

        if flags & ATTR_EXTENDED:
            for _ in range(self.u32()):
                self.string()
                self.string()

        return Attrs(size, mode, mtime, atime, uid, gid)


def pack_string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def pack_path(path: str) -> bytes:
    return pack_string(path.encode("utf-8", "surrogateescape"))


def pack_attrs(
    size: int | None = None,
    mode: int | None = None,
    times: tuple[int, int] | None = None,
) -> bytes:
    """An attribute block that sets only what is given."""

    flags = 0
    body = b""

    if size is not None:
        flags |= ATTR_SIZE
        body += struct.pack(">Q", size)

    if mode is not None:
        flags |= ATTR_MODE
        body += struct.pack(">I", mode & 0o7777)

    if times is not None:
        flags |= ATTR_TIMES
        body += struct.pack(">II", times[0], times[1])

    return struct.pack(">I", flags) + body


class Client:
    """One conversation with a target's file server."""

    def __init__(self, link, clock=time.monotonic):
        self._link = link
        self._clock = clock
        self._lock = threading.Lock()
        self._next_id = 1
        self._extensions: set[bytes] = set()
        self._dead = False

    # ---- the wire ----------------------------------------------------

    def _read_packet(self, until: float) -> tuple[int, Reader]:
        size = HEADER.unpack(self._link.read_exact(4, until))[0]

        if size < 1 or size > MAX_PACKET:
            raise SftpError(FX_FAILURE, f"a packet of {size} bytes")

        body = self._link.read_exact(size, until)

        return body[0], Reader(body[1:])

    def _exchange(self, packet: bytes) -> tuple[int, Reader]:
        """Send one packet and read the one that answers it."""

        if self._dead:
            raise SftpError(FX_NO_LINK, "the link is closed")

        try:
            self._link.write(HEADER.pack(len(packet)) + packet)

            return self._read_packet(self._clock() + CALL_SECONDS)
        except LinkError as error:
            # Half an answer may be sitting in the pipe. Nothing sent
            # after this could be matched to its reply.
            self._dead = True

            raise SftpError(FX_NO_LINK, str(error)) from None

    def start(self) -> None:
        """Agree on the protocol and learn what the server adds to it."""

        with self._lock:
            kind, reader = self._exchange(
                struct.pack(">BI", FXP_INIT, PROTOCOL_VERSION)
            )

        if kind != FXP_VERSION:
            raise SftpError(FX_FAILURE, "the target has no SFTP server")

        reader.u32()

        try:
            while True:
                self._extensions.add(reader.string())
                reader.string()
        except SftpError:
            return

    def _call(self, kind: int, payload: bytes) -> tuple[int, Reader]:
        with self._lock:
            number = self._next_id
            self._next_id = (self._next_id + 1) & 0xFFFFFFFF

            answer, reader = self._exchange(
                struct.pack(">BI", kind, number) + payload
            )

        if reader.u32() != number:
            self._dead = True

            raise SftpError(FX_NO_LINK, "an answer came out of order")

        return answer, reader

    def _expect(self, wanted: int, kind: int, payload: bytes) -> Reader:
        """Make a call whose answer must be of one type."""

        answer, reader = self._call(kind, payload)

        if answer == wanted:
            return reader

        if answer != FXP_STATUS:
            raise SftpError(FX_FAILURE, f"an answer of type {answer}")

        code = reader.u32()

        raise SftpError(code, reader.text())

    def _status(self, kind: int, payload: bytes) -> int:
        """Make a call that is answered with a status and nothing more."""

        answer, reader = self._call(kind, payload)

        if answer != FXP_STATUS:
            raise SftpError(FX_FAILURE, f"an answer of type {answer}")

        code = reader.u32()

        if code not in (FX_OK, FX_EOF):
            raise SftpError(code, reader.text())

        return code

    @property
    def alive(self) -> bool:
        return not self._dead

    def close(self) -> None:
        self._dead = True
        self._link.close()

    # ---- looking -----------------------------------------------------

    def realpath(self, path: str) -> str:
        """The full path the target gives a name. `.` is its start folder."""

        reader = self._expect(FXP_NAME, FXP_REALPATH, pack_path(path))
        reader.u32()

        return reader.text()

    def lstat(self, path: str) -> Attrs:
        """What a path is. A link is reported as the link itself."""

        return self._expect(FXP_ATTRS, FXP_LSTAT, pack_path(path)).attrs()

    def stat(self, path: str) -> Attrs:
        """What a path is, after every link in it is followed."""

        return self._expect(FXP_ATTRS, FXP_STAT, pack_path(path)).attrs()

    def readlink(self, path: str) -> str:
        reader = self._expect(FXP_NAME, FXP_READLINK, pack_path(path))
        reader.u32()

        return reader.text()

    def listdir(self, path: str) -> list[tuple[str, Attrs]]:
        """Every name in a folder with what it is, in one pass."""

        handle = self._expect(
            FXP_HANDLE,
            FXP_OPENDIR,
            pack_path(path),
        ).string()
        rows = []

        try:
            while True:
                answer, reader = self._call(FXP_READDIR, pack_string(handle))

                if answer != FXP_NAME:
                    break

                for _ in range(reader.u32()):
                    name = reader.text()
                    reader.string()
                    found = reader.attrs()

                    if name not in (".", ".."):
                        rows.append((name, found))
        finally:
            self.close_handle(handle)

        return rows

    # ---- reading and writing -----------------------------------------

    def open(self, path: str, flags: int, mode: int | None = None) -> bytes:
        payload = pack_path(path) + struct.pack(">I", flags)

        return self._expect(
            FXP_HANDLE,
            FXP_OPEN,
            payload + pack_attrs(mode=mode),
        ).string()

    def close_handle(self, handle: bytes) -> None:
        try:
            self._status(FXP_CLOSE, pack_string(handle))
        except SftpError:
            # A handle that cannot be closed is gone either way.
            return

    def read(self, handle: bytes, offset: int, size: int) -> bytes:
        """Up to `size` bytes from `offset`. Empty at the end of the file."""

        parts = []

        while size > 0:
            answer, reader = self._call(
                FXP_READ,
                pack_string(handle)
                + struct.pack(">QI", offset, min(size, CHUNK)),
            )

            if answer != FXP_DATA:
                break

            chunk = reader.string()

            if not chunk:
                break

            parts.append(chunk)
            offset += len(chunk)
            size -= len(chunk)

        return b"".join(parts)

    def write(self, handle: bytes, offset: int, data: bytes) -> None:
        view = memoryview(data)

        while view:
            chunk = bytes(view[:CHUNK])

            self._status(
                FXP_WRITE,
                pack_string(handle)
                + struct.pack(">Q", offset)
                + pack_string(chunk),
            )

            offset += len(chunk)
            view = view[len(chunk):]

    # ---- changing ----------------------------------------------------

    def setstat(
        self,
        path: str,
        size: int | None = None,
        mode: int | None = None,
        times: tuple[int, int] | None = None,
    ) -> None:
        self._status(
            FXP_SETSTAT,
            pack_path(path) + pack_attrs(size, mode, times),
        )

    def mkdir(self, path: str, mode: int) -> None:
        self._status(FXP_MKDIR, pack_path(path) + pack_attrs(mode=mode))

    def rmdir(self, path: str) -> None:
        self._status(FXP_RMDIR, pack_path(path))

    def remove(self, path: str) -> None:
        self._status(FXP_REMOVE, pack_path(path))

    def rename(self, old: str, new: str) -> None:
        """Rename, replacing `new` if it is there, when the server can."""

        names = pack_path(old) + pack_path(new)

        if POSIX_RENAME in self._extensions:
            self._status(FXP_EXTENDED, pack_string(POSIX_RENAME) + names)
            return

        self._status(FXP_RENAME, names)

    def symlink(self, target: str, path: str) -> None:
        # OpenSSH takes these two the other way round from the
        # protocol's own text, and every client follows OpenSSH.
        self._status(FXP_SYMLINK, pack_path(target) + pack_path(path))

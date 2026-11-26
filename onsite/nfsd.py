"""A small NFS server, so a host can mount a target's files.

macOS mounts NFS with a program it ships, as a normal user, with no
extension to install. So a server that speaks NFS version 3 on the
host's own loopback address is all it takes to make a target's disk a
folder on the host. Unlike WebDAV, NFS carries file modes, links and
times, so `git` on the host sees the tree as it is.

This file is the protocol and nothing else. The files come from a
backend, in practice `remotefs.TargetFiles`.

Three things keep another user of the same host from reading a
target through this port:

- It listens on the loopback address only.
- A mount is granted once, for a path that is a random token.
- Every file handle is 16 random bytes. A call with a handle the
  server did not hand out is refused.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import os
import secrets
import socketserver
import stat as statmod
import struct
import threading
import time

from . import remotefs
from .paths import TARGET_ROOT
from .paths import PathError
from .paths import join_target
from .paths import parent_target


LOOPBACK = "127.0.0.1"

PROGRAM_MOUNT = 100005
PROGRAM_NFS = 100003

# RPC framing and reply words.
LAST_FRAGMENT = 0x80000000
MAX_RECORD = 4 << 20
REPLY = 1
MSG_ACCEPTED = 0
AUTH_NONE = 0
AUTH_UNIX = 1
SUCCESS = 0
PROG_UNAVAIL = 1
PROC_UNAVAIL = 3

# The mount program's procedures.
MOUNT_MNT = 1
MOUNT_UMNT = 3

# The NFS procedures, as the protocol numbers them.
NULL = 0
GETATTR = 1
SETATTR = 2
LOOKUP = 3
ACCESS = 4
READLINK = 5
READ = 6
WRITE = 7
CREATE = 8
MKDIR = 9
SYMLINK = 10
MKNOD = 11
REMOVE = 12
RMDIR = 13
RENAME = 14
LINK = 15
READDIR = 16
READDIRPLUS = 17
FSSTAT = 18
FSINFO = 19
PATHCONF = 20
COMMIT = 21

# Status codes.
NFS_OK = 0
ERR_NOENT = 2
ERR_IO = 5
ERR_ACCES = 13
ERR_EXIST = 17
ERR_NOTDIR = 20
ERR_ISDIR = 21
ERR_INVAL = 22
ERR_NOTEMPTY = 66
ERR_STALE = 70
ERR_NOTSUPP = 10004

STATUS_OF = {
    remotefs.NOENT: ERR_NOENT,
    remotefs.ACCES: ERR_ACCES,
    remotefs.EXIST: ERR_EXIST,
    remotefs.NOTEMPTY: ERR_NOTEMPTY,
    remotefs.NOTDIR: ERR_NOTDIR,
    remotefs.ISDIR: ERR_ISDIR,
    remotefs.IO: ERR_IO,
}

# File types.
TYPE_FILE = 1
TYPE_DIR = 2
TYPE_LINK = 5

# How CREATE is asked to behave.
CREATE_UNCHECKED = 0
CREATE_GUARDED = 1
CREATE_EXCLUSIVE = 2

# How a time is set.
TIME_KEEP = 0
TIME_NOW = 1
TIME_GIVEN = 2

# What a WRITE promises: the data is on the target before the reply.
FILE_SYNC = 2

# What ACCESS may grant.
ACCESS_READ = 0x01
ACCESS_LOOKUP = 0x02
ACCESS_MODIFY = 0x04
ACCESS_EXTEND = 0x08
ACCESS_DELETE = 0x10
ACCESS_EXECUTE = 0x20

HANDLE_BYTES = 16
TOKEN_BYTES = 16
MAX_IO = 1 << 20
MAX_NAME = 255
BLOCK = 4096
FSID = 0x6F6E73697465
BIG = 1 << 40

# A file system that has links and keeps times a client can trust.
FS_PROPERTIES = 0x1B

# macOS keeps what it knows about a file, such as where it came
# from, in a second file beside it whose name starts like this, on
# any file system that cannot hold that itself. Through this server
# that would put a second file beside every file a Mac touched on the
# target. So these names cannot be made here, and are not shown.
SIDECAR_PREFIX = "._"

# Failures answer with attributes that are "not given". How many such
# marks follow the status differs by procedure.
ABSENT_MARKS = {
    GETATTR: 0,
    SETATTR: 2,
    LOOKUP: 1,
    ACCESS: 1,
    READLINK: 1,
    READ: 1,
    WRITE: 2,
    CREATE: 2,
    MKDIR: 2,
    SYMLINK: 2,
    MKNOD: 2,
    REMOVE: 2,
    RMDIR: 2,
    RENAME: 4,
    LINK: 3,
    READDIR: 1,
    READDIRPLUS: 1,
    FSSTAT: 1,
    FSINFO: 1,
    PATHCONF: 1,
    COMMIT: 2,
}


class Refused(Exception):
    """A call that ends with a status and no result."""

    def __init__(self, status: int):
        super().__init__(f"nfs status {status}")
        self.status = status


class Unpacker:
    """Reads the words of one call, in order."""

    def __init__(self, data: bytes):
        self._data = data
        self._at = 0

    def _take(self, count: int) -> bytes:
        end = self._at + count

        if end > len(self._data):
            raise Refused(ERR_INVAL)

        chunk = self._data[self._at:end]
        self._at = end

        return chunk

    def u32(self) -> int:
        return struct.unpack(">I", self._take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self._take(8))[0]

    def opaque(self) -> bytes:
        size = self.u32()
        value = self._take(size)

        self._take((4 - size % 4) % 4)

        return value

    def name(self) -> str:
        return self.opaque().decode("utf-8", "surrogateescape")

    def sattr(self) -> dict:
        """The attributes a client wants set. Owners are read and
        dropped: the target decides who owns its files."""

        wanted: dict = {"size": None, "mode": None, "times": None}

        if self.u32():
            wanted["mode"] = self.u32()

        if self.u32():
            self.u32()

        if self.u32():
            self.u32()

        if self.u32():
            wanted["size"] = self.u64()

        wanted["atime"] = self._time()
        wanted["mtime"] = self._time()

        return wanted

    def _time(self) -> int | None:
        how = self.u32()

        if how == TIME_KEEP:
            return None

        if how == TIME_NOW:
            return -1

        seconds = self.u32()
        self.u32()

        return seconds


class Packer:
    """Writes the words of one reply, in order."""

    def __init__(self):
        self._parts: list[bytes] = []
        self._size = 0

    def raw(self, value: bytes) -> "Packer":
        self._parts.append(value)
        self._size += len(value)

        return self

    def u32(self, value: int) -> "Packer":
        return self.raw(struct.pack(">I", value & 0xFFFFFFFF))

    def u64(self, value: int) -> "Packer":
        return self.raw(struct.pack(">Q", value & 0xFFFFFFFFFFFFFFFF))

    def opaque(self, value: bytes) -> "Packer":
        pad = (4 - len(value) % 4) % 4

        return self.raw(struct.pack(">I", len(value)) + value + b"\0" * pad)

    def name(self, value: str) -> "Packer":
        return self.opaque(value.encode("utf-8", "surrogateescape"))

    def absent(self, count: int = 1) -> "Packer":
        for _ in range(count):
            self.u32(0)

        return self

    @property
    def size(self) -> int:
        return self._size

    def bytes(self) -> bytes:
        return b"".join(self._parts)


class Handles:
    """Hands out a random handle for each path and takes them back."""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_path: dict[str, bytes] = {}
        self._by_handle: dict[bytes, str] = {}
        self._ids: dict[bytes, int] = {}

    def of(self, path: str) -> bytes:
        with self._lock:
            handle = self._by_path.get(path)

            if handle is None:
                handle = secrets.token_bytes(HANDLE_BYTES)
                self._by_path[path] = handle
                self._by_handle[handle] = path
                self._ids[handle] = len(self._ids) + 1

            return handle

    def path(self, handle: bytes) -> str:
        with self._lock:
            path = self._by_handle.get(handle)

        if path is None:
            raise Refused(ERR_STALE)

        return path

    def file_id(self, path: str) -> int:
        handle = self.of(path)

        with self._lock:
            return self._ids[handle]

    def drop(self, path: str) -> None:
        with self._lock:
            handle = self._by_path.pop(path, None)

            if handle is not None:
                self._by_handle.pop(handle, None)

    def move(self, old: str, new: str) -> None:
        """A renamed file keeps its handle. So does all that is in it."""

        inside = old.rstrip("/") + "/"

        with self._lock:
            stale = self._by_path.pop(new, None)

            if stale is not None:
                self._by_handle.pop(stale, None)

            for path in list(self._by_path):
                if path != old and not path.startswith(inside):
                    continue

                handle = self._by_path.pop(path)
                moved = new + path[len(old):]
                self._by_path[moved] = handle
                self._by_handle[handle] = moved


class Server:
    """Answers mount and NFS calls from a file backend."""

    def __init__(self, files, writable: bool = True):
        self._files = files
        self._writable = writable
        self._handles = Handles()
        self._lock = threading.Lock()
        self._granted = False
        self._uid = os.getuid()
        self._gid = os.getgid()
        self._verifier = secrets.token_bytes(8)

        self.token = secrets.token_hex(TOKEN_BYTES)
        self.calls: dict[int, int] = {}

    @property
    def export(self) -> str:
        """The path a mount has to ask for."""

        return TARGET_ROOT + self.token

    def allow_mount(self) -> None:
        """Let the next mount call through. One, then the door shuts."""

        with self._lock:
            self._granted = False

    # ---- attributes --------------------------------------------------

    def _attrs_into(self, out: Packer, path: str, found) -> None:
        kind = TYPE_FILE
        links = 1

        if statmod.S_ISDIR(found.mode):
            kind = TYPE_DIR
            links = 2

        if statmod.S_ISLNK(found.mode):
            kind = TYPE_LINK

        out.u32(kind).u32(statmod.S_IMODE(found.mode)).u32(links)

        # Every file is shown as the mounting user's. The target has
        # already decided what its own account may do.
        out.u32(self._uid).u32(self._gid)
        out.u64(found.size).u64(found.size)
        out.u32(0).u32(0)
        out.u64(FSID).u64(self._handles.file_id(path))
        out.u32(found.atime or found.mtime).u32(0)
        out.u32(found.mtime).u32(0)
        out.u32(found.mtime).u32(0)

    def _post(self, out: Packer, path: str) -> None:
        """A file's attributes after a call, or a mark that says none."""

        try:
            found = self._files.lstat(path)
        except remotefs.FsError:
            out.absent()
            return

        out.u32(1)
        self._attrs_into(out, path, found)

    def _wcc(self, out: Packer, path: str) -> None:
        """What changed around a write: nothing before, the file after."""

        out.absent()
        self._post(out, path)

    def _child(self, call: Unpacker, making: bool) -> tuple[str, str]:
        """A folder and a name in it, for a call that changes one.

        `making` says the name is about to exist. A sidecar name is
        refused then, and is "not there" for anything else.
        """

        folder = self._handles.path(call.opaque())
        name = call.name()

        if len(name.encode("utf-8", "surrogateescape")) > MAX_NAME:
            raise Refused(ERR_INVAL)

        if name.startswith(SIDECAR_PREFIX):
            raise Refused(ERR_ACCES if making else ERR_NOENT)

        try:
            return folder, join_target(folder, name)
        except PathError:
            raise Refused(ERR_INVAL) from None

    def _must_write(self) -> None:
        if not self._writable:
            raise Refused(ERR_ACCES)

    # ---- the mount program -------------------------------------------

    def mount(self, procedure: int, call: Unpacker) -> bytes:
        out = Packer()

        if procedure != MOUNT_MNT:
            return out.bytes()

        asked = call.name()

        with self._lock:
            refused = self._granted or asked != self.export

            if not refused:
                self._granted = True

        if refused:
            return out.u32(ERR_ACCES).bytes()

        out.u32(NFS_OK).opaque(self._handles.of(TARGET_ROOT))

        return out.u32(1).u32(AUTH_UNIX).bytes()

    # ---- looking -----------------------------------------------------

    def _getattr(self, call: Unpacker, out: Packer) -> None:
        path = self._handles.path(call.opaque())
        found = self._files.lstat(path)

        out.u32(NFS_OK)
        self._attrs_into(out, path, found)

    def _lookup(self, call: Unpacker, out: Packer) -> None:
        folder = self._handles.path(call.opaque())
        name = call.name()
        path = folder

        if name == "..":
            path = parent_target(folder)

        if name.startswith(SIDECAR_PREFIX):
            raise Refused(ERR_NOENT)

        if name not in (".", ".."):
            try:
                path = join_target(folder, name)
            except PathError:
                raise Refused(ERR_INVAL) from None

        found = self._files.lstat(path)

        out.u32(NFS_OK).opaque(self._handles.of(path))
        out.u32(1)
        self._attrs_into(out, path, found)
        self._post(out, folder)

    def _access(self, call: Unpacker, out: Packer) -> None:
        path = self._handles.path(call.opaque())
        wanted = call.u32()
        granted = ACCESS_READ | ACCESS_LOOKUP | ACCESS_EXECUTE

        if self._writable:
            granted |= ACCESS_MODIFY | ACCESS_EXTEND | ACCESS_DELETE

        self._files.lstat(path)

        out.u32(NFS_OK)
        self._post(out, path)
        out.u32(wanted & granted)

    def _readlink(self, call: Unpacker, out: Packer) -> None:
        path = self._handles.path(call.opaque())
        target = self._files.readlink(path)

        out.u32(NFS_OK)
        self._post(out, path)
        out.name(target)

    def _read(self, call: Unpacker, out: Packer) -> None:
        path = self._handles.path(call.opaque())
        offset = call.u64()
        count = min(call.u32(), MAX_IO)
        found = self._files.lstat(path)

        if statmod.S_ISDIR(found.mode):
            raise Refused(ERR_ISDIR)

        data = self._files.read(path, offset, count)

        out.u32(NFS_OK)
        self._post(out, path)
        out.u32(len(data))
        out.u32(1 if offset + len(data) >= found.size else 0)
        out.opaque(data)

    def _readdir(self, call: Unpacker, out: Packer, plus: bool) -> None:
        path = self._handles.path(call.opaque())
        cookie = call.u64()
        call.u64()

        if plus:
            call.u32()

        limit = min(max(call.u32(), 1024), MAX_IO)
        rows = self._files.listdir(path)

        out.u32(NFS_OK)
        self._post(out, path)
        out.raw(self._verifier)

        done = True

        for number, (name, found) in enumerate(rows, start=1):
            if number <= cookie:
                continue

            if name.startswith(SIDECAR_PREFIX):
                continue

            child = join_target(path, name)
            entry = Packer()
            entry.u32(1).u64(self._handles.file_id(child))
            entry.name(name).u64(number)

            if plus:
                entry.u32(1)
                self._attrs_into(entry, child, found)
                entry.u32(1).opaque(self._handles.of(child))

            # Room is kept for the two words that end the list.
            if out.size + entry.size + 8 > limit:
                done = False
                break

            out.raw(entry.bytes())

        out.u32(0).u32(1 if done else 0)

    def _fsstat(self, call: Unpacker, out: Packer) -> None:
        path = self._handles.path(call.opaque())

        out.u32(NFS_OK)
        self._post(out, path)

        # The target's free space is not asked for. These numbers
        # only keep a client from deciding the disk is full.
        out.u64(BIG).u64(BIG).u64(BIG)
        out.u64(BIG).u64(BIG).u64(BIG)
        out.u32(0)

    def _fsinfo(self, call: Unpacker, out: Packer) -> None:
        path = self._handles.path(call.opaque())

        out.u32(NFS_OK)
        self._post(out, path)
        out.u32(MAX_IO).u32(MAX_IO).u32(BLOCK)
        out.u32(MAX_IO).u32(MAX_IO).u32(BLOCK)
        out.u32(MAX_IO)
        out.u64(BIG)
        out.u32(1).u32(0)
        out.u32(FS_PROPERTIES)

    def _pathconf(self, call: Unpacker, out: Packer) -> None:
        path = self._handles.path(call.opaque())

        out.u32(NFS_OK)
        self._post(out, path)
        out.u32(1).u32(MAX_NAME)
        out.u32(1).u32(1).u32(0).u32(1)

    # ---- changing ----------------------------------------------------

    def _apply(self, path: str, wanted: dict) -> None:
        times = None

        if wanted["mtime"] is not None or wanted["atime"] is not None:
            found = self._files.lstat(path)
            atime = wanted["atime"]
            mtime = wanted["mtime"]

            # "Now" is the host's now. The two clocks may differ, and
            # the host's is the one the person is looking at.
            now = int(time.time())
            atime = found.atime if atime is None else atime
            mtime = found.mtime if mtime is None else mtime
            times = (
                now if atime == -1 else atime,
                now if mtime == -1 else mtime,
            )

        self._files.setattr(path, wanted["size"], wanted["mode"], times)

    def _setattr(self, call: Unpacker, out: Packer) -> None:
        self._must_write()

        path = self._handles.path(call.opaque())

        self._apply(path, call.sattr())

        out.u32(NFS_OK)
        self._wcc(out, path)

    def _write(self, call: Unpacker, out: Packer) -> None:
        self._must_write()

        path = self._handles.path(call.opaque())
        offset = call.u64()
        call.u32()
        call.u32()
        data = call.opaque()

        self._files.write(path, offset, data)

        out.u32(NFS_OK)
        self._wcc(out, path)
        out.u32(len(data)).u32(FILE_SYNC).raw(self._verifier)

    def _made(self, out: Packer, folder: str, path: str) -> None:
        """The reply to anything that makes a new name in a folder."""

        out.u32(NFS_OK)
        out.u32(1).opaque(self._handles.of(path))
        self._post(out, path)
        self._wcc(out, folder)

    def _create(self, call: Unpacker, out: Packer) -> None:
        self._must_write()

        folder, path = self._child(call, making=True)
        how = call.u32()
        wanted = {"size": None, "mode": None, "atime": None, "mtime": None}

        if how == CREATE_EXCLUSIVE:
            call.u64()
        else:
            wanted = call.sattr()

        mode = wanted["mode"]

        self._files.create(
            path,
            remotefs.DEFAULT_FILE_MODE if mode is None else mode,
            exclusive=how != CREATE_UNCHECKED,
        )

        if wanted["size"] is not None:
            self._files.setattr(path, size=wanted["size"])

        self._made(out, folder, path)

    def _mkdir(self, call: Unpacker, out: Packer) -> None:
        self._must_write()

        folder, path = self._child(call, making=True)
        mode = call.sattr()["mode"]

        self._files.mkdir(path, 0o755 if mode is None else mode)
        self._made(out, folder, path)

    def _symlink(self, call: Unpacker, out: Packer) -> None:
        self._must_write()

        folder, path = self._child(call, making=True)

        call.sattr()

        self._files.symlink(path, call.name())
        self._made(out, folder, path)

    def _remove(self, call: Unpacker, out: Packer, folder_too: bool) -> None:
        self._must_write()

        folder, path = self._child(call, making=False)

        if folder_too:
            self._files.rmdir(path)
        else:
            self._files.remove(path)

        self._handles.drop(path)

        out.u32(NFS_OK)
        self._wcc(out, folder)

    def _rename(self, call: Unpacker, out: Packer) -> None:
        self._must_write()

        old_folder, old = self._child(call, making=False)
        new_folder, new = self._child(call, making=True)

        self._files.rename(old, new)
        self._handles.move(old, new)

        out.u32(NFS_OK)
        self._wcc(out, old_folder)
        self._wcc(out, new_folder)

    def _commit(self, call: Unpacker, out: Packer) -> None:
        path = self._handles.path(call.opaque())

        # Every write was on the target before it was answered.
        out.u32(NFS_OK)
        self._wcc(out, path)
        out.raw(self._verifier)

    # ---- one call ----------------------------------------------------

    def _remove_file(self, call: Unpacker, out: Packer) -> None:
        self._remove(call, out, folder_too=False)

    def _remove_folder(self, call: Unpacker, out: Packer) -> None:
        self._remove(call, out, folder_too=True)

    def _list(self, call: Unpacker, out: Packer) -> None:
        self._readdir(call, out, plus=False)

    def _list_plus(self, call: Unpacker, out: Packer) -> None:
        self._readdir(call, out, plus=True)

    def _procedures(self) -> dict:
        return {
            GETATTR: self._getattr,
            SETATTR: self._setattr,
            LOOKUP: self._lookup,
            ACCESS: self._access,
            READLINK: self._readlink,
            READ: self._read,
            WRITE: self._write,
            CREATE: self._create,
            MKDIR: self._mkdir,
            SYMLINK: self._symlink,
            REMOVE: self._remove_file,
            RMDIR: self._remove_folder,
            RENAME: self._rename,
            READDIR: self._list,
            READDIRPLUS: self._list_plus,
            FSSTAT: self._fsstat,
            FSINFO: self._fsinfo,
            PATHCONF: self._pathconf,
            COMMIT: self._commit,
        }

    def _status_of(self, procedure: int, call: Unpacker, out: Packer) -> int:
        """Run one procedure into `out` and say how it went."""

        run = self._procedures().get(procedure)

        if run is None:
            return ERR_NOTSUPP

        try:
            run(call, out)
        except Refused as refused:
            return refused.status
        except remotefs.FsError as error:
            return STATUS_OF.get(error.why, ERR_IO)

        return NFS_OK

    def nfs(self, procedure: int, call: Unpacker) -> bytes:
        """The result of one NFS call. Never raises."""

        with self._lock:
            self.calls[procedure] = self.calls.get(procedure, 0) + 1

        if procedure == NULL:
            return b""

        out = Packer()
        status = self._status_of(procedure, call, out)

        if status == NFS_OK:
            return out.bytes()

        failed = Packer().u32(status)

        return failed.absent(ABSENT_MARKS.get(procedure, 0)).bytes()


def accepted(xid: int, state: int, body: bytes = b"") -> bytes:
    """An RPC reply that the call was taken, with how it went."""

    head = struct.pack(">IIIIII", xid, REPLY, MSG_ACCEPTED, AUTH_NONE, 0, state)

    return head + body


def answer(server: Server, record: bytes) -> bytes:
    """The reply to one RPC record."""

    call = Unpacker(record)
    xid = call.u32()

    call.u32()
    call.u32()

    program = call.u32()

    call.u32()

    procedure = call.u32()

    call.u32()
    call.opaque()
    call.u32()
    call.opaque()

    if program == PROGRAM_MOUNT:
        return accepted(xid, SUCCESS, server.mount(procedure, call))

    if program == PROGRAM_NFS:
        return accepted(xid, SUCCESS, server.nfs(procedure, call))

    return accepted(xid, PROG_UNAVAIL)


def build_handler(server: Server):
    class Handler(socketserver.BaseRequestHandler):
        def read_exact(self, count: int) -> bytes:
            parts = []

            while count:
                chunk = self.request.recv(count)

                if not chunk:
                    raise ConnectionError("the client closed")

                parts.append(chunk)
                count -= len(chunk)

            return b"".join(parts)

        def read_record(self) -> bytes:
            parts = []
            total = 0

            while True:
                mark = struct.unpack(">I", self.read_exact(4))[0]
                size = mark & ~LAST_FRAGMENT
                total += size

                if total > MAX_RECORD:
                    raise ConnectionError("a record too large to be real")

                parts.append(self.read_exact(size))

                if mark & LAST_FRAGMENT:
                    return b"".join(parts)

        def handle(self) -> None:
            try:
                while True:
                    reply = answer(server, self.read_record())
                    mark = struct.pack(">I", LAST_FRAGMENT | len(reply))

                    self.request.sendall(mark + reply)
            except (ConnectionError, OSError, Refused):
                return

    return Handler


class Listener(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def listen(server: Server, port: int = 0) -> Listener:
    """Start answering on the loopback address. Port 0 picks a free one."""

    listener = Listener((LOOPBACK, port), build_handler(server))
    thread = threading.Thread(
        target=listener.serve_forever,
        name="onsite-nfsd",
        daemon=True,
    )
    thread.start()

    return listener

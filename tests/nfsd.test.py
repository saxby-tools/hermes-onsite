"""The NFS server's answers, call by call, with no kernel involved."""

from __future__ import annotations

import os
import socket
import struct

import pytest

from onsite import nfsd
from onsite import remotefs
from onsite import sftp


class DiskFiles:
    """A folder on this machine, behind the calls the server makes.

    Target paths are taken as paths under `root`.
    """

    def __init__(self, root):
        self.root = str(root)
        self.down = False

    def _real(self, path: str) -> str:
        return self.root + path

    def _guard(self) -> None:
        if self.down:
            raise remotefs.FsError(remotefs.IO, "the target is away")

    def lstat(self, path: str) -> sftp.Attrs:
        self._guard()

        try:
            found = os.lstat(self._real(path))
        except FileNotFoundError:
            raise remotefs.FsError(remotefs.NOENT) from None

        return sftp.Attrs(
            found.st_size,
            found.st_mode,
            int(found.st_mtime),
            int(found.st_atime),
        )

    def listdir(self, path: str):
        self._guard()

        names = sorted(os.listdir(self._real(path)))

        folder = path.rstrip("/")

        return [(name, self.lstat(f"{folder}/{name}")) for name in names]

    def readlink(self, path: str) -> str:
        return os.readlink(self._real(path))

    def read(self, path: str, offset: int, size: int) -> bytes:
        self._guard()

        with open(self._real(path), "rb") as handle:
            handle.seek(offset)

            return handle.read(size)

    def write(self, path: str, offset: int, data: bytes) -> None:
        with open(self._real(path), "r+b") as handle:
            handle.seek(offset)
            handle.write(data)

    def create(self, path: str, mode: int, exclusive: bool) -> None:
        flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else 0)

        try:
            os.close(os.open(self._real(path), flags, mode))
        except FileExistsError:
            raise remotefs.FsError(remotefs.EXIST) from None

    def mkdir(self, path: str, mode: int) -> None:
        try:
            os.mkdir(self._real(path), mode)
        except FileExistsError:
            raise remotefs.FsError(remotefs.EXIST) from None

    def symlink(self, path: str, target: str) -> None:
        os.symlink(target, self._real(path))

    def remove(self, path: str) -> None:
        try:
            os.remove(self._real(path))
        except FileNotFoundError:
            raise remotefs.FsError(remotefs.NOENT) from None

    def rmdir(self, path: str) -> None:
        try:
            os.rmdir(self._real(path))
        except OSError:
            raise remotefs.FsError(remotefs.NOTEMPTY) from None

    def rename(self, old: str, new: str) -> None:
        os.replace(self._real(old), self._real(new))

    def setattr(self, path, size=None, mode=None, times=None) -> None:
        if size is not None:
            os.truncate(self._real(path), size)

        if mode is not None:
            os.chmod(self._real(path), mode)

        if times is not None:
            os.utime(self._real(path), times)


def opaque(value: bytes) -> bytes:
    pad = (4 - len(value) % 4) % 4

    return struct.pack(">I", len(value)) + value + b"\0" * pad


def name(text: str) -> bytes:
    return opaque(text.encode())


def no_change() -> bytes:
    """Attributes to set that set nothing."""

    return struct.pack(">IIIIII", 0, 0, 0, 0, 0, 0)


def record(program: int, procedure: int, body: bytes, xid: int = 7) -> bytes:
    head = struct.pack(">IIIIII", xid, 0, 2, program, 3, procedure)
    no_auth = struct.pack(">IIII", 0, 0, 0, 0)

    return head + no_auth + body


class Rig:
    def __init__(self, root, writable: bool = True):
        self.files = DiskFiles(root)
        self.server = nfsd.Server(self.files, writable)
        self.root = self.mount()

    def call(self, program: int, procedure: int, body: bytes) -> nfsd.Unpacker:
        reply = nfsd.Unpacker(
            nfsd.answer(self.server, record(program, procedure, body))
        )

        assert reply.u32() == 7
        assert reply.u32() == nfsd.REPLY
        assert reply.u32() == nfsd.MSG_ACCEPTED

        reply.u32()
        reply.u32()

        assert reply.u32() == nfsd.SUCCESS

        return reply

    def nfs(self, procedure: int, body: bytes) -> nfsd.Unpacker:
        return self.call(nfsd.PROGRAM_NFS, procedure, body)

    def mount(self, path: str | None = None) -> bytes:
        reply = self.call(
            nfsd.PROGRAM_MOUNT,
            nfsd.MOUNT_MNT,
            name(self.server.export if path is None else path),
        )

        if reply.u32() != nfsd.NFS_OK:
            return b""

        return reply.opaque()

    def lookup(self, folder: bytes, child: str) -> tuple[int, bytes]:
        reply = self.nfs(nfsd.LOOKUP, opaque(folder) + name(child))
        status = reply.u32()

        if status != nfsd.NFS_OK:
            return status, b""

        return status, reply.opaque()

    def names(self, folder: bytes, limit: int = 8192) -> list[str]:
        """Every name READDIR gives, following cookies to the end."""

        found = []
        cookie = 0

        while True:
            reply = self.nfs(
                nfsd.READDIR,
                opaque(folder) + struct.pack(">QQI", cookie, 0, limit),
            )

            assert reply.u32() == nfsd.NFS_OK

            skip_post_attr(reply)
            reply.u64()

            while reply.u32():
                reply.u64()
                found.append(reply.name())
                cookie = reply.u64()

            if reply.u32():
                return found


def skip_post_attr(reply: nfsd.Unpacker) -> None:
    if reply.u32():
        skip_attrs(reply)


def skip_attrs(reply: nfsd.Unpacker) -> dict:
    kind, mode, _ = reply.u32(), reply.u32(), reply.u32()

    reply.u32()
    reply.u32()

    size = reply.u64()

    reply.u64()
    reply.u64()
    reply.u64()
    reply.u64()

    for _ in range(3):
        reply.u64()

    return {"kind": kind, "mode": mode, "size": size}


@pytest.fixture
def rig(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("hello")
    (tmp_path / "run.sh").write_text("#!/bin/sh\n")
    (tmp_path / "run.sh").chmod(0o755)

    return Rig(tmp_path)


def test_a_mount_needs_the_token_and_is_granted_once(tmp_path):
    made = Rig(tmp_path)

    assert made.root
    assert len(made.root) == nfsd.HANDLE_BYTES
    assert made.mount() == b""

    made.server.allow_mount()

    assert made.mount("/") == b""
    assert made.mount("/wrong") == b""
    assert made.mount() == made.root


def test_a_handle_the_server_did_not_give_is_stale(rig):
    reply = rig.nfs(nfsd.GETATTR, opaque(b"\0" * nfsd.HANDLE_BYTES))

    assert reply.u32() == nfsd.ERR_STALE


def test_a_file_is_found_with_its_real_mode_and_size(rig):
    status, docs = rig.lookup(rig.root, "docs")

    assert status == nfsd.NFS_OK

    status, handle = rig.lookup(rig.root, "run.sh")
    reply = rig.nfs(nfsd.GETATTR, opaque(handle))

    assert reply.u32() == nfsd.NFS_OK

    found = skip_attrs(reply)

    assert found == {"kind": nfsd.TYPE_FILE, "mode": 0o755, "size": 10}
    assert rig.lookup(docs, "a.txt")[0] == nfsd.NFS_OK
    assert rig.lookup(docs, "nope")[0] == nfsd.ERR_NOENT


def test_dot_dot_goes_up_and_never_past_the_root(rig):
    _, docs = rig.lookup(rig.root, "docs")

    assert rig.lookup(docs, "..")[1] == rig.root
    assert rig.lookup(rig.root, "..")[1] == rig.root
    assert rig.lookup(rig.root, "a/b")[0] == nfsd.ERR_INVAL


def test_a_file_is_read_in_pieces_with_an_end_mark(rig):
    _, docs = rig.lookup(rig.root, "docs")
    _, handle = rig.lookup(docs, "a.txt")

    reply = rig.nfs(nfsd.READ, opaque(handle) + struct.pack(">QI", 1, 3))

    assert reply.u32() == nfsd.NFS_OK

    skip_post_attr(reply)

    assert reply.u32() == 3
    assert reply.u32() == 0
    assert reply.opaque() == b"ell"

    reply = rig.nfs(nfsd.READ, opaque(handle) + struct.pack(">QI", 3, 99))
    reply.u32()
    skip_post_attr(reply)

    assert reply.u32() == 2
    assert reply.u32() == 1


def test_a_long_folder_is_listed_across_several_replies(rig, tmp_path):
    for number in range(300):
        (tmp_path / "docs" / f"file-{number:03}.txt").write_text("x")

    _, docs = rig.lookup(rig.root, "docs")
    found = rig.names(docs, limit=2048)

    assert len(found) == 301
    assert found == sorted(found)


def test_a_file_can_be_made_filled_renamed_and_removed(rig, tmp_path):
    body = opaque(rig.root) + name("new.txt")
    reply = rig.nfs(
        nfsd.CREATE,
        body + struct.pack(">I", nfsd.CREATE_GUARDED) + no_change(),
    )

    assert reply.u32() == nfsd.NFS_OK
    assert reply.u32() == 1

    handle = reply.opaque()
    write = opaque(handle) + struct.pack(">QII", 0, 5, nfsd.FILE_SYNC)
    reply = rig.nfs(nfsd.WRITE, write + opaque(b"fresh"))

    assert reply.u32() == nfsd.NFS_OK
    assert (tmp_path / "new.txt").read_bytes() == b"fresh"

    reply = rig.nfs(
        nfsd.RENAME,
        body + opaque(rig.root) + name("kept.txt"),
    )

    assert reply.u32() == nfsd.NFS_OK
    assert (tmp_path / "kept.txt").read_bytes() == b"fresh"

    # The handle followed the file to its new name.
    reply = rig.nfs(nfsd.GETATTR, opaque(handle))

    assert reply.u32() == nfsd.NFS_OK

    reply = rig.nfs(nfsd.REMOVE, opaque(rig.root) + name("kept.txt"))

    assert reply.u32() == nfsd.NFS_OK
    assert not (tmp_path / "kept.txt").exists()
    assert rig.nfs(nfsd.GETATTR, opaque(handle)).u32() == nfsd.ERR_STALE


def test_making_what_is_there_is_refused(rig):
    body = opaque(rig.root) + name("run.sh")
    reply = rig.nfs(
        nfsd.CREATE,
        body + struct.pack(">I", nfsd.CREATE_GUARDED) + no_change(),
    )

    assert reply.u32() == nfsd.ERR_EXIST

    reply = rig.nfs(nfsd.MKDIR, opaque(rig.root) + name("docs") + no_change())

    assert reply.u32() == nfsd.ERR_EXIST


def test_size_and_mode_are_set(rig, tmp_path):
    _, handle = rig.lookup(rig.root, "run.sh")
    wanted = struct.pack(">II", 1, 0o600) + struct.pack(">II", 0, 0)

    wanted += struct.pack(">IQ", 1, 4) + struct.pack(">II", 0, 0)

    no_guard = struct.pack(">I", 0)
    reply = rig.nfs(nfsd.SETATTR, opaque(handle) + wanted + no_guard)

    assert reply.u32() == nfsd.NFS_OK
    assert (tmp_path / "run.sh").stat().st_size == 4
    assert ((tmp_path / "run.sh").stat().st_mode & 0o777) == 0o600


def test_a_macos_sidecar_name_is_neither_made_nor_shown(rig, tmp_path):
    (tmp_path / "._run.sh").write_text("left by another mac")

    body = opaque(rig.root) + name("._new.txt")
    reply = rig.nfs(
        nfsd.CREATE,
        body + struct.pack(">I", nfsd.CREATE_UNCHECKED) + no_change(),
    )

    assert reply.u32() == nfsd.ERR_ACCES
    assert not (tmp_path / "._new.txt").exists()
    assert rig.lookup(rig.root, "._run.sh")[0] == nfsd.ERR_NOENT
    assert "._run.sh" not in rig.names(rig.root)
    assert (tmp_path / "._run.sh").exists()


def test_files_macos_leaves_on_a_volume_cannot_be_made(rig, tmp_path):
    (tmp_path / "docs" / ".DS_Store").write_text("already there")

    for litter in (".DS_Store", ".Spotlight-V100", ".fseventsd"):
        body = opaque(rig.root) + name(litter)

        reply = rig.nfs(
            nfsd.CREATE,
            body + struct.pack(">I", nfsd.CREATE_UNCHECKED) + no_change(),
        )

        assert reply.u32() == nfsd.ERR_ACCES

        reply = rig.nfs(nfsd.MKDIR, body + no_change())

        assert reply.u32() == nfsd.ERR_ACCES

    _, docs = rig.lookup(rig.root, "docs")

    assert ".DS_Store" in rig.names(docs)


def test_a_read_only_server_refuses_every_change(tmp_path):
    (tmp_path / "a.txt").write_text("x")

    made = Rig(tmp_path, writable=False)
    _, handle = made.lookup(made.root, "a.txt")
    write = opaque(handle) + struct.pack(">QII", 0, 1, nfsd.FILE_SYNC)

    assert made.nfs(nfsd.WRITE, write + opaque(b"y")).u32() == nfsd.ERR_ACCES

    reply = made.nfs(nfsd.REMOVE, opaque(made.root) + name("a.txt"))

    assert reply.u32() == nfsd.ERR_ACCES
    assert (tmp_path / "a.txt").read_text() == "x"

    reply = made.nfs(nfsd.ACCESS, opaque(handle) + struct.pack(">I", 0x3F))

    assert reply.u32() == nfsd.NFS_OK

    skip_post_attr(reply)

    looking = nfsd.ACCESS_READ | nfsd.ACCESS_LOOKUP | nfsd.ACCESS_EXECUTE

    assert reply.u32() == looking


def test_a_target_that_is_away_is_an_io_error_of_the_right_length(rig):
    _, handle = rig.lookup(rig.root, "run.sh")
    rig.files.down = True

    body = opaque(handle) + struct.pack(">QI", 0, 9)
    reply = nfsd.answer(
        rig.server,
        record(nfsd.PROGRAM_NFS, nfsd.READ, body),
    )

    # The RPC header, the status, and one "no attributes" mark.
    assert len(reply) == 24 + 4 + 4
    assert struct.unpack(">I", reply[24:28])[0] == nfsd.ERR_IO


def test_every_failure_is_as_long_as_its_procedure_says(rig):
    stale = opaque(b"\1" * nfsd.HANDLE_BYTES)

    for procedure, marks in nfsd.ABSENT_MARKS.items():
        reply = nfsd.answer(
            rig.server,
            record(nfsd.PROGRAM_NFS, procedure, stale + b"\0" * 64),
        )

        assert len(reply) == 24 + 4 + 4 * marks, procedure


def test_a_procedure_the_server_lacks_says_so(rig):
    assert rig.nfs(nfsd.MKNOD, opaque(rig.root)).u32() == nfsd.ERR_NOTSUPP


def test_another_program_is_turned_away(rig):
    reply = nfsd.answer(rig.server, record(100000, 0, b""))

    assert struct.unpack(">I", reply[20:24])[0] == nfsd.PROG_UNAVAIL


def test_it_answers_on_a_loopback_socket(rig):
    listener = nfsd.listen(rig.server)
    address = listener.server_address

    assert address[0] == "127.0.0.1"

    body = record(nfsd.PROGRAM_NFS, nfsd.GETATTR, opaque(rig.root))

    with socket.create_connection(address, timeout=5) as link:
        link.sendall(struct.pack(">I", nfsd.LAST_FRAGMENT | len(body)) + body)

        mark = struct.unpack(">I", link.recv(4))[0]
        reply = link.recv(mark & ~nfsd.LAST_FRAGMENT)

    listener.shutdown()
    listener.server_close()

    assert struct.unpack(">I", reply[24:28])[0] == nfsd.NFS_OK

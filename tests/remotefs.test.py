"""A target's files as a file system, over the real file server."""

from __future__ import annotations

import os
import shutil

import pytest

from onsite import link
from onsite import remotefs
from onsite import sftp


SERVER_PLACES = (
    "/usr/libexec/sftp-server",
    "/usr/lib/openssh/sftp-server",
    "/usr/lib/ssh/sftp-server",
    "/usr/libexec/openssh/sftp-server",
)


def find_server() -> str:
    for place in SERVER_PLACES:
        if os.path.exists(place):
            return place

    return shutil.which("sftp-server") or ""


SERVER = find_server()

pytestmark = pytest.mark.skipif(
    not SERVER,
    reason="no OpenSSH sftp-server on this machine",
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Harness:
    def __init__(self, monkeypatch):
        monkeypatch.setattr(link, "SSH_BINARY", SERVER)

        self.clock = Clock()
        self.connects = 0
        self.refuse = False
        self.clients: list[sftp.Client] = []
        self.files = remotefs.TargetFiles(self.connect, self.clock)

    def connect(self) -> sftp.Client:
        self.connects += 1

        if self.refuse:
            raise sftp.SftpError(sftp.FX_NO_LINK, "Connection refused")

        client = sftp.Client(link.SshLink([], timeout=10))
        client.start()
        self.clients.append(client)

        return client

    def kill_link(self) -> None:
        self.clients[-1]._link._process.kill()
        self.clients[-1]._link._process.wait()


@pytest.fixture
def made(monkeypatch):
    harness = Harness(monkeypatch)

    yield harness

    harness.files.close()


def test_the_link_is_opened_on_first_use_and_kept(made, tmp_path):
    assert made.connects == 0

    made.files.lstat(str(tmp_path))
    made.files.listdir(str(tmp_path))

    assert made.connects == 1
    assert made.files.connected()


def test_the_start_folder_is_the_targets_own_answer(made):
    assert made.files.home() == os.path.realpath(os.getcwd())


def test_an_answer_is_trusted_for_a_moment_then_asked_again(made, tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("one")

    assert made.files.lstat(str(path)).size == 3

    path.write_text("longer")

    assert made.files.lstat(str(path)).size == 3

    made.clock.now += remotefs.ATTR_SECONDS

    assert made.files.lstat(str(path)).size == 6


def test_a_missing_file_is_remembered_for_less_long(made, tmp_path):
    path = tmp_path / "late.txt"

    with pytest.raises(remotefs.FsError) as caught:
        made.files.lstat(str(path))

    assert caught.value.why == remotefs.NOENT

    path.write_text("x")
    made.clock.now += remotefs.MISSING_SECONDS

    assert made.files.lstat(str(path)).size == 1


def test_listing_a_folder_answers_for_its_names_too(made, tmp_path):
    (tmp_path / "a.txt").write_text("hello")

    rows = made.files.listdir(str(tmp_path))

    assert [name for name, _ in rows] == ["a.txt"]

    os.remove(tmp_path / "a.txt")

    # Still answered from the listing, with nothing sent.
    assert made.files.lstat(str(tmp_path / "a.txt")).size == 5


def test_reads_in_pieces_share_one_open_file(made, tmp_path):
    path = tmp_path / "data.bin"
    data = os.urandom(200_000)
    path.write_bytes(data)

    got = b"".join(
        made.files.read(str(path), offset, 65536)
        for offset in range(0, len(data), 65536)
    )

    assert got == data
    assert len(made.files._files) == 1


def test_a_write_changes_the_file_and_what_is_remembered(made, tmp_path):
    path = tmp_path / "note.txt"

    made.files.create(str(path), 0o600, exclusive=True)
    made.files.write(str(path), 0, b"hello")

    assert made.files.lstat(str(path)).size == 5
    assert path.read_bytes() == b"hello"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_a_new_read_only_file_can_still_be_filled(made, tmp_path):
    # What `git` does for every object it stores.
    path = tmp_path / "object"

    made.files.create(str(path), 0o444, exclusive=True)
    made.files.write(str(path), 0, b"blob")

    assert path.read_bytes() == b"blob"
    assert (path.stat().st_mode & 0o777) == 0o444


def test_the_owner_can_write_to_its_own_read_only_file(made, tmp_path):
    path = tmp_path / "locked.txt"
    path.write_text("old")
    path.chmod(0o444)

    made.files.write(str(path), 0, b"new")

    assert path.read_bytes() == b"new"
    assert (path.stat().st_mode & 0o777) == 0o444


def test_making_what_is_there_says_so(made, tmp_path):
    path = str(tmp_path / "made")

    made.files.mkdir(path, 0o755)

    with pytest.raises(remotefs.FsError) as caught:
        made.files.mkdir(path, 0o755)

    assert caught.value.why == remotefs.EXIST

    made.files.create(path + "/x", 0o644, exclusive=True)

    with pytest.raises(remotefs.FsError) as caught:
        made.files.create(path + "/x", 0o644, exclusive=True)

    assert caught.value.why == remotefs.EXIST


def test_a_folder_with_something_in_it_is_not_removed(made, tmp_path):
    path = str(tmp_path / "made")

    made.files.mkdir(path, 0o755)
    made.files.create(path + "/x", 0o644, exclusive=False)

    with pytest.raises(remotefs.FsError) as caught:
        made.files.rmdir(path)

    assert caught.value.why == remotefs.NOTEMPTY

    made.files.remove(path + "/x")
    made.files.rmdir(path)

    assert not os.path.exists(path)


def test_a_save_by_rename_replaces_the_file(made, tmp_path):
    old = str(tmp_path / "note.txt.tmp")
    new = str(tmp_path / "note.txt")

    made.files.create(new, 0o644, exclusive=False)
    made.files.write(new, 0, b"first")
    made.files.create(old, 0o644, exclusive=False)
    made.files.write(old, 0, b"second")

    made.files.rename(old, new)

    assert made.files.read(new, 0, 100) == b"second"

    with pytest.raises(remotefs.FsError):
        made.files.lstat(old)


def test_size_and_mode_can_be_set(made, tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("0123456789")

    made.files.setattr(str(path), size=4, mode=0o640)

    found = made.files.lstat(str(path))

    assert found.size == 4
    assert (found.mode & 0o777) == 0o640


def test_a_dropped_link_is_an_error_then_a_fresh_link(made, tmp_path):
    made.files.lstat(str(tmp_path))
    made.kill_link()

    with pytest.raises(remotefs.FsError) as caught:
        made.files.listdir(str(tmp_path))

    assert caught.value.why == remotefs.IO

    # Nothing is tried while the target is given time to come back.
    with pytest.raises(remotefs.FsError):
        made.files.listdir(str(tmp_path))

    assert made.connects == 1

    made.clock.now += remotefs.RETRY_SECONDS

    assert made.files.listdir(str(tmp_path)) == []
    assert made.connects == 2


def test_a_target_that_refuses_is_not_asked_in_a_loop(made, tmp_path):
    made.refuse = True

    for _ in range(5):
        with pytest.raises(remotefs.FsError) as caught:
            made.files.lstat(str(tmp_path))

        assert caught.value.why == remotefs.IO
        assert "Connection refused" in str(caught.value)

    assert made.connects == 1


def test_open_files_are_closed_when_left_alone(made, tmp_path):
    for number in range(3):
        path = tmp_path / f"{number}.txt"
        path.write_text("x")
        made.files.read(str(path), 0, 1)

    assert len(made.files._files) == 3

    made.clock.now += remotefs.HANDLE_SECONDS
    made.files.read(str(tmp_path / "0.txt"), 0, 1)

    assert len(made.files._files) == 1

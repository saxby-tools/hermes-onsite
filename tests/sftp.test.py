"""The SFTP client, against the real OpenSSH file server.

`sftp-server` speaks the protocol on its input and output, which is
all `ssh -s sftp` gives a client. So it can be run here with no
network and no login, and the client is tested against the same
program a target runs.
"""

from __future__ import annotations

import os
import shutil
import stat

import pytest

from onsite import link
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


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(link, "SSH_BINARY", SERVER)

    made = sftp.Client(link.SshLink([], timeout=10))
    made.start()

    yield made

    made.close()


def put(client, path: str, data: bytes, mode: int = 0o644) -> None:
    flags = sftp.OPEN_WRITE | sftp.OPEN_CREATE | sftp.OPEN_TRUNCATE
    handle = client.open(path, flags, mode)

    client.write(handle, 0, data)
    client.close_handle(handle)


def get(client, path: str) -> bytes:
    handle = client.open(path, sftp.OPEN_READ)
    data = client.read(handle, 0, 10_000_000)

    client.close_handle(handle)

    return data


def test_the_server_says_where_a_login_lands(client):
    assert client.realpath(".") == os.path.realpath(os.getcwd())


def test_a_file_written_is_the_file_read(client, tmp_path):
    path = str(tmp_path / "note.txt")
    data = os.urandom(100_000)

    put(client, path, data)

    assert get(client, path) == data
    assert (tmp_path / "note.txt").read_bytes() == data


def test_a_read_can_start_anywhere(client, tmp_path):
    path = str(tmp_path / "note.txt")

    put(client, path, b"0123456789")

    handle = client.open(path, sftp.OPEN_READ)

    assert client.read(handle, 3, 4) == b"3456"
    assert client.read(handle, 8, 100) == b"89"
    assert client.read(handle, 50, 10) == b""

    client.close_handle(handle)


def test_a_write_can_land_in_the_middle(client, tmp_path):
    path = str(tmp_path / "note.txt")

    put(client, path, b"0123456789")

    handle = client.open(path, sftp.OPEN_WRITE)
    client.write(handle, 4, b"XY")
    client.close_handle(handle)

    assert get(client, path) == b"0123XY6789"


def test_a_folder_is_listed_with_what_each_name_is(client, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "run.sh").write_text("#!/bin/sh\n")
    (tmp_path / "run.sh").chmod(0o755)

    rows = dict(client.listdir(str(tmp_path)))

    assert sorted(rows) == ["a.txt", "run.sh", "sub"]
    assert rows["sub"].is_dir
    assert rows["a.txt"].size == 5
    assert stat.S_IMODE(rows["run.sh"].mode) == 0o755
    assert not rows["a.txt"].is_dir


def test_a_link_is_seen_as_a_link_and_can_be_read(client, tmp_path):
    (tmp_path / "real.txt").write_text("x")
    client.symlink("real.txt", str(tmp_path / "alias"))

    assert client.lstat(str(tmp_path / "alias")).is_link
    assert not client.stat(str(tmp_path / "alias")).is_link
    assert client.readlink(str(tmp_path / "alias")) == "real.txt"
    assert os.readlink(tmp_path / "alias") == "real.txt"


def test_a_rename_replaces_what_was_there(client, tmp_path):
    old = str(tmp_path / "new.tmp")
    new = str(tmp_path / "note.txt")

    put(client, old, b"second")
    put(client, new, b"first")

    client.rename(old, new)

    assert get(client, new) == b"second"
    assert not os.path.exists(old)


def test_size_mode_and_times_can_be_set(client, tmp_path):
    path = str(tmp_path / "note.txt")

    put(client, path, b"0123456789")

    client.setstat(path, size=4)
    client.setstat(path, mode=0o600)
    client.setstat(path, times=(1_700_000_000, 1_700_000_100))

    found = client.lstat(path)

    assert found.size == 4
    assert stat.S_IMODE(found.mode) == 0o600
    assert found.mtime == 1_700_000_100


def test_folders_and_files_can_be_made_and_removed(client, tmp_path):
    folder = str(tmp_path / "made")

    client.mkdir(folder, 0o755)
    put(client, folder + "/x", b"1")

    with pytest.raises(sftp.SftpError):
        client.rmdir(folder)

    client.remove(folder + "/x")
    client.rmdir(folder)

    assert not os.path.exists(folder)


def test_a_missing_file_is_told_apart_from_other_failures(client, tmp_path):
    with pytest.raises(sftp.SftpError) as caught:
        client.lstat(str(tmp_path / "nope"))

    assert caught.value.code == sftp.FX_NO_SUCH_FILE
    assert client.alive


def test_a_link_that_dies_ends_the_conversation(client, tmp_path):
    client._link.close()

    with pytest.raises(sftp.SftpError) as caught:
        client.lstat(str(tmp_path))

    assert caught.value.code == sftp.FX_NO_LINK
    assert not client.alive

    # Nothing more is sent down a link that lost its place.
    with pytest.raises(sftp.SftpError):
        client.realpath(".")


def test_a_name_that_is_not_utf8_survives_the_round_trip(client, tmp_path):
    odd = os.fsdecode(os.fsencode(str(tmp_path)) + b"/caf\xe9.txt")

    try:
        put(client, odd, b"x")
    except sftp.SftpError:
        pytest.skip("this file system refuses names that are not UTF-8")

    names = [name for name, _ in client.listdir(str(tmp_path))]

    assert odd.rsplit("/", 1)[1] in names

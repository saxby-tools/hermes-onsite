"""Starting and stopping a target's mount, without mounting anything."""

from __future__ import annotations

import os

from pathlib import Path

import pytest

from onsite import config
from onsite import mounts
from onsite import remote


SPEC = config.TargetSpec(
    name="laptop",
    kind=config.KIND_SSH,
    host="laptop.example",
    user="dev",
    connect_timeout=3,
)

MAC_MOUNT = """\
/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
devfs on /dev (devfs, local, nobrowse)
127.0.0.1:/3f9a on /data/onsite/mount/laptop (nfs, nodev, mounted by dev)
//dev@nas.local/Share Name on /Volumes/Share Name (smbfs, nodev, nosuid)
"""

PROC_MOUNTS = """\
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
dev@laptop.example:/ /data/onsite/mount/laptop fuse.sshfs rw,nosuid 0 0
/dev/sdb1 /media/dev/My\\040Disk ext4 rw 0 0
"""


def data_dir(tmp_path: Path, profile: str = "") -> Path:
    """The plugin's data folder, laid out the way Hermes lays it out.

    The Hermes home is `tmp_path / "hermes"`. With no profile named,
    it is the default profile's folder.
    """

    hermes = tmp_path / "hermes"

    if not profile:
        return hermes / "plugin-data" / "onsite"

    return hermes / "profiles" / profile / "plugin-data" / "onsite"


@pytest.fixture
def steps(monkeypatch):
    """What `ensure` went on to do, with nothing mounted or stopped."""

    done = []

    monkeypatch.setattr(
        mounts,
        "take_down",
        lambda root, state, system: done.append("take down"),
    )
    monkeypatch.setattr(
        mounts,
        "ensure_linux",
        lambda spec, root, state: done.append("mount"),
    )
    monkeypatch.setattr(
        mounts,
        "ensure_mac",
        lambda spec, root, state: done.append("mount"),
    )

    return done


def test_the_default_root_is_outside_the_hermes_home(tmp_path):
    home = tmp_path / "home"
    data = home / ".hermes" / "profiles" / "work" / "plugin-data" / "onsite"

    root = mounts.default_root(data, "laptop", home=home)

    share = home / ".local" / "share" / "onsite"
    assert root == share / "mount" / "work" / "laptop"
    assert home / ".hermes" not in root.parents


def test_the_default_profile_gets_a_folder_name_without_a_dot(tmp_path):
    home = tmp_path / "home"
    data = home / ".hermes" / "plugin-data" / "onsite"

    root = mounts.default_root(data, "laptop", home=home)

    assert root.parent.name == "hermes"


def test_the_hermes_home_is_found_from_either_kind_of_profile(tmp_path):
    hermes = tmp_path / "hermes"

    assert mounts.hermes_root(data_dir(tmp_path)) == hermes
    assert mounts.hermes_root(data_dir(tmp_path, "work")) == hermes


def test_the_default_place_in_an_ordinary_home_folder_is_used(
    tmp_path,
    monkeypatch,
    steps,
):
    home = tmp_path / "home"
    data = home / ".hermes" / "profiles" / "work" / "plugin-data" / "onsite"

    monkeypatch.setenv("HOME", str(home))

    mounts.ensure(SPEC, mounts.default_root(data, "laptop"), data)

    assert steps == ["mount"]


# Hermes' own container image runs the default profile with the
# Hermes home as its home folder, and a named profile with a home
# folder inside the profile's own folder.
@pytest.mark.parametrize("profile, home_parts", [
    ("", ()),
    ("work", ("profiles", "work", "home")),
])
@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_a_home_folder_inside_the_hermes_home_gets_no_default_mount(
    tmp_path,
    monkeypatch,
    steps,
    profile,
    home_parts,
    system,
):
    hermes = tmp_path / "hermes"
    data = data_dir(tmp_path, profile)

    monkeypatch.setenv("HOME", str(hermes.joinpath(*home_parts)))

    root = mounts.default_root(data, "laptop")

    with pytest.raises(mounts.MountError) as caught:
        mounts.ensure(SPEC, root, data, system=system)

    said = str(caught.value)

    assert "laptop (dev@laptop.example)" in said
    assert f"inside the Hermes home, {hermes}" in said
    assert "Set this target's `mount`" in said
    assert "targets.laptop.mount" in said
    assert "Machines page" in said
    assert steps == ["take down"]
    assert not root.exists()


@pytest.mark.parametrize("parts", [
    ("mounts", "laptop"),
    ("profiles", "work", "mounts", "laptop"),
    ("profiles", "another", "home", "laptop"),
    (),
])
def test_a_named_place_inside_the_hermes_home_is_refused(
    tmp_path,
    steps,
    parts,
):
    hermes = tmp_path / "hermes"
    root = hermes.joinpath(*parts)

    with pytest.raises(mounts.MountError) as caught:
        mounts.ensure(SPEC, root, data_dir(tmp_path, "work"), system="Linux")

    assert f"cannot be mounted at {root}" in str(caught.value)
    assert "inside the Hermes home" in str(caught.value)
    assert steps == ["take down"]


def test_a_place_is_judged_by_where_a_link_leads(tmp_path, steps):
    inside = tmp_path / "hermes" / "mounts"
    link = tmp_path / "shortcut"

    inside.mkdir(parents=True)
    link.symlink_to(inside)

    with pytest.raises(mounts.MountError) as caught:
        mounts.ensure(SPEC, link / "laptop", data_dir(tmp_path), system="Linux")

    assert "inside the Hermes home" in str(caught.value)
    assert steps == ["take down"]


def test_a_place_beside_the_hermes_home_is_used(tmp_path, steps):
    # The name starts the same way. The folder is not inside.
    root = tmp_path / "hermes-mounts" / "laptop"

    mounts.ensure(SPEC, root, data_dir(tmp_path, "work"), system="Linux")

    assert steps == ["mount"]


def test_taking_a_mac_mount_down_stops_its_helper_too(tmp_path, monkeypatch):
    root = tmp_path / "m" / "root"
    state = mounts.state_file(data_dir(tmp_path), "laptop")
    done = []

    mounts.write_state(state, {"pid": 12})
    monkeypatch.setattr(mounts, "mount_points", lambda system: {str(root)})
    monkeypatch.setattr(
        mounts,
        "stop_helper",
        lambda found: done.append(("stop", found["pid"])),
    )
    monkeypatch.setattr(
        mounts,
        "unmount",
        lambda path, force=False: done.append(("unmount", path, force)),
    )

    mounts.stop(SPEC, root, data_dir(tmp_path), system="Darwin")

    assert done == [("stop", 12), ("unmount", root, True)]
    assert not state.exists()


def test_mount_points_are_read_from_macos_own_list():
    points = mounts.parse_mac_mounts(MAC_MOUNT)

    assert "/data/onsite/mount/laptop" in points
    assert "/Volumes/Share Name" in points
    assert "/" in points


def test_mount_points_are_read_from_proc_mounts():
    points = mounts.parse_proc_mounts(PROC_MOUNTS)

    assert "/data/onsite/mount/laptop" in points
    assert "/media/dev/My Disk" in points


def test_the_nfs_mount_is_soft_and_on_the_loopback_address():
    command = mounts.build_nfs_command(40123, "/token", Path("/mnt/x"))

    assert command[0] == "/sbin/mount_nfs"
    assert command[-2:] == ["127.0.0.1:/token", "/mnt/x"]

    options = command[command.index("-o") + 1].split(",")

    assert "port=40123" in options
    assert "mountport=40123" in options
    assert "soft" in options
    assert "vers=3" in options


def test_the_helper_is_given_the_sftp_link_after_a_separator(tmp_path):
    command = mounts.build_helper_command(
        SPEC,
        tmp_path / "root",
        tmp_path / "state.json",
    )
    at = command.index("--")

    assert command[1].endswith("mountd.py")
    assert command[at + 1:] == remote.build_sftp_arguments(SPEC)
    assert command[at - 1] == str(tmp_path / "root")


def test_the_sftp_link_notices_a_sleeping_target_fast():
    arguments = remote.build_sftp_arguments(SPEC)

    # OpenSSH keeps the first value it sees for an option.
    first = arguments.index("ServerAliveInterval=5")
    later = arguments.index("ServerAliveInterval=15")

    assert first < later
    assert arguments[-3:] == ["dev@laptop.example", "-s", "sftp"]
    assert "BatchMode=yes" in arguments


def test_sshfs_carries_the_targets_own_ssh_settings():
    spec = config.TargetSpec(
        name="laptop",
        kind=config.KIND_SSH,
        host="laptop.example",
        user="dev",
        port=2222,
        key="/keys/id",
        known_hosts="/keys/known",
        connect_timeout=3,
    )
    command = mounts.build_sshfs_command(spec, Path("/mnt/x"))

    assert command[:3] == ["sshfs", "dev@laptop.example:/", "/mnt/x"]

    options = command[-1].split(",")

    # A dead mount that stays in place stops Hermes' whole backend.
    assert "reconnect" not in options
    assert "BatchMode=yes" in options
    assert "port=2222" in options
    assert "IdentityFile=/keys/id" in options
    assert "UserKnownHostsFile=/keys/known" in options


@pytest.mark.parametrize("text, wanted", [
    ('ID=ubuntu\nID_LIKE=debian\n', "sudo apt install sshfs"),
    ('ID=debian\n', "sudo apt install sshfs"),
    ('ID=fedora\n', "sudo dnf install fuse-sshfs"),
    (
        'ID="rocky"\nID_LIKE="rhel centos fedora"\n',
        "sudo dnf install fuse-sshfs",
    ),
    ('ID=arch\n', "sudo pacman -S sshfs"),
    ('ID=manjaro\nID_LIKE=arch\n', "sudo pacman -S sshfs"),
    ('ID=alpine\n', "sudo apk add sshfs"),
    (
        'ID="opensuse-leap"\nID_LIKE="suse opensuse"\n',
        "sudo zypper install sshfs",
    ),
])
def test_the_install_line_fits_the_distribution(text, wanted):
    assert mounts.install_line(text) == wanted


def test_an_unknown_distribution_still_gets_an_answer():
    assert "sshfs" in mounts.install_line("ID=plan9\n")
    assert "sshfs" in mounts.install_line("")


def test_a_linux_server_without_sshfs_is_told_what_to_run(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(mounts, "mount_points", lambda system: set())
    monkeypatch.setattr(mounts.shutil, "which", lambda name: None)
    monkeypatch.setattr(mounts, "read_os_release", lambda: "ID=ubuntu\n")

    with pytest.raises(mounts.MountError) as caught:
        mounts.ensure(
            SPEC,
            tmp_path / "root",
            data_dir(tmp_path),
            system="Linux",
        )

    said = str(caught.value)

    assert "laptop (dev@laptop.example)" in said
    assert "has no `sshfs`" in said
    assert "sudo apt install sshfs" in said
    assert not (tmp_path / "root").exists()


def test_a_linux_mount_that_is_up_is_left_alone(tmp_path, monkeypatch):
    root = tmp_path / "root"
    ran = []

    mounts.write_state(
        mounts.state_file(data_dir(tmp_path), "laptop"),
        {"link": remote.build_sftp_arguments(SPEC), "home": "/home/dev"},
    )
    monkeypatch.setattr(mounts, "mount_points", lambda system: {str(root)})
    monkeypatch.setattr(mounts.subprocess, "run", lambda *a, **k: ran.append(a))

    found = mounts.ensure(SPEC, root, data_dir(tmp_path), system="Linux")

    assert found.root == root
    assert ran == []


def test_what_sshfs_says_is_what_the_person_is_told(tmp_path, monkeypatch):
    class Done:
        returncode = 1
        stdout = ""
        stderr = "read: Connection reset by peer\n"

    monkeypatch.setattr(mounts, "mount_points", lambda system: set())
    monkeypatch.setattr(mounts.shutil, "which", lambda name: "/usr/bin/sshfs")
    monkeypatch.setattr(mounts.subprocess, "run", lambda *a, **k: Done())

    with pytest.raises(mounts.MountError) as caught:
        mounts.ensure(
            SPEC,
            tmp_path / "root",
            data_dir(tmp_path),
            system="Linux",
        )

    assert "Connection reset by peer" in str(caught.value)


def test_a_system_with_no_way_to_mount_says_so(tmp_path):
    with pytest.raises(mounts.MountError) as caught:
        mounts.ensure(
            SPEC,
            tmp_path / "root",
            data_dir(tmp_path),
            system="Windows",
        )

    assert "Windows" in str(caught.value)


def test_the_folder_above_a_mount_is_closed_to_other_users(tmp_path):
    root = tmp_path / "mount" / "laptop"

    mounts.prepare_root(root)

    assert root.is_dir()
    assert (root.parent.stat().st_mode & 0o777) == 0o700


def test_a_folder_that_was_already_there_is_never_changed(tmp_path):
    parent = tmp_path / "somebodys-home"
    parent.mkdir()
    os.chmod(parent, 0o755)

    mounts.prepare_root(parent / "laptop")

    assert (parent / "laptop").is_dir()
    assert (parent.stat().st_mode & 0o777) == 0o755


def test_a_folder_that_cannot_be_made_is_an_error_with_its_reason(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("")

    with pytest.raises(mounts.MountError) as caught:
        mounts.prepare_root(blocker / "mount" / "laptop")

    assert "cannot be made on this server" in str(caught.value)


def test_a_mac_never_mounts_where_other_accounts_can_look(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "shared"
    parent.mkdir()
    os.chmod(parent, 0o755)
    started = []

    monkeypatch.setattr(mounts, "mount_points", lambda system: set())
    monkeypatch.setattr(
        mounts,
        "start_helper",
        lambda *args: started.append(args) or {},
    )

    with pytest.raises(mounts.MountError) as caught:
        mounts.ensure(
            SPEC,
            parent / "laptop",
            data_dir(tmp_path),
            system="Darwin",
        )

    assert "other accounts on this server can open" in str(caught.value)
    assert str(parent) in str(caught.value)
    assert started == []


def test_state_is_replaced_whole_and_survives_garbage(tmp_path):
    path = tmp_path / "state" / "laptop.json"

    assert mounts.read_state(path) == {}

    mounts.write_state(path, {"pid": 12, "home": "/home/dev"})

    assert mounts.read_state(path) == {"pid": 12, "home": "/home/dev"}
    assert [item.name for item in path.parent.iterdir()] == ["laptop.json"]

    path.write_text("{not json")

    assert mounts.read_state(path) == {}

    path.write_text("[1, 2]")

    assert mounts.read_state(path) == {}


def test_only_a_real_process_counts_as_alive():
    assert mounts.is_alive(os.getpid())
    assert not mounts.is_alive(0)
    assert not mounts.is_alive(None)
    assert not mounts.is_alive("12")
    assert not mounts.is_alive(2 ** 22 + 12345)


def test_a_helper_that_reports_a_failure_becomes_an_error(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(mounts, "mount_points", lambda system: set())
    monkeypatch.setattr(
        mounts,
        "start_helper",
        lambda spec, root, state: {"error": "Operation not permitted"},
    )

    with pytest.raises(mounts.MountError) as caught:
        mounts.ensure(
            SPEC,
            tmp_path / "m" / "root",
            data_dir(tmp_path),
            system="Darwin",
        )

    assert "Operation not permitted" in str(caught.value)
    assert "laptop (dev@laptop.example)" in str(caught.value)


def test_a_mac_mount_that_is_up_is_answered_from_the_state_file(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "m" / "root"
    started = []

    mounts.write_state(
        mounts.state_file(data_dir(tmp_path), "laptop"),
        {
            "pid": os.getpid(),
            "home": "/home/dev",
            "link": remote.build_sftp_arguments(SPEC),
            "error": "",
        },
    )
    monkeypatch.setattr(mounts, "mount_points", lambda system: {str(root)})
    monkeypatch.setattr(
        mounts,
        "start_helper",
        lambda *args: started.append(args) or {},
    )

    found = mounts.ensure(SPEC, root, data_dir(tmp_path), system="Darwin")

    assert found == mounts.Mounted(root, "/home/dev")
    assert started == []


def test_a_mount_made_for_another_address_is_replaced(tmp_path, monkeypatch):
    root = tmp_path / "m" / "root"
    moved = config.TargetSpec(
        name="laptop",
        kind=config.KIND_SSH,
        host="new-address.example",
        user="dev",
        connect_timeout=3,
    )
    steps = []

    mounts.write_state(
        mounts.state_file(data_dir(tmp_path), "laptop"),
        {
            "pid": os.getpid(),
            "home": "/home/dev",
            "link": remote.build_sftp_arguments(SPEC),
            "error": "",
        },
    )
    monkeypatch.setattr(mounts, "mount_points", lambda system: {str(root)})
    monkeypatch.setattr(
        mounts,
        "stop_helper",
        lambda found: steps.append("stop"),
    )
    monkeypatch.setattr(
        mounts,
        "unmount",
        lambda path, force=False: steps.append("unmount"),
    )
    monkeypatch.setattr(
        mounts,
        "start_helper",
        lambda spec, path, state: steps.append(spec.host)
        or {"pid": 1, "home": "/home/dev"},
    )

    mounts.ensure(moved, root, data_dir(tmp_path), system="Darwin")

    assert steps == ["stop", "unmount", "new-address.example"]


def test_a_linux_mount_for_another_address_is_remounted(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "m" / "root"
    steps = []

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(command, **kwargs):
        steps.append(command[0])

        return Done()

    mounts.write_state(
        mounts.state_file(data_dir(tmp_path), "laptop"),
        {"link": ["old", "arguments"], "home": "/home/old"},
    )
    monkeypatch.setattr(mounts, "mount_points", lambda system: {str(root)})
    monkeypatch.setattr(mounts.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(mounts.subprocess, "run", run)
    monkeypatch.setattr(mounts, "ask_home", lambda spec: "/home/dev")

    found = mounts.ensure(SPEC, root, data_dir(tmp_path), system="Linux")

    assert steps == ["fusermount3", "sshfs"]
    assert found == mounts.Mounted(root, "/home/dev")

    kept = mounts.read_state(mounts.state_file(data_dir(tmp_path), "laptop"))

    assert kept["link"] == remote.build_sftp_arguments(SPEC)


def test_a_folder_that_is_there_is_found_at_once(tmp_path):
    slept = []

    seen = mounts.look_for_folder(tmp_path, sleep=slept.append)

    assert seen == mounts.FOLDER_THERE
    assert slept == []


def test_a_folder_that_is_not_there_is_not_waited_for(tmp_path):
    slept = []

    (tmp_path / "file.txt").write_text("x")

    for path in (tmp_path / "nope", tmp_path / "file.txt"):
        seen = mounts.look_for_folder(path, sleep=slept.append)

        assert seen == mounts.FOLDER_MISSING

    assert slept == []


def test_a_target_that_just_woke_is_given_a_moment(tmp_path, monkeypatch):
    answers = [OSError(5, "Input/output error")] * 3
    real_stat = os.stat
    slept = []

    def flaky(path):
        if answers:
            raise answers.pop()

        return real_stat(path)

    monkeypatch.setattr(mounts.os, "stat", flaky)

    seen = mounts.look_for_folder(tmp_path, sleep=slept.append)

    assert seen == mounts.FOLDER_THERE
    assert len(slept) == 3


def test_a_target_that_stays_away_is_given_up_on(tmp_path, monkeypatch):
    now = [0.0]

    def refuse(path):
        raise OSError(60, "Operation timed out")

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(mounts.os, "stat", refuse)

    seen = mounts.look_for_folder(tmp_path, clock=lambda: now[0], sleep=sleep)

    assert seen == mounts.FOLDER_AWAY
    assert now[0] >= mounts.WAKE_SECONDS


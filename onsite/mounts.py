"""Putting a target's files where the host can see them.

Hermes only runs a session in a folder on the host, and its file tree
and folder picker read the host's disk. So a target's whole disk is
mounted in a folder on the host, the mount root, and a session opened
under it runs in the real folder on the target.

How it is mounted depends on the host's system:

- macOS mounts NFS itself, as a normal user. The plugin runs a small
  NFS server on the loopback address, fed from the target over SFTP,
  in a helper process of its own (`mountd.py`). Nothing is installed.
- Linux has no such door without root. It needs FUSE and `sshfs`,
  which the person installs. When they are missing the error says so
  and gives the line to run for that distribution.

There is one way in and no fallback. A target that cannot be mounted
is an error with its fix, never a folder of empty stand-ins.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import fcntl
import json
import os
import platform
import shutil
import signal
import stat as statmod
import subprocess
import sys
import time

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import sftp
from .config import DEFAULT_SSH_PORT
from .config import PLUGIN_ID
from .config import TargetSpec
from .link import LinkError
from .link import SshLink
from .remote import SFTP_ALIVE_COUNT
from .remote import SFTP_ALIVE_INTERVAL
from .remote import build_sftp_arguments


SYSTEM_MAC = "Darwin"
SYSTEM_LINUX = "Linux"

# Where mounts live, under the home folder of the account Hermes runs
# as: ~/.local/share/onsite/mount/<profile>/<target>. Not inside the
# Hermes home. Deleting a profile deletes everything below its
# folder, and that would reach through a live mount into the
# target's disk. A Hermes home kept in git also makes the app hide
# the mounted files that the repository ignores. Not a cache or a
# temporary folder either: whatever empties those would reach
# through the mount the same way.
#
# Some set-ups put that home folder inside the Hermes home. Hermes'
# own container image does. There the place above is unsafe, and
# `ensure` refuses it. No second place is guessed.
SHARE_PARTS = (".local", "share")
MOUNTS_DIR = "mount"
UNNAMED_PROFILE = "hermes"

# A named profile's folder is <Hermes home>/profiles/<name>.
PROFILES_DIR = "profiles"
STATE_DIR = "mount-state"
HELPER_FILE = "mountd.py"

MOUNT_NFS = "/sbin/mount_nfs"
UMOUNT = "/sbin/umount"
MOUNT_LIST = "/sbin/mount"
PROC_MOUNTS = "/proc/mounts"
OS_RELEASE = "/etc/os-release"

SSHFS = "sshfs"
FUSERMOUNTS = ("fusermount3", "fusermount")

# `soft` makes a read fail when the server stops answering. Without
# it everything that touches the mount would hang for as long as a
# target sleeps. Times are in tenths of a second.
NFS_OPTIONS = (
    "tcp",
    "vers=3",
    "nolocks",
    "locallocks",
    "soft",
    "timeo=30",
    "retrans=2",
    "nobrowse",
)

HELPER_WAIT_SECONDS = 12.0
HELPER_POLL_SECONDS = 0.05
STOP_WAIT_SECONDS = 5.0
COMMAND_SECONDS = 20
HOME_SECONDS = 10
PRIVATE_DIR_MODE = 0o700

# The bits of a folder's mode that let anyone but its owner in.
OTHERS_MODE = 0o077

# The line that installs sshfs, by the names a distribution gives
# itself in /etc/os-release.
INSTALL_LINES = (
    (("debian", "ubuntu"), "sudo apt install sshfs"),
    (("fedora", "rhel", "centos"), "sudo dnf install fuse-sshfs"),
    (("arch",), "sudo pacman -S sshfs"),
    (("alpine",), "sudo apk add sshfs"),
    (("suse", "opensuse"), "sudo zypper install sshfs"),
    (("nixos",), "nix-env -iA nixos.sshfs"),
)
INSTALL_FALLBACK = "install the `sshfs` package with your package manager"


# What looking for a folder under a mount can find.
FOLDER_THERE = "there"
FOLDER_MISSING = "missing"
FOLDER_AWAY = "away"

# A target that was away is given this long to answer again. Its
# mount notices it is back within a few seconds, not at once.
WAKE_SECONDS = 8.0
WAKE_POLL_SECONDS = 0.5


class MountError(RuntimeError):
    """A target's files could not be mounted. The message says why
    and, where there is one, what to run."""


@dataclass(frozen=True)
class Mounted:
    """A target's disk, mounted on the host."""

    root: Path
    home: str


def profile_name(data_dir: Path) -> str:
    """The folder name of the Hermes home that holds `data_dir`.

    `data_dir` is <Hermes home>/plugin-data/onsite. The name keeps two
    profiles with a target of the same name on separate folders. A
    leading dot is dropped, so `.hermes` gives `hermes`.
    """

    name = data_dir.parent.parent.name.lstrip(".")

    if not name:
        return UNNAMED_PROFILE

    return name


def default_root(
    data_dir: Path,
    name: str,
    home: Path | None = None,
) -> Path:
    """Where a target is mounted when the settings name no place.

    In the standard folder for a program's own data, so nothing new
    appears at the top of anybody's home folder.

    This only builds the path. `ensure` is what refuses it when the
    home folder is inside the Hermes home.
    """

    if home is None:
        home = Path.home()

    share = home.joinpath(*SHARE_PARTS)

    return share / PLUGIN_ID / MOUNTS_DIR / profile_name(data_dir) / name


def hermes_root(data_dir: Path) -> Path:
    """The folder Hermes keeps everything in, for every profile.

    `data_dir` is <a profile's folder>/plugin-data/onsite. The default
    profile's folder is that top folder itself. A named profile's is
    <top folder>/profiles/<name>. Hermes tells the two apart by the
    same folder name.
    """

    own = data_dir.parent.parent

    if own.parent.name == PROFILES_DIR:
        return own.parent.parent

    return own


def is_inside(place: Path, folder: Path) -> bool:
    """Whether `place` is `folder`, or anywhere below it.

    Links are followed first, so a place reached through a link is
    judged by where it really is. Only the folder above `place` is
    followed, never `place` itself. It may be a mount whose target
    sleeps, and asking a dead mount about itself can hang.
    """

    real = Path(os.path.realpath(place.parent)) / place.name
    outer = Path(os.path.realpath(folder))

    if real == outer:
        return True

    return outer in real.parents


def state_file(data_dir: Path, name: str) -> Path:
    return data_dir / STATE_DIR / f"{name}.json"


def read_state(path: Path) -> dict:
    try:
        found = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}

    return found if isinstance(found, dict) else {}


def write_state(path: Path, state: dict) -> None:
    """Replace the state file in one step, so no reader sees half."""

    path.parent.mkdir(parents=True, exist_ok=True)

    draft = path.with_name(path.name + f".{os.getpid()}.tmp")

    draft.write_text(json.dumps(state))
    os.replace(draft, path)


@contextmanager
def locked(path: Path):
    """One process at a time may start or stop a target's mount."""

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path.with_suffix(".lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)

        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def is_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    return True


def parse_mac_mounts(text: str) -> set[str]:
    """The mount points in the output of macOS' `mount`."""

    points = set()

    for line in text.splitlines():
        _, found, rest = line.partition(" on ")

        if not found:
            continue

        point, opened, _ = rest.rpartition(" (")

        if opened:
            points.add(point)

    return points


def parse_proc_mounts(text: str) -> set[str]:
    """The mount points in Linux' /proc/mounts."""

    points = set()

    for line in text.splitlines():
        fields = line.split(" ")

        if len(fields) < 2:
            continue

        # A space in a path is written as \\040 there.
        points.add(fields[1].replace("\\040", " "))

    return points


def mount_points(system: str) -> set[str]:
    """Every mount point on this host, read without touching one.

    Asking a dead mount about itself can hang. The system's own list
    cannot.
    """

    if system == SYSTEM_LINUX:
        try:
            return parse_proc_mounts(Path(PROC_MOUNTS).read_text())
        except OSError:
            return set()

    try:
        done = subprocess.run(
            [MOUNT_LIST],
            capture_output=True,
            text=True,
            timeout=COMMAND_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    return parse_mac_mounts(done.stdout)


def is_mounted(root: Path, system: str) -> bool:
    return str(root) in mount_points(system)


def look_for_folder(
    folder: Path,
    clock=time.monotonic,
    sleep=time.sleep,
) -> str:
    """Whether a folder is there on a mounted target.

    A folder that is not there is said at once. A target that does
    not answer is asked again for a few seconds, because one that
    just woke up fails the first reads through its mount.
    """

    until = clock() + WAKE_SECONDS

    while True:
        try:
            found = os.stat(folder)
        except (FileNotFoundError, NotADirectoryError):
            return FOLDER_MISSING
        except OSError:
            if clock() >= until:
                return FOLDER_AWAY

            sleep(WAKE_POLL_SECONDS)
            continue

        if statmod.S_ISDIR(found.st_mode):
            return FOLDER_THERE

        return FOLDER_MISSING


def prepare_root(root: Path) -> None:
    """Make the folder to mount on.

    The folder above it is closed to other accounts when it is made
    here. One that was already there is never changed. It may be
    somebody's home folder.
    """

    parent = root.parent

    try:
        if not parent.exists():
            parent.mkdir(parents=True)
            os.chmod(parent, PRIVATE_DIR_MODE)

        root.mkdir(exist_ok=True)
    except OSError as error:
        raise MountError(
            f"{root} cannot be made on this server: "
            f"{error.strerror or error}"
        ) from None


def is_private(folder: Path) -> bool:
    """Whether this account alone can open a folder."""

    try:
        found = os.stat(folder)
    except OSError:
        return False

    if found.st_uid != os.getuid():
        return False

    return statmod.S_IMODE(found.st_mode) & OTHERS_MODE == 0


def open_to_others(spec: TargetSpec, root: Path) -> MountError:
    return MountError(
        f"{spec.label} cannot be mounted at {root}: other accounts on "
        f"this server can open {root.parent}, and the mount would show "
        "them the target's files. Name a place inside a folder that "
        "only your account can open, or leave `mount` out."
    )


def inside_hermes(spec: TargetSpec, root: Path, hermes: Path) -> MountError:
    return MountError(
        f"{spec.label} cannot be mounted at {root}: that is inside the "
        f"Hermes home, {hermes}. Deleting a profile or removing Hermes "
        "deletes everything in that folder, and through a mount it "
        "would delete the target's own files. Set this target's "
        f"`mount` to a folder outside {hermes} that nothing empties. "
        "In the desktop app that is the folder field on the Machines "
        "page. In a terminal on the server: hermes config set "
        f"plugins.entries.{PLUGIN_ID}.settings.targets.{spec.name}.mount "
        "/A/FOLDER"
    )


# ---- macOS: the plugin's own NFS server ---------------------------------


def build_nfs_command(port: int, export: str, root: Path) -> list[str]:
    options = [f"port={port}", f"mountport={port}", *NFS_OPTIONS]

    return [
        MOUNT_NFS,
        "-o", ",".join(options),
        f"127.0.0.1:{export}",
        str(root),
    ]


def build_helper_command(
    spec: TargetSpec,
    root: Path,
    state: Path,
) -> list[str]:
    helper = Path(__file__).resolve().parent / HELPER_FILE

    return [
        sys.executable,
        str(helper),
        "--state", str(state),
        "--mount-point", str(root),
        "--",
        *build_sftp_arguments(spec),
    ]


def unmount(root: Path, force: bool = False) -> None:
    command = [UMOUNT, *(["-f"] if force else []), str(root)]

    subprocess.run(
        command,
        capture_output=True,
        timeout=COMMAND_SECONDS,
        check=False,
    )


def stop_helper(state: dict) -> None:
    pid = state.get("pid")

    if not is_alive(pid):
        return

    os.kill(pid, signal.SIGTERM)

    until = time.monotonic() + STOP_WAIT_SECONDS

    while is_alive(pid) and time.monotonic() < until:
        time.sleep(HELPER_POLL_SECONDS)


def start_helper(spec: TargetSpec, root: Path, state: Path) -> dict:
    """Start the helper and wait until it has mounted or said why not."""

    state.unlink(missing_ok=True)

    log = state.with_suffix(".log")

    with open(log, "ab") as output:
        child = subprocess.Popen(
            build_helper_command(spec, root, state),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            start_new_session=True,
            close_fds=True,
        )

    until = time.monotonic() + HELPER_WAIT_SECONDS

    while time.monotonic() < until:
        found = read_state(state)

        if found.get("pid") == child.pid:
            return found

        if child.poll() is not None:
            break

        time.sleep(HELPER_POLL_SECONDS)

    found = read_state(state)

    if found.get("error"):
        return found

    return {"error": f"the mount helper did not start. See {log}"}


def serves(found: dict, spec: TargetSpec) -> bool:
    """Whether a mount was made for this target as it is set up now.

    A mount outlives an edit to the settings. One made for another
    address or another account would show the wrong machine's files
    under this target's name.
    """

    return found.get("link") == build_sftp_arguments(spec)


def ensure_mac(spec: TargetSpec, root: Path, state: Path) -> Mounted:
    found = read_state(state)
    live = is_alive(found.get("pid")) and not found.get("error")

    if live and serves(found, spec) and is_mounted(root, SYSTEM_MAC):
        return Mounted(root, str(found.get("home") or ""))

    # Something is left from a helper that died, or from a mount
    # somebody took down by hand. Both halves are cleared first.
    stop_helper(found)

    if is_mounted(root, SYSTEM_MAC):
        unmount(root, force=True)

    prepare_root(root)

    # The file server answers whoever reaches the mount as the
    # target's own account. The folder above it is the lock.
    if not is_private(root.parent):
        raise open_to_others(spec, root)

    found = start_helper(spec, root, state)

    if found.get("error"):
        raise MountError(
            f"{spec.label} could not be mounted: {found['error']}"
        )

    return Mounted(root, str(found.get("home") or ""))


# ---- Linux: sshfs --------------------------------------------------------


def install_line(os_release: str) -> str:
    """The command that installs sshfs on the distribution described."""

    names = set()

    for line in os_release.splitlines():
        key, _, value = line.partition("=")

        if key in ("ID", "ID_LIKE"):
            names.update(value.strip().strip('"').lower().split())

    for known, line in INSTALL_LINES:
        if names.intersection(known):
            return line

    return INSTALL_FALLBACK


def read_os_release() -> str:
    try:
        return Path(OS_RELEASE).read_text()
    except OSError:
        return ""


def missing_sshfs(spec: TargetSpec) -> MountError:
    return MountError(
        f"{spec.label} cannot be mounted: this server has no `sshfs`. "
        f"Install it, then try again: {install_line(read_os_release())}"
    )


def build_sshfs_command(spec: TargetSpec, root: Path) -> list[str]:
    # No `reconnect`. With it, sshfs keeps a dead mount in place and
    # makes every read wait for a new login that cannot succeed: five
    # to nine seconds each, measured against a frozen target. Hermes
    # reads a session's folder from its main loop, so one sleeping
    # laptop stopped the whole backend for every session. Without it,
    # sshfs leaves when the keep-alive gives up, the folder is plain
    # and empty again, and reads fail at once. `ensure` mounts it
    # again when the target answers.
    options = [
        "BatchMode=yes",
        f"ConnectTimeout={spec.connect_timeout}",
        f"ServerAliveInterval={SFTP_ALIVE_INTERVAL}",
        f"ServerAliveCountMax={SFTP_ALIVE_COUNT}",
        "StrictHostKeyChecking=accept-new",
    ]

    if spec.port != DEFAULT_SSH_PORT:
        options.append(f"port={spec.port}")

    if spec.key:
        options.append(f"IdentityFile={spec.key}")

    if spec.known_hosts:
        options.append(f"UserKnownHostsFile={spec.known_hosts}")

    if spec.host_key_alias:
        options.append(f"HostKeyAlias={spec.host_key_alias}")

    return [
        SSHFS,
        f"{spec.user}@{spec.host}:/",
        str(root),
        "-o", ",".join(options),
    ]


def unmount_linux(root: Path) -> None:
    for program in FUSERMOUNTS:
        if shutil.which(program) is None:
            continue

        subprocess.run(
            [program, "-u", str(root)],
            capture_output=True,
            timeout=COMMAND_SECONDS,
            check=False,
        )
        return


def ask_home(spec: TargetSpec) -> str:
    """Where a login on the target lands, as the target tells it.

    sshfs is given the target's root and does not say. So the target
    is asked once, over the plugin's own SFTP client. Nothing here is
    worked out from a user name.
    """

    try:
        link = SshLink(build_sftp_arguments(spec), timeout=HOME_SECONDS)
    except LinkError:
        return ""

    client = sftp.Client(link)

    try:
        client.start()

        return client.realpath(".")
    except sftp.SftpError:
        return ""
    finally:
        client.close()


def ensure_linux(spec: TargetSpec, root: Path, state: Path) -> Mounted:
    found = read_state(state)
    mounted = is_mounted(root, SYSTEM_LINUX)

    if mounted and serves(found, spec):
        return Mounted(root, str(found.get("home") or ""))

    if shutil.which(SSHFS) is None:
        raise missing_sshfs(spec)

    # Mounted for this target as it was set up before.
    if mounted:
        unmount_linux(root)

    prepare_root(root)

    try:
        done = subprocess.run(
            build_sshfs_command(spec, root),
            capture_output=True,
            text=True,
            timeout=COMMAND_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MountError(
            f"{spec.label} could not be mounted: {error}"
        ) from None

    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip().splitlines()
        reason = said[-1] if said else f"sshfs exited {done.returncode}"

        raise MountError(f"{spec.label} could not be mounted: {reason}")

    home = ask_home(spec)

    write_state(state, {"link": build_sftp_arguments(spec), "home": home})

    return Mounted(root, home)


# ---- both ---------------------------------------------------------------


def take_down(root: Path, state: Path, system: str) -> None:
    """Unmount `root` and stop what served it. The caller holds the
    target's lock."""

    if system == SYSTEM_LINUX:
        unmount_linux(root)
        return

    stop_helper(read_state(state))

    if is_mounted(root, SYSTEM_MAC):
        unmount(root, force=True)

    state.unlink(missing_ok=True)


def ensure(
    spec: TargetSpec,
    root: Path,
    data_dir: Path,
    system: str | None = None,
) -> Mounted:
    """Mount a target's disk at `root` if it is not there already.

    Cheap when the mount is up: one read of a small file and one look
    at the system's list of mounts.
    """

    system = system or platform.system()
    state = state_file(data_dir, spec.name)

    if system not in (SYSTEM_MAC, SYSTEM_LINUX):
        raise MountError(
            f"{spec.label} cannot be mounted: onsite does not mount a "
            f"target's files on {system or 'this system'} yet."
        )

    hermes = hermes_root(data_dir)

    with locked(state):
        # Asked every time, of the default place and of a place the
        # settings name alike. Hermes deletes a profile by deleting
        # everything below its folder, and that would go through a
        # mount into the target's disk. A mount an older version left
        # there is taken down, so the danger does not outlive it.
        if is_inside(root, hermes):
            take_down(root, state, system)

            raise inside_hermes(spec, root, hermes)

        if system == SYSTEM_LINUX:
            return ensure_linux(spec, root, state)

        return ensure_mac(spec, root, state)


def stop(
    spec: TargetSpec,
    root: Path,
    data_dir: Path,
    system: str | None = None,
) -> None:
    """Take a target's mount down. Nothing on the target changes."""

    system = system or platform.system()
    state = state_file(data_dir, spec.name)

    with locked(state):
        take_down(root, state, system)

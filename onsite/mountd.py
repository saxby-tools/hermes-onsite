"""The helper that keeps one target mounted on a macOS host.

Started by `mounts.py`, as a process of its own, so a mount outlives
the Hermes process that asked for it. It runs the NFS server that
macOS mounts, feeds it from the target over SFTP, mounts it, and
then stays until it is told to stop or the mount is taken away.

    mountd.py --state FILE --mount-point DIR -- <arguments for ssh>

It writes one small file, the state file: its process id, the port
it listens on, and the folder a login on the target starts in. A
failure to mount is written there too, so the process that started
it can say why.

This file imports nothing from Hermes. It is run as a script, so it
loads the rest of the plugin by path.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import signal
import subprocess
import sys
import threading

from pathlib import Path


PACKAGE = "onsite_mountd"
SEPARATOR = "--"
SYSTEM_MAC = "Darwin"

# How often the helper looks at whether it is still wanted.
WATCH_SECONDS = 2.0
HOME_RETRY_SECONDS = 10.0
MOUNT_SECONDS = 30


def load_plugin():
    """Load the plugin's folder as a package, whatever it is named."""

    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    package = importlib.util.module_from_spec(spec)

    sys.modules[PACKAGE] = package
    spec.loader.exec_module(package)

    return importlib.import_module


def parse_arguments(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    own, ssh_arguments = argv, []

    if SEPARATOR in argv:
        at = argv.index(SEPARATOR)
        own, ssh_arguments = argv[:at], argv[at + 1:]

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--mount-point", required=True, type=Path)

    return parser.parse_args(own), ssh_arguments


class Helper:
    def __init__(self, load, arguments, ssh_arguments: list[str]):
        self.link = load(f"{PACKAGE}.link")
        self.sftp = load(f"{PACKAGE}.sftp")
        self.remotefs = load(f"{PACKAGE}.remotefs")
        self.nfsd = load(f"{PACKAGE}.nfsd")
        self.mounts = load(f"{PACKAGE}.mounts")

        self.state = arguments.state
        self.root = arguments.mount_point
        self.ssh_arguments = ssh_arguments
        self.stopping = threading.Event()
        self.home = ""

        self.files = self.remotefs.TargetFiles(self.connect)
        self.server = self.nfsd.Server(self.files)
        self.listener = self.nfsd.listen(self.server)
        self.port = self.listener.server_address[1]

    def connect(self):
        try:
            client = self.sftp.Client(self.link.SshLink(self.ssh_arguments))
            client.start()
        except self.link.LinkError as error:
            raise self.sftp.SftpError(
                self.sftp.FX_NO_LINK,
                str(error),
            ) from None

        return client

    def write_state(self, error: str = "") -> None:
        self.mounts.write_state(self.state, {
            "pid": os.getpid(),
            "port": self.port,
            "mount_point": str(self.root),
            "home": self.home,
            "link": self.ssh_arguments,
            "error": error,
        })

    def learn_home(self) -> None:
        """Ask the target where a login lands. It may be asleep."""

        try:
            self.home = self.files.home()
        except self.remotefs.FsError:
            self.home = ""

    def mount(self) -> str:
        """Mount the server. Returns what went wrong, or nothing."""

        command = self.mounts.build_nfs_command(
            self.port,
            self.server.export,
            self.root,
        )

        try:
            done = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=MOUNT_SECONDS,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return str(error)

        if done.returncode != 0:
            return (done.stderr or done.stdout).strip() or "mount_nfs failed"

        return ""

    def watch(self) -> None:
        """Stay until told to stop, or until the mount is gone."""

        waited = 0.0

        while not self.stopping.wait(WATCH_SECONDS):
            if not self.mounts.is_mounted(self.root, SYSTEM_MAC):
                return

            waited += WATCH_SECONDS

            if self.home or waited < HOME_RETRY_SECONDS:
                continue

            waited = 0.0
            self.learn_home()

            if self.home:
                self.write_state()

    def close(self) -> None:
        if self.mounts.is_mounted(self.root, SYSTEM_MAC):
            self.mounts.unmount(self.root, force=True)

        self.listener.shutdown()
        self.listener.server_close()
        self.files.close()


def main(argv: list[str]) -> int:
    arguments, ssh_arguments = parse_arguments(argv)
    helper = Helper(load_plugin(), arguments, ssh_arguments)

    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: helper.stopping.set())

    helper.learn_home()

    problem = helper.mount()

    if problem:
        helper.write_state(error=problem)
        helper.close()

        return 1

    helper.write_state()

    try:
        helper.watch()
    finally:
        helper.close()
        helper.state.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

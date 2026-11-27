"""`hermes onsite dial-in`, and what `init` and `status` say of it."""

from __future__ import annotations

import argparse
import copy
import subprocess

import pytest

from onsite import commands
from onsite import dialin
from onsite import health
from onsite import runtime


SETTINGS = {
    "default": "mac",
    "targets": {
        "box": {
            "kind": "ssh",
            "host": "box.example",
            "user": "dev",
            "port": 2222,
            "mount": "/mnt/box",
        },
        "mac": {"kind": "local"},
    },
}

REFUSED = "ssh: connect to host 127.0.0.1 port 20500: Connection refused"
LINK = (
    "ssh -N -o ExitOnForwardFailure=yes -o ConnectTimeout=10 "
    "-o ServerAliveInterval=15 -o ServerAliveCountMax=3 "
    "-o ControlPath=none -R 127.0.0.1:20500:127.0.0.1:22 "
    "you@server.example"
)


class Harness:
    """A runtime whose settings live in memory and can be saved."""

    def __init__(self, tmp_path, settings=SETTINGS):
        self.settings = copy.deepcopy(settings)
        self.saves = 0

        session = runtime.SessionReader(cwd=lambda: "", session_id=lambda: "")

        self.runtime = runtime.Runtime(
            load_settings=lambda: self.settings,
            data_dir=tmp_path,
            session=session,
            build_environment=lambda *args: object(),
            save_settings=self.save,
        )

    def save(self, settings: dict) -> None:
        self.settings = settings
        self.saves += 1


@pytest.fixture(autouse=True)
def a_quiet_server(monkeypatch):
    """No test here may dial, look at a real port or read a real key
    file. `ssh` says the port is closed unless a test says otherwise."""

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 255, "", REFUSED)

    monkeypatch.setattr(health.subprocess, "run", fake_run)
    monkeypatch.setattr(dialin, "keys_on_file", lambda alias, known: [])
    monkeypatch.setattr(dialin, "port_is_free", lambda port: True)
    monkeypatch.setattr(dialin, "draw_port", lambda: 20500)


def dial_in_args(target: str, **flags) -> argparse.Namespace:
    values = {
        "target": target,
        "user": None,
        "server": None,
        "port": None,
        "keep": None,
        "apply": False,
    }
    values.update(flags)

    return argparse.Namespace(**values)


def add_laptop(harness: Harness, **flags) -> int:
    return commands.cli_dial_in(
        harness.runtime,
        dial_in_args(
            "laptop",
            user="dev",
            server="you@server.example",
            **flags,
        ),
    )


def test_adding_a_computer_writes_it_and_prints_its_one_command(
    tmp_path,
    capsys,
):
    harness = Harness(tmp_path)
    code = add_laptop(harness, apply=True)
    printed = capsys.readouterr().out

    assert code == commands.EXIT_OK
    assert harness.settings["targets"]["laptop"] == {
        "kind": "ssh",
        "host": "127.0.0.1",
        "user": "dev",
        "dial_in": "you@server.example",
        "port": 20500,
    }
    assert f"   {LINK}\n" in printed
    assert "dials in to this server on port 20500" in printed
    assert 'permitlisten="127.0.0.1:20500"' in printed


def test_the_other_targets_are_left_as_they_were(tmp_path):
    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)

    assert harness.settings["targets"]["box"] == SETTINGS["targets"]["box"]
    assert harness.settings["default"] == "mac"


def test_without_apply_nothing_is_written_and_no_command_is_shown(
    tmp_path,
    capsys,
):
    harness = Harness(tmp_path)
    code = add_laptop(harness)
    printed = capsys.readouterr().out

    assert code == commands.EXIT_OK
    assert harness.saves == 0
    assert "[DRY RUN] nothing was written" in printed
    assert "[DRY RUN] Its port is picked again then." in printed
    assert "dial_in: you@server.example" in printed
    assert "ssh -N" not in printed


def test_a_dry_run_for_a_saved_computer_does_not_say_its_port_moves(
    tmp_path,
    capsys,
):
    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)
    capsys.readouterr()

    commands.cli_dial_in(harness.runtime, dial_in_args("laptop", user="me"))
    printed = capsys.readouterr().out

    assert "port: 20500" in printed
    assert "picked again" not in printed


def test_a_saved_computer_is_shown_its_command_again(tmp_path, capsys):
    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)
    capsys.readouterr()

    code = commands.cli_dial_in(harness.runtime, dial_in_args("laptop"))
    printed = capsys.readouterr().out

    assert code == commands.EXIT_OK
    assert harness.saves == 1
    assert f"   {LINK}\n" in printed
    assert "State: not connected: it has not connected" in printed


def test_it_says_connected_when_the_computer_has_arrived(
    tmp_path,
    capsys,
    monkeypatch,
):
    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)
    capsys.readouterr()

    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(
        dialin,
        "keys_on_file",
        lambda alias, known: ["ED25519 SHA256:abc"],
    )

    commands.cli_dial_in(harness.runtime, dial_in_args("laptop"))
    printed = capsys.readouterr().out

    assert "State: connected (" in printed
    assert "   ED25519 SHA256:abc" in printed


@pytest.mark.parametrize("system, path, start", [
    (
        "linux",
        "~/.config/systemd/user/onsite-dial-in.service",
        "systemctl --user enable --now onsite-dial-in.service",
    ),
    (
        "macos",
        "~/Library/LaunchAgents/io.github.saxby-tools.onsite.dial-in.plist",
        "launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/",
    ),
])
def test_keep_prints_the_file_to_save_and_how_to_turn_it_on(
    tmp_path,
    capsys,
    system,
    path,
    start,
):
    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)
    capsys.readouterr()

    code = commands.cli_dial_in(
        harness.runtime,
        dial_in_args("laptop", keep=system),
    )
    printed = capsys.readouterr().out

    assert code == commands.EXIT_OK
    assert f"save this as {path}:" in printed
    assert start in printed
    assert "BatchMode=yes" in printed


def test_the_hermes_server_is_not_turned_into_a_computer_that_dials_in(
    tmp_path,
    capsys,
):
    harness = Harness(tmp_path)
    code = commands.cli_dial_in(
        harness.runtime,
        dial_in_args(
            "mac",
            user="dev",
            server="you@server.example",
            apply=True,
        ),
    )

    assert code == commands.EXIT_USAGE
    assert harness.saves == 0
    assert "`mac` is the Hermes server" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["box", "mac", "nobody"])
def test_a_target_that_does_not_dial_in_has_no_command(
    tmp_path,
    capsys,
    name,
):
    code = commands.cli_dial_in(Harness(tmp_path).runtime, dial_in_args(name))
    printed = capsys.readouterr().out

    assert code == commands.EXIT_USAGE
    assert f"`{name}` is not a computer that dials in." in printed
    assert "--user you --server you@server.example" in printed


def test_a_new_computer_needs_its_account_and_the_server(tmp_path, capsys):
    harness = Harness(tmp_path)
    code = commands.cli_dial_in(
        harness.runtime,
        dial_in_args("laptop", user="dev", apply=True),
    )

    assert code == commands.EXIT_USAGE
    assert harness.saves == 0
    assert "--server" in capsys.readouterr().out


def test_two_computers_get_two_ports(tmp_path, monkeypatch):
    # The draw offers the first computer's port again.
    offers = [20500, 20500, 20777]

    monkeypatch.setattr(dialin, "draw_port", lambda: offers.pop(0))

    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)
    commands.cli_dial_in(
        harness.runtime,
        dial_in_args(
            "desktop",
            user="dev",
            server="you@server.example",
            apply=True,
        ),
    )

    assert harness.settings["targets"]["laptop"]["port"] == 20500
    assert harness.settings["targets"]["desktop"]["port"] == 20777


def test_a_port_another_computer_dials_in_on_is_refused(tmp_path, capsys):
    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)
    capsys.readouterr()

    code = commands.cli_dial_in(
        harness.runtime,
        dial_in_args(
            "desktop",
            user="dev",
            server="you@server.example",
            port=20500,
            apply=True,
        ),
    )

    assert code == commands.EXIT_USAGE
    assert harness.saves == 1
    assert "both use port 20500" in capsys.readouterr().out


def test_changing_a_saved_computer_keeps_its_port(tmp_path, monkeypatch):
    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)
    monkeypatch.setattr(dialin, "draw_port", lambda: 20999)

    commands.cli_dial_in(
        harness.runtime,
        dial_in_args("laptop", user="me", apply=True),
    )

    assert harness.settings["targets"]["laptop"]["port"] == 20500
    assert harness.settings["targets"]["laptop"]["user"] == "me"


def test_a_target_the_server_dialed_can_be_turned_into_one_that_dials_in(
    tmp_path,
):
    harness = Harness(tmp_path)

    commands.cli_dial_in(
        harness.runtime,
        dial_in_args("box", server="you@server.example", apply=True),
    )

    # Its old port was its own SSH port, and its old address is gone.
    # What had nothing to do with the address is kept.
    assert harness.settings["targets"]["box"] == {
        "kind": "ssh",
        "host": "127.0.0.1",
        "user": "dev",
        "mount": "/mnt/box",
        "dial_in": "you@server.example",
        "port": 20500,
    }


def test_a_server_that_is_not_a_plain_login_is_refused(tmp_path, capsys):
    harness = Harness(tmp_path)
    code = commands.cli_dial_in(
        harness.runtime,
        dial_in_args(
            "laptop",
            user="dev",
            server="you@server.example; id",
            apply=True,
        ),
    )

    assert code == commands.EXIT_USAGE
    assert harness.saves == 0
    assert "USER@ADDRESS" in capsys.readouterr().out


# ---- status ------------------------------------------------------------


def laptop_spec(harness: Harness):
    return harness.runtime.load_config().target("laptop")


def test_status_says_a_computer_has_not_connected(tmp_path):
    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)

    row = commands.describe_row(laptop_spec(harness))

    assert row["state"].startswith("DOWN (it has not connected")
    assert "Connection refused" not in row["state"]


def test_status_names_the_port_of_a_computer_that_is_up(
    tmp_path,
    monkeypatch,
):
    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)
    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )

    row = commands.describe_row(laptop_spec(harness))

    assert row["state"].startswith("up (")
    assert row["state"].endswith("dev, dials in on port 20500")


# ---- init --------------------------------------------------------------


def init_args(**flags) -> argparse.Namespace:
    values = {
        "ssh": [],
        "local": [],
        "dial_in": [],
        "server": None,
        "prefix": [],
        "map": [],
        "key": [],
        "known_hosts": [],
        "mount": [],
        "start": [],
        "default": None,
        "apply": False,
    }
    values.update(flags)

    return argparse.Namespace(**values)


def test_init_sets_up_a_computer_that_dials_in(tmp_path, capsys):
    harness = Harness(tmp_path, {"default": None, "targets": {}})
    code = commands.cli_init(
        harness.runtime,
        init_args(
            dial_in=["laptop=dev"],
            server="you@server.example",
            local=["mac"],
            apply=True,
        ),
    )
    printed = capsys.readouterr().out

    assert code == commands.EXIT_OK
    assert harness.settings["targets"]["laptop"]["port"] == 20500
    assert harness.settings["default"] == "mac"
    assert "see: hermes onsite dial-in laptop" in printed


def test_a_dry_run_of_init_says_the_port_is_not_final(tmp_path, capsys):
    harness = Harness(tmp_path, {"default": None, "targets": {}})

    commands.cli_init(
        harness.runtime,
        init_args(
            dial_in=["laptop=dev"],
            server="you@server.example",
            local=["mac"],
        ),
    )

    assert harness.saves == 0
    assert "is picked again when this is written" in capsys.readouterr().out


def test_a_dry_run_of_init_for_a_saved_computer_says_nothing_of_ports(
    tmp_path,
    capsys,
):
    harness = Harness(tmp_path)

    add_laptop(harness, apply=True)
    capsys.readouterr()

    commands.cli_init(
        harness.runtime,
        init_args(
            dial_in=["laptop=dev"],
            server="you@server.example",
            local=["mac"],
        ),
    )
    printed = capsys.readouterr().out

    assert "port: 20500" in printed
    assert "picked again" not in printed

"""Dial-in: a computer the server cannot reach offers itself over SSH."""

from __future__ import annotations

import plistlib
import shlex
import socket
import subprocess

import pytest

from onsite import dialin


SERVER = dialin.Server("you", "server.example")
PORT = 22022


def never_free(port: int) -> bool:
    return False


def always_free(port: int) -> bool:
    return True


def draws(*ports: int):
    """A stand-in for the random draw that hands out these ports."""

    left = list(ports)

    return lambda: left.pop(0)


# ---- the server, as the computer logs in to it -------------------------


def test_a_server_is_an_account_and_an_address():
    assert dialin.parse_server("you@server.example") == SERVER


def test_a_server_may_carry_its_ssh_port():
    server = dialin.parse_server("you@server.example:2222")

    assert server.port == 2222
    assert dialin.format_server(server) == "you@server.example:2222"


def test_a_server_may_leave_the_account_to_the_computers_ssh_setup():
    server = dialin.parse_server("server")

    assert server.login == "server"
    assert dialin.link_arguments(server, PORT)[-1] == "server"


@pytest.mark.parametrize("text", [
    "",
    "you@",
    "@server.example",
    "you@server example",
    "you@server.example; rm -rf x",
    "you@$(id)",
    "you@`id`",
    "you@server.example\nid",
    "-oProxyCommand=id@server.example",
    "you@-oProxyCommand=id",
    "you@server.example:0",
    "you@server.example:65536",
    "you@server.example:port",
    None,
    22,
])
def test_a_server_that_is_not_a_plain_login_is_refused(text):
    # The text ends up on a line a person pastes into a terminal.
    with pytest.raises(dialin.DialInError):
        dialin.parse_server(text)


# ---- the port ----------------------------------------------------------


def test_a_port_is_drawn_from_the_range_set_aside_for_it():
    for _ in range(50):
        port = dialin.pick_port(set(), is_free=always_free)

        assert dialin.PORT_FIRST <= port <= dialin.PORT_LAST


def test_a_port_another_target_has_is_passed_over():
    port = dialin.pick_port(
        {20001, 20002},
        is_free=always_free,
        draw=draws(20001, 20002, 20003),
    )

    assert port == 20003


def test_a_port_something_answers_on_is_passed_over():
    busy = {20001}

    port = dialin.pick_port(
        set(),
        is_free=lambda port: port not in busy,
        draw=draws(20001, 20002),
    )

    assert port == 20002


def test_no_free_port_is_said_and_not_guessed():
    with pytest.raises(dialin.DialInError, match="no free port"):
        dialin.pick_port(set(), is_free=never_free)


def test_a_port_nothing_listens_on_is_free():
    # Bound and closed again, so the system knows of nothing there.
    with socket.socket() as probe:
        probe.bind((dialin.LOOPBACK, 0))
        port = probe.getsockname()[1]

    assert dialin.port_is_free(port) is True


def test_a_port_something_listens_on_is_not_free():
    with socket.socket() as listener:
        listener.bind((dialin.LOOPBACK, 0))
        listener.listen(1)

        assert dialin.port_is_free(listener.getsockname()[1]) is False


@pytest.mark.parametrize("port", [22, 1023, 65536, "22022", True, None])
def test_a_port_out_of_range_is_refused(port):
    with pytest.raises(dialin.DialInError, match="the port must be"):
        dialin.check_port(port)


# ---- what the plugin decides for a new computer ------------------------


def complete(name, block, targets, *ports):
    return dialin.complete(
        name,
        block,
        targets,
        pick=lambda taken: dialin.pick_port(
            taken,
            is_free=always_free,
            draw=draws(*ports),
        ),
    )


def test_a_new_computer_gets_the_loopback_address_and_a_port():
    block = complete(
        "laptop",
        {"user": "you", "dial_in": "you@server.example"},
        {},
        20500,
    )

    assert block == {
        "kind": "ssh",
        "host": "127.0.0.1",
        "user": "you",
        "dial_in": "you@server.example",
        "port": 20500,
    }


def test_two_computers_never_share_a_port():
    targets = {}
    request = {"user": "you", "dial_in": "you@server.example"}

    # The draw offers the laptop's port to the desktop first.
    targets["laptop"] = complete("laptop", request, targets, 20500)
    targets["desktop"] = complete("desktop", request, targets, 20500, 20777)

    assert targets["laptop"]["port"] == 20500
    assert targets["desktop"]["port"] == 20777


def test_a_port_any_other_target_names_is_kept_away_from():
    targets = {"box": {"kind": "ssh", "host": "box.example", "port": 20500}}
    request = {"user": "you", "dial_in": "you@server.example"}

    assert complete("laptop", request, targets, 20500, 20501)["port"] == 20501


def test_a_saved_computer_keeps_its_port():
    # Its own command names the port. A new one would cut its link.
    targets = {
        "laptop": {
            "kind": "ssh",
            "host": "127.0.0.1",
            "user": "you",
            "dial_in": "you@server.example",
            "port": 20500,
        },
    }
    request = {"user": "me", "dial_in": "me@server.example"}

    assert complete("laptop", request, targets, 20999)["port"] == 20500


def test_a_port_the_person_names_is_kept():
    request = {"user": "you", "dial_in": "you@server.example", "port": 4022}

    assert complete("laptop", request, {}, 20999)["port"] == 4022


def test_the_port_of_a_target_the_server_used_to_dial_is_not_kept():
    # That port was the computer's own SSH port. It means something
    # else, and the form and the CLI both drop it before asking.
    box = {"kind": "ssh", "host": "laptop.example", "port": 2222}
    request = {"user": "you", "dial_in": "you@server.example"}

    assert complete("laptop", request, {"laptop": box}, 20999)["port"] == 20999


@pytest.mark.parametrize("port", ["20500", 20500.0, True])
def test_a_port_that_is_not_a_number_is_not_swapped_for_another(port):
    # Picking a new one would change, without a word, the port the
    # computer's own command names.
    request = {"user": "you", "dial_in": "you@server.example", "port": port}

    with pytest.raises(dialin.DialInError, match="must be a number"):
        complete("laptop", request, {}, 20999)


# ---- the one command the person runs on the computer -------------------


def test_the_computer_forwards_its_ssh_port_to_loopback_on_the_server():
    command = dialin.link_command(SERVER, PORT)

    assert command.startswith("ssh -N ")
    assert "-R 127.0.0.1:22022:127.0.0.1:22 " in command
    assert command.endswith(" you@server.example")


def test_a_port_that_cannot_be_forwarded_stops_the_link():
    # Without this, ssh stays up with no forward and looks healthy.
    assert "ExitOnForwardFailure=yes" in dialin.link_command(SERVER, PORT)


def test_the_link_gets_a_connection_of_its_own():
    # With a shared connection, ssh hands the forward to another ssh
    # and this one ends at once. Seen in a container.
    assert "ControlPath=none" in dialin.link_command(SERVER, PORT)


def test_a_dead_link_is_noticed_and_a_dead_server_is_not_waited_for():
    command = dialin.link_command(SERVER, PORT)

    assert "ServerAliveInterval=15" in command
    assert "ServerAliveCountMax=3" in command
    assert "ConnectTimeout=10" in command


def test_a_server_on_another_ssh_port_is_dialed_there():
    server = dialin.parse_server("you@server.example:2222")
    words = dialin.link_arguments(server, PORT)

    assert words[-3:] == ["-p", "2222", "you@server.example"]


def test_the_command_is_one_shell_line_with_nothing_to_quote():
    command = dialin.link_command(SERVER, PORT)

    assert shlex.split(command) == dialin.link_arguments(SERVER, PORT)
    assert "'" not in command
    assert '"' not in command


def test_the_command_a_person_runs_may_ask_and_the_service_may_not():
    by_hand = dialin.link_arguments(SERVER, PORT)
    unattended = dialin.link_arguments(SERVER, PORT, unattended=True)

    assert "BatchMode=yes" not in by_hand
    assert "BatchMode=yes" in unattended


def test_the_line_that_limits_the_computers_key():
    options = dialin.key_options(PORT)

    assert options == (
        'restrict,port-forwarding,permitlisten="127.0.0.1:22022",'
        'permitopen="127.0.0.1:22022",command="exit 1"'
    )


# ---- keeping the link up across restarts -------------------------------


def test_the_linux_service_restarts_the_link_when_it_drops():
    keep = dialin.keep_running(dialin.SYSTEM_LINUX, SERVER, PORT)

    assert keep["path"] == "~/.config/systemd/user/onsite-dial-in.service"
    assert "Restart=always" in keep["text"]
    assert "RestartSec=5" in keep["text"]
    assert "WantedBy=default.target" in keep["text"]
    assert keep["start"][-1] == (
        "systemctl --user enable --now onsite-dial-in.service"
    )


def test_the_linux_service_runs_the_same_link_with_nobody_to_ask():
    unit = dialin.systemd_unit(SERVER, PORT)
    line = next(
        line for line in unit.splitlines() if line.startswith("ExecStart=")
    )
    words = shlex.split(line[len("ExecStart="):])

    assert words[:2] == ["/usr/bin/env", "ssh"]
    assert words[1:] == dialin.link_arguments(SERVER, PORT, unattended=True)


def test_the_mac_agent_is_a_property_list_that_runs_the_same_link():
    keep = dialin.keep_running(dialin.SYSTEM_MAC, SERVER, PORT)
    agent = plistlib.loads(keep["text"].encode())
    words = dialin.link_arguments(SERVER, PORT, unattended=True)

    assert agent["Label"] == "io.github.saxby-tools.onsite.dial-in"
    assert agent["ProgramArguments"] == ["/usr/bin/ssh", *words[1:]]
    assert agent["KeepAlive"] is True
    assert agent["RunAtLoad"] is True
    assert keep["path"].endswith(f"{agent['Label']}.plist")
    assert agent["Label"] in keep["stop"][0]


def test_a_system_that_is_not_known_is_refused():
    with pytest.raises(dialin.DialInError, match="linux, macos"):
        dialin.keep_running("windows", SERVER, PORT)


def test_everything_a_person_needs_comes_in_one_answer():
    info = dialin.instructions("laptop", "you@server.example", PORT)

    assert info["target"] == "laptop"
    assert info["port"] == PORT
    assert info["command"] == dialin.link_command(SERVER, PORT)
    assert info["key_options"] == dialin.key_options(PORT)
    assert sorted(info["keep"]) == ["linux", "macos"]


# ---- the host key on file ----------------------------------------------


KEYGEN_OUTPUT = (
    "# Host onsite-laptop found: line 1 \n"
    "onsite-laptop ED25519 SHA256:abc\n"
    "# Host onsite-laptop found: line 2 \n"
    "onsite-laptop RSA SHA256:def\n"
)


def test_the_filed_host_keys_are_read_through_ssh_keygen(monkeypatch):
    asked = []

    def fake_run(command, **kwargs):
        asked.append(command)

        return subprocess.CompletedProcess(command, 0, KEYGEN_OUTPUT, "")

    monkeypatch.setattr(dialin.subprocess, "run", fake_run)

    keys = dialin.keys_on_file("onsite-laptop", "/keys/known")

    assert keys == ["ED25519 SHA256:abc", "RSA SHA256:def"]
    assert asked == [
        ["ssh-keygen", "-l", "-F", "onsite-laptop", "-f", "/keys/known"],
    ]


def test_a_known_hosts_setting_is_read_the_way_ssh_reads_it(monkeypatch):
    # `ssh` takes `~` for the home folder and several files in one
    # setting. `ssh-keygen` takes one plain path at a time.
    asked = []

    def fake_run(command, **kwargs):
        asked.append(command[-1])

        return subprocess.CompletedProcess(command, 0, KEYGEN_OUTPUT, "")

    monkeypatch.setattr(dialin.subprocess, "run", fake_run)
    monkeypatch.setenv("HOME", "/home/hermes")

    keys = dialin.keys_on_file("onsite-laptop", "~/known /keys/other")

    assert asked == ["/home/hermes/known", "/keys/other"]
    assert len(keys) == 4


def test_no_key_on_file_is_an_empty_answer_not_an_error(monkeypatch):
    def fake_run(command, **kwargs):
        raise OSError("no such program")

    monkeypatch.setattr(dialin.subprocess, "run", fake_run)

    assert dialin.keys_on_file("onsite-laptop") == []


# ---- what ssh says, in plain words -------------------------------------


NO_LINK = (
    "it has not connected to this server, or its link dropped. Nothing "
    "answers on port 22022"
)
SILENT = (
    "its link is up but it does not answer. It may be asleep or off "
    "the network"
)
NOT_SSH = (
    "port 22022 answers, but not as an SSH server. The computer's own "
    "SSH server may be off, or another program on this server has the "
    "port"
)
KEY_REFUSED = "it is connected but does not accept this server's key"
OTHER_KEY = (
    "something else answers on port 22022, or `laptop` has a new host "
    "key. It is not the key first filed, so nothing was sent to it. To "
    "take the new key for a computer that was set up afresh: "
    "ssh-keygen -R onsite-laptop"
)
DROPPED = "its link to this server dropped while it was in use"


# The first six lines were read off a real run against a container.
# The rest are what OpenSSH prints for a link lost under a command.
@pytest.mark.parametrize("said, means", [
    (
        "ssh: connect to host 127.0.0.1 port 22022: Connection refused",
        NO_LINK,
    ),
    ("Connection timed out during banner exchange", SILENT),
    ("no answer in 4s", SILENT),
    (
        "kex_exchange_identification: read: Connection reset by peer",
        NOT_SSH,
    ),
    ("dev@127.0.0.1: Permission denied (publickey).", KEY_REFUSED),
    ("Host key verification failed.", OTHER_KEY),
    (
        "kex_exchange_identification: Connection closed by remote host",
        NOT_SSH,
    ),
    ("Timeout, server 127.0.0.1 not responding.", SILENT),
    ("Connection to 127.0.0.1 closed by remote host.", DROPPED),
    ("client_loop: send disconnect: Broken pipe", DROPPED),
])
def test_what_ssh_says_is_told_in_plain_words(said, means):
    assert dialin.explain("laptop", PORT, said) == means


@pytest.mark.parametrize("said", [
    "ssh exited 3",
    # Not the SSH server turning a login away: a firewall, a program
    # that may not be run, and a command's own last line.
    "ssh: connect to host 127.0.0.1 port 22022: Permission denied",
    "cannot run ssh: [Errno 13] Permission denied: 'ssh'",
    "bash: /opt/tool: Permission denied",
])
def test_words_that_are_not_known_are_passed_on(said):
    assert dialin.explain("laptop", PORT, said) == said


def test_a_wrong_key_is_said_whatever_else_is_in_the_text():
    # ssh prints a long warning, and a mount program adds its own
    # last line. The one reason that must never be missed is found
    # wherever it is.
    said = (
        "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\n"
        "ssh: connect to host 127.0.0.1 port 22022: Connection refused\n"
        "read: Connection reset by peer"
    )

    assert dialin.explain("laptop", PORT, said) == OTHER_KEY

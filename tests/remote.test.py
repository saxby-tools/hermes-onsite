"""SSH option, setup and wrapper text, checked without a network."""

from __future__ import annotations

import subprocess

from hermes_hands import config
from hermes_hands import remote


SPEC = config.TargetSpec(
    name="laptop",
    kind=config.KIND_SSH,
    host="laptop.example",
    user="dev",
    connect_timeout=3,
)


def run_sh(script: str) -> str:
    done = subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )

    return done.stdout


def test_keepalives_and_a_short_connect_timeout_are_set():
    options = remote.build_ssh_options(SPEC)

    assert "ConnectTimeout=3" in options
    assert "ServerAliveInterval=1" in options
    assert "ServerAliveCountMax=3" in options


def test_a_private_known_hosts_file_is_passed_when_set():
    spec = config.TargetSpec(
        name="box",
        kind=config.KIND_SSH,
        host="h",
        user="u",
        known_hosts="/keys/known",
    )

    assert "UserKnownHostsFile=/keys/known" in remote.build_ssh_options(spec)
    assert not any(
        option.startswith("UserKnownHostsFile")
        for option in remote.build_ssh_options(SPEC)
    )


def test_the_setup_script_is_empty_when_nothing_is_wanted():
    spec = config.TargetSpec(
        name="plain",
        kind=config.KIND_SSH,
        host="h",
        user="u",
        path_mode=config.PATH_MODE_NONE,
        inhibit_sleep=False,
    )

    assert remote.build_setup_script(spec) == ""


def test_the_setup_script_runs_and_parses_on_this_machine():
    found = remote.parse_setup(run_sh(remote.build_setup_script(SPEC)))

    assert found.path.startswith("/")
    assert found.inhibit in remote.VALID_INHIBIT_MODES


def test_a_greeting_before_the_path_is_ignored():
    output = "\n".join([
        remote.PATH_BEGIN,
        "Welcome back!",
        "/opt/tools/bin:/usr/bin",
        remote.PATH_END,
    ])

    assert remote.parse_path(output) == "/opt/tools/bin:/usr/bin"


def test_a_missing_or_odd_path_is_refused():
    assert remote.parse_path("no markers here") == ""

    empty = f"{remote.PATH_BEGIN}\n{remote.PATH_END}\n"

    assert remote.parse_path(empty) == ""

    odd = f"{remote.PATH_BEGIN}\nnot a path\n{remote.PATH_END}\n"

    assert remote.parse_path(odd) == ""


def test_the_inhibit_mode_is_read_from_its_marker():
    assert remote.parse_inhibit("__HANDS_INHIBIT__=delay\n") == "delay"
    assert remote.parse_inhibit("__HANDS_INHIBIT__=weird\n") == "none"
    assert remote.parse_inhibit("") == "none"


def test_a_borrowed_path_is_exported_ahead_of_the_script():
    script = remote.prefix_path("echo hi", "/a b/bin:/usr/bin")

    assert run_sh(script + '\nprintf "%s" "$PATH"') == "hi\n/a b/bin:/usr/bin"


def test_no_path_leaves_the_script_alone():
    assert remote.prefix_path("echo hi", "") == "echo hi"


def test_no_inhibitor_leaves_the_script_alone():
    assert remote.wrap_with_inhibitor("echo hi", "none") == "echo hi"


def test_a_systemd_inhibitor_wraps_the_whole_script():
    wrapped = remote.wrap_with_inhibitor("echo 'a b'; exit 3", "delay")

    assert wrapped.startswith("exec systemd-inhibit --what=sleep")
    assert "--mode=delay" in wrapped
    assert wrapped.endswith("bash -c 'echo '\"'\"'a b'\"'\"'; exit 3'")


def test_a_caffeinate_wrapper_keeps_the_exit_status():
    wrapped = remote.wrap_with_inhibitor("exit 3", "caffeinate")

    assert wrapped == "exec caffeinate -i bash -c 'exit 3'"

"""Dial-out: a machine the host cannot reach offers itself over SSH."""

from __future__ import annotations

import pytest

from hermes_hands import config
from hermes_hands import dialout
from hermes_hands import health
from hermes_hands import remote


def spec_for(host: str, port: int = 22022) -> config.TargetSpec:
    parsed = config.parse_config({
        "default": "mac",
        "targets": {
            "laptop": {
                "kind": "ssh",
                "host": host,
                "port": port,
                "user": "dev",
            },
            "mac": {"kind": "local"},
        },
    })

    return parsed.target("laptop")


# ---- the text a person runs on the machine ----------------------------


def test_the_machine_forwards_its_ssh_port_to_loopback_on_the_host():
    command = dialout.tunnel_command("me@server.example", 22022)

    assert command.startswith("ssh -N ")
    assert "-R 127.0.0.1:22022:127.0.0.1:22" in command
    assert command.endswith(" me@server.example")


def test_a_port_that_cannot_be_forwarded_stops_the_link():
    # Without this, ssh stays up with no forward and looks healthy.
    command = dialout.tunnel_command("me@server.example", 22022)

    assert "ExitOnForwardFailure=yes" in command


def test_the_service_restarts_the_link_when_it_drops():
    unit = dialout.service_unit("me@server.example", 22022)

    assert "Restart=always" in unit
    assert "-R 127.0.0.1:22022:127.0.0.1:22 me@server.example" in unit


@pytest.mark.parametrize("port", [22, 1023, 65536])
def test_a_port_out_of_range_is_refused(port):
    with pytest.raises(dialout.DialOutError, match="the port must be"):
        dialout.tunnel_command("me@server.example", port)


@pytest.mark.parametrize("server", [
    "server.example",
    "@server.example",
    "me@",
    "me@server.example; rm x",
    "me@server example",
    "me@$(id)",
])
def test_a_server_that_is_not_a_plain_login_is_refused(server):
    with pytest.raises(dialout.DialOutError):
        dialout.tunnel_command(server, 22022)


def test_the_target_points_at_the_forwarded_port():
    block = dialout.target_settings("dev", 22022)
    parsed = config.parse_config({
        "default": "laptop",
        "targets": {"laptop": block},
    })

    assert parsed.target("laptop").host == "127.0.0.1"
    assert parsed.target("laptop").port == 22022


# ---- the host key of a machine on loopback ----------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_a_loopback_target_files_its_key_under_its_own_name(host):
    assert spec_for(host).host_key_alias == "hermes-hands-laptop"


def test_a_target_with_a_real_address_keeps_its_own_host_key_name():
    assert spec_for("laptop.example").host_key_alias == ""


def test_a_local_target_has_no_host_key():
    parsed = config.parse_config({
        "default": "mac",
        "targets": {"mac": {"kind": "local"}},
    })

    assert parsed.target("mac").host_key_alias == ""


def test_the_alias_reaches_both_ssh_command_lines():
    spec = spec_for("127.0.0.1")
    wanted = "HostKeyAlias=hermes-hands-laptop"

    assert wanted in remote.build_ssh_options(spec)
    assert wanted in health.build_probe_command(spec)


def test_no_alias_is_passed_for_a_real_address():
    spec = spec_for("laptop.example")

    assert not any(
        "HostKeyAlias" in part for part in health.build_probe_command(spec)
    )

"""`hermes onsite init` builds only settings that will load."""

from __future__ import annotations

import pytest

from onsite import config
from onsite import dialin
from onsite import wizard


def build(**overrides):
    flags = {
        "ssh": ["laptop=dev@laptop.example"],
        "local": ["mac"],
        "prefix": ["laptop=/home/dev/"],
        "path_map": [],
        "key": [],
        "known_hosts": [],
        "default": None,
    }
    flags.update(overrides)

    return wizard.settings_from_flags(**flags)


def test_flags_build_a_block_that_loads():
    parsed = wizard.validate(build())

    assert parsed.default == "mac"
    assert parsed.target("laptop").host == "laptop.example"
    assert parsed.target("laptop").prefixes == ("/home/dev",)


def test_an_ssh_flag_may_carry_a_port():
    name, block = wizard.parse_ssh("box=dev@10.0.0.9:2222")

    assert name == "box"
    assert block == {
        "kind": "ssh",
        "user": "dev",
        "host": "10.0.0.9",
        "port": 2222,
    }


def test_a_map_flag_reads_both_paths():
    settings = build(path_map=["laptop=/Users/dev/Code=/home/dev/Code"])

    assert settings["targets"]["laptop"]["path_map"] == {
        "/Users/dev/Code": "/home/dev/Code",
    }


def test_a_key_flag_is_attached_to_its_target():
    settings = build(key=["laptop=/keys/laptop"])

    assert settings["targets"]["laptop"]["key"] == "/keys/laptop"


def test_a_known_hosts_flag_is_attached_to_its_target():
    settings = build(known_hosts=["laptop=/keys/known"])

    assert settings["targets"]["laptop"]["known_hosts"] == "/keys/known"


def test_no_targets_at_all_says_what_to_do():
    with pytest.raises(wizard.FlagError) as caught:
        build(ssh=[], local=[], prefix=[])

    assert "on a terminal" in str(caught.value)


@pytest.mark.parametrize("value", ["laptop", "laptop=dev", "=dev@h"])
def test_a_malformed_ssh_flag_is_refused(value):
    with pytest.raises(wizard.FlagError):
        wizard.parse_ssh(value)


def test_a_flag_for_an_unknown_target_is_refused():
    with pytest.raises(wizard.FlagError):
        build(prefix=["desktop=/home/dev/"])


def test_no_default_and_two_locals_says_which_flag_to_add():
    settings = build(local=["mac", "mini"])

    with pytest.raises(wizard.FlagError, match="--default NAME"):
        wizard.validate(settings)


def test_no_default_and_no_local_says_which_flag_to_add():
    settings = build(ssh=["laptop=dev@laptop.example"], local=[])

    with pytest.raises(wizard.FlagError, match="--default NAME"):
        wizard.validate(settings)


def test_the_interview_builds_the_common_setup():
    answers = iter([
        "",
        "",
        "laptop.example",
        "dev",
        "",
        "/Users/dev/Development",
        "/home/dev/Development",
        "",
    ])

    settings = wizard.interview(lambda question: next(answers))
    parsed = wizard.validate(settings)

    assert parsed.default == "mac"
    assert parsed.target("laptop").host == "laptop.example"
    assert parsed.target("laptop").dial_in == ""
    assert parsed.target("laptop").prefixes == ("/home/dev",)
    assert parsed.target("laptop").path_map == (
        ("/Users/dev/Development", "/home/dev/Development"),
    )


# ---- a machine that dials in -------------------------------------------


def offer_ports(monkeypatch, *ports):
    """Stand in for the random draw and for the look at each port."""

    left = list(ports)

    monkeypatch.setattr(dialin, "draw_port", lambda: left.pop(0))
    monkeypatch.setattr(dialin, "port_is_free", lambda port: True)


def test_a_dial_in_flag_builds_a_block_that_loads(monkeypatch):
    offer_ports(monkeypatch, 20500)

    settings = build(
        ssh=[],
        prefix=[],
        dial_in=["laptop=dev"],
        server="you@server.example",
    )
    spec = wizard.validate(settings).target("laptop")

    assert spec.dial_in == "you@server.example"
    assert spec.user == "dev"
    assert spec.host == "127.0.0.1"
    assert spec.port == 20500


def test_a_dial_in_flag_needs_the_server():
    with pytest.raises(wizard.FlagError, match="--server USER@ADDRESS"):
        build(ssh=[], prefix=[], dial_in=["laptop=dev"])


def test_two_dial_in_flags_get_two_ports(monkeypatch):
    # The draw offers the first machine's port again.
    offer_ports(monkeypatch, 20500, 20500, 20777)

    settings = build(
        ssh=[],
        prefix=[],
        dial_in=["laptop=dev", "desk=dev"],
        server="you@server.example",
    )

    assert settings["targets"]["laptop"]["port"] == 20500
    assert settings["targets"]["desk"]["port"] == 20777


def test_running_init_again_keeps_a_saved_machines_port(monkeypatch):
    # `init` writes the whole list. A machine that already dials in
    # must come out with the port its own command names.
    offer_ports(monkeypatch, 20999)

    saved = {
        "laptop": {
            "kind": "ssh",
            "host": "127.0.0.1",
            "port": 20500,
            "user": "dev",
            "dial_in": "you@server.example",
        },
    }
    settings = build(
        ssh=[],
        prefix=[],
        dial_in=["laptop=dev"],
        server="you@server.example",
        saved=saved,
    )

    assert settings["targets"]["laptop"]["port"] == 20500


def test_a_dial_in_server_that_is_not_a_plain_login_is_refused(monkeypatch):
    offer_ports(monkeypatch, 20500)

    settings = build(
        ssh=[],
        prefix=[],
        dial_in=["laptop=dev"],
        server="you@server.example; id",
    )

    with pytest.raises(config.ConfigError, match="USER@ADDRESS"):
        wizard.validate(settings)


def test_no_free_port_is_said_with_the_flag_it_came_from(monkeypatch):
    monkeypatch.setattr(dialin, "port_is_free", lambda port: False)

    with pytest.raises(wizard.FlagError, match="--dial-in laptop: no free"):
        build(
            ssh=[],
            prefix=[],
            dial_in=["laptop=dev"],
            server="you@server.example",
        )


def test_a_folder_rule_can_be_given_to_a_machine_that_dials_in(monkeypatch):
    offer_ports(monkeypatch, 20500)

    settings = build(
        ssh=[],
        dial_in=["laptop=dev"],
        server="you@server.example",
    )

    assert settings["targets"]["laptop"]["prefixes"] == ["/home/dev/"]


def test_the_interview_asks_how_a_machine_out_of_reach_gets_in(monkeypatch):
    offer_ports(monkeypatch, 20500)

    answers = iter([
        "",
        "no",
        "dev",
        "you@server.example",
        "",
        "",
        "",
    ])

    settings = wizard.interview(lambda question: next(answers))
    spec = wizard.validate(settings).target("laptop")

    assert spec.dial_in == "you@server.example"
    assert spec.port == 20500
    assert spec.prefixes == ("/home/dev",)


def test_the_block_is_shown_where_it_will_live():
    text = wizard.render_yaml(build())

    assert text.startswith("plugins:\n  entries:\n    onsite:")
    assert "          laptop:\n            kind: ssh" in text
    assert "            prefixes:\n              - /home/dev/" in text

"""`hermes hands init` builds only settings that will load."""

from __future__ import annotations

import pytest

from hermes_hands import config
from hermes_hands import wizard


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


def test_no_default_and_two_locals_fails_validation():
    settings = build(local=["mac", "mini"])

    with pytest.raises(config.ConfigError):
        wizard.validate(settings)


def test_the_interview_builds_the_common_setup():
    answers = iter([
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
    assert parsed.target("laptop").prefixes == ("/home/dev",)
    assert parsed.target("laptop").path_map == (
        ("/Users/dev/Development", "/home/dev/Development"),
    )


def test_the_block_is_shown_where_it_will_live():
    text = wizard.render_yaml(build())

    assert text.startswith("plugins:\n  entries:\n    hermes-hands:")
    assert "          laptop:\n            kind: ssh" in text
    assert "            prefixes:\n              - /home/dev/" in text

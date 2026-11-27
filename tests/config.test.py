"""Settings must load whole or fail loudly with every problem."""

from __future__ import annotations

import pytest

from onsite import config


GOOD = {
    "default": "mac",
    "targets": {
        "laptop": {
            "kind": "ssh",
            "host": "laptop.example",
            "user": "dev",
            "prefixes": ["/home/dev/"],
            "path_map": {"/Users/dev/Development": "/home/dev/Development"},
        },
        "mac": {"kind": "local"},
    },
}


def test_a_good_block_loads():
    parsed = config.parse_config(GOOD)

    assert parsed.default == "mac"
    assert list(parsed.targets) == ["laptop", "mac"]

    laptop = parsed.target("laptop")

    assert laptop.kind == config.KIND_SSH
    assert laptop.prefixes == ("/home/dev",)
    assert laptop.path_map == (
        ("/Users/dev/Development", "/home/dev/Development"),
    )
    assert laptop.port == config.DEFAULT_SSH_PORT
    assert laptop.inhibit_sleep is True
    assert laptop.label == "laptop (dev@laptop.example)"


def test_no_settings_fails_with_the_fix():
    with pytest.raises(config.ConfigError) as caught:
        config.parse_config(None)

    assert "hermes onsite init" in str(caught.value)


def test_missing_targets_is_named():
    with pytest.raises(config.ConfigError) as caught:
        config.parse_config({"default": "mac"})

    assert any("targets" in item for item in caught.value.problems)


def test_every_problem_is_reported_at_once():
    broken = {
        "default": "nowhere",
        "targets": {
            "laptop": {"kind": "ssh", "prefixes": ["relative/path"]},
            "Bad Name": {"kind": "local"},
            "odd": {"kind": "carrier-pigeon"},
            "auto": {"kind": "local"},
        },
    }

    with pytest.raises(config.ConfigError) as caught:
        config.parse_config(broken)

    text = "\n".join(caught.value.problems)

    assert "`laptop.host` is required" in text
    assert "`laptop.user` is required" in text
    assert "not an absolute path" in text
    assert "Bad Name" in text
    assert "`odd.kind`" in text
    assert "reserved" in text
    assert "`nowhere`" in text


def test_unknown_keys_are_refused():
    block = {
        "default": "mac",
        "targets": {"mac": {"kind": "local", "hots": "typo"}},
    }

    with pytest.raises(config.ConfigError) as caught:
        config.parse_config(block)

    assert "`mac.hots` is not a known setting" in caught.value.problems


@pytest.mark.parametrize("port", [0, 70000, "22", True])
def test_bad_ports_are_refused(port):
    block = {
        "default": "box",
        "targets": {
            "box": {"kind": "ssh", "host": "h", "user": "u", "port": port},
        },
    }

    with pytest.raises(config.ConfigError):
        config.parse_config(block)


def test_settings_round_trip():
    parsed = config.parse_config(GOOD)
    written = config.config_to_settings(parsed)

    assert config.parse_config(written) == parsed


# ---- the way to a target's screen -------------------------------------


def laptop_with(**extra) -> dict:
    block = {"kind": "ssh", "host": "laptop.example", "user": "dev"}
    block.update(extra)

    return {
        "default": "mac",
        "targets": {"laptop": block, "mac": {"kind": "local"}},
    }


def test_a_target_is_left_to_choose_its_screen_route():
    spec = config.parse_config(GOOD).target("laptop")

    assert spec.screen == config.SCREEN_AUTO
    assert spec.screen_port == config.DEFAULT_SCREEN_PORT


def test_a_screen_route_can_be_set():
    parsed = config.parse_config(laptop_with(
        screen="sharing",
        screen_port=5901,
    ))
    spec = parsed.target("laptop")

    assert spec.screen == config.SCREEN_SHARING
    assert spec.screen_port == 5901


def test_an_unknown_screen_route_is_refused_with_the_choices():
    with pytest.raises(config.ConfigError) as caught:
        config.parse_config(laptop_with(screen="vnc"))

    assert "auto, driver, sharing" in str(caught.value)


def test_a_screen_port_out_of_range_is_named_by_its_own_key():
    with pytest.raises(config.ConfigError) as caught:
        config.parse_config(laptop_with(screen_port=70000))

    assert "`laptop.screen_port` must be between" in str(caught.value)


def test_a_password_has_no_place_in_the_settings():
    # It comes from Hermes' own secret store, by a declared name.
    with pytest.raises(config.ConfigError) as caught:
        config.parse_config(laptop_with(screen_password="s3cret"))

    assert "`laptop.screen_password` is not a known setting" in str(
        caught.value
    )


def test_screen_settings_are_written_only_when_they_differ():
    plain = config.parse_config(GOOD).target("laptop")
    custom = config.parse_config(laptop_with(
        screen="sharing",
        screen_port=5901,
    )).target("laptop")

    assert "screen" not in config.target_to_settings(plain)
    assert "screen_port" not in config.target_to_settings(plain)
    assert config.target_to_settings(custom)["screen"] == "sharing"
    assert config.target_to_settings(custom)["screen_port"] == 5901


@pytest.mark.parametrize("key", ["user", "host"])
def test_a_login_that_ssh_would_read_as_an_option_is_refused(key):
    # `ssh` takes `-oProxyCommand=...@host` as an option, and that
    # one runs a program on the host.
    block = {"kind": "ssh", "host": "laptop.example", "user": "dev"}
    block[key] = "-oProxyCommand=touch /tmp/x"

    with pytest.raises(config.ConfigError) as caught:
        config.parse_config({"default": "laptop", "targets": {"laptop": block}})

    assert caught.value.problems == [
        f"`laptop.{key}` cannot start with a hyphen",
    ]

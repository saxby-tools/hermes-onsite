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


@pytest.mark.parametrize("key", ["key", "known_hosts"])
def test_a_file_path_that_sshfs_would_read_as_two_options_is_refused(key):
    # `sshfs` splits its option list at commas, and ProxyCommand runs
    # a program on the host.
    block = {"kind": "ssh", "host": "laptop.example", "user": "dev"}
    block[key] = "/keys/id,ProxyCommand=touch /tmp/x"

    with pytest.raises(config.ConfigError) as caught:
        config.parse_config({"default": "laptop", "targets": {"laptop": block}})

    assert caught.value.problems == [
        f"`laptop.{key}` cannot have a comma in it",
    ]


# ---- a computer that dials in ------------------------------------------


def dial_in(**extra) -> dict:
    block = {
        "kind": "ssh",
        "host": "127.0.0.1",
        "port": 22022,
        "user": "dev",
        "dial_in": "you@server.example",
    }
    block.update(extra)

    return block


def with_targets(**targets) -> dict:
    return {"default": "mac", "targets": {**targets, "mac": {"kind": "local"}}}


def load_laptop(**extra) -> config.TargetSpec:
    settings = with_targets(laptop=dial_in(**extra))

    return config.parse_config(settings).target("laptop")


def problems_of(settings: dict) -> list[str]:
    with pytest.raises(config.ConfigError) as caught:
        config.parse_config(settings)

    return caught.value.problems


def test_a_computer_that_dials_in_loads():
    spec = load_laptop()

    assert spec.dial_in == "you@server.example"
    assert spec.host == "127.0.0.1"
    assert spec.port == 22022


def test_its_label_does_not_show_the_servers_own_address():
    assert load_laptop().label == "laptop (dev, dials in)"


def test_its_host_key_is_filed_under_its_own_name():
    # Every such computer is at 127.0.0.1. The name is what keeps a
    # port that changed hands from being taken for the same computer.
    assert load_laptop().host_key_alias == "onsite-laptop"


def test_a_target_the_server_dials_has_no_dial_in():
    assert config.parse_config(GOOD).target("laptop").dial_in == ""


def test_dial_in_settings_round_trip():
    parsed = config.parse_config(with_targets(laptop=dial_in()))
    written = config.config_to_settings(parsed)

    assert written["targets"]["laptop"]["dial_in"] == "you@server.example"
    assert config.parse_config(written) == parsed


def test_two_computers_cannot_dial_in_on_one_port():
    settings = with_targets(laptop=dial_in(), desktop=dial_in())

    assert problems_of(settings) == [
        "`laptop` and `desktop` both use port 22022. A computer that "
        "dials in needs a port of its own",
    ]


def test_a_dial_in_port_cannot_be_another_loopback_targets():
    box = {"kind": "ssh", "host": "localhost", "port": 22022, "user": "dev"}
    found = problems_of(with_targets(box=box, laptop=dial_in()))

    assert "`box` and `laptop` both use port 22022" in found[0]


def test_two_plain_loopback_targets_may_share_a_port_number():
    # Not a dial-in matter. The rig's own containers are set up so.
    one = {"kind": "ssh", "host": "127.0.0.1", "port": 2222, "user": "dev"}
    two = {"kind": "ssh", "host": "::1", "port": 2222, "user": "dev"}

    config.parse_config(with_targets(one=one, two=two))


def test_the_same_port_on_a_machine_elsewhere_is_no_clash():
    box = {"kind": "ssh", "host": "box.example", "port": 22022, "user": "dev"}

    config.parse_config(with_targets(box=box, laptop=dial_in()))


@pytest.mark.parametrize("host", ["laptop.example", "localhost", "::1"])
def test_a_dial_in_computer_must_be_at_the_loopback_address(host):
    # That address and no other name for it. The link listens there
    # alone, and `localhost` may be `::1`, where somebody else could
    # be the one to answer.
    settings = with_targets(laptop=dial_in(host=host))

    assert problems_of(settings) == [
        "`laptop.host` must be 127.0.0.1 for a computer that dials in",
    ]


def test_a_name_with_a_line_end_in_it_is_not_a_name():
    # The name is what a loopback target's host key is filed under.
    settings = {"default": "mac", "targets": {"laptop\n": dial_in()}}

    assert "must be lowercase letters" in problems_of(settings)[0]


def test_a_dial_in_computer_needs_a_port():
    block = dial_in()
    del block["port"]

    assert problems_of(with_targets(laptop=block)) == [
        "`laptop.port` is required for a computer that dials in",
    ]


def test_a_dial_in_port_must_be_one_an_account_may_listen_on():
    assert problems_of(with_targets(laptop=dial_in(port=222))) == [
        "`laptop.port` must be 1024 or higher for a computer that dials in",
    ]


@pytest.mark.parametrize("server", [
    "you@server.example; id",
    "-oProxyCommand=id@server.example",
    "you@",
    7,
])
def test_a_server_that_is_not_a_plain_login_is_refused(server):
    found = problems_of(with_targets(laptop=dial_in(dial_in=server)))

    assert len(found) == 1
    assert found[0].startswith("`laptop.dial_in`: the server is named as")


def test_only_an_ssh_target_dials_in():
    mac = {"kind": "local", "dial_in": "you@server.example"}
    settings = {"default": "mac", "targets": {"mac": mac}}

    assert problems_of(settings) == [
        "`mac.dial_in` is only for an ssh target",
    ]

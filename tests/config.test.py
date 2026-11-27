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


# ---- environments ----------------------------------------------------


def with_environment(**block) -> dict:
    settings = {"kind": "environment", "image": "debian:stable-slim"}
    settings.update(block)

    return {
        "default": "mac",
        "targets": {"web": settings, "mac": {"kind": "local"}},
    }


def problems_of(settings: dict) -> str:
    with pytest.raises(config.ConfigError) as caught:
        config.parse_config(settings)

    return str(caught.value)


def test_an_environment_needs_only_an_image():
    web = config.parse_config(with_environment()).target("web")

    assert web.kind == config.KIND_ENVIRONMENT
    assert web.image == "debian:stable-slim"
    assert web.repository == ""
    assert web.setup == ""
    assert web.env == ()
    assert web.workspace == ""
    assert web.label == "web (sandbox, debian:stable-slim)"


def test_an_environment_has_no_network_until_it_is_given_one():
    closed = config.parse_config(with_environment()).target("web")
    opened = config.parse_config(
        with_environment(network=True),
    ).target("web")

    assert closed.network is False
    assert opened.network is True


def test_an_environment_reads_every_setting_of_its_own():
    web = config.parse_config(with_environment(
        repository="https://host.example/you/project.git",
        setup="npm ci\n",
        env={"NODE_ENV": "development", "PORT": 3000},
        workspace="/srv/onsite/web/",
    )).target("web")

    assert web.repository == "https://host.example/you/project.git"
    assert web.setup == "npm ci"
    assert web.env == (("NODE_ENV", "development"), ("PORT", "3000"))
    assert web.workspace == "/srv/onsite/web"


def test_an_environment_with_no_image_is_named():
    settings = with_environment()

    del settings["targets"]["web"]["image"]

    assert "`web.image` is required for an environment" in problems_of(
        settings
    )


@pytest.mark.parametrize("image", ["--privileged", "a b", "", 7])
def test_an_image_that_could_be_read_as_an_option_is_refused(image):
    assert "`web.image`" in problems_of(with_environment(image=image))


@pytest.mark.parametrize("repository", [
    "https://host.example/you/project.git",
    "ssh://git@host.example/you/project.git",
    "git@host.example:you/project.git",
    "/srv/git/project.git",
])
def test_a_repository_can_be_named_four_ways(repository):
    web = config.parse_config(
        with_environment(repository=repository),
    ).target("web")

    assert web.repository == repository


@pytest.mark.parametrize("repository", [
    "--upload-pack=touch /tmp/x",
    "ext::sh -c id",
    "http://host.example/you/project.git",
    "project",
    "https://host.example/a b",
    7,
])
def test_a_repository_git_could_misread_is_refused(repository):
    assert "`web.repository`" in problems_of(
        with_environment(repository=repository),
    )


@pytest.mark.parametrize("env", [
    ["A=1"],
    {"1A": "x"},
    {"A B": "x"},
    {"A": ["x"]},
    {"A": True},
])
def test_variables_that_are_not_plain_are_refused(env):
    assert "`web.env" in problems_of(with_environment(env=env))


@pytest.mark.parametrize("workspace", ["sessions", "/", 7])
def test_a_workspace_must_be_a_full_path_and_not_the_root(workspace):
    assert "`web.workspace`" in problems_of(
        with_environment(workspace=workspace),
    )


def test_a_folder_rule_on_an_environment_is_refused():
    found = problems_of(with_environment(prefixes=["/srv/work"]))

    assert "`web.prefixes` is not for an environment" in found


def test_an_environment_setting_on_a_machine_is_refused():
    settings = with_environment()

    settings["targets"]["mac"]["image"] = "debian:stable-slim"
    settings["targets"]["mac"]["network"] = True

    found = problems_of(settings)

    assert "`mac.image` is only for an environment" in found
    assert "`mac.network` is only for an environment" in found


def test_an_environment_cannot_be_the_default():
    settings = with_environment()

    settings["default"] = "web"

    assert "`default` names `web`, an environment" in problems_of(settings)


def test_an_environment_is_written_back_as_it_was_read():
    block = {
        "kind": "environment",
        "image": "debian:stable-slim",
        "repository": "/srv/git/project.git",
        "setup": "make deps",
        "network": True,
        "env": {"A": "1"},
        "workspace": "/srv/onsite/web",
    }
    parsed = config.parse_config(with_environment(**block))

    assert config.target_to_settings(parsed.target("web")) == block


def test_an_environment_left_plain_is_written_plain():
    parsed = config.parse_config(with_environment())

    assert config.target_to_settings(parsed.target("web")) == {
        "kind": "environment",
        "image": "debian:stable-slim",
    }

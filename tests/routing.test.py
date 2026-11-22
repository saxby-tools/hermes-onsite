"""Folder routing: override, then longest match, then the default."""

from __future__ import annotations

import pytest

from hermes_hands import config
from hermes_hands import routing


SETTINGS = {
    "default": "mac",
    "targets": {
        "laptop": {
            "kind": "ssh",
            "host": "laptop.example",
            "user": "dev",
            "prefixes": ["/home/dev/"],
            "path_map": {"/Users/dev/Development": "/home/dev/Development"},
        },
        "build": {
            "kind": "ssh",
            "host": "build.example",
            "user": "dev",
            "prefixes": ["/home/dev/Builds"],
        },
        "mac": {"kind": "local"},
    },
}


@pytest.fixture
def parsed():
    return config.parse_config(SETTINGS)


def test_a_folder_under_a_prefix_goes_to_that_target(parsed):
    route = routing.route("/home/dev/Development/app", parsed)

    assert route.target == "laptop"
    assert route.cwd == "/home/dev/Development/app"
    assert route.reason == routing.REASON_PREFIX


def test_the_prefix_itself_matches(parsed):
    assert routing.route("/home/dev", parsed).target == "laptop"


def test_a_lookalike_folder_does_not_match(parsed):
    route = routing.route("/home/developer/app", parsed)

    assert route.target == "mac"
    assert route.reason == routing.REASON_DEFAULT


def test_the_longest_match_wins(parsed):
    route = routing.route("/home/dev/Builds/nightly", parsed)

    assert route.target == "build"


def test_a_host_path_is_translated_through_the_map(parsed):
    route = routing.route("/Users/dev/Development/app/src", parsed)

    assert route.target == "laptop"
    assert route.cwd == "/home/dev/Development/app/src"
    assert route.reason == routing.REASON_PATH_MAP


def test_anything_else_stays_on_the_default(parsed):
    route = routing.route("/Users/dev/Documents", parsed)

    assert route.target == "mac"
    assert route.cwd == "/Users/dev/Documents"


@pytest.mark.parametrize("cwd", ["", "~", "relative/dir"])
def test_an_unusable_folder_goes_to_the_default_home(parsed, cwd):
    route = routing.route(cwd, parsed)

    assert route.target == "mac"
    assert route.cwd == routing.HOME


def test_a_pinned_target_wins_over_the_folder(parsed):
    route = routing.resolve(
        "/Users/dev/Documents",
        parsed,
        pinned="laptop",
        pin_reason=routing.REASON_OVERRIDE,
    )

    assert route.target == "laptop"
    assert route.reason == routing.REASON_OVERRIDE

    # The folder does not exist on the laptop, so it is not guessed.
    assert route.cwd == routing.HOME


def test_a_pinned_target_still_translates_the_folder(parsed):
    route = routing.resolve(
        "/Users/dev/Development/app",
        parsed,
        pinned="laptop",
    )

    assert route.cwd == "/home/dev/Development/app"


def test_a_stale_pin_falls_back_to_the_folder_rules(parsed):
    route = routing.resolve("/home/dev/app", parsed, pinned="retired")

    assert route.target == "laptop"


def test_a_tie_goes_to_the_target_listed_first():
    tied = config.parse_config({
        "default": "one",
        "targets": {
            "one": {"kind": "local", "prefixes": ["/srv/work"]},
            "two": {"kind": "local", "prefixes": ["/srv/work"]},
        },
    })

    assert routing.route("/srv/work/x", tied).target == "one"


def test_a_root_prefix_catches_everything():
    wide = config.parse_config({
        "default": "box",
        "targets": {
            "box": {
                "kind": "ssh",
                "host": "h",
                "user": "u",
                "prefixes": ["/"],
            },
        },
    })

    assert routing.route("/var/log", wide).cwd == "/var/log"

"""Finding a target's screen: what was found, and what is missing."""

from __future__ import annotations

import pytest

from onsite import config
from onsite import mirror
from onsite import screen


def probe(*fields: str) -> bytes:
    return "\0".join(fields).encode() + b"\0"


WAYLAND = probe(
    "os=Linux",
    "user=dev",
    "XDG_RUNTIME_DIR=/run/user/1000",
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
    "DISPLAY=:0",
    "WAYLAND_DISPLAY=wayland-0",
    "XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.ABC",
    "XDG_CURRENT_DESKTOP=GNOME",
    "driver=/usr/bin/cua-driver",
)
X11 = probe("os=Linux", "user=dev", "DISPLAY=:1", "driver=")
HEADLESS = probe("os=Linux", "user=dev", "driver=/usr/bin/cua-driver")
MAC = probe("os=Darwin", "user=dev", "console=dev", "driver=/opt/cua-driver")
MAC_AWAY = probe("os=Darwin", "user=dev", "console=root", "driver=/opt/x")


# ---- reading the probe ------------------------------------------------


@pytest.mark.parametrize("raw, kind", [
    (WAYLAND, screen.SEAT_WAYLAND),
    (X11, screen.SEAT_X11),
    (HEADLESS, screen.SEAT_NONE),
    (MAC, screen.SEAT_MACOS),
    (MAC_AWAY, screen.SEAT_NONE),
])
def test_the_desktop_is_named_from_what_was_found(raw, kind):
    assert screen.parse_probe(raw).kind == kind


def test_only_the_seat_variables_are_kept():
    seat = screen.parse_probe(probe("os=Linux", "DISPLAY=:0", "SECRET=x"))

    assert seat.variables == {"DISPLAY": ":0"}


def test_a_value_may_hold_an_equals_sign():
    seat = screen.parse_probe(WAYLAND)

    assert seat.variables["DBUS_SESSION_BUS_ADDRESS"] == (
        "unix:path=/run/user/1000/bus"
    )


def test_the_first_copy_of_a_name_wins():
    seat = screen.parse_probe(probe("os=Linux", "DISPLAY=:0", "DISPLAY=:9"))

    assert seat.variables["DISPLAY"] == ":0"


def test_a_target_that_cannot_be_asked_says_so(monkeypatch):
    def refuse(spec, script, timeout):
        raise mirror.MirrorError("Connection refused")

    monkeypatch.setattr(screen, "run_remote", refuse)

    class Spec:
        label = "laptop (dev@laptop.example)"

    with pytest.raises(screen.ScreenError, match="could not be asked"):
        screen.read_seat(Spec())


# ---- what is missing --------------------------------------------------


def test_a_ready_target_has_no_problems():
    assert screen.find_problems(screen.parse_probe(WAYLAND)) == []


def test_a_missing_driver_is_named_and_never_installed_for_you():
    problems = screen.find_problems(screen.parse_probe(X11))

    assert len(problems) == 1
    assert "is not installed there" in problems[0]
    assert "never installs it" in problems[0]


def test_no_desktop_session_is_a_problem():
    problems = screen.find_problems(screen.parse_probe(HEADLESS))

    assert "no desktop session was found" in problems[0]


def test_a_mac_with_someone_else_at_the_screen_is_a_problem():
    problems = screen.find_problems(screen.parse_probe(MAC_AWAY))

    assert "nobody is logged in at the screen as `dev`" in problems[0]


def test_wayland_and_macos_come_with_notes():
    assert len(screen.find_notes(screen.parse_probe(WAYLAND))) == 2
    assert len(screen.find_notes(screen.parse_probe(MAC))) == 1
    assert screen.find_notes(screen.parse_probe(X11)) == []


# ---- what runs on the target ------------------------------------------


def test_the_driver_joins_the_desktop_before_it_starts():
    script = screen.build_driver_script(screen.parse_probe(WAYLAND))

    assert "export DISPLAY=:0" in script
    assert "export XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.ABC" in script
    assert script.endswith("exec /usr/bin/cua-driver mcp --no-overlay")


def test_telemetry_is_off():
    script = screen.build_driver_script(screen.parse_probe(MAC))

    assert "export CUA_DRIVER_RS_TELEMETRY_ENABLED=0" in script


def test_wayland_input_is_only_on_when_asked_for():
    seat = screen.parse_probe(WAYLAND)

    assert "ENABLE_WAYLAND" not in screen.build_driver_script(seat)
    assert "export CUA_DRIVER_RS_ENABLE_WAYLAND=1" in (
        screen.build_driver_script(seat, native_wayland=True)
    )


def test_a_value_with_a_space_or_a_quote_cannot_break_out():
    seat = screen.parse_probe(probe(
        "os=Linux",
        "DISPLAY=:0; touch x",
        "XAUTHORITY=/tmp/it's here",
        "driver=/opt/my tools/cua-driver",
    ))
    script = screen.build_driver_script(seat)

    assert "export DISPLAY=':0; touch x'" in script
    assert "exec '/opt/my tools/cua-driver' mcp --no-overlay" in script


def test_no_driver_means_no_script():
    with pytest.raises(screen.ScreenError, match="is not installed"):
        screen.build_driver_script(screen.parse_probe(X11))


# ---- which way to the screen ------------------------------------------


def sharing_spec(setting: str = config.SCREEN_AUTO) -> config.TargetSpec:
    return config.TargetSpec(
        name="laptop",
        kind=config.KIND_SSH,
        host="laptop.example",
        user="dev",
        screen=setting,
    )


def test_left_to_choose_a_target_with_a_driver_uses_it():
    seat = screen.parse_probe(WAYLAND)

    assert screen.pick_route(config.SCREEN_AUTO, seat) == config.SCREEN_DRIVER


def test_left_to_choose_a_target_with_no_driver_uses_screen_sharing():
    seat = screen.parse_probe(X11)

    assert screen.pick_route(config.SCREEN_AUTO, seat) == config.SCREEN_SHARING


def test_a_set_route_is_kept_whatever_the_target_has():
    seat = screen.parse_probe(WAYLAND)

    assert (
        screen.pick_route(config.SCREEN_SHARING, seat)
        == config.SCREEN_SHARING
    )


def test_screen_sharing_that_answers_has_no_problems():
    seat = screen.parse_probe(X11)

    assert screen.find_route_problems(
        sharing_spec(),
        config.SCREEN_SHARING,
        seat,
        "",
    ) == []


def test_left_to_choose_with_neither_way_working_names_both():
    seat = screen.parse_probe(X11)

    problems = screen.find_route_problems(
        sharing_spec(),
        config.SCREEN_SHARING,
        seat,
        "Connection refused",
    )

    assert "did not answer on port 5900: Connection refused" in problems[0]
    assert "is not installed there" in problems[1]


def test_set_to_sharing_a_missing_driver_is_not_a_problem():
    seat = screen.parse_probe(X11)

    problems = screen.find_route_problems(
        sharing_spec(config.SCREEN_SHARING),
        config.SCREEN_SHARING,
        seat,
        "Connection refused",
    )

    assert len(problems) == 1


def test_the_driver_route_keeps_the_drivers_own_problems():
    seat = screen.parse_probe(X11)

    problems = screen.find_route_problems(
        sharing_spec(config.SCREEN_DRIVER),
        config.SCREEN_DRIVER,
        seat,
        "Connection refused",
    )

    assert problems == screen.find_problems(seat)


def test_screen_sharing_comes_with_a_note_that_it_sees_pixels_only():
    seat = screen.parse_probe(X11)
    notes = screen.find_route_notes(config.SCREEN_SHARING, seat)

    assert "pixels only" in notes[0]

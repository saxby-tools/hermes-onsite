"""Clicks, keys and pictures for a screen reached by screen sharing."""

from __future__ import annotations

import io
import sys
import time

import pytest

from onsite import config
from onsite import rfb
from onsite import screenshare


class FakeClient:
    """Stands in for the RFB client and records what it was told."""

    def __init__(self, width: int = 200, height: int = 100):
        self.width = width
        self.height = height
        self.frame = bytearray(width * height * 4)
        self.events: list[tuple] = []
        self.refreshed = 0

    def refresh(self) -> None:
        self.refreshed += 1

    def pointer(self, x: int, y: int, buttons: int = 0) -> None:
        self.events.append(("pointer", x, y, buttons))

    def key(self, number: int, down: bool) -> None:
        self.events.append(("key", number, down))

    def close(self) -> None:
        self.events.append(("close",))


def sharer(client: FakeClient, limit: int = 0) -> screenshare.Sharer:
    return screenshare.Sharer(client, limit, pause=lambda seconds: None)


def changes(events: list[tuple]) -> list[tuple]:
    """The events with each run of repeats counted once.

    The pointer reports every state more than once, on purpose. Most
    tests care about the order of states, not the repeats.
    """

    kept: list[tuple] = []

    for event in events:
        if kept and kept[-1] == event:
            continue

        kept.append(event)

    return kept


# ---- keys -------------------------------------------------------------


def test_a_letter_is_its_own_key_number():
    assert screenshare.key_for_character("a") == ord("a")
    assert screenshare.key_for_character("A") == ord("A")


def test_a_newline_types_the_return_key():
    assert screenshare.key_for_character("\n") == screenshare.KEY_RETURN


def test_a_character_past_latin_is_sent_by_its_unicode_number():
    assert screenshare.key_for_character("€") == 0x01000000 | 0x20AC


def test_a_control_code_is_not_typed():
    with pytest.raises(ValueError):
        screenshare.key_for_character("\x07")


def test_named_keys_are_known_in_any_case():
    assert screenshare.key_for_name("Return") == screenshare.KEY_RETURN
    assert screenshare.key_for_name("CMD") == screenshare.KEYS["super"]


def test_function_keys_are_counted_from_f1():
    assert screenshare.key_for_name("f1") == screenshare.KEY_F1
    assert screenshare.key_for_name("F12") == screenshare.KEY_F1 + 11


def test_an_unknown_key_name_is_refused_by_name():
    with pytest.raises(ValueError) as caught:
        screenshare.key_for_name("hyper")

    assert "`hyper`" in str(caught.value)


def test_a_combination_is_split_on_the_plus_sign():
    assert screenshare.split_combination("ctrl+alt+t") == [
        "ctrl",
        "alt",
        "t",
    ]


def test_a_plus_sign_can_itself_be_the_key():
    assert screenshare.split_combination("ctrl++") == ["ctrl", "+"]
    assert screenshare.split_combination("+") == ["+"]


@pytest.mark.parametrize("keys", [
    "cmd+shift+q",
    "win+shift+q",
    "super+shift+Q",
    "meta+option+backspace",
    "cmd+l",
    "win+l",
    "ctrl+opt+delete",
    "control+alt+del",
    "opt+f4",
    "alt+F4",
    "cmd+ctrl+q",
    "cmd+option+shift+q",
])
def test_a_blocked_combination_is_blocked_under_every_name(keys):
    # Hermes blocks these by name. Here `cmd` and `win` are one key
    # and `opt` is `alt`, so each has to be caught again by key.
    with pytest.raises(ValueError) as caught:
        screenshare.parse_keys(keys)

    assert "never sent" in str(caught.value)


@pytest.mark.parametrize("keys", ["cmd+q", "ctrl+l", "alt+f5", "shift+q"])
def test_a_harmless_neighbour_of_a_blocked_combination_goes_through(keys):
    assert len(screenshare.parse_keys(keys)) == 2


def test_a_blocked_combination_sends_no_key_at_all():
    client = FakeClient()

    with pytest.raises(ValueError):
        sharer(client).press("win+shift+q")

    assert client.events == []


def test_no_key_at_all_is_refused():
    with pytest.raises(ValueError):
        screenshare.parse_keys("  ")


# ---- the picture's size -----------------------------------------------


def test_a_screen_within_the_limit_is_shown_as_it_is():
    assert screenshare.fit(1280, 800, 1456) == (1280, 800)


def test_a_larger_screen_is_shrunk_by_its_longest_side():
    assert screenshare.fit(2912, 1820, 1456) == (1456, 910)


def test_no_limit_means_no_shrinking():
    assert screenshare.fit(5000, 3000, 0) == (5000, 3000)


def test_a_picture_holds_the_frames_colours():
    image_module = pytest.importorskip("PIL.Image")

    # Two pixels: blue, green, red, unused. First is red, second blue.
    frame = bytes([0, 0, 255, 0, 255, 0, 0, 0])

    picture = screenshare.encode_picture(frame, 2, 1, 0)
    image = image_module.open(io.BytesIO(picture.png))

    assert (picture.width, picture.height) == (2, 1)
    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert image.getpixel((1, 0)) == (0, 0, 255)


def test_a_picture_of_a_large_screen_is_made_smaller():
    image_module = pytest.importorskip("PIL.Image")

    picture = screenshare.encode_picture(bytes(400 * 200 * 4), 400, 200, 100)
    image = image_module.open(io.BytesIO(picture.png))

    assert (picture.width, picture.height) == (100, 50)
    assert image.size == (100, 50)


def test_a_hermes_without_the_picture_package_says_so(monkeypatch):
    # A None entry makes Python refuse the import, as if it were gone.
    monkeypatch.setitem(sys.modules, "PIL", None)

    with pytest.raises(screenshare.SharingError) as caught:
        screenshare.encode_picture(bytes(4), 1, 1, 0)

    assert "Pillow" in str(caught.value)


# ---- pointer and keyboard ---------------------------------------------


def test_a_capture_reads_the_screen_first():
    pytest.importorskip("PIL.Image")

    client = FakeClient(4, 2)

    picture = sharer(client).capture()

    assert client.refreshed == 1
    assert (picture.width, picture.height) == (4, 2)


def test_a_click_moves_there_presses_and_lets_go():
    client = FakeClient()

    sharer(client).click(30, 40)

    assert changes(client.events) == [
        ("pointer", 30, 40, 0),
        ("pointer", 30, 40, rfb.BUTTON_LEFT),
        ("pointer", 30, 40, 0),
    ]


def test_every_pointer_state_is_reported_more_than_once():
    # x11vnc plays a queued event when the next pointer event comes,
    # and can drop one press of a double click.
    client = FakeClient()
    times = 1 + screenshare.POINTER_NUDGES

    sharer(client).click(30, 40)

    assert client.events == (
        [("pointer", 30, 40, 0)] * times
        + [("pointer", 30, 40, rfb.BUTTON_LEFT)] * times
        + [("pointer", 30, 40, 0)] * times
    )


def test_a_place_in_a_shrunk_picture_is_scaled_back_to_the_screen():
    # The picture is half the screen's size, so each step in it is
    # two on the screen.
    client = FakeClient(200, 100)

    sharer(client, limit=100).click(30, 40)

    assert client.events[0] == ("pointer", 60, 80, 0)


def test_a_double_click_presses_twice():
    client = FakeClient()

    sharer(client).click(5, 5, count=2)

    presses = [
        event
        for event in changes(client.events)
        if event[3:] == (rfb.BUTTON_LEFT,)
    ]

    assert len(presses) == 2


def test_a_right_click_uses_the_right_button():
    client = FakeClient()

    sharer(client).click(5, 5, button="right")

    assert ("pointer", 5, 5, rfb.BUTTON_RIGHT) in client.events


def test_an_unknown_button_is_refused_before_anything_is_sent():
    client = FakeClient()

    with pytest.raises(ValueError):
        sharer(client).click(5, 5, button="thumb")

    assert client.events == []


def test_modifiers_are_held_around_a_click_and_let_go_in_reverse():
    client = FakeClient()
    shift = screenshare.KEYS["shift"]
    ctrl = screenshare.KEYS["ctrl"]

    sharer(client).click(5, 5, modifiers=["ctrl", "shift"])

    assert client.events[:2] == [("key", ctrl, True), ("key", shift, True)]
    assert client.events[-2:] == [("key", shift, False), ("key", ctrl, False)]


def test_modifiers_stay_down_until_the_click_has_been_played():
    # Let go any sooner and a queued click would arrive without them.
    client = FakeClient()
    shift = screenshare.KEYS["shift"]

    sharer(client).click(5, 5, modifiers=["shift"])

    pointer_events = [
        index
        for index, event in enumerate(client.events)
        if event[0] == "pointer"
    ]

    assert client.events.index(("key", shift, False)) > pointer_events[-1]


def test_a_drag_travels_with_the_button_down_and_lets_go_at_the_end():
    client = FakeClient()

    sharer(client).drag((10, 10), (130, 70))

    travel = changes(client.events)

    assert travel[0] == ("pointer", 10, 10, 0)
    assert travel[1] == ("pointer", 10, 10, rfb.BUTTON_LEFT)
    assert travel[-2] == ("pointer", 130, 70, rfb.BUTTON_LEFT)
    assert travel[-1] == ("pointer", 130, 70, 0)
    assert len(travel) == 3 + screenshare.DRAG_STEPS


def test_a_scroll_turns_the_wheel_once_for_each_step():
    client = FakeClient()

    sharer(client).scroll("down", amount=3, x=50, y=60)

    turns = [
        event
        for event in changes(client.events)
        if event == ("pointer", 50, 60, rfb.WHEEL_DOWN)
    ]

    assert len(turns) == 3


def test_a_scroll_with_no_place_happens_where_the_pointer_was_left():
    client = FakeClient()
    screen = sharer(client)

    screen.click(70, 20)
    client.events.clear()
    screen.scroll("up", amount=1)

    assert ("pointer", 70, 20, rfb.WHEEL_UP) in client.events


def test_a_first_scroll_with_no_place_happens_mid_screen():
    # Not at the top-left corner, which opens things on many desktops.
    client = FakeClient(200, 100)

    sharer(client).scroll("down", amount=1)

    assert ("pointer", 100, 50, rfb.WHEEL_DOWN) in client.events
    assert not any(event[1:3] == (0, 0) for event in client.events)


def test_a_scroll_given_one_side_keeps_the_other_where_it_was():
    client = FakeClient(200, 100)
    screen = sharer(client)

    screen.click(70, 20)
    client.events.clear()
    screen.scroll("down", amount=1, y=90)

    assert ("pointer", 70, 90, rfb.WHEEL_DOWN) in client.events


def test_an_unknown_scroll_direction_is_refused():
    with pytest.raises(ValueError):
        sharer(FakeClient()).scroll("sideways")


def test_typing_presses_and_releases_each_character_in_order():
    client = FakeClient()

    sharer(client).type_text("hi\n")

    assert client.events == [
        ("key", ord("h"), True),
        ("key", ord("h"), False),
        ("key", ord("i"), True),
        ("key", ord("i"), False),
        ("key", screenshare.KEY_RETURN, True),
        ("key", screenshare.KEY_RETURN, False),
    ]


def test_text_with_a_control_code_types_nothing_at_all():
    # Half a command typed into a terminal is worse than none.
    client = FakeClient()

    with pytest.raises(ValueError):
        sharer(client).type_text("ls\x07 -la")

    assert client.events == []


def test_a_combination_goes_down_in_order_and_up_in_reverse():
    client = FakeClient()
    ctrl = screenshare.KEYS["ctrl"]

    sharer(client).press("ctrl+s")

    assert client.events == [
        ("key", ctrl, True),
        ("key", ord("s"), True),
        ("key", ord("s"), False),
        ("key", ctrl, False),
    ]


# ---- the link ---------------------------------------------------------


def shell_link(monkeypatch, script: str, timeout: float = 5):
    """A link to a local shell, standing in for `ssh`."""

    monkeypatch.setattr(screenshare, "SSH_BINARY", "sh")

    return screenshare.SshLink(["-c", script], timeout)


def test_a_link_reads_exactly_what_was_asked_for(monkeypatch):
    link = shell_link(monkeypatch, "printf hello; sleep 2")

    assert link.read_exact(2) == b"he"
    assert link.read_exact(3) == b"llo"

    link.close()


def test_a_link_carries_what_is_written(monkeypatch):
    link = shell_link(monkeypatch, "cat")

    link.write(b"ping")

    assert link.read_exact(4) == b"ping"

    link.close()


def test_a_link_that_closes_reports_what_ssh_said(monkeypatch):
    link = shell_link(monkeypatch, "echo 'connect failed' >&2; exit 1")

    with pytest.raises(screenshare.LinkError) as caught:
        link.read_exact(1)

    assert "connect failed" in str(caught.value)

    link.close()


def test_a_link_whose_answer_runs_past_its_deadline_gives_up(monkeypatch):
    # The link is not silent for long enough to give up on that
    # count. The caller's own deadline is what ends it.
    link = shell_link(monkeypatch, "sleep 5", timeout=30)

    with pytest.raises(screenshare.LinkError) as caught:
        link.read_exact(1, until=time.monotonic() + 0.2)

    assert "did not finish in time" in str(caught.value)

    link.close()


def test_a_machine_with_no_ssh_program_says_so(monkeypatch):
    monkeypatch.setattr(screenshare, "SSH_BINARY", "no-such-program-here")

    with pytest.raises(screenshare.LinkError) as caught:
        screenshare.SshLink([])

    assert "could not be started" in str(caught.value)


def test_a_link_that_says_nothing_gives_up_in_time(monkeypatch):
    link = shell_link(monkeypatch, "sleep 5", timeout=0.2)

    with pytest.raises(screenshare.LinkError) as caught:
        link.read_exact(1)

    assert "no answer" in str(caught.value)

    link.close()


# ---- signing in -------------------------------------------------------


TARGET = config.TargetSpec(
    name="laptop",
    kind=config.KIND_SSH,
    host="laptop.example",
    user="dev",
)


class FakeLink:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_a_handshake_that_fails_in_any_way_closes_the_link(monkeypatch):
    # Not only the failures this code expects: an `ssh` process left
    # behind stays until Hermes stops.
    link = FakeLink()

    def explode(client, password):
        raise ZeroDivisionError("something nobody planned for")

    monkeypatch.setattr(screenshare, "SshLink", lambda arguments: link)
    monkeypatch.setattr(rfb.Client, "handshake", explode)

    with pytest.raises(ZeroDivisionError):
        screenshare.connect(TARGET)

    assert link.closed is True


def test_the_password_is_handed_to_the_handshake(monkeypatch):
    seen = []
    hello = rfb.Hello((3, 8), rfb.SECURITY_PASSWORD, 4, 2, "desk")

    def remember(client, password):
        seen.append(password)

        return hello

    monkeypatch.setattr(screenshare, "SshLink", lambda arguments: FakeLink())
    monkeypatch.setattr(rfb.Client, "handshake", remember)

    screenshare.connect(TARGET, "secret")

    assert seen == ["secret"]


def test_asking_reports_a_refusal_as_a_problem_not_an_error(monkeypatch):
    def refuse(spec, password):
        raise screenshare.LinkError("Connection refused")

    monkeypatch.setattr(screenshare, "connect", refuse)

    answer = screenshare.ask(TARGET)

    assert answer.ok is False
    assert answer.problem == "Connection refused"


def test_asking_leaves_as_soon_as_it_has_its_answer(monkeypatch):
    client = FakeClient()
    hello = rfb.Hello((3, 8), rfb.SECURITY_NONE, 1280, 800, "desk")

    monkeypatch.setattr(
        screenshare,
        "connect",
        lambda spec, password: (client, hello),
    )

    answer = screenshare.ask(TARGET)

    assert answer.ok is True
    assert answer.hello.width == 1280
    assert client.events == [("close",)]

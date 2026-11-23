"""Hermes' screen tool, answered through screen sharing.

The backend builds on three names from Hermes. Outside Hermes they do
not exist, so small stand-ins with the same fields are put in their
place. The rig runs the same backend inside a real Hermes.
"""

from __future__ import annotations

import base64
import sys
import types

from abc import ABC
from dataclasses import dataclass
from dataclasses import field

import pytest

from onsite import config
from onsite import rfb
from onsite import screenshare


HERMES_MODULE = "tools.computer_use.backend"


@dataclass
class CaptureResult:
    mode: str
    width: int
    height: int
    png_b64: str | None = None
    elements: list = field(default_factory=list)
    png_bytes_len: int = 0
    image_mime_type: str | None = None
    note: str = ""


@dataclass
class ActionResult:
    ok: bool
    action: str
    message: str = ""
    effect: str | None = None
    path: str | None = None
    code: str | None = None


class ComputerUseBackend(ABC):
    pass


def install_stand_ins() -> None:
    if HERMES_MODULE in sys.modules:
        return

    module = types.ModuleType(HERMES_MODULE)
    module.ActionResult = ActionResult
    module.CaptureResult = CaptureResult
    module.ComputerUseBackend = ComputerUseBackend

    for name in ("tools", "tools.computer_use"):
        sys.modules.setdefault(name, types.ModuleType(name))

    sys.modules[HERMES_MODULE] = module


install_stand_ins()

from onsite import sharebackend  # noqa: E402


SPEC = config.TargetSpec(
    name="laptop",
    kind=config.KIND_SSH,
    host="laptop.example",
    user="dev",
)


class FakeSharer:
    """Stands in for the link. `fail` makes the next call lose it."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.fail = False
        self.closed = False

    def _note(self, *call) -> None:
        if self.fail:
            raise screenshare.LinkError("the link closed")

        self.calls.append(call)

    def capture(self):
        self._note("capture")

        return screenshare.Picture(b"png-bytes", 640, 400)

    def click(self, x, y, button, count, modifiers):
        self._note("click", x, y, button, count, modifiers)

    def drag(self, start, end, button, modifiers):
        self._note("drag", start, end, button, modifiers)

    def scroll(self, direction, amount, x, y, modifiers):
        if direction == "sideways":
            raise ValueError("`sideways` is not a way to scroll")

        self._note("scroll", direction, amount, x, y, modifiers)

    def type_text(self, text):
        self._note("type", text)

    def press(self, keys):
        self._note("press", keys)

    def close(self):
        self.closed = True


class Harness:
    def __init__(self, monkeypatch, driver_missing: bool = False):
        self.sharers: list[FakeSharer] = []
        self.refuse = ""

        monkeypatch.setattr(
            sharebackend.SharedScreenBackend,
            "_open",
            lambda backend: self.open(),
        )

        self.backend = sharebackend.SharedScreenBackend(SPEC, driver_missing)

    def open(self) -> FakeSharer:
        if self.refuse:
            raise rfb.RfbError(self.refuse)

        self.sharers.append(FakeSharer())

        return self.sharers[-1]


# ---- starting ---------------------------------------------------------


def test_a_start_opens_the_link_once(monkeypatch):
    harness = Harness(monkeypatch)

    harness.backend.start()
    harness.backend.capture()

    assert len(harness.sharers) == 1


def test_a_failed_start_says_which_screen_and_how_to_check(monkeypatch):
    harness = Harness(monkeypatch)
    harness.refuse = "Connection refused"

    with pytest.raises(RuntimeError) as caught:
        harness.backend.start()

    assert "laptop (dev@laptop.example)" in str(caught.value)
    assert "Connection refused" in str(caught.value)
    assert "hermes onsite screen laptop" in str(caught.value)


def test_a_failed_start_with_no_driver_names_both_ways(monkeypatch):
    harness = Harness(monkeypatch, driver_missing=True)
    harness.refuse = "Connection refused"

    with pytest.raises(RuntimeError) as caught:
        harness.backend.start()

    assert "driver is not installed there either" in str(caught.value)


def test_a_stop_closes_the_link(monkeypatch):
    harness = Harness(monkeypatch)

    harness.backend.start()
    harness.backend.stop()

    assert harness.sharers[0].closed is True


# ---- seeing -----------------------------------------------------------


def test_a_capture_is_a_picture_with_no_elements_and_says_so(monkeypatch):
    harness = Harness(monkeypatch)

    result = harness.backend.capture(mode="som")

    assert result.mode == "vision"
    assert (result.width, result.height) == (640, 400)
    assert base64.b64decode(result.png_b64) == b"png-bytes"
    assert result.image_mime_type == "image/png"
    assert result.elements == []
    assert "pixels only" in result.note


def test_a_capture_on_a_lost_link_says_so_and_the_next_one_reconnects(
    monkeypatch,
):
    harness = Harness(monkeypatch)
    harness.backend.start()
    harness.sharers[0].fail = True

    with pytest.raises(RuntimeError) as caught:
        harness.backend.capture()

    assert "stopped answering" in str(caught.value)
    assert harness.sharers[0].closed is True

    harness.backend.capture()

    assert len(harness.sharers) == 2


# ---- acting -----------------------------------------------------------


def test_a_click_by_position_goes_through(monkeypatch):
    harness = Harness(monkeypatch)

    result = harness.backend.click(x=300, y=200)

    assert result.ok is True
    assert result.path == "screen_sharing"
    assert harness.sharers[0].calls == [
        ("click", 300, 200, "left", 1, None),
    ]


def test_a_click_cannot_be_checked_and_hermes_is_told(monkeypatch):
    harness = Harness(monkeypatch)

    assert harness.backend.click(x=1, y=1).effect == "unverifiable"


def test_a_click_by_element_is_turned_down_with_what_to_do(monkeypatch):
    harness = Harness(monkeypatch)

    result = harness.backend.click(element=4)

    assert result.ok is False
    assert result.code == "unsupported_action"
    assert "coordinate=[x, y]" in result.message
    assert harness.sharers == []


def test_a_drag_needs_two_positions(monkeypatch):
    harness = Harness(monkeypatch)

    refused = harness.backend.drag(from_element=1, to_element=2)
    done = harness.backend.drag(from_xy=(1, 2), to_xy=(30, 40))

    assert refused.code == "unsupported_action"
    assert done.ok is True
    assert harness.sharers[0].calls == [
        ("drag", (1, 2), (30, 40), "left", None),
    ]


def test_a_scroll_by_element_is_turned_down(monkeypatch):
    harness = Harness(monkeypatch)

    assert harness.backend.scroll(
        direction="down",
        element=3,
    ).code == "unsupported_action"


def test_a_bad_argument_is_reported_and_keeps_the_link(monkeypatch):
    harness = Harness(monkeypatch)

    result = harness.backend.scroll(direction="sideways")

    assert result.ok is False
    assert "`sideways`" in result.message
    assert harness.sharers[0].closed is False


def test_typing_and_keys_go_through(monkeypatch):
    harness = Harness(monkeypatch)

    harness.backend.type_text("ls")
    harness.backend.key("return")

    assert harness.sharers[0].calls == [("type", "ls"), ("press", "return")]


def test_an_action_on_a_lost_link_fails_plainly_then_reconnects(
    monkeypatch,
):
    harness = Harness(monkeypatch)
    harness.backend.start()
    harness.sharers[0].fail = True

    lost = harness.backend.type_text("ls")
    again = harness.backend.type_text("ls")

    assert lost.ok is False
    assert "stopped answering" in lost.message
    assert again.ok is True
    assert len(harness.sharers) == 2


def test_apps_and_values_are_things_pixels_cannot_answer(monkeypatch):
    harness = Harness(monkeypatch)

    assert harness.backend.list_apps() == []
    assert harness.backend.focus_app("Files").code == "unsupported_action"
    assert harness.backend.set_value("x", 1).code == "unsupported_action"

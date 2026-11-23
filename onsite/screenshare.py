"""Driving a screen through screen sharing: the parts above the wire.

`rfb` speaks the protocol. This module does the rest that is not
Hermes: it owns the `ssh` process the conversation travels over,
turns "click here" and "type this" into pointer and key events, and
turns the frame into a picture.

A picture may be made smaller than the screen, because a model reads
a picture of limited size. Positions the model gives are positions in
that picture, and are scaled back to the screen here.

Standard library only, but for the picture, which is encoded with the
Pillow package Hermes ships. This module imports nothing from Hermes.
"""

from __future__ import annotations

import io
import os
import select
import subprocess
import threading
import time

from dataclasses import dataclass

from . import rfb
from .config import TargetSpec
from .remote import build_forward_arguments


SSH_BINARY = "ssh"

# How long the server gets to answer one read. A whole screen of raw
# pixels over a slow link is the slow case.
READ_SECONDS = 30
READ_CHUNK = 1 << 16
STOP_SECONDS = 2
ERROR_WAIT_SECONDS = 0.5
ERROR_CHARS = 400

# Long enough for a desktop to see a press as a press, and for it to
# draw the result before the next picture is taken.
PRESS_SECONDS = 0.03
SETTLE_SECONDS = 0.15
DRAG_STEPS = 12

# x11vnc queues a viewer's pointer events and plays them when more
# arrive, while keys go straight through. A click followed by typing
# could then land out of order, with the start of the text going to
# whatever had focus before, and one press of a double click could
# be dropped. A person's hand never stops dead, so neither does this
# pointer: every press, release and resting place is reported, then
# reported twice more. Found by test against x11vnc: 80 of 80 clicks
# landed on time this way, about 6 in 10 without.
POINTER_NUDGES = 2
NUDGE_SECONDS = 0.02

BUTTONS = {
    "left": rfb.BUTTON_LEFT,
    "middle": rfb.BUTTON_MIDDLE,
    "right": rfb.BUTTON_RIGHT,
}
WHEELS = {
    "up": rfb.WHEEL_UP,
    "down": rfb.WHEEL_DOWN,
    "left": rfb.WHEEL_LEFT,
    "right": rfb.WHEEL_RIGHT,
}

KEY_RETURN = 0xFF0D
KEY_TAB = 0xFF09
KEY_F1 = 0xFFBE
LAST_F_KEY = 12

# X11 key numbers, which is what RFB sends. A Mac's own server reads
# the "super" key as Command and "alt" as Option.
KEYS = {
    "return": KEY_RETURN,
    "enter": KEY_RETURN,
    "tab": KEY_TAB,
    "escape": 0xFF1B,
    "esc": 0xFF1B,
    "backspace": 0xFF08,
    "delete": 0xFFFF,
    "del": 0xFFFF,
    "insert": 0xFF63,
    "space": 0x20,
    "up": 0xFF52,
    "down": 0xFF54,
    "left": 0xFF51,
    "right": 0xFF53,
    "home": 0xFF50,
    "end": 0xFF57,
    "pageup": 0xFF55,
    "page_up": 0xFF55,
    "pagedown": 0xFF56,
    "page_down": 0xFF56,
    "capslock": 0xFFE5,
    "shift": 0xFFE1,
    "ctrl": 0xFFE3,
    "control": 0xFFE3,
    "alt": 0xFFE9,
    "option": 0xFFE9,
    "opt": 0xFFE9,
    "cmd": 0xFFEB,
    "command": 0xFFEB,
    "super": 0xFFEB,
    "win": 0xFFEB,
    "meta": 0xFFEB,
}
TEXT_KEYS = {
    "\n": KEY_RETURN,
    "\r": KEY_RETURN,
    "\t": KEY_TAB,
}

# Latin-1 characters are their own key numbers. Anything past them is
# sent as its Unicode number with this bit set.
UNICODE_KEY = 0x01000000
FIRST_PRINTABLE = 0x20
LAST_ASCII = 0x7E
FIRST_LATIN = 0xA0
LAST_LATIN = 0xFF

COMBO_SEPARATOR = "+"


class SharingError(Exception):
    """Screen sharing on a target cannot be used. The message says why."""


class LinkError(SharingError):
    """The `ssh` link to the target's screen sharing failed."""


# Everything that can go wrong between here and a target's screen.
FAILURES = (rfb.RfbError, SharingError)


class SshLink:
    """An `ssh` process whose input and output are a port on a target."""

    def __init__(self, arguments: list[str], timeout: float = READ_SECONDS):
        self._timeout = timeout
        self._process = subprocess.Popen(
            [SSH_BINARY, *arguments],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def _last_words(self) -> str:
        """What `ssh` itself said, when the link has closed."""

        stream = self._process.stderr

        if stream is None:
            return ""

        ready, _, _ = select.select([stream], [], [], ERROR_WAIT_SECONDS)

        if not ready:
            return ""

        said = os.read(stream.fileno(), READ_CHUNK)

        return said.decode("utf-8", "replace").strip()[-ERROR_CHARS:]

    def _closed(self) -> LinkError:
        return LinkError(self._last_words() or "the link closed")

    def read_exact(self, count: int) -> bytes:
        descriptor = self._process.stdout.fileno()
        deadline = time.monotonic() + self._timeout
        parts = []
        missing = count

        while missing:
            wait = deadline - time.monotonic()

            if wait <= 0:
                raise LinkError(
                    f"no answer in {self._timeout:.0f} seconds"
                )

            ready, _, _ = select.select([descriptor], [], [], wait)

            if not ready:
                continue

            chunk = os.read(descriptor, min(missing, READ_CHUNK))

            if not chunk:
                raise self._closed()

            parts.append(chunk)
            missing -= len(chunk)

        return b"".join(parts)

    def write(self, data: bytes) -> None:
        descriptor = self._process.stdin.fileno()
        view = memoryview(data)

        try:
            while view:
                sent = os.write(descriptor, view)
                view = view[sent:]
        except OSError:
            raise self._closed() from None

    def close(self) -> None:
        process = self._process

        if process.poll() is None:
            process.terminate()

            try:
                process.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def key_for_character(character: str) -> int:
    """The key number that types one character."""

    if character in TEXT_KEYS:
        return TEXT_KEYS[character]

    number = ord(character)

    if FIRST_PRINTABLE <= number <= LAST_ASCII:
        return number

    if FIRST_LATIN <= number <= LAST_LATIN:
        return number

    if number < FIRST_LATIN:
        raise ValueError(
            f"character {number} is a control code, not something "
            "a keyboard types"
        )

    return UNICODE_KEY | number


def key_for_name(name: str) -> int:
    """The key number for one part of a combination such as `ctrl+s`."""

    known = KEYS.get(name.lower())

    if known is not None:
        return known

    lowered = name.lower()

    if lowered.startswith("f") and lowered[1:].isdigit():
        number = int(lowered[1:])

        if 1 <= number <= LAST_F_KEY:
            return KEY_F1 + number - 1

    if len(name) == 1:
        return key_for_character(name)

    raise ValueError(f"`{name}` is not a key screen sharing knows")


def split_combination(keys: str) -> list[str]:
    """`ctrl+alt+t` gives three names. A plus sign can be a key too:
    `ctrl++` gives `ctrl` and `+`."""

    names = []
    plus_pending = False

    for part in keys.strip().split(COMBO_SEPARATOR):
        if part.strip():
            names.append(part.strip())
            plus_pending = False
            continue

        # An empty part sits beside a separator that was itself a key.
        # Two empty parts in a row are the two sides of one plus sign.
        if plus_pending:
            names.append(COMBO_SEPARATOR)
            plus_pending = False
            continue

        plus_pending = True

    return names


def parse_keys(keys: str) -> list[int]:
    names = split_combination(keys)

    if not names:
        raise ValueError("no key was named")

    return [key_for_name(name) for name in names]


def button_mask(button: str) -> int:
    mask = BUTTONS.get(button)

    if mask is None:
        raise ValueError(f"`{button}` is not a mouse button")

    return mask


def fit(width: int, height: int, limit: int) -> tuple[int, int]:
    """The picture's size: the screen's, or smaller to fit `limit`."""

    longest = max(width, height)

    if limit <= 0:
        return width, height

    if longest <= limit:
        return width, height

    return (
        max(1, round(width * limit / longest)),
        max(1, round(height * limit / longest)),
    )


@dataclass(frozen=True)
class Picture:
    png: bytes
    width: int
    height: int


def encode_picture(
    frame: bytes,
    width: int,
    height: int,
    limit: int,
) -> Picture:
    from PIL import Image

    image = Image.frombuffer(
        "RGB",
        (width, height),
        bytes(frame),
        "raw",
        "BGRX",
        0,
        1,
    )

    size = fit(width, height, limit)

    if size != (width, height):
        image = image.resize(size, Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    return Picture(buffer.getvalue(), size[0], size[1])


class Sharer:
    """One screen, seen and driven through an RFB client.

    Every method takes positions in the picture, not on the screen.
    """

    def __init__(self, client: rfb.Client, limit: int, pause=time.sleep):
        self._client = client
        self._limit = limit
        self._pause = pause
        self._lock = threading.Lock()
        self._at = (0, 0)

    @property
    def screen_size(self) -> tuple[int, int]:
        return self._client.width, self._client.height

    def _on_screen(self, x: float, y: float) -> tuple[int, int]:
        width, height = self.screen_size
        shown = fit(width, height, self._limit)

        return (
            round(x * width / shown[0]),
            round(y * height / shown[1]),
        )

    def _move(self, x: int, y: int, buttons: int = 0) -> None:
        self._client.pointer(x, y, buttons)
        self._at = (x, y)

    def _report(self, x: int, y: int, buttons: int = 0) -> None:
        """Move or change a button, and say so again.

        See `POINTER_NUDGES`.
        """

        self._move(x, y, buttons)

        for _ in range(POINTER_NUDGES):
            self._pause(NUDGE_SECONDS)
            self._client.pointer(x, y, buttons)

    def _press_keys(self, numbers: list[int]) -> None:
        for number in numbers:
            self._client.key(number, True)

    def _release_keys(self, numbers: list[int]) -> None:
        for number in reversed(numbers):
            self._client.key(number, False)

    def capture(self) -> Picture:
        with self._lock:
            self._client.refresh()

            width, height = self.screen_size

            return encode_picture(
                self._client.frame,
                width,
                height,
                self._limit,
            )

    def click(
        self,
        x: float,
        y: float,
        button: str = "left",
        count: int = 1,
        modifiers: list[str] | None = None,
    ) -> None:
        mask = button_mask(button)
        held = [key_for_name(name) for name in modifiers or []]

        with self._lock:
            place = self._on_screen(x, y)

            self._press_keys(held)
            self._report(*place)

            for _ in range(max(1, count)):
                self._report(*place, mask)
                self._report(*place)

            self._pause(SETTLE_SECONDS)
            self._release_keys(held)

    def drag(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        button: str = "left",
        modifiers: list[str] | None = None,
    ) -> None:
        mask = button_mask(button)
        held = [key_for_name(name) for name in modifiers or []]

        with self._lock:
            first = self._on_screen(*start)
            last = self._on_screen(*end)

            self._press_keys(held)
            self._report(*first)
            self._report(*first, mask)

            # A desktop only starts a drag once the pointer has been
            # seen to travel with the button down.
            for step in range(1, DRAG_STEPS + 1):
                part = step / DRAG_STEPS

                self._move(
                    round(first[0] + (last[0] - first[0]) * part),
                    round(first[1] + (last[1] - first[1]) * part),
                    mask,
                )
                self._pause(PRESS_SECONDS)

            self._report(*last)
            self._pause(SETTLE_SECONDS)
            self._release_keys(held)

    def scroll(
        self,
        direction: str,
        amount: int = 3,
        x: float | None = None,
        y: float | None = None,
        modifiers: list[str] | None = None,
    ) -> None:
        wheel = WHEELS.get(direction)

        if wheel is None:
            raise ValueError(f"`{direction}` is not a way to scroll")

        held = [key_for_name(name) for name in modifiers or []]

        with self._lock:
            place = self._at

            if x is not None and y is not None:
                place = self._on_screen(x, y)

            self._press_keys(held)
            self._report(*place)

            for _ in range(max(1, amount)):
                self._report(*place, wheel)
                self._report(*place)

            self._pause(SETTLE_SECONDS)
            self._release_keys(held)

    def type_text(self, text: str) -> None:
        numbers = [key_for_character(character) for character in text]

        with self._lock:
            for number in numbers:
                self._client.key(number, True)
                self._client.key(number, False)

            self._pause(SETTLE_SECONDS)

    def press(self, keys: str) -> None:
        numbers = parse_keys(keys)

        with self._lock:
            self._press_keys(numbers)
            self._pause(PRESS_SECONDS)
            self._release_keys(numbers)
            self._pause(SETTLE_SECONDS)

    def close(self) -> None:
        self._client.close()


def read_password(spec: TargetSpec, environment=None) -> str:
    """The VNC password for a target, from the variable it names.

    The settings hold the variable's name and never the password.
    """

    name = spec.screen_password_env

    if not name:
        return ""

    if environment is None:
        environment = os.environ

    password = environment.get(name, "")

    if not password:
        raise SharingError(
            f"`{name}` is named as the screen password of `{spec.name}` "
            "but is not set where Hermes runs. Put it in the profile's "
            "`.env` file"
        )

    return password


def connect(spec: TargetSpec) -> tuple[rfb.Client, rfb.Hello]:
    """Open a link to a target's screen sharing and sign in."""

    password = read_password(spec)
    link = SshLink(build_forward_arguments(spec, spec.screen_port))
    client = rfb.Client(link)

    try:
        hello = client.handshake(password)
    except FAILURES:
        link.close()
        raise

    return client, hello


@dataclass(frozen=True)
class Answer:
    """Whether a target's screen sharing answered, for a report."""

    hello: rfb.Hello | None = None
    problem: str = ""

    @property
    def ok(self) -> bool:
        return self.hello is not None


def ask(spec: TargetSpec) -> Answer:
    """Sign in to a target's screen sharing, then leave at once."""

    try:
        client, hello = connect(spec)
    except FAILURES as error:
        return Answer(problem=str(error))

    client.close()

    return Answer(hello=hello)

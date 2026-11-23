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
KEY_BACKSPACE = 0xFF08
KEY_DELETE = 0xFFFF
KEY_SHIFT = 0xFFE1
KEY_CTRL = 0xFFE3
KEY_ALT = 0xFFE9
KEY_SUPER = 0xFFEB
KEY_F1 = 0xFFBE
KEY_F4 = KEY_F1 + 3
LAST_F_KEY = 12

# X11 key numbers, which is what RFB sends. A Mac's own server reads
# the "super" key as Command and "alt" as Option.
KEYS = {
    "return": KEY_RETURN,
    "enter": KEY_RETURN,
    "tab": KEY_TAB,
    "escape": 0xFF1B,
    "esc": 0xFF1B,
    "backspace": KEY_BACKSPACE,
    "delete": KEY_DELETE,
    "del": KEY_DELETE,
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
    "shift": KEY_SHIFT,
    "ctrl": KEY_CTRL,
    "control": KEY_CTRL,
    "alt": KEY_ALT,
    "option": KEY_ALT,
    "opt": KEY_ALT,
    "cmd": KEY_SUPER,
    "command": KEY_SUPER,
    "super": KEY_SUPER,
    "win": KEY_SUPER,
    "meta": KEY_SUPER,
}

# Hermes hard-blocks a few combinations by name before any backend
# sees them: the ones that log out, lock the screen or delete with no
# question. Here several names are one key: `cmd` and `win` both
# press the "super" key, which is Command on a Mac. So a combination
# Hermes lets through under one name can be a blocked one under
# another. The same list is checked again here, after names have
# become keys. It follows Hermes' list as of v0.23.2.
BLOCKED_COMBINATIONS = (
    frozenset({KEY_SUPER, KEY_SHIFT, KEY_BACKSPACE}),
    frozenset({KEY_SUPER, KEY_ALT, KEY_BACKSPACE}),
    frozenset({KEY_SUPER, KEY_CTRL, ord("q")}),
    frozenset({KEY_SUPER, KEY_SHIFT, ord("q")}),
    frozenset({KEY_SUPER, ord("l")}),
    frozenset({KEY_CTRL, KEY_ALT, KEY_DELETE}),
    frozenset({KEY_ALT, KEY_F4}),
)
BLOCKED_MESSAGE = (
    "that key combination logs out, locks the screen or deletes "
    "without asking. It is never sent."
)
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

        try:
            self._process = subprocess.Popen(
                [SSH_BINARY, *arguments],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as error:
            raise LinkError(
                f"the `{SSH_BINARY}` program could not be started: {error}"
            ) from None

    def _last_words(self) -> str:
        """What `ssh` itself said, when the link has closed."""

        stream = self._process.stderr

        if stream is None:
            return ""

        try:
            ready, _, _ = select.select(
                [stream],
                [],
                [],
                ERROR_WAIT_SECONDS,
            )
        except (OSError, ValueError):
            return ""

        if not ready:
            return ""

        said = os.read(stream.fileno(), READ_CHUNK)

        return said.decode("utf-8", "replace").strip()[-ERROR_CHARS:]

    def _closed(self) -> LinkError:
        return LinkError(self._last_words() or "the link closed")

    def _wait_for_bytes(self, descriptor: int, wait: float) -> bool:
        # `select` refuses a descriptor past its own limit, which a
        # busy Hermes can reach. That is a failed link, not a crash.
        try:
            ready, _, _ = select.select([descriptor], [], [], wait)
        except (OSError, ValueError) as error:
            raise LinkError(f"the link could not be read: {error}") from None

        return bool(ready)

    def read_exact(self, count: int, until: float | None = None) -> bytes:
        """Read `count` bytes.

        Two limits apply. The link may not stay silent for longer than
        its timeout, however large the read. And when `until` is
        given, the whole read must be done by then.
        """

        descriptor = self._process.stdout.fileno()
        quiet_until = time.monotonic() + self._timeout
        parts = []
        missing = count

        while missing:
            now = time.monotonic()

            if now >= quiet_until:
                raise LinkError(
                    f"no answer in {self._timeout:.0f} seconds"
                )

            if until is not None and now >= until:
                raise LinkError("the answer did not finish in time")

            limit = quiet_until if until is None else min(quiet_until, until)

            if not self._wait_for_bytes(descriptor, limit - now):
                continue

            chunk = os.read(descriptor, min(missing, READ_CHUNK))

            if not chunk:
                raise self._closed()

            parts.append(chunk)
            missing -= len(chunk)
            quiet_until = time.monotonic() + self._timeout

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


def without_case(number: int) -> int:
    """A letter key as its small letter, so `Q` and `q` are one key."""

    if ord("A") <= number <= ord("Z"):
        return number + ord("a") - ord("A")

    return number


def is_blocked(numbers: list[int]) -> bool:
    pressed = {without_case(number) for number in numbers}

    return any(blocked <= pressed for blocked in BLOCKED_COMBINATIONS)


def parse_keys(keys: str) -> list[int]:
    names = split_combination(keys)

    if not names:
        raise ValueError("no key was named")

    numbers = [key_for_name(name) for name in names]

    if is_blocked(numbers):
        raise ValueError(BLOCKED_MESSAGE)

    return numbers


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
    try:
        from PIL import Image
    except ImportError:
        raise SharingError(
            "the Pillow package that makes the picture is missing from "
            "this Hermes"
        ) from None

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
        self._at: tuple[int, int] | None = None

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

    def _resting_place(self) -> tuple[int, int]:
        """Where the pointer was left, or the middle of the screen.

        Never a corner: on many desktops a pointer in the corner
        opens something.
        """

        if self._at is not None:
            return self._at

        width, height = self.screen_size

        return width // 2, height // 2

    def _place(self, x: float | None, y: float | None) -> tuple[int, int]:
        """A place on the screen, with a missing side left where it is."""

        rest_x, rest_y = self._resting_place()

        if x is not None:
            rest_x = self._on_screen(x, 0)[0]

        if y is not None:
            rest_y = self._on_screen(0, y)[1]

        return rest_x, rest_y

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
            place = self._place(x, y)

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


def connect(
    spec: TargetSpec,
    password: str = "",
) -> tuple[rfb.Client, rfb.Hello]:
    """Open a link to a target's screen sharing and sign in.

    The password is only used if the server asks for one. The caller
    gets it from Hermes. See `compat.screen_password`.
    """

    link = SshLink(build_forward_arguments(spec, spec.screen_port))
    client = rfb.Client(link)

    # Whatever goes wrong, the `ssh` process must not be left behind.
    try:
        hello = client.handshake(password)
    except BaseException:
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


def ask(spec: TargetSpec, password: str = "") -> Answer:
    """Sign in to a target's screen sharing, then leave at once."""

    try:
        client, hello = connect(spec, password)
    except FAILURES as error:
        return Answer(problem=str(error))

    client.close()

    return Answer(hello=hello)

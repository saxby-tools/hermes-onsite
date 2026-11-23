"""A small client for screen sharing: the RFB protocol, as VNC speaks.

This is the second way to a target's screen. The first runs Hermes'
screen driver on the target. This one needs nothing installed there
beyond the screen sharing the machine already has. It sees pixels
only: there are no buttons to name, so the agent clicks by position.

Only what a screenshot, a click and a keystroke need is here:

- the handshake, with no password or with a VNC password,
- one pixel format, asked for by this client, and raw pixels,
- pointer and key events.

A link is anything with `read_exact`, `write` and `close`. The real
one is an `ssh` process, in `screenshare`. Tests use a socket.

This module imports nothing from Hermes.
"""

from __future__ import annotations

import struct

from dataclasses import dataclass


BANNER_LENGTH = 12
BANNER_START = b"RFB "

VERSION_3_3 = (3, 3)
VERSION_3_7 = (3, 7)
VERSION_3_8 = (3, 8)

SECURITY_INVALID = 0
SECURITY_NONE = 1
SECURITY_PASSWORD = 2

# What Apple's screen sharing offers when it wants a macOS account
# instead of a VNC password.
SECURITY_APPLE = frozenset({30, 33, 35, 36})

RESULT_OK = 0

CHALLENGE_LENGTH = 16
PASSWORD_LENGTH = 8

# Other viewers of the same screen stay connected.
SHARED = 1

MESSAGE_SET_PIXEL_FORMAT = 0
MESSAGE_SET_ENCODINGS = 2
MESSAGE_UPDATE_REQUEST = 3
MESSAGE_KEY = 4
MESSAGE_POINTER = 5

SERVER_UPDATE = 0
SERVER_COLOUR_MAP = 1
SERVER_BELL = 2
SERVER_CUT_TEXT = 3

ENCODING_RAW = 0
# Not pixels: the server's way to say the screen changed size.
ENCODING_DESKTOP_SIZE = -223

BYTES_PER_PIXEL = 4

# 32 bits a pixel, 24 of them colour, least significant byte first:
# blue, green, red, then one unused byte.
PIXEL_FORMAT = struct.pack(
    ">BBBBHHHBBB3x",
    32,
    24,
    0,
    1,
    255,
    255,
    255,
    16,
    8,
    0,
)

BUTTON_LEFT = 1
BUTTON_MIDDLE = 2
BUTTON_RIGHT = 4
WHEEL_UP = 8
WHEEL_DOWN = 16
WHEEL_LEFT = 32
WHEEL_RIGHT = 64

MAX_REASON_LENGTH = 4096
MAX_NAME_LENGTH = 4096
MAX_CUT_TEXT_LENGTH = 16 * 1024 * 1024

# A screen larger than this is a broken or hostile server, not a
# desktop. It bounds the memory one update can ask for.
MAX_SIDE = 16384


class RfbError(Exception):
    """The screen-sharing server said no, or said something else."""


@dataclass(frozen=True)
class Hello:
    """What the handshake learned about the server."""

    version: tuple[int, int]
    security: int
    width: int
    height: int
    name: str


def parse_banner(banner: bytes) -> tuple[int, int]:
    """`RFB 003.008\\n` gives (3, 8)."""

    if len(banner) != BANNER_LENGTH:
        raise RfbError("the server closed the link before saying hello")

    if not banner.startswith(BANNER_START):
        raise RfbError(
            "the port did not answer like a screen-sharing server"
        )

    try:
        major = int(banner[4:7])
        minor = int(banner[8:11])
    except ValueError:
        raise RfbError("the server's version could not be read") from None

    return major, minor


def pick_version(server: tuple[int, int]) -> tuple[int, int]:
    """The newest of the three public versions the server can speak.

    Apple's server calls itself 3.889. It speaks 3.8.
    """

    if server >= VERSION_3_8:
        return VERSION_3_8

    if server >= VERSION_3_7:
        return VERSION_3_7

    if server >= VERSION_3_3:
        return VERSION_3_3

    raise RfbError(f"the server speaks RFB {server[0]}.{server[1]}, too old")


def format_banner(version: tuple[int, int]) -> bytes:
    return b"RFB %03d.%03d\n" % version


def describe_offers(offers: list[int]) -> str:
    return ", ".join(str(one) for one in offers) or "none"


def pick_security(offers: list[int], has_password: bool) -> int:
    """No password when the server allows it, else a VNC password."""

    if SECURITY_NONE in offers:
        return SECURITY_NONE

    if SECURITY_PASSWORD in offers and has_password:
        return SECURITY_PASSWORD

    if SECURITY_PASSWORD in offers:
        raise RfbError(
            "the server asks for a VNC password and none is set. Name "
            "an environment variable that holds it in the target's "
            "`screen_password_env` setting"
        )

    if SECURITY_APPLE.intersection(offers):
        raise RfbError(
            "the server only takes a macOS account sign-in. In System "
            "Settings, Sharing, Screen Sharing, turn on \"VNC viewers "
            "may control screen with password\""
        )

    raise RfbError(
        "the server offers no sign-in this client knows "
        f"(types {describe_offers(offers)})"
    )


def reverse_bits(value: int) -> int:
    return int(f"{value:08b}"[::-1], 2)


def password_key(password: str) -> bytes:
    """The DES key VNC makes from a password.

    Eight bytes, cut or padded with zeros, and each byte's bits in
    reverse order. That last part is a quirk of the first VNC and
    every server since has kept it.
    """

    raw = password.encode("latin-1", errors="replace")[:PASSWORD_LENGTH]
    raw = raw.ljust(PASSWORD_LENGTH, b"\x00")

    return bytes(reverse_bits(byte) for byte in raw)


def answer_challenge(challenge: bytes, password: str) -> bytes:
    """Encrypt the server's challenge with the password.

    DES is long broken, and VNC passwords were never strong. This
    client only meets one through SSH, which is the real lock. The
    cipher comes from the `cryptography` package Hermes ships, as
    triple DES with one key three times, which is plain DES.
    """

    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    from cryptography.hazmat.primitives.ciphers import Cipher
    from cryptography.hazmat.primitives.ciphers import modes

    key = password_key(password)
    cipher = Cipher(TripleDES(key * 3), modes.ECB())
    encryptor = cipher.encryptor()

    return encryptor.update(challenge) + encryptor.finalize()


class Client:
    """One conversation with a screen-sharing server."""

    def __init__(self, link):
        self._link = link
        self.width = 0
        self.height = 0
        self.frame = bytearray()

    # ---- reading ------------------------------------------------------

    def _read(self, count: int) -> bytes:
        return self._link.read_exact(count)

    def _read_u8(self) -> int:
        return self._read(1)[0]

    def _read_u16(self) -> int:
        return struct.unpack(">H", self._read(2))[0]

    def _read_u32(self) -> int:
        return struct.unpack(">I", self._read(4))[0]

    def _read_text(self, limit: int) -> str:
        length = self._read_u32()

        if length > limit:
            raise RfbError("the server sent a text too long to be real")

        return self._read(length).decode("utf-8", errors="replace")

    # ---- the handshake ------------------------------------------------

    def _read_offers(self, version: tuple[int, int]) -> list[int]:
        # The oldest version does not offer a choice. It names one.
        if version == VERSION_3_3:
            return [self._read_u32()]

        count = self._read_u8()

        if count == 0:
            return [SECURITY_INVALID]

        return list(self._read(count))

    def _refuse_invalid(self, offers: list[int]) -> None:
        if offers != [SECURITY_INVALID]:
            return

        reason = self._read_text(MAX_REASON_LENGTH)

        raise RfbError(f"the server refused the connection: {reason}")

    def _read_result(self, version: tuple[int, int], security: int) -> None:
        # Before 3.8 a server that asked for no password sends no
        # result either.
        if security == SECURITY_NONE and version != VERSION_3_8:
            return

        if self._read_u32() == RESULT_OK:
            return

        reason = "the password was not accepted"

        if version == VERSION_3_8:
            reason = self._read_text(MAX_REASON_LENGTH) or reason

        raise RfbError(f"the server refused the sign-in: {reason}")

    def _set_size(self, width: int, height: int) -> None:
        if not 0 < width <= MAX_SIDE:
            raise RfbError(f"the server reports a screen {width} wide")

        if not 0 < height <= MAX_SIDE:
            raise RfbError(f"the server reports a screen {height} high")

        self.width = width
        self.height = height
        self.frame = bytearray(width * height * BYTES_PER_PIXEL)

    def handshake(self, password: str = "") -> Hello:
        server = parse_banner(self._read(BANNER_LENGTH))
        version = pick_version(server)

        self._link.write(format_banner(version))

        offers = self._read_offers(version)
        self._refuse_invalid(offers)

        security = pick_security(offers, bool(password))

        if version != VERSION_3_3:
            self._link.write(bytes([security]))

        if security == SECURITY_PASSWORD:
            challenge = self._read(CHALLENGE_LENGTH)
            self._link.write(answer_challenge(challenge, password))

        self._read_result(version, security)

        self._link.write(bytes([SHARED]))

        width = self._read_u16()
        height = self._read_u16()

        # The server's own pixel format. This client sets its own.
        self._read(16)

        name = self._read_text(MAX_NAME_LENGTH)

        self._set_size(width, height)

        self._link.write(
            struct.pack(">B3x", MESSAGE_SET_PIXEL_FORMAT) + PIXEL_FORMAT
        )
        self._link.write(struct.pack(
            ">BxHii",
            MESSAGE_SET_ENCODINGS,
            2,
            ENCODING_RAW,
            ENCODING_DESKTOP_SIZE,
        ))

        return Hello(server, security, width, height, name)

    # ---- the screen ---------------------------------------------------

    def _request_update(self) -> None:
        self._link.write(struct.pack(
            ">BBHHHH",
            MESSAGE_UPDATE_REQUEST,
            0,
            0,
            0,
            self.width,
            self.height,
        ))

    def _read_raw(self, x: int, y: int, width: int, height: int) -> int:
        """Copy one rectangle into the frame. Returns its area."""

        if x + width > self.width or y + height > self.height:
            raise RfbError("the server sent pixels outside the screen")

        row_bytes = width * BYTES_PER_PIXEL
        stride = self.width * BYTES_PER_PIXEL
        data = self._read(row_bytes * height)

        # The usual case, a band as wide as the screen, is one copy.
        if width == self.width:
            start = y * stride

            self.frame[start:start + len(data)] = data

            return width * height

        for row in range(height):
            start = (y + row) * stride + x * BYTES_PER_PIXEL
            source = row * row_bytes

            self.frame[start:start + row_bytes] = (
                data[source:source + row_bytes]
            )

        return width * height

    def _read_update(self) -> tuple[int, bool]:
        """Read one screen update.

        Returns the area of the pixels it brought, and whether the
        screen changed size, which empties the frame.
        """

        self._read(1)

        area = 0
        resized = False

        for _ in range(self._read_u16()):
            x, y, width, height, encoding = struct.unpack(
                ">HHHHi",
                self._read(12),
            )

            if encoding == ENCODING_DESKTOP_SIZE:
                self._set_size(width, height)
                resized = True
                continue

            if encoding != ENCODING_RAW:
                raise RfbError(
                    f"the server sent pixels in a form ({encoding}) "
                    "this client did not ask for"
                )

            area += self._read_raw(x, y, width, height)

        return area, resized

    def _skip_other(self, kind: int) -> None:
        if kind == SERVER_BELL:
            return

        if kind == SERVER_COLOUR_MAP:
            self._read(3)
            self._read(self._read_u16() * 6)
            return

        if kind == SERVER_CUT_TEXT:
            self._read(3)
            length = self._read_u32()

            if length > MAX_CUT_TEXT_LENGTH:
                raise RfbError("the server sent a clipboard too large")

            # The target's clipboard is read and dropped. It is none
            # of this client's business.
            self._read(length)
            return

        raise RfbError(f"the server sent a message ({kind}) not in RFB")

    def refresh(self) -> None:
        """Bring the frame up to date with the whole screen.

        A server may answer one request with several updates. Reading
        goes on until they add up to the whole screen.
        """

        self._request_update()

        needed = self.width * self.height
        seen = 0

        while seen < needed:
            kind = self._read_u8()

            if kind != SERVER_UPDATE:
                self._skip_other(kind)
                continue

            area, resized = self._read_update()

            if resized:
                self._request_update()

                needed = self.width * self.height
                seen = 0
                continue

            seen += area

    # ---- input --------------------------------------------------------

    def pointer(self, x: int, y: int, buttons: int = 0) -> None:
        x = min(max(int(x), 0), max(self.width - 1, 0))
        y = min(max(int(y), 0), max(self.height - 1, 0))

        self._link.write(
            struct.pack(">BBHH", MESSAGE_POINTER, buttons, x, y)
        )

    def key(self, keysym: int, down: bool) -> None:
        self._link.write(
            struct.pack(">BBxxI", MESSAGE_KEY, 1 if down else 0, keysym)
        )

    def close(self) -> None:
        self._link.close()

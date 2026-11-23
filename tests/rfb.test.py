"""The screen-sharing client, against a small server made for the test.

The server here speaks just enough RFB to hold up its end. It runs on
a thread, at the other end of a socket pair, and records what the
client sent.
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from onsite import rfb


PIXEL = 4


class SocketLink:
    def __init__(self, sock: socket.socket):
        self._sock = sock

    def read_exact(self, count: int) -> bytes:
        parts = []
        missing = count

        while missing:
            chunk = self._sock.recv(missing)

            if not chunk:
                raise ConnectionError("closed")

            parts.append(chunk)
            missing -= len(chunk)

        return b"".join(parts)

    def write(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        self._sock.close()


class FakeServer:
    """One scripted server. `updates` is a list of answers to update
    requests, each a list of (x, y, width, height, encoding, data)."""

    def __init__(
        self,
        banner: bytes = b"RFB 003.008\n",
        offers: tuple = (rfb.SECURITY_NONE,),
        refuse_with: str = "",
        result: int = rfb.RESULT_OK,
        result_reason: str = "",
        size: tuple = (4, 2),
        name: str = "desk",
        updates: list | None = None,
        per_request: int = 1,
        chatter: bytes = b"",
    ):
        self.banner = banner
        self.offers = offers
        self.refuse_with = refuse_with
        self.result = result
        self.result_reason = result_reason
        self.size = size
        self.name = name
        self.updates = list(updates or [])
        self.per_request = per_request
        self.chatter = chatter

        self.client_banner = b""
        self.chosen = None
        self.response = b""
        self.pixel_format = b""
        self.encodings: list[int] = []
        self.requests = 0
        self.pointers: list[tuple] = []
        self.keys: list[tuple] = []

        ours, theirs = socket.socketpair()

        self._sock = ours
        self.link = SocketLink(theirs)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _read(self, count: int) -> bytes:
        return SocketLink(self._sock).read_exact(count)

    def _send_text(self, text: str) -> None:
        raw = text.encode()

        self._sock.sendall(struct.pack(">I", len(raw)) + raw)

    def _handshake(self) -> bool:
        self._sock.sendall(self.banner)
        self.client_banner = self._read(12)

        old = self.client_banner == b"RFB 003.003\n"

        if self.refuse_with:
            self._sock.sendall(b"\x00")
            self._send_text(self.refuse_with)
            return False

        if old:
            self._sock.sendall(struct.pack(">I", self.offers[0]))
            self.chosen = self.offers[0]

        if not old:
            self._sock.sendall(bytes([len(self.offers), *self.offers]))
            self.chosen = self._read(1)[0]

        if self.chosen == rfb.SECURITY_PASSWORD:
            self._sock.sendall(bytes(range(16)))
            self.response = self._read(16)

        sends_result = (
            self.chosen != rfb.SECURITY_NONE
            or self.client_banner == b"RFB 003.008\n"
        )

        if sends_result:
            self._sock.sendall(struct.pack(">I", self.result))

        if self.result != rfb.RESULT_OK:
            self._send_text(self.result_reason)
            return False

        self._read(1)

        raw_name = self.name.encode()

        self._sock.sendall(
            struct.pack(">HH", *self.size)
            + bytes(16)
            + struct.pack(">I", len(raw_name))
            + raw_name
        )

        return True

    def _send_update(self) -> None:
        if not self.updates:
            return

        rectangles = self.updates.pop(0)
        message = struct.pack(">BxH", 0, len(rectangles))

        for x, y, width, height, encoding, data in rectangles:
            message += struct.pack(">HHHHi", x, y, width, height, encoding)
            message += data

        self._sock.sendall(message)

    def _answer_request(self) -> None:
        self.requests += 1

        if self.chatter:
            self._sock.sendall(self.chatter)

        for _ in range(self.per_request):
            self._send_update()

    def _serve(self) -> None:
        while True:
            kind = self._read(1)[0]

            if kind == rfb.MESSAGE_SET_PIXEL_FORMAT:
                self.pixel_format = self._read(19)[3:]
                continue

            if kind == rfb.MESSAGE_SET_ENCODINGS:
                count = struct.unpack(">xH", self._read(3))[0]
                raw = self._read(4 * count)
                self.encodings = list(struct.unpack(f">{count}i", raw))
                continue

            if kind == rfb.MESSAGE_UPDATE_REQUEST:
                self._read(9)
                self._answer_request()
                continue

            if kind == rfb.MESSAGE_POINTER:
                self.pointers.append(struct.unpack(">BHH", self._read(5)))
                continue

            if kind == rfb.MESSAGE_KEY:
                self.keys.append(struct.unpack(">BxxI", self._read(7)))
                continue

            raise AssertionError(f"the client sent message {kind}")

    def _run(self) -> None:
        try:
            if self._handshake():
                self._serve()
        except (ConnectionError, OSError):
            pass
        finally:
            self._sock.close()

    def settle(self) -> None:
        """Close the client's end and wait for the server to see it."""

        self.link.close()
        self._thread.join(2)


def raw(width: int, height: int, value: int) -> bytes:
    return bytes([value]) * (width * height * PIXEL)


def connect(server: FakeServer, password: str = "") -> rfb.Client:
    client = rfb.Client(server.link)
    client.handshake(password)

    return client


# ---- saying hello -----------------------------------------------------


def test_a_banner_gives_the_servers_version():
    assert rfb.parse_banner(b"RFB 003.008\n") == (3, 8)


def test_a_port_that_is_not_screen_sharing_is_named_as_such():
    with pytest.raises(rfb.RfbError) as caught:
        rfb.parse_banner(b"SSH-2.0-Open")

    assert "did not answer like a screen-sharing server" in str(caught.value)


def test_apples_version_number_is_spoken_to_as_3_8():
    assert rfb.pick_version((3, 889)) == rfb.VERSION_3_8


def test_an_old_server_is_met_at_its_own_version():
    assert rfb.pick_version((3, 3)) == rfb.VERSION_3_3
    assert rfb.pick_version((3, 7)) == rfb.VERSION_3_7
    assert rfb.pick_version((3, 5)) == rfb.VERSION_3_3


def test_a_server_with_no_password_is_joined():
    server = FakeServer(size=(1280, 800), name="desk:99")
    client = rfb.Client(server.link)

    hello = client.handshake()
    server.settle()

    assert hello.width == 1280
    assert hello.height == 800
    assert hello.name == "desk:99"
    assert hello.security == rfb.SECURITY_NONE
    assert server.client_banner == b"RFB 003.008\n"
    assert server.chosen == rfb.SECURITY_NONE


def test_the_client_asks_for_its_own_pixel_format_and_raw_pixels():
    server = FakeServer()

    connect(server)
    server.settle()

    assert server.pixel_format == rfb.PIXEL_FORMAT
    assert server.encodings == [
        rfb.ENCODING_RAW,
        rfb.ENCODING_DESKTOP_SIZE,
    ]


def test_no_password_is_preferred_when_both_are_offered():
    server = FakeServer(
        offers=(rfb.SECURITY_PASSWORD, rfb.SECURITY_NONE),
    )

    connect(server, password="secret")
    server.settle()

    assert server.chosen == rfb.SECURITY_NONE


def test_the_oldest_version_takes_the_servers_choice():
    server = FakeServer(banner=b"RFB 003.003\n")

    connect(server)
    server.settle()

    assert server.client_banner == b"RFB 003.003\n"
    assert server.chosen == rfb.SECURITY_NONE


def test_a_refusal_carries_the_servers_reason():
    server = FakeServer(refuse_with="Too many viewers")
    client = rfb.Client(server.link)

    with pytest.raises(rfb.RfbError) as caught:
        client.handshake()

    assert "Too many viewers" in str(caught.value)


def test_a_password_server_with_no_password_set_says_what_to_set():
    with pytest.raises(rfb.RfbError) as caught:
        rfb.pick_security([rfb.SECURITY_PASSWORD], has_password=False)

    assert "screen_password_env" in str(caught.value)


def test_a_mac_that_wants_an_account_says_which_switch_to_turn_on():
    with pytest.raises(rfb.RfbError) as caught:
        rfb.pick_security([30, 33, 36, 35], has_password=True)

    assert "VNC viewers may control screen with password" in str(
        caught.value
    )


def test_an_unknown_sign_in_lists_what_was_offered():
    with pytest.raises(rfb.RfbError) as caught:
        rfb.pick_security([19, 18], has_password=True)

    assert "19, 18" in str(caught.value)


# ---- a VNC password ---------------------------------------------------


def test_the_password_key_is_eight_bytes_with_their_bits_reversed():
    # "p" is 0111 0000. Reversed, it is 0000 1110.
    key = rfb.password_key("p")

    assert key == bytes([0x0E, 0, 0, 0, 0, 0, 0, 0])
    assert len(rfb.password_key("a much longer password")) == 8


def test_the_challenge_is_answered_with_plain_des():
    pytest.importorskip("cryptography")

    # A published DES example: this key turns this block into that one.
    # The password is the key with each byte's bits reversed.
    key = bytes.fromhex("133457799BBCDFF1")
    password = bytes(rfb.reverse_bits(byte) for byte in key).decode("latin-1")
    block = bytes.fromhex("0123456789ABCDEF")

    answer = rfb.answer_challenge(block + block, password)

    assert answer == bytes.fromhex("85E813540F0AB405") * 2


def test_a_password_server_gets_the_answer_and_lets_the_client_in():
    pytest.importorskip("cryptography")

    server = FakeServer(offers=(rfb.SECURITY_PASSWORD,))

    hello = rfb.Client(server.link).handshake("secret")
    server.settle()

    assert hello.security == rfb.SECURITY_PASSWORD
    assert server.response == rfb.answer_challenge(bytes(range(16)), "secret")


def test_a_wrong_password_carries_the_servers_reason():
    pytest.importorskip("cryptography")

    server = FakeServer(
        offers=(rfb.SECURITY_PASSWORD,),
        result=1,
        result_reason="Authentication failed",
    )

    with pytest.raises(rfb.RfbError) as caught:
        rfb.Client(server.link).handshake("wrong")

    assert "Authentication failed" in str(caught.value)


# ---- the screen -------------------------------------------------------


def test_one_update_fills_the_frame():
    server = FakeServer(
        size=(4, 2),
        updates=[[(0, 0, 4, 2, rfb.ENCODING_RAW, raw(4, 2, 7))]],
    )
    client = connect(server)

    client.refresh()
    server.settle()

    assert bytes(client.frame) == raw(4, 2, 7)
    assert server.requests == 1


def test_reading_goes_on_until_the_whole_screen_has_arrived():
    server = FakeServer(
        size=(4, 2),
        updates=[
            [(0, 0, 4, 1, rfb.ENCODING_RAW, raw(4, 1, 1))],
            [(0, 1, 4, 1, rfb.ENCODING_RAW, raw(4, 1, 2))],
        ],
        per_request=2,
    )
    client = connect(server)

    client.refresh()
    server.settle()

    assert bytes(client.frame) == raw(4, 1, 1) + raw(4, 1, 2)
    assert server.requests == 1


def test_a_rectangle_narrower_than_the_screen_lands_in_place():
    server = FakeServer(
        size=(4, 2),
        updates=[[
            (0, 0, 4, 2, rfb.ENCODING_RAW, raw(4, 2, 0)),
            (2, 1, 2, 1, rfb.ENCODING_RAW, raw(2, 1, 9)),
        ]],
    )
    client = connect(server)

    client.refresh()
    server.settle()

    assert bytes(client.frame) == raw(4, 1, 0) + raw(2, 1, 0) + raw(2, 1, 9)


def test_pixels_outside_the_screen_are_refused():
    server = FakeServer(
        size=(4, 2),
        updates=[[(2, 0, 4, 2, rfb.ENCODING_RAW, raw(4, 2, 1))]],
    )
    client = connect(server)

    with pytest.raises(rfb.RfbError) as caught:
        client.refresh()

    assert "outside the screen" in str(caught.value)


def test_an_encoding_that_was_not_asked_for_is_refused():
    server = FakeServer(
        size=(4, 2),
        updates=[[(0, 0, 4, 2, 16, b"")]],
    )
    client = connect(server)

    with pytest.raises(rfb.RfbError) as caught:
        client.refresh()

    assert "did not ask for" in str(caught.value)


def test_a_screen_that_changes_size_is_read_again_at_the_new_size():
    server = FakeServer(
        size=(4, 2),
        updates=[
            [(0, 0, 2, 2, rfb.ENCODING_DESKTOP_SIZE, b"")],
            [(0, 0, 2, 2, rfb.ENCODING_RAW, raw(2, 2, 5))],
        ],
    )
    client = connect(server)

    client.refresh()
    server.settle()

    assert (client.width, client.height) == (2, 2)
    assert bytes(client.frame) == raw(2, 2, 5)
    assert server.requests == 2


def test_a_bell_and_a_clipboard_are_read_past():
    bell = bytes([rfb.SERVER_BELL])
    clipboard = (
        bytes([rfb.SERVER_CUT_TEXT])
        + bytes(3)
        + struct.pack(">I", 5)
        + b"hello"
    )

    server = FakeServer(
        size=(4, 2),
        chatter=bell + clipboard,
        updates=[[(0, 0, 4, 2, rfb.ENCODING_RAW, raw(4, 2, 3))]],
    )
    client = connect(server)

    client.refresh()
    server.settle()

    assert bytes(client.frame) == raw(4, 2, 3)


def test_a_screen_too_large_to_be_real_is_refused():
    server = FakeServer(size=(60000, 2))

    with pytest.raises(rfb.RfbError) as caught:
        rfb.Client(server.link).handshake()

    assert "60000 wide" in str(caught.value)


# ---- input ------------------------------------------------------------


def test_a_pointer_event_carries_the_buttons_and_the_place():
    server = FakeServer(size=(100, 50))
    client = connect(server)

    client.pointer(10, 20, rfb.BUTTON_LEFT)
    server.settle()

    assert server.pointers == [(rfb.BUTTON_LEFT, 10, 20)]


def test_a_pointer_is_kept_on_the_screen():
    server = FakeServer(size=(100, 50))
    client = connect(server)

    client.pointer(500, -3)
    server.settle()

    assert server.pointers == [(0, 99, 0)]


def test_a_key_event_carries_down_and_up():
    server = FakeServer()
    client = connect(server)

    client.key(0xFF0D, True)
    client.key(0xFF0D, False)
    server.settle()

    assert server.keys == [(1, 0xFF0D), (0, 0xFF0D)]

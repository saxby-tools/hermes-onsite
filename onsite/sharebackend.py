"""Hermes' screen tool, answered through a target's screen sharing.

The second way to a target's screen. Nothing runs on the target but
the screen sharing it already has, reached through SSH at its own
loopback address. See `rfb` for the protocol and `screenshare` for
the pointer, the keys and the picture.

Screen sharing shows pixels and nothing else. There is no list of
buttons, no window titles and no way to check that a click did what
was meant. So every request by element is turned down with a message
that says to act by position, and a capture always returns a picture.

Hermes' approval prompt and its hard blocks sit above this file and
are untouched. No approval is added, skipped or answered here.

Only Hermes' public backend contract is imported.
"""

from __future__ import annotations

import base64
import logging
import threading

from tools.computer_use.backend import ActionResult
from tools.computer_use.backend import CaptureResult
from tools.computer_use.backend import ComputerUseBackend

from . import messages
from . import screenshare
from .config import TargetSpec


logger = logging.getLogger(__name__)

ROUTE = "screen_sharing"
MODE_VISION = "vision"
PNG = "image/png"
CODE_UNSUPPORTED = "unsupported_action"

# Hermes' own default for the longest side of a picture it sends to a
# model. Read from the same setting its driver uses.
DEFAULT_IMAGE_LIMIT = 1456


def image_limit() -> int:
    from hermes_cli.config import load_config_readonly

    settings = (load_config_readonly() or {}).get("computer_use") or {}

    try:
        limit = int(settings.get("max_image_dimension", DEFAULT_IMAGE_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_LIMIT

    return max(limit, 0)


class SharedScreenBackend(ComputerUseBackend):
    """A target's screen, seen and driven through its screen sharing."""

    def __init__(self, spec: TargetSpec, driver_missing: bool = False):
        self._spec = spec
        self._driver_missing = driver_missing
        self._sharer = None
        self._lock = threading.Lock()

    # ---- the link -----------------------------------------------------

    def _open(self) -> screenshare.Sharer:
        client, hello = screenshare.connect(self._spec)

        logger.info(
            "onsite: screen sharing on %s answered: %s by %s",
            self._spec.label,
            hello.width,
            hello.height,
        )

        return screenshare.Sharer(client, image_limit())

    def _ready(self) -> screenshare.Sharer:
        """The open link, opened again if the last one was lost.

        A laptop that slept takes the link with it. The next request
        after it wakes should work without a new session.
        """

        with self._lock:
            if self._sharer is None:
                self._sharer = self._open()

            return self._sharer

    def _drop(self) -> None:
        with self._lock:
            sharer = self._sharer
            self._sharer = None

        if sharer is None:
            return

        try:
            sharer.close()
        except Exception:
            logger.debug("onsite: closing a lost link failed", exc_info=True)

    def start(self) -> None:
        try:
            self._ready()
        except screenshare.FAILURES as error:
            raise RuntimeError(messages.format_sharing_failed(
                self._spec,
                str(error),
                self._driver_missing,
            )) from None

    def stop(self) -> None:
        self._drop()

    def is_available(self) -> bool:
        return True

    # ---- seeing -------------------------------------------------------

    def capture(
        self,
        mode: str = "som",
        app: str | None = None,
        pid: int | None = None,
        window_id: int | None = None,
    ) -> CaptureResult:
        try:
            picture = self._ready().capture()
        except screenshare.FAILURES as error:
            self._drop()

            raise RuntimeError(
                messages.format_sharing_lost(self._spec, str(error))
            ) from None

        return CaptureResult(
            mode=MODE_VISION,
            width=picture.width,
            height=picture.height,
            png_b64=base64.b64encode(picture.png).decode("ascii"),
            png_bytes_len=len(picture.png),
            image_mime_type=PNG,
            note=messages.SHARING_CAPTURE_NOTE,
        )

    # ---- acting -------------------------------------------------------

    def _unsupported(self, action: str, reason: str) -> ActionResult:
        return ActionResult(
            ok=False,
            action=action,
            message=reason,
            code=CODE_UNSUPPORTED,
        )

    def _act(self, action: str, done: str, work) -> ActionResult:
        """Run one action and say plainly how it went."""

        try:
            work(self._ready())
        except ValueError as error:
            return ActionResult(ok=False, action=action, message=str(error))
        except screenshare.FAILURES as error:
            self._drop()

            return ActionResult(
                ok=False,
                action=action,
                message=messages.format_sharing_lost(self._spec, str(error)),
            )

        # Screen sharing cannot tell whether the desktop took the
        # event the way it was meant. Hermes is told that much.
        return ActionResult(
            ok=True,
            action=action,
            message=done,
            path=ROUTE,
            effect="unverifiable",
        )

    def click(
        self,
        *,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        click_count: int = 1,
        modifiers: list[str] | None = None,
        delivery_mode: str | None = None,
        bring_to_front: bool = False,
    ) -> ActionResult:
        if x is None or y is None:
            return self._unsupported("click", messages.SHARING_NO_ELEMENTS)

        return self._act(
            "click",
            f"clicked at ({x}, {y})",
            lambda sharer: sharer.click(
                x,
                y,
                button,
                click_count,
                modifiers,
            ),
        )

    def drag(
        self,
        *,
        from_element: int | None = None,
        to_element: int | None = None,
        from_xy: tuple[int, int] | None = None,
        to_xy: tuple[int, int] | None = None,
        button: str = "left",
        modifiers: list[str] | None = None,
        delivery_mode: str | None = None,
        bring_to_front: bool = False,
    ) -> ActionResult:
        if from_xy is None or to_xy is None:
            return self._unsupported("drag", messages.SHARING_NO_ELEMENTS)

        return self._act(
            "drag",
            f"dragged from {tuple(from_xy)} to {tuple(to_xy)}",
            lambda sharer: sharer.drag(from_xy, to_xy, button, modifiers),
        )

    def scroll(
        self,
        *,
        direction: str,
        amount: int = 3,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        modifiers: list[str] | None = None,
        delivery_mode: str | None = None,
        bring_to_front: bool = False,
    ) -> ActionResult:
        if element is not None:
            return self._unsupported("scroll", messages.SHARING_NO_ELEMENTS)

        return self._act(
            "scroll",
            f"scrolled {direction} by {amount}",
            lambda sharer: sharer.scroll(direction, amount, x, y, modifiers),
        )

    def type_text(
        self,
        text: str,
        *,
        delivery_mode: str | None = None,
        bring_to_front: bool = False,
    ) -> ActionResult:
        return self._act(
            "type",
            f"typed {len(text)} characters",
            lambda sharer: sharer.type_text(text),
        )

    def key(
        self,
        keys: str,
        *,
        delivery_mode: str | None = None,
        bring_to_front: bool = False,
    ) -> ActionResult:
        return self._act(
            "key",
            f"pressed {keys}",
            lambda sharer: sharer.press(keys),
        )

    # ---- what pixels cannot answer ------------------------------------

    def list_apps(self) -> list:
        return []

    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:
        return self._unsupported("focus_app", messages.SHARING_NO_WINDOWS)

    def set_value(
        self,
        value: str,
        element: int | None = None,
    ) -> ActionResult:
        return self._unsupported("set_value", messages.SHARING_NO_ELEMENTS)

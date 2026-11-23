"""Finding a target's screen, and what is needed to drive it.

Screen control acts on the desktop a person is logged in to. An SSH
login is not part of that desktop: it has no display, no session bus
and no window system. So before anything can be driven, the plugin
has to learn three things about a target, and say plainly which one
is missing:

- which desktop session is the user's, and how to address it,
- whether a screen driver is installed there,
- what that desktop will and will not let a driver do.

The answers come from one short script run over SSH. It reads the
environment of a process that belongs to the user's desktop session.
It changes nothing and installs nothing. The plugin never installs a
driver on a target: that is the owner's call, on the owner's machine.

Standard library only.
"""

from __future__ import annotations

import shlex

from dataclasses import dataclass
from dataclasses import field

from .config import TargetSpec
from .mirror import MirrorError
from .mirror import run_remote
from .remote import QUIET_PATH_LINES


PROBE_TIMEOUT_SECONDS = 15

DRIVER_NAME = "cua-driver"
DRIVER_ARGUMENTS = ("mcp", "--no-overlay")

OS_LINUX = "Linux"
OS_MACOS = "Darwin"

SEAT_NONE = "none"
SEAT_X11 = "x11"
SEAT_WAYLAND = "wayland"
SEAT_MACOS = "macos"

FIELD_SEPARATOR = "\0"

# What a program needs to find the user's desktop on Linux.
SEAT_VARIABLES = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_TYPE",
)

# Hermes sets this for its own driver when the desktop is Wayland and
# the owner has opted in. See `computer_use.native_wayland`.
WAYLAND_INPUT_VARIABLE = "CUA_DRIVER_RS_ENABLE_WAYLAND"
TELEMETRY_VARIABLE = "CUA_DRIVER_RS_TELEMETRY_ENABLED"

# Each line prints `name=value` and a zero byte. A value may hold
# anything but a zero byte, so nothing needs quoting on the way back.
#
# A desktop is made of many processes and not all of them carry every
# variable: the display server itself starts before the session bus
# exists. So the process that knows the most about the desktop wins.
SEAT_PATTERN = "^(" + "|".join(SEAT_VARIABLES) + ")="
PROBE_SCRIPT = (
    "printf 'os=%s\\0' \"$(uname -s)\"\n"
    "printf 'user=%s\\0' \"$(id -un)\"\n"
    "best=0\n"
    "seat=\n"
    "for p in /proc/[0-9]*; do\n"
    "  [ -O \"$p\" ] || continue\n"
    "  e=$(tr '\\0' '\\n' < \"$p/environ\" 2>/dev/null) || continue\n"
    "  case \"$e\" in *DISPLAY=*) ;; *) continue ;; esac\n"
    f"  m=$(printf '%s\\n' \"$e\" | grep -E '{SEAT_PATTERN}')\n"
    "  n=$(printf '%s\\n' \"$m\" | grep -c .)\n"
    "  [ \"$n\" -gt \"$best\" ] || continue\n"
    "  best=$n\n"
    "  seat=$m\n"
    "done\n"
    "[ -n \"$seat\" ] && printf '%s\\n' \"$seat\" | tr '\\n' '\\0'\n"
    "[ \"$(uname -s)\" = Darwin ] && "
    "printf 'console=%s\\0' \"$(stat -f %Su /dev/console 2>/dev/null)\"\n"
    + QUIET_PATH_LINES + "\n"
    f"printf 'driver=%s\\0' \"$(command -v {DRIVER_NAME} 2>/dev/null)\"\n"
)


class ScreenError(RuntimeError):
    """A target's screen cannot be driven. The message says why."""


@dataclass(frozen=True)
class Seat:
    """What one look at a target found."""

    system: str = ""
    user: str = ""
    console_user: str = ""
    driver: str = ""
    variables: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        if self.system == OS_MACOS:
            if self.console_user and self.console_user == self.user:
                return SEAT_MACOS

            return SEAT_NONE

        if self.variables.get("WAYLAND_DISPLAY"):
            return SEAT_WAYLAND

        if self.variables.get("DISPLAY"):
            return SEAT_X11

        return SEAT_NONE


def parse_probe(raw: bytes) -> Seat:
    values = {}

    for item in raw.decode("utf-8", "replace").split(FIELD_SEPARATOR):
        name, separator, value = item.partition("=")

        if not separator:
            continue

        # The first value wins. The script prints a name once, and a
        # later copy could only come from a value that held a newline.
        values.setdefault(name.strip(), value)

    return Seat(
        system=values.get("os", ""),
        user=values.get("user", ""),
        console_user=values.get("console", ""),
        driver=values.get("driver", ""),
        variables={
            name: values[name]
            for name in SEAT_VARIABLES
            if values.get(name)
        },
    )


def read_seat(spec: TargetSpec) -> Seat:
    try:
        raw = run_remote(spec, PROBE_SCRIPT, PROBE_TIMEOUT_SECONDS)
    except MirrorError as error:
        raise ScreenError(f"{spec.label} could not be asked: {error}") from None

    return parse_probe(raw)


def find_problems(seat: Seat) -> list[str]:
    """Why the screen cannot be driven yet. Empty when it can."""

    problems = []

    if seat.kind == SEAT_NONE and seat.system == OS_MACOS:
        problems.append(
            f"nobody is logged in at the screen as `{seat.user}`. Log in "
            "there first."
        )

    if seat.kind == SEAT_NONE and seat.system != OS_MACOS:
        problems.append(
            f"no desktop session was found for `{seat.user}`. Log in at "
            "the machine's own screen first."
        )

    if not seat.driver:
        problems.append(
            f"`{DRIVER_NAME}` is not installed there. On that machine, "
            "run `hermes computer-use install`, or install it as its "
            "own project describes. This plugin never installs it."
        )

    return problems


def find_notes(seat: Seat) -> list[str]:
    """Limits a person should know before relying on it."""

    if seat.kind == SEAT_WAYLAND:
        return [
            "the desktop is Wayland. Hermes' driver reads the screen "
            "through XWayland, so a window drawn by a Wayland-only "
            "program may be missing from what it sees.",
            "typing and clicking on Wayland are off until you set "
            "`computer_use.native_wayland: true`.",
        ]

    if seat.kind == SEAT_MACOS:
        return [
            "macOS asks at the machine itself before a program may "
            "read the screen or send input. Grant both there once.",
        ]

    return []


def driver_environment(seat: Seat, native_wayland: bool) -> dict:
    """The variables the driver needs to find the user's desktop."""

    environment = {
        name: value
        for name, value in seat.variables.items()
        if name in SEAT_VARIABLES
    }

    # Off unless the owner opted in on the host, as Hermes does.
    environment[TELEMETRY_VARIABLE] = "0"

    if seat.kind == SEAT_WAYLAND and native_wayland:
        environment[WAYLAND_INPUT_VARIABLE] = "1"

    return environment


def build_driver_script(seat: Seat, native_wayland: bool = False) -> str:
    """What runs on the target: join the desktop, then be the driver."""

    if not seat.driver:
        raise ScreenError(f"`{DRIVER_NAME}` is not installed on the target")

    exports = [
        f"export {name}={shlex.quote(value)}"
        for name, value in sorted(
            driver_environment(seat, native_wayland).items()
        )
    ]

    command = shlex.join([seat.driver, *DRIVER_ARGUMENTS])

    return "; ".join([*exports, f"exec {command}"])

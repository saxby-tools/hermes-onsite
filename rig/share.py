"""Drive the desk's screen through screen sharing, below Hermes.

The screen phase tests screen control the way a person meets it: a
Hermes session, the `computer_use` tool, Hermes' own approval prompt.
This phase tests what is under all that, with no Hermes in the way.
It loads the plugin's own modules and has them connect to the desk's
VNC server through SSH, look, click, type, drag and scroll. Each
action is then checked from inside the container.

It exists for two reasons. A failure here is the plugin's link or
protocol, not Hermes. And it needs no running gateway, so it can be
run when the gateway cannot be restarted.

Standard library only. The plugin's picture encoder needs the Pillow
package Hermes ships, so this phase reads the raw frame instead.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
import time

from pathlib import Path


PACKAGE = "onsite_rig"

DISPLAY = ":99"
TERMINAL_TITLE = "desk-terminal"
TERMINAL_WAIT_SECONDS = 15
SETTLE_SECONDS = 0.6

# A point inside the terminal's text, and one on its title bar. The
# terminal is placed by `desk-start.sh`.
TEXT_POINT = (300, 200)
TITLE_POINT = (340, 49)
DRAG_BY = (200, 100)

TYPED_FILE = "/tmp/onsite-rig-shared.txt"
TYPED_TEXT = "share-rig"

# A second window that prints every button it is sent.
EVENTS_FILE = "/tmp/onsite-rig-events.txt"
EVENTS_PLACE = "300x200+800+400"
EVENTS_POINT = (950, 520)
EVENTS_TITLE = "Event Tester"

CLOSED_PORT = 5999

WHEEL_DOWN_TURNS = 3
WHEEL_UP_TURNS = 2

# X numbers the buttons: left, middle, right, wheel up, wheel down.
EXPECTED_BUTTONS = {
    "1": 2,
    "3": 1,
    "4": WHEEL_UP_TURNS,
    "5": WHEEL_DOWN_TURNS,
}


def load_plugin(plugin_dir: Path):
    """The plugin's own code, loaded by path as the tests load it."""

    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]

    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)

    return module


def on_desk(hands, command: str) -> str:
    return hands.exec(f"export DISPLAY={DISPLAY}; {command}").strip()


def window_place(hands, title: str) -> tuple[int, int]:
    """Where a window's content starts, as the X server reports it."""

    raw = on_desk(
        hands,
        f"xdotool search --name '{title}' getwindowgeometry "
        "| sed -n 's/.*Position: \\([0-9]*\\),\\([0-9]*\\).*/\\1 \\2/p'",
    )
    x, y = raw.split()

    return int(x), int(y)


def wait_for_terminal(hands) -> None:
    deadline = time.monotonic() + TERMINAL_WAIT_SECONDS

    while time.monotonic() < deadline:
        found = on_desk(
            hands,
            f"xdotool search --name '{TERMINAL_TITLE}' 2>/dev/null || true",
        )

        if found:
            return

        time.sleep(0.5)

    raise RuntimeError("the desk's terminal never appeared")


def count_buttons(hands) -> dict[str, int]:
    """How many presses of each button the second window was sent."""

    raw = on_desk(
        hands,
        f"grep -A3 ButtonPress {EVENTS_FILE} "
        "| grep -oE 'button [0-9]+' | sort | uniq -c || true",
    )
    counts = {}

    for line in raw.splitlines():
        count, _, button = line.split()
        counts[button] = int(count)

    return counts


def check_link(report, onsite, target) -> bool:
    report.section("Sharing: the screen answers through SSH")

    answer = onsite.screenshare.ask(target)
    detail = answer.problem

    if answer.ok:
        detail = f"{answer.hello.width} by {answer.hello.height}"

    report.check("screen sharing answers at the target's loopback", answer.ok,
                 detail)

    closed = dataclasses.replace(target, screen_port=CLOSED_PORT)
    refusal = onsite.screenshare.ask(closed)

    report.check(
        "a port with nothing behind it fails with ssh's own reason",
        not refusal.ok and bool(refusal.problem),
        refusal.problem,
    )

    return answer.ok


def check_picture(report, client) -> None:
    report.section("Sharing: the screen arrives as pixels")

    client.refresh()

    expected = client.width * client.height * 4
    lit = len(client.frame) - client.frame.count(0)

    report.check(
        "one request brings the whole screen",
        len(client.frame) == expected,
        f"{client.width} by {client.height}",
    )
    report.check(
        "the picture is not blank",
        lit > 0,
        f"{lit} bytes are not black",
    )


def check_typing(report, hands, sharer) -> None:
    report.section("Sharing: a click and a typed line land in the terminal")

    hands.exec(f"rm -f {TYPED_FILE}")

    sharer.click(*TEXT_POINT)
    sharer.type_text(f"echo {TYPED_TEXT} > {TYPED_FILE}")
    sharer.press("return")
    time.sleep(SETTLE_SECONDS)

    typed = hands.read(TYPED_FILE) or ""

    report.check(
        "the line ran in the terminal on the target",
        typed.strip() == TYPED_TEXT,
        f"file holds: {typed.strip()!r}",
    )


def check_drag(report, hands, sharer) -> None:
    report.section("Sharing: a drag moves a window by the same distance")

    before = window_place(hands, TERMINAL_TITLE)
    end = (TITLE_POINT[0] + DRAG_BY[0], TITLE_POINT[1] + DRAG_BY[1])

    sharer.drag(TITLE_POINT, end)
    time.sleep(SETTLE_SECONDS)

    after = window_place(hands, TERMINAL_TITLE)
    moved = (after[0] - before[0], after[1] - before[1])

    report.check(
        "the terminal moved by the length of the drag",
        moved == DRAG_BY,
        f"moved by {moved}",
    )

    sharer.drag(end, TITLE_POINT)
    time.sleep(SETTLE_SECONDS)

    report.check(
        "and back again",
        window_place(hands, TERMINAL_TITLE) == before,
    )


def check_buttons(report, hands, sharer) -> None:
    report.section("Sharing: each button arrives as itself")

    on_desk(
        hands,
        f"(stdbuf -oL xev -event button -geometry {EVENTS_PLACE} "
        f"> {EVENTS_FILE} 2>&1 &); sleep 1",
    )

    try:
        sharer.scroll("down", WHEEL_DOWN_TURNS, *EVENTS_POINT)
        sharer.scroll("up", WHEEL_UP_TURNS, *EVENTS_POINT)
        sharer.click(*EVENTS_POINT, button="right")
        sharer.click(*EVENTS_POINT, count=2)
        time.sleep(SETTLE_SECONDS)

        counts = count_buttons(hands)
    finally:
        on_desk(hands, "pkill xev || true")

    report.check(
        "wheel turns, a right click and a double click were all seen",
        counts == EXPECTED_BUTTONS,
        f"presses by button: {counts}",
    )


def run(report, hands, plugin_dir: Path) -> None:
    onsite = load_plugin(plugin_dir)

    importlib.import_module(f"{PACKAGE}.screenshare")

    target = onsite.config.TargetSpec(
        name="laptop",
        kind=onsite.config.KIND_SSH,
        host=hands.ssh_host,
        port=hands.ssh_port,
        user=hands.user,
        key=str(hands.key),
        known_hosts=str(hands.known_hosts),
        connect_timeout=5,
        screen=onsite.config.SCREEN_SHARING,
    )

    wait_for_terminal(hands)

    if not check_link(report, onsite, target):
        return

    client, _ = onsite.screenshare.connect(target)
    sharer = onsite.screenshare.Sharer(client, 0)

    try:
        check_picture(report, client)
        check_typing(report, hands, sharer)
        check_drag(report, hands, sharer)
        check_buttons(report, hands, sharer)
    finally:
        sharer.close()

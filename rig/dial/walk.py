"""Walk the dial-in feature from the computer's side.

Two containers stand in for two machines. `dial-server` runs Hermes
with the plugin. `dial-laptop` is a computer whose SSH server answers
on its own loopback address only, so the server cannot log in to it.
The laptop opens the link itself, with the command the plugin prints.

The walk then does what a person would: runs a command and reads a
file through the link, mounts the laptop's disk, cuts the link,
brings it back, freezes the laptop, lets another account on the
server listen on the laptop's port, and adds a second computer.

It builds and starts containers, so it defaults to a dry run. Pass
`--apply` to run it. `--keep` leaves the containers up at the end,
with a link open, so the real desktop app can be pointed at them.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time

from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS = REPO / "rig" / "results" / "dial-walk.txt"

SERVER = "dial-server"
LAPTOP = "dial-laptop"
DESKTOP = "dial-desktop"
DESKTOP_IMAGE = "onsite-rig/dial-desktop:dev"
NETWORK = "dial-net"

# Where the `dev` account's own service manager is reached, and the
# file its link is saved in.
USER_SESSION = "XDG_RUNTIME_DIR=/run/user/1000"
UNIT_FILE = "onsite-dial-in.service"

HERMES = "/opt/hermes/.venv/bin/hermes"
PYTHON = "/opt/hermes/.venv/bin/python3"
HERMES_ENV = ["-e", "HOME=/opt/data", "-e", "HERMES_HOME=/opt/data"]

TARGET = "laptop"
SECOND_TARGET = "desktop"
LAPTOP_USER = "dev"
SERVER_LOGIN = "hermes@server"
MOUNT_PLACE = "/mnt/onsite/laptop"
PROJECT = "~/Development/sample"
HOST_PROJECT = f"{MOUNT_PLACE}/home/{LAPTOP_USER}/Development/sample"
SEEDED_FILE = "notes.txt"
SEEDED_WORD = "bravo"

KEYS_FILE = "/etc/dial/hermes.keys"
GUEST_KEY = "/home/guest/hostkey"
GUEST_LOG = "/home/guest/sshd.log"
GUEST_PID = "/home/guest/sshd.pid"

# A folder that is on the server and on the laptop under one path. A
# session opened in it runs on the laptop by a folder rule, and its
# folder on the server is not on the laptop's mount.
ROUTED_FOLDER = "/srv/dial"

API = "http://127.0.0.1:9119/api/plugins/onsite"
TOKEN_HEADER = "X-Hermes-Session-Token"

BOOT_SECONDS = 90
LINK_SECONDS = 20
# The breaker cools for ten seconds after a failure. One more gives
# it room.
RECOVERY_SECONDS = 25
FAIL_FAST_SECONDS = 5.0
# How long a turn of the session on the mount is waited for while
# the laptop is frozen, before the laptop is thawed.
MOUNT_TURN_SECONDS = 30
# How long a mount may take to let go of a frozen laptop. The mount
# program gives up after two missed answers, five seconds apart.
MOUNT_GONE_SECONDS = 40
RETRY_SECONDS = 5
# How long the laptop is off the network in the hard drop. The
# laptop's own `ssh` gives a dead link up after 45 seconds.
DROP_SECONDS = 60
DROP_RECOVERY_SECONDS = 180

# Text the plugin is expected to say. A bare "Connection refused" is
# what the walk is here to rule out.
SAYS_NO_LINK = "has not connected to this server, or its link dropped"
SAYS_SILENT = "its link is up but it does not answer"
SAYS_OTHER_KEY = "something else answers on port"
SSH_OWN_WORDS = "Connection refused"

API_SCRIPT = """
import json, os, sys, urllib.request
request = urllib.request.Request(
    sys.argv[1],
    data=sys.argv[2].encode(),
    headers={
        "Content-Type": "application/json",
        sys.argv[3]: os.environ["HERMES_DASHBOARD_SESSION_TOKEN"],
    },
)
print(urllib.request.urlopen(request, timeout=30).read().decode())
"""

ARGV_SCRIPT = """
import json, sys
from onsite import config, mcptools, remote
spec = config.parse_config(json.loads(sys.argv[1])).target("{target}")
print(json.dumps({call}))
"""

KEEP_LOOP = (
    "while true; do {command} >>/tmp/dial-loop.log 2>&1; "
    "sleep {retry}; done"
)

# Where the laptop comes back when the walk moves it. `up.sh` makes
# the network with this subnet.
MOVED_ADDRESS = "10.213.77.177"


class Report:
    """What the walk saw, for the screen and for the results file."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failed = 0
        self.passed = 0

    def write(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def title(self, text: str) -> None:
        self.write()
        self.write(f"== {text}")

    def note(self, text: str) -> None:
        for line in text.strip().splitlines():
            self.write(f"   | {line}")

    def check(self, name: str, passed: bool, seen: str = "") -> bool:
        mark = "PASS" if passed else "FAIL"

        self.write(f"  [{mark}] {name}")

        if seen:
            self.note(seen)

        if passed:
            self.passed += 1
            return True

        self.failed += 1

        return False

    def save(self) -> None:
        RESULTS.write_text("\n".join(self.lines) + "\n")


def run(command: list[str], timeout: float = 120, stdin: str = ""):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        input=stdin or None,
        check=False,
    )


def said(done) -> str:
    """What a command printed, less Hermes' note about its own
    install, which is about the container image and not the walk."""

    lines = (done.stdout + done.stderr).splitlines()

    return "\n".join(
        line for line in lines if "install out of sync" not in line
    ).strip()


def hermes(*args: str, timeout: float = 120):
    return run(
        ["docker", "exec", "-u", "hermes", *HERMES_ENV, SERVER,
         HERMES, *args],
        timeout,
    )


def on_server(script: str, user: str = "hermes", timeout: float = 60):
    return run(
        ["docker", "exec", "-u", user, *HERMES_ENV, SERVER,
         "sh", "-c", script],
        timeout,
    )


def on_laptop(script: str, user: str = LAPTOP_USER, timeout: float = 60):
    return run(
        ["docker", "exec", "-u", user, LAPTOP, "sh", "-c", script],
        timeout,
    )


def in_background_on_laptop(script: str) -> None:
    run(["docker", "exec", "-d", "-u", LAPTOP_USER, LAPTOP,
         "sh", "-c", script])


def ask_api(path: str, body: dict) -> dict:
    done = run(
        ["docker", "exec", "-u", "hermes", *HERMES_ENV, SERVER,
         PYTHON, "-c", API_SCRIPT, f"{API}{path}", json.dumps(body),
         TOKEN_HEADER],
        timeout=60,
    )

    try:
        return json.loads(done.stdout)
    except ValueError:
        return {"ok": False, "error": said(done)}


def status_line(name: str = TARGET) -> str:
    """The line `hermes onsite status` prints for one target."""

    for line in said(hermes("onsite", "status")).splitlines():
        words = line.replace("*", " ").split()

        if words[:1] == [name]:
            return line.strip()

    return ""


def wait_for_status(wanted: str, limit: float, name: str = TARGET):
    """Poll until a target's status line holds `wanted`. Returns the
    line and how long it took, or nothing and the limit."""

    started = time.monotonic()

    while time.monotonic() - started < limit:
        line = status_line(name)

        if wanted in line:
            return line, time.monotonic() - started

        time.sleep(1)

    return "", limit


def timed_status(name: str = TARGET) -> tuple[str, float]:
    started = time.monotonic()
    line = status_line(name)

    return line, time.monotonic() - started


class Session:
    """One Hermes session on the server, kept open across steps."""

    def __init__(self, folder: str) -> None:
        self._process = subprocess.Popen(
            ["docker", "exec", "-i", "-u", "hermes", *HERMES_ENV, SERVER,
             PYTHON, "/rig/dial/session.py", folder],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.ready = self._read().get("ready") is True

    def _read(self) -> dict:
        line = self._process.stdout.readline()

        try:
            return json.loads(line)
        except ValueError:
            return {"error": line.strip() or "the session ended"}

    def prompt(self, text: str) -> dict:
        self._process.stdin.write(text + "\n")
        self._process.stdin.flush()

        return self._read()

    def close(self) -> None:
        self._process.stdin.close()

        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._process.kill()


def tool_text(turn: dict) -> str:
    return "\n".join(tool["text"] for tool in turn.get("tools", []))


def describe_turn(turn: dict) -> str:
    if "error" in turn:
        return f"error: {turn['error']}"

    lines = []

    for tool in turn.get("tools", []):
        lines.append(
            f"{tool['title']} [{tool['status']}, {tool['seconds']}s]"
        )
        lines += tool["text"].strip().splitlines()[:8]

    return "\n".join(lines) or f"no tool ran: {turn.get('message', '')}"


def bring_up(alive: str) -> None:
    done = subprocess.run(
        ["sh", str(HERE / "up.sh")],
        env={**os.environ, "DIAL_CLIENT_ALIVE": alive},
        check=False,
    )

    if done.returncode != 0:
        raise SystemExit("the containers could not be started")


def wait_for_backend() -> bool:
    started = time.monotonic()

    while time.monotonic() - started < BOOT_SECONDS:
        answer = ask_api("/targets", {})

        if answer.get("ok"):
            return True

        time.sleep(2)

    return False


def read_link_command(text: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith("ssh -N "):
            return line.strip()

    return ""


def read_key_options(text: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith("restrict,"):
            return line.strip()

    return ""


def read_port(text: str) -> int:
    found = re.search(r"on port (\d+)", text)

    return int(found.group(1)) if found else 0


def start_link(command: str) -> None:
    in_background_on_laptop(command)


def stop_link() -> None:
    on_laptop("pkill -u dev -x ssh; true")


def step_unreachable(report: Report) -> None:
    report.title("1. The server cannot reach the laptop")

    done = on_server(
        "ssh -o BatchMode=yes -o ConnectTimeout=3 "
        "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"{LAPTOP_USER}@laptop true"
    )

    report.check(
        "a login from the server to the laptop is refused",
        done.returncode != 0 and SSH_OWN_WORDS in said(done),
        said(done),
    )


def step_add(report: Report) -> tuple[str, str, int]:
    report.title("2. Add the laptop on the server, with the CLI")

    dry = hermes(
        "onsite", "dial-in", TARGET,
        "--user", LAPTOP_USER, "--server", SERVER_LOGIN,
    )

    report.check(
        "without --apply nothing is written and no command is shown",
        "nothing was written" in said(dry)
        and not read_link_command(said(dry)),
    )

    done = hermes(
        "onsite", "dial-in", TARGET,
        "--user", LAPTOP_USER, "--server", SERVER_LOGIN, "--apply",
    )
    text = said(done)
    command = read_link_command(text)
    options = read_key_options(text)
    port = read_port(text)

    report.check("the plugin picked a port", port >= 1024, f"port {port}")
    report.check(
        "it printed one command to run on the laptop",
        command.endswith(SERVER_LOGIN) and f":{port}:" in command,
        command,
    )
    report.check(
        "it printed the line that limits the laptop's key",
        f'permitlisten="127.0.0.1:{port}"' in options,
        options,
    )

    # In this image the account's home is the Hermes home, where the
    # plugin will not mount. The README says to name a place.
    hermes(
        "config", "set",
        f"plugins.entries.onsite.settings.targets.{TARGET}.mount",
        MOUNT_PLACE,
    )

    line, seconds = timed_status()

    report.check(
        "before the laptop dials, status says it has not connected",
        SAYS_NO_LINK in line and SSH_OWN_WORDS not in line,
        f"{line}\n({seconds:.1f}s)",
    )

    return command, options, port


def step_dial(report: Report, command: str, options: str) -> None:
    report.title("3. On the laptop: a key, then the printed command")

    # What a person does once: make a key, say yes to the server's
    # host key, and put the public key on the server. `ssh-keyscan`
    # stands in for typing "yes" at the prompt.
    on_laptop(
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "ssh-keygen -q -t ed25519 -N '' -f ~/.ssh/id_ed25519 && "
        "ssh-keyscan -T 5 server > ~/.ssh/known_hosts 2>/dev/null"
    )
    public = on_laptop("cat ~/.ssh/id_ed25519.pub").stdout.strip()

    on_server(
        f"printf '%s\\n' {shlex.quote(options + ' ' + public)} "
        f"> {KEYS_FILE}",
        user="root",
    )

    start_link(command)

    line, seconds = wait_for_status("up (", LINK_SECONDS)

    report.check(
        "the laptop ran the command and status turned to up",
        bool(line),
        f"{line}\n(seen {seconds:.1f}s after the command was started)",
    )

    shell = on_laptop(f"ssh -o BatchMode=yes {SERVER_LOGIN} id")

    report.check(
        "the limited key cannot run a command on the server",
        shell.returncode != 0 and "uid=" not in shell.stdout,
        said(shell) or f"exit {shell.returncode}",
    )

    filed = on_server(f"ssh-keygen -l -F onsite-{TARGET}").stdout
    own = on_laptop(
        "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"
    ).stdout.split()
    fingerprint = own[1] if len(own) > 1 else "missing"

    report.check(
        "the laptop's host key is filed under the target's own name",
        fingerprint in filed,
        f"filed for onsite-{TARGET}: {fingerprint}",
    )


def step_api(report: Report, command: str, port: int) -> None:
    report.title("4. What the desktop app is told")

    answer = ask_api("/dial-in", {"target": TARGET})

    report.check(
        "the route hands over the same command and says connected",
        answer.get("command") == command
        and answer.get("port") == port
        and answer.get("connected") is True,
        json.dumps({
            key: answer.get(key)
            for key in ("ok", "port", "connected", "latency_ms", "error")
        }),
    )

    status = ask_api("/status", {"cwd": HOST_PROJECT, "rows": True})
    rows = {row["name"]: row for row in status.get("rows") or []}
    row = rows.get(TARGET, {})

    report.check(
        "the machine list marks it as one that dials in, and up",
        row.get("dial_in") is True and row.get("connected") is True,
        json.dumps({
            key: row.get(key)
            for key in ("label", "dial_in", "connected", "mount_error")
        }),
    )


def step_edit(report: Report, port: int) -> None:
    report.title("5. Change the saved machine, the way the app does")

    # A folder rule, so that a session can run on the laptop from a
    # folder that is not on its mount. The freeze step needs one.
    for place in (SERVER, LAPTOP):
        run(["docker", "exec", place, "sh", "-c",
             f"mkdir -p {ROUTED_FOLDER} && chmod 0777 {ROUTED_FOLDER}"])

    saved = ask_api("/targets", {})
    block = dict(saved.get("targets", {}).get(TARGET, {}))

    block["prefixes"] = [ROUTED_FOLDER]

    answer = ask_api("/targets/save", {"name": TARGET, "target": block})
    after = answer.get("targets", {}).get(TARGET, {})

    report.check(
        "saving it again keeps its port, so its link is not cut",
        answer.get("ok") is True and after.get("port") == port,
        json.dumps(after),
    )

    line = status_line()

    report.check("it is still up", "up (" in line, line)


def step_work(report: Report) -> Session:
    report.title("6. Work through the link: mount, file, command")

    opened = hermes("onsite", "open", f"{TARGET}:{PROJECT}")

    report.check(
        "the laptop's disk mounts on the server through the link",
        HOST_PROJECT in said(opened),
        said(opened),
    )

    read = on_server(f"cat {HOST_PROJECT}/{SEEDED_FILE}")

    report.check(
        "a file on the laptop is read through the mount",
        SEEDED_WORD in read.stdout,
        read.stdout.strip(),
    )

    session = Session(HOST_PROJECT)

    report.check("a Hermes session opens in that folder", session.ready)

    turn = session.prompt("script:where")

    report.check(
        "a command runs on the laptop, as its user, in its folder",
        "laptop" in tool_text(turn) and LAPTOP_USER in tool_text(turn),
        describe_turn(turn),
    )

    turn = session.prompt("script:files")
    wrote = on_laptop(f"cat {PROJECT}/rig-note.txt")

    report.check(
        "the file tools write and read the laptop's own files",
        SEEDED_WORD in wrote.stdout and "laptop" in tool_text(turn),
        describe_turn(turn),
    )

    return session


def plugin_ssh(call: str) -> list[str]:
    """An `ssh` command line, built by the plugin's own code for the
    laptop as the settings describe it. `call` is the builder, with
    `spec` standing for the laptop."""

    saved = ask_api("/targets", {})
    settings = {"default": saved.get("default"), "targets": saved["targets"]}

    built = run(
        ["docker", "exec", "-i", "-u", "hermes", *HERMES_ENV,
         "-e", "PYTHONPATH=/opt/data/plugins", SERVER, PYTHON, "-",
         json.dumps(settings)],
        stdin=ARGV_SCRIPT.format(target=TARGET, call=call),
    )

    try:
        return ["ssh", *json.loads(built.stdout)]
    except ValueError:
        return ["false", said(built)]


def step_extras(report: Report) -> None:
    report.title("7. The other links through the same port")

    # An MCP server on the laptop is a program whose input and
    # output Hermes holds over `ssh`. `cat` stands in for one: what
    # goes in must come back.
    words = plugin_ssh("mcptools.build_ssh_arguments(spec, ['cat'])")
    echoed = run(
        ["docker", "exec", "-i", "-u", "hermes", *HERMES_ENV, SERVER,
         *words],
        timeout=30,
        stdin="over the link\n",
    )

    report.check(
        "the link an MCP server on the laptop talks over works",
        bool(words) and "over the link" in echoed.stdout,
        f"{shlex.join(words)}\n-> {echoed.stdout.strip() or said(echoed)}",
    )

    # Screen sharing is reached with `ssh -W`, to a port on the
    # laptop's own loopback address. The laptop has no screen, so
    # its SSH server's own port stands in: it greets whoever
    # connects.
    words = plugin_ssh("remote.build_forward_arguments(spec, 22)")
    greeted = run(
        ["docker", "exec", "-i", "-u", "hermes", *HERMES_ENV, SERVER,
         *words],
        timeout=30,
        stdin="\n",
    )

    report.check(
        "a forward to a port on the laptop itself works (ssh -W)",
        greeted.stdout.startswith("SSH-2.0"),
        f"{shlex.join(words)}\n-> "
        f"{greeted.stdout.strip()[:40] or said(greeted)}",
    )


def is_mounted() -> bool:
    """Whether the laptop's disk is mounted on the server now. Read
    from the system's own list, so a dead mount is not touched."""

    found = on_server(f"grep -q ' {MOUNT_PLACE} ' /proc/mounts")

    return found.returncode == 0


def remount_as_the_chip_does() -> float:
    """Ask what the app's chip asks every five seconds for a session
    in a folder on the mount. That question is what mounts the disk
    again. Returns how long the mount took to be there."""

    started = time.monotonic()

    while time.monotonic() - started < RECOVERY_SECONDS:
        ask_api("/status", {"cwd": HOST_PROJECT, "rows": False})

        if is_mounted():
            break

        time.sleep(1)

    return time.monotonic() - started


def watch_mount_go(seen: dict) -> threading.Thread:
    """Note, in the background, how long the mount takes to let go
    from now."""

    started = time.monotonic()

    def watch() -> None:
        while time.monotonic() - started < MOUNT_GONE_SECONDS:
            if not is_mounted():
                seen["seconds"] = time.monotonic() - started
                return

            time.sleep(0.5)

    watcher = threading.Thread(target=watch, daemon=True)

    watcher.start()

    return watcher


def step_cut(report: Report, session: Session, command: str) -> None:
    report.title("8. Cut the link, then bring it back")

    turn = session.prompt("script:where")

    report.check(
        "a session in a folder of its own on the server runs there too",
        "laptop" in tool_text(turn),
        describe_turn(turn),
    )

    stop_link()

    line, seconds = timed_status()

    report.check(
        "status says the link dropped, not what ssh says",
        SAYS_NO_LINK in line and SSH_OWN_WORDS not in line,
        f"{line}\n({seconds:.1f}s)",
    )

    # The session used its link a moment ago, so this call goes
    # straight to it and finds it gone under the command.
    turn = session.prompt("script:where")
    text = tool_text(turn)

    report.check(
        "a tool call on the link it just used says the link dropped",
        SAYS_NO_LINK in text and turn.get("seconds", 99) < FAIL_FAST_SECONDS,
        describe_turn(turn),
    )

    turn = session.prompt("script:where")
    text = tool_text(turn)

    report.check(
        "the next call is refused at once, and nothing is sent",
        SAYS_NO_LINK in text
        and "No command was run" in text
        and turn.get("seconds", 99) < FAIL_FAST_SECONDS,
        describe_turn(turn),
    )

    answer = ask_api("/status", {"cwd": HOST_PROJECT, "rows": True})

    report.check(
        "the desktop app is told the same, with no second reason",
        SAYS_NO_LINK in str(answer.get("detail"))
        and not answer.get("mount_error"),
        json.dumps({
            key: answer.get(key)
            for key in ("connected", "detail", "mount_error")
        }),
    )

    started = time.monotonic()
    read = on_server(f"cat {HOST_PROJECT}/{SEEDED_FILE}", timeout=60)

    report.check(
        "a read through the mount fails and does not hang",
        read.returncode != 0,
        f"{said(read)}\n({time.monotonic() - started:.1f}s)",
    )
    report.check(
        "the mount has let go: no dead mount is left in place",
        not is_mounted(),
    )

    start_link(command)

    back = time.monotonic()
    turn = {}

    while time.monotonic() - back < RECOVERY_SECONDS:
        turn = session.prompt("script:where")

        if "laptop" in tool_text(turn) and SAYS_NO_LINK not in tool_text(turn):
            break

        time.sleep(1)

    report.check(
        "the same session works again, with nothing done on the server",
        "laptop" in tool_text(turn) and SAYS_NO_LINK not in tool_text(turn),
        f"{describe_turn(turn)}\n"
        f"({time.monotonic() - back:.1f}s after the laptop dialed again)",
    )

    seconds = remount_as_the_chip_does()
    read = on_server(f"cat {HOST_PROJECT}/{SEEDED_FILE}", timeout=60)

    report.check(
        "the chip's next question brings the mount back",
        SEEDED_WORD in read.stdout,
        f"{read.stdout.strip() or said(read)}\n"
        f"(mounted {seconds:.1f}s after it was asked)",
    )


def measure_mounted_turn(report: Report, mounted: Session):
    """Time a turn of a session whose folder is on the mount, while
    the laptop is frozen.

    Hermes itself reads a session's folder at the start of a turn, on
    the server, through the mount. That is the mount's own behaviour
    for any target, so it is measured here and not gated.
    """

    answer: dict = {}
    started = time.monotonic()
    worker = threading.Thread(
        target=lambda: answer.update(mounted.prompt("script:where")),
        daemon=True,
    )

    worker.start()
    worker.join(MOUNT_TURN_SECONDS)

    waited = time.monotonic() - started
    outcome = f"ended after {waited:.1f}s: {describe_turn(answer)}"

    if worker.is_alive():
        outcome = f"still waiting after {waited:.0f}s, while frozen"

    report.write("  [MEASURED] a turn of the session on the mount, frozen")
    report.note(outcome)

    return worker


def step_freeze(report: Report, session: Session, mounted: Session) -> None:
    report.title("9. Freeze the laptop: a link that is up and silent")

    # Let the session's own link go idle first, so the next call
    # checks it before it sends anything.
    time.sleep(6)
    remount_as_the_chip_does()

    gone: dict = {}

    run(["docker", "pause", LAPTOP])

    watcher = watch_mount_go(gone)
    line, seconds = timed_status()

    report.check(
        "status says the link is up but the laptop does not answer",
        SAYS_SILENT in line,
        f"{line}\n({seconds:.1f}s)",
    )

    turn = session.prompt("script:where")

    report.check(
        "a tool call says so too, and nothing is sent",
        SAYS_SILENT in tool_text(turn)
        and "No command was run" in tool_text(turn),
        describe_turn(turn),
    )

    worker = measure_mounted_turn(report, mounted)

    watcher.join(MOUNT_GONE_SECONDS)

    report.check(
        "the mount lets go of the frozen laptop: no dead mount stays",
        "seconds" in gone,
        f"gone {gone.get('seconds', -1):.1f}s after the freeze",
    )

    run(["docker", "unpause", LAPTOP])
    worker.join(RECOVERY_SECONDS * 2)

    line, seconds = wait_for_status("up (", RECOVERY_SECONDS)

    report.check(
        "thawed, the same link answers again: the laptop did not dial",
        bool(line),
        f"{line}\n({seconds:.1f}s after the thaw)",
    )

    time.sleep(RETRY_SECONDS * 2)
    turn = session.prompt("script:where")

    report.check(
        "and the session works again",
        "laptop" in tool_text(turn) and SAYS_SILENT not in tool_text(turn),
        describe_turn(turn),
    )

    seconds = remount_as_the_chip_does()
    read = on_server(f"cat {HOST_PROJECT}/{SEEDED_FILE}", timeout=60)

    report.check(
        "the chip's next question brings the mount back here too",
        SEEDED_WORD in read.stdout,
        f"{read.stdout.strip() or said(read)}\n"
        f"(mounted {seconds:.1f}s after it was asked)",
    )


def step_other_account(report: Report, command: str, port: int) -> None:
    report.title("10. Another account on the server takes the port")

    stop_link()
    time.sleep(1)

    # An SSH server of the guest's own, with a key of its own, on the
    # port the laptop let go of. Any account may do this.
    run(["docker", "exec", "-u", "guest", SERVER, "sh", "-c",
         f"rm -f {GUEST_KEY} {GUEST_KEY}.pub && "
         f"ssh-keygen -q -t ed25519 -N '' -f {GUEST_KEY} && "
         f"/usr/sbin/sshd -f /dev/null -h {GUEST_KEY} -p {port} "
         f"-o ListenAddress=127.0.0.1 -o PidFile={GUEST_PID} "
         f"-o AuthorizedKeysFile=/home/guest/keys -E {GUEST_LOG}"])
    time.sleep(1)

    line, seconds = timed_status()

    report.check(
        "status says something else answers, and that nothing was sent",
        SAYS_OTHER_KEY in line and "nothing was sent" in line,
        f"{line}\n({seconds:.1f}s)",
    )

    log = run(["docker", "exec", SERVER, "cat", GUEST_LOG]).stdout

    report.check(
        "the stand-in saw a connection that ended before any login",
        "[preauth]" in log and "Accepted" not in log,
        "\n".join(log.strip().splitlines()[-3:]),
    )

    dialed = on_laptop(f"timeout 15 {command}", timeout=30)

    report.check(
        "the laptop's own command says its port is taken",
        "remote port forwarding failed" in said(dialed),
        said(dialed),
    )

    run(["docker", "exec", "-u", "guest", SERVER, "sh", "-c",
         f"kill $(cat {GUEST_PID})"])
    time.sleep(1)
    start_link(command)

    line, seconds = wait_for_status("up (", LINK_SECONDS)

    report.check(
        "with the port let go, the laptop is back under its own key",
        bool(line),
        f"{line}\n({seconds:.1f}s)",
    )


def step_second(report: Report, port: int) -> None:
    report.title("11. A second computer never shares the port")

    done = hermes(
        "onsite", "dial-in", SECOND_TARGET,
        "--user", LAPTOP_USER, "--server", SERVER_LOGIN, "--apply",
    )
    other = read_port(said(done))

    report.check(
        "the second computer was given a port of its own",
        other >= 1024 and other != port,
        f"{TARGET}: {port}, {SECOND_TARGET}: {other}",
    )

    forced = hermes(
        "onsite", "dial-in", SECOND_TARGET, "--port", str(port), "--apply",
    )

    report.check(
        "asking for the first one's port is refused",
        "both use port" in said(forced),
        said(forced),
    )


def step_keep(report: Report, command: str) -> None:
    report.title("12. Keep it running: a loop in place of the service")

    unit = said(hermes("onsite", "dial-in", TARGET, "--keep", "linux"))
    started_by = ""

    for line in unit.splitlines():
        if line.strip().startswith("ExecStart="):
            started_by = line.strip()[len("ExecStart="):]

    report.check(
        "the service file starts the same link, with nobody to ask",
        "BatchMode=yes" in started_by and command.split()[-1] in started_by,
        started_by,
    )

    # The stand-in laptop has no service manager. This loop does what
    # `Restart=always` and `RestartSec=5` do: start it again five
    # seconds after it ends.
    stop_link()
    in_background_on_laptop(
        KEEP_LOOP.format(command=started_by, retry=RETRY_SECONDS)
    )

    line, seconds = wait_for_status("up (", LINK_SECONDS)

    report.check("the loop opened the link", bool(line), line)

    stop_link()

    line, seconds = wait_for_status("up (", LINK_SECONDS * 2)

    report.check(
        "killed, the link comes back by itself",
        bool(line),
        f"{line}\n({seconds:.1f}s after it was killed)",
    )


def laptop_address() -> str:
    done = run([
        "docker", "inspect", "--format",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        LAPTOP,
    ])

    return done.stdout.strip()


def lose_packets_to(address: str) -> None:
    """Make the server's packets to an address vanish without a word.

    A laptop that moves to another network leaves nothing behind at
    its old address: no refusal, no error. A Docker network says "no
    such host" at once, which is kinder than the real thing. So the
    server is told to drop what it sends there and hears from there.
    """

    for chain, side in (("OUTPUT", "-d"), ("INPUT", "-s")):
        run(["docker", "exec", SERVER,
             "iptables", "-I", chain, side, address, "-j", "DROP"])


def step_drop(report: Report, alive: str, moved: bool) -> None:
    report.title("13. The laptop falls off the network, then returns")

    where = "at the same address"

    if moved:
        where = "at a new address, and nothing answers at the old one"

    report.write(
        f"   server keepalive (ClientAliveInterval): {alive or 'none'}"
    )
    report.write(f"   the laptop returns {where}")

    if moved:
        lose_packets_to(laptop_address())

    run(["docker", "network", "disconnect", NETWORK, LAPTOP])

    line, seconds = timed_status()

    report.check(
        "status notices within seconds",
        SAYS_SILENT in line,
        f"{line}\n({seconds:.1f}s)",
    )

    time.sleep(DROP_SECONDS)

    address = ["--ip", MOVED_ADDRESS] if moved else []

    run(["docker", "network", "connect", "--alias", "laptop",
         *address, NETWORK, LAPTOP])

    line, seconds = wait_for_status("up (", DROP_RECOVERY_SECONDS)
    seen = (
        f"{line or status_line()}\n"
        f"({seconds:.1f}s after the network came back, "
        f"{DROP_SECONDS}s off it)"
    )

    # A server with no keepalive holds the dead link's port until its
    # system gives up on the connection, which is minutes. That is
    # the documented cost, so it is measured here and not gated.
    if not line and not alive:
        said_by_laptop = on_laptop("tail -n 2 /tmp/dial-loop.log").stdout

        report.write("  [MEASURED] with no keepalive the port is still held")
        report.note(seen)
        report.note(said_by_laptop)

        return

    report.check(
        "back on the network, the loop dials in again by itself",
        bool(line),
        seen,
    )


def on_desktop(script: str, user: str = "root", timeout: float = 60):
    return run(
        ["docker", "exec", "-u", user, "-e", USER_SESSION, DESKTOP,
         "sh", "-c", script],
        timeout,
    )


def read_block(text: str, after: str, until: str = "") -> list[str]:
    """The indented lines between two lines of what the plugin
    printed, with the indent taken off. With no second line named,
    the lines to the end."""

    lines = []
    inside = False

    for line in text.splitlines():
        if until and until in line:
            inside = False

        if inside:
            lines.append(line[3:])

        if after in line:
            inside = True

    # The empty lines around the block are the plugin's spacing. The
    # ones inside it belong to the file.
    return "\n".join(lines).strip("\n").splitlines()


def start_desktop(report: Report) -> bool:
    """A second computer, with a real service manager."""

    built = run(
        ["docker", "build", "--quiet", "--tag", DESKTOP_IMAGE,
         "--file", str(HERE / "desktop.Dockerfile"), str(HERE)],
        timeout=600,
    )

    run(["docker", "rm", "--force", DESKTOP])

    started = run([
        "docker", "run", "--detach", "--name", DESKTOP,
        "--hostname", "desktop", "--network", NETWORK,
        "--privileged", "--cgroupns=host",
        "--volume", "/sys/fs/cgroup:/sys/fs/cgroup:rw",
        "--tmpfs", "/run", "--tmpfs", "/run/lock",
        DESKTOP_IMAGE,
    ])

    time.sleep(5)

    state = on_desktop("systemctl is-system-running").stdout.strip()

    return report.check(
        "a second computer starts with systemd as its service manager",
        built.returncode == 0
        and started.returncode == 0
        and state in ("running", "degraded"),
        state or said(started) or said(built),
    )


def step_service(report: Report) -> None:
    report.title("14. The printed service file, under a real systemd")

    if not start_desktop(report):
        return

    public = (REPO / ".rig" / "dial" / "key.pub").read_text().strip()

    # As for any target: the server's key is accepted on the desktop.
    on_desktop(
        f"mkdir -p /etc/dial && printf '%s\\n' {shlex.quote(public)} "
        "> /etc/dial/dev.keys && systemctl restart ssh"
    )

    # The desktop's own key, limited on the server to its one port.
    on_desktop(
        "mkdir -p ~/.ssh ~/.config/systemd/user && chmod 700 ~/.ssh && "
        "ssh-keygen -q -t ed25519 -N '' -f ~/.ssh/id_ed25519 && "
        "ssh-keyscan -T 5 server > ~/.ssh/known_hosts 2>/dev/null",
        user=LAPTOP_USER,
    )
    own = on_desktop("cat ~/.ssh/id_ed25519.pub", user=LAPTOP_USER)
    options = read_key_options(
        said(hermes("onsite", "dial-in", SECOND_TARGET))
    )

    on_server(
        f"printf '%s\\n' {shlex.quote(options + ' ' + own.stdout.strip())} "
        f">> {KEYS_FILE}",
        user="root",
    )

    printed = said(
        hermes("onsite", "dial-in", SECOND_TARGET, "--keep", "linux")
    )
    unit = read_block(printed, "save this as", "Then turn it on")
    starts = read_block(printed, "Then turn it on", "It runs while")
    stops = read_block(printed, "To turn it off again")

    # Saved where the plugin says, and turned on with its own lines.
    on_desktop("loginctl enable-linger dev")
    time.sleep(2)
    run(["docker", "exec", "-i", "-u", LAPTOP_USER, DESKTOP, "sh", "-c",
         f"cat > /home/dev/.config/systemd/user/{UNIT_FILE}"],
        stdin="\n".join(unit) + "\n")

    turned_on = on_desktop(" && ".join(starts), user=LAPTOP_USER)
    line, seconds = wait_for_status("up (", LINK_SECONDS, SECOND_TARGET)

    report.check(
        "saved and turned on with the printed lines, it opens the link",
        turned_on.returncode == 0 and bool(line),
        f"{' && '.join(starts)}\n{line or said(turned_on)}\n"
        f"({seconds:.1f}s)",
    )

    both = said(hermes("onsite", "status"))
    up = [text for text in both.splitlines() if "dials in on port" in text]

    report.check(
        "two computers are connected at once, each on its own port",
        len(up) == 2,
        "\n".join(text.strip() for text in up),
    )

    on_desktop("pkill -u dev -x ssh")

    killed = time.monotonic()

    wait_for_status("DOWN", LINK_SECONDS, SECOND_TARGET)

    line, seconds = wait_for_status("up (", LINK_SECONDS * 2, SECOND_TARGET)
    restarts = on_desktop(
        f"systemctl --user show {UNIT_FILE} -p NRestarts",
        user=LAPTOP_USER,
    ).stdout.strip()

    report.check(
        "killed, systemd starts the link again by itself",
        bool(line) and restarts != "NRestarts=0",
        f"{line}\n({time.monotonic() - killed:.1f}s after it was killed, "
        f"{restarts})",
    )

    turned_off = on_desktop(" && ".join(stops), user=LAPTOP_USER)
    line, seconds = wait_for_status("DOWN", LINK_SECONDS, SECOND_TARGET)

    report.check(
        "turned off with the printed line, the link closes",
        turned_off.returncode == 0 and SAYS_NO_LINK in line,
        f"{' && '.join(stops)}\n{line}",
    )


def take_down() -> None:
    subprocess.run(["sh", str(HERE / "down.sh")], check=False)


def walk(report: Report, alive: str, moved: bool, service: bool) -> None:
    bring_up(alive)

    if not wait_for_backend():
        raise SystemExit("the Hermes backend never answered")

    step_unreachable(report)

    command, options, port = step_add(report)

    step_dial(report, command, options)
    step_api(report, command, port)
    step_edit(report, port)

    mounted = step_work(report)
    routed = Session(ROUTED_FOLDER)

    step_extras(report)
    step_cut(report, routed, command)
    step_freeze(report, routed, mounted)

    routed.close()
    mounted.close()

    step_other_account(report, command, port)
    step_second(report, port)
    step_keep(report, command)
    step_drop(report, alive, moved)

    if service:
        step_service(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="run the walk. Without it, only this plan is shown.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the containers up at the end.",
    )
    parser.add_argument(
        "--alive",
        default="",
        metavar="SECONDS",
        help="give the server's SSH server a keepalive of this many "
        "seconds. Default: none, as most servers have.",
    )
    parser.add_argument(
        "--moved",
        action="store_true",
        help="bring the laptop back from the network drop at a new "
        "address, as a laptop on another network would be.",
    )
    parser.add_argument(
        "--no-service",
        action="store_true",
        help="skip the step that needs a container with systemd in "
        "it, which Docker has to be allowed to run.",
    )
    args = parser.parse_args()

    if not args.apply:
        print("[DRY RUN] would build and start dial-server and "
              "dial-laptop, walk the")
        print("[DRY RUN] feature from the laptop's side, and remove "
              "them. Add --apply.")
        return 0

    report = Report()

    report.write("Dial-in, walked from the computer's side")
    report.write(time.strftime("%Y-%m-%d %H:%M:%S %Z"))

    try:
        walk(report, args.alive, args.moved, not args.no_service)
    finally:
        report.write()
        report.write(f"{report.passed} passed, {report.failed} failed")
        report.save()

        if not args.keep:
            take_down()

    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())

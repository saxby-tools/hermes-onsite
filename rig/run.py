"""Run the hermes-hands acceptance tests end to end.

The rig is three parts:

- the hands: a container with sshd playing the user's laptop, or
  with `--real USER@HOST` a real machine,
- a stub model that replays scripted tool calls,
- a Hermes test profile, driven over the Agent Client Protocol.

This script changes state: it builds a container or writes a scratch
folder on the real machine, and it swaps the test profile's config
for the length of the run. So it defaults to a dry run. Pass
`--apply` to run for real. The profile's own config is put back at
the end, byte for byte, even when a test fails.

Standard library only.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acp  # noqa: E402  (the path line above has to come first)
import term  # noqa: E402

from machines import ContainerHands  # noqa: E402
from machines import RealHands  # noqa: E402
from shell import run  # noqa: E402
from shell import wait_for_port  # noqa: E402


REPO = Path(__file__).resolve().parent.parent
RIG_DIR = REPO / ".rig"
KEYS_DIR = RIG_DIR / "keys"
WORK_DIR = RIG_DIR / "work"
LAPTOP_SESSION_DIR = WORK_DIR / "sample"
MAC_SESSION_DIR = RIG_DIR / "mac-work"
LOG_DIR = RIG_DIR / "logs"
RESULTS_DIR = REPO / "rig" / "results"

PLUGIN_ID = "hermes-hands"
HOST_NODE = os.uname().nodename

# The folder the gated command removes. The name is the rig's own,
# so a wrong working folder on a real machine could not hit anything.
VICTIM = "hands-rig-victim"

INHIBIT_WHO = "hermes-hands"
INHIBIT_COUNT_COMMAND = (
    f"systemd-inhibit --list --no-pager | grep -c {INHIBIT_WHO} || true"
)
STUB_PORT = 11499
SERVE_PORT = 18733
SERVE_START_SECONDS = 60
TOKEN_ENV = "HERMES_DASHBOARD_SESSION_TOKEN"
TOKEN_HEADER = "X-Hermes-Session-Token"
PLUGIN_API = f"http://127.0.0.1:{SERVE_PORT}/api/plugins/{PLUGIN_ID}"

BACKUP_SUFFIX = ".hands-rig-backup"
STUB_PID_FILE = "stub-model.pid"
DEFAULT_PROFILE = "buster"
DEFAULT_HERMES = "hermes"

# The breaker's cooldown is ten seconds. One more gives it room.
RECOVERY_WAIT_SECONDS = 11
FAIL_FAST_SECONDS = 5.0
FAIL_AT_ONCE_SECONDS = 1.0

# The SSH target probes a link that has been unused for five seconds.
IDLE_WAIT_SECONDS = 6
# A target that dies with a command in flight is the slow case: the
# shared connection has to time out first. It is measured, not gated
# at five seconds.
MID_FLIGHT_LIMIT_SECONDS = 10.0

# How long a terminal client gets to draw its first screen, and how
# long a turn gets to finish.
TERMINAL_BOOT_SECONDS = {"--cli": 8.0, "--tui": 12.0}
TERMINAL_TURN_SECONDS = 40.0
RUN_WAIT_SECONDS = 20.0

# Each client words Hermes' approval prompt its own way. In both,
# the fourth choice is Deny.
APPROVAL_TITLES = {
    "--cli": "Dangerous Command",
    "--tui": "approval required",
}
APPROVAL_DENY_KEY = "4"

MODE_DRY = "[DRY RUN]"
MODE_LIVE = "[LIVE]"

CONFIG_TEMPLATE = """\
model:
  provider: custom
  default: stub
  base_url: http://127.0.0.1:{stub_port}/v1
  api_key: stub
  context_length: 131072
_config_version: 53
telemetry:
  shared_metrics:
    enabled: false
    send: false
    offer_version: 2
approvals:
  mode: manual
terminal:
  backend: hands
plugins:
  enabled:
    - {plugin}
  entries:
    {plugin}:
      allow_gateway_injection: true
      settings:
        default: mac
        targets:
          laptop:
            kind: ssh
            host: {ssh_host}
            port: {ssh_port}
            user: {user}
            key: {key}
            known_hosts: {known_hosts}
            prefixes:
              - {home}/
            path_map:
              {work}: {root}
          mac:
            kind: local
"""

PLAN = (
    "start the stub model",
    "back up the test profile's config.yaml",
    "write a rig config.yaml that points at the stub and the hands",
    "link this checkout into the profile's plugins folder",
    "ask the running gateway to load the plugin",
    "run the acceptance tests and save their output",
    "unload the plugin and put the profile's config.yaml back",
    "stop the stub model and clean up the hands machine",
)

# Machine names and paths a run adds to the ones `scrub` hides.
SCRUB_PAIRS: list[tuple[str, str]] = []


class Report:
    """Collects check results and the text saved for each phase."""

    def __init__(self):
        self.lines: list[str] = []
        self.failures = 0

    def write(self, text: str = "") -> None:
        print(text)
        self.lines.append(text)

    def section(self, title: str) -> None:
        self.write()
        self.write(f"== {title}")

    def check(self, name: str, passed: bool, detail: str = "") -> None:
        mark = "PASS" if passed else "FAIL"

        if not passed:
            self.failures += 1

        self.write(f"  {mark}  {name}")

        if detail:
            self.write(f"        {detail}")

    def quote(self, title: str, text: str) -> None:
        self.write(f"  --- {title}")

        for line in text.rstrip("\n").splitlines():
            self.write(f"  | {line}")


def scrub(text: str) -> str:
    """Keep machine-specific paths out of saved results."""

    text = text.replace(str(REPO), "<repo>")
    text = text.replace(str(Path.home()), "~")
    text = text.replace(socket.gethostname(), "<this-host>")

    # `whoami` prints the name alone on a line.
    lines = [
        "<this-user>" if line == getpass.getuser() else line
        for line in text.split("\n")
    ]

    text = "\n".join(lines)

    # A run's own names go last. A real laptop's user may have the
    # same name as this host's, and the lines above come first.
    for needle, shown in SCRUB_PAIRS:
        if needle:
            text = text.replace(needle, shown)

    return text


def make_dirs() -> None:
    for folder in (KEYS_DIR, LAPTOP_SESSION_DIR, MAC_SESSION_DIR, LOG_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def start_stub() -> subprocess.Popen:
    log = open(LOG_DIR / "stub-model.log", "w")

    process = subprocess.Popen(
        [
            sys.executable,
            str(REPO / "rig" / "stub-model" / "server.py"),
            "--port", str(STUB_PORT),
        ],
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
    )

    wait_for_port(STUB_PORT, 10)

    return process


def render_config(hands) -> str:
    return CONFIG_TEMPLATE.format(
        stub_port=STUB_PORT,
        plugin=PLUGIN_ID,
        ssh_host=hands.ssh_host,
        ssh_port=hands.ssh_port,
        user=hands.user,
        key=hands.key,
        known_hosts=hands.known_hosts,
        home=hands.home,
        root=hands.root,
        work=WORK_DIR,
    )


class Profile:
    """The Hermes test profile, and the care taken to put it back."""

    def __init__(self, hermes: str, name: str, home: Path):
        self.hermes = hermes
        self.name = name
        self.home = home
        self.config = home / "config.yaml"
        self.backup = home / f"config.yaml{BACKUP_SUFFIX}"
        self.link = home / "plugins" / PLUGIN_ID

    def command(self, *args: str) -> list[str]:
        return [self.hermes, "-p", self.name, *args]

    def plugins(self, verb: str) -> None:
        # The CLI nudges the running gateway to rescan this profile's
        # plugins. Nothing is restarted.
        run(self.command("plugins", verb, PLUGIN_ID), cwd=str(RIG_DIR))

    def reload(self) -> None:
        """Make the gateway re-import the plugin from this checkout."""

        self.plugins("disable")
        self.plugins("enable")

    def restore(self) -> None:
        if not self.backup.exists():
            return

        shutil.copy2(self.backup, self.config)
        self.backup.unlink()

    def install(self, config_text: str) -> None:
        # A backup left by a crashed run is the real config. It is
        # put back before anything else is saved over it.
        self.restore()

        shutil.copy2(self.config, self.backup)
        self.config.write_text(config_text)

        self.link.parent.mkdir(parents=True, exist_ok=True)

        if self.link.is_symlink():
            self.link.unlink()

        self.link.symlink_to(REPO)
        self.reload()

    def uninstall(self) -> None:
        self.plugins("disable")
        self.restore()

    def cli(self, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return run(self.command(*args), cwd=str(cwd), timeout=120)

    def terminal(self, cwd: Path, flag: str, name: str) -> term.Terminal:
        screen = term.Terminal(
            self.command(flag),
            cwd,
            LOG_DIR / f"term-{name}.log",
        )
        screen.settle(TERMINAL_BOOT_SECONDS[flag])

        return screen

    def oneshot(self, cwd: Path, prompt: str) -> str:
        """Send one prompt through the plain CLI and return its reply."""

        done = run(self.command("-z", prompt), cwd=str(cwd), timeout=180)

        return done.stdout + done.stderr

    def session(self, cwd: Path, name: str) -> acp.AcpClient:
        client = acp.AcpClient(
            self.command("acp"),
            cwd,
            LOG_DIR / f"acp-{name}.log",
        )
        client.start()

        return client


def show_turn(report: Report, turn: acp.Turn) -> None:
    for tool in turn.tools:
        title = f"{tool.title} [{tool.status}, {tool.seconds:.2f}s]"
        report.quote(title, scrub(tool.text))

    for permission in turn.permissions:
        options = " / ".join(permission["options"])
        title = scrub(permission["title"])
        report.write(f"  --- approval prompt: {title}")
        report.write(f"  | options: {options}")
        report.write(f"  | answered: {permission['answer']}")


def require_rig_folder(hands, text: str) -> None:
    """Stop a phase before it writes anywhere but the rig's folder."""

    if hands.sample_dir in text:
        return

    raise RuntimeError(
        "the session is not in the rig's folder on the hands machine. "
        "Stopping before anything is written there."
    )


def phase_one(report: Report, profile: Profile, hands) -> None:
    report.section("Phase 1.1: a session in a laptop folder runs there")
    client = profile.session(LAPTOP_SESSION_DIR, "laptop")

    try:
        turn = client.prompt("script:where")
        show_turn(report, turn)
        text = turn.tool_text()

        report.check("pwd is the laptop path", hands.sample_dir in text)
        report.check("whoami is the laptop user", f"\n{hands.user}\n" in text)
        report.check("the machine name is the laptop's", hands.node in text)

        require_rig_folder(hands, text)

        report.section("Phase 1.2: PATH from the interactive shell")
        turn = client.prompt("script:path")

        # A real machine's PATH says what is installed there. It is
        # checked and counted, not saved.
        if hands.quote_path:
            show_turn(report, turn)

        passed, detail = hands.path_verdict(turn.tool_text())
        report.check(hands.path_claim, passed, detail)

        report.section("Phase 1.3: file tools act on the laptop's files")
        turn = client.prompt("script:files")
        show_turn(report, turn)

        finished = [tool.status for tool in turn.tools]
        note = f"{hands.sample_dir}/rig-note.txt"
        on_host = LAPTOP_SESSION_DIR / "rig-note.txt"

        report.check(
            "write, read, search and cat all completed",
            finished == ["completed"] * 4,
            f"statuses: {finished}",
        )
        report.check(
            "the file was written in the session folder on the laptop",
            hands.exec(f"cat {note}") == "alpha\nbravo\n",
        )
        report.check(
            "nothing was written on the host",
            not on_host.exists(),
        )
        report.check(
            "read_file returned the laptop's content",
            "bravo" in turn.tools[1].text,
        )
        report.check(
            "search_files found the new file",
            "rig-note.txt" in turn.tools[2].text,
        )

        # The editor protocol previews every edit by reading the file
        # on the host, so a patch to a file that only the laptop has
        # is refused there. That is a Hermes limit, written up in
        # docs/upstream.md. The plain CLI has no such preview.
        report.write("  patch runs through the plain CLI:")
        reply = profile.oneshot(LAPTOP_SESSION_DIR, "script:patch")
        report.quote("hermes -z script:patch", scrub(reply))

        report.check(
            "patch edited the file on the laptop",
            hands.exec(f"cat {note}") == "alpha\ncharlie\n",
            f"content: {hands.exec(f'cat {note}')!r}",
        )
    finally:
        client.close()

    report.section("Phase 1.4: a session in a Mac path runs on the Mac")
    client = profile.session(MAC_SESSION_DIR, "mac")

    try:
        turn = client.prompt("script:where")
        show_turn(report, turn)
        text = turn.tool_text()

        report.check(
            "pwd is the host folder",
            str(MAC_SESSION_DIR.resolve()) in text,
        )
        report.check("whoami is the host user", getpass.getuser() in text)
        report.check("the machine name is the host's", HOST_NODE in text)
    finally:
        client.close()

    report.section("Phase 1.5: a denied approval stops the command")
    client = profile.session(LAPTOP_SESSION_DIR, "deny")

    try:
        turn = client.prompt("script:danger", decide=acp.deny)
        show_turn(report, turn)

        report.check(
            "Hermes' built-in prompt fired for the gated command",
            len(turn.permissions) == 1,
            f"prompts: {len(turn.permissions)}",
        )
        report.check(
            "the denied command did not run",
            "SURVIVED" in turn.tools[-1].text,
        )
        kept = hands.exec(f"ls {hands.sample_dir}/{VICTIM}").strip()

        report.check(
            "the folder is still on the laptop",
            kept == "keep",
        )
    finally:
        client.close()

    report.section("Phase 1.6: an allowed approval runs the command")
    client = profile.session(LAPTOP_SESSION_DIR, "allow")

    try:
        turn = client.prompt("script:danger", decide=acp.allow)
        show_turn(report, turn)

        report.check(
            "the prompt fired again in a new session",
            len(turn.permissions) == 1,
        )
        report.check("the command ran", "GONE" in turn.tools[-1].text)
    finally:
        client.close()


def phase_two(report: Report, profile: Profile, hands) -> None:
    report.section("Phase 2.1: the target stops under a live session")
    client = profile.session(LAPTOP_SESSION_DIR, "resilience")

    try:
        turn = client.prompt("script:where")
        report.check(
            "the session works before the outage",
            hands.sample_dir in turn.tool_text(),
        )

        hands.stop()

        turn = client.prompt("script:where")
        show_turn(report, turn)
        first = turn.tools[0]

        report.check(
            "the error names the target",
            "laptop" in first.text and "hands" in first.text,
        )
        report.check(
            f"it arrives within {FAIL_FAST_SECONDS:.0f} seconds",
            first.seconds < FAIL_FAST_SECONDS,
            f"took {first.seconds:.2f}s",
        )
        report.check(
            "it says the command was not retried",
            "not retried" in first.text or "nothing was retried" in first.text,
        )

        turn = client.prompt("script:where")
        show_turn(report, turn)
        second = turn.tools[0]

        report.check(
            "the next call fails at once",
            second.seconds < FAIL_AT_ONCE_SECONDS,
            f"took {second.seconds:.2f}s",
        )
        report.check(
            "it still names the target",
            "laptop" in second.text,
        )

        hands.resume()
        report.write(f"  waiting {RECOVERY_WAIT_SECONDS}s for the cooldown")
        time.sleep(RECOVERY_WAIT_SECONDS)

        turn = client.prompt("script:where")
        show_turn(report, turn)

        report.check(
            "service resumes with no restart once the target is back",
            hands.sample_dir in turn.tool_text(),
        )
    finally:
        client.close()

    report.section("Phase 2.2: a new session while the target is down")
    hands.stop()
    client = profile.session(LAPTOP_SESSION_DIR, "down-at-start")

    try:
        turn = client.prompt("script:where")
        show_turn(report, turn)
        first = turn.tools[0]

        report.check(
            "the error names the target",
            "laptop" in first.text,
        )
        report.check(
            f"it arrives within {FAIL_FAST_SECONDS:.0f} seconds",
            first.seconds < FAIL_FAST_SECONDS,
            f"took {first.seconds:.2f}s",
        )
        report.check(
            "the session did not fail over to another machine",
            HOST_NODE not in first.text
            and str(LAPTOP_SESSION_DIR.resolve()) not in first.text,
        )
    finally:
        client.close()
        hands.resume()


def phase_two_frozen(report: Report, profile: Profile, hands) -> None:
    report.section("Phase 2.3: the target freezes, as a sleeping laptop")
    client = profile.session(LAPTOP_SESSION_DIR, "frozen")

    try:
        turn = client.prompt("script:where")
        report.check(
            "the session works before the freeze",
            hands.sample_dir in turn.tool_text(),
        )

        hands.freeze()
        report.write(f"  idle for {IDLE_WAIT_SECONDS}s, then a tool call")
        time.sleep(IDLE_WAIT_SECONDS)

        turn = client.prompt("script:where")
        show_turn(report, turn)
        first = turn.tools[0]

        report.check(
            "the error names the target",
            "laptop" in first.text and "hands" in first.text,
        )
        report.check(
            f"it arrives within {FAIL_FAST_SECONDS:.0f} seconds",
            first.seconds < FAIL_FAST_SECONDS,
            f"took {first.seconds:.2f}s",
        )
        report.check(
            "the command was never sent",
            "No command was run" in first.text,
        )

        turn = client.prompt("script:where")
        second = turn.tools[0]

        report.check(
            "the next call fails at once",
            second.seconds < FAIL_AT_ONCE_SECONDS,
            f"took {second.seconds:.2f}s",
        )

        hands.thaw()
        report.write(f"  waiting {RECOVERY_WAIT_SECONDS}s for the cooldown")
        time.sleep(RECOVERY_WAIT_SECONDS)

        turn = client.prompt("script:where")
        show_turn(report, turn)

        report.check(
            "service resumes with no restart once the target wakes",
            hands.sample_dir in turn.tool_text(),
        )
    finally:
        client.close()
        hands.thaw()


def phase_two_mid_flight(report: Report, profile: Profile, hands) -> None:
    report.section("Phase 2.4: the target freezes just before a call")
    client = profile.session(LAPTOP_SESSION_DIR, "mid-flight")

    try:
        turn = client.prompt("script:where")
        report.check(
            "the session works before the freeze",
            hands.sample_dir in turn.tool_text(),
        )

        hands.freeze()

        turn = client.prompt("script:where")
        show_turn(report, turn)
        first = turn.tools[0]

        report.check(
            "the error names the target",
            "laptop" in first.text and "hands" in first.text,
        )
        report.check(
            "it says the command may have run and was not retried",
            "may have run" in first.text and "not retried" in first.text,
        )
        report.check(
            f"it arrives within {MID_FLIGHT_LIMIT_SECONDS:.0f} seconds",
            first.seconds < MID_FLIGHT_LIMIT_SECONDS,
            f"took {first.seconds:.2f}s (the five-second bar is missed "
            "here; see DECISIONS.md)",
        )
    finally:
        client.close()
        hands.thaw()


def phase_two_all(report: Report, profile: Profile, hands) -> None:
    phase_two(report, profile, hands)

    # The breaker belongs to the target, not to a session. The outage
    # above left it open, so the next test waits out the cooldown.
    time.sleep(RECOVERY_WAIT_SECONDS)

    phase_two_frozen(report, profile, hands)

    time.sleep(RECOVERY_WAIT_SECONDS)

    phase_two_mid_flight(report, profile, hands)


def init_flags(hands) -> list[str]:
    """Flags that describe the rig's own targets, so applying them
    leaves the rig working."""

    return [
        "--ssh", f"laptop={hands.user}@{hands.ssh_host}:{hands.ssh_port}",
        "--local", "mac",
        "--prefix", f"laptop={hands.home}/",
        "--map", f"laptop={WORK_DIR}={hands.root}",
        "--key", f"laptop={hands.key}",
        "--known-hosts", f"laptop={hands.known_hosts}",
    ]


def phase_three_cli(report: Report, profile: Profile, hands) -> None:
    report.section("Phase 3.1: hermes hands status")
    done = profile.cli(LAPTOP_SESSION_DIR, "hands", "status")
    report.quote("in a laptop folder", scrub(done.stdout))

    report.check(
        "a laptop folder shows the laptop target and that it is up",
        "Target    laptop" in done.stdout and "connected" in done.stdout,
    )
    report.check(
        "it shows the folder the session would start in",
        f"Start in  {hands.sample_dir}" in done.stdout,
    )
    report.check("it exits 0 when the target is up", done.returncode == 0)

    done = profile.cli(MAC_SESSION_DIR, "hands", "status")

    report.check(
        "a Mac folder shows the local target",
        "Target    mac" in done.stdout,
    )

    hands.stop()
    done = profile.cli(LAPTOP_SESSION_DIR, "hands", "status")
    hands.resume()
    report.quote("with the laptop stopped", scrub(done.stdout))

    report.check(
        "a stopped target is shown as not reachable",
        "NOT REACHABLE" in done.stdout and "DOWN" in done.stdout,
    )
    report.check("it exits 1 when the target is down", done.returncode == 1)

    report.section("Phase 3.2: hermes hands init")
    before = profile.config.read_bytes()
    done = profile.cli(RIG_DIR, "hands", "init", *init_flags(hands))
    report.quote("without --apply", scrub(done.stdout))

    report.check(
        "without --apply it is a dry run",
        "[DRY RUN]" in done.stdout and "nothing was written" in done.stdout,
    )
    report.check(
        "the dry run left config.yaml untouched",
        profile.config.read_bytes() == before,
    )

    done = profile.cli(RIG_DIR, "hands", "init", "--ssh", "laptop")

    report.check(
        "a malformed flag is refused with a reason",
        done.returncode == 2 and "NAME=VALUE" in done.stdout,
        scrub(done.stdout.strip()),
    )

    done = profile.cli(RIG_DIR, "hands", "init", *init_flags(hands), "--apply")

    report.check(
        "with --apply it writes and says so",
        "[LIVE] written." in done.stdout,
    )

    done = profile.cli(LAPTOP_SESSION_DIR, "hands", "status")

    report.check(
        "the written settings load and route as before",
        "Target    laptop" in done.stdout and done.returncode == 0,
    )


def wait_for_file(hands, path: str, seconds: float) -> str:
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        content = hands.read(path)

        if content is not None:
            return content

        time.sleep(0.5)

    return ""


def phase_three_slash(report: Report, profile: Profile, hands) -> None:
    report.section("Phase 3.3: /hands shows and switches the target")
    client = profile.session(LAPTOP_SESSION_DIR, "slash")

    try:
        turn = client.prompt("script:where")
        require_rig_folder(hands, turn.tool_text())

        turn = client.prompt("/hands")
        report.quote("/hands", scrub(turn.message))

        report.check(
            "/hands names the target, its state and the folder",
            "Hands:   laptop" in turn.message
            and "connected" in turn.message
            and hands.sample_dir in turn.message,
        )

        turn = client.prompt("/hands mac")
        report.quote("/hands mac", scrub(turn.message))
        report.check(
            "/hands mac moves the session",
            "now runs on mac" in turn.message,
        )

        turn = client.prompt("script:where")
        show_turn(report, turn)
        report.check(
            "the next tool call runs on the Mac, in the session folder",
            str(LAPTOP_SESSION_DIR.resolve()) in turn.tool_text(),
        )

        turn = client.prompt("/hands auto")
        report.check(
            "/hands auto goes back to the folder rules",
            "The folder rules pick laptop" in turn.message,
        )

        turn = client.prompt("script:where")
        show_turn(report, turn)
        report.check(
            "the next tool call runs on the laptop again",
            hands.sample_dir in turn.tool_text(),
        )

        turn = client.prompt("/hands nowhere")
        report.check(
            "an unknown target is refused with the choices",
            "is not a target" in turn.message and "laptop, mac" in turn.message,
            scrub(turn.message.strip()),
        )

        report.section("Phase 3.4: propose_command and /run")
        marker = f"{hands.sample_dir}/proposed.txt"
        hands.exec(f"rm -f {marker}")

        turn = client.prompt("script:propose")
        show_turn(report, turn)

        report.check(
            "propose_command prints a numbered block",
            "Proposed command #1" in turn.tool_text()
            and "Run it with /run 1" in turn.tool_text(),
        )
        report.check(
            "proposing does not run the command",
            not hands.has(marker),
        )

        turn = client.prompt("/run 1")
        report.quote("/run 1", scrub(turn.message))
        report.check(
            "/run 1 hands the command to the agent",
            "Asked the agent to run #1" in turn.message,
        )
        report.check(
            "the agent ran it through the terminal tool on the laptop",
            wait_for_file(hands, marker, RUN_WAIT_SECONDS) == "proposed-ran\n",
        )

        turn = client.prompt("/run 9")
        report.check(
            "/run with an unknown number says what exists",
            "no proposed command #9" in turn.message,
            scrub(turn.message.strip()[-120:]),
        )
    finally:
        client.close()


def terminal_checks(
    report: Report,
    profile: Profile,
    hands,
    flag: str,
) -> None:
    name = flag.lstrip("-")
    screen = profile.terminal(LAPTOP_SESSION_DIR, flag, f"{name}-hands")

    try:
        start = screen.mark()
        screen.enter("script:where")
        screen.expect("Done.", TERMINAL_TURN_SECONDS, since=start)

        start = screen.mark()
        screen.enter("/hands")
        seen = screen.expect("Targets:", TERMINAL_TURN_SECONDS, since=start)

        report.check(
            f"/hands works in the {name} client",
            f"laptop({hands.user}@{hands.ssh_host})" in term.squash(seen),
        )
    except term.TermError as error:
        report.check(f"/hands works in the {name} client", False, str(error))
    finally:
        screen.close()

    hands.exec(f"rm -rf {hands.sample_dir}/{VICTIM}")
    screen = profile.terminal(LAPTOP_SESSION_DIR, flag, f"{name}-approval")

    try:
        start = screen.mark()
        screen.enter("script:danger")
        seen = screen.expect(
            APPROVAL_TITLES[flag],
            TERMINAL_TURN_SECONDS,
            since=start,
        )
        screen.settle(1.0)
        seen = screen.text[start:]

        report.check(
            f"Hermes' own approval prompt appears in the {name} client",
            "Deny" in seen
            and term.squash(f"rm -rf {VICTIM}") in term.squash(seen),
        )

        screen.type(APPROVAL_DENY_KEY)
        screen.settle(0.5)
        screen.type("\r")
        screen.expect("Done.", TERMINAL_TURN_SECONDS, since=start)

        kept = hands.exec(f"ls {hands.sample_dir}/{VICTIM}").strip()

        report.check(
            f"denying it in the {name} client stops the command",
            kept == "keep",
        )
    except (term.TermError, RuntimeError) as error:
        report.check(
            f"the approval prompt works in the {name} client",
            False,
            str(error)[-600:],
        )
    finally:
        screen.close()


def phase_three_terminals(report: Report, profile: Profile, hands) -> None:
    report.section("Phase 3.5: the classic CLI on a real terminal")
    terminal_checks(report, profile, hands, "--cli")

    report.section("Phase 3.6: the TUI on a real terminal")
    terminal_checks(report, profile, hands, "--tui")


def phase_three(report: Report, profile: Profile, hands) -> None:
    phase_three_cli(report, profile, hands)

    # Stopping the container above opened the breaker.
    time.sleep(RECOVERY_WAIT_SECONDS)

    phase_three_slash(report, profile, hands)
    phase_three_terminals(report, profile, hands)


def post_json(route: str, body: dict, token: str = "") -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{PLUGIN_API}{route}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    if token:
        request.add_header(TOKEN_HEADER, token)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, {}


def start_server(profile: Profile, token: str) -> subprocess.Popen:
    """Start the server Hermes Desktop would start for this profile.

    It is a process of its own, scoped to the test profile, and it is
    stopped by its own pid. The shared gateway is left alone.
    """

    log = open(LOG_DIR / "serve.log", "w")

    process = subprocess.Popen(
        profile.command(
            "serve", "--isolated", "--skip-build",
            "--host", "127.0.0.1",
            "--port", str(SERVE_PORT),
        ),
        env={**os.environ, TOKEN_ENV: token},
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        cwd=str(RIG_DIR),
    )

    wait_for_port(SERVE_PORT, SERVE_START_SECONDS)

    return process


def phase_four_api(report: Report, profile: Profile, hands) -> None:
    report.section("Phase 4.1: the status routes on a real Hermes server")
    token = secrets.token_hex(16)
    server = start_server(profile, token)

    try:
        code, _ = post_json("/status", {})
        report.check(
            "a request with no token is refused",
            code == 401,
            f"HTTP {code}",
        )

        body = {"cwd": str(LAPTOP_SESSION_DIR)}
        code, status = post_json("/status", body, token)
        report.quote("POST /status", scrub(json.dumps(status, indent=2)))

        report.check(
            "the route answers for a laptop folder",
            code == 200
            and status.get("target") == "laptop"
            and status.get("connected") is True,
        )
        report.check(
            "it lists every target with its live state",
            [row["name"] for row in status.get("rows", [])]
            == ["laptop", "mac"],
        )

        hands.stop()
        code, status = post_json("/status", body, token)
        hands.resume()

        report.check(
            "a stopped target is reported from a live probe",
            status.get("connected") is False and bool(status.get("detail")),
            f"detail: {status.get('detail')}",
        )

        code, listed = post_json("/proposals", {"session_ids": ["x"]}, token)
        report.check(
            "the proposals route answers",
            code == 200 and listed.get("items") == [],
        )
    finally:
        server.terminate()

        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()


def phase_four_desktop(report: Report, profile: Profile, hands) -> None:
    report.section("Phase 4.2: desktop/plugin.js with real React")
    report.write(
        "  The plugin file is loaded as the desktop app loads it, with"
    )
    report.write(
        "  real React and a real DOM. The app itself and its SDK are"
    )
    report.write(
        "  stand-ins. Gate 3 is still a look at the real app."
    )

    done = run(
        ["node", "--test", str(REPO / "tests" / "desktop.test.mjs")],
        cwd=str(REPO),
        timeout=300,
    )
    report.quote("node --test tests/desktop.test.mjs", scrub(done.stdout))

    report.check(
        "every desktop test passes",
        done.returncode == 0 and "# fail 0" in done.stdout.replace("ℹ", "#"),
    )
    report.check(
        "none was skipped for lack of React",
        "skipped 0" in done.stdout,
    )


def phase_four(report: Report, profile: Profile, hands) -> None:
    phase_four_api(report, profile, hands)
    phase_four_desktop(report, profile, hands)


def phase_sleep(report: Report, profile: Profile, hands) -> None:
    report.section("Sleep: a lock is held while a command runs")

    if not hands.has_systemd:
        report.write(f"  skipped: {hands.label} has no systemd.")
        return

    client = profile.session(LAPTOP_SESSION_DIR, "sleep")

    try:
        turn = client.prompt("script:inhibit")
        show_turn(report, turn)

        held = [
            line for line in turn.tool_text().splitlines()
            if INHIBIT_WHO in line
        ]
        row = held[0] if held else ""

        report.check(
            "the running command holds a hermes-hands sleep lock",
            bool(held),
        )
        report.check(
            "the lock is one the target allows a remote login to take",
            "delay" in row or "block" in row,
            "mode: " + ("block" if "block" in row else "delay"),
        )
    finally:
        client.close()

    left = hands.exec(INHIBIT_COUNT_COMMAND).strip()

    report.check(
        "no lock is left once the command ends",
        left == "0",
        f"locks listed afterward: {left}",
    )


PHASES = {
    "1": ("Phase 1: backend and routing", phase_one),
    "2": ("Phase 2: resilience", phase_two_all),
    "3": ("Phase 3: CLI surface", phase_three),
    "4": ("Phase 4: desktop surface", phase_four),
    "sleep": ("Sleep inhibitor on a systemd target", phase_sleep),
}


def save_results(prefix: str, name: str, report: Report, start: int) -> None:
    path = RESULTS_DIR / f"{prefix}phase-{name}.txt"
    path.write_text("\n".join(report.lines[start:]).strip() + "\n")


def run_phases(
    names: list[str],
    report: Report,
    profile: Profile,
    hands,
) -> None:
    for name in names:
        title, function = PHASES[name]
        start = len(report.lines)

        report.write()
        report.write(f"# {title}")

        try:
            function(report, profile, hands)
        except Exception as error:
            report.check("the phase ran to its end", False, repr(error))

        save_results(hands.results_prefix, name, report, start)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--apply",
        action="store_true",
        help="run for real. Without it, only the plan is printed.",
    )
    parser.add_argument(
        "--phase",
        action="append",
        choices=sorted(PHASES),
        help="a phase to run. Repeat for more. Default: all.",
    )
    parser.add_argument(
        "--real",
        metavar="USER@HOST",
        help="use a real machine as the hands, not the container. It "
             "must take your SSH key with no prompt. The rig writes "
             "only under ~/.cache/hermes-hands-rig there.",
    )
    parser.add_argument("--hermes", default=DEFAULT_HERMES)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument(
        "--profile-home",
        type=Path,
        help="the profile's folder. Default: $HERMES_HOME/profiles/<profile>.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the container and stub running afterward. "
             "Not with --real.",
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="build the rig, leave it up, and run no tests. For "
             "trying things by hand. Undo with --teardown-only. "
             "Not with --real.",
    )
    parser.add_argument(
        "--teardown-only",
        action="store_true",
        help="put the profile's config back and remove the rig.",
    )

    return parser.parse_args()


def find_profile_home(args: argparse.Namespace) -> Path:
    if args.profile_home is not None:
        return args.profile_home

    root = os.environ.get("HERMES_HOME")

    if not root:
        raise SystemExit(
            "Set --profile-home, or HERMES_HOME so it can be worked out."
        )

    return Path(root) / "profiles" / args.profile


def pick_hands(args: argparse.Namespace):
    if not args.real:
        return ContainerHands(REPO / "rig" / "hands", KEYS_DIR)

    # The relay that fakes an outage lives in this process. It cannot
    # be left running once the script ends.
    if args.keep or args.setup_only:
        raise SystemExit("--keep and --setup-only do not work with --real.")

    return RealHands(args.real, KEYS_DIR)


def print_plan(home: Path, hands) -> None:
    print(f"{MODE_DRY} test profile: {home}")
    print(f"{MODE_DRY} hands: {hands.label}")

    for step in (*hands.plan(), *PLAN):
        print(f"{MODE_DRY} would {step}")

    print(f"{MODE_DRY} nothing was changed. Pass --apply to run.")


def stop_stub() -> None:
    pid_file = RIG_DIR / STUB_PID_FILE

    if not pid_file.exists():
        return

    try:
        os.kill(int(pid_file.read_text()), signal.SIGTERM)
    except (OSError, ValueError):
        pass

    pid_file.unlink()


def teardown(profile: Profile, hands) -> None:
    profile.uninstall()
    stop_stub()
    hands.remove()


def main() -> int:
    args = parse_args()
    home = find_profile_home(args)
    hands = pick_hands(args)

    if not args.apply:
        print_plan(home, hands)
        return 0

    if not (home / "config.yaml").is_file():
        raise SystemExit(f"{home} has no config.yaml. Is it a profile?")

    print(f"{MODE_LIVE} test profile: {home}")
    print(f"{MODE_LIVE} hands: {hands.label}")

    profile = Profile(args.hermes, args.profile, home)
    report = Report()
    stub = None

    if args.teardown_only:
        teardown(profile, hands)
        print(f"{MODE_LIVE} the rig is down and the config is back.")
        return 0

    make_dirs()

    if args.setup_only:
        hands.start()
        stub = start_stub()
        (RIG_DIR / STUB_PID_FILE).write_text(str(stub.pid))
        profile.install(render_config(hands))
        print(f"{MODE_LIVE} the rig is up. Undo with --teardown-only.")
        return 0

    try:
        hands.start()
        SCRUB_PAIRS.extend(hands.scrub_pairs())
        stub = start_stub()
        profile.install(render_config(hands))

        run_phases(args.phase or sorted(PHASES), report, profile, hands)
    finally:
        profile.uninstall()

        if stub is not None and not args.keep:
            stub.terminate()

        if not args.keep:
            hands.remove()

    print()
    print(f"{MODE_LIVE} failures: {report.failures}")
    print(f"{MODE_LIVE} results saved under rig/results/")

    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())

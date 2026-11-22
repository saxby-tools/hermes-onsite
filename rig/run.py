"""Run the hermes-hands acceptance tests end to end.

The rig is three parts:

- a `hands` container with sshd, playing the user's laptop,
- a stub model that replays scripted tool calls,
- a Hermes test profile, driven over the Agent Client Protocol.

This script changes state: it builds a container, and it swaps the
test profile's config for the length of the run. So it defaults to a
dry run. Pass `--apply` to run for real. The profile's own config is
put back at the end, byte for byte, even when a test fails.

Standard library only.
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acp  # noqa: E402  (the path line above has to come first)
import term  # noqa: E402


REPO = Path(__file__).resolve().parent.parent
RIG_DIR = REPO / ".rig"
KEYS_DIR = RIG_DIR / "keys"
WORK_DIR = RIG_DIR / "work"
LAPTOP_SESSION_DIR = WORK_DIR / "sample"
MAC_SESSION_DIR = RIG_DIR / "mac-work"
LOG_DIR = RIG_DIR / "logs"
RESULTS_DIR = REPO / "rig" / "results"

PLUGIN_ID = "hermes-hands"
IMAGE = "hermes-hands-rig/hands:dev"
CONTAINER = "hermes-hands-rig-hands"
HANDS_USER = "dev"
HANDS_SAMPLE_DIR = "/home/dev/Development/sample"
HANDS_SSH_PORT = 2222
STUB_PORT = 11499

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
            host: 127.0.0.1
            port: {ssh_port}
            user: {user}
            key: {key}
            known_hosts: {known_hosts}
            prefixes:
              - /home/{user}/
            path_map:
              {work}: /home/{user}/Development
          mac:
            kind: local
"""

PLAN = (
    "build the hands container image",
    "start the hands container with a fresh key pair",
    "start the stub model",
    "back up the test profile's config.yaml",
    "write a rig config.yaml that points at the stub and the container",
    "link this checkout into the profile's plugins folder",
    "ask the running gateway to load the plugin",
    "run the acceptance tests and save their output",
    "unload the plugin and put the profile's config.yaml back",
    "stop the stub model and remove the container",
)


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

    return "\n".join(lines)


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        **kwargs,
    )


def must(command: list[str], **kwargs) -> str:
    done = run(command, **kwargs)

    if done.returncode != 0:
        joined = " ".join(command)

        raise RuntimeError(f"`{joined}` failed:\n{done.stdout}{done.stderr}")

    return done.stdout


def docker(*args: str) -> str:
    return must(["docker", *args])


def hands_exec(command: str) -> str:
    return docker("exec", "--user", HANDS_USER, CONTAINER, "sh", "-c", command)


def make_dirs() -> None:
    for folder in (KEYS_DIR, LAPTOP_SESSION_DIR, MAC_SESSION_DIR, LOG_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def make_keys() -> str:
    key = KEYS_DIR / "id"

    # A fresh pair and an empty known_hosts for each run. The
    # container gets new host keys each time it is created.
    for stale in (key, KEYS_DIR / "id.pub", KEYS_DIR / "known_hosts"):
        stale.unlink(missing_ok=True)

    must([
        "ssh-keygen", "-q", "-t", "ed25519", "-N", "",
        "-C", "hermes-hands-rig", "-f", str(key),
    ])

    return (KEYS_DIR / "id.pub").read_text().strip()


def wait_for_port(port: int, seconds: float) -> None:
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)

    raise RuntimeError(f"nothing is listening on port {port}")


def start_container(public_key: str) -> None:
    docker("build", "--quiet", "--tag", IMAGE, str(REPO / "rig" / "hands"))

    run(["docker", "rm", "--force", CONTAINER])

    docker(
        "run", "--detach",
        "--name", CONTAINER,
        "--publish", f"127.0.0.1:{HANDS_SSH_PORT}:22",
        "--env", f"HANDS_AUTHORIZED_KEY={public_key}",
        IMAGE,
    )

    wait_for_port(HANDS_SSH_PORT, 20)

    # sshd accepts the port a moment before the home is seeded.
    time.sleep(1)


def stop_container() -> None:
    docker("stop", "--time", "1", CONTAINER)


def resume_container() -> None:
    docker("start", CONTAINER)
    wait_for_port(HANDS_SSH_PORT, 20)


def freeze_container() -> None:
    # A paused container keeps its port open and answers nothing,
    # which is how a sleeping laptop looks from the network.
    docker("pause", CONTAINER)


def thaw_container() -> None:
    docker("unpause", CONTAINER)


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


def render_config() -> str:
    return CONFIG_TEMPLATE.format(
        stub_port=STUB_PORT,
        plugin=PLUGIN_ID,
        ssh_port=HANDS_SSH_PORT,
        user=HANDS_USER,
        key=KEYS_DIR / "id",
        known_hosts=KEYS_DIR / "known_hosts",
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

    def install(self) -> None:
        # A backup left by a crashed run is the real config. It is
        # put back before anything else is saved over it.
        self.restore()

        shutil.copy2(self.config, self.backup)
        self.config.write_text(render_config())

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
        report.write(f"  --- approval prompt: {permission['title']}")
        report.write(f"  | options: {options}")
        report.write(f"  | answered: {permission['answer']}")


def phase_one(report: Report, profile: Profile) -> None:
    container_host = hands_exec("hostname").strip()

    report.section("Phase 1.1: a session in a laptop folder runs there")
    client = profile.session(LAPTOP_SESSION_DIR, "laptop")

    try:
        turn = client.prompt("script:where")
        show_turn(report, turn)
        text = turn.tool_text()

        report.check("pwd is the laptop path", HANDS_SAMPLE_DIR in text)
        report.check("whoami is the laptop user", f"\n{HANDS_USER}\n" in text)
        report.check("hostname is the laptop", container_host in text)

        report.section("Phase 1.2: PATH from the interactive shell")
        turn = client.prompt("script:path")
        show_turn(report, turn)
        report.check(
            "a tool that only .zshrc adds to PATH is found",
            "handstool ok" in turn.tool_text(),
        )

        report.section("Phase 1.3: file tools act on the laptop's files")
        turn = client.prompt("script:files")
        show_turn(report, turn)

        finished = [tool.status for tool in turn.tools]
        note = f"{HANDS_SAMPLE_DIR}/rig-note.txt"
        on_host = LAPTOP_SESSION_DIR / "rig-note.txt"

        report.check(
            "write, read, search and cat all completed",
            finished == ["completed"] * 4,
            f"statuses: {finished}",
        )
        report.check(
            "the file was written in the session folder on the laptop",
            hands_exec(f"cat {note}") == "alpha\nbravo\n",
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
            hands_exec(f"cat {note}") == "alpha\ncharlie\n",
            f"content: {hands_exec(f'cat {note}')!r}",
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
        report.check("hostname is not the laptop", container_host not in text)
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
        report.check(
            "the folder is still on the laptop",
            hands_exec(f"ls {HANDS_SAMPLE_DIR}/victim").strip() == "keep",
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


def phase_two(report: Report, profile: Profile) -> None:
    report.section("Phase 2.1: the target stops under a live session")
    client = profile.session(LAPTOP_SESSION_DIR, "resilience")

    try:
        turn = client.prompt("script:where")
        report.check(
            "the session works before the outage",
            HANDS_SAMPLE_DIR in turn.tool_text(),
        )

        stop_container()

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

        resume_container()
        report.write(f"  waiting {RECOVERY_WAIT_SECONDS}s for the cooldown")
        time.sleep(RECOVERY_WAIT_SECONDS)

        turn = client.prompt("script:where")
        show_turn(report, turn)

        report.check(
            "service resumes with no restart once the target is back",
            HANDS_SAMPLE_DIR in turn.tool_text(),
        )
    finally:
        client.close()

    report.section("Phase 2.2: a new session while the target is down")
    stop_container()
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
            getpass.getuser() not in first.text,
        )
    finally:
        client.close()
        resume_container()


def phase_two_frozen(report: Report, profile: Profile) -> None:
    report.section("Phase 2.3: the target freezes, as a sleeping laptop")
    client = profile.session(LAPTOP_SESSION_DIR, "frozen")

    try:
        turn = client.prompt("script:where")
        report.check(
            "the session works before the freeze",
            HANDS_SAMPLE_DIR in turn.tool_text(),
        )

        freeze_container()
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

        thaw_container()
        report.write(f"  waiting {RECOVERY_WAIT_SECONDS}s for the cooldown")
        time.sleep(RECOVERY_WAIT_SECONDS)

        turn = client.prompt("script:where")
        show_turn(report, turn)

        report.check(
            "service resumes with no restart once the target wakes",
            HANDS_SAMPLE_DIR in turn.tool_text(),
        )
    finally:
        client.close()
        run(["docker", "unpause", CONTAINER])


def phase_two_mid_flight(report: Report, profile: Profile) -> None:
    report.section("Phase 2.4: the target freezes just before a call")
    client = profile.session(LAPTOP_SESSION_DIR, "mid-flight")

    try:
        turn = client.prompt("script:where")
        report.check(
            "the session works before the freeze",
            HANDS_SAMPLE_DIR in turn.tool_text(),
        )

        freeze_container()

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
        run(["docker", "unpause", CONTAINER])


def phase_two_all(report: Report, profile: Profile) -> None:
    phase_two(report, profile)

    # The breaker belongs to the target, not to a session. The outage
    # above left it open, so the next test waits out the cooldown.
    time.sleep(RECOVERY_WAIT_SECONDS)

    phase_two_frozen(report, profile)

    time.sleep(RECOVERY_WAIT_SECONDS)

    phase_two_mid_flight(report, profile)


def init_flags() -> list[str]:
    """Flags that describe the rig's own targets, so applying them
    leaves the rig working."""

    return [
        "--ssh", f"laptop={HANDS_USER}@127.0.0.1:{HANDS_SSH_PORT}",
        "--local", "mac",
        "--prefix", f"laptop=/home/{HANDS_USER}/",
        "--map", f"laptop={WORK_DIR}=/home/{HANDS_USER}/Development",
        "--key", f"laptop={KEYS_DIR / 'id'}",
        "--known-hosts", f"laptop={KEYS_DIR / 'known_hosts'}",
    ]


def phase_three_cli(report: Report, profile: Profile) -> None:
    report.section("Phase 3.1: hermes hands status")
    done = profile.cli(LAPTOP_SESSION_DIR, "hands", "status")
    report.quote("in a laptop folder", scrub(done.stdout))

    report.check(
        "a laptop folder shows the laptop target and that it is up",
        "Target    laptop" in done.stdout and "connected" in done.stdout,
    )
    report.check(
        "it shows the folder the session would start in",
        f"Start in  {HANDS_SAMPLE_DIR}" in done.stdout,
    )
    report.check("it exits 0 when the target is up", done.returncode == 0)

    done = profile.cli(MAC_SESSION_DIR, "hands", "status")

    report.check(
        "a Mac folder shows the local target",
        "Target    mac" in done.stdout,
    )

    stop_container()
    done = profile.cli(LAPTOP_SESSION_DIR, "hands", "status")
    resume_container()
    report.quote("with the laptop stopped", scrub(done.stdout))

    report.check(
        "a stopped target is shown as not reachable",
        "NOT REACHABLE" in done.stdout and "DOWN" in done.stdout,
    )
    report.check("it exits 1 when the target is down", done.returncode == 1)

    report.section("Phase 3.2: hermes hands init")
    before = profile.config.read_bytes()
    done = profile.cli(RIG_DIR, "hands", "init", *init_flags())
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

    done = profile.cli(RIG_DIR, "hands", "init", *init_flags(), "--apply")

    report.check(
        "with --apply it writes and says so",
        "[LIVE] written." in done.stdout,
    )

    done = profile.cli(LAPTOP_SESSION_DIR, "hands", "status")

    report.check(
        "the written settings load and route as before",
        "Target    laptop" in done.stdout and done.returncode == 0,
    )


def wait_for_file(path: str, seconds: float) -> str:
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        done = run([
            "docker", "exec", "--user", HANDS_USER, CONTAINER,
            "cat", path,
        ])

        if done.returncode == 0:
            return done.stdout

        time.sleep(0.5)

    return ""


def phase_three_slash(report: Report, profile: Profile) -> None:
    report.section("Phase 3.3: /hands shows and switches the target")
    client = profile.session(LAPTOP_SESSION_DIR, "slash")

    try:
        client.prompt("script:where")
        turn = client.prompt("/hands")
        report.quote("/hands", scrub(turn.message))

        report.check(
            "/hands names the target, its state and the folder",
            "Hands:   laptop" in turn.message
            and "connected" in turn.message
            and HANDS_SAMPLE_DIR in turn.message,
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
            HANDS_SAMPLE_DIR in turn.tool_text(),
        )

        turn = client.prompt("/hands nowhere")
        report.check(
            "an unknown target is refused with the choices",
            "is not a target" in turn.message and "laptop, mac" in turn.message,
            scrub(turn.message.strip()),
        )

        report.section("Phase 3.4: propose_command and /run")
        marker = f"{HANDS_SAMPLE_DIR}/proposed.txt"
        hands_exec(f"rm -f {marker}")

        turn = client.prompt("script:propose")
        show_turn(report, turn)

        report.check(
            "propose_command prints a numbered block",
            "Proposed command #1" in turn.tool_text()
            and "Run it with /run 1" in turn.tool_text(),
        )
        report.check(
            "proposing does not run the command",
            run([
                "docker", "exec", CONTAINER, "test", "-e", marker,
            ]).returncode != 0,
        )

        turn = client.prompt("/run 1")
        report.quote("/run 1", scrub(turn.message))
        report.check(
            "/run 1 hands the command to the agent",
            "Asked the agent to run #1" in turn.message,
        )
        report.check(
            "the agent ran it through the terminal tool on the laptop",
            wait_for_file(marker, RUN_WAIT_SECONDS) == "proposed-ran\n",
        )

        turn = client.prompt("/run 9")
        report.check(
            "/run with an unknown number says what exists",
            "no proposed command #9" in turn.message,
            scrub(turn.message.strip()[-120:]),
        )
    finally:
        client.close()


def terminal_checks(report: Report, profile: Profile, flag: str) -> None:
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
            "laptop(dev@127.0.0.1)" in term.squash(seen),
        )
    except term.TermError as error:
        report.check(f"/hands works in the {name} client", False, str(error))
    finally:
        screen.close()

    hands_exec(f"rm -rf {HANDS_SAMPLE_DIR}/victim")
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
            "Deny" in seen and "rm -rf victim" in seen,
        )

        screen.type(APPROVAL_DENY_KEY)
        screen.settle(0.5)
        screen.type("\r")
        screen.expect("Done.", TERMINAL_TURN_SECONDS, since=start)

        report.check(
            f"denying it in the {name} client stops the command",
            hands_exec(f"ls {HANDS_SAMPLE_DIR}/victim").strip() == "keep",
        )
    except (term.TermError, RuntimeError) as error:
        report.check(
            f"the approval prompt works in the {name} client",
            False,
            str(error)[-600:],
        )
    finally:
        screen.close()


def phase_three_terminals(report: Report, profile: Profile) -> None:
    report.section("Phase 3.5: the classic CLI on a real terminal")
    terminal_checks(report, profile, "--cli")

    report.section("Phase 3.6: the TUI on a real terminal")
    terminal_checks(report, profile, "--tui")


def phase_three(report: Report, profile: Profile) -> None:
    phase_three_cli(report, profile)

    # Stopping the container above opened the breaker.
    time.sleep(RECOVERY_WAIT_SECONDS)

    phase_three_slash(report, profile)
    phase_three_terminals(report, profile)


PHASES = {
    "1": ("Phase 1: backend and routing", phase_one),
    "2": ("Phase 2: resilience", phase_two_all),
    "3": ("Phase 3: CLI surface", phase_three),
}


def save_results(name: str, report: Report, start: int) -> None:
    path = RESULTS_DIR / f"phase-{name}.txt"
    path.write_text("\n".join(report.lines[start:]).strip() + "\n")


def run_phases(names: list[str], report: Report, profile: Profile) -> None:
    for name in names:
        title, function = PHASES[name]
        start = len(report.lines)

        report.write()
        report.write(f"# {title}")

        try:
            function(report, profile)
        except Exception as error:
            report.check("the phase ran to its end", False, repr(error))

        save_results(name, report, start)


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
        help="leave the container and stub running afterward.",
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="build the rig, leave it up, and run no tests. For "
             "trying things by hand. Undo with --teardown-only.",
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


def print_plan(home: Path) -> None:
    print(f"{MODE_DRY} test profile: {home}")

    for step in PLAN:
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


def teardown(profile: Profile) -> None:
    profile.uninstall()
    stop_stub()
    run(["docker", "rm", "--force", CONTAINER])


def main() -> int:
    args = parse_args()
    home = find_profile_home(args)

    if not args.apply:
        print_plan(home)
        return 0

    if not (home / "config.yaml").is_file():
        raise SystemExit(f"{home} has no config.yaml. Is it a profile?")

    print(f"{MODE_LIVE} test profile: {home}")

    profile = Profile(args.hermes, args.profile, home)
    report = Report()
    stub = None

    if args.teardown_only:
        teardown(profile)
        print(f"{MODE_LIVE} the rig is down and the config is back.")
        return 0

    make_dirs()

    if args.setup_only:
        start_container(make_keys())
        stub = start_stub()
        (RIG_DIR / STUB_PID_FILE).write_text(str(stub.pid))
        profile.install()
        print(f"{MODE_LIVE} the rig is up. Undo with --teardown-only.")
        return 0

    try:
        start_container(make_keys())
        stub = start_stub()
        profile.install()

        run_phases(args.phase or sorted(PHASES), report, profile)
    finally:
        profile.uninstall()

        if stub is not None and not args.keep:
            stub.terminate()

        if not args.keep:
            run(["docker", "rm", "--force", CONTAINER])

    print()
    print(f"{MODE_LIVE} failures: {report.failures}")
    print(f"{MODE_LIVE} results saved under rig/results/")

    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())

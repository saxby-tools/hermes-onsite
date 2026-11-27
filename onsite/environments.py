"""Environments: a sandbox the server makes for one session.

An environment is the third kind of target. The other two are
machines that are already there. This one is made when it is asked
for: a container on the server, with one folder of the server's disk
shared into it.

That folder is the session's folder. It is on the server's own disk,
so the desktop app's folder picker and file tree show it with no
mount at all. It has the same path inside the container, so no path
is ever translated.

What is where on the server:

    <root>/<id>/           one session's folder, shared into its
                           sandbox at this same path
    <root>/<id>/<name>/    the clone, when the settings name a
                           repository. The session opens here.

The clone is made on the server, before the sandbox exists, by the
`git` program with whatever logins the server's account already has.
No login is put in the sandbox, and none is read or kept here.

Nothing else is shared with the sandbox: no part of the Hermes home,
no skill, no key. The agent itself stays on the server. Only its
commands and file edits run in the sandbox.

Not released. This module imports nothing from Hermes. It runs the
`docker` and `git` programs through `run` alone, so it can be tested
with neither installed.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import time

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import mounts
from .config import PLUGIN_ID
from .config import TargetSpec


DOCKER = "docker"
GIT = "git"

# Where Docker Desktop puts its program on a Mac. A service often
# runs with a PATH that has none of these in it.
DOCKER_PLACES = (
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
)

ENVS_DIR = "env"

# How a sandbox is told apart from every other container.
LABEL_ENVIRONMENT = "onsite-environment"
LABEL_SESSION = "onsite-session"
LABEL_PROFILE = "onsite-profile"
CONTAINER_PREFIX = "onsite-env"

# A session's id is the time it was made and four random characters.
# The time puts the folders in order for a person who looks.
ID_TIME_FORMAT = "%Y%m%d-%H%M%S"
ID_TOKEN_BYTES = 2
ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")

UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
REPOSITORY_SUFFIX = ".git"
DEFAULT_REPO_NAME = "repo"

# The container program reads a colon as the end of a folder's name.
VOLUME_SEPARATOR = ":"

# Every capability is dropped but these. A package manager needs them
# to set a file's owner and to step down to its own account. Hermes'
# own Docker backend keeps the same five.
KEPT_CAPABILITIES = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID")

# Enough for a browser, and far below what would hurt the server.
PIDS_LIMIT = "2048"

NETWORK_NONE = "none"

# What the container runs while it waits for commands.
IDLE_COMMAND = ("sleep", "infinity")

# Written in the container's own disk when the setup script is done.
# A container that replaces this one has no such file, so the script
# runs again there.
SETUP_MARKER = "/var/lib/onsite-setup-done"

# Where git reads the settings that hold for a whole machine: here,
# for a whole sandbox.
GIT_SYSTEM_CONFIG = "/etc/gitconfig"

# All a container needs to delete files that are not its own.
SWEEP_CAPABILITY = "DAC_OVERRIDE"

STATE_RUNNING = "running"
FALLBACK_HOME = "/root"

PROBE_SECONDS = 5
QUICK_SECONDS = 30
CLONE_SECONDS = 600
# A first run may have to fetch the image.
RUN_SECONDS = 300
SETUP_SECONDS = 900

PRIVATE_DIR_MODE = 0o700

Run = Callable[..., subprocess.CompletedProcess]


class SandboxError(RuntimeError):
    """An environment could not do what was asked. The text says why
    and what to change."""


@dataclass(frozen=True)
class Session:
    """One session's folder on the server."""

    id: str
    folder: Path
    start: Path


@dataclass(frozen=True)
class Sandbox:
    """A session's container, ready for commands."""

    container: str
    home: str
    made: bool


def run_program(
    command: list[str],
    timeout: int,
    stdin_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """One program, waited for. Nothing is ever asked of a person."""

    if stdin_text is not None:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            input=stdin_text,
            env=env,
            check=False,
        )

    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        env=env,
        check=False,
    )


def call(
    run: Run,
    what: str,
    command: list[str],
    timeout: int,
    **options,
) -> subprocess.CompletedProcess:
    """`run`, with the two ways it can fail to start said plainly."""

    try:
        return run(command, timeout, **options)
    except subprocess.TimeoutExpired:
        raise SandboxError(
            f"{what} did not finish in {timeout} seconds."
        ) from None
    except OSError as error:
        raise SandboxError(
            f"{what} could not start: `{command[0]}` cannot be run "
            f"({error.strerror or error})."
        ) from None


def last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if not lines:
        return ""

    return lines[-1]


def said(done: subprocess.CompletedProcess) -> str:
    """What a program that failed had to say for itself."""

    detail = last_line(done.stderr or "") or last_line(done.stdout or "")

    return detail or f"it exited {done.returncode}"


def find_docker(
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
) -> str:
    """The container program, or nothing when the server has none."""

    found = which(DOCKER)

    if found:
        return found

    for place in DOCKER_PLACES:
        if exists(place):
            return place

    return ""


def need_docker() -> str:
    docker = find_docker()

    if docker:
        return docker

    raise SandboxError(
        "An environment needs Docker on the Hermes server, and no "
        "`docker` program was found there."
    )


def probe(
    run: Run = run_program,
    docker: str | None = None,
) -> tuple[bool, str, int]:
    """Whether Docker answers: (yes or no, why not, milliseconds)."""

    started = time.monotonic()
    program = find_docker() if docker is None else docker

    if not program:
        return False, "no `docker` program on the Hermes server", 0

    command = [program, "version", "--format", "{{.Server.Version}}"]

    try:
        done = call(run, "Docker", command, PROBE_SECONDS)
    except SandboxError as error:
        elapsed = int((time.monotonic() - started) * 1000)

        return False, str(error), elapsed

    elapsed = int((time.monotonic() - started) * 1000)

    if done.returncode != 0:
        return False, f"Docker is not answering: {said(done)}", elapsed

    return True, "", elapsed


def default_root(
    data_dir: Path,
    name: str,
    home: Path | None = None,
) -> Path:
    """Where an environment's sessions are kept when no place is set.

    Beside the mounts, in the standard folder for a program's own
    data. This only builds the path. `open_session` is what refuses
    it when the home folder is inside the Hermes home.
    """

    if home is None:
        home = Path.home()

    share = home.joinpath(*mounts.SHARE_PARTS)
    profile = mounts.profile_name(data_dir)

    return share / PLUGIN_ID / ENVS_DIR / profile / name


def root_of(spec: TargetSpec) -> Path:
    return Path(spec.workspace)


def inside_hermes(spec: TargetSpec, root: Path, hermes: Path) -> SandboxError:
    return SandboxError(
        f"{spec.label} cannot keep its sessions in {root}: that is "
        f"inside the Hermes home, {hermes}. Hermes reads the folders "
        "above a session's folder for a repository, and deleting a "
        "profile would delete every session's work. Set this "
        f"environment's `workspace` to a folder outside {hermes}."
    )


def new_id(now: float | None = None, token: str | None = None) -> str:
    if now is None:
        now = time.time()

    if token is None:
        token = secrets.token_hex(ID_TOKEN_BYTES)

    stamp = time.strftime(ID_TIME_FORMAT, time.localtime(now))

    return f"{stamp}-{token}"


def repo_name(repository: str) -> str:
    """The folder a repository is cloned into: its own last name."""

    last = repository.rstrip("/").replace(":", "/").rsplit("/", 1)[-1]

    if last.endswith(REPOSITORY_SUFFIX):
        last = last[:-len(REPOSITORY_SUFFIX)]

    name = UNSAFE_NAME_RE.sub("-", last).strip(".-")

    return name or DEFAULT_REPO_NAME


def start_folder(spec: TargetSpec, folder: Path) -> Path:
    """Where a session opens: in the clone, or in its empty folder."""

    if not spec.repository:
        return folder

    return folder / repo_name(spec.repository)


def session_of(spec: TargetSpec, path: str) -> Session | None:
    """The session a folder on the server belongs to, if any.

    Only the path's own words are read. Nothing is looked up on disk,
    so a folder that is gone still names its session.
    """

    root = root_of(spec)

    try:
        rest = Path(path).relative_to(root)
    except ValueError:
        return None

    if not rest.parts:
        return None

    name = rest.parts[0]

    if ID_RE.match(name) is None:
        return None

    folder = root / name

    return Session(name, folder, start_folder(spec, folder))


def list_sessions(spec: TargetSpec) -> list[Session]:
    """Every session folder of an environment, oldest first."""

    root = root_of(spec)

    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []

    found = [session_of(spec, str(root / name)) for name in names]

    return [session for session in found if session is not None]


def clone_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """The surroundings `git` clones in. It may use any login the
    server's account has. It may not stop to ask for one: nobody is
    there to answer, and the click would hang."""

    env = dict(os.environ if base is None else base)

    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")

    return env


def build_clone_command(repository: str, folder: Path) -> list[str]:
    return [
        GIT,
        "clone",
        "--quiet",
        # A clone of a folder on the server would share its files
        # with the original. The sandbox could then write into the
        # original through them.
        "--no-hardlinks",
        # Nothing after this is read as an option.
        "--",
        repository,
        str(folder),
    ]


def prepare_root(root: Path) -> None:
    """Make the folder that holds an environment's sessions.

    It is closed to other accounts when it is made here. One that was
    already there is never changed.
    """

    try:
        if not root.exists():
            root.mkdir(parents=True)
            os.chmod(root, PRIVATE_DIR_MODE)
    except OSError as error:
        raise SandboxError(
            f"{root} cannot be made on this server: "
            f"{error.strerror or error}"
        ) from None


def clone_into(spec: TargetSpec, session: Session, run: Run) -> None:
    if not spec.repository:
        return

    done = call(
        run,
        f"The clone of {spec.repository}",
        build_clone_command(spec.repository, session.start),
        CLONE_SECONDS,
        env=clone_env(),
    )

    if done.returncode == 0:
        return

    raise SandboxError(
        f"{spec.repository} could not be cloned on the Hermes server: "
        f"{said(done)}. The clone uses the logins of the account "
        "Hermes runs as."
    )


def open_session(
    spec: TargetSpec,
    data_dir: Path,
    run: Run = run_program,
    session_id: str | None = None,
) -> Session:
    """Make a new session folder, with the clone in it.

    The sandbox is not made here. It is made by the session's first
    command, so a session nobody uses costs a folder and no more.
    """

    root = root_of(spec)
    hermes = mounts.hermes_root(data_dir)

    if mounts.is_inside(root, hermes):
        raise inside_hermes(spec, root, hermes)

    if VOLUME_SEPARATOR in str(root):
        raise SandboxError(
            f"{root} has a colon in it, which Docker reads as the end "
            "of a folder's name. Set `workspace` to a folder without."
        )

    prepare_root(root)

    name = session_id or new_id()
    folder = root / name
    session = Session(name, folder, start_folder(spec, folder))

    try:
        folder.mkdir()
    except OSError as error:
        raise SandboxError(
            f"{folder} cannot be made on this server: "
            f"{error.strerror or error}"
        ) from None

    try:
        clone_into(spec, session, run)
    except SandboxError:
        # A failed clone leaves no half a session behind.
        shutil.rmtree(folder, ignore_errors=True)

        raise

    return session


def container_name(spec: TargetSpec, session: Session) -> str:
    return f"{CONTAINER_PREFIX}-{spec.name}-{session.id}"


def build_run_command(
    docker: str,
    spec: TargetSpec,
    session: Session,
    profile: str,
) -> list[str]:
    """The one line that makes a sandbox. Everything the container is
    given, and everything it is denied, is on it."""

    command = [
        docker,
        "run",
        "--detach",
        # Reaps what a command leaves behind.
        "--init",
        "--name", container_name(spec, session),
        "--label", f"{LABEL_ENVIRONMENT}={spec.name}",
        "--label", f"{LABEL_SESSION}={session.id}",
        "--label", f"{LABEL_PROFILE}={profile}",
        "--cap-drop", "ALL",
    ]

    for capability in KEPT_CAPABILITIES:
        command += ["--cap-add", capability]

    command += [
        "--security-opt", "no-new-privileges",
        "--pids-limit", PIDS_LIMIT,
    ]

    if not spec.network:
        command += ["--network", NETWORK_NONE]

    for key, value in spec.env:
        command += ["--env", f"{key}={value}"]

    command += [
        # The session's folder, at the same path as on the server.
        "--volume", f"{session.folder}:{session.folder}",
        "--workdir", str(session.start),
        spec.image,
        *IDLE_COMMAND,
    ]

    return command


def build_exec_command(
    docker: str,
    container: str,
    with_stdin: bool = False,
) -> list[str]:
    """The start of a line that runs something in a sandbox."""

    command = [docker, "exec"]

    if with_stdin:
        command.append("--interactive")

    command.append(container)

    return command


def container_state(
    docker: str,
    name: str,
    run: Run,
) -> str | None:
    """`running`, `exited` and so on, or None when there is none."""

    command = [docker, "inspect", "--format", "{{.State.Status}}", name]
    done = call(run, "Docker", command, QUICK_SECONDS)

    if done.returncode != 0:
        return None

    return done.stdout.strip() or None


def remove_container(docker: str, name: str, run: Run) -> None:
    call(run, "Docker", [docker, "rm", "--force", name], QUICK_SECONDS)


def start_container(
    docker: str,
    spec: TargetSpec,
    name: str,
    run: Run,
) -> None:
    done = call(run, "Docker", [docker, "start", name], QUICK_SECONDS)

    if done.returncode == 0:
        return

    raise SandboxError(
        f"The sandbox of {spec.label} could not be started again: "
        f"{said(done)}"
    )


def trust_clone(
    docker: str,
    spec: TargetSpec,
    session: Session,
    run: Run,
) -> None:
    """Tell the sandbox's own git that the clone is safe to work in.

    On a Linux server the clone belongs to the account Hermes runs
    as, and the sandbox runs as its own root. Git refuses a
    repository that belongs to somebody else. The line goes in the
    sandbox's own settings and reaches no git outside it.
    """

    if not spec.repository:
        return

    command = [
        *build_exec_command(docker, container_name(spec, session), True),
        "sh",
        "-c",
        f"cat >> {GIT_SYSTEM_CONFIG}",
    ]
    text = f"[safe]\n\tdirectory = {session.start}\n"

    call(run, "Docker", command, QUICK_SECONDS, stdin_text=text)


def make_container(
    docker: str,
    spec: TargetSpec,
    session: Session,
    profile: str,
    run: Run,
) -> None:
    name = container_name(spec, session)
    command = build_run_command(docker, spec, session, profile)
    done = call(run, f"The sandbox of {spec.label}", command, RUN_SECONDS)

    if done.returncode != 0:
        # A run that fails can leave a container that never started.
        remove_container(docker, name, run)

        raise SandboxError(
            f"The sandbox of {spec.label} could not be made: {said(done)}"
        )

    check = [*build_exec_command(docker, name), "bash", "-c", "true"]
    shell = call(run, "Docker", check, QUICK_SECONDS)

    if shell.returncode != 0:
        remove_container(docker, name, run)

        raise SandboxError(
            f"The image {spec.image} has no `bash`. Hermes runs every "
            "command with it, so an environment needs an image that has."
        )

    trust_clone(docker, spec, session, run)


def run_setup(
    docker: str,
    spec: TargetSpec,
    session: Session,
    run: Run,
) -> bool:
    """Run the setup script, once per container. True when it ran."""

    if not spec.setup:
        return False

    name = container_name(spec, session)
    inside = build_exec_command(docker, name)
    marked = call(
        run,
        "Docker",
        [*inside, "test", "-e", SETUP_MARKER],
        QUICK_SECONDS,
    )

    if marked.returncode == 0:
        return False

    script = [
        *build_exec_command(docker, name, with_stdin=True),
        "bash",
        "-s",
    ]
    done = call(
        run,
        f"The setup script of {spec.label}",
        script,
        SETUP_SECONDS,
        stdin_text=spec.setup + "\n",
    )

    if done.returncode != 0:
        raise SandboxError(
            f"The setup script of {spec.label} failed (exit "
            f"{done.returncode}): {said(done)}. Fix `setup` in the "
            "settings. It runs again on the next command."
        )

    call(run, "Docker", [*inside, "touch", SETUP_MARKER], QUICK_SECONDS)

    return True


def read_home(docker: str, name: str, run: Run) -> str:
    """The home folder commands in a sandbox start with."""

    command = [*build_exec_command(docker, name), "printenv", "HOME"]
    done = call(run, "Docker", command, QUICK_SECONDS)
    home = done.stdout.strip()

    if done.returncode != 0 or not home.startswith("/"):
        return FALLBACK_HOME

    return home


def ensure_sandbox(
    spec: TargetSpec,
    session: Session,
    profile: str,
    run: Run = run_program,
    docker: str | None = None,
) -> Sandbox:
    """A session's container, running and set up.

    One that is there is used again, and started first if a restart
    of the server stopped it. Otherwise one is made.
    """

    program = docker or need_docker()

    if not session.folder.is_dir():
        raise SandboxError(
            f"{session.folder} is gone, so the sandbox of {spec.label} "
            "has no folder to work in. Start a new session."
        )

    name = container_name(spec, session)
    state = container_state(program, name, run)

    if state is None:
        make_container(program, spec, session, profile, run)

    if state is not None and state != STATE_RUNNING:
        start_container(program, spec, name, run)

    run_setup(program, spec, session, run)

    return Sandbox(name, read_home(program, name, run), state is None)


def build_empty_command(session: Session) -> list[str]:
    """What empties a session's folder and leaves the folder."""

    return ["find", str(session.folder), "-mindepth", "1", "-delete"]


def build_sweep_command(
    docker: str,
    spec: TargetSpec,
    session: Session,
) -> list[str]:
    """A container that lives only to empty one session's folder.

    For a session whose own sandbox is gone. It is given that folder,
    no network and the one capability the job needs.
    """

    return [
        docker,
        "run",
        "--rm",
        "--network", NETWORK_NONE,
        "--cap-drop", "ALL",
        "--cap-add", SWEEP_CAPABILITY,
        "--security-opt", "no-new-privileges",
        "--volume", f"{session.folder}:{session.folder}",
        spec.image,
        *build_empty_command(session),
    ]


def empty_from_inside(
    docker: str,
    spec: TargetSpec,
    session: Session,
    run: Run,
) -> None:
    """Delete a session's files as its sandbox's own root.

    On a Linux server, a file the sandbox made belongs to root, and
    the account Hermes runs as cannot delete it. The sandbox can.
    """

    name = container_name(spec, session)
    state = container_state(docker, name, run)

    if state is None:
        return

    if state != STATE_RUNNING:
        call(run, "Docker", [docker, "start", name], QUICK_SECONDS)

    command = [*build_exec_command(docker, name), *build_empty_command(session)]

    call(run, "Docker", command, QUICK_SECONDS)


def remove_session(
    spec: TargetSpec,
    session: Session,
    run: Run = run_program,
    docker: str | None = None,
) -> None:
    """Take a session's sandbox and its folder away, for good."""

    program = find_docker() if docker is None else docker

    if program:
        empty_from_inside(program, spec, session, run)
        remove_container(program, container_name(spec, session), run)

    shutil.rmtree(session.folder, ignore_errors=True)

    if not os.path.lexists(session.folder):
        return

    # The sandbox was lost before this, and left files only root can
    # delete. A container made for the job empties the folder.
    if program:
        sweep = build_sweep_command(program, spec, session)

        call(run, "Docker", sweep, RUN_SECONDS)
        shutil.rmtree(session.folder, ignore_errors=True)

    if not os.path.lexists(session.folder):
        return

    raise SandboxError(
        f"{session.folder} could not be removed. Something in it "
        "belongs to another account. Remove it as root."
    )


def count_containers(
    spec: TargetSpec,
    profile: str,
    run: Run = run_program,
    docker: str | None = None,
) -> tuple[int, int]:
    """An environment's sandboxes: (running, stopped)."""

    program = find_docker() if docker is None else docker

    if not program:
        return 0, 0

    command = [
        program,
        "ps",
        "--all",
        "--filter", f"label={LABEL_ENVIRONMENT}={spec.name}",
        "--filter", f"label={LABEL_PROFILE}={profile}",
        "--format", "{{.State}}",
    ]

    try:
        done = call(run, "Docker", command, QUICK_SECONDS)
    except SandboxError:
        return 0, 0

    states = [line.strip() for line in done.stdout.splitlines()]
    states = [state for state in states if state]
    running = states.count(STATE_RUNNING)

    return running, len(states) - running

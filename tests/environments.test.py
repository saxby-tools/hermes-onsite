"""An environment: a session folder on the server and its sandbox.

No test here runs Docker or git. Each program is stood in for by a
script of answers, and the lines sent to it are what is checked.
"""

from __future__ import annotations

import subprocess

from pathlib import Path

import pytest

from onsite import config
from onsite import environments


DOCKER = "/usr/bin/docker"
SESSION_ID = "20261126-231502-9f3a"
PROFILE = "work"


def build_spec(tmp_path, **settings) -> config.TargetSpec:
    block = {
        "kind": "environment",
        "image": "debian:stable-slim",
        "workspace": str(tmp_path / "sessions"),
        **settings,
    }
    parsed = config.parse_config({
        "default": "mac",
        "targets": {"web": block, "mac": {"kind": "local"}},
    })

    return parsed.target("web")


def data_dir(tmp_path) -> Path:
    """A plugin data folder in a Hermes home beside the sessions."""

    return tmp_path / "hermes" / "plugin-data" / "onsite"


def done(code: int = 0, out: str = "", err: str = ""):
    return subprocess.CompletedProcess([], code, out, err)


class Programs:
    """Stands in for `docker` and `git`. Answers are matched on a word
    of the line, in the order given. A line nothing matches succeeds
    with no output."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.lines: list[list[str]] = []
        self.options: list[dict] = []

    def __call__(self, command, timeout, **options):
        self.lines.append(list(command))
        self.options.append(options)

        for index, (word, answer) in enumerate(self.answers):
            if word not in command:
                continue

            del self.answers[index]

            if isinstance(answer, BaseException):
                raise answer

            return answer

        return done()

    def sent(self, word: str) -> list[list[str]]:
        return [line for line in self.lines if word in line]


def make_session(tmp_path, spec) -> environments.Session:
    session = environments.open_session(
        spec,
        data_dir(tmp_path),
        run=Programs(),
        session_id=SESSION_ID,
    )

    return session


# ---- Finding Docker --------------------------------------------------------


def test_docker_on_the_path_is_used():
    found = environments.find_docker(which=lambda name: f"/bin/{name}")

    assert found == "/bin/docker"


def test_docker_desktop_is_found_when_the_path_has_none():
    place = environments.DOCKER_PLACES[1]
    found = environments.find_docker(
        which=lambda name: None,
        exists=lambda path: path == place,
    )

    assert found == place


def test_a_server_with_no_docker_says_so():
    found = environments.find_docker(
        which=lambda name: None,
        exists=lambda path: False,
    )

    assert found == ""


def test_the_probe_asks_the_daemon_not_only_the_program():
    programs = Programs()
    ok, detail, _ = environments.probe(run=programs, docker=DOCKER)

    assert ok is True
    assert detail == ""
    assert programs.lines == [
        [DOCKER, "version", "--format", "{{.Server.Version}}"],
    ]


def test_a_daemon_that_is_down_fails_the_probe_with_its_words():
    programs = Programs(("version", done(1, err="Cannot connect\n")))
    ok, detail, _ = environments.probe(run=programs, docker=DOCKER)

    assert ok is False
    assert "Cannot connect" in detail


def test_a_probe_with_no_docker_runs_nothing():
    programs = Programs()
    ok, detail, _ = environments.probe(run=programs, docker="")

    assert ok is False
    assert "no `docker` program" in detail
    assert programs.lines == []


def test_a_probe_that_hangs_is_a_failure_not_a_crash():
    hung = subprocess.TimeoutExpired("docker", 5)
    programs = Programs(("version", hung))
    ok, detail, _ = environments.probe(run=programs, docker=DOCKER)

    assert ok is False
    assert "did not finish" in detail


# ---- Where sessions are kept -----------------------------------------------


def test_the_default_place_is_beside_the_mounts(tmp_path):
    root = environments.default_root(
        tmp_path / ".hermes" / "plugin-data" / "onsite",
        "web",
        home=tmp_path,
    )

    assert root == tmp_path / ".local/share/onsite/env/hermes/web"


def test_an_id_is_the_time_and_a_token():
    made = environments.new_id(now=0.0, token="9f3a")

    assert environments.ID_RE.match(made)
    assert made.endswith("-9f3a")


def test_two_ids_made_at_once_differ():
    assert environments.new_id(now=1.0) != environments.new_id(now=1.0)


@pytest.mark.parametrize("repository, name", [
    ("https://host.example/you/project.git", "project"),
    ("https://host.example/you/project/", "project"),
    ("git@host.example:you/project.git", "project"),
    ("git@host.example:project.git", "project"),
    ("/srv/git/project.git", "project"),
    ("https://host.example/you/a b.git", "a-b"),
    ("https://host.example/you/.git", "repo"),
])
def test_a_clone_is_named_for_its_repository(repository, name):
    assert environments.repo_name(repository) == name


def test_a_session_with_a_repository_opens_in_the_clone(tmp_path):
    spec = build_spec(
        tmp_path,
        repository="https://host.example/you/project.git",
    )
    folder = tmp_path / "sessions" / SESSION_ID

    assert environments.start_folder(spec, folder) == folder / "project"


def test_a_session_with_no_repository_opens_in_its_own_folder(tmp_path):
    spec = build_spec(tmp_path)
    folder = tmp_path / "sessions" / SESSION_ID

    assert environments.start_folder(spec, folder) == folder


def test_a_folder_inside_a_session_names_that_session(tmp_path):
    spec = build_spec(tmp_path)
    inside = tmp_path / "sessions" / SESSION_ID / "project" / "src"
    session = environments.session_of(spec, str(inside))

    assert session.id == SESSION_ID
    assert session.folder == tmp_path / "sessions" / SESSION_ID


@pytest.mark.parametrize("path", [
    "sessions",
    "sessions/not-a-session",
    "sessions-of-another/20261126-231502-9f3a",
    "elsewhere",
])
def test_a_folder_that_is_no_session_names_none(tmp_path, path):
    spec = build_spec(tmp_path)

    assert environments.session_of(spec, str(tmp_path / path)) is None


def test_sessions_are_listed_oldest_first_and_strays_left_out(tmp_path):
    spec = build_spec(tmp_path)
    root = tmp_path / "sessions"

    for name in ("20261126-231502-9f3a", "20261125-080000-0001", "notes"):
        (root / name).mkdir(parents=True)

    listed = environments.list_sessions(spec)

    assert [session.id for session in listed] == [
        "20261125-080000-0001",
        "20261126-231502-9f3a",
    ]


def test_an_environment_never_used_has_no_sessions(tmp_path):
    assert environments.list_sessions(build_spec(tmp_path)) == []


# ---- Opening a session -----------------------------------------------------


def test_a_new_session_is_a_folder_and_nothing_else(tmp_path):
    spec = build_spec(tmp_path)
    programs = Programs()

    session = environments.open_session(
        spec,
        data_dir(tmp_path),
        run=programs,
        session_id=SESSION_ID,
    )

    assert session.folder.is_dir()
    assert session.start == session.folder
    # No clone was asked for and no sandbox is made yet.
    assert programs.lines == []


def test_the_folder_that_holds_the_sessions_is_closed_to_others(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)

    mode = session.folder.parent.stat().st_mode & 0o777

    assert mode == environments.PRIVATE_DIR_MODE


def test_a_folder_that_was_already_there_is_not_changed(tmp_path):
    spec = build_spec(tmp_path)
    root = tmp_path / "sessions"

    root.mkdir(mode=0o755)
    make_session(tmp_path, spec)

    assert root.stat().st_mode & 0o777 == 0o755


def test_the_repository_is_cloned_on_the_server(tmp_path):
    spec = build_spec(
        tmp_path,
        repository="https://host.example/you/project.git",
    )
    programs = Programs()

    session = environments.open_session(
        spec,
        data_dir(tmp_path),
        run=programs,
        session_id=SESSION_ID,
    )

    assert programs.lines == [[
        "git",
        "clone",
        "--quiet",
        "--no-hardlinks",
        "--",
        "https://host.example/you/project.git",
        str(session.folder / "project"),
    ]]


def test_the_clone_may_never_stop_to_ask_for_a_login(tmp_path):
    spec = build_spec(tmp_path, repository="/srv/git/project.git")
    programs = Programs()

    environments.open_session(
        spec,
        data_dir(tmp_path),
        run=programs,
        session_id=SESSION_ID,
    )

    env = programs.options[0]["env"]

    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]


def test_an_ssh_command_the_account_set_is_kept():
    env = environments.clone_env({"GIT_SSH_COMMAND": "ssh -i /key"})

    assert env["GIT_SSH_COMMAND"] == "ssh -i /key"


def test_a_failed_clone_leaves_no_half_a_session(tmp_path):
    spec = build_spec(
        tmp_path,
        repository="https://host.example/you/private.git",
    )
    refused = done(128, err="fatal: could not read Username\n")

    with pytest.raises(environments.SandboxError) as caught:
        environments.open_session(
            spec,
            data_dir(tmp_path),
            run=Programs(("clone", refused)),
            session_id=SESSION_ID,
        )

    assert "could not read Username" in str(caught.value)
    assert "the account Hermes runs as" in str(caught.value)
    assert not (tmp_path / "sessions" / SESSION_ID).exists()


def test_a_server_with_no_git_says_so(tmp_path):
    spec = build_spec(tmp_path, repository="/srv/git/project.git")
    missing = FileNotFoundError(2, "No such file or directory")

    with pytest.raises(environments.SandboxError) as caught:
        environments.open_session(
            spec,
            data_dir(tmp_path),
            run=Programs(("clone", missing)),
            session_id=SESSION_ID,
        )

    assert "`git` cannot be run" in str(caught.value)


def test_sessions_inside_the_hermes_home_are_refused(tmp_path):
    hermes = tmp_path / "hermes"
    spec = build_spec(tmp_path, workspace=str(hermes / "sessions"))

    with pytest.raises(environments.SandboxError) as caught:
        environments.open_session(spec, data_dir(tmp_path), run=Programs())

    assert "inside the Hermes home" in str(caught.value)
    assert "`workspace`" in str(caught.value)
    # Nothing is made at the place first.
    assert not (hermes / "sessions").exists()


def test_a_place_with_a_colon_is_refused_before_docker_reads_it(tmp_path):
    spec = build_spec(tmp_path, workspace=str(tmp_path / "a:b"))

    with pytest.raises(environments.SandboxError) as caught:
        environments.open_session(spec, data_dir(tmp_path), run=Programs())

    assert "colon" in str(caught.value)


# ---- The line that makes a sandbox -----------------------------------------


def run_line(tmp_path, **settings) -> list[str]:
    spec = build_spec(tmp_path, **settings)
    session = environments.session_of(
        spec,
        str(tmp_path / "sessions" / SESSION_ID),
    )

    return environments.build_run_command(DOCKER, spec, session, PROFILE)


def pairs(line: list[str], option: str) -> list[str]:
    return [
        line[index + 1]
        for index, word in enumerate(line)
        if word == option
    ]


def test_a_sandbox_is_named_and_labeled_for_its_session(tmp_path):
    line = run_line(tmp_path)

    assert pairs(line, "--name") == [f"onsite-env-web-{SESSION_ID}"]
    assert pairs(line, "--label") == [
        "onsite-environment=web",
        f"onsite-session={SESSION_ID}",
        f"onsite-profile={PROFILE}",
    ]


def test_a_sandbox_is_given_one_folder_at_its_own_path(tmp_path):
    line = run_line(tmp_path)
    folder = tmp_path / "sessions" / SESSION_ID

    assert pairs(line, "--volume") == [f"{folder}:{folder}"]
    assert pairs(line, "--workdir") == [str(folder)]
    # Nothing else of the server is shared: no second folder, no
    # socket, no device.
    assert "--mount" not in line
    assert "--device" not in line
    assert "--privileged" not in line


def test_every_capability_is_dropped_but_the_five_named(tmp_path):
    line = run_line(tmp_path)

    assert pairs(line, "--cap-drop") == ["ALL"]
    assert pairs(line, "--cap-add") == list(environments.KEPT_CAPABILITIES)
    assert pairs(line, "--security-opt") == ["no-new-privileges"]
    assert pairs(line, "--pids-limit") == [environments.PIDS_LIMIT]


def test_a_sandbox_has_no_network_unless_the_settings_say_so(tmp_path):
    assert pairs(run_line(tmp_path), "--network") == ["none"]
    assert pairs(run_line(tmp_path, network=False), "--network") == ["none"]
    assert pairs(run_line(tmp_path, network=True), "--network") == []


def test_plain_variables_are_set_in_the_sandbox(tmp_path):
    line = run_line(tmp_path, env={"NODE_ENV": "development", "N": 2})

    assert pairs(line, "--env") == ["NODE_ENV=development", "N=2"]


def test_the_image_and_the_wait_come_last(tmp_path):
    line = run_line(tmp_path)

    assert line[-3:] == ["debian:stable-slim", "sleep", "infinity"]


def test_a_line_that_runs_in_a_sandbox_can_take_input():
    plain = environments.build_exec_command(DOCKER, "box")
    piped = environments.build_exec_command(DOCKER, "box", with_stdin=True)

    assert plain == [DOCKER, "exec", "box"]
    assert piped == [DOCKER, "exec", "--interactive", "box"]


# ---- Making a sandbox ready ------------------------------------------------


def test_the_first_command_makes_the_sandbox(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs(
        ("inspect", done(1, err="No such object")),
        ("printenv", done(0, "/root\n")),
    )

    sandbox = environments.ensure_sandbox(
        spec, session, PROFILE, run=programs, docker=DOCKER,
    )

    assert sandbox.container == f"onsite-env-web-{SESSION_ID}"
    assert sandbox.home == "/root"
    assert sandbox.made is True
    assert len(programs.sent("run")) == 1
    assert programs.sent("start") == []


def test_a_sandbox_that_is_running_is_used_again(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs(("inspect", done(0, "running\n")))

    sandbox = environments.ensure_sandbox(
        spec, session, PROFILE, run=programs, docker=DOCKER,
    )

    assert sandbox.made is False
    assert programs.sent("run") == []
    assert programs.sent("start") == []


def test_a_sandbox_a_restart_stopped_is_started_not_remade(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs(("inspect", done(0, "exited\n")))

    sandbox = environments.ensure_sandbox(
        spec, session, PROFILE, run=programs, docker=DOCKER,
    )

    assert sandbox.made is False
    assert programs.sent("run") == []
    assert programs.sent("start") == [
        [DOCKER, "start", f"onsite-env-web-{SESSION_ID}"],
    ]


def test_a_sandbox_that_will_not_start_says_why(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs(
        ("inspect", done(0, "exited\n")),
        ("start", done(1, err="Error: mount source is gone\n")),
    )

    with pytest.raises(environments.SandboxError) as caught:
        environments.ensure_sandbox(
            spec, session, PROFILE, run=programs, docker=DOCKER,
        )

    assert "mount source is gone" in str(caught.value)


def test_a_session_whose_folder_is_gone_gets_no_sandbox(tmp_path):
    spec = build_spec(tmp_path)
    session = environments.session_of(
        spec,
        str(tmp_path / "sessions" / SESSION_ID),
    )
    programs = Programs()

    with pytest.raises(environments.SandboxError) as caught:
        environments.ensure_sandbox(
            spec, session, PROFILE, run=programs, docker=DOCKER,
        )

    assert "is gone" in str(caught.value)
    assert programs.lines == []


def test_a_run_that_fails_leaves_no_container_behind(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs(
        ("inspect", done(1)),
        ("run", done(125, err="Unable to find image\n")),
    )

    with pytest.raises(environments.SandboxError) as caught:
        environments.ensure_sandbox(
            spec, session, PROFILE, run=programs, docker=DOCKER,
        )

    assert "Unable to find image" in str(caught.value)
    assert programs.sent("rm") == [
        [DOCKER, "rm", "--force", f"onsite-env-web-{SESSION_ID}"],
    ]


def test_an_image_with_no_bash_is_refused_and_removed(tmp_path):
    spec = build_spec(tmp_path, image="alpine:3.20")
    session = make_session(tmp_path, spec)
    programs = Programs(
        ("inspect", done(1)),
        ("bash", done(127, err="exec: bash: not found\n")),
    )

    with pytest.raises(environments.SandboxError) as caught:
        environments.ensure_sandbox(
            spec, session, PROFILE, run=programs, docker=DOCKER,
        )

    assert "has no `bash`" in str(caught.value)
    assert len(programs.sent("rm")) == 1


# ---- The setup script ------------------------------------------------------


SETUP = "apt-get update\napt-get install -y shellcheck"


def test_the_setup_script_runs_once_in_a_new_sandbox(tmp_path):
    spec = build_spec(tmp_path, setup=SETUP)
    session = make_session(tmp_path, spec)
    programs = Programs(
        ("inspect", done(1)),
        ("test", done(1)),
    )

    environments.ensure_sandbox(
        spec, session, PROFILE, run=programs, docker=DOCKER,
    )

    name = f"onsite-env-web-{SESSION_ID}"
    script = programs.sent("-s")

    assert script == [[DOCKER, "exec", "--interactive", name, "bash", "-s"]]

    sent_with = programs.options[programs.lines.index(script[0])]

    # The script goes in on standard input, never on the line.
    assert sent_with["stdin_text"] == SETUP + "\n"
    assert programs.sent("touch") == [
        [DOCKER, "exec", name, "touch", environments.SETUP_MARKER],
    ]


def test_a_sandbox_already_set_up_is_not_set_up_again(tmp_path):
    spec = build_spec(tmp_path, setup=SETUP)
    session = make_session(tmp_path, spec)
    programs = Programs(
        ("inspect", done(0, "running\n")),
        ("test", done(0)),
    )

    environments.ensure_sandbox(
        spec, session, PROFILE, run=programs, docker=DOCKER,
    )

    assert programs.sent("-s") == []
    assert programs.sent("touch") == []


def test_no_setup_script_means_nothing_is_run_for_one(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs(("inspect", done(0, "running\n")))

    environments.ensure_sandbox(
        spec, session, PROFILE, run=programs, docker=DOCKER,
    )

    assert programs.sent("test") == []
    assert programs.sent("-s") == []


def test_a_setup_script_that_fails_stops_the_session_and_is_not_marked(
    tmp_path,
):
    spec = build_spec(tmp_path, setup=SETUP)
    session = make_session(tmp_path, spec)
    programs = Programs(
        ("inspect", done(1)),
        ("test", done(1)),
        ("-s", done(100, out="E: Unable to locate package shellcheck\n")),
    )

    with pytest.raises(environments.SandboxError) as caught:
        environments.ensure_sandbox(
            spec, session, PROFILE, run=programs, docker=DOCKER,
        )

    assert "exit 100" in str(caught.value)
    assert "Unable to locate package" in str(caught.value)
    assert programs.sent("touch") == []


def test_a_home_the_sandbox_cannot_name_falls_back(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs(
        ("inspect", done(0, "running\n")),
        ("printenv", done(1)),
    )

    sandbox = environments.ensure_sandbox(
        spec, session, PROFILE, run=programs, docker=DOCKER,
    )

    assert sandbox.home == environments.FALLBACK_HOME


# ---- Removing a session ----------------------------------------------------


def test_removing_a_session_takes_its_sandbox_and_its_folder(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)

    (session.folder / "notes.txt").write_text("kept until removed")

    programs = Programs(("inspect", done(0, "running\n")))

    environments.remove_session(spec, session, run=programs, docker=DOCKER)

    name = f"onsite-env-web-{SESSION_ID}"

    # Emptied from inside first, so a file root made there is gone
    # even where the server's account could not delete it.
    assert programs.sent("find") == [[
        DOCKER, "exec", name,
        "find", str(session.folder), "-mindepth", "1", "-delete",
    ]]
    assert programs.sent("rm") == [[DOCKER, "rm", "--force", name]]
    assert not session.folder.exists()
    # The folder that holds the sessions is the person's to keep.
    assert session.folder.parent.is_dir()


def test_a_stopped_sandbox_is_started_so_it_can_empty_its_folder(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs(("inspect", done(0, "exited\n")))

    environments.remove_session(spec, session, run=programs, docker=DOCKER)

    order = [line[1] for line in programs.lines]

    assert order == ["inspect", "start", "exec", "rm"]


def test_a_session_that_never_ran_is_only_a_folder_to_remove(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs(("inspect", done(1)))

    environments.remove_session(spec, session, run=programs, docker=DOCKER)

    assert programs.sent("find") == []
    assert not session.folder.exists()


def test_a_server_whose_docker_is_gone_can_still_remove_the_folder(
    tmp_path,
):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs()

    environments.remove_session(spec, session, run=programs, docker="")

    assert programs.lines == []
    assert not session.folder.exists()


def test_a_lost_sandbox_gets_a_container_made_to_empty_its_folder(
    tmp_path,
    monkeypatch,
):
    # The sandbox is gone and left files only root can delete, so the
    # first try at the folder fails.
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    tries = []

    def stubborn(folder, ignore_errors=False):
        tries.append(folder)

        if len(tries) > 1:
            session.folder.rmdir()

    monkeypatch.setattr(environments.shutil, "rmtree", stubborn)

    programs = Programs(("inspect", done(1)))

    environments.remove_session(spec, session, run=programs, docker=DOCKER)

    folder = str(session.folder)

    assert programs.sent("run") == [[
        DOCKER, "run", "--rm",
        "--network", "none",
        "--cap-drop", "ALL",
        "--cap-add", "DAC_OVERRIDE",
        "--security-opt", "no-new-privileges",
        "--volume", f"{folder}:{folder}",
        "debian:stable-slim",
        "find", folder, "-mindepth", "1", "-delete",
    ]]
    assert not session.folder.exists()


def test_a_folder_that_will_not_go_is_said_not_hidden(tmp_path, monkeypatch):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)

    monkeypatch.setattr(
        environments.shutil,
        "rmtree",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(environments.SandboxError) as caught:
        environments.remove_session(
            spec, session, run=Programs(), docker=DOCKER,
        )

    assert "could not be removed" in str(caught.value)


# ---- Git in the sandbox ----------------------------------------------------


def test_the_sandbox_is_told_its_clone_is_safe_to_work_in(tmp_path):
    spec = build_spec(tmp_path, repository="/srv/git/project.git")
    session = make_session(tmp_path, spec)
    programs = Programs(("inspect", done(1)))

    environments.ensure_sandbox(
        spec, session, PROFILE, run=programs, docker=DOCKER,
    )

    name = f"onsite-env-web-{SESSION_ID}"
    told = programs.sent("cat >> /etc/gitconfig")

    assert told == [[
        DOCKER, "exec", "--interactive", name,
        "sh", "-c", "cat >> /etc/gitconfig",
    ]]

    sent_with = programs.options[programs.lines.index(told[0])]

    assert sent_with["stdin_text"] == (
        f"[safe]\n\tdirectory = {session.folder}/project\n"
    )


def test_a_sandbox_with_no_clone_is_told_nothing_about_git(tmp_path):
    spec = build_spec(tmp_path)
    session = make_session(tmp_path, spec)
    programs = Programs(("inspect", done(1)))

    environments.ensure_sandbox(
        spec, session, PROFILE, run=programs, docker=DOCKER,
    )

    assert programs.sent("cat >> /etc/gitconfig") == []


def test_a_sandbox_used_again_is_not_told_twice(tmp_path):
    spec = build_spec(tmp_path, repository="/srv/git/project.git")
    session = make_session(tmp_path, spec)
    programs = Programs(("inspect", done(0, "running\n")))

    environments.ensure_sandbox(
        spec, session, PROFILE, run=programs, docker=DOCKER,
    )

    assert programs.sent("cat >> /etc/gitconfig") == []


# ---- Counting, for status --------------------------------------------------


def test_sandboxes_are_counted_by_environment_and_profile(tmp_path):
    spec = build_spec(tmp_path)
    programs = Programs(("ps", done(0, "running\nexited\nrunning\n")))

    counted = environments.count_containers(
        spec, PROFILE, run=programs, docker=DOCKER,
    )

    assert counted == (2, 1)
    assert programs.lines == [[
        DOCKER, "ps", "--all",
        "--filter", "label=onsite-environment=web",
        "--filter", f"label=onsite-profile={PROFILE}",
        "--format", "{{.State}}",
    ]]


def test_counting_with_no_docker_is_zero_not_an_error(tmp_path):
    spec = build_spec(tmp_path)

    assert environments.count_containers(spec, PROFILE, docker="") == (0, 0)

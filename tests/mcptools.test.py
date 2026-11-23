"""MCP servers on a target: the command that starts one, and the
hook that keeps its tools to sessions on that target."""

from __future__ import annotations

import pytest

from hermes_hands import config
from hermes_hands import hooks
from hermes_hands import mcptools


SETTINGS = {
    "default": "mac",
    "targets": {
        "laptop": {"kind": "ssh", "host": "laptop.example", "user": "dev"},
        "lab": {"kind": "ssh", "host": "lab.example", "user": "dev"},
        "lab-gpu": {
            "kind": "ssh",
            "host": "127.0.0.1",
            "port": 22022,
            "user": "dev",
            "key": "/keys/gpu",
            "path": "none",
        },
        "mac": {"kind": "local"},
    },
}
CONFIG = config.parse_config(SETTINGS)
LAPTOP = CONFIG.target("laptop")
GPU = CONFIG.target("lab-gpu")


class FakeRuntime:
    def __init__(self, loaded=CONFIG, error=None):
        self.loaded = loaded
        self.error = error

    def load_config(self):
        if self.error is not None:
            raise self.error

        return self.loaded


# ---- naming -----------------------------------------------------------


def test_a_server_is_named_for_its_target():
    assert mcptools.server_name(LAPTOP, "files") == "laptop_files"
    assert mcptools.server_name(GPU, "files") == "lab_gpu_files"


@pytest.mark.parametrize("name", ["", "Files", "my-files", "9lives", "a b"])
def test_a_bad_server_name_is_refused(name):
    with pytest.raises(mcptools.McpError, match="not a server name"):
        mcptools.server_name(LAPTOP, name)


# ---- the command Hermes will run --------------------------------------


def test_the_server_is_started_over_ssh_on_the_target():
    arguments = mcptools.build_ssh_arguments(LAPTOP, ["npx", "-y", "pkg"])

    assert "dev@laptop.example" in arguments
    assert "BatchMode=yes" in arguments
    assert arguments[-1].startswith("sh -c ")
    assert "exec npx -y pkg" in arguments[-1]


def test_the_interactive_path_is_read_without_touching_the_stream():
    script = mcptools.build_remote_script(LAPTOP, ["npx", "pkg"])

    # The shell's output is captured and its input closed, so its
    # greeting cannot land in the server's conversation with Hermes.
    assert "</dev/null" in script
    assert script.index("PATH=") < script.index("exec npx pkg")


def test_a_target_with_path_none_skips_the_interactive_shell():
    script = mcptools.build_remote_script(GPU, ["npx", "pkg"])

    assert script == "exec npx pkg"


def test_words_with_spaces_stay_one_word_each():
    script = mcptools.build_remote_script(GPU, ["server", "/my files"])

    assert script == "exec server '/my files'"


def test_port_key_and_host_key_alias_are_passed():
    arguments = mcptools.build_ssh_arguments(GPU, ["server"])

    assert arguments[arguments.index("-p") + 1] == "22022"
    assert arguments[arguments.index("-i") + 1] == "/keys/gpu"
    assert "HostKeyAlias=hermes-hands-lab-gpu" in arguments


def test_a_local_target_is_sent_to_the_ordinary_command():
    with pytest.raises(mcptools.McpError, match="is this machine"):
        mcptools.build_ssh_arguments(CONFIG.target("mac"), ["server"])


def test_a_server_needs_a_command():
    with pytest.raises(mcptools.McpError, match="after `--`"):
        mcptools.build_ssh_arguments(LAPTOP, [])


def test_the_add_call_ends_with_the_ssh_arguments():
    call = mcptools.add_command(LAPTOP, "files", ["npx", "pkg"])

    assert call[:4] == ["hermes", "mcp", "add", "laptop_files"]
    assert call[call.index("--command") + 1] == "ssh"
    assert call.index("--args") > call.index("--command")
    assert call[call.index("--env") + 1] == "SSH_AUTH_SOCK=${SSH_AUTH_SOCK}"
    assert call[-1].endswith("exec npx pkg'")


# ---- whose tool is it -------------------------------------------------


@pytest.mark.parametrize("tool, server", [
    ("mcp__laptop_files__read", "laptop_files"),
    ("mcp__lab_gpu_files__read_text", "lab_gpu_files"),
    ("read_file", ""),
    ("mcp__broken", ""),
])
def test_the_server_is_read_from_the_tool_name(tool, server):
    assert mcptools.server_of(tool) == server


@pytest.mark.parametrize("tool, owner", [
    ("mcp__laptop_files__read", "laptop"),
    ("mcp__lab_files__read", "lab"),
    ("mcp__lab_gpu_files__read", "lab-gpu"),
    ("mcp__github__search", None),
    ("mcp__mac_files__read", None),
    ("terminal", None),
])
def test_a_tool_belongs_to_the_target_its_server_is_named_for(tool, owner):
    assert mcptools.owner_of(tool, CONFIG) == owner


# ---- the hook ---------------------------------------------------------


def test_a_tool_on_another_machine_is_refused(monkeypatch):
    monkeypatch.setattr(hooks, "session_target", lambda runtime, cfg: "mac")

    verdict = hooks.refuse_foreign_mcp(
        FakeRuntime(), "mcp__laptop_files__read",
    )

    assert verdict["action"] == "block"
    assert "runs on `laptop`" in verdict["message"]
    assert "this session runs on `mac`" in verdict["message"]
    assert "/hands laptop" in verdict["message"]


def test_a_tool_on_the_sessions_own_machine_runs(monkeypatch):
    monkeypatch.setattr(hooks, "session_target", lambda runtime, cfg: "laptop")

    assert hooks.refuse_foreign_mcp(
        FakeRuntime(), "mcp__laptop_files__read",
    ) is None


def test_a_server_named_for_no_target_is_left_alone(monkeypatch):
    monkeypatch.setattr(hooks, "session_target", lambda runtime, cfg: "mac")

    assert hooks.refuse_foreign_mcp(
        FakeRuntime(), "mcp__github__search",
    ) is None


def test_unusable_settings_do_not_block_other_tools():
    runtime = FakeRuntime(error=config.ConfigError(["bad"]))

    assert hooks.refuse_foreign_mcp(
        runtime, "mcp__laptop_files__read",
    ) is None


def test_an_unknown_session_does_not_block(monkeypatch):
    def no_session(runtime, cfg):
        raise RuntimeError("no session in scope")

    monkeypatch.setattr(hooks, "session_target", no_session)

    assert hooks.refuse_foreign_mcp(
        FakeRuntime(), "mcp__laptop_files__read",
    ) is None


def test_the_hook_only_looks_at_mcp_tools(monkeypatch):
    seen = []
    monkeypatch.setattr(
        hooks,
        "refuse_foreign_mcp",
        lambda runtime, tool: seen.append(tool),
    )

    hook = hooks.build_hook(FakeRuntime())
    hook(tool_name="terminal", task_id="t")
    hook(tool_name="mcp__laptop_files__read", task_id="t")

    assert seen == ["mcp__laptop_files__read"]

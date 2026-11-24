# Upstream drafts

Drafts only. Nothing here has been posted. Each one is written so the
owner can paste it, edit it or drop it.

All findings are from Hermes v0.23.2, commit `ae2a81ccc0`, read from
source and then measured on a running install.

## Draft comment for issue #104163

*Workspace bindings for provider plugins.*

> I built a terminal backend plugin that routes each session to a
> named machine by the session's folder. Notes on what the seam gave
> me and what it did not, in case they help scope this.
>
> **What worked.** `hermes_cli.plugin_session.session_cwd()` is
> readable inside `create_environment()`, and the environment is
> cached per session key. Those two facts are enough to bind a
> session to a target once and keep it there.
>
> **What the `cwd` argument is.** For a gateway-owned CLI or TUI
> session it was a placeholder (`~` from `default_cwd`), not the
> folder the client was opened in. Desktop explicit picks and the ACP
> adapter do pass the real folder. A plugin cannot tell the two cases
> apart from the argument alone.
>
> **Gaps I hit:**
>
> 1. **Slash commands get no folder.** `_hm_run_plugin_handler` binds
>    `HERMES_SESSION_*` but not the session policy, so
>    `session_cwd()` is empty in a plugin command for a local
>    session. A `/where` style command has to remember the folder
>    from an earlier tool call. Passing `cwd` alongside `session_id`
>    and `session_key` would close it.
> 2. **`on_session_open` fires without the folder in scope.**
>    `_deliver` holds `policy.cwd` but passes only `session_id`,
>    `platform` and `reason`.
> 3. **The prompt-time probe is cached per backend, not per
>    session.** The cache key is `(home, env_type, TERMINAL_CWD)`. A
>    backend with more than one machine behind it gets one OS line
>    reused across sessions. I declined the probe and added a prompt
>    section instead. A per-session key, or a provider flag to skip
>    the probe, would be cleaner.
> 4. **`is_remote` is per backend.** With some targets remote and one
>    local, the prompt's "not on the machine where Hermes itself
>    runs" is wrong for the local target.

## Draft comment for issue #1855

*Multi-backend terminal.*

> A data point from building one backend that serves several
> machines itself, because `terminal.backend` is one setting.
>
> It works, but three things in core key on the backend *name*
> `local` rather than on what the environment is, so a plugin
> backend that returns a `LocalEnvironment` does not get them:
>
> - `self_repo_block` and `locked_skill_block` in
>   `_pre_exec_block` (`env_type == "local"`).
> - `execute_code` treats any backend other than `local` as remote.
> - `terminal_uses_host_cwd` in `gateway/session_policy.py` is true
>   only for `local` and mounted docker, so a launch folder is not
>   registered as the terminal's folder.
>
> `env_uses_host_filesystem(env)` already asks the environment
> instead of the name. The same question for those three would let a
> routing backend keep the host guards for its host target.

## Possible new issues

### ACP edit approval resolves paths on the host with no task id

`acp_adapter/edit_approval.py`, `_required_path`:

```python
from tools.file_tools_paths import _resolve_path_for_task
return str(_resolve_path_for_task(path))
```

Two effects with any remote backend, the built-in `ssh` included:

- The task id defaults to `default`, so a relative path is anchored
  on a folder record that the session never wrote. With an SSH
  environment live it resolves against `~`. The approval title then
  shows the wrong path, for example `~/note.txt` for a file the tool
  goes on to write in the session folder.
- `_read_text_if_exists` reads the file from the host filesystem. For
  `patch`, a file that only the remote has is "Failed to read file",
  and the edit is denied before the user is asked.

Seen as: `patch failed: Edit approval denied: could not prepare diff
(Failed to read file: /home/dev/rig-note.txt)`, with the file present
on the target at `/home/dev/Development/sample/rig-note.txt`.

A fix would pass the session's task id and read through the
environment when it does not use the host filesystem.

### The terminal and the file tools key the folder record differently

The terminal tool records a session's folder under the approval
session key (`record_session_cwd(session_key, ...)` in
`terminal_tool_result.py`). The file tools look it up under the task
id (`_ssh_remote_anchor(task_id)` and
`_authoritative_workspace_root(task_id)`).

In a gateway session the two differ: the task id is the session id
and the session key is the route key. So after `cd project` in the
terminal, `write_file("note.txt")` on an SSH backend lands in `~`.

Seen as: `write_file` reporting `/home/dev/rig-note.txt` right after
a terminal call reported `cwd: /home/dev/Development/sample`.

### File-path rules are chosen from the environment's class name

`_terminal_env_type_for_task` in `tools/file_tools_paths.py` picks
`ssh`, `local`, `docker` and so on by substring match on the class
name, before it looks at the stamped backend name.

This is the only per-environment switch for path rules, and a plugin
can only reach it by naming its class to match. A declared attribute
on the environment, in the spirit of `uses_host_filesystem`, would
make it a contract. On a macOS host the difference is not cosmetic:
host rules call `Path.resolve()`, and `/home` is a symlink there, so
every `/home/...` path is rewritten before it is sent to a Linux
target.

### The plugin docs give the wrong result key

`website/docs/developer-guide/terminal-environment-plugin.md` says
`execute` returns `{"output", "exit_code"}`. Core reads `returncode`
(`terminal_tool_result.py`, `file_operations.py`). An environment
written to the docs reports every command as exit 0.

### A plugin's test rig can make the plugin uninstallable

`plugin_guard` scans every file in a plugin's tree, and the pattern
for the per-user SSH key file is `critical`, which `--force` does not
override. A test container's entrypoint that writes that file blocks
`hermes plugins install` for the whole plugin. `tests/` trees are
stepped down one level, but a rig outside `tests/` is not. Either
documenting which folders are exempt, or stepping down any path a
manifest lists as test-only, would help plugin authors.

### A plugin installed from a subfolder is listed as "provenance: drift"

`hermes_cli/plugins_provenance.py` sorts an installed plugin by two
facts: whether Hermes has an install record for it, and whether its
folder holds a `.git`. A record with no `.git` is called drift, "flagged
with a reinstall remedy".

An install from one folder of a repository
(`hermes plugins install owner/repo/subdir`, or a catalog entry with
`subdir`) publishes only that folder, so it never has a `.git`. Every
such plugin is listed as drift from the moment it is installed.

Seen as: `provenance: drift` under the plugin in `hermes plugins
list`, right after a clean `hermes plugins install
saxby-tools/hermes-onsite/onsite` into an empty home. To a new user
it reads as tampering.

The record already holds the source with its `#subdir` and the exact
revision. Treating a record whose source names a subfolder as a git
install would fix the label.


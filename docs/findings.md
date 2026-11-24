# Phase 0 findings

Nov. 22, 2026. Read from the installed Hermes source, not from docs.

This page is a record of that day. The plugin was named hermes-hands
then and its command was `/hands`. Both are written here by their
names today, onsite and `/onsite`. The counts are that day's.

## Verdict

**Go on folder routing, with one addition: a path map.**

A backend plugin can learn a session's folder. It cannot learn it from
the `cwd` argument of `create_environment()`. It can learn it from
`hermes_cli.plugin_session.session_cwd()`, which Hermes documents for
plugins and which is readable inside `create_environment()`.

Hermes also builds one environment per session key. So a target picked
when the environment is created stays picked for the session. That is
the sticky-session rule, and Hermes gives it for free.

The addition: a brain and a hand rarely agree on paths. The brain may
see a shared folder under a different name than the hand does, and
macOS resolves `/home` through a symlink. Each target therefore takes
an optional `path_map` from brain paths to hand paths.

## Versions

| Thing | Recorded |
| --- | --- |
| Hermes on the host | v0.23.2, commit `ae2a81ccc0`. The brief said 0.23.3. |
| Hermes on the laptop | 0.23.2, read Nov. 22 once SSH worked. |
| Desktop app on the laptop | 0.23.2, the same. |
| `requires_hermes` | `>=0.23.2,<0.24` |

## The verify-first list

| # | Question | Answer |
| --- | --- | --- |
| 1 | Which `cwd` and `task_id` reach the provider | `task_id` is a cache key, `session:<session key>` when a session is bound and `default` when not. `cwd` is the start folder inside the backend, and for a CLI or TUI session it is only `terminal.cwd` or a placeholder. Desktop sends its picked folder. The ACP adapter sends the editor's folder. |
| 2 | May one provider return a stock local environment and reuse `SSHEnvironment` | Yes. Both are private imports, so `hermes plugins validate` warns. The plugin keeps every private import in `compat.py`. |
| 3 | Do approvals stay on | Yes. `tools/approval.py` skips the gate only when the provider's `skip_container_guards` is true. The provider default is true, so the plugin sets it false. |
| 4 | Output and timeout limits | 180 s default, 600 s foreground cap, 50,000 characters of output. Past the cap a command moves to the background. |
| 5 | What state SSH copies to the remote | Credential files, skills and cache, into `~/.hermes` on the remote. `SSHEnvironment(sync_files=False)` turns all of it off. |
| 6 | Where plugin config belongs | `plugins.entries.onsite.settings`. Each profile loads plugins only from its own `plugins/` folder and its own `plugins.enabled` list. |
| 7 | Can a hook add the target to the approval prompt | No. `pre_approval_request` is an observer and its return value is ignored. |
| 8 | Tool-media renderer and relative imports | The desktop half must be one file. Only `@hermes/plugin-sdk` and `react` resolve. The tool-media renderer needs a `MEDIA:` line and is absent from desktop 0.22.5. A transcript directive is the workable card. |
| 9 | Does `/rollback` work with a remote target | No. Checkpoints run host `git` on the host path. They are off by default. |
| 10 | Is Hermes-Relay a backend | Not checked. Nothing in the build depends on it. |

## What changed from the brief

- **Routing reads the session folder, not the `cwd` argument.**
- **Path translation is required.** On the host, the shared folder
  resolves to a different path than on the laptop.
- **File paths resolve per environment by class name.** Hermes picks
  its path rules from the environment's class name. A class with `ssh`
  in its name gets remote path rules, and one with `local` gets host
  rules. The two target classes are named to match. Without this, a
  macOS brain rewrites every `/home/...` path before it is sent.
- **`plugin_api.py` lives in `dashboard/`.** Hermes mounts it from
  `dashboard/manifest.json`.
- **The desktop half does not travel over SSH.** It has to be copied
  to the machine that runs Hermes Desktop.
- **A failed command must return a result, not raise.** The terminal
  tool retries any exception three times, with waits of 2, 4 and 8
  seconds. Raising would retry a command, which the brief forbids.
- **The prompt-time probe is declined.** Hermes caches its answer
  across sessions, so one session's machine would be described to the
  next. A per-session prompt section names the target instead.

## Known gaps

- Guards keyed on the backend name `local` do not run for the `mac`
  target: the self-repo guard and the locked-skill guard. One backend
  per profile is the cause. Upstream issue #1855 tracks it.
- `execute_code` treats every backend but `local` as remote.
- File tools on the SSH target lose LSP diagnostics and host search.
- Hermes cannot tell a read from a write at the backend seam. The
  plugin retries its own probes and nothing else.

## Found after Phase 0, by running it

These came out of the end-to-end rig, not out of reading source.

- **Every client attaches to one shared gateway.** The CLI, the TUI
  and the ACP adapter for the test profile all run their turns in
  the default profile's gateway process. A plugin enabled later is
  loaded there by `hermes plugins enable`, which asks the gateway to
  rescan that profile. No restart is needed, and none was done.
- **The terminal and the file tools disagree on the folder.** They
  record and read it under different keys. A hook copies it across.
- **A slash command is given no folder.** `/onsite` uses the folder
  noted at the session's first tool call or first prompt.
- **A switch leaves the old machine's folder behind.** `/onsite
  <target>` has to clear Hermes' folder record too.
- **Editors preview edits on the host.** Over ACP, a `patch` to a
  file only the target has is refused. Not worked around.
- **Hermes' plugin scanner reads the whole tree.** A test container
  that wrote the per-user SSH key file would have blocked install.

Each has an entry in `DECISIONS.md`, and the ones that are Hermes'
to fix are drafted in `docs/upstream.md`.

## Found on the real laptop

Nov. 22, after the owner fixed SSH. `rig/run.py --real` ran every
check against the laptop: Arch Linux, a zsh login shell, reached over
a Tailscale link. All 76 pass. The plugin's code did not change. The
rig's did.

- **A stock Arch install has no `hostname` program.** The rig's
  scripts called it. They now call `uname -n`. The plugin never did.
- **The PATH gap is real.** The laptop's interactive shell adds 10
  folders that a plain SSH shell does not have. All 10 reach the
  session.
- **systemd gives an SSH login a delay lock, not a blocking one.**
  The blocking lock is refused: it "requires interactive
  authentication". The plugin fell back to the delay lock, as built.
  A delay lock holds sleep off for `InhibitDelayMaxSec`, which is 5
  seconds on this laptop. A long write can still be cut by a closing
  lid. The lock is gone once the command ends.
- **The numbers held over a real link.** A probe takes 155 to 305
  ms. A refused connection is reported in 0.03 seconds, a frozen
  link in 3.1, and a link that freezes just before a call in 7.0.
  That last one still misses the five-second bar.
- **The owner's own SSH setup is enough.** With no `key` and no
  `known_hosts` in the settings, the test profile reached the laptop
  through the `Host` block in the owner's SSH config. One command
  from a real model, started in the host's mount of the shared
  folder, ran in the laptop's copy of it as the laptop's user.

What this run does not show: the outages were faked by a relay on
the host, so no laptop really slept. And no person sat at a client.

## Blockers for the owner

- **None for the build.** SSH to the laptop works as of Nov. 22.
- **Gates 2 and 3 need a person.** The test profile is set up for
  them. See `DECISIONS.md` entry 41.

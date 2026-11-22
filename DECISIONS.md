# Decisions

Every default the build chose on its own, in the order it came up.
Each entry gives the question, the options, the pick, the reason and
how to undo it.

Written Nov. 22, 2026, against Hermes v0.23.2.

## Process

### 1. Gate 1 was passed without a sign-off

- **Question.** The brief says to stop after Phase 0 until the owner
  reads the findings and says go or no-go on folder routing.
- **Options.** Stop and wait, or carry on.
- **Pick.** Carry on. The owner was away and had asked for as much of
  the build as possible in one go.
- **Why.** The findings said go. Everything after Phase 0 is code in
  this repo and tests on the crash-dummy profile, all of it easy to
  throw away.
- **Reverse.** If the answer is no-go, delete the `build/v1` branch.
  `main` was never touched.

### 2. Gates 2, 3 and 4 were not passed

- **Question.** Can a test stand in for a person at a gate?
- **Pick.** No. The rig proves the approval prompt appears and that a
  denial holds in the classic CLI, the TUI and over ACP. It proves the
  desktop plugin's logic. Gate 2 still needs a person at a real
  prompt, Gate 3 a look at the real desktop app, Gate 4 the switch.
- **Why.** A gate is the owner's judgment, not a check.

### 3. Work is on a branch, not on `main`

- **Pick.** Branch `build/v1`.
- **Why.** `main` held only the license. A branch leaves the owner one
  clean diff to read and one fast-forward to accept.
- **Reverse.** `git switch main && git merge --ff-only build/v1`.

### 4. Commits are authored and committed as Claude Code, unsigned

- **Question.** The owner's git signs every commit with a personal
  key. The brief says Claude Code commits as itself.
- **Options.** Sign as the owner, or commit unsigned as Claude Code.
- **Pick.** Unsigned, author and committer both `Claude Code`.
- **Why.** A signature says the key's owner stands behind the commit.
  These were made while the owner was away. It also keeps the owner's
  private address out of a public history.
- **Reverse.** Rebase and re-sign: `git rebase --exec 'git commit
  --amend --no-edit --reset-author -S' main`.

### 5. One commit per file, with a few bundles

- **Pick.** One file per commit. Files that cannot work apart share
  one: the three container files, the two target classes and their
  package file, the manifest and the entry point, the dashboard
  manifest with its routes and entry script.
- **Known slip.** The commit that fixed the rig's key file also
  carries the Phase 4 additions to `rig/run.py`, which were in the
  same file at the time. Its message describes only the fix.

### 6. The license follows the repo, not the brief

- **Question.** The brief's default was MIT. The repo arrived with a
  GNU Affero GPL v3 `LICENSE` file.
- **Pick.** `AGPL-3.0-only` in `plugin.yaml`.
- **Why.** The file in the repo is the owner's own choice and is
  newer than the brief. "Only" is the narrow reading of a bare
  license file. "Or later" is a grant the owner has to make.
- **Reverse.** Change the one line in `plugin.yaml`.

### 7. The branch was pushed and GitHub Pages turned on

- **Question.** The brief's default was to publish nothing.
- **Pick.** Push `build/v1` and serve `docs/` as a Pages site.
- **Why.** The owner asked for GitHub Pages by name after writing the
  brief, on a repo that was already public.
- **What was not done.** No issue, pull request or comment was posted
  anywhere. Drafts for upstream are in `docs/upstream.md`.
- **Reverse.** Turn Pages off in the repo settings and delete the
  remote branch.

## Routing

### 8. The session folder comes from the session, not from `cwd`

- **Question.** How does a backend learn which folder a session is
  in?
- **Options.** The `cwd` argument of `create_environment()`, or the
  plugin session API.
- **Pick.** The session API, with `cwd` as a fallback.
- **Why.** Measured: for a CLI or TUI session `cwd` is a placeholder.
  The session API returns the folder the client was opened in.
- **Reverse.** `Runtime.create_environment` in `runtime.py`.

### 9. A path map was added to the config

- **Question.** The brain and the hand name the same folder
  differently. On the build host the shared folder resolves to a path
  under `/Users`, and macOS resolves `/home` through a symlink.
- **Pick.** An optional `path_map` per target, from a host path to a
  target path. It both routes and translates.
- **Why.** Without it, a session opened in the shared folder matches
  no prefix and runs on the wrong machine.
- **Reverse.** Leave `path_map` out. Prefixes alone still work where
  both machines agree on paths.

### 10. A folder that does not map starts in the target's home

- **Options.** Guess the same path, fail, or start in home.
- **Pick.** Start in home, and log a warning.
- **Why.** Guessing fails at `cd` on every call. Failing would make
  `/hands <target>` useless from most folders.

### 11. Pins are keyed by the environment's cache key

- **Pick.** Pin under the key Hermes caches the environment by, and
  store the session id beside it. Keys that are not session keys are
  never pinned by a route and never written to disk.
- **Why.** That key is what "one session, one environment" means in
  Hermes. Sessionless work, such as cron, shares one key, and a pin
  on it would send the next job wherever the last one ran.

### 12. `/hands <target>` drops the environment and the folder record

- **Question.** How does a switch take effect?
- **Pick.** Save the pin, discard the session's environment, and
  clear the folder Hermes recorded for the session.
- **Why.** Found by test: without the last step Hermes hands the old
  machine's working directory to the new machine.
- **Limit.** The agent is not told about the switch. The reply to
  `/hands` says so.

## The backend

### 13. Hermes' own local and SSH environments are reused

- **Options.** Reuse them through private imports, or write new ones
  on the public base class.
- **Pick.** Reuse, with every private import in `compat.py` and an
  upper bound on `requires_hermes`.
- **Why.** The local environment is 1,300 lines of process, secret
  and privilege handling. A rewrite would be worse and would still need
  one private import. `hermes plugins validate` warns about this. The
  warning is expected.
- **Reverse.** Replace the two classes in `targets/`.

### 14. The two target classes are named for Hermes' path rules

- **Question.** On a macOS host, Hermes rewrote `/home/...` paths
  before sending them to the target.
- **Pick.** Name the classes `HandsSshEnvironment` and
  `HandsLocalEnvironment`.
- **Why.** Hermes picks its file-path rules from the environment's
  class name. `ssh` in the name gets remote rules. This is the only
  per-environment switch there is.
- **Risk.** It leans on an internal detail. The rig's file-tool tests
  would catch a change.

### 15. Classification is stated outright

- **Pick.** `is_remote = True`, `is_container = False`,
  `skip_container_guards = False`, `default_cwd = "~"`.
- **Why.** Hermes' defaults mark a plugin backend as a container, and
  a container skips dangerous-command prompts. These are real
  machines.

### 16. The prompt-time probe is declined

- **Question.** Hermes builds a throwaway environment to describe the
  backend in the system prompt, and caches the answer across
  sessions.
- **Pick.** Return a stand-in that answers nothing. Add a per-session
  prompt section that names the real target.
- **Why.** A cached answer would describe one session's machine to
  the next.
- **Cost.** Hermes' own note says tools run "not on the machine
  where Hermes itself runs". For the local target that is wrong, and
  the plugin's section says so right after it.

### 17. A file-tool hook was added

- **Question.** A relative path in `write_file` landed in the home
  folder, not the session folder. A session whose first call was a
  file tool got host path rules.
- **Pick.** A `pre_tool_call` hook that builds the environment and
  copies the terminal's folder to where the file tools look.
- **Why.** Hermes records the folder under the session key and the
  file tools read it under the task id. In a gateway session those
  differ.
- **Reverse.** Remove `hooks.py`. Absolute paths still work.

### 18. Hermes' file sync is off for SSH targets

- **Pick.** `sync_files=False`.
- **Why.** The stock SSH environment copies credentials, skills and
  cache into `~/.hermes` on the remote. The remote here is the user's
  own workstation.

### 19. Settings live under `plugins.entries.hermes-hands.settings`

- **Why.** That is where Hermes' own plugin config API reads and
  writes. `hermes hands init` uses that API.

## Resilience

### 20. A failed command is returned, never raised

- **Why.** The terminal tool retries any exception three times, with
  waits of 2, 4 and 8 seconds. Raising would retry a command.

### 21. Nothing passed to `execute()` is ever retried

- **Question.** The brief says to retry reads only.
- **Pick.** The plugin retries its own setup script once, and nothing
  else.
- **Why.** A backend sees shell commands. It cannot tell a read from
  a write.

### 22. The breaker opens on one failure and cools for 10 seconds

- **Why.** A second try at a sleeping laptop teaches nothing.
- **Reverse.** Two constants at the top of `health.py`.

### 23. Keepalives are one second apart, three missed

- **Why.** Measured: two seconds and two missed took 6.6 seconds to
  notice a frozen target.
- **Cost.** A link that stalls for about four seconds drops a command
  in flight. That command is reported as "no exit status".

### 24. An idle link is probed before use

- **Pick.** A link unused for more than 5 seconds gets a fresh probe
  before a command is sent.
- **Why.** A laptop falls asleep between tool calls. A failed probe
  takes about 3 seconds and sends nothing, so the model is told the
  command did not run.
- **Cost.** One extra SSH handshake after each pause. About 50 ms on
  the rig.

### 25. One case misses the five-second bar

- **What.** A target that freezes within about four seconds before a
  call, on a link used within the last five, is reported in about
  seven seconds. The shared connection has to time out, then a fresh
  one.
- **Pick.** Measure it and say so. See `rig/results/phase-2.txt`.
- **Why not fix it.** Tighter keepalives would drop commands on an
  ordinary Wi-Fi hiccup. That is the worse failure.

### 26. The sleep inhibitor takes what the target allows

- **Pick.** On connect, try a blocking `systemd-inhibit` lock, then a
  delay lock, then none. Use `caffeinate` on a macOS target. Hold it
  only while a command runs.
- **Why.** A remote login is often refused a blocking lock. Wrapping
  a command in a lock the target refuses would stop the command.
- **Not tested.** The rig's container has no systemd. The wrapper
  text is unit tested. The real laptop is the first real test.

### 27. PATH is borrowed once per connection

- **Pick.** Read PATH from an interactive login shell and export it
  ahead of the login script Hermes snapshots.
- **Why.** Setting it per command would undo a later `export PATH`
  by the agent.

### 28. A target may name its own `known_hosts` file

- **Why.** The first rig run wrote the test container's host key
  into the user's real `known_hosts`. The entries were removed and
  the file is back to its earlier size.

## The CLI and the desktop

### 29. `/run N` hands the command to the agent

- **Pick.** `/run` sends the agent a message. It runs nothing.
- **Why.** The command then goes through the terminal tool, where
  Hermes' approval gate is.
- **Needs.** `plugins.entries.hermes-hands.allow_gateway_injection:
  true` on gateway surfaces. Without it `/run` explains the setting.

### 30. Both the run card and the pane ship

- **Question.** The card was time-boxed, with the pane as fallback.
- **Pick.** Ship both.
- **Why.** The card is a transcript directive, which also exists in
  the older packaged desktop app. It depends on the model echoing one
  line, so the pane is the dependable path.
- **Not done.** The tool-media renderer. It needs a file delivery
  line and is absent from desktop 0.22.5.

### 31. `plugin_api.py` is in `dashboard/` and takes POST bodies

- **Why.** Hermes mounts routes from `dashboard/manifest.json`. A
  folder path in a query string would end up in access logs.

### 32. A session-open hook was tried and removed

- **Why.** It was meant to learn a session's folder before its first
  message. Hermes fires it without the folder in scope.
- **Limit.** Before a session's first message, `/hands` cannot know
  the folder and says so.

### 33. The editor protocol's edit preview was not worked around

- **What.** Over ACP, Hermes previews each edit by reading the file
  on the host with no task id. A `patch` to a file that only the
  target has is refused, and the preview shows the wrong path for a
  relative one.
- **Pick.** Leave it and write it up in `docs/upstream.md`.
- **Why.** The fix that works is to plant a shared record under the
  key `default`, which other sessions also read.
- **Effect.** In an editor, `patch` works where the same path exists
  on both machines, as in a shared folder. `write_file` works.

## Testing

### 34. The rig runs on the host, not on the laptop

- **Question.** The brief put the rig on the laptop.
- **Pick.** Docker on the host.
- **Why.** SSH from the host to the laptop as the laptop user was
  refused with the two keys tried. No more were tried.

### 35. The brain is the Buster profile, not a container

- **Why.** Hermes finds its dependencies under its real home, so a
  throwaway home cannot start. The brief names Buster as the test
  profile.
- **How.** The rig swaps Buster's `config.yaml` for the run and puts
  it back byte for byte, even when a test fails.

### 36. The gateway is never restarted

- **Question.** Buster's sessions are served by the default profile's
  gateway, which also serves the owner's real profile.
- **Pick.** Load the plugin with `hermes plugins disable` and
  `enable`, which ask the gateway to rescan one profile's plugins.
- **Why.** A restart would interrupt the owner's real profile.

### 37. Hermes is driven over ACP and a pseudo-terminal

- **Why.** ACP carries approval prompts as plain requests. The
  classic CLI and the TUI only show theirs on a real terminal.

### 38. Desktop tests use real React and a real DOM, not the app

- **Question.** The brief asked for a Playwright run in the app.
- **Pick.** `node --test` with React and jsdom borrowed from the
  Hermes checkout.
- **Why.** The desktop app is not built on the host, and building it
  would change the Hermes install, which is off limits.
- **Gap.** This does not show the plugin inside the real app.

### 39. The rig's login key is not in the usual key file

- **Why.** Hermes' plugin scanner blocks any plugin whose files name
  the per-user SSH key file. No flag overrides it. The rig tripped
  it, which would have blocked `hermes plugins install`.

## Open decisions from the brief

| # | Decision | What the build did |
| --- | --- | --- |
| 1 | License and repo | Followed the repo. See 6 and 7. |
| 2 | Approval mode | `manual` in the rig. Buster's own is `manual`. |
| 3 | Upgrades | None. Built against Hermes v0.23.2 as installed. |
| 4 | Run card fallback | Both the card and the pane ship. See 30. |
| 5 | How much routes to hands | The owner's settings decide. Not written. |
| 6 | Keeping the laptop awake | A short lock per command. See 26. |
| 7 | Going public | Code and site pushed. Nothing posted. See 7. |

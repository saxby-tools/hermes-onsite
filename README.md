# hermes-hands

hermes-hands makes a remote
[Hermes](https://github.com/NousResearch/hermes-agent) agent act on
the machine you are sitting at.

The agent's loop, memory and skills stay on the host. That is the
brain. Shell commands and file edits run on a machine you name, as
you. That is the hands. Open a project folder and the agent works in
it, on your machine, with Hermes' own approval prompts still in front
of every risky command.

It is a Hermes plugin. Hermes itself is not patched.

**Status: v0.1.0, built in one pass and not yet accepted.** Phases 0
to 4 of the build pass on a test rig. The last step, a run from a real
laptop, has not happened. See [What is tested](#what-is-tested).

Site: <https://saxby-tools.github.io/hermes-hands/>

## How it works

```text
  the machine you sit at                    the always-on host
  ----------------------                    ------------------
  Hermes CLI, TUI, Desktop  --- chat --->   Hermes agent loop
  or an editor                              memory, skills, model
                                                   |
                                            approval gate (stock)
                                                   |
                                            hands backend (this plugin)
                                              |              |
  your shell and files  <--- SSH, as you ---  laptop       mac
                                              target      target
                                                         (local)
```

One backend plugin routes each session's tool calls to a named
target. The clients, the agent loop and the approval gate are stock
Hermes.

A session picks its target once, from the folder it was opened in:

1. A target set with `/hands <target>` wins for that session.
2. Otherwise the longest matching path wins.
3. Otherwise the default target is used.

A session never moves to another machine by itself. If its target is
down, tool calls fail with an error that names the target.

## Requirements

- Hermes v0.23.2. The plugin refuses to load on 0.24 or later until it
  has been run against it. See [Limits](#limits).
- `plugins.isolation: in_process`, which is Hermes' default. A
  terminal backend cannot run in the plugin host.
- For an SSH target: key-based SSH from the host to that machine with
  no prompt, and `bash` on that machine.
- No Python packages. It drives the `ssh` program you already have.

## Install

On the host, in the Hermes home of the profile that will use it:

```bash
git clone https://github.com/saxby-tools/hermes-hands.git "$HERMES_HOME/plugins/hermes-hands"
```

```bash
hermes plugins enable hermes-hands
```

A profile loads plugins only from its own `plugins/` folder, so a
named profile needs its own copy or link.

`hermes plugins install` from the Git URL should also work. It has
not been tried.

## Set up

Name your targets. This is a dry run that prints the settings:

```bash
hermes hands init --ssh laptop=you@laptop.example --local mac --prefix laptop=/home/you/
```

Add `--apply` to write them. With no flags, on a terminal, it asks
instead.

Then select the backend and check it:

```bash
hermes config set terminal.backend hands
```

```bash
hermes hands status
```

The settings land in `config.yaml`:

```yaml
terminal:
  backend: hands

plugins:
  enabled:
    - hermes-hands
  entries:
    hermes-hands:
      allow_gateway_injection: true   # lets /run hand a command to the agent
      settings:
        default: mac
        targets:
          laptop:
            kind: ssh
            host: laptop.example
            user: you
            prefixes:
              - /home/you/
            path_map:
              /Users/you/Development: /home/you/Development
          mac:
            kind: local
```

### Target settings

| Key | For | Meaning |
| --- | --- | --- |
| `kind` | all | `ssh` or `local`. |
| `host`, `user` | ssh | Where to log in, and as whom. |
| `port` | ssh | Default 22. |
| `key` | ssh | A private key file. Default: your SSH setup decides. |
| `known_hosts` | ssh | A known-hosts file for this target alone. |
| `prefixes` | all | Sessions opened under these folders go to this target. |
| `path_map` | all | A folder on the host, and the same folder on the target. |
| `path` | ssh | `interactive` (default) or `none`. See below. |
| `inhibit_sleep` | ssh | Default true. See below. |
| `connect_timeout` | ssh | Seconds. Default 3. |

**`path_map`** is for when the two machines name one folder
differently. A host that mounts your laptop's `~/Development` at
`/Users/you/Development` needs the map above. It both routes the
session and translates the folder.

**`path: interactive`** reads PATH once per connection from an
interactive login shell on the target. A plain SSH shell skips
`.zshrc` and `.bashrc`, where many people build PATH, so tools such
as `node` would otherwise be missing.

**`inhibit_sleep`** holds a sleep inhibitor on the target while a
command runs, so a closing lid cannot cut a write in half. It uses
`systemd-inhibit` or `caffeinate` when the target has one and allows
it. It is never held between commands.

## Use

| What | CLI and TUI | Hermes Desktop |
| --- | --- | --- |
| See where tools run | `/hands`, or `hermes hands status` | A chip on the right of the status bar. |
| Move this session | `/hands <target>`, back with `/hands auto` | Same, typed in the chat. |
| Approve a risky command | Hermes' own prompt | Hermes' own prompt. |
| Run a command the agent proposed | `/run <number>` | Run and Copy buttons, in a pane and in the transcript. |

The agent has a tool, `propose_command`. It saves a command with a
number and runs nothing. `/run N` then asks the agent to run it with
the terminal tool, so the approval prompt still applies.

### The desktop half

Hermes Desktop loads its plugin half from its own machine. When the
app connects to a host over SSH, copy one file to the machine the app
runs on:

```bash
mkdir -p ~/.hermes/desktop-plugins/hermes-hands
```

```bash
scp host.example:'$HERMES_HOME/plugins/hermes-hands/desktop/plugin.js' ~/.hermes/desktop-plugins/hermes-hands/plugin.js
```

Then run "Reload desktop plugins" from the command palette. The
folder must be named `hermes-hands`.

## When a target is down

- A tool call returns an error that names the target, within about
  three seconds. No command is sent.
- Later calls fail at once for ten seconds, then one call checks
  again. Service resumes by itself when the target is back.
- A link lost while a command is running is reported as "no exit
  status". The command may have run. It is never retried, because a
  retry could repeat a write.

The agent is told the same thing in plain words, and told not to work
around it.

## Limits

Read these before trusting it with real work.

- **No sandbox.** An SSH target can do anything its user can.
  Hermes' approval prompts are the only gate. Keep them on.
- **Private Hermes APIs.** The plugin reuses Hermes' own local and
  SSH environments, which are not public plugin API. All such imports
  are in `compat.py`, and `requires_hermes` stops the plugin loading
  on a Hermes it has not been run against.
- **Editors over ACP.** Hermes previews an edit by reading the file
  on the host. In an editor such as Zed, `patch` only works where the
  same path exists on both machines, as in a shared folder.
  `write_file` and the terminal work everywhere.
- **Before the first message,** `/hands` cannot know a session's
  folder and says so.
- **After `/hands <target>`,** the agent is not told. Say so in your
  next message if it matters.
- **One slow case.** A target that freezes in the few seconds before
  a call can take about seven seconds to report, not three.
- **Host-only guards.** Hermes guards keyed on the backend name
  `local`, such as the self-repo guard, do not run for a `local`
  target here.
- **Lost on SSH targets.** LSP diagnostics and host-side search
  speedups. `/rollback` checkpoints run on the host and do not cover
  a remote target.

[DECISIONS.md](DECISIONS.md) has the reason for each.

## What is tested

| Phase | What | Checks | Result |
| --- | --- | --- | --- |
| 1 | Routing, file tools, approvals | 18 | Pass |
| 2 | A stopped, a frozen and a returning target | 20 | Pass |
| 3 | `/hands`, `/run`, `hermes hands`, CLI and TUI | 28 | Pass |
| 4 | Status routes, desktop plugin logic | 7 | Pass |
| 5 | A real laptop, real entry points | 0 | Not run |

The output of each run is in [rig/results/](rig/results/).

Not yet tested by anyone:

- A real laptop over a real network. The build could not log in to
  one.
- The desktop plugin inside the real Hermes Desktop app.
- The sleep inhibitor on a machine that has `systemd`.
- `hermes plugins install` from the Git URL.

### Run the tests

Unit tests need nothing but `pytest`:

```bash
uv run --no-project --with pytest python -m pytest
```

The desktop tests borrow React and jsdom from a Hermes checkout:

```bash
node --test tests/desktop.test.mjs
```

The rig needs Docker and a Hermes test profile. It is a dry run
unless `--apply` is given:

```bash
python3 rig/run.py
```

```bash
python3 rig/run.py --apply
```

It swaps the test profile's `config.yaml` for the run and puts it
back at the end. It never restarts the Hermes gateway.

## Layout

```text
plugin.yaml          the manifest
__init__.py          register(ctx)
compat.py            every import from Hermes, in one place
config.py            parse and validate the settings
routing.py           pick a target from a folder
runtime.py           shared state: pins, breakers, status
provider.py          the `hands` terminal backend
targets/             one class per kind of target: ssh, local
health.py            probe and circuit breaker
remote.py            SSH options, PATH, sleep inhibitor
exits.py             the four ways a command can end
hooks.py             keeps the file tools on the right folder
commands.py          /hands, /run, hermes hands
tools.py             propose_command
prompt.py            the per-session note in the system prompt
messages.py          every string shown to a person or the model
wizard.py            hermes hands init
api.py               what the desktop half is told
dashboard/           the HTTP routes Hermes mounts
desktop/plugin.js    status chip, commands pane, run card
tests/               unit tests
rig/                 the end-to-end rig and its saved results
docs/                the site, the Phase 0 findings, upstream drafts
```

## License

GNU Affero General Public License v3. See [LICENSE](LICENSE).

Built by Claude Code from a brief. Every commit says so.

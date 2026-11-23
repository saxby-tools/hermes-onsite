# hermes-hands

![hermes-hands: a marble head and a marble hand, drawn in blue](docs/brand/banner.png)

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
to 4 of the build pass on a test rig, against a container and against
a real laptop. The last step, a person using it from the real clients,
has not happened. See [What is tested](#what-is-tested).

Site: <https://saxby-tools.github.io/hermes-hands/>

## How it works

<!-- hands:diagram {"caption": "Chat goes to the host. Tool calls come back past the gate."} -->

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

<!-- /hands:diagram -->

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
  no prompt, and `bash` on that machine. A machine the host cannot
  reach can dial out to it. See
  [A machine the host cannot reach](#a-machine-the-host-cannot-reach).
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
| `mirror` | ssh | A folder on the host that holds the target's folder names. See below. |
| `mirror_depth` | ssh | Levels under the target's home to mirror. Default 3. |

**`path_map`** is for when the two machines name one folder
differently. A host that mounts your laptop's `~/Development` at
`/Users/you/Development` needs the map above. It both routes the
session and translates the folder.

**`path: interactive`** reads PATH once per connection from an
interactive login shell on the target. A plain SSH shell skips
`.zshrc` and `.bashrc`, where many people build PATH, so tools such
as `node` would otherwise be missing.

**`inhibit_sleep`** holds a sleep inhibitor on the target while a
command runs. It uses `systemd-inhibit` or `caffeinate` when the
target has one and allows it. It is never held between commands.

On a `systemd` laptop, expect less than the name promises. An SSH
login is refused a blocking lock and gets a delay lock, which holds
sleep off for five seconds by default (`InhibitDelayMaxSec` in
`logind.conf`). That covers a short write. A long one can still be
cut by a closing lid.

### A machine the host cannot reach

A laptop behind a home router cannot be dialed from the host. It can
dial out. The machine opens one SSH connection to the host and offers
its own SSH server back through it. The host then reaches the machine
at a port on its own loopback address.

This prints the command to run on the machine, a service file that
keeps the link up, and the target's settings. It is a dry run until
you add `--apply`, which writes the settings:

```bash
hermes hands dial-out laptop --server you@host.example --user you
```

The machine still needs its own SSH server, but only the host can
reach it, through the link. The plugin adds no listener: the host's
SSH server opens the port, on loopback only. When the link drops,
tool calls fail at once and resume when the machine dials back in.

### Folders that exist only on the target

Hermes only runs a session in a folder the host can see, and the
desktop app's folder picker browses the host. A folder on your laptop
is not on the host. A mirror gives it an address there.

A mirror is a folder on the host that copies the target's folder
names and nothing else: no files, ever. Open a session in a mirrored
folder and it runs in the real folder on the target.

Name the mirror when you set up, or add `mirror` to the target:

```bash
hermes hands init --ssh laptop=you@laptop.example --local mac --mirror laptop=/Users/you/Hands/laptop
```

Then copy the folder names. Both commands are dry runs until you add
`--apply`:

```bash
hermes hands mirror --apply
```

That covers the folders under the target's home, three levels deep,
and skips any whose name starts with a dot. For one folder at any
depth:

```bash
hermes hands open laptop:~/work/site --apply
```

It prints the folder to open on the host. The mirror is only an
address. The app's file tree shows its empty folders, not the
target's files, and anything the app itself writes there stays on the
host. The plugin never deletes from a mirror and never makes one
outside the folder you named.

### MCP servers on a target

An MCP server can run on a target, so its tools act there. Hermes
starts a local MCP server by running a command, and that command can
be `ssh`. This shows the call, and adds the server with `--apply`:

```bash
hermes hands mcp laptop files --apply -- npx -y @modelcontextprotocol/server-filesystem /home/you
```

The server is named for its target, here `laptop_files`, and Hermes
lists its tools as `mcp__laptop_files__...`. To Hermes it is an
ordinary MCP server, so its own approval prompts apply. No tool call
is rerouted.

Hermes loads MCP servers for a whole profile, so every session sees
these tools. The plugin refuses one in a session that runs on another
machine, and tells the agent to say so. A session never acts on a
second machine by itself.

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
- **The folder must exist on the host.** Hermes will not run a turn
  in a folder the host cannot see, and the desktop app's folder
  picker and file tree read the host's disk. To work in a folder on
  an SSH target, the host needs its own view of that folder: a
  `mirror` of its folder names, or a network mount with a `path_map`
  entry. With a mirror, the app's file tree shows folders and no
  files.
- **The Bots tab in Hermes Desktop.** The app sends the chip's
  requests to its active profile, which is set in the Sessions tab.
  With a bot's chat open in the Bots tab, the chip can describe a
  different profile. It names the one it asked.
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
| 5 | A person at the real clients | 0 | Not run |

The same checks pass twice: against a container on the host, and
against a real Linux laptop over a real network. The laptop run adds
three checks of the sleep lock, for 76 in all. The output of each run
is in [rig/results/](rig/results/). The laptop's files start with
`real-`.

One real-model command has also run from the host's copy of a shared
folder and landed on the laptop, as the laptop's user.

Hermes' own check, `hermes plugins validate`, passes on a clean copy
of the tree. It warns that the plugin imports private Hermes modules,
which [Limits](#limits) covers.

<!-- hands:notice {"kind": "untested"} -->

Not yet tested by anyone:

- A laptop that really sleeps or drops off the network. The rig
  fakes both by cutting a relay in front of the laptop's SSH port.
- A person approving a real command in the TUI, the desktop app and
  an editor.
- The run card and its Run button inside the real Hermes Desktop
  app. The chip and the pane have been seen there, on a gateway that
  serves several profiles, with the laptop shown as connected.
- A macOS machine as an SSH target, and `caffeinate`.
- `hermes plugins install` from the Git URL.

<!-- /hands:notice -->

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

To use a real machine as the hands, name it. It must take your SSH
key with no prompt. The rig writes only under
`~/.cache/hermes-hands-rig` there and removes it at the end:

```bash
python3 rig/run.py --apply --real you@laptop.example
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
mirror.py            a names-only copy of a target's folders
dialout.py           set-up text for a machine that dials out
mcptools.py          MCP servers that run on a target
exits.py             the four ways a command can end
hooks.py             file tools on the right folder, MCP tools
                     on the right machine
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
docs/                the design page, brand files, findings, drafts
site/                the site's build: Eleventy, layouts, styles
```

## License

Copyright (C) 2026 saxby-tools.

hermes-hands is free software. You can redistribute it and modify it
under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version. See [LICENSE](LICENSE).

It comes with no warranty.

In short: you may use, study, change and share it. If you share a
changed copy, or run one as a service for other people, you must
offer them its source under the same license. Hermes itself is MIT
licensed, which allows a plugin under these terms.

Built by Claude Code from a brief. Every commit says so.

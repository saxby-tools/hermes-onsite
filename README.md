# onsite

Open a project folder on your own computer and your always-on
[Hermes](https://github.com/NousResearch/hermes-agent) works in it.

Hermes stays on your server. Its commands and file edits run on the
computer the folder lives on, as you. It is the way of working you
know from Claude Code, Codex and the other big coding apps, for a
Hermes that lives somewhere else.

- **Open a folder. That is the whole switch.** The folder you open
  picks the computer. There is no switch phrase and no mode.
- **Approve where you are.** Risky commands stop at Hermes' own
  prompt, in the app you are holding. A chip in the desktop app names
  the computer in use.
- **Nothing new to install on your computer.** It uses the SSH you
  already have. Either side can start the link.

It is a Hermes plugin. Hermes itself is not patched.

**Status: v0.1.0, built in one pass and not yet accepted.** Phases 0
to 4 of the build pass on a test rig, against a container and against
a real laptop. The last step, a person using it from the real clients,
has not happened. See [What is tested](#what-is-tested).

Site: <https://saxby-tools.github.io/hermes-onsite/>

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
                                            onsite backend (this plugin)
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

1. A target set with `/onsite <target>` wins for that session.
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

On the host, for the profile that will use it:

```bash
hermes plugins install saxby-tools/hermes-onsite/onsite
```

That installs one folder of this repository, `onsite/`, which is the
plugin. The test rig, the site and these docs stay on GitHub.

A profile loads plugins only from its own `plugins/` folder, so a
named profile needs its own install: add `-p <profile>` after
`hermes`.

To work on the plugin itself, clone the repository and link that one
folder into a profile:

```bash
git clone https://github.com/saxby-tools/hermes-onsite.git
```

```bash
ln -s "$PWD/hermes-onsite/onsite" "$HERMES_HOME/plugins/onsite"
```

```bash
hermes plugins enable onsite
```

The install command has not been run by anyone yet. The link is how
every test here was run.

## Set up

Name your targets. This is a dry run that prints the settings:

```bash
hermes onsite init --ssh laptop=you@laptop.example --local mac --prefix laptop=/home/you/
```

Add `--apply` to write them. With no flags, on a terminal, it asks
instead.

Then select the backend and check it:

```bash
hermes config set terminal.backend onsite
```

```bash
hermes onsite status
```

The settings land in `config.yaml`:

```yaml
terminal:
  backend: onsite

plugins:
  enabled:
    - onsite
  entries:
    onsite:
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
| `mirror` | ssh | Where on the host the target's folder names are kept. Default: the plugin's data folder. |
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
hermes onsite dial-out laptop --server you@host.example --user you
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

Every SSH target has a mirror with no set-up. It is kept in the
plugin's data folder. To keep it somewhere you can browse, name the
place with `mirror` in the target's settings.

In Hermes Desktop, the pane does this for you: pick a machine, browse
its folders, and start a session in one. From a terminal, open one
folder at any depth. It is a dry run until you add `--apply`, and it
prints the folder to start in:

```bash
hermes onsite open laptop:~/work/site --apply
```

To fill a mirror with the folder names under the target's home, three
levels deep, so the app's own folder picker can browse them:

```bash
hermes onsite mirror --apply
```

The mirror is only an address. The app's file tree shows its empty
folders, not the target's files, and anything the app itself writes
there stays on the host. The plugin never deletes from a mirror and
never makes one outside its folder.

### MCP servers on a target

An MCP server can run on a target, so its tools act there. Hermes
starts a local MCP server by running a command, and that command can
be `ssh`. This shows the call, and adds the server with `--apply`:

```bash
hermes onsite mcp laptop files --apply -- npx -y @modelcontextprotocol/server-filesystem /home/you
```

The server is named for its target, here `laptop_files`, and Hermes
lists its tools as `mcp__laptop_files__...`. To Hermes it is an
ordinary MCP server, so its own approval prompts apply. No tool call
is rerouted.

Hermes loads MCP servers for a whole profile, so every session sees
these tools. The plugin refuses one in a session that runs on another
machine, and tells the agent to say so. A session never acts on a
second machine by itself.

### Screen control on a target

Hermes' `computer_use` tool can act on a target's screen. It follows
the session: a session on the host keeps the host's screen, and a
session on a target gets that target's.

It works by running Hermes' own screen driver on the target and
talking to it over the SSH link. The plugin never installs that
driver. This checks a target and says what is missing:

```bash
hermes onsite screen laptop
```

Then link the screen backend into place and select it. The first
command is a dry run until you add `--apply`:

```bash
hermes onsite screen-install --apply
```

```bash
hermes config set computer_use.backend onsite-screen
```

Hermes' own approval prompt and hard blocks still stand in front of
every click and keystroke. The prompt does not name the machine, so
check the status chip or `/onsite` when it matters.

This is new and narrowly tested: on a Linux X11 desktop in the test
rig, with Hermes' pinned driver. See [What is tested](#what-is-tested).

## Use

| What | CLI and TUI | Hermes Desktop |
| --- | --- | --- |
| See where tools run | `/onsite`, or `hermes onsite status` | A chip on the right of the status bar. |
| Move this session | `/onsite <target>`, back with `/onsite auto`. The agent is told with your next message. | Same, typed in the chat. |
| Approve a risky command | Hermes' own prompt | Hermes' own prompt. |
| Run a command the agent proposed | `/run <number>` | Run and Copy buttons, in a pane and in the transcript. |
| Open a folder on a machine | `hermes onsite open laptop:~/work --apply` | Browse the machine in the pane and start a session there. |
| Add, change or test a machine | `hermes onsite init`, or edit `config.yaml` | Settings, Plugins, Machines. |

The agent has a tool, `propose_command`. It saves a command with a
number and runs nothing. `/run N` then asks the agent to run it with
the terminal tool, so the approval prompt still applies.

### The desktop half

Hermes Desktop loads its plugin half from its own machine. When the
app connects to a host over SSH, copy one file to the machine the app
runs on:

```bash
mkdir -p ~/.hermes/desktop-plugins/onsite
```

```bash
scp host.example:'$HERMES_HOME/plugins/onsite/desktop/plugin.js' ~/.hermes/desktop-plugins/onsite/plugin.js
```

Then run "Reload desktop plugins" from the command palette. The
folder must be named `onsite`.

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

## What it touches

Read this before you install it. It is the full list.

**On a target.** It runs shell commands and reads and writes files
there, as the account you named, with everything that account may do.
The commands and edits are the ones the agent asks for. Hermes'
approval prompt is the only gate. While a command runs it may hold a
sleep lock (`systemd-inhibit` or `caffeinate`). To check a screen it
reads the environment of your own desktop processes. With screen
control on, it starts Hermes' screen driver there, which can see the
screen and send clicks and keys. It installs nothing on a target.

**On the host.** It starts `ssh` processes: one kept open per target
while sessions use it, one for each MCP server you add, and one per
session that uses a target's screen. It files each target's host key
in your `known_hosts`, on first contact. It writes three small files
under `plugin-data/onsite/` in the profile's Hermes home: which
target each session is pinned to, each session's folder, and the
commands the agent has proposed. A mirror makes empty folders under
the folder you name, and nothing else.

**Your keys.** It uses the `ssh` program and whatever keys your SSH
setup offers. It never reads, copies or stores a key.

**The network.** It opens SSH connections to the targets you name
and nothing else. It sends no telemetry and calls no other service.
It does not update itself.

**Inside Hermes.** It registers a terminal backend, one tool
(`propose_command`), two hooks, two slash commands and one CLI
command. One hook runs before each tool call. It can refuse an MCP
tool that belongs to another machine; it never approves anything. The
other runs before each turn. After `/onsite` moves a session, it adds
one note to your next message that tells the agent which machine its
tools now run on. `/run` puts a message into the chat for the agent,
which needs `allow_gateway_injection: true`. It serves eight routes
to the desktop app under `/api/plugins/onsite/`. Two of them write
the plugin's own settings and one makes mirror folders. The desktop
half adds a status chip, a pane, a run card and a settings page.

**Private Hermes code.** It imports Hermes modules that are not
plugin API, to reuse Hermes' own SSH, local and screen code. They are
listed in `onsite/compat.py` and `onsite/screenbackend.py`. A Hermes
release can
move them, which is why `requires_hermes` has an upper bound.

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
  an SSH target, the host needs its own view of that folder. The
  plugin keeps a mirror of folder names for every SSH target, so any
  folder can be opened. A network mount with a `path_map` entry also
  works. With a mirror, the app's file tree shows folders and no
  files.
- **The Bots tab in Hermes Desktop.** The app sends the chip's
  requests to its active profile, which is set in the Sessions tab.
  With a bot's chat open in the Bots tab, the chip can describe a
  different profile. It names the one it asked.
- **Before the first message,** `/onsite` cannot know a session's
  folder and says so.
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
| 3 | `/onsite`, `/run`, `hermes onsite`, CLI and TUI | 28 | Pass |
| 4 | Routes for status, machines and folders; desktop plugin logic | 17 | Pass |
| Screen | A target's screen: look, deny, approve | 8 | Pass |
| 5 | A person at the real clients | 0 | Not run |

The same checks pass twice: against a container on the host, and
against a real Linux laptop over a real network. The laptop run adds
three checks of the sleep lock, for 86 in all. The screen checks run
on a container with its own desktop. The output of each run
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
- The run card, the folder browser and the Machines page inside
  the real Hermes Desktop app. The chip and the pane have been
  seen there, on a gateway that serves several profiles, with the
  laptop shown as connected.
- A macOS machine as an SSH target, and `caffeinate`.
- `hermes plugins install` of the plugin's folder from GitHub.
- Screen control on macOS, on Wayland, or on any desktop a
  person is using. It has run only against the rig's own X11
  desktop.
- A machine that dials out from its own side. The rig opens the
  same kind of link from the host.

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

To test against a real machine, name it. It must take your SSH
key with no prompt. The rig writes only under
`~/.cache/onsite-rig` there and removes it at the end:

```bash
python3 rig/run.py --apply --real you@laptop.example
```

Screen control is tested on a container with a desktop nobody looks
at and Hermes' own screen driver. Its results start with `desk-`:

```bash
python3 rig/run.py --apply --desk --phase screen
```

It swaps the test profile's `config.yaml` for the run and puts it
back at the end. It never restarts the Hermes gateway.

## Layout

```text
onsite/              the plugin: all that an install puts on a host
  plugin.yaml        the manifest
  __init__.py        register(ctx)
  compat.py          every import from Hermes, in one place
  config.py          parse and validate the settings
  routing.py         pick a target from a folder
  runtime.py         shared state: pins, breakers, status, settings
  provider.py        the `onsite` terminal backend
  targets/           one class per kind of target: ssh, local
  health.py          probe and circuit breaker
  remote.py          SSH options, PATH, sleep inhibitor
  mirror.py          a names-only copy of a target's folders
  dialout.py         set-up text for a machine that dials out
  mcptools.py        MCP servers that run on a target
  screen.py          find a target's desktop and screen driver
  screenbackend.py   Hermes' screen backend, on a target
  onsite-screen/     the folder Hermes finds that backend by
  exits.py           the four ways a command can end
  hooks.py           file tools on the right folder, MCP tools on
                     the right machine, a note after a move
  moves.py           a session's move, held until its agent is told
  commands.py        /onsite, /run, hermes onsite
  tools.py           propose_command
  prompt.py          the per-session note in the system prompt
  messages.py        every string shown to a person or the model
  wizard.py          hermes onsite init
  api.py             what the desktop half is told and may change
  dashboard/         the HTTP routes Hermes mounts
  desktop/plugin.js  chip, pane, run card, folder browser, Machines
tests/               unit tests
rig/                 the end-to-end rig and its saved results
docs/                the design page, brand files, QA journeys, drafts
site/                the site's build: Eleventy, layouts, styles
```

## License

Copyright (C) 2026 saxby-tools.

onsite is free software. You can redistribute it and modify it
under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version. See [LICENSE](LICENSE).

It comes with no warranty.

In short: you may use, study, change and share it. If you share a
changed copy, or run one as a service for other people, you must
offer them its source under the same license. Hermes itself is MIT
licensed, which allows a plugin under these terms.

Built by Claude Code from a brief. Every commit says so.

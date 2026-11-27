# onsite

Open a project folder on your own computer and the Hermes that lives
on your server works in it.

Hermes stays on your server. Its commands and file edits run on the
computer the folder lives on, as you, over the SSH you already have.
The folder you open picks the computer. Risky commands still stop at
Hermes' own approval prompt, in the app you are holding.

**Status: v0.1.0, a first release candidate.** It passes its test
rig against a container and a real Linux laptop. No person has yet
walked it through the real clients. It was built on Hermes 0.23.2.
A later 0.23 release loads it but has not been tried. Hermes 0.24
does not load it.

This folder is the plugin. It is all that an install puts on your
server. The guide, every setting and the test record are on the site
and in the repository:

- <https://saxby-tools.github.io/hermes-onsite/>
- <https://github.com/saxby-tools/hermes-onsite>

## Set up

First, the server must be able to log in to your computer over SSH
with a key and no prompt. `ssh you@laptop.example true`, run on the
server, must return at once. The server runs Linux or macOS. A Linux
server also needs the `sshfs` program. When it is missing, onsite
says so and names the line that installs it.

Then install it on the server. Until it is in the Hermes catalog,
that is:

```bash
hermes plugins install https://github.com/saxby-tools/hermes-onsite/tree/main/onsite --enable
```

Then, still on the server:

```bash
hermes onsite init --ssh laptop=you@laptop.example --local mac --apply
```

```bash
hermes config set terminal.backend onsite
```

```bash
hermes config set plugins.entries.onsite.allow_gateway_injection true
```

```bash
hermes onsite status
```

The first names your computers. `laptop` and `mac` are names you
choose, for your computer and for the server. `you@laptop.example`
is what you would type after `ssh`. Leave off `--apply` to see the
settings without writing them. The second turns onsite on for new
sessions. The third is for commands the agent proposes and waits
on: it lets `/run`, and the Run button in the desktop app, hand one
to the agent. The fourth should show each computer as `up`. In Hermes
Desktop, the first can also be done under Settings, Plugins, Onsite,
then Machines.

To start work, open a folder on your computer. Use one that exists
there. From a terminal on the server, `hermes onsite open
laptop:~/work` prints a folder. Start `hermes` in it and ask it to
run `uname -n`. It should print your computer's name. In Hermes
Desktop, click the onsite chip at the bottom right. A pane opens.
Click "New session" beside your computer. The app's own folder
picker and file tree then show your computer's real files.

Hermes Desktop needs the plugin's desktop part on the computer the
app runs on. In the app, open Capabilities, then Plugins, click
"Install from Git" and paste:

```text
https://github.com/saxby-tools/hermes-onsite/tree/main/onsite/desktop
```

It is done when a chip that starts with "onsite" shows at the bottom
right of the app. If none shows, open Onsite's entry in Capabilities,
Plugins and turn on its Desktop switch.

## What it touches

Read this before you turn it on.

- **It runs the agent's shell commands and file edits on the
  computers you name,** as the account you named, with everything
  that account may do. There is no sandbox. Hermes' approval prompt
  is the only gate. Keep it on.
- **It uses your SSH.** It runs the `ssh` program with whatever keys
  your SSH setup offers. It never reads, copies or stores a key. It
  files each computer's host key in `known_hosts` on first contact.
- **It mounts each computer's disk on the server,** so the server
  and the desktop app see your real files. Anything on the server
  that reads or writes under the mount reads or writes your
  computer's files, as your account there. On a macOS server the
  plugin runs a helper process per computer for this: a small file
  server that listens on `127.0.0.1` only and stays running. The
  folder that holds the mounts is closed to every account but yours.
  On a Linux server it runs `sshfs`, which you install.
- **It keeps `ssh` processes open on the server:** one per computer
  in use, one per mounted computer, one per MCP server you add
  there, and one per session that
  uses a computer's screen. With Hermes Desktop in view, it also
  logs in to the session's computer every five seconds and after
  each tool call, to see that it is up, and to every computer while
  the pane is open.
- **On your computer it installs nothing.** It may hold a sleep lock
  while a command runs. It starts your interactive shell to read
  PATH each time a session's environment is built. Hermes' SSH code,
  which it reuses, writes a small file to `/tmp` there each time,
  holding your shell's exported variables, functions and aliases,
  readable by your account only. The plugin removes it when that
  environment is closed.
- **Screen control is off until you turn it on.** Then it can see a
  computer's screen and send clicks and keys, through Hermes' own
  driver or the screen sharing that computer already runs. It reads
  one optional secret through Hermes, `ONSITE_SCREEN_PASSWORD`, for
  screen sharing that asks for a VNC password.
- **On the server it writes** a few small state files under
  `plugin-data/onsite/`. The mounts are in
  `~/.local/share/onsite/mount/`, or where you name. A place inside
  the Hermes home is refused: deleting a profile there would delete
  your computer's files through the mount. Nothing is added to the
  top of your home folder, and no file is copied.
- **Inside Hermes** it adds a terminal backend, one tool, two hooks,
  two slash commands, one CLI command, a section of the system
  prompt and seven routes for the desktop app. Two routes write the
  plugin's own settings and one mounts a disk. After `/onsite` moves
  a session, it adds one note to
  your next message to tell the agent.
- **It imports private Hermes modules** to reuse Hermes' own SSH,
  local and screen-driver code. A Hermes release can move them,
  which is why it stops loading at Hermes 0.24.
- **No telemetry, no other network calls, no self-update.**

## Limits

- On your computer, Hermes can do anything your account can.
- Hermes only runs a session in a folder the server can see, so the
  plugin mounts your computer's disk there. The mount is a network
  disk: a program on the server that walks a big folder through it
  is slow. Hermes' own commands run on your computer and are not.
- Servers on Linux and macOS only. It refuses to load on a Windows
  server. The Linux mount has run in a container, never on a real
  Linux server.
- When your computer sleeps, the work stops until it is back.
- A real sleeping laptop, a Mac as a target and the phone apps have
  not been tested.
- Screen control has only run on a Linux X11 desktop.

The full lists are in the repository's README, under "What it
touches", "Limits" and "What is tested".

## Environments (not released)

This part is a spike on a branch. It is not in any release, the
guide does not cover it, and it may change or go.

An environment is a third kind of target: a sandbox the server makes
for one session. It is a Docker container on the Hermes server, with
the session's folder shared into it. The work goes on with your own
computer off. It needs Docker on the server, and an image that has
`bash`.

```yaml
targets:
  web:
    kind: environment
    image: debian:stable-slim
    repository: https://host.example/you/project.git
    network: false
    setup: |
      apt-get update && apt-get install -y shellcheck
```

```bash
hermes onsite env new web
```

That prints a new folder on the server, with the repository cloned
into it. Start `hermes` in it. In Hermes Desktop, click "New
session" beside the environment in the pane. The session's first
command makes the sandbox and runs `setup` in it once.
`hermes onsite env list` shows the sessions. `hermes onsite env rm
web <id>` removes one: its sandbox and its folder.

Read this before you try it.

- **The sandbox is a container, not a separate machine.** It shares
  the server's kernel. A flaw in the kernel or in Docker could let
  a command out, as root on the server.
- **Hermes' account needs Docker, and Docker is root.** An account
  that can use Docker can do anything on the server. With it, a
  session on the Hermes server itself can too.
- **The sandbox gets one folder.** Nothing else of the server is
  shared: no part of the Hermes home, no skill, no key. Commands run
  there as the container's root, with most of root's powers taken
  away.
- **`network: true` is the whole network.** That includes the
  server's own network, and any service on the server that listens
  beyond its loopback address. There is no list of allowed sites.
  The default is off, and then `setup` cannot download anything.
- **Nothing caps a sandbox's memory, processor or disk.**
- **The clone uses the server account's own git logins.** The
  plugin reads and keeps none, and none is put in the sandbox. So
  the sandbox cannot push to a private repository, and nothing
  pushes for it yet: the work is the files in the folder. Do not
  put a secret in `env`: every command there can read it.
- **Do not run `git` on the server in a session's folder.** The
  sandbox can write that repository's settings and hooks, and git
  would run them as your account.
- **On a Linux server, files the sandbox makes belong to root.**
  You can read them. To change or delete them, remove the session.
- **A sandbox stays until you remove its session.** A restart of the
  server stops it, and the next command starts it again.
- **In Hermes' Docker image,** name a `workspace` outside
  `/opt/data`, and add it to `HERMES_WRITE_SAFE_ROOT`, or Hermes'
  file tools refuse to write there.

## License

Free software under the GNU Affero General Public License, version 3
or later. See `LICENSE` in this folder. It comes with no warranty.

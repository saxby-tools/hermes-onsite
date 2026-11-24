# onsite

Open a project folder on your own computer and the Hermes that lives
on your server works in it.

Hermes stays on your server. Its commands and file edits run on the
computer the folder lives on, as you, over the SSH you already have.
The folder you open picks the computer. Risky commands still stop at
Hermes' own approval prompt, in the app you are holding.

**Status: v0.1.0, a first release candidate.** It passes its test
rig against a container and a real Linux laptop. No person has yet
walked it through the real clients. It needs Hermes 0.23.2 and does
not load on 0.24.

This folder is the plugin. It is all that an install puts on your
server. The guide, every setting and the test record are on the site
and in the repository:

- <https://saxby-tools.github.io/hermes-onsite/>
- <https://github.com/saxby-tools/hermes-onsite>

## Set up

On the server, after the install:

```bash
hermes onsite init --ssh laptop=you@laptop.example --local mac --prefix laptop=/home/you/ --apply
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

The first names your computers. Leave off `--apply` to see the
settings without writing them. The third lets `/run` hand a command
to the agent. In Hermes Desktop, the same settings are under
Settings, Plugins, Onsite.

Hermes Desktop loads the plugin's desktop part from the computer the
app runs on. When that is not the server, install it there too: in
the app, Capabilities, Plugins, "Install from Git", and enter
`saxby-tools/hermes-onsite/onsite`.

## What it touches

Read this before you turn it on.

- **It runs the agent's shell commands and file edits on the
  computers you name,** as the account you named, with everything
  that account may do. There is no sandbox. Hermes' approval prompt
  is the only gate. Keep it on.
- **It uses your SSH.** It runs the `ssh` program with whatever keys
  your SSH setup offers. It never reads, copies or stores a key. It
  files each computer's host key in `known_hosts` on first contact.
- **It keeps `ssh` processes open on the server:** one per computer
  in use, one per MCP server you add there, and one per session that
  uses a computer's screen. With Hermes Desktop open, it also logs
  in to each computer every five seconds to see that it is up.
- **On your computer it installs nothing.** It may hold a sleep lock
  while a command runs. It starts your interactive shell once per
  link to read PATH. Hermes' SSH code, which it reuses, leaves one
  small file per session in `/tmp` there, holding your shell's
  exported variables, readable by your account only.
- **Screen control is off until you turn it on.** Then it can see a
  computer's screen and send clicks and keys, through Hermes' own
  driver or the screen sharing that computer already runs. It reads
  one optional secret through Hermes, `ONSITE_SCREEN_PASSWORD`, for
  screen sharing that asks for a VNC password.
- **On the server it writes** three small state files under
  `plugin-data/onsite/` and empty folders that mirror a computer's
  folder names. Never a file's contents.
- **Inside Hermes** it adds a terminal backend, one tool, two hooks,
  two slash commands, a section of the system prompt and eight
  routes for the desktop app. Two routes write the plugin's own
  settings. After `/onsite` moves a session, it adds one note to
  your next message to tell the agent.
- **It imports private Hermes modules** to reuse Hermes' own SSH,
  local and screen-driver code. A Hermes release can move them,
  which is why it stops loading at Hermes 0.24.
- **No telemetry, no other network calls, no self-update.**

## Limits

- An SSH target can do anything its user can.
- Hermes only runs a session in a folder the server can see. The
  plugin keeps empty folders that mirror a computer's folder names,
  so any folder can be opened. The app's own file tree then shows
  folders and no files.
- A real sleeping laptop, a Mac as a target and the phone apps have
  not been tested.
- Screen control has only run on a Linux X11 desktop.

The full lists are in the repository's README, under "What it
touches", "Limits" and "What is tested".

## License

Free software under the GNU Affero General Public License, version 3
or later. See `LICENSE` in this folder. It comes with no warranty.

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
server, must return at once.

Then install it on the server. Until it is in the Hermes catalog,
that is:

```bash
hermes plugins install saxby-tools/hermes-onsite/onsite --enable
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
laptop:~/work --apply` prints a folder. Start `hermes` in it and ask
it to run `uname -n`. It should print your computer's name. In
Hermes Desktop, click the onsite chip at the bottom right. A pane
opens where you browse to the folder.

Hermes Desktop loads the plugin's desktop part from the computer the
app runs on, and it may arrive switched off. If no chip that starts
with "onsite" shows at the bottom right of the app, open
Capabilities, then Plugins, open Onsite's entry and turn on its
Desktop switch. If there is no such switch, the part is not on that
computer yet. The repository's README says how to put it there,
under "The desktop half".

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
- **On the server it writes** three small state files under
  `plugin-data/onsite/` and empty folders that mirror a computer's
  folder names. Never a file's contents.
- **Inside Hermes** it adds a terminal backend, one tool, two hooks,
  two slash commands, one CLI command, a section of the system
  prompt and eight routes for the desktop app. Two routes write the plugin's own
  settings. After `/onsite` moves a session, it adds one note to
  your next message to tell the agent.
- **It imports private Hermes modules** to reuse Hermes' own SSH,
  local and screen-driver code. A Hermes release can move them,
  which is why it stops loading at Hermes 0.24.
- **No telemetry, no other network calls, no self-update.**

## Limits

- On your computer, Hermes can do anything your account can.
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

# QA journeys

A person walks every feature once before the plugin is submitted to
the Hermes catalog. Each journey says what to do, what you should
see, and what to send back if you do not.

Work top to bottom. Journeys 1 to 3 set up the rest. Tick a box when
its line holds. A box left empty with one line of what you saw is a
good bug report.

The examples use made-up names. The host, `host.example`, is the
machine that runs the Hermes gateway. The target, `laptop.example`,
is a Linux laptop. Your user on both is `you`, and the test profile
is `test`. Put your own names in. Journeys 1 to 13 touch only the
`qa` and `test` profiles. The rig, in journeys 11 and 15, swaps the
config of the profile it is given for the length of a run. That is
`buster` unless you pass `--profile`.

## Before you start

- [ ] From the host, this returns at once with no question. If it
      asks for a password, the laptop does not accept the host's key
      yet, and nothing below can work:

```bash
ssh you@laptop.example true
```

- [ ] Restart the Hermes gateway on the host if the plugin was
      updated or reloaded while it ran. A gateway keeps what it
      loaded: each reload leaves an old copy of the hooks behind, and
      the screen backend is only read once.
- [ ] The `test` profile has the plugin set up. A profile set up
      earlier needs nothing here. Do not run the second command on
      it: `init --apply` writes the whole list of targets and drops
      any it is not given. A new profile needs these four, in order:

```bash
hermes -p test plugins install https://github.com/saxby-tools/hermes-onsite/tree/main/onsite --enable
```

```bash
hermes -p test onsite init --ssh laptop=you@laptop.example --local mac --apply
```

```bash
hermes -p test config set terminal.backend onsite
```

```bash
hermes -p test config set plugins.entries.onsite.allow_gateway_injection true
```

The journeys work in a folder on the laptop, here `~/Development`.
Use one that exists there and put its name in.

- [ ] On the host, this prints a table with `laptop` marked `up`:

```bash
hermes -p test onsite status
```

If `laptop` is `DOWN`, wake the laptop and run it again. If the
command is not found, run `hermes -p test plugins enable onsite`.

- [ ] The desktop app has its part of the plugin, on the computer the
      app runs on. Journey 1 installs it. If the chip from journey 7
      is already there, it is in place.

## 1. A fresh install

This is the path a stranger takes, so it is tested on a profile that
has never seen the plugin. The command has been run into an empty
Hermes home, and into a fresh profile on Hermes' own Docker image
with a gateway running. Nobody has run it into a profile on a real
server.

- [ ] Make a throwaway profile and install from GitHub:

```bash
hermes profile create qa
```

```bash
hermes -p qa plugins install https://github.com/saxby-tools/hermes-onsite/tree/main/onsite
```

- [ ] Hermes warns that the source is custom and unreviewed, clones
      the one folder, and asks "Enable 'onsite' now?". No security
      warning stops it. Answer yes.
- [ ] `hermes -p qa plugins list` shows `onsite` as enabled.
- [ ] The installed folder holds the plugin and nothing else: no
      `rig`, `site`, `docs` or `tests`.

```bash
ls "$(hermes -p qa config path | xargs dirname)/plugins/onsite"
```

Now the desktop app's part, on the computer the app runs on. **Tried
once, on a test rig** with app version 0.23.2. It went as the boxes
below say, and the chip came up with no switch to turn on.

- [ ] In the app, open Capabilities, then Plugins, and click "Install
      from Git". Paste this and click "Review repository":

```text
https://github.com/saxby-tools/hermes-onsite/tree/main/onsite/desktop
```

- [ ] The dialog lists one part, "Desktop UI", and no agent plugin.
      Write down what it says about where it goes.
- [ ] Click Install. If you copied `plugin.js` there by hand before,
      the app says the desktop plugin already exists. Move the old
      `~/.hermes/desktop-plugins/onsite` folder away and try again.
- [ ] Within a few seconds the status bar shows a chip that starts
      with "onsite". If it does not, open the plugin's entry in
      Capabilities, Plugins and turn on the switch labelled Desktop.
      Note which it took.
- [ ] Enter the bare address,
      `https://github.com/saxby-tools/hermes-onsite`, in the same
      dialog. It says no plugin artifacts were found. That is
      expected.

The other way installs both parts from the app. It puts the agent
plugin into the profile set under "Install for profile". **Check
that profile before the click.** A click with another profile picked
installs the plugin there and turns it on.

- [ ] Optional: make a second throwaway profile, `hermes profile
      create qa2`. In "Install from Git", paste
      `https://github.com/saxby-tools/hermes-onsite/tree/main/onsite`.
      The dialog lists "Agent plugin" and "Desktop UI". Set "Install
      for profile" to `qa2` and click Install. Both parts should
      install and turn on. Remove `qa2` when done.

## 2. Set up

- [ ] A dry run prints settings and writes nothing:

```bash
hermes -p qa onsite init --ssh laptop=you@laptop.example --local mac --prefix laptop=/home/you/
```

- [ ] The same command with `--apply` writes them and prints what to
      run next: two commands, then one more that lets `/run` hand a
      command to the agent.
- [ ] Before running them, `hermes -p qa onsite status` starts with
      a line that says onsite is set up but not in use, and still
      lists the targets.
- [ ] Run all three with `-p qa` added after `hermes`. The printed
      lines leave the profile out. `hermes -p qa onsite status` then
      has no such line, and shows `laptop` up and `mac` as the
      default.
- [ ] `hermes -p qa onsite init --ssh box=you@laptop.example` with
      no other flag refuses, and says to add `--default NAME`.
- [ ] With no flags, in a terminal, `hermes -p qa onsite init` asks
      questions instead.

## 3. The folder picks the machine

Use the `test` profile from here on.

- [ ] On the host, ask where the laptop's folder is. It prints a
      folder on the host, under
      `~/.local/share/onsite/mount/test/laptop`:

```bash
hermes -p test onsite open laptop:~/Development
```

- [ ] Go to the folder it printed and start the TUI there:

```bash
hermes -p test --tui
```

- [ ] Before any message, type `/onsite`. It either says the session
      has not said which folder it is in yet, or names a target from
      the folder you started in. Note which.
- [ ] Ask: "Run `pwd; whoami; uname -n` and show me the output." You
      see `/home/you/Development`, `you` and the laptop's name.
- [ ] Type `/onsite`. It names `laptop`, says it is connected, and
      gives the reason "this session started there".
- [ ] On the host, in that same folder, `hermes -p test onsite
      status` gives the reason the folder was picked: "the session
      folder is on its disk".
- [ ] Quit. Start the TUI from `/Users/you/Workspaces`. The same
      question now prints the Mac's folder and the Mac's name.
- [ ] In that Mac session, after one message, type `/onsite laptop`.
      The reply ends "The agent is told with your next message."
- [ ] Ask: "Which machine do your tools run on now? Then run
      `uname -n`." The agent names the laptop without being told by
      you, and the command prints the laptop's name.
- [ ] `/onsite auto` sends the session back to the folder's own
      answer, and the agent knows that too.

## 4. Approvals stay Hermes' own

- [ ] In a laptop session, ask: "Make a folder named
      `onsite-qa-victim`, put a file in it, then delete the folder
      with `rm -rf`." Hermes' own approval prompt appears for the
      delete.
- [ ] Deny it. The folder is still on the laptop:

```bash
ssh you@laptop.example 'ls ~/Development/onsite-qa-victim'
```

- [ ] Ask again and allow it once. The folder is gone.
- [ ] No second prompt appeared anywhere else at any point.

## 5. Files land on the target

- [ ] In a laptop session, ask: "Write a file named `onsite-qa.txt`
      with three lines, read it back, then change the second line."
- [ ] The file is on the laptop and holds the changed line:

```bash
ssh you@laptop.example 'cat ~/Development/onsite-qa.txt'
```

- [ ] Ask it to search the folder for a word in that file. It finds
      it.

## 6. Proposed commands and `/run`

- [ ] Ask: "Propose a command that lists this folder. Do not run it."
      The agent replies with a numbered command and runs nothing.
- [ ] Type `/run 1`. The agent runs it with the terminal tool, and
      any approval prompt still applies.
- [ ] `/run 9` says "There is no proposed command #9." and lists the
      numbers it knows.
- [ ] If `/run 1` answers that this client does not let a plugin hand
      the agent a message, the fourth command of "Before you start"
      was not run for this profile. Run it and try again. No restart
      should be needed. Note it if one is.

## 7. Hermes Desktop

On the laptop, in the desktop app, connected to the gateway.

- [ ] In the **Sessions** tab, pick the test profile. The profiles
      are a row of squares at the bottom of the sidebar, or a menu in
      the status bar. Open any session.
- [ ] The status bar shows a chip that starts with `onsite`. Click
      it. A pane opens that reads "Onsite" at the top, with a list
      of machines. `laptop` has a button beside it, "New session".
      `mac` has none.
- [ ] Click "New session". A new session opens, named for the
      laptop's home folder and the laptop, such as `you on laptop`.
      Its chip reads `onsite: laptop`. Seen on a test rig and once in
      a person's own app.
- [ ] Click "New session" three more times, one after the other. Each
      opens at once, with a number after its name. The file tree
      fills every time.
- [ ] Ask that session for `pwd`. It prints your home folder on the
      laptop.
- [ ] Open the app's command palette. Under Projects there is one
      for the laptop's home folder, named `laptop` unless you made
      one for that folder before. Pick it and start a new chat in
      it. The chat's folder menu has "Change working directory".
- [ ] Change the session's folder with the app's own folder picker.
      The picker opens in your home folder on the laptop and lists
      the laptop's real folders. Pick `Development`. The app's file
      tree shows that folder's real files. Open one. The tree has
      been seen in the real app. **The picker has not**: on the test
      rig the app did not offer "Change working directory" at all.
      Note how long the tree takes to fill.
- [ ] The chip's tooltip has four lines: where tools run, the state,
      the folder and the reason. The reason is "the folder is on its
      disk" before the first message, and "this session started
      there" after it.
- [ ] The pane shows the machine, its state in milliseconds, the
      folder and the reason above the list of machines.
- [ ] In Capabilities, then Plugins, the card for the desktop part
      is named `onsite-desktop`. Note the name if it differs.
- [ ] Ask the agent to propose a command. A run card appears in the
      transcript with Run and Copy buttons, and the same command in
      the pane. Seen on a test rig, with a scripted model. **Not yet
      with a real model**, which has to copy one line from the tool's
      answer for the card to show.
- [ ] Click Run. The button reads "Sent" and the command runs after
      any approval. Click Copy and paste: it is the exact command.
      Until Nov. 27 the button did nothing in the app but print a
      hint. If you see that hint with the setting on, the server
      still runs the older code.
- [ ] Reload the app with the card in view. Its button still reads
      "Sent".
- [ ] On the host, turn the `/run` setting off: the fourth command of
      "Before you start" with `false` in place of `true`. Ask for
      another proposed command. Within a few seconds its Run button
      is greyed out and a line says "Run is off for this profile."
      Copy still works. Turn the setting back on and Run comes back.
      Note it if either change needs a restart.
- [ ] Open Settings, then Plugins, then Onsite. Both machines are
      listed, and a line names the profile these settings belong to.
      The page follows the profile picked in the Sessions tab. If
      Settings shows an "Applies to" row of profiles anywhere, it
      does not change this page. On the test rig that row is on
      Model, Main model. Note it if you see it anywhere else.
- [ ] Open Machines, under Onsite in the list on the left. The same
      two machines have Edit and Remove buttons.
- [ ] Click Edit on `laptop`, then "Test connection". It says
      "Connected in" some milliseconds. The form has a field named
      "Folder on the server for its files". Leave it empty.
- [ ] Add a machine named `spare` of the kind "The machine Hermes
      runs on" and save. It appears in the list. Remove it. It is
      gone, and gone from the profile's `config.yaml`.
- [ ] The Remove button for the default machine is greyed out. With
      the set-up above that is `mac`.
- [ ] Click Edit on `laptop`. A field named "Its screen is reached
      by" offers three choices. Pick the screen sharing one, save,
      and see `screen: sharing` under the laptop in `config.yaml`.
      Set it back to the first choice and the line is gone.
- [ ] Switch to the **Bots** tab and open the test profile's chat.
      If the app's active profile is another one that has no onsite
      plugin, the pane says which profile this chat belongs to and
      which one it asked. That message is right, not a bug. If the
      other profile has onsite too, the pane shows that profile's
      answer and says nothing. That is the known gap in issue 8.

## 8. When the laptop goes away

**The most important journey. A laptop that really sleeps has never
been tested.** The rig only fakes it.

- [ ] In a laptop session in the TUI, run one command so the session
      is live. Close the laptop's lid. Wait ten seconds.
- [ ] Ask for another command. Within about three seconds you get an
      error that names `laptop`. Nothing ran. Write down what the
      error says, word for word.
- [ ] If Hermes says the working folder no longer exists instead,
      note that. The session's folder is on the laptop's mount, and
      a mount stops answering while the laptop sleeps.
- [ ] In the desktop app, with a laptop session in view, the chip's
      dot turns red within about ten seconds of the lid closing. The
      file tree goes empty or shows an error. The app itself does not
      freeze, and a session on another machine still answers. Note
      what you see. On a test rig with a Linux server and a frozen
      laptop this held: the mount let go after twelve seconds and the
      tree read "Could not read this folder".
- [ ] Ask again right away. It fails at once, without a wait.
- [ ] Open the lid. Within about fifteen seconds, the next command
      runs with no restart of anything, and the app's file tree
      shows the laptop's files again. On the test rig both were back
      in three seconds.
- [ ] Start a long command (`sleep 60; echo done`), close the lid
      during it, and open it again. The result is reported as "no
      exit status", and the command is not run a second time.

## 9. The laptop's files on the host

On the host, in a terminal.

- [ ] Mount the laptop's disk. It prints where it is mounted and the
      folder a new session starts in. Neither is in your home
      folder:

```bash
hermes -p test onsite mount laptop
```

- [ ] `ls` on the start folder it printed shows the real files in
      your home folder on the laptop, not empty folders.
- [ ] Write a file through the mount, with the start folder in place
      of `START`. The second command prints `hi`:

```bash
echo hi > START/onsite-qa-mount.txt
```

```bash
ssh you@laptop.example 'cat ~/onsite-qa-mount.txt'
```

- [ ] `rm START/onsite-qa-mount.txt`, and the file is gone on the
      laptop.
- [ ] `cd` to a git repository on the laptop, through the mount, and
      run `git status` on the host. It answers within a few seconds
      and lists no files you did not change. Note the time.
- [ ] Ask for a folder by name. It prints that folder on the host.
      Start the TUI there: `pwd` prints the real folder on the
      laptop, and `ls` shows the laptop's files:

```bash
hermes -p test onsite open laptop:~/Downloads
```

- [ ] Ask for a folder that is not there, such as
      `laptop:~/no-such-folder`. It says the folder is not a folder
      on the laptop, and makes nothing.
- [ ] Take the mount down. The folder it was in is now empty. Run
      the first command of this journey again and the files are
      back:

```bash
hermes -p test onsite mount laptop --stop
```

- [ ] Restart the host, if you can. Open the pane in the desktop
      app, or run the first command again. The files are back with
      no other step.
- [ ] Only on a Linux host with no `sshfs`: the first command says
      the server has no `sshfs` and names the line that installs it
      for your distribution. Run that line, then the command again.
      **The Linux route has never run on a real Linux server.**

## 10. MCP tools on the laptop

- [ ] Add a server that runs on the laptop. Hermes connects, lists
      its tools and asks which to keep:

```bash
hermes -p test onsite mcp laptop files --apply -- npx -y @modelcontextprotocol/server-filesystem /home/you/Development
```

- [ ] `hermes -p test mcp list` shows `laptop_files`. If it landed in
      another profile, note which.
- [ ] In a new laptop session, ask the agent to list a folder with
      the `mcp__laptop_files__` tools. It works, behind Hermes'
      prompt if the tool can write.
- [ ] In a new Mac session, ask for the same tool. It is refused,
      with the reason and the words `/onsite laptop`.
- [ ] Remove it when done: `hermes -p test mcp remove laptop_files`.

## 11. Screen control

The rig needs Docker, and `HERMES_HOME` set to the folder that holds
`profiles/`. Run it from a clone of the repository, on the host.

- [ ] Check the laptop. It names the desktop, says whether Hermes'
      screen driver is there and whether screen sharing answers,
      names the route it would use, and adds notes for that route:

```bash
hermes -p test onsite screen laptop
```

- [ ] Run the rig's screen phase. It builds a container with its own
      desktop and drives that, not your screen. Ten checks pass. This
      needs the gateway restart from "Before you start":

```bash
python3 rig/run.py --apply --desk --phase screen
```

- [ ] Run it again over screen sharing. Ten checks pass. **This has
      never been run.** If a check fails, send back the file
      `rig/results/desk-share-phase-screen.txt`:

```bash
python3 rig/run.py --apply --desk --share --phase screen
```

- [ ] Run the phase that drives the same desktop with no Hermes
      session. Eight checks pass, with or without a restart:

```bash
python3 rig/run.py --apply --desk --phase share
```

- [ ] Optional, and your call: install Hermes' driver on the laptop
      (`hermes computer-use install`, run there), link the backend
      with `hermes -p test onsite screen-install --apply`, select
      it with `hermes -p test config set computer_use.backend
      onsite-screen`, and ask a laptop session to capture the screen.
      On Wayland expect a partial view. Note exactly what you get.
- [ ] With the backend selected, a Mac session still captures the
      Mac's own screen.
- [ ] Optional, and your call: try screen sharing on the laptop. On
      an X11 session, run `x11vnc -localhost -nopw -forever -shared`
      there, set `screen: sharing` on the target, and ask a laptop
      session to capture the screen and click somewhere harmless.
      GNOME on Wayland is not covered: its sharing speaks RDP.

## 12. An editor

- [ ] Open the folder from journey 3, the one on the laptop's
      mount, in Zed with the Hermes agent on the test profile. A
      terminal command runs on the laptop.
- [ ] Ask it to write a new file. It lands on the laptop.
- [ ] Ask it to patch that file. Note what happens. `patch` is only
      known to work where the same path exists on both machines.
      Note that the screen tool is not offered in an editor; Hermes
      leaves it out.

## 13. A phone

Never tested.

- [ ] In the Android app, open a chat with the test profile and ask
      for `uname -n`. Note which machine answers and whether you can
      pick a folder at all.
- [ ] Ask for a risky command. Note whether Hermes' approval prompt
      shows on the phone and whether you can answer it.

## 14. A laptop the host cannot reach

**Never tried from a real laptop.** It has been walked between test
containers. Use a computer the host cannot log in to: a laptop on a
phone's hotspot or behind a home router, with the VPN off. Its name
here is `roamer`. This journey also adds one line to
`~/.ssh/authorized_keys` on the host and one on the laptop.

- [ ] From the host, this fails or times out:

```bash
ssh you@roamer.example true
```

- [ ] On the laptop, its SSH server is on, and this returns at once
      with no question:

```bash
ssh you@host.example true
```

- [ ] On the laptop, fetch the host's public key, so the host can
      log in through the link. Use the name of the host's own key
      file:

```bash
ssh you@host.example cat .ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
```

- [ ] In the desktop app, open Settings, Plugins, Onsite, then
      Machines. Click "Add a machine". Name it `roamer`. Under
      "Where it is" pick "Another machine, which connects to the
      server by itself". The Address and Port fields go away. Note
      what "This server, as that machine logs in to it over SSH" is
      filled with. If the app reaches the host over SSH, it should
      be that login. Fill in your account on the laptop and click
      Save.
- [ ] The row reads `you, dials in on port` and a number between
      20000 and 29999. Under it a box titled "Connect roamer" has a
      red dot and "Waiting: it has not connected to this server, or
      its link dropped. Nothing answers on port" and the same number.
- [ ] Click Copy beside step 1. Paste it in a terminal on the
      laptop and run it. It asks once whether to trust the host. Say
      yes. It then prints nothing and does not return.
- [ ] Without a click, the box turns to a green dot and "Connected"
      with a time, within about five seconds. Note how long.
- [ ] Under the steps the page shows the key the server filed for
      `roamer`. On the laptop, this prints the same ED25519 line:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

- [ ] On the host, the last line names the port and reads `up`. The
      second command prints the same command the app showed, and
      "State: connected":

```bash
hermes -p test onsite status
```

```bash
hermes -p test onsite dial-in roamer
```

- [ ] Click the chip, then "New session" beside `roamer`. Ask for
      `uname -n`. It prints the laptop's name. The file tree shows
      the laptop's files.
- [ ] Press Ctrl-C on the command on the laptop. Within about ten
      seconds the chip's dot is red. Its hover text reads "State:
      not connected (it has not connected to this server, or its
      link dropped" and the port. The pane's line for `roamer`
      starts "Not connected:" and ends "Its command is in Settings,
      Plugins, Onsite, Machines." Nowhere does it only say
      "Connection refused".
- [ ] Ask for a command. The error names `roamer` and says the same
      thing. Nothing ran.
- [ ] Run the command on the laptop again. Within about fifteen
      seconds the next command runs and the file tree is back. You
      did nothing on the host.
- [ ] Keep it running. In the box, pick the laptop's system at step
      2. Save the file where it says, and run the lines under it on
      the laptop. Stop the command you started by hand. The link
      stays up. Restart the laptop and log in. Note how long until
      the chip is green, with nothing typed.
- [ ] Close the laptop's lid for two minutes and open it. Note how
      long until the chip is green. If it takes more than a minute,
      look at what the link said. On Linux:
      `journalctl --user -u onsite-dial-in -n 20`. "remote port
      forwarding failed" means the host held the old link's port.
- [ ] Move the laptop to another network, such as from the hotspot
      to Wi-Fi, and note the same. Then add the two lines the README
      gives under "Let the server notice a dead link" to the host's
      `/etc/ssh/sshd_config`, restart its SSH server, and note it
      again. It should now be back within about a minute.
- [ ] Limit the key. Copy the line at step 3 and put it in front of
      the laptop's key in `~/.ssh/authorized_keys` on the host, then
      a space. The link still comes up. From the laptop,
      `ssh you@host.example id` now prints nothing and fails.
- [ ] Add a second computer the same way. Its port is a different
      number.
- [ ] When done: turn the service off with the line the page gives,
      click Remove beside `roamer`, and take both lines out of the
      two `authorized_keys` files.

## 15. The automatic tests

Run these from a clone of the repository, on the host. The rig uses a
profile named `buster` unless you pass `--profile`, and needs Docker
and `HERMES_HOME`, as in journey 11.

- [ ] Unit tests: 653 pass and one is skipped.

```bash
uv run --no-project --with pytest --with pillow --with cryptography python -m pytest -q
```

- [ ] Desktop tests: 91 pass and none is skipped. They borrow React
      from a Hermes checkout at `~/.hermes/hermes-agent`, or the
      folder named in `HERMES_SRC`. Without one they are skipped, not
      failed.

```bash
node --test tests/desktop.test.mjs
```

- [ ] The rig against the container, then against the laptop:

```bash
python3 rig/run.py --apply
```

```bash
python3 rig/run.py --apply --real you@laptop.example
```

- [ ] The dial-in walk, which needs Docker alone and builds its own
      two computers. Every line reads PASS:

```bash
python3 rig/dial/walk.py --apply
```

## When you are done

- [ ] Remove the throwaway profile: `hermes profile delete qa`. If
      the app's install went into another profile by mistake, turn
      it off there: `hermes -p <that profile> plugins disable
      onsite`.
- [ ] Remove the test file on the laptop:
      `ssh you@laptop.example 'rm ~/Development/onsite-qa.txt'`.
- [ ] Take back what journey 11 added to the test profile, if you do
      not want to keep it: `screen: sharing` and
      `computer_use.backend`.
- [ ] Take the laptop's mount down if you do not want it kept:
      `hermes -p test onsite mount laptop --stop`.
- [ ] Send back every empty box with what you saw.

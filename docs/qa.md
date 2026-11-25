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
is `test`. Put your own names in. Journeys 1 to 14 touch only the
`qa` and `test` profiles. The rig, in journeys 12 and 15, swaps the
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
hermes -p test onsite init --ssh laptop=you@laptop.example --local mac --map laptop=/Users/you/Development=/home/you/Development --mirror laptop=/Users/you/Onsite/laptop --apply
```

```bash
hermes -p test config set terminal.backend onsite
```

```bash
hermes -p test config set plugins.entries.onsite.allow_gateway_injection true
```

The `--map` flag is for a folder the laptop shares with the host:
here the host sees the laptop's `~/Development` at
`/Users/you/Development`. With no shared folder, leave the flag out
and use a mirrored folder from journey 9 wherever a journey asks for
a shared one.

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
Hermes home. Nobody has run it into a profile that a gateway serves.

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

Now the desktop app's part, on the computer the app runs on. **This
has never been tried.** It was read from Hermes' source.

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

- [ ] From a folder the laptop shares with the host, start the TUI:

```bash
cd /Users/you/Development && hermes -p test --tui
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
      folder is in its path map".
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
      the status bar. Open a folder under `/Users/you/Development`.
- [ ] The status bar shows a chip that reads `onsite: laptop`. Its
      tooltip has four lines: where tools run, the state, the folder
      and the reason.
- [ ] Click the chip. A pane opens that reads "Onsite" at the top,
      with the machine, its state in milliseconds, the folder, the
      reason, and a list of machines.
- [ ] Ask the agent to propose a command. A run card appears in the
      transcript with Run and Copy buttons, and the same command in
      the pane. **This has never been seen in the real app.**
- [ ] Click Run. The button reads "Sent" and the command runs after
      any approval. Click Copy and paste: it is the exact command.
- [ ] On the host, turn the `/run` setting off: the fourth command of
      "Before you start" with `false` in place of `true`. Ask for
      another proposed command. Within a few seconds its Run button
      is greyed out and a line says "Run is off for this profile."
      Copy still works. Turn the setting back on and Run comes back.
      Note it if either change needs a restart.
- [ ] In the pane, under "Open a folder", pick `laptop` and click
      Browse. You see the laptop's home folders. Click into one, then
      click "Start a session in ...". A new session opens, and its
      chip reads `onsite: laptop`. **Never seen in the real app.**
- [ ] Ask that session for `pwd`. It prints the folder you picked.
- [ ] Open Settings, then Plugins, then Onsite. Both machines are
      listed, and a line names the profile these settings belong to.
      The page follows the profile picked in the Sessions tab. If
      Settings shows an "Applies to" row of profiles anywhere, it
      does not change this page. Note where you see that row.
      **Never seen in the real app.**
- [ ] Open Machines, under Onsite in the list on the left. The same
      two machines have Edit and Remove buttons.
- [ ] Click Edit on `laptop`, then "Test connection". It says
      "Connected in" some milliseconds.
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
      note that. It means the shared folder vanished with the laptop.
      Repeat this journey from a mirrored folder (journey 9) and see
      whether the plugin's own error appears there.
- [ ] Ask again right away. It fails at once, without a wait.
- [ ] Open the lid. Within about fifteen seconds, the next command
      runs with no restart of anything.
- [ ] Start a long command (`sleep 60; echo done`), close the lid
      during it, and open it again. The result is reported as "no
      exit status", and the command is not run a second time.

## 9. A folder that exists only on the laptop

- [ ] Open one folder that is on the laptop and not on the host. Pick
      one that exists. It prints a folder on the host:

```bash
hermes -p test onsite open laptop:~/Downloads --apply
```

- [ ] Start the TUI in the folder it printed. `pwd` prints the real
      folder on the laptop, and `ls` shows the laptop's files.
- [ ] Copy the laptop's folder names to the host. Run it once as
      shown, a dry run, then again with `--apply`. It says how many
      folders and where:

```bash
hermes -p test onsite mirror laptop
```

- [ ] The folder it named holds folders and no files. With the set-up
      above that is `/Users/you/Onsite/laptop`. It stands for the
      laptop's whole disk, so your folders are under
      `/Users/you/Onsite/laptop/home/you`. This prints 0, while `ls`
      on that longer path shows the laptop's folder names:

```bash
find /Users/you/Onsite/laptop -type f | wc -l
```

- [ ] In the desktop app's own folder picker, browse to a folder
      under `/Users/you/Onsite/laptop`, pick it, and see the chip
      read `onsite: laptop`.

## 10. A machine that dials out

Never tested from the laptop's own side.

- [ ] On the host, print the set-up:

```bash
hermes -p test onsite dial-out laptop-out --server you@host.example --user you
```

- [ ] On the laptop, run the `ssh -N ... -R ...` line it printed. It
      stays running and prints nothing. If it asks for a password,
      the laptop has no key for the host yet; stop and note it.
- [ ] On the host, run the same command with `--apply`, then
      `hermes -p test onsite status`. `laptop-out` is `up`, and
      `laptop` and `mac` are still listed.
- [ ] Stop the `ssh` line on the laptop. Status shows `laptop-out`
      down within two seconds, with "Connection refused".

## 11. MCP tools on the laptop

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

## 12. Screen control

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

## 13. An editor

- [ ] Open a folder under `/Users/you/Development` in Zed with the
      Hermes agent on the test profile. A terminal command runs on
      the laptop.
- [ ] Ask it to write a new file. It lands on the laptop.
- [ ] Ask it to patch that file. In a shared folder this works. Note
      that the screen tool is not offered in an editor; Hermes leaves
      it out.

## 14. A phone

Never tested.

- [ ] In the Android app, open a chat with the test profile and ask
      for `uname -n`. Note which machine answers and whether you can
      pick a folder at all.
- [ ] Ask for a risky command. Note whether Hermes' approval prompt
      shows on the phone and whether you can answer it.

## 15. The automatic tests

Run these from a clone of the repository, on the host. The rig uses a
profile named `buster` unless you pass `--profile`, and needs Docker
and `HERMES_HOME`, as in journey 12.

- [ ] Unit tests: 421 pass.

```bash
uv run --no-project --with pytest --with pillow --with cryptography python -m pytest -q
```

- [ ] Desktop tests: 40 pass and none is skipped. They borrow React
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

## When you are done

- [ ] Remove the throwaway profile: `hermes profile delete qa`. If
      the app's install went into another profile by mistake, turn
      it off there: `hermes -p <that profile> plugins disable
      onsite`.
- [ ] Remove the test file on the laptop:
      `ssh you@laptop.example 'rm ~/Development/onsite-qa.txt'`.
- [ ] Take back what the journeys added to the test profile, if you
      do not want to keep it: the `laptop-out` target from journey
      10, and `screen: sharing` and `computer_use.backend` from
      journey 12. The mirror folders from journey 9 are empty and
      can stay or go.
- [ ] Send back every empty box with what you saw.

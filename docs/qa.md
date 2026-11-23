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
is `test`. Put your own names in. Nothing here touches another
profile.

## Before you start

- [ ] Restart the Hermes gateway on the host if the plugin was
      updated or reloaded while it ran. A gateway keeps what it
      loaded: each reload leaves an old copy of the hooks behind, and
      the screen backend is only read once.
- [ ] On the host, this prints a table with `laptop` marked `up`:

```bash
hermes -p test onsite status
```

If `laptop` is `DOWN`, wake the laptop and run it again. If the
command is not found, run `hermes -p test plugins enable onsite`.

## 1. A fresh install

This is the path a stranger takes, so it is tested on a profile that
has never seen the plugin. It has not been run by anyone yet.

- [ ] Make a throwaway profile and install from GitHub:

```bash
hermes profile create qa
```

```bash
hermes -p qa plugins install saxby-tools/hermes-onsite/onsite
```

- [ ] The security scan passes and Hermes offers to enable it.
- [ ] `hermes -p qa plugins list` shows `onsite`.
- [ ] The installed folder holds the plugin and nothing else: no
      `rig`, `site`, `docs` or `tests`.

```bash
ls "$(hermes -p qa config path | xargs dirname)/plugins/onsite"
```

## 2. Set up

- [ ] A dry run prints settings and writes nothing:

```bash
hermes -p qa onsite init --ssh laptop=you@laptop.example --local mac --prefix laptop=/home/you/
```

- [ ] The same command with `--apply` writes them and prints three
      next steps.
- [ ] After the two commands it prints, `hermes -p qa onsite status`
      shows `laptop` up and `mac` as the default.
- [ ] With no flags, in a terminal, `hermes -p qa onsite init` asks
      questions instead.

## 3. The folder picks the machine

Use the `test` profile from here on.

- [ ] From a folder the laptop shares with the host, start the TUI:

```bash
cd /Users/you/Development && hermes -p test --tui
```

- [ ] Ask: "Run `pwd; whoami; uname -n` and show me the output." You
      see `/home/you/Development`, `you` and the laptop's name.
- [ ] Type `/onsite`. It names `laptop`, says it is connected, and
      gives the reason "the session folder is in its path map".
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
- [ ] `/run 9` says there is no such command and lists what is
      waiting.

## 7. Hermes Desktop

On the laptop, in the desktop app, connected to the gateway.

- [ ] Run "Reload desktop plugins" from the command palette.
- [ ] In the **Sessions** tab, pick the test profile from the row
      of profile squares at the bottom of the sidebar. Open a folder
      under `/Users/you/Development`.
- [ ] The status bar shows a chip that reads `onsite: laptop`. Its
      tooltip gives the folder and the reason.
- [ ] Click the chip. A pane opens with the target, its state in
      milliseconds, the folder, and a list of targets.
- [ ] Ask the agent to propose a command. A run card appears in the
      transcript with Run and Copy buttons, and the same command in
      the pane. **This has never been seen in the real app.**
- [ ] Click Run. The command runs after any approval. Click Copy and
      paste: it is the exact command.
- [ ] In the pane, under "Open a folder", pick `laptop` and click
      Browse. You see the laptop's home folders. Click into one, then
      click "Start a session in ...". A new session opens, and its
      chip reads `onsite: laptop`. **Never seen in the real app.**
- [ ] Ask that session for `pwd`. It prints the folder you picked.
- [ ] Open Settings, then Plugins, then Machines. Both machines are
      listed. **Never seen in the real app.**
- [ ] Click Edit on `laptop`, then "Test connection". It says
      "Connected in" some milliseconds.
- [ ] Add a machine named `spare` of the kind "The machine Hermes
      runs on" and save. It appears in the list. Remove it. It is
      gone, and gone from the profile's `config.yaml`.
- [ ] The Remove button for the default machine is greyed out.
- [ ] Switch to the **Bots** tab and open the test profile's chat.
      If the app's active profile is another one, the pane says which
      profile this chat belongs to and which one it asked. That
      message is right, not a bug.

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

- [ ] Open one deep folder with no set-up at all. It prints a
      folder on the host:

```bash
hermes -p test onsite open laptop:~/Downloads --apply
```

- [ ] On the host, copy the laptop's folder names. Add `--apply`
      after the dry run looks right:

```bash
hermes -p test onsite mirror
```

- [ ] `/Users/you/Onsite/laptop` holds folders and no files:

```bash
find /Users/you/Onsite -type f | wc -l
```

- [ ] Open one deep folder by name. It prints a folder on the host:

```bash
hermes -p test onsite open laptop:~/Downloads/some-project --apply
```

- [ ] Start the TUI in the folder it printed. `pwd` prints the real
      folder on the laptop, and `ls` shows the laptop's files.
- [ ] In the desktop app's folder picker, browse to that folder under
      `/Users/you/Onsite/laptop`, pick it, and see the chip read
      `onsite: laptop`.

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
      `hermes -p test onsite status`. `laptop-out` is `up`.
- [ ] Stop the `ssh` line on the laptop. Status shows `laptop-out`
      down within two seconds, with "Connection refused".

## 11. MCP tools on the laptop

- [ ] Add a server that runs on the laptop. Hermes connects, lists
      its tools and asks which to keep:

```bash
hermes -p test onsite mcp laptop files --apply -- npx -y @modelcontextprotocol/server-filesystem /home/you/Development
```

- [ ] In a new laptop session, ask the agent to list a folder with
      the `mcp__laptop_files__` tools. It works, behind Hermes'
      prompt if the tool can write.
- [ ] In a new Mac session, ask for the same tool. It is refused,
      with the reason and the words `/onsite laptop`.
- [ ] Remove it when done: `hermes -p test mcp remove laptop_files`.

## 12. Screen control

- [ ] Check the laptop. It names the desktop, says whether Hermes'
      screen driver is there, and adds notes for a Wayland desktop:

```bash
hermes -p test onsite screen laptop
```

- [ ] Run the rig's screen phase from a clone of the repository. It
      builds a container with its own desktop and drives that, not
      your screen. Eight checks pass:

```bash
python3 rig/run.py --apply --desk --phase screen
```

- [ ] Optional, and your call: install Hermes' driver on the laptop
      (`hermes computer-use install`, run there), link the backend
      with `hermes -p test onsite screen-install --apply`, select
      it with `hermes -p test config set computer_use.backend
      onsite-screen`, and ask a laptop session to capture the screen.
      On Wayland expect a partial view. Note exactly what you get.
- [ ] With the backend selected, a Mac session still captures the
      Mac's own screen.

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

- [ ] In the Android app, open a chat with the test profile and ask for
      `uname -n`. Note which machine answers and whether you can pick
      a folder at all.
- [ ] Ask for a risky command. Note whether Hermes' approval prompt
      shows on the phone and whether you can answer it.

## 15. The automatic tests

Run these from a clone of the repository, on the host. The rig uses a
profile named `buster` unless you pass `--profile`.

- [ ] Unit tests: 273 pass.

```bash
uv run --no-project --with pytest python -m pytest -q
```

- [ ] Desktop tests: 29 pass.

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

- [ ] Remove the throwaway profile: `hermes profile delete qa`.
- [ ] Remove the two test files on the laptop:
      `ssh you@laptop.example 'rm ~/Development/onsite-qa.txt'`.
- [ ] Send back every empty box with what you saw.

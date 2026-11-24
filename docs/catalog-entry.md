# Catalog entry draft

The Hermes plugin catalog takes one file in a pull request to
`NousResearch/hermes-agent`: `plugin-catalog/onsite.yaml`. This page
holds a draft of that file and the steps to submit it. Nothing here
has been filed.

Only the repository's owner, or a major contributor, may submit it.

## The entry

Two lines are filled in on the day: `sha` and the commit in `image`.
Both must be the same full commit, because the catalog shows the
README and the picture as they were at that commit. `sha` is 40
small hex characters and must name a commit, not a tag.

The README the catalog shows is the one in the plugin's folder,
`onsite/README.md`, because the entry has a `subdir`. That page
carries the status line and the disclosure. The repository's own
README is not shown there.

```yaml
name: onsite
title: Onsite
repo: https://github.com/saxby-tools/hermes-onsite
subdir: onsite
sha: <the 40-character commit being listed>
version: "0.1.0"
description: >-
  Open a project folder on your own computer and the Hermes that
  lives on your server works in it. Its terminal and file tools run
  on the computer the folder lives on, as you, over the SSH you
  already have, started from either side. The folder you open picks
  the computer: no switch phrase, no mode. Risky commands
  stop at Hermes' own approval prompt in the app you are holding. In
  Hermes Desktop a chip names the computer in use, a pane browses its
  folders and starts sessions there, proposed commands get a Run
  button, and a settings page adds and tests computers. MCP servers
  can run on the computer too, and Hermes can see and drive its
  screen, through Hermes' own driver or the screen sharing the
  computer already has. Disclosure —
  runs model-authored shell commands and file edits on the SSH
  targets you name, with that account's full authority and no
  sandbox; Hermes' approval prompt is the only gate. Uses the system
  ssh client and the keys your SSH setup offers, and never reads or
  stores a key. Reads one optional secret through Hermes,
  ONSITE_SCREEN_PASSWORD, if a target's screen sharing asks for a VNC
  password. Keeps one ssh connection open per target in use, one
  per MCP server you add and one per session that uses a target's
  screen. While Hermes Desktop is open, logs in to each target every
  five seconds to see that it is up. Files each target's host key in
  known_hosts on first contact. May hold a sleep lock on a target
  while a command runs. Hermes' own SSH code, which it reuses, leaves
  one small file per session in the target's /tmp holding the
  shell's exported variables. Writes three small state files under
  plugin-data/onsite and empty folders that mirror a target's folder
  names. /run puts a message into the chat, which needs
  allow_gateway_injection. Serves eight routes to the desktop app
  under /api/plugins/onsite. Two write the plugin's own settings and
  one makes mirror folders. Adds a section to the system prompt that
  names the session's computer. After /onsite moves a session, adds
  one note to the user's next message to tell the agent. No
  telemetry, no other network calls, no self-update, nothing
  installed on a target.
maintainer: saxby-tools
tier: community
category: tools
requires_hermes: ">=0.23.2,<0.24"
docs_url: https://saxby-tools.github.io/hermes-onsite/
image: https://raw.githubusercontent.com/saxby-tools/hermes-onsite/<the same commit>/docs/brand/banner.png
readme: true
platforms: [linux, macos]
known_issues:
  - "Imports private Hermes modules to reuse Hermes' own SSH, local and screen-driver code: 14 in compat.py and 4 in screenbackend.py. hermes plugins validate lists them as warnings. requires_hermes has an upper bound for that reason, so the plugin stops loading at Hermes 0.24 until it has been run against it."
  - "Needs plugins.isolation in_process (the default): Hermes cannot hand a terminal environment across the plugin-host boundary."
  - "Hermes only runs a session in a folder that exists on the host. The plugin keeps empty folders that mirror a target's folder names, so any folder can be opened, but the desktop app's own file tree shows those empty folders, not the target's files."
  - "Screen control needs either Hermes' screen driver installed on the target by its owner, or screen sharing (VNC) turned on there. Screen sharing sees pixels only: Hermes clicks by position and cannot name a button. Both have been tested only on a Linux X11 desktop. The approval prompt for a screen action cannot name the computer."
  - "Screen sharing borrows two packages Hermes itself ships, Pillow for the picture and cryptography for a VNC password. They are not declared as the plugin's own, and are only imported when a session uses that route."
  - "Editors over ACP: Hermes leaves the screen tool out, and patch only works where the same path exists on both computers."
  - "In the desktop app's Bots tab the chip follows the app's active profile, which may not be the bot in view. It names the profile it asked."
  - "A real sleeping laptop, macOS as a target and the mobile apps have not been tested. See the repository's issues."
capabilities:
  provides_tools:
    - propose_command
  provides_hooks:
    - pre_tool_call
    - pre_llm_call
  provides_middleware: []
  requires_env: []
```

## Before it is filed

- [ ] Every box in the [QA journeys](qa.md) is ticked, or has an
      issue.
- [ ] `hermes plugins validate --install-deps <clone>/onsite` passes
      on a clean clone at the commit being listed, against the
      current Hermes release. The path is the plugin's folder, not
      the top of the clone. It passed on Nov. 24 on a copy installed
      from GitHub, with the one warning about private imports.
- [ ] If Hermes 0.24 has shipped, the plugin has been run on it and
      `requires_hermes` raised in `plugin.yaml` and here. An entry
      that cannot be installed on arrival will be declined.
- [x] A banner picture exists with the new name:
      `docs/brand/banner.png`, 1200 by 600, the subject on the right,
      because the listing lays the title over the left. It is in the
      entry as `image`. Pin it to the same commit as `sha`.
- [ ] Screenshots of the chip, the pane, an approval and the
      Machines page are in the repository, for `screenshots` and for
      the pull request. Up to six, each pinned to the same commit.
      They must be taken in the real app. The guide's pictures are
      drawings and must not be listed as screenshots.
- [ ] The status line says what is true on the day, in both
      `README.md` and `onsite/README.md`. The second is the one the
      catalog shows.

## What the pull request should say

The submission guide asks the pull request to say what the plugin
does, which Hermes surfaces it uses and everything the disclosure
rule covers, with screenshots of anything that has a screen. Saying
how it differs from entries already listed is our own addition. Four
are close:

- **`local-hands`** runs a small program on each computer. It dials
  a hub on the server and asks you before acting. The agent switches
  computers with a tool. Onsite runs no program of its own on your
  computer and approves in the Hermes client.
- **`hermes-teleoperator`** puts its own receiver program on each
  computer and switches a conversation between computers over SSH.
  Onsite adds nothing to the computer and picks it from the folder a
  session is opened in.
- **`tether`** needs Hermes on your computer, which must be a Mac or
  run Windows. You switch by saying "tether" and a name, and the
  person at that computer allows each session on its own screen.
- **`hermes-remote-hands`** adds six separate tools for one Mac,
  beside Hermes' own.

None of the four says it picks the computer by folder, or opens a
folder that exists only on your computer. `tether` has its own panel
in the desktop app. None lists a folder browser or run buttons
there.

These lines were checked on Nov. 24 against each plugin's entry in
the catalog that ships with Hermes 0.23.2. Read them against the
catalog again on the day.

The pull request must also repeat the disclosure, and say where the
private imports are: in two files, `compat.py` and
`screenbackend.py`. The second names every member it needs and fails
with a plain message if one moves. The first fails with an import
error.

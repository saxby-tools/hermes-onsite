# Catalog entry draft

The Hermes plugin catalog takes one file in a pull request to
`NousResearch/hermes-agent`: `plugin-catalog/onsite.yaml`. This page
holds a draft of that file and the steps to submit it. Nothing here
has been filed.

Only the repository's owner, or a major contributor, may submit it.

## The entry

Two lines are filled in on the day: `sha` and the commit in `image`.
Both must be the same full commit, because the catalog shows the
README and the picture as they were at that commit.

```yaml
name: onsite
title: Onsite
repo: https://github.com/saxby-tools/hermes-onsite
subdir: onsite
sha: <the 40-character commit being listed>
version: "0.1.0"
description: >-
  Open a project folder on your own computer and your always-on
  Hermes works in it. Hermes stays on your server; its terminal and
  file tools run on the computer the folder lives on, as you, over
  the SSH you already have, started from either side. The folder you
  open picks the computer: no switch phrase, no mode. Risky commands
  stop at Hermes' own approval prompt in the app you are holding. In
  Hermes Desktop a chip names the computer in use, a pane browses its
  folders and starts sessions there, proposed commands get a Run
  button, and a settings page adds and tests computers. MCP servers
  and Hermes' screen driver can run on the computer too. Disclosure —
  runs model-authored shell commands and file edits on the SSH
  targets you name, with that account's full authority and no
  sandbox; Hermes' approval prompt is the only gate. Uses the system
  ssh client and the keys your SSH setup offers, and never reads or
  stores a key. Keeps one ssh connection open per target in use, one
  per MCP server you add and one per session that uses a target's
  screen. Files each target's host key in known_hosts on first
  contact. May hold a sleep lock on a target while a command runs.
  Writes three small state files under plugin-data/onsite and empty
  folders that mirror a target's folder names. /run puts a message
  into the chat, which needs allow_gateway_injection. Serves routes
  to the desktop app under /api/plugins/onsite, one of which writes
  the plugin's own settings. After /onsite moves a session, adds one
  note to the user's next message to tell the agent. No telemetry, no
  other network calls, no self-update, nothing installed on a target.
maintainer: saxby-tools
tier: community
category: tools
requires_hermes: ">=0.23.2,<0.24"
docs_url: https://saxby-tools.github.io/hermes-onsite/
readme: true
platforms: [linux, macos]
known_issues:
  - "Imports private Hermes modules to reuse Hermes' own SSH, local and screen-driver code: 14 in compat.py and 4 in screenbackend.py. hermes plugins validate lists them as warnings. requires_hermes has an upper bound for that reason, so the plugin stops loading at Hermes 0.24 until it has been run against it."
  - "Needs plugins.isolation in_process (the default): Hermes cannot hand a terminal environment across the plugin-host boundary."
  - "Hermes only runs a session in a folder that exists on the host. The plugin keeps empty folders that mirror a target's folder names, so any folder can be opened, but the desktop app's own file tree shows those empty folders, not the target's files."
  - "Screen control needs Hermes' screen driver installed on the target by its owner, and has been tested only on a Linux X11 desktop. The approval prompt for a screen action cannot name the computer."
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
- [ ] `hermes plugins validate --install-deps` passes on a clean
      clone at the commit being listed, against the current Hermes
      release.
- [ ] If Hermes 0.24 has shipped, the plugin has been run on it and
      `requires_hermes` raised in `plugin.yaml` and here. An entry
      that cannot be installed on arrival will be declined.
- [ ] A banner picture exists with the new name: 1200 by 600, the
      subject on the right, because the listing lays the title over
      the left. Add it as `image`, pinned to the same commit. The old
      banner shows the old name and was taken out.
- [ ] Screenshots of the chip, the pane, an approval and the
      Machines page are in the repository, for `screenshots` and for
      the pull request. Up to six, each pinned to the same commit.
- [ ] The README's status line says what is true on the day.

## What the pull request should say

The catalog asks a new entry to explain how it differs from ones
already listed. Four are close:

- **`local-hands`** runs a program on your computer that dials a hub
  on the server, and asks for approval in that program's own
  terminal. Onsite installs nothing on your computer and approves in
  the Hermes client.
- **`hermes-teleoperator`** deploys a program to each computer and
  switches with a typed command; every chat starts on the server.
  Onsite picks the computer from the folder a session is opened in.
- **`tether`** needs a full Hermes on your computer and a code typed
  there for each session. It does not run on a Linux computer.
- **`hermes-remote-hands`** adds separate tools for one Mac beside
  Hermes' own.

None of the four picks the computer by folder, opens a folder that
exists only on your computer, or adds a folder browser, a settings
page or run buttons to the desktop app.

The pull request must also repeat the disclosure, and say that the
private imports are fenced in two files that name every member they
need and fail with a plain message if one moves.

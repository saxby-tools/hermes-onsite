# The onsite guide

Open a folder on your own computer and Hermes works in it. This guide
shows each screen you will meet, in the order you meet it. You do not
need to know how any of it is set up.

For people who use Hermes Desktop. About six minutes to read.

<!-- onsite:notice -->

**New here?** Hermes is an AI agent that lives on a server that is
always on. onsite lets it do its work, such as running commands and
editing files, on the computer where your project is. You stay in the
Hermes app the whole time.

<!-- /onsite:notice -->

Jump to a part:

- [Open a folder](#open-a-folder)
- [See where Hermes is working](#see-where-hermes-is-working)
- [Say yes or no](#say-yes-or-no)
- [Run a command Hermes suggests](#run-a-command-hermes-suggests)
- [Add or change a computer](#add-or-change-a-computer)
- [If something goes wrong](#if-something-goes-wrong)

## How it works

You talk to Hermes in the app. Hermes thinks on your server. When it
needs to run a command or change a file, onsite sends that one step to
the computer your folder is on and brings the result back.

| Who | What it does |
| --- | --- |
| You | Open a folder and ask for something. |
| Hermes, on your server | Plans the work. Asks you before a risky step. |
| onsite | Picks the computer from the folder you opened. |
| Your computer | Runs the command or edits the file, as you. |

One word to know: the app calls each computer a **machine**. This
guide says "computer" and uses "machine" only where the app does.

## Before you start

onsite has to be set up once, on the server. If you did not do that
yourself, ask the person who runs your Hermes server. The steps are in
the [README](../README.md#set-up).

You can tell it is ready by looking at the bottom right corner of
Hermes Desktop. A small chip there names a computer.

<!-- onsite:screen {"shows": "chip"} -->

The Hermes Desktop window, with a chat in the middle. In the bottom
right corner, in the status bar, is a chip with a green dot. It reads
"onsite: laptop" and then the folder, "…/work/app".

<!-- /onsite:screen -->

If there is no chip at all, the app is missing its half of onsite. The
README has that step too, under
[The desktop half](../README.md#the-desktop-half).

## Open a folder

### 1. Click the chip

Click the chip in the bottom right corner. A pane opens on the right
side of the window. It is titled "Onsite".

<!-- onsite:screen {"shows": "pane"} -->

The onsite pane, on the right of the chat. At the top, four lines:
"Runs on: laptop (you@laptop.example)", "State: connected (42 ms)",
"Folder: /home/you/work/app" and "Why: the folder is in its mirror".
Below that is a list of two machines, each with a green dot: "laptop",
marked "(default)", and "mac (this machine)". Then a hint, "Move this
session with /onsite <target>." Then "Open a folder", with a menu set
to "laptop" and a Browse button. Last, "Proposed commands", which says
"Nothing is waiting. Commands the agent proposes show up here."

<!-- /onsite:screen -->

### 2. Pick a computer and browse

Under "Open a folder", pick a computer from the menu and click
**Browse**. You see the folders in your home folder on that computer.
Click a folder to go into it. Click the row with two dots to go back
up.

<!-- onsite:screen {"shows": "browser"} -->

The "Open a folder" part of the pane after a click on Browse. The
menu is set to "laptop". Under it is the path "/home/you/work", then
four rows: two dots, "app", "notes" and "site". At the bottom is a
button that reads "Start a session in work".

<!-- /onsite:screen -->

### 3. Start a session

Click **Start a session in work**. The button names the folder you are
in. A new chat opens, named for the folder and the computer: "work on
laptop". From here on, what Hermes does in that chat happens in that
folder, on that computer.

<!-- onsite:notice -->

The list shows the names of your folders and nothing else. onsite
does not copy your files to the server.

<!-- /onsite:notice -->

The list only appears for computers Hermes reaches over the network.
A folder on the server itself opens the usual way.

## See where Hermes is working

The chip always says where. It has four looks.

<!-- onsite:screen {"shows": "chip-states", "caption": "Drawing of the chip. Beside each look is what you see when you rest the pointer on it."} -->

Four versions of the chip, each beside its hover text. A grey dot and
"onsite": it is checking where this session runs. A green dot and
"onsite: laptop …/work/app": tools run on laptop, the state is
"connected (42 ms)", with the folder and the reason. A red dot and
"onsite: laptop …/work/app": the state is "not reachable (no
answer)". A red dot and "onsite: not set up": onsite is not set up on
the host.

<!-- /onsite:screen -->

| The chip shows | It means |
| --- | --- |
| A grey dot and "onsite" | It is checking. This lasts a moment. |
| A green dot and a computer's name | Hermes is working there, in the folder shown. |
| A red dot and a computer's name | onsite cannot reach that computer right now. |
| A red dot and "onsite: not set up" | onsite is not set up for the Hermes you are talking to. |

The chip shows the last two parts of the folder. Rest the pointer on
it, or click it, for the whole path and for **Why**: the reason that
computer was picked. The reasons are short, such as "the folder is in
its mirror" or "set with /onsite".

### Move a chat to another computer

Type this in the chat, with the computer's name:

```text
/onsite laptop
```

To hand the choice back to the folder, type:

```text
/onsite auto
```

Hermes is not told when you move a chat. Say so in your next message
if it matters, because the same folder can have a different path on
another computer.

## Say yes or no

Before a risky command, Hermes stops and asks you. It asks in the app
you are using, not on some other screen. The question is Hermes' own.
onsite adds no second question and never answers for you.

<!-- onsite:screen {"shows": "approval", "caption": "Drawing of the screen. The question is Hermes' own, and its wording and buttons differ from this."} -->

A chat. You asked Hermes to clear out the old build folder. Hermes
says that deletes files, so it needs your say-so. Below is a box with
the command, "rm -r build", and two buttons, to approve it or to deny
it. The chip in the corner still reads "onsite: laptop".

<!-- /onsite:screen -->

Look at the chip before you answer. It tells you which computer the
command will run on.

<!-- onsite:notice {"kind": "limit"} -->

That question is the only safety net. On your computer Hermes can do
anything you can do. Read the command before you say yes.

<!-- /onsite:notice -->

## Run a command Hermes suggests

Sometimes Hermes suggests a command and does not run it. The
suggestion shows up as a card in the chat, with a number.

<!-- onsite:screen {"shows": "run-card"} -->

A chat. Hermes says a command starts the site on your laptop and to
run it when you are ready. Below is a card titled "Proposed command
#1" with two buttons, Run and Copy, the command "npm run dev", and a
line saying what it is for. A second copy of the card shows it after
a click on Run: the Run button now reads "Sent".

<!-- /onsite:screen -->

- **Run** asks Hermes to run it. The button changes to "Sent". If the
  command is risky, Hermes still asks you first.
- **Copy** puts the command on your clipboard. The button reads
  "Copied" for a moment.

The same cards are in the pane, under "Proposed commands". You can
also type the number yourself:

```text
/run 1
```

## Add or change a computer

Open Settings, then Plugins, then **Machines**. You see each computer
onsite knows.

<!-- onsite:screen {"shows": "machines"} -->

The Machines page in Settings. A line at the top says "The machines a
session can run on. A session picks one from the folder it is opened
in." Two rows follow. The first is "laptop", "you@laptop.example
(default)", with an Edit button and a Remove button that is greyed
out. The second is "mac", "this machine", with Edit and Remove. At
the bottom is a button, "Add a machine".

<!-- /onsite:screen -->

### Add one

Click **Add a machine** and fill in the form.

<!-- onsite:screen {"shows": "machine-form"} -->

The form for a machine. From the top: "Name", filled with "laptop".
"Where it is", set to "Another machine, over SSH". "Address", filled
with "laptop.example". "Log in as", filled with "you". "Port (leave
empty for 22)", empty. "Key file (leave empty to use your SSH
setup)", empty. "Folders that belong to it, one a line", filled with
"/home/you/". A ticked box, "Use it when no folder rule matches". A
line that reads "Connected in 42 ms." Then three buttons: Save, Test
connection and Cancel.

<!-- /onsite:screen -->

| Field | What to put in it |
| --- | --- |
| Name | A short name you will recognise, such as "laptop". You cannot change it later. |
| Where it is | "Another machine, over SSH" for any computer but the server. |
| Address | The computer's name or address on your network. |
| Log in as | Your user name on that computer. |
| Port | Leave it empty unless you were told a number. |
| Key file | Leave it empty unless you were told a file. |
| Folders that belong to it | Folders whose work should happen on this computer, one on each line. |
| Use it when no folder rule matches | Tick it for the computer you use most. |

Click **Test connection** before you save. It tries the computer as
you typed it and saves nothing. You get one of two answers:

- "Connected in 42 ms." It works. Click **Save**.
- "No connection:" and a reason. Check the address and the user name,
  and that the computer is awake.

### Change or remove one

**Edit** opens the same form. **Remove** takes the computer off the
list at once. It does not ask first.

You cannot remove the computer marked "(default)". Its Remove button
is greyed out. Tick "Use it when no folder rule matches" on another
computer first.

## If something goes wrong

| You see | It means | Do this |
| --- | --- | --- |
| A red dot on the chip | The computer is asleep, off or off the network. | Wake it. Work picks up by itself within about 10 seconds. |
| Hermes says a command has "no exit status" | The link dropped while the command was running. It may have finished or not. Hermes will not run it twice by itself. | Check the result on your computer, then tell Hermes what you found. |
| "onsite: not set up" | onsite is off, or has no computers, for the Hermes you are talking to. | Open Machines and add one, or ask the person who runs your server. |
| A message that names a profile, such as: This chat belongs to the "home" profile. The app asked the "work" profile, which has no onsite plugin enabled. | The app is asking a different Hermes profile than the one this chat belongs to. | Pick the profile the message names, in the Sessions tab. |
| "That folder could not be listed." | The computer did not answer, or the folder cannot be read. | Check the chip's dot. Click Browse again. |
| "More folders than fit here. Go deeper to narrow it." | The folder has more than 500 folders in it. | Click into a folder closer to the one you want. |
| "Could not reach the chat box. Type /run 3." | The Run button could not type for you. | Type `/run 3` in the chat yourself. |
| No Machines page in Settings | Your version of the app cannot show a plugin's settings page. | Use the commands in the [README](../README.md#set-up). |

## What to type

You never have to type a command. These are here if you like to.

| Type this in the chat | What happens |
| --- | --- |
| `/onsite` | Shows where this chat runs, and why. |
| `/onsite laptop` | Moves this chat to the computer named laptop. |
| `/onsite auto` | Lets the folder pick the computer again. |
| `/run 2` | Asks Hermes to run proposed command 2. |

## What is not proven yet

onsite is new. These are the things in this guide that nobody has
checked in the real app yet.

<!-- onsite:notice {"kind": "untested"} -->

The cards with Run and Copy, the folder list and the Machines page
have passed their tests, but no person has used them inside the real
Hermes Desktop app. The chip and the pane have been seen there.

No person has yet answered Hermes' yes-or-no question for a real
command through onsite. The tests show the question appears and that
a "no" holds.

A laptop that really falls asleep has not been tried. The tests cut
the network link, which is not quite the same.

<!-- /onsite:notice -->

The full list is in the README, under
[What is tested](../README.md#what-is-tested). The screens in this
guide are drawings. Every word in them is a word the app shows, but
the app around them will look different.

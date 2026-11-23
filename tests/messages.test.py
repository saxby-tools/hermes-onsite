"""The words shown to a person and to the model."""

from __future__ import annotations

from onsite import config
from onsite import messages
from onsite import moves
from onsite import proposals
from onsite import routing


PARSED = config.parse_config({
    "default": "mac",
    "targets": {
        "laptop": {
            "kind": "ssh",
            "host": "laptop.example",
            "user": "dev",
            "prefixes": ["/home/dev/"],
        },
        "mac": {"kind": "local"},
    },
})

UP = {
    "ok": True,
    "target": "laptop",
    "kind": "ssh",
    "label": "laptop (dev@laptop.example)",
    "connected": True,
    "detail": "",
    "latency_ms": 41,
    "cwd": "/home/dev/app",
    "reason": routing.REASON_PREFIX,
    "pinned": True,
    "default": "mac",
    "targets": ["laptop", "mac"],
}

PROPOSAL = proposals.Proposal(
    number=3,
    command="make deploy",
    why="ships the build",
    status=proposals.STATUS_PENDING,
    at=0.0,
)


def test_status_names_the_target_state_folder_and_reason():
    text = messages.format_status(UP)

    assert "Onsite:  laptop (dev@laptop.example)" in text
    assert "State:   connected (41 ms)" in text
    assert "Folder:  /home/dev/app" in text
    assert "under one of its prefixes" in text
    assert "Targets: laptop, mac (default)" in text


def test_status_shouts_when_the_target_is_down():
    down = {**UP, "connected": False, "detail": "connection refused"}

    assert "NOT REACHABLE (connection refused)" in messages.format_status(down)


def test_status_admits_when_the_folder_is_not_known():
    unknown = {**UP, "folder_known": False}

    assert "may change" in messages.format_status(unknown)
    assert "may change" not in messages.format_status(UP)


def test_status_passes_a_settings_error_through():
    broken = {"ok": False, "error": "the settings are not usable."}

    assert messages.format_status(broken) == "the settings are not usable."


def test_a_switch_says_where_the_next_call_starts():
    route = routing.Route("laptop", "~", routing.REASON_OVERRIDE)
    text = messages.format_switch(
        route,
        PARSED.target("laptop"),
        UP,
        cleared=False,
    )

    assert "now runs on laptop (dev@laptop.example)" in text
    assert "starts in ~" in text
    assert "The agent is told with your next message" in text
    assert "Warning" not in text


def test_a_move_note_says_who_wrote_it_and_where_tools_run_now():
    move = moves.Move("mac", "laptop", "/home/dev/app")
    text = messages.format_move_note(move, PARSED)

    assert text.startswith("[Note from the onsite plugin, not typed")
    assert "moved this session to `laptop` with `/onsite`" in text
    assert "now run on `laptop`, a remote machine" in text
    assert "start in `/home/dev/app`" in text
    assert "Until now they ran on `mac`" in text
    assert text.endswith("may not exist on `laptop`.]")


def test_a_move_note_is_one_paragraph():
    # It rides inside the user's message. A blank line would make
    # the rest read as something the user wrote.
    move = moves.Move("mac", "laptop", "~")
    text = messages.format_move_note(move, PARSED)

    assert "\n" not in text
    assert "start in its home folder" in text


def test_a_move_note_for_a_cleared_override_says_so():
    move = moves.Move("mac", "laptop", "~", cleared=True)
    text = messages.format_move_note(move, PARSED)

    assert "back to `laptop`" in text
    assert "`/onsite auto`" in text


def test_a_switch_to_a_dead_target_warns():
    route = routing.Route("laptop", "~", routing.REASON_OVERRIDE)
    down = {**UP, "connected": False, "detail": "timed out"}

    text = messages.format_switch(
        route,
        PARSED.target("laptop"),
        down,
        cleared=False,
    )

    assert "Warning: it is not reachable right now (timed out)." in text


def test_the_block_is_numbered_and_says_how_to_run_it():
    text = messages.format_block(PROPOSAL)

    assert text.startswith("Proposed command #3")
    assert "    $ make deploy" in text
    assert "Why: ships the build" in text
    assert text.endswith("Run it with /run 3")


def test_the_tool_result_says_the_command_has_not_run():
    text = messages.format_proposal_result(PROPOSAL, with_card=False)

    assert "It has NOT run." in text
    assert "::onsite-run" not in text


def test_only_the_desktop_gets_the_card_directive():
    text = messages.format_proposal_result(PROPOSAL, with_card=True)

    assert '::onsite-run{n="3"}' in text


def test_the_run_request_carries_the_exact_command():
    text = messages.format_run_request(PROPOSAL)

    assert "terminal tool" in text
    assert "```\nmake deploy\n```" in text


def test_a_blocked_run_explains_the_setting():
    text = messages.format_run_blocked(PROPOSAL)

    assert "allow_gateway_injection" in text
    assert "make deploy" in text


def test_a_missing_number_lists_what_exists():
    assert "Known: #3." in messages.format_no_proposal(9, [PROPOSAL])
    assert "Nothing has been proposed" in messages.format_no_proposal(9, [])


def test_the_prompt_note_names_a_remote_target():
    route = routing.Route("laptop", "/home/dev/app", routing.REASON_PREFIX)
    text = messages.format_prompt_section(route, PARSED)

    assert "`laptop`, a remote machine reached over SSH" in text
    assert "as user `dev`" in text
    assert "`/home/dev/app`" in text
    assert "[onsite:" in text
    assert "propose_command" in text


def test_the_prompt_note_names_the_local_target():
    route = routing.Route("mac", "~", routing.REASON_DEFAULT)
    text = messages.format_prompt_section(route, PARSED)

    assert "the same machine Hermes runs on" in text
    assert "its home folder" in text


def test_the_cli_status_marks_the_default_target():
    rows = [
        {"name": "laptop", "kind": "ssh", "state": "up (41 ms)"},
        {"name": "mac", "kind": "local", "state": "up"},
    ]

    text = messages.format_cli_status("/Users/dev/app", UP, rows)

    assert "Folder    /Users/dev/app" in text
    assert "Target    laptop (dev@laptop.example)" in text
    assert " * mac     local  up" in text
    assert "   laptop  ssh    up (41 ms)" in text

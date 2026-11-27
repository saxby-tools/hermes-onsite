"""Every command ends in exactly one of four named states."""

from __future__ import annotations

from onsite import exits


def test_exit_zero_is_success():
    assert exits.classify({"returncode": 0}) == exits.STATE_SUCCESS


def test_another_exit_status_is_nonzero():
    assert exits.classify({"returncode": 2}) == exits.STATE_NONZERO


def test_a_hermes_timeout_is_killed():
    result = {"returncode": 124, "hermes_timed_out": True}

    assert exits.classify(result) == exits.STATE_KILLED


def test_an_interrupt_is_killed():
    result = {"returncode": 130, "hermes_interrupted": True}

    assert exits.classify(result) == exits.STATE_KILLED


def test_death_by_signal_is_killed():
    assert exits.classify({"returncode": 137}) == exits.STATE_KILLED


def test_a_lost_link_is_no_status():
    result = {"returncode": 255}

    assert exits.classify(result, link_lost=True) == exits.STATE_NO_STATUS


def test_255_with_the_exit_marker_is_the_commands_own_status():
    result = {"returncode": 255, "cwd_observed": True}

    assert exits.transport_verdict(result) == exits.VERDICT_FINE


def test_255_with_an_ssh_error_is_a_lost_link():
    result = {
        "returncode": 255,
        "output": "ssh: connect to host box port 22: Connection refused",
    }

    assert exits.transport_verdict(result) == exits.VERDICT_LOST


def test_255_with_no_clue_needs_a_probe():
    result = {"returncode": 255, "output": ""}

    assert exits.transport_verdict(result) == exits.VERDICT_UNSURE


def test_other_statuses_never_look_like_a_lost_link():
    result = {"returncode": 1, "output": "Connection refused"}

    assert exits.transport_verdict(result) == exits.VERDICT_FINE


def test_a_lost_link_is_spelled_out_for_the_model():
    result = {"returncode": 255, "output": "partial"}
    note = exits.no_status_note("laptop (dev@box)", "timed out")

    exits.annotate(result, "laptop", exits.STATE_NO_STATUS, note)

    assert result["onsite"] == {"target": "laptop", "state": "no_status"}
    assert result["output"].startswith("partial\n[onsite: no exit status")
    assert "It was not retried." in result["output"]


def test_a_refusal_says_nothing_ran():
    result = exits.refusal("laptop", "target is unreachable")

    assert result["returncode"] == exits.SSH_TRANSPORT_EXIT
    assert result["onsite"]["state"] == exits.STATE_NO_STATUS
    assert result["output"] == "[onsite: target is unreachable]"


def test_a_refusal_names_the_plugin_once():
    said = "onsite target laptop is unreachable: no answer."
    result = exits.refusal("laptop", said)

    assert result["output"] == (
        "[onsite: target laptop is unreachable: no answer.]"
    )

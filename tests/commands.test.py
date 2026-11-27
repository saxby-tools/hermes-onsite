"""The slash commands, without Hermes."""

from __future__ import annotations

from onsite import commands
from onsite import proposals
from onsite import runtime


SETTINGS = {
    "default": "mac",
    "targets": {"mac": {"kind": "local"}},
}

COMMAND = "make test"


class Context:
    """Stands in for Hermes' plugin context. Records what it is asked
    to hand to the agent, and answers as it was told to."""

    def __init__(self, accepts: bool = True):
        self.accepts = accepts
        self.handed = []

    def inject_message(self, text, **named):
        self.handed.append((text, named))

        return self.accepts


def build(tmp_path):
    session = runtime.SessionReader(cwd=lambda: "", session_id=lambda: "")

    return runtime.Runtime(
        load_settings=lambda: SETTINGS,
        data_dir=tmp_path,
        session=session,
        build_environment=lambda *args: object(),
    )


def test_run_names_the_session_it_came_from(tmp_path):
    built = build(tmp_path)
    ctx = Context()

    built.proposals.add("key-1", COMMAND, "runs the tests")

    said = commands.build_run_command(ctx, built)("1", session_key="key-1")

    text, named = ctx.handed[0]

    assert named == {"session_key": "key-1"}
    assert COMMAND in text
    assert "#1" in said
    assert built.proposals.get("key-1", 1).status == proposals.STATUS_SENT


def test_run_with_no_session_key_names_none(tmp_path):
    built = build(tmp_path)
    ctx = Context()

    built.proposals.add("id-1", COMMAND, "runs the tests")

    commands.build_run_command(ctx, built)("1", session_id="id-1")

    assert ctx.handed[0][1] == {}


def test_a_refused_run_says_how_to_allow_it(tmp_path):
    built = build(tmp_path)
    ctx = Context(accepts=False)

    built.proposals.add("key-1", COMMAND, "runs the tests")

    said = commands.build_run_command(ctx, built)("1", session_key="key-1")

    assert "allow_gateway_injection" in said
    assert built.proposals.get("key-1", 1).status == proposals.STATUS_PENDING

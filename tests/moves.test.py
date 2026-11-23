"""A moved session's agent is told once, on its next turn."""

from __future__ import annotations

from onsite import config
from onsite import hooks
from onsite import moves


PARSED = config.parse_config({
    "default": "mac",
    "targets": {
        "laptop": {
            "kind": "ssh",
            "host": "laptop.example",
            "user": "dev",
        },
        "mac": {"kind": "local"},
    },
})


class FakeRuntime:
    def __init__(self, parsed=PARSED):
        self.moves = moves.Moves()
        self._parsed = parsed

    def load_config(self):
        if self._parsed is None:
            raise config.ConfigError("the settings are not usable.")

        return self._parsed


def move(before: str, after: str, cleared: bool = False) -> moves.Move:
    return moves.Move(
        before=before,
        after=after,
        cwd="/home/dev/project",
        cleared=cleared,
    )


def test_a_move_is_handed_over_once():
    pending = moves.Moves()

    pending.note(move("mac", "laptop"), "session:abc", "stored-1")

    assert pending.take("session:abc").after == "laptop"
    assert pending.take("session:abc") is None


def test_a_move_is_found_by_any_id_the_session_goes_by():
    # The slash command and the turn do not see the same ids.
    pending = moves.Moves()

    pending.note(move("mac", "laptop"), "session:abc", "stored-1")

    assert pending.take("", "stored-1").after == "laptop"


def test_taking_by_one_id_clears_the_others():
    pending = moves.Moves()

    pending.note(move("mac", "laptop"), "session:abc", "stored-1")
    pending.take("stored-1")

    assert pending.take("session:abc") is None


def test_one_session_does_not_take_another_sessions_move():
    pending = moves.Moves()

    pending.note(move("mac", "laptop"), "session:abc")

    assert pending.take("session:xyz") is None
    assert pending.take("session:abc") is not None


def test_an_empty_id_never_matches():
    # A caller with no id must not collect a stranger's move.
    pending = moves.Moves()

    pending.note(move("mac", "laptop"), "", "session:abc")

    assert pending.take("") is None


def test_two_moves_before_a_turn_read_as_one_trip():
    pending = moves.Moves()

    pending.note(move("mac", "laptop"), "session:abc")
    pending.note(move("laptop", "desk"), "session:abc")

    taken = pending.take("session:abc")

    assert taken.before == "mac"
    assert taken.after == "desk"


def test_a_trip_that_ends_where_it_began_says_nothing():
    pending = moves.Moves()

    pending.note(move("mac", "laptop"), "session:abc", "stored-1")
    pending.note(move("laptop", "mac", cleared=True), "session:abc")

    assert pending.take("session:abc") is None
    assert pending.take("stored-1") is None


def test_a_move_to_the_same_machine_says_nothing():
    pending = moves.Moves()

    pending.note(move("laptop", "laptop"), "session:abc")

    assert pending.take("session:abc") is None


def test_old_moves_are_dropped_past_the_limit():
    pending = moves.Moves()

    for number in range(moves.MAX_PENDING + 5):
        pending.note(move("mac", "laptop"), f"session:{number}")

    assert pending.take("session:0") is None
    assert pending.take(f"session:{moves.MAX_PENDING + 4}") is not None


# ---- the hook that carries a move to the agent ------------------------


def test_the_turn_after_a_move_carries_the_note(monkeypatch):
    monkeypatch.setattr(hooks, "session_names", lambda: [])
    monkeypatch.setattr(hooks, "screen_is_ours", lambda: False)
    fake = FakeRuntime()
    fake.moves.note(move("mac", "laptop"), "session:abc", "stored-1")

    hook = hooks.build_turn_hook(fake)
    said = hook(session_id="stored-1", task_id="other")

    assert "moved this session to `laptop`" in said["context"]


def test_the_turn_after_that_carries_nothing(monkeypatch):
    monkeypatch.setattr(hooks, "session_names", lambda: [])
    monkeypatch.setattr(hooks, "screen_is_ours", lambda: False)
    fake = FakeRuntime()
    fake.moves.note(move("mac", "laptop"), "stored-1")

    hook = hooks.build_turn_hook(fake)
    hook(session_id="stored-1")

    assert hook(session_id="stored-1") is None


def test_a_move_is_found_by_the_ids_hermes_holds_for_the_turn(
    monkeypatch,
):
    monkeypatch.setattr(hooks, "session_names", lambda: ["session:abc"])
    monkeypatch.setattr(hooks, "screen_is_ours", lambda: False)
    fake = FakeRuntime()
    fake.moves.note(move("mac", "laptop"), "session:abc")

    hook = hooks.build_turn_hook(fake)

    assert hook(session_id="unrelated", task_id="") is not None


def test_a_turn_in_another_session_carries_nothing(monkeypatch):
    monkeypatch.setattr(hooks, "session_names", lambda: [])
    monkeypatch.setattr(hooks, "screen_is_ours", lambda: False)
    fake = FakeRuntime()
    fake.moves.note(move("mac", "laptop"), "stored-1")

    hook = hooks.build_turn_hook(fake)

    assert hook(session_id="stored-2", task_id="session:xyz") is None


def test_a_move_to_a_target_since_removed_carries_nothing(monkeypatch):
    monkeypatch.setattr(hooks, "session_names", lambda: [])
    monkeypatch.setattr(hooks, "screen_is_ours", lambda: False)
    fake = FakeRuntime()
    fake.moves.note(move("mac", "desk"), "stored-1")

    hook = hooks.build_turn_hook(fake)

    assert hook(session_id="stored-1") is None


def test_broken_settings_carry_nothing(monkeypatch):
    monkeypatch.setattr(hooks, "session_names", lambda: [])
    monkeypatch.setattr(hooks, "screen_is_ours", lambda: False)
    fake = FakeRuntime(parsed=None)
    fake.moves.note(move("mac", "laptop"), "stored-1")

    hook = hooks.build_turn_hook(fake)

    assert hook(session_id="stored-1") is None


def test_settings_broken_for_one_turn_do_not_use_the_move_up(monkeypatch):
    monkeypatch.setattr(hooks, "session_names", lambda: [])
    monkeypatch.setattr(hooks, "screen_is_ours", lambda: False)
    fake = FakeRuntime(parsed=None)
    fake.moves.note(move("mac", "laptop"), "stored-1")

    hook = hooks.build_turn_hook(fake)

    assert hook(session_id="stored-1") is None

    fake._parsed = PARSED

    assert hook(session_id="stored-1") is not None


def test_the_note_warns_about_the_screen_when_the_plugin_runs_it(
    monkeypatch,
):
    monkeypatch.setattr(hooks, "session_names", lambda: [])
    monkeypatch.setattr(hooks, "screen_is_ours", lambda: True)
    fake = FakeRuntime()
    fake.moves.note(move("mac", "laptop"), "stored-1")

    said = hooks.build_turn_hook(fake)(session_id="stored-1")

    assert "`computer_use` tool did not move" in said["context"]

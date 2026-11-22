"""Pins and proposals survive a restart and stay per session."""

from __future__ import annotations

from hermes_hands import pins
from hermes_hands import proposals
from hermes_hands import store


class FakeClock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


def test_a_missing_file_reads_as_empty(tmp_path):
    assert store.JsonStore(tmp_path / "none.json").read() == {}


def test_a_corrupt_file_reads_as_empty(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")

    assert store.JsonStore(path).read() == {}


def test_a_write_is_private_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "nested" / "data.json"

    store.JsonStore(path).update(lambda data: data.update(a=1))

    assert store.JsonStore(path).read() == {"a": 1}
    assert path.stat().st_mode & 0o777 == store.FILE_MODE
    assert [item.name for item in path.parent.iterdir()] == ["data.json"]


def test_a_session_pin_outlives_the_process(tmp_path):
    path = tmp_path / "pins.json"
    first = pins.Pins(store.JsonStore(path))

    first.set("session:abc", "laptop", pins.SOURCE_ROUTE, "/work", "id-1")

    second = pins.Pins(store.JsonStore(path))
    pin = second.get("session:abc")

    assert pin.target == "laptop"
    assert pin.is_override is False
    assert second.find("id-1").target == "laptop"


def test_a_sessionless_pin_is_never_written(tmp_path):
    path = tmp_path / "pins.json"
    first = pins.Pins(store.JsonStore(path))

    first.set("default", "laptop", pins.SOURCE_OVERRIDE)

    assert first.get("default").target == "laptop"
    assert pins.Pins(store.JsonStore(path)).get("default") is None
    assert not path.exists()


def test_clearing_a_pin_removes_it(tmp_path):
    kept = pins.Pins(store.JsonStore(tmp_path / "pins.json"))

    kept.set("session:abc", "laptop", pins.SOURCE_OVERRIDE)

    assert kept.clear("session:abc") is True
    assert kept.get("session:abc") is None
    assert kept.clear("session:abc") is False


def test_old_pins_are_dropped_on_the_next_write(tmp_path):
    clock = FakeClock()
    kept = pins.Pins(store.JsonStore(tmp_path / "pins.json"), clock)

    kept.set("session:old", "laptop", pins.SOURCE_ROUTE)
    clock.now += pins.MAX_AGE_SECONDS + 1
    kept.set("session:new", "mac", pins.SOURCE_ROUTE)

    assert kept.get("session:old") is None
    assert kept.get("session:new").target == "mac"


def test_proposals_count_up_per_session(tmp_path):
    kept = proposals.Proposals(store.JsonStore(tmp_path / "p.json"))

    one = kept.add("session:a", "make test", "check the build")
    two = kept.add("session:a", "git status")
    other = kept.add("session:b", "ls")

    assert (one.number, two.number, other.number) == (1, 2, 1)
    assert kept.get("session:a", 2).command == "git status"
    assert kept.get("session:a", 9) is None


def test_a_session_is_found_by_any_of_its_ids(tmp_path):
    kept = proposals.Proposals(store.JsonStore(tmp_path / "p.json"))

    kept.add("session:a", "make test", ids=("id-1",))

    assert kept.list("id-1")[0].command == "make test"
    assert kept.add("id-1", "make lint").number == 2


def test_a_sent_proposal_is_marked(tmp_path):
    kept = proposals.Proposals(store.JsonStore(tmp_path / "p.json"))

    kept.add("session:a", "make test")

    assert kept.mark("session:a", 1, proposals.STATUS_SENT) is True
    assert kept.get("session:a", 1).status == proposals.STATUS_SENT
    assert kept.mark("session:a", 7, proposals.STATUS_SENT) is False


def test_numbers_are_never_reused_past_the_cap(tmp_path):
    kept = proposals.Proposals(store.JsonStore(tmp_path / "p.json"))

    for index in range(proposals.MAX_PER_SESSION + 5):
        kept.add("session:a", f"echo {index}")

    listed = kept.list("session:a")

    assert len(listed) == proposals.MAX_PER_SESSION
    assert listed[-1].number == proposals.MAX_PER_SESSION + 5
    assert kept.get("session:a", 1) is None

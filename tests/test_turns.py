"""turns.py: the deadline, the budget and the feedback loop every command shares."""
from __future__ import annotations

import time

from fake_server import FakeServer
from jobloop.turns import Api, Turns


def turns(tmp_path, replies, **kw):
    server = FakeServer(replies)
    return Turns(Api("http://x", server.transport()), tmp_path / "log.jsonl", **kw), server


def test_at_the_deadline_the_turn_in_flight_is_cancelled(tmp_path):
    t, server = turns(tmp_path, [], minutes=0.0005)
    with t:
        t.api.current = "resp_7"
        time.sleep(0.3)
    assert server.cancelled == ["resp_7"] and t.stopped() == "time"


def test_leaving_early_disarms_the_deadline(tmp_path):
    t, server = turns(tmp_path, [], minutes=0.0005)
    with t:
        pass
    time.sleep(0.2)
    assert server.cancelled == []


def test_at_the_budget_no_new_turn_starts(tmp_path):
    t, server = turns(tmp_path, [{"cost": 0.5}, {"cost": 0.5}], budget=0.4)
    with t:
        assert t.turn("A", "h", "x", "i") is not None
        assert t.turn("B", "h", "x", "i") is None
    assert len(server.requests) == 1 and t.stopped() == "budget"


def test_ctrl_c_cancels_the_turn_and_ends_the_run_cleanly(tmp_path):
    t, server = turns(tmp_path, [])
    with t:
        t.api.current = "resp_3"
        raise KeyboardInterrupt
    assert server.cancelled == ["resp_3"] and t.stopped() == "interrupted"


def test_settle_sends_every_failure_back_verbatim_on_the_same_conversation(tmp_path):
    t, server = turns(tmp_path, [{"files": {"a": "1"}}, {"files": {"a": "2"}}])
    seen = []

    def validate(record):
        seen.append(record["id"])
        return ["line one\nline two", "second"] if record["id"] == "resp_1" else []

    with t:
        first = t.turn("DO", "h/m", "x", "role")
        assert t.settle(first, validate, 2, "role")["id"] == "resp_2"
    fix = server.requests[1]
    assert fix["previous_response_id"] == "resp_1" and fix["model"] == "h/m" and fix["input"][0]["text"] == "line one\nline two\n\nsecond"


def test_settle_gives_up_after_its_rounds(tmp_path):
    t, server = turns(tmp_path, [{}, {}, {}])
    with t:
        assert t.settle(t.turn("DO", "h", "x", "i"), lambda r: ["still wrong"], 2, "i") is None
    assert len(server.requests) == 3


def test_a_file_the_turn_did_not_write_is_none(tmp_path):
    t, _ = turns(tmp_path, [{"files": {"a": "1"}}])
    with t:
        record = t.turn("DO", "h", "x", "i")
        assert t.files(record, "a", "b") == {"a": "1", "b": None}


def test_the_listing_is_the_whole_workspace_not_only_what_the_last_turn_changed(tmp_path):
    t, _ = turns(tmp_path, [{"files": {"a": "1", "b": "2"}}, {"files": {"a": "3"}}])
    with t:
        second = t.turn("FIX", "h", "x", "i", previous=t.turn("DO", "h", "x", "i")["id"])
        assert t.api.listing(second["id"]) == ["a", "b"] and second["harness"]["files"] == ["a"]


def test_a_turn_that_reports_no_cost_still_has_its_tokens_counted(tmp_path):
    t, _ = turns(tmp_path, [{"cost": None, "usage": {"input_tokens": 1000, "output_tokens": 50}}, {"cost": 0.2, "usage": {"input_tokens": 9, "output_tokens": 9}}])
    with t:
        t.turn("A", "codex", "x", "i")
        t.turn("B", "claude-code", "x", "i")
    assert t.unpriced == 1 and t.unpriced_tokens == 1050 and "1 turns reported no cost, 1,050 tokens" in t.summary()

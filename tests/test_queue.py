"""The person's queue helpers: list (soonest deadline first), show, and done, which never marks anything applied."""
from __future__ import annotations

import pytest

import sim
from jobloop.engine import queue
from jobloop.engine.store import Store


def put(store, key, status, deadline="", title=None, fit=80):
    store.queue_put(key, status, f"me/queue/{key}", {"title": title or key, "fit": fit, "deadline": deadline, "why": "", "url": "https://x.example"})


def test_the_queue_lists_the_soonest_deadline_first_and_no_deadline_last(tmp_path):
    with Store(tmp_path) as store:
        put(store, "late__dev", "ready", "2026-12-01")
        put(store, "none__dev", "ready", "")
        put(store, "soon__dev", "needs_decision", "2026-10-25")
        put(store, "same__dev", "ready", "2026-10-25")
        put(store, "gone__dev", "done", "2026-10-01")
        text = queue.list_text(store)
    order = [line.split()[-1] for line in text.splitlines() if line.startswith("  ") and "__" in line.split()[-1] and line.split()[0] not in ("deadline",)]
    keys = [k for k in order if k.endswith("__dev")]
    assert keys == ["same__dev", "soon__dev", "late__dev", "none__dev"]       # same deadline: ready before needs_decision
    assert "gone__dev" not in text and "1 done" in text and "no deadline" in text


def test_an_empty_queue_says_so(tmp_path):
    with Store(tmp_path) as store:
        assert queue.list_text(store) == "The queue is empty."


def test_done_marks_the_row_only_and_says_what_to_do_next(tmp_path):
    me = sim.make_me(tmp_path, tracker_rows=[("Acme", "Dev", "drafted")])
    before = (me / "tracker.csv").read_bytes()
    with Store(me) as store:
        put(store, "acme__dev", "ready")
        message = queue.done(store, "acme__dev")
        assert store.queue_item("acme__dev")["status"] == "done"
        assert queue.done(store, "acme__dev")                                # twice is fine
    assert "now run: jobloop tracker set acme__dev applied after you have submitted" in message
    assert (me / "tracker.csv").read_bytes() == before


def test_unknown_keys_are_refused_in_plain_words(tmp_path):
    with Store(tmp_path) as store:
        put(store, "a__b", "ready")
        with pytest.raises(queue.QueueError, match="no queue entry 'x__y'"):
            queue.done(store, "x__y")
        with pytest.raises(queue.QueueError, match="no queue entry"):
            queue.show_text(tmp_path, store, "x__y")


def test_entry_dir_stays_inside_the_queue(tmp_path):
    assert queue.entry_dir(tmp_path, "acme__dev") == tmp_path / "queue" / "acme__dev"
    for bad in ("../x", "a/b", "", "..", "_x"):
        with pytest.raises(queue.QueueError):
            queue.entry_dir(tmp_path, bad)


def test_show_prints_the_checklist_and_the_files(tmp_path):
    folder = queue.entry_dir(tmp_path, "acme__dev")
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("# Acme\n\n1. Read both documents\n")
    (folder / "cv.pdf").write_bytes(b"x")
    with Store(tmp_path) as store:
        put(store, "acme__dev", "ready", "2026-11-01", title="Acme — Dev")
        text = queue.show_text(tmp_path, store, "acme__dev")
    assert "Acme — Dev  [ready]" in text and "Read both documents" in text and "files: README.md, cv.pdf" in text
    assert "apply:  https://x.example" in text

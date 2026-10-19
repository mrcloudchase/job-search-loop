"""state.py: the posting store, and what must never happen to it (a corrupt file overwritten, a rank field lost, a status downgraded)."""
from __future__ import annotations

import json
import multiprocessing
import os
from datetime import date

import pytest

from jobloop import state

D1, D2 = date(2026, 10, 1), date(2026, 10, 9)


def found(**kw):
    return {"key": "acme_engineer", "title": "Engineer", "company": "Acme", "url": "https://x.io/1", "board": "freehire",
            "source": "board", "posted_date": "2026-09-30", "deadline": None, "locations": ["Aarhus"], **kw}


def test_missing_state_file_starts_empty(tmp_path):
    assert state.load(tmp_path) == {} and not state.path(tmp_path).exists()


@pytest.mark.parametrize("text", ["", "  \n"])
def test_an_empty_file_is_an_empty_store(tmp_path, text):
    state.path(tmp_path).parent.mkdir(parents=True)
    state.path(tmp_path).write_text(text)
    assert state.load(tmp_path) == {}


def test_state_is_saved_under_me_state_and_round_trips(tmp_path):
    s = {}
    state.upsert(s, found(), D1)
    state.save(tmp_path, s)
    assert state.path(tmp_path) == tmp_path / "state" / "postings.json"
    assert state.load(tmp_path) == s and json.loads(state.path(tmp_path).read_text())["postings"]["acme_engineer"]["first_seen"] == "2026-10-01"
    assert sorted(p.name for p in (tmp_path / "state").iterdir()) == ["postings.json", "postings.json.lock"]


@pytest.mark.parametrize("text", ["{not json", "[]", '{"postings": []}', '{"postings": {"k": 3}}', '"x"', '{"seen": {}}'])
def test_a_corrupt_state_file_is_never_overwritten(tmp_path, text):
    state.path(tmp_path).parent.mkdir(parents=True)
    state.path(tmp_path).write_text(text)
    with pytest.raises(state.StateCorrupt, match="move it aside"):
        state.load(tmp_path)
    with pytest.raises(state.StateCorrupt):
        state.save(tmp_path, {"k": {}})
    with pytest.raises(state.StateCorrupt):
        with state.transaction(tmp_path) as s:
            s["k"] = {}
    assert state.path(tmp_path).read_text() == text


def test_a_failed_transaction_saves_nothing(tmp_path):
    state.save(tmp_path, {"k": {"key": "k"}})
    with pytest.raises(RuntimeError):
        with state.transaction(tmp_path) as s:
            s["other"] = {"key": "other"}
            raise RuntimeError("boom")
    assert list(state.load(tmp_path)) == ["k"]


def test_the_write_is_atomic_a_failing_write_leaves_the_old_file(tmp_path, monkeypatch):
    state.save(tmp_path, {"k": {"key": "k"}})
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        state.save(tmp_path, {"changed": {}})
    assert list(state.load(tmp_path)) == ["k"] and not [p for p in (tmp_path / "state").iterdir() if p.suffix == ".tmp"]


def _add(args):
    me, n = args
    with state.transaction(me) as s:
        s[f"k{n}"] = {"key": f"k{n}"}


def test_concurrent_transactions_lose_nothing(tmp_path):
    with multiprocessing.get_context("fork").Pool(4) as pool:
        pool.map(_add, [(str(tmp_path), n) for n in range(12)])
    assert sorted(state.load(tmp_path)) == sorted(f"k{n}" for n in range(12))


def test_a_new_posting_gets_the_schema_with_dates_and_provenance():
    s = {}
    entry = state.upsert(s, found(deadline="2026-11-01"), D1)
    assert entry == {"key": "acme_engineer", "title": "Engineer", "company": "Acme", "url": "https://x.io/1", "alt_urls": [],
                     "board": "freehire", "source": "board", "posted_date": "2026-09-30", "deadline": "2026-11-01",
                     "first_seen": "2026-10-01", "last_seen": "2026-10-01", "locations": ["Aarhus"], "status": "new", "tags": []}
    assert s["acme_engineer"] is entry


def test_posted_date_is_null_when_the_board_gave_none_and_never_inferred():
    entry = state.upsert({}, found(posted_date=None), D1)
    assert entry["posted_date"] is None and entry["deadline"] is None


def test_seeing_it_again_updates_last_seen_and_keeps_first_seen_and_a_known_date():
    s = {}
    state.upsert(s, found(), D1)
    entry = state.upsert(s, found(posted_date=None, locations=["aarhus", "Odense"]), D2)
    assert (entry["first_seen"], entry["last_seen"], entry["posted_date"]) == ("2026-10-01", "2026-10-09", "2026-09-30")
    assert entry["locations"] == ["Aarhus", "Odense"]


def test_a_changed_deadline_is_taken_but_a_missing_one_never_erases_it():
    s = {}
    state.upsert(s, found(deadline="2026-11-01"), D1)
    assert state.upsert(s, found(deadline=None), D2)["deadline"] == "2026-11-01"
    assert state.upsert(s, found(deadline="2026-11-15"), D2)["deadline"] == "2026-11-15"


RANK = {"rank_score": 81, "rank_verdict": "PASS", "rank_date": "2026-10-02", "gates": {"language": "ok"}, "strengths": ["a"], "gaps": ["b"],
        "fetch_attempts": 2, "first_unreachable": "2026-10-03", "last_fetch_error": "blocked", "something_new": {"x": 1}}


def test_rewrite_preserves_rank_fields():
    s = {}
    state.upsert(s, found(), D1)
    s["acme_engineer"].update(RANK, status="ranked")
    entry = state.upsert(s, found(locations=["Odense"], tags=["mass_posted"]), D2)
    assert all(entry[k] == v for k, v in RANK.items()) and entry["status"] == "ranked" and entry["tags"] == ["mass_posted"]


@pytest.mark.parametrize("settled", ["applied", "skipped", "gave_up", "expired"])
def test_a_settled_status_is_never_downgraded(settled):
    s = {}
    state.upsert(s, found(), D1)
    s["acme_engineer"]["status"] = settled
    for incoming in ("new", "ranked", "unreachable", None):
        assert state.upsert(s, found(status=incoming), D2)["status"] == settled


@pytest.mark.parametrize("current,after", [("new", "expired"), ("ranked", "expired"), ("unreachable", "expired"),
                                           ("applied", "applied"), ("skipped", "skipped"), ("gave_up", "gave_up")])
def test_a_closed_posting_expires_what_is_open_and_nothing_settled(current, after):
    s = {}
    state.upsert(s, found(), D1)
    s["acme_engineer"]["status"] = current
    assert state.upsert(s, found(status="expired"), D2)["status"] == after


@pytest.mark.parametrize("current", ["ranked", "unreachable"])
def test_a_status_set_by_ranking_is_not_reset_to_new_by_a_search(current):
    s = {}
    state.upsert(s, found(), D1)
    s["acme_engineer"]["status"] = current
    assert state.upsert(s, found(), D2)["status"] == current


def test_a_fallback_never_replaces_a_boards_provenance_but_fills_a_missing_one():
    s = {}
    state.upsert(s, found(), D1)
    assert state.upsert(s, found(source="fallback", board="linkedin"), D2)["source"] == "board"
    assert state.upsert(s, found(source="fallback"), D2)["board"] == "freehire"
    assert state.upsert({}, found(source="fallback"), D1)["source"] == "fallback"


def test_another_address_is_kept_and_found_whatever_the_key():
    s = {"old-rule-key": {"key": "old-rule-key", "url": "https://X.io/1/?utm_source=a", "alt_urls": ["https://y.io/z"]}}
    index = state.url_index(s)
    assert index["https://x.io/1"] == "old-rule-key" and index["https://y.io/z"] == "old-rule-key"
    entry = state.upsert({}, found(), D1)
    state.upsert({"acme_engineer": entry}, found(url="https://other.io/9"), D2)
    assert entry["alt_urls"] == ["https://other.io/9"] and entry["url"] == "https://x.io/1"


@pytest.mark.parametrize("bad", [{"source": "web"}, {"status": "done"}])
def test_an_unknown_source_or_status_is_refused(bad):
    with pytest.raises(ValueError):
        state.upsert({}, found(**bad), D1)

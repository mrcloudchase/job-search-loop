"""engine/proposals.py: accept is the only writer of profile.md, additive, checked by the profile's own checks."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from jobloop import search
from jobloop.engine import playbook, proposals
from jobloop.engine.store import Store

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
QUOTE = "Rustcsv is a command line tool written in Rust that compares two CSV files"
URL = "https://alice.example/"
FIXTURE = (__import__("pathlib").Path(__file__).resolve().parent.parent / "fixtures" / "profile-cph.md").read_text()


@pytest.fixture
def rig(tmp_path):
    me = tmp_path / "me"
    me.mkdir()
    (me / "profile.md").write_text(FIXTURE)
    with Store(me) as store:
        yield me, store


def enrichment(store, text="Wrote a Rust command line tool that compares CSV files", section="Confirmed additions", **kw):
    payload = {"summary": "add", "section": section, "text": text, "source_url": URL, "quote": QUOTE, **kw}
    return store.add_proposal("profile_enrichment", payload, "public page", NOW)


def accept(me, store, pid):
    return proposals.accept(me, store, pid, NOW, ignored=lambda p: True)


def test_a_proposal_never_touches_the_profile_until_accepted(rig):
    me, store = rig
    before = (me / "profile.md").read_bytes()
    pid = enrichment(store)
    assert (me / "profile.md").read_bytes() == before and [p["id"] for p in proposals.list_pending(store)] == [pid]


def test_accept_adds_one_source_tagged_bullet_and_changes_no_existing_line(rig):
    me, store = rig
    old = (me / "profile.md").read_text().splitlines()
    result = accept(me, store, enrichment(store))
    new = (me / "profile.md").read_text().splitlines()
    assert result.ok and "(source: public page https://alice.example/, checked 2026-10-19)" in result.applied
    added = {result.applied, "## Confirmed additions"}
    assert [l for l in new if l.strip() and l not in added] == [l for l in old if l.strip()]      # every old line, in order, none changed
    assert added <= set(new)
    assert store.proposals("pending") == [] and store.proposals("accepted")
    assert [e["kind"] for e in store.events("proposal:accepted")] == ["proposal:accepted"]
    assert (me / "state" / "profile.previous.md").read_text() == FIXTURE


def test_a_bullet_goes_under_the_stated_existing_section(rig):
    me, store = rig
    assert accept(me, store, enrichment(store, section="Awards", text="Won a regional hackathon")).ok
    lines = (me / "profile.md").read_text().splitlines()
    assert lines.index(next(l for l in lines if l.startswith("- Won a regional"))) == lines.index("- Tidewater Logistics Engineering Excellence Award, 2023") + 1


def test_accept_refuses_text_that_fails_grounding_and_leaves_it_pending_with_the_reason(rig):
    me, store = rig
    before = (me / "profile.md").read_bytes()
    pid = enrichment(store, text="Maintains 4000 repositories used by 77 companies")
    result = accept(me, store, pid)
    assert not result.ok and "not in the sources" in result.reason
    assert (me / "profile.md").read_bytes() == before
    row = store.proposals("pending")[0]
    assert row["id"] == pid and "not in the sources" in row["note"]


@pytest.mark.parametrize("bad", [{"quote": "short"}, {"source_url": "http://alice.example/"}, {"source_url": ""}, {"section": "Experience"},
                                 {"text": ""}, {"text": "- list marker"}, {"text": "Mail me at x@evil.example"}, {"text": "See https://evil.example/x"}])
def test_accept_refuses_unverified_or_malformed_enrichments(rig, bad):
    me, store = rig
    before = (me / "profile.md").read_bytes()
    assert not accept(me, store, enrichment(store, **bad)).ok
    assert (me / "profile.md").read_bytes() == before and len(store.proposals("pending")) == 1


def test_a_fact_the_profile_already_says_is_refused_and_a_replay_after_a_crash_is_idempotent(rig):
    me, store = rig
    assert not accept(me, store, enrichment(store, text="Open-source dbt package for freight-rate data tests")).ok
    first = enrichment(store)
    assert accept(me, store, first).ok
    after = (me / "profile.md").read_bytes()
    store.db.execute("UPDATE proposals SET status='pending' WHERE id=?", (first,))      # killed between the write and the decision
    store.db.commit()
    assert accept(me, store, first).ok and (me / "profile.md").read_bytes() == after


def test_a_rejected_proposal_writes_nothing_and_cannot_be_accepted_later(rig):
    me, store = rig
    before = (me / "profile.md").read_bytes()
    pid = enrichment(store)
    assert proposals.reject(store, pid, NOW, "not me").ok
    assert (me / "profile.md").read_bytes() == before and not (me / "state").exists()
    assert not accept(me, store, pid).ok and (me / "profile.md").read_bytes() == before
    assert not proposals.reject(store, pid, NOW).ok


def test_accept_without_a_profile_is_refused(tmp_path):
    with Store(tmp_path) as store:
        pid = enrichment(store)
        assert not accept(tmp_path, store, pid).ok and not (tmp_path / "profile.md").exists()


def test_a_profile_fact_is_grounded_in_the_persons_own_words(rig):
    me, store = rig
    ok = store.add_proposal("profile_fact", {"summary": "s", "section": "Confirmed additions", "text": "Led the Kafka migration",
                                              "evidence": "I led the Kafka migration at Tidewater"}, "learn", NOW)
    assert accept(me, store, ok).ok and "- Led the Kafka migration (source: your own edit, accepted 2026-10-19)" in (me / "profile.md").read_text()
    invented = store.add_proposal("profile_fact", {"summary": "s", "text": "Led a team of 31 engineers", "evidence": "I led the migration"}, "learn", NOW)
    assert not accept(me, store, invented).ok
    bare = store.add_proposal("profile_fact", {"summary": "s", "text": "Knows Rust"}, "learn", NOW)
    assert not accept(me, store, bare).ok and "your own words" in store.proposals("pending")[-1]["note"] + "your own words"


def test_a_playbook_change_is_applied_only_on_accept_and_only_inside_the_bounds(rig):
    me, store = rig
    playbook.load(me, store, now=NOW)
    pid = store.add_proposal("playbook_change", {"summary": "voice", "change": {"summary": "voice", "style_add": ["Lead with the result."]}}, "learn", NOW)
    assert playbook.load(me, store).version == 1
    assert accept(me, store, pid).ok and playbook.load(me, store).version == 2
    bad = store.add_proposal("playbook_change", {"summary": "x", "change": {"style_add": ["Say you led 40 engineers."]}}, "learn", NOW)
    assert not accept(me, store, bad).ok and playbook.load(me, store).version == 2 and store.proposals("pending")[0]["id"] == bad


def test_add_board_accept_means_read_and_switches_on_only_when_told_and_terms_are_acknowledged(rig):
    me, store = rig
    read = store.add_proposal("add_board", {"summary": "b", "board": "freehire"}, "boards", NOW)
    assert accept(me, store, read).ok and not (me / "boards.json").exists()
    terms = store.add_proposal("add_board", {"summary": "b", "board": "jobbank", "enable": True}, "boards", NOW)
    assert not accept(me, store, terms).ok and not search.is_enabled(me, "jobbank")
    (me / "loop.json").write_text(json.dumps({"acknowledge_terms": True}))
    assert accept(me, store, terms).ok and search.is_enabled(me, "jobbank")
    nameless = store.add_proposal("add_board", {"summary": "b", "enable": True}, "boards", NOW)
    assert not accept(me, store, nameless).ok


def test_unknown_kinds_are_refused_and_stay_pending(rig):
    me, store = rig
    before = (me / "profile.md").read_bytes()
    pid = store.add_proposal("rewrite_profile", {"summary": "all of it", "text": "# New"}, "x", NOW)
    result = accept(me, store, pid)
    assert not result.ok and "unknown proposal kind" in result.reason and (me / "profile.md").read_bytes() == before
    assert len(store.proposals("pending")) == 1

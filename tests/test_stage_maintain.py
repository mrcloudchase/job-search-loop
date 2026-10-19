"""stages/maintain.py: expiry sweep, one proposal per stale application (never resolved by a pass), the escaped static report."""
from __future__ import annotations

import csv
import json
from datetime import date, timedelta

import pytest

import sim
from jobloop import archive, state, tracker
from jobloop.engine import snapshot
from jobloop.stages import maintain as stage

TODAY = date(2026, 10, 19)


def ago(days):
    return (TODAY - timedelta(days=days)).isoformat()


def put_rows(me, *rows):
    path = me / "tracker.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(tracker.HEADER)
        for r in rows:
            w.writerow([r.get(c, "") for c in tracker.HEADER])


def app(company, status="applied", days=70, role="Dev"):
    return {"company": company, "role": role, "status": status, "date": ago(days)}


def world(tmp_path, *rows, postings=None):
    me = sim.make_me(tmp_path, postings=postings)
    put_rows(me, *rows)
    return me


def run(tmp_path, me, now=sim.NOW):
    ctx, server = sim.context(tmp_path, me=me, now=now)
    actions = stage.STAGE.propose(ctx.snapshot)
    return ctx, actions, stage.STAGE.run(actions[0], ctx) if actions else None


def stale_proposals(ctx, status="pending"):
    return [json.loads(r["payload"]) for r in ctx.store.proposals(status) if r["kind"] == "stale_resolution"]


def test_propose_follows_the_cadence(tmp_path):
    ctx, actions, out = run(tmp_path, world(tmp_path))
    assert [a.key for a in actions] == ["*"] and out.terminal == "success"
    ctx.store.record_attempt(ctx.run_id, "maintain", "*", "r", "h", "2026-10-19T11:00:00+00:00", "2026-10-19T11:00:00+00:00", "success", 0, 0, [], [])
    assert stage.STAGE.propose(sim.context(tmp_path, me=ctx.me)[0].snapshot) == []
    assert stage.STAGE.spec.rank == 1 and stage.STAGE.spec.roles == ()


def test_one_proposal_per_stale_candidate_and_nothing_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("input() called")))
    me = world(tmp_path, app("Old Co", days=90), app("Older Co", "interview", days=120), app("Fresh Co", days=10), app("Draft Co", "drafted", days=200))
    before = snapshot.fingerprint(me)
    ctx, _, out = run(tmp_path, me)
    assert out.terminal == "success" and snapshot.fingerprint(me) == before
    found = stale_proposals(ctx)
    assert sorted(p["key"] for p in found) == sorted([archive.key("Old Co", "Dev"), archive.key("Older Co", "Dev")])
    for p in found:
        assert p["options"] == ["no_response", "withdrawn", "keep"] and p["quiet_days"] >= 60 and p["summary"]
    queue = ctx.store.queue("needs_decision")
    assert len(queue) == 2 and all(json.loads(q["data"])["deadline"] == "" for q in queue)


def test_passes_are_idempotent_about_pending_proposals(tmp_path):
    me = world(tmp_path, app("Old Co", days=90))
    ctx, _, _ = run(tmp_path, me)
    ctx2, _, _ = run(tmp_path, me, now=sim.NOW + timedelta(days=2))
    assert len(stale_proposals(ctx2)) == 1 and len(ctx2.store.proposals()) == 1


def test_accept_stale_goes_through_the_tracker_and_closes_the_proposal(tmp_path):
    me = world(tmp_path, app("Old Co", days=90), app("Other Co", days=90))
    ctx, _, _ = run(tmp_path, me)
    key = archive.key("Old Co", "Dev")
    done = stage.accept_stale(me, ctx.store, key, "no_response", TODAY)
    rows = {r.key: r for r in tracker.read_rows(me / "tracker.csv")}
    assert rows[key].status == "no_response" and rows[archive.key("Other Co", "Dev")].status == "applied" and key in done
    assert [p["key"] for p in stale_proposals(ctx)] == [archive.key("Other Co", "Dev")]
    assert ctx.store.queue_item(f"stale:{key}")["status"] == "done"
    stage.accept_stale(me, ctx.store, archive.key("Other Co", "Dev"), "withdrawn", TODAY)
    assert {r.key: r.status for r in tracker.read_rows(me / "tracker.csv")}[archive.key("Other Co", "Dev")] == "withdrawn"


def test_accept_stale_refuses_what_is_not_stale_or_not_an_option(tmp_path):
    me = world(tmp_path, app("Fresh Co", days=5), app("Old Co", days=90), app("Done Co", "rejected", days=200))
    before = snapshot.fingerprint(me)
    for name in ("Fresh Co", "Done Co", "Nobody"):
        with pytest.raises(ValueError, match="not a stale application"):
            stage.accept_stale(me, None, archive.key(name, "Dev"), "no_response", TODAY)
    with pytest.raises(ValueError, match="choose one of"):
        stage.accept_stale(me, None, archive.key("Old Co", "Dev"), "hired", TODAY)
    assert snapshot.fingerprint(me) == before


def test_keep_changes_nothing_and_is_not_raised_again_soon(tmp_path):
    me = world(tmp_path, app("Old Co", days=90))
    ctx, _, _ = run(tmp_path, me)
    key = archive.key("Old Co", "Dev")
    before = snapshot.fingerprint(me)
    stage.accept_stale(me, ctx.store, key, "keep", TODAY, now=sim.NOW)
    assert snapshot.fingerprint(me) == before
    ctx2, _, _ = run(tmp_path, me, now=sim.NOW + timedelta(days=3))
    assert len(ctx2.store.proposals()) == 1 and ctx2.store.proposals()[0]["status"] == "rejected"
    ctx3, _, _ = run(tmp_path, me, now=sim.NOW + timedelta(days=40))
    assert len(stale_proposals(ctx3)) == 1


def test_report_is_regenerated_and_escaped(tmp_path):
    me = world(tmp_path, app("<script>alert(1)</script> & Co", days=10))
    (me / "report.html").write_text("old")
    _, _, out = run(tmp_path, me)
    text = (me / "report.html").read_text(encoding="utf-8")
    assert out.terminal == "success" and "<script>alert(1)" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_expiry_sweep_expires_only_ranked_postings_past_their_deadline(tmp_path):
    posts = [sim.posting("late", "A", "Dev", status="ranked", score=70, deadline="2026-10-01"),
             sim.posting("open", "B", "Dev", status="ranked", score=70, deadline="2026-11-30"),
             sim.posting("none", "C", "Dev", status="ranked", score=70),
             sim.posting("newone", "D", "Dev", status="new", deadline="2026-10-01")]
    me = world(tmp_path, postings=posts)
    _, _, out = run(tmp_path, me)
    store = state.load(me)
    assert {k: v["status"] for k, v in store.items()} == {"late": "expired", "open": "ranked", "none": "ranked", "newone": "new"}
    assert any("expired 1" in n for n in out.notes)


def test_a_corrupt_posting_store_is_blocked_not_overwritten(tmp_path):
    me = world(tmp_path)
    (me / "state").mkdir(exist_ok=True)
    (me / "state" / "postings.json").write_text("{not json")
    _, _, out = run(tmp_path, me)
    assert out.terminal == "blocked" and (me / "state" / "postings.json").read_text() == "{not json"

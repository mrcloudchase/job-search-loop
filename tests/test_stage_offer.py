"""The offer stage: only for the person's `offer` rows, terms from me/offers/<key>.md, grounded analysis, nothing contacted."""
from __future__ import annotations

import builtins
import json

import pytest

import sim
from jobloop import archive, offer as O, tracker
from jobloop.engine import config as cfgmod, invariants, snapshot
from jobloop.engine.spec import Action
from jobloop.stages import offer as stage
from test_offer import ANALYSIS, DB1, PREP, TERMS

KEY = "acme__senior_data_engineer"
GOOD = {"files": {O.ANALYSIS: ANALYSIS, O.PREP: PREP}, "cost": 0.2}


def setup(tmp_path, replies=(), status="offer", terms=TERMS, **config):
    me = sim.make_me(tmp_path, tracker_rows=[("Acme", "Senior Data Engineer", status)])
    archive.open_application(me, "Acme", "Senior Data Engineer")
    if terms is not None:
        (me / "offers").mkdir()
        (me / "offers" / f"{KEY}.md").write_text(terms)
    ctx, server = sim.context(tmp_path, list(replies), me=me, config=cfgmod.Config(**config).validate())
    return ctx, server


def proposed(ctx):
    return stage.STAGE.propose(snapshot.take(ctx.me, ctx.store, ctx.config, ctx.now))


def act(ctx):
    return proposed(ctx)[0]


def test_spec_matches_the_contract():
    s = stage.STAGE.spec
    assert (s.name, s.rank, s.roles, s.trigger.kind) == ("offer", 90, ("planner",), "state")


@pytest.mark.parametrize("status,expected", [("offer", 1), ("interview", 0), ("applied", 0), ("drafted", 0), ("hired", 0),
                                              ("offer_declined", 0), ("withdrawn", 0), ("rejected", 0)])
def test_only_the_persons_offer_status_is_analysed(tmp_path, status, expected):
    ctx, _ = setup(tmp_path, status=status)
    assert len(proposed(ctx)) == expected


def test_no_terms_file_is_blocked_with_a_needs_decision_entry_and_not_asked_twice(tmp_path):
    ctx, server = setup(tmp_path, terms=None)
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "blocked" and server.requests == [] and "me/offers/acme__senior_data_engineer.md" in out.failures[0]
    entry = ctx.store.queue_item(f"offer:{KEY}")
    assert entry["status"] == "needs_decision" and "paste the offer terms" in json.loads(entry["data"])["why"]
    assert proposed(ctx) == []                                         # asked once; it waits for the file
    (ctx.me / "offers").mkdir()
    (ctx.me / "offers" / f"{KEY}.md").write_text(TERMS)
    assert len(proposed(ctx)) == 1                                     # the file arrived


def test_terms_produce_the_analysis_the_prep_and_a_ready_entry_and_nothing_else_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: (_ for _ in ()).throw(AssertionError("no prompts")))
    ctx, server = setup(tmp_path, [GOOD])
    rows, before = tracker.read_rows(ctx.me / "tracker.csv"), snapshot.fingerprint(ctx.me)
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "success" and out.produced["harness"] == "claude-deepseek/deepseek-chat"
    folder = ctx.me / "applications" / KEY
    assert ANALYSIS.strip() in (folder / O.ANALYSIS).read_text() and (folder / O.PREP).read_text().strip() == PREP.strip()
    entry = ctx.store.queue_item(f"offer:{KEY}")
    data = json.loads(entry["data"])
    assert entry["status"] == "ready" and set(data) == {"title", "why", "deadline"} and data["deadline"] == ""
    assert "No salary benchmark was supplied" in data["why"] and data["why"].endswith("Your decision.")
    assert tracker.read_rows(ctx.me / "tracker.csv") == rows and invariants.violations(before, snapshot.fingerprint(ctx.me)) == []
    assert [r["model"] for r in server.requests] == ["claude-deepseek/deepseek-chat"] and len(server.requests) == 1
    assert proposed(ctx) == []                                         # current analysis: nothing more to do
    (ctx.me / "offers" / f"{KEY}.md").write_text(TERMS + "- Relocation: none\n")
    assert len(proposed(ctx)) == 1                                     # changed terms are analysed again


def test_figures_not_in_a_source_are_sent_back_verbatim_until_fixed(tmp_path):
    bad = {"files": {O.ANALYSIS: ANALYSIS.replace("DKK 700,000", "DKK 750,000"), O.PREP: PREP}}
    ctx, server = setup(tmp_path, [bad, {"files": {O.ANALYSIS: ANALYSIS}}])
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "success" and len(server.requests) == 2
    back = server.requests[1]["input"][0]["text"]
    assert "750,000" in back and "none of the sources" in back
    assert "750,000" not in (ctx.me / "applications" / KEY / O.ANALYSIS).read_text()


def test_documents_that_keep_failing_stall_and_nothing_is_saved(tmp_path):
    bad = {"files": {O.ANALYSIS: ANALYSIS.replace(DB1, "four days"), O.PREP: PREP}}
    ctx, server = setup(tmp_path, [bad] * 4)
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "stalled" and out.failures and len(server.requests) == 4
    assert not (ctx.me / "applications" / KEY / O.ANALYSIS).exists() and ctx.store.queue_item(f"offer:{KEY}") is None


def test_exhausted_stops_cleanly_with_nothing_saved(tmp_path):
    ctx, server = setup(tmp_path, [{"files": {O.ANALYSIS: "x"}, "cost": 50.0}, GOOD])
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "exhausted" and len(server.requests) == 1
    assert not (ctx.me / "applications" / KEY / O.ANALYSIS).exists() and ctx.store.queue() == []


def test_a_profile_without_preferences_asks_the_person(tmp_path):
    ctx, server = setup(tmp_path, [])
    (ctx.me / "profile.md").write_text("# Alex\n\n## Summary\nx\n")
    ctx.snapshot = snapshot.take(ctx.me, ctx.store, ctx.config, ctx.now)
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "blocked" and server.requests == []
    assert ctx.store.queue_item(f"offer:{KEY}")["status"] == "needs_decision"


def test_a_row_that_moved_on_is_a_no_op_and_nothing_is_contacted(tmp_path):
    ctx, server = setup(tmp_path, status="interview")
    assert stage.STAGE.run(Action("offer", KEY, "stale"), ctx).terminal == "no_op" and server.requests == []
    assert ctx.store.queue() == []


def test_the_stage_never_writes_a_status_or_prompts():
    source = open(stage.__file__).read()
    assert "set_status" not in source and "input(" not in source and "smtp" not in source.lower()


def test_a_whole_pass_with_the_engine_keeps_every_invariant(tmp_path):
    from datetime import datetime, timezone
    from jobloop.engine import runner
    ctx, server = setup(tmp_path, [GOOD])
    ctx.store.close()
    now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    result = runner.run_pass(ctx.me, [stage.STAGE], config=cfgmod.Config().validate(), now=lambda: now, out=lambda s: None,
                             transport=server.transport())
    assert result.terminal == "success" and result.violations == [] and result.actions == 1

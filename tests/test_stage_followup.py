"""The followup stage: drafts only, for the person's own `applied` rows; never sent, never a tracker write, never for `drafted`."""
from __future__ import annotations

import builtins
import json
from datetime import timedelta

import pytest

import sim
from jobloop import archive, tracker
from jobloop.engine import config as cfgmod, invariants, snapshot
from jobloop.engine.spec import Action
from jobloop.stages import followup as stage
from test_followup import GOOD, THANKS, TODAY, app, put_rows, stage_for, submit

KEY = "acme__data_engineer"
SECRET = "Quartz Compiler Maintainer"


def setup(tmp_path, *rows, replies=(), docs=True, **config):
    me = tmp_path / "me"
    put_rows(me, *rows)
    (me / "profile.md").write_text(f"# Anna Hansen\n\n## Summary\n{SECRET}\n")
    if docs:
        submit(me)
    ctx, server = sim.context(tmp_path, list(replies), me=me, config=cfgmod.Config(**config).validate())
    return ctx, server


def proposed(ctx):
    return stage.STAGE.propose(snapshot.take(ctx.me, ctx.store, ctx.config, ctx.now))


def no_input(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: (_ for _ in ()).throw(AssertionError("no prompts in a pass")))


# ── propose ──
@pytest.mark.parametrize("status,expected", [("applied", 1), ("drafted", 0), ("withdrawn", 0), ("rejected", 0), ("no_response", 0),
                                              ("hired", 0), ("offer_declined", 0), ("offer", 0), ("interview", 0)])
def test_a_follow_up_is_proposed_only_for_the_persons_applied_rows(tmp_path, status, expected):
    ctx, _ = setup(tmp_path, app(status=status, days=40))
    assert len(proposed(ctx)) == expected


def test_not_due_yet_and_the_cap_of_two(tmp_path):
    ctx, _ = setup(tmp_path, app(days=9))
    assert proposed(ctx) == []
    ctx, _ = setup(tmp_path / "x", app(days=60, notes="followup 1 sent; followup 2 sent"))
    folder = archive.folder_for(ctx.me, KEY, create=True)
    (folder / f"followup_{(TODAY - timedelta(days=30)).isoformat()}.md").write_text("x")
    (folder / f"followup_{(TODAY - timedelta(days=20)).isoformat()}.md").write_text("x")
    assert proposed(ctx) == []


def test_a_thank_you_is_proposed_after_a_logged_stage_for_applied_and_interview_rows_only(tmp_path):
    ctx, _ = setup(tmp_path, app("Acme", "interview", days=40))
    stage_for(ctx.me, KEY, "Acme", TODAY - timedelta(days=1))
    actions = proposed(ctx)
    assert [a.payload["kind"] for a in actions] == ["thankyou"] and actions[0].key.startswith(f"{KEY}:thankyou:")
    ctx, _ = setup(tmp_path / "o", app("Acme", "offer", days=40))
    stage_for(ctx.me, KEY, "Acme", TODAY - timedelta(days=1))
    assert proposed(ctx) == []


def test_followups_can_be_switched_off(tmp_path):
    ctx, _ = setup(tmp_path, app(days=40), followups=False)
    assert proposed(ctx) == []


# ── run ──
def test_a_follow_up_draft_is_written_and_queued_and_nothing_else_changes(tmp_path, monkeypatch):
    no_input(monkeypatch)
    ctx, server = setup(tmp_path, app(days=12), replies=[{"files": {"draft.md": GOOD}, "cost": 0.1}])
    before_rows = tracker.read_rows(ctx.me / "tracker.csv")
    before = snapshot.fingerprint(ctx.me)
    out = stage.STAGE.run(proposed(ctx)[0], ctx)
    assert out.terminal == "success" and out.produced["harness"] == "claude-deepseek/deepseek-chat"
    path = ctx.me / "applications" / KEY / f"followup_{TODAY.isoformat()}.md"
    assert path.read_text().strip() == GOOD.strip()
    entry = ctx.store.queue_item(f"followup:{KEY}:{TODAY.isoformat()}")
    data = json.loads(entry["data"])
    assert entry["status"] == "ready" and entry["path"] == str(path)
    assert set(data) == {"title", "why", "deadline"} and data["deadline"] == "" and "a draft only: you send it" in data["why"]
    assert tracker.read_rows(ctx.me / "tracker.csv") == before_rows and invariants.violations(before, snapshot.fingerprint(ctx.me)) == []
    assert proposed(ctx) == []                                     # the draft is made once


def test_the_profile_is_never_sent_to_the_harness(tmp_path):
    ctx, server = setup(tmp_path, app(days=12), replies=[{"files": {"draft.md": GOOD}}])
    stage.STAGE.run(proposed(ctx)[0], ctx)
    assert server.requests and SECRET not in json.dumps(server.requests)


def test_a_thank_you_draft_is_named_for_its_stage(tmp_path):
    ctx, _ = setup(tmp_path, app("Acme", "interview", days=40), replies=[{"files": {"draft.md": THANKS}}])
    stage_for(ctx.me, KEY, "Acme", TODAY - timedelta(days=1))
    action = proposed(ctx)[0]
    out = stage.STAGE.run(action, ctx)
    assert out.terminal == "success"
    name = f"thankyou_{(TODAY - timedelta(days=1)).isoformat()}_technical.md"
    assert (ctx.me / "applications" / KEY / name).is_file()
    assert ctx.store.queue_item(f"followup:{KEY}:{(TODAY - timedelta(days=1)).isoformat()}-thankyou")["status"] == "ready"


def test_an_ungrounded_draft_goes_back_verbatim_and_never_passes_if_it_stays_wrong(tmp_path):
    bad = GOOD.replace("the demand forecasting pipeline", "a pipeline that cut costs by 83 percent")
    ctx, server = setup(tmp_path, app(days=12), replies=[{"files": {"draft.md": bad}}] * 4)
    out = stage.STAGE.run(proposed(ctx)[0], ctx)
    assert out.terminal == "stalled" and out.failures and len(server.requests) == 4
    assert "83" in json.dumps(server.requests[1]) and "none of the sources" in json.dumps(server.requests[1])
    assert not list((ctx.me / "applications" / KEY).glob("followup_*")) and ctx.store.queue() == []


def test_no_submitted_documents_is_blocked_with_a_needs_decision_entry(tmp_path):
    ctx, server = setup(tmp_path, app(days=12), docs=False)
    out = stage.STAGE.run(Action("followup", KEY, "test"), ctx)
    assert out.terminal == "blocked" and server.requests == []
    assert [r["status"] for r in ctx.store.queue()] == ["needs_decision"]


def test_a_row_that_stopped_being_due_is_a_no_op(tmp_path):
    ctx, server = setup(tmp_path, app(status="drafted", days=40))
    out = stage.STAGE.run(Action("followup", KEY, "stale proposal"), ctx)
    assert out.terminal == "no_op" and server.requests == []


def test_exhausted_stops_cleanly(tmp_path):
    ctx, server = setup(tmp_path, app(days=12), replies=[{"files": {"draft.md": "nope"}, "cost": 50.0}, {"files": {"draft.md": GOOD}}])
    out = stage.STAGE.run(proposed(ctx)[0], ctx)
    assert out.terminal == "exhausted" and len(server.requests) == 1 and ctx.store.queue() == []


def test_the_stage_has_no_send_or_status_write_path():
    source = open(stage.__file__).read()
    assert "set_status" not in source and "input(" not in source and "smtp" not in source.lower()

"""The interview stage: which statuses it prepares for, stage details from outcome.md, research without the profile, and
what it must never do (prompt, write the tracker, build a mock)."""
from __future__ import annotations

import builtins
import copy
import json
from datetime import date

import httpx
import pytest

import sim
from jobloop import archive, interview as I
from jobloop.engine import config as cfgmod, invariants, snapshot
from jobloop.stages import interview as stage
from jobloop.turns import Api
from test_interview import COMPANY, FIX, GOOD, PROFILE, ROLE

KEY = archive.key(COMPANY, ROLE)
PAGE = "Brightwell Analytics runs a hybrid policy with two office days a week for all engineering teams."
URL = "https://brightwell.example/careers"
CLAIMS = json.dumps([{"claim": "Hybrid, two office days", "url": URL, "quote": PAGE}])
RESEARCHER = "claude-research/deepseek-chat"
TODAY = date(2026, 10, 19)


def cfg(**kw):
    return cfgmod.Config(**kw).validate()


def pack(hook="w1"):
    data = copy.deepcopy(GOOD)
    for t in data["tough"]:
        if t["id"] == "why_company":
            t["hook"] = hook
    return {"files": {"pack.json": json.dumps(data)}, "cost": 0.1}


def web(answer=CLAIMS):
    return {"answer": answer, "cost": 0.05}


class Site:
    def __init__(self):
        self.seen = []

    def __call__(self, request):
        self.seen.append((request.url.host, request.url.path))
        if request.url.path == "/careers":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=f"<html><body><p>{PAGE}</p></body></html>")
        return httpx.Response(404)


def setup(tmp_path, monkeypatch, replies, status="interview", stages=(), profile=True, rows=None, **config):
    me = sim.make_me(tmp_path, tracker_rows=rows or [(COMPANY, ROLE, status)], profile=False)
    if profile:
        (me / "profile.md").write_text(PROFILE)
    archive.open_application(me, COMPANY, ROLE)
    archive.store_posting(me, KEY, (FIX / "posting.md").read_bytes())
    archive.store_document(me, KEY, "cv", FIX / "cv.txt")
    archive.store_document(me, KEY, "cover_letter", FIX / "cover_letter.txt")
    for s in stages:
        archive.record_stage(me, KEY, **s)
    ctx, server = sim.context(tmp_path, replies, me=me, config=cfg(**config))
    site = Site()
    monkeypatch.setattr(stage, "TRANSPORT", httpx.MockTransport(site))
    monkeypatch.setattr(stage, "RESOLVE", lambda host: ["93.184.216.34"])
    return ctx, server, site


def no_input(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a pass is unattended: nothing may call input()")
    monkeypatch.setattr(builtins, "input", boom)


def act(ctx):
    return stage.STAGE.propose(ctx.snapshot)[0]


# ── propose ──
@pytest.mark.parametrize("status,expected", [("interview", 1), ("applied", 0), ("drafted", 0), ("offer", 0), ("withdrawn", 0),
                                              ("rejected", 0), ("hired", 0), ("no_response", 0)])
def test_only_the_persons_interview_status_is_prepared(tmp_path, monkeypatch, status, expected):
    ctx, *_ = setup(tmp_path, monkeypatch, [], status=status)
    assert len(stage.STAGE.propose(ctx.snapshot)) == expected


def test_a_pack_for_the_current_stage_ends_the_proposals_and_a_new_stage_starts_them(tmp_path, monkeypatch):
    ctx, *_ = setup(tmp_path, monkeypatch, [], stages=[dict(on=date(2026, 10, 20), stage="phone", outcome="pending")])
    assert [a.payload["stage"] for a in stage.STAGE.propose(ctx.snapshot)] == ["phone"]
    I.save_pack(ctx.me, KEY, "phone", "# pack\n")
    assert stage.STAGE.propose(snapshot.take(ctx.me, ctx.store, ctx.config, ctx.now)) == []
    archive.record_stage(ctx.me, KEY, on=date(2026, 10, 27), stage="technical", outcome="pending")
    assert [a.payload["stage"] for a in stage.STAGE.propose(snapshot.take(ctx.me, ctx.store, ctx.config, ctx.now))] == ["technical"]


def test_a_settled_stage_is_not_prepared_again(tmp_path, monkeypatch):
    ctx, *_ = setup(tmp_path, monkeypatch, [], stages=[dict(on=date(2026, 10, 1), stage="phone", outcome="passed")])
    assert [a.payload["stage"] for a in stage.STAGE.propose(ctx.snapshot)] == [stage.GENERIC]


# ── run ──
def test_generic_pack_when_no_stage_is_logged(tmp_path, monkeypatch):
    no_input(monkeypatch)
    ctx, server, _ = setup(tmp_path, monkeypatch, [web(), pack()])
    before = snapshot.fingerprint(ctx.me)
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "success" and out.produced["harness"] == "claude-deepseek/deepseek-chat"
    path = ctx.me / "applications" / KEY / "interview_prep_next-interview.md"
    text = path.read_text()
    assert f"jobloop mock {KEY}" in text and "generic pack" in text and "hybrid policy" in text
    entry = ctx.store.queue_item(f"interview:{KEY}:next-interview")
    assert entry["status"] == "ready" and entry["path"] == str(path)
    assert json.loads(entry["data"]) == {"title": f"Interview prep: {COMPANY}, {ROLE} (next interview)",
                                         "why": entry_why(entry), "deadline": ""}
    assert invariants.violations(before, snapshot.fingerprint(ctx.me)) == []
    assert stage.STAGE.propose(snapshot.take(ctx.me, ctx.store, ctx.config, ctx.now)) == []


def entry_why(entry):
    return json.loads(entry["data"])["why"]


def test_pack_uses_the_logged_stage_details_and_researches_only_logged_interviewers(tmp_path, monkeypatch):
    stages = [dict(on=date(2026, 10, 22), stage="technical", format="video", people=("Jo Vale",), outcome="pending")]
    ctx, server, _ = setup(tmp_path, monkeypatch, [web(), web("[]"), pack()], stages=stages)
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "success"
    text = (ctx.me / "applications" / KEY / "interview_prep_technical.md").read_text()
    assert "- Date: 2026-10-22" in text and "- Interviewers: Jo Vale" in text and "- Format: video" in text
    assert "generic pack" not in text and "Jo Vale: nothing verified" in text
    researchers = [r for r in server.requests if r["model"] == RESEARCHER]
    assert len(researchers) == 2 and "Jo Vale" in json.dumps(researchers[1])
    assert json.loads(ctx.store.queue_item(f"interview:{KEY}:technical")["data"])["deadline"] == "2026-10-22"


def test_no_logged_names_means_no_interviewer_research(tmp_path, monkeypatch):
    ctx, server, _ = setup(tmp_path, monkeypatch, [web(), pack()])
    stage.STAGE.run(act(ctx), ctx)
    assert len([r for r in server.requests if r["model"] == RESEARCHER]) == 1


def test_the_profile_never_reaches_the_research_harness(tmp_path, monkeypatch):
    stages = [dict(on=date(2026, 10, 22), stage="technical", people=("Jo Vale",), outcome="pending")]
    ctx, server, _ = setup(tmp_path, monkeypatch, [web(), web("[]"), pack()], stages=stages)
    stage.STAGE.run(act(ctx), ctx)
    research = [r for r in server.requests if r["model"] == RESEARCHER]
    planner = [r for r in server.requests if r["model"] != RESEARCHER]
    assert research and planner
    for body in research:
        blob = json.dumps(body)
        assert "Marlow" not in blob and "profile.md" not in blob and not body.get("files")
    assert "Marlow Retail Group" in json.dumps(planner[0])


def test_the_company_domain_from_loop_json_sets_the_researchers_hosts(tmp_path, monkeypatch):
    ctx, server, _ = setup(tmp_path, monkeypatch, [], company_domains={"brightwell analytics": "brightwell.example"})
    hosts = []

    def handle(request):
        if request.url.path == "/sandboxes/research":
            if request.method == "PUT" or request.method == "POST":
                hosts.append(json.loads(request.content)["egress"])
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"name": "research", "dockerfile": "FROM x\n", "egress": []})
        return server.handle(request)

    server.replies += [web(), pack()]
    ctx.gateway.api = Api("http://x", httpx.MockTransport(handle))
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "success"
    assert any("brightwell.example" in h for h in hosts) and hosts[-1] == []


def test_a_status_that_moved_on_is_a_no_op(tmp_path, monkeypatch):
    ctx, server, _ = setup(tmp_path, monkeypatch, [], status="applied")
    out = stage.STAGE.run(Action("interview", KEY, "test"), ctx)
    assert out.terminal == "no_op" and server.requests == []


def test_no_profile_blocks_with_a_needs_decision_entry(tmp_path, monkeypatch):
    ctx, server, _ = setup(tmp_path, monkeypatch, [], profile=False)
    out = stage.STAGE.run(Action("interview", KEY, "test"), ctx)
    assert out.terminal == "blocked" and server.requests == []
    assert ctx.store.queue_item(f"interview:{KEY}:profile")["status"] == "needs_decision"


def test_a_pack_that_never_passes_stalls_and_saves_nothing(tmp_path, monkeypatch):
    bad = {"files": {"pack.json": "{}"}, "cost": 0.1}
    ctx, _, _ = setup(tmp_path, monkeypatch, [web(), bad, bad, bad, bad])
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "stalled" and out.failures
    assert not list((ctx.me / "applications" / KEY).glob("interview_prep*")) and ctx.store.queue() == []


def test_exhausted_stops_cleanly_without_a_pack(tmp_path, monkeypatch):
    ctx, server, _ = setup(tmp_path, monkeypatch, [{"answer": "[]", "cost": 50.0}, pack()])
    out = stage.STAGE.run(act(ctx), ctx)
    assert out.terminal == "exhausted" and len(server.requests) == 1
    assert not list((ctx.me / "applications" / KEY).glob("interview_prep*")) and ctx.store.queue() == []


def test_vetoed_withdrawn_row_gets_nothing_and_the_tracker_is_never_written(tmp_path, monkeypatch):
    ctx, *_ = setup(tmp_path, monkeypatch, [], status="withdrawn")
    assert stage.STAGE.propose(ctx.snapshot) == []
    assert "set_status" not in open(stage.__file__).read() and "input(" not in open(stage.__file__).read()


from jobloop.engine.spec import Action  # noqa: E402

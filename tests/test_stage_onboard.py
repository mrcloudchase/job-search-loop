"""Stage onboard: no profile -> blocked + a queue entry, and nothing else runs; with a profile, verified enrichment as proposals only."""
from __future__ import annotations

import builtins
import json
from datetime import timedelta

import httpx
import pytest

import sim
from test_enrich import PORTFOLIO, SITE_HOST, TALK, TOOL, site
from test_research import Rig, answer
from jobloop.engine import config as cfgmod, policy, proposals, runner, snapshot
from jobloop.engine.spec import Action
from jobloop.engine.store import Store
from jobloop.stages import onboard
from jobloop.turns import Api

NOW = sim.NOW
CONFIG = dict(enrich_github="https://github.com/alice-example", enrich_site="https://alice.example/")


def cfg(**kw):
    return cfgmod.Config(**{**CONFIG, **kw}).validate()


@pytest.fixture(autouse=True)
def no_prompts(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("an unattended pass prompted")
    monkeypatch.setattr(builtins, "input", refuse)
    monkeypatch.setattr(onboard, "FETCH", lambda named: site().fetch(named.hosts))


def with_rig(ctx, rig):
    ctx.gateway.api = Api("http://x", httpx.MockTransport(rig.handle))
    return ctx


def enrich_ctx(tmp_path, claims, **kw):
    rig = Rig([answer(*claims)] if claims is not None else [])
    ctx, _ = sim.context(tmp_path, [], config=kw.pop("config", cfg()), **kw)
    return with_rig(ctx, rig), rig


def act(key=onboard.ENRICH):
    return Action("onboard", key, "r")


def propose(tmp_path, config=None, now=NOW, profile=True):
    me = sim.make_me(tmp_path, profile=profile)
    with Store(me) as store:
        return onboard.STAGE.propose(snapshot.take(me, store, config or cfg(), now))


# ── no profile ──
def test_without_a_profile_the_only_action_is_asking_for_one(tmp_path):
    actions = propose(tmp_path, profile=False)
    assert [(a.stage, a.key, a.reason) for a in actions] == [("onboard", "__setup__", "no profile: run `jobloop setup`")]


def test_without_a_profile_run_is_blocked_with_a_needs_decision_queue_entry(tmp_path):
    me = sim.make_me(tmp_path, profile=False)
    ctx, _ = sim.context(tmp_path, me=me)
    out = onboard.STAGE.run(act("__setup__"), ctx)
    assert out.terminal == "blocked" and out.failures == ["no profile: run `jobloop setup`"]
    row = ctx.store.queue_item("__setup__")
    data = json.loads(row["data"])
    assert row["status"] == "needs_decision" and row["path"] == "me/profile.md" and data["title"] == "Build your profile" and data["why"]


def test_a_pass_without_a_profile_runs_only_onboard_and_the_digest_tells_the_person(tmp_path):
    me = sim.make_me(tmp_path, postings=[sim.posting("p1", "Acme", "Engineer", status="new")], profile=False)
    ran = []

    class Other:
        spec = onboard.Onboard.spec.__class__("triage", "g")

        def propose(self, snap):
            return [Action("triage", "p1", "new")]

        def run(self, action, ctx):
            ran.append(action)
            return onboard.Outcome("success")

    result = runner.run_pass(me, [Other(), onboard.STAGE], config=cfg(), now=lambda: NOW, out=lambda s: None)
    assert ran == [] and result.actions == 1
    text = result.digest_path.read_text()
    assert "no profile: run `jobloop setup`" in text and "Build your profile" in text and "MISSING" not in "" and "needs_decision" in text
    assert not (me / "profile.md").exists()


def test_once_the_profile_exists_the_setup_request_is_closed_and_other_stages_may_run(tmp_path):
    me = sim.make_me(tmp_path, profile=False)
    with Store(me) as store:
        store.queue_put("__setup__", "needs_decision", "me/profile.md", {"title": "Build your profile"}, NOW)
    (me / "profile.md").write_text(sim.PROFILE.read_text())
    result = runner.run_pass(me, [onboard.STAGE], config=cfg(enrich_github="", enrich_site=""), now=lambda: NOW, out=lambda s: None)
    assert result.actions >= 1
    with Store(me) as store:
        assert store.queue_item("__setup__")["status"] == "done"


# ── propose with a profile ──
def test_with_a_profile_and_a_named_page_enrichment_is_due_and_then_rests_for_the_cadence(tmp_path):
    me = sim.make_me(tmp_path)
    with Store(me) as store:
        snap = lambda at: snapshot.take(me, store, cfg(), at)
        assert [a.key for a in onboard.STAGE.propose(snap(NOW))] == ["enrich"]
        store.record_attempt(1, "onboard", "enrich", "r", "h", NOW.isoformat(), NOW.isoformat(), "success", 0, 0, [], [])
        assert onboard.STAGE.propose(snap(NOW + timedelta(hours=100))) == []
        assert [a.key for a in onboard.STAGE.propose(snap(NOW + timedelta(hours=169)))] == ["enrich"]


def test_nothing_is_proposed_when_no_page_is_named_and_the_profile_has_no_links(tmp_path):
    me = sim.make_me(tmp_path)
    text = sim.PROFILE.read_text().replace("- Links: github.com/afernsby-example\n", "")
    (me / "profile.md").write_text(text)
    with Store(me) as store:
        assert onboard.STAGE.propose(snapshot.take(me, store, cfgmod.Config().validate(), NOW)) == []


def test_pages_are_taken_from_the_profiles_links_when_loop_json_names_none():
    md = sim.PROFILE.read_text()
    named = onboard.named_sources(cfgmod.Config(), md)
    assert named.urls == ("https://github.com/afernsby-example",)
    named = onboard.named_sources(cfgmod.Config(), md.replace("github.com/afernsby-example", "github.com/afernsby-example, alex.example/about"))
    assert named.urls == ("https://github.com/afernsby-example", "https://alex.example/about")
    half = onboard.named_sources(cfgmod.Config(enrich_site="http://insecure.example", enrich_github="https://github.com/alice"), md)
    assert half.urls == ("https://github.com/alice",)                    # a bad half does not stop the good half
    assert onboard.named_sources(cfgmod.Config(enrich_site="http://insecure.example"), md) is None


# ── run: proposals only, verified only ──
def test_verified_claims_become_proposals_and_the_profile_is_untouched(tmp_path):
    ctx, rig = enrich_ctx(tmp_path, [TOOL, TALK])
    before = (ctx.me / "profile.md").read_bytes()
    out = onboard.STAGE.run(act(), ctx)
    assert out.terminal == "success" and (ctx.me / "profile.md").read_bytes() == before
    pending = proposals.list_pending(ctx.store)
    assert [p["kind"] for p in pending] == ["profile_enrichment"] * 2
    first = pending[0]["payload"]
    assert set(first) == {"summary", "section", "text", "source_url", "quote"}
    assert first["text"] == TOOL["claim"] and first["source_url"] == TOOL["url"] and first["quote"] == TOOL["quote"] and first["section"] == "Confirmed additions"
    assert out.produced["harness"] == "claude-research/deepseek-chat"


def test_the_researcher_is_told_the_pages_and_never_sees_the_profile(tmp_path):
    ctx, rig = enrich_ctx(tmp_path, [TOOL])
    onboard.STAGE.run(act(), ctx)
    sent = json.dumps(rig.server.requests)
    assert "alice.example" in sent and "afernsby" not in sent and "Alex" not in sent and "Tidewater" not in sent
    assert rig.history[0] == ["github.com", "raw.githubusercontent.com", "alice.example"]


def test_a_claim_whose_quote_is_not_on_the_page_is_never_proposed(tmp_path):
    fake = {**TOOL, "quote": "Rustcsv is the fastest CSV comparison tool ever built by anyone"}
    off_site = {**TOOL, "url": "https://evil.example.net/", "quote": TOOL["quote"]}
    other_account = {**TOOL, "url": "https://github.com/bob/x"}
    ctx, _ = enrich_ctx(tmp_path, [fake, off_site, other_account])
    out = onboard.STAGE.run(act(), ctx)
    assert out.terminal == "success" and proposals.list_pending(ctx.store) == [] and "3 dropped" in out.notes[0]


def test_known_and_already_decided_facts_are_not_proposed_again(tmp_path):
    ctx, rig = enrich_ctx(tmp_path, [TOOL, {**TALK, "claim": "Open-source dbt package for freight-rate data tests"}])
    onboard.STAGE.run(act(), ctx)
    [only] = proposals.list_pending(ctx.store)
    proposals.reject(ctx.store, only["id"], NOW)
    ctx2 = with_rig(sim.context(tmp_path, [], me=ctx.me, config=cfg())[0], Rig([answer(TOOL)]))
    onboard.STAGE.run(act(), ctx2)
    assert proposals.list_pending(ctx2.store) == [] and len(ctx2.store.proposals()) == 1


def test_no_completed_turn_is_stalled_and_a_spent_budget_is_exhausted(tmp_path):
    rig = Rig([{"answer": "not json"}, {"answer": "still not"}, {"answer": "no"}])
    ctx, _ = sim.context(tmp_path, [], config=cfg())
    ctx = with_rig(ctx, rig)
    assert onboard.STAGE.run(act(), ctx).terminal == "stalled"
    ctx, _ = enrich_ctx(tmp_path / "b", [TOOL])
    ctx.gateway.ledger.add(1e9, 0)
    assert onboard.STAGE.run(act(), ctx).terminal == "exhausted" and proposals.list_pending(ctx.store) == []


# ── the whole loop: accept is the only writer ──
def test_a_full_pass_leaves_the_profile_bytes_unchanged_and_accept_is_the_only_writer(tmp_path):
    me = sim.make_me(tmp_path)
    before = snapshot.fingerprint(me)
    rig = Rig([answer(TOOL, TALK)])
    result = runner.run_pass(me, [onboard.STAGE], config=cfg(), now=lambda: NOW, transport=httpx.MockTransport(rig.handle), out=lambda s: None)
    assert result.terminal == "success" and result.violations == [] and snapshot.fingerprint(me) == before
    with Store(me) as store:
        assert len(proposals.list_pending(store)) == 2
        assert "Proposals to decide" in result.digest_path.read_text()
        pid = proposals.list_pending(store)[0]["id"]
        assert proposals.accept(me, store, pid, NOW, ignored=lambda p: True).ok
    assert snapshot.fingerprint(me) != before
    assert "(source: public page https://alice.example/" in (me / "profile.md").read_text()
    again = runner.run_pass(me, [onboard.STAGE], config=cfg(), now=lambda: NOW + timedelta(hours=1), out=lambda s: None)
    assert again.terminal == "no_op"                                 # not due for a week; nothing prompts, nothing runs

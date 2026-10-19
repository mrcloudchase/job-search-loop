"""The discover stage: planned queries, playbook extras and drops, terms, health, broken boards, yield events, and the negative space."""
from __future__ import annotations

import builtins
import json
import types
from pathlib import Path

import httpx
import pytest

import sim
from jobloop import search, state
from jobloop.engine import config as cfgmod
from jobloop.engine.snapshot import take
from jobloop.stages import discover

FIX = Path(__file__).parent / "fixtures" / "boards" / "freehire"


class World:
    """The network as a board sees it: freehire answers from saved responses; every request is kept."""

    def __init__(self, search_file="search_agent.json", status=200):
        self.requests: list[httpx.Request] = []
        self.search_file, self.status = search_file, status

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/v1/agent/jobs/search":
            return httpx.Response(self.status, content=(FIX / self.search_file).read_bytes(), headers={"content-type": "application/json"})
        if path.startswith("/api/v1/jobs/"):
            return httpx.Response(200, content=(FIX / "detail_open.json").read_bytes(), headers={"content-type": "application/json"})
        return httpx.Response(404)

    def searched(self) -> list[str]:
        return [r.url.params["q"] for r in self.requests if r.url.path == "/api/v1/agent/jobs/search"]


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(discover, "CLIENT", lambda module, **kw: search.make_client(module, transport=httpx.MockTransport(w.handle), sleep=lambda s: None, **kw))
    monkeypatch.setattr(discover, "SLEEP", lambda s: None)

    def no_prompt(*a, **k):
        raise AssertionError("discover prompted a person")

    monkeypatch.setattr(builtins, "input", no_prompt)
    return w


def setup(tmp_path, playbook=None, **config):
    ctx, server = sim.context(tmp_path, config=cfgmod.Config(**config).validate())
    ctx.playbook = playbook
    return ctx, server


def run(ctx):
    return discover.STAGE.run(discover.STAGE.propose(ctx.snapshot)[0], ctx)


def events(ctx, kind):
    return [(r["item"], json.loads(r["data"])) for r in reversed(ctx.store.events(kind, limit=500))]


def test_a_due_discover_is_proposed_once_there_is_a_profile_and_not_again_after_success(tmp_path, world):
    ctx, _ = setup(tmp_path)
    (action,) = discover.STAGE.propose(ctx.snapshot)
    assert (action.stage, action.key) == ("discover", "*") and discover.STAGE.spec.rank == 10 and discover.STAGE.spec.roles == ()
    ctx.store.record_attempt(ctx.run_id, "discover", "*", "", "", "2026-10-19T11:00:00+00:00", "2026-10-19T11:00:00+00:00", "success", 0, 0, [], [])
    assert discover.STAGE.propose(take(ctx.me, ctx.store, ctx.config, sim.NOW)) == []
    bare, _ = setup(tmp_path / "bare")
    bare.snapshot.profile = ""
    assert discover.STAGE.propose(bare.snapshot) == []


def test_queries_are_planned_from_the_profile_and_searched_with_the_configured_recency_and_limit(tmp_path, world):
    ctx, _ = setup(tmp_path, recency_days=10, search_limit=7)
    outcome = run(ctx)
    assert outcome.terminal == "success" and "searches over freehire" in outcome.notes[0]
    assert {"data engineer", "airflow engineer", "Dateningenieur"} <= set(world.searched())
    sent = [r.url.params for r in world.requests if r.url.params.get("q") == "data engineer" and r.url.params.get("limit") == "7"]
    assert sent and all(p["posted_within_days"] == "10" for p in sent)
    assert "freehire" in {e["board"] for e in state.load(ctx.me).values()}
    assert all(r.headers["user-agent"] and "Mozilla" not in r.headers["user-agent"] for r in world.requests)


def test_playbook_extras_are_added_and_dropped_queries_are_never_requested(tmp_path, world):
    pb = types.SimpleNamespace(extra_queries=["dbt consultant"], dropped_queries=["Dateningenieur", "airflow engineer"])
    ctx, _ = setup(tmp_path, pb)
    assert run(ctx).terminal == "success"
    asked = set(world.searched())
    assert "dbt consultant" in asked and "data engineer" in asked
    assert not {"Dateningenieur", "airflow engineer"} & asked
    assert "dbt consultant" in {d["query"] for _, d in events(ctx, "yield")}


def test_an_invalid_extra_query_is_ignored_with_a_note(tmp_path, world):
    ctx, _ = setup(tmp_path, types.SimpleNamespace(extra_queries=["-bad"], dropped_queries=[]))
    outcome = run(ctx)
    assert outcome.terminal == "success" and any("-bad" in n for n in outcome.notes)


def test_health_is_recorded_for_each_enabled_board(tmp_path, world):
    ctx, _ = setup(tmp_path)
    run(ctx)
    assert events(ctx, "health") == [("freehire", {"status": "ok", "message": ""})]


def test_yield_events_record_found_and_new_per_query_and_board(tmp_path, world):
    ctx, _ = setup(tmp_path)
    run(ctx)
    found = events(ctx, "yield")
    assert found and all(item == "freehire" and set(d) == {"query", "found", "new"} for item, d in found)
    first = next(d for _, d in found if d["query"] == "data engineer")
    assert first["found"] == 2 and first["new"] == 2
    assert sum(d["new"] for _, d in found) == len(state.load(ctx.me))      # the same two postings are not new the second time


def test_a_broken_board_is_skipped_and_no_search_is_made(tmp_path, monkeypatch):
    w = World("search_empty.json")
    monkeypatch.setattr(discover, "CLIENT", lambda module, **kw: search.make_client(module, transport=httpx.MockTransport(w.handle), sleep=lambda s: None, **kw))
    ctx, _ = setup(tmp_path)
    outcome = run(ctx)
    assert outcome.terminal == "blocked" and "freehire: broken" in outcome.failures[0]
    assert events(ctx, "health")[0][1]["status"] == "broken" and events(ctx, "yield") == []
    assert set(w.searched()) <= {"engineer", "manager"} and state.load(ctx.me) == {}


def test_a_rate_limited_board_is_noted_and_not_retried(tmp_path, monkeypatch):
    w = World(status=429)
    monkeypatch.setattr(discover, "CLIENT", lambda module, **kw: search.make_client(module, transport=httpx.MockTransport(w.handle), sleep=lambda s: None, **kw))
    ctx, _ = setup(tmp_path)
    outcome = run(ctx)
    assert outcome.terminal == "blocked" and "rate_limited" in outcome.failures[0] and "not retried" in outcome.failures[0]
    assert len(w.requests) == 1


def test_a_board_with_terms_is_skipped_without_the_persons_acknowledgement_and_the_loop_never_records_one(tmp_path, world):
    ctx, _ = setup(tmp_path)
    (ctx.me / "boards.json").write_text(json.dumps({"boards": {"jobbank": {"enabled": True}}}))
    before = (ctx.me / "boards.json").read_text()
    outcome = run(ctx)
    assert outcome.terminal == "success" and any("jobbank: skipped" in n and "terms" in n for n in outcome.notes)
    assert "jobbank" not in {i for i, _ in events(ctx, "health")}
    assert (ctx.me / "boards.json").read_text() == before


def test_a_terms_board_runs_with_the_persons_acknowledgement_in_loop_json_and_nothing_is_written_to_boards_json(tmp_path, world):
    ctx, _ = setup(tmp_path, acknowledge_terms=True)
    (ctx.me / "boards.json").write_text(json.dumps({"boards": {"jobbank": {"enabled": True}}}))
    run(ctx)
    assert "jobbank" in {i for i, _ in events(ctx, "health")}
    assert json.loads((ctx.me / "boards.json").read_text()) == {"boards": {"jobbank": {"enabled": True}}}


def test_no_usable_board_is_blocked_and_says_why(tmp_path, world):
    ctx, _ = setup(tmp_path)
    (ctx.me / "boards.json").write_text(json.dumps({"boards": {"freehire": {"enabled": False}}}))
    outcome = run(ctx)
    assert outcome.terminal == "blocked" and "no board is switched on" in outcome.failures[0] and world.requests == []


def test_a_profile_with_no_queries_is_a_no_op(tmp_path, world):
    me = sim.make_me(tmp_path)
    (me / "profile.md").write_text("# Someone\n\n## Summary\nNothing.\n")
    ctx, _ = sim.context(tmp_path, me=me)
    outcome = discover.STAGE.run(discover.STAGE.propose(ctx.snapshot)[0], ctx)
    assert outcome.terminal == "no_op" and world.requests == []


def test_discover_never_calls_a_model_and_never_touches_the_profile_or_tracker(tmp_path, world):
    ctx, server = setup(tmp_path)
    before = (ctx.me / "profile.md").read_bytes()
    run(ctx)
    assert server.requests == [] and ctx.gateway.ledger.spent_usd == 0
    assert (ctx.me / "profile.md").read_bytes() == before and not (ctx.me / "tracker.csv").exists()


def test_the_stage_is_declared_as_the_contract_says():
    spec = discover.STAGE.spec
    assert (spec.name, spec.trigger.kind, spec.rank, spec.roles) == ("discover", "every", 10, ())
    assert cfgmod.Config().discover_every_hours == 12.0

"""stages/triage.py: a panel of harnesses scores fetched postings; code averages, unions vetoes and uses the playbook's weights.
The network is a fake fetch ladder, the harnesses are the fake harness-interface server."""
from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

import sim
from jobloop import fetch as FT, fit as F, rank, rankstate as R, state as store
from jobloop.engine import snapshot
from jobloop.engine.config import Config
from jobloop.engine.spec import Action
from jobloop.stages import triage as T

FIX = Path(__file__).parent / "fixtures" / "fit"
PROFILE = (FIX / "profile.md").read_text()
POSTING = (FIX / "posting.md").read_text()
GOOD = json.loads((FIX / "good.json").read_text())
HOST = FT.hosts.classify("https://jobs.example/x", ())
CITIZEN = "US citizenship is required for this role"
VETO = {"eligibility": [{"kind": "citizenship", "stance": "requires", "value": "US citizen", "scope": "role", "text": CITIZEN}]}


def reply(scores=None, **extra):
    d = {**copy.deepcopy(GOOD), **extra}
    if scores:
        d["scores"] = scores
    return {"files": {"fit.json": json.dumps(d)}, "cost": 0.01}


def bad():
    return {"files": {"fit.json": json.dumps({"scores": {"technical": 50}})}, "cost": 0.01}


def sc(t, e, b, c):
    return {"technical": t, "experience": e, "behavioural": b, "career": c}


class Fetcher:
    def __init__(self, answers=None, text=POSTING + "\n" + CITIZEN + "\n"):
        self.answers, self.text, self.asked = answers or {}, text, []

    def __call__(self, url, **kw):
        self.asked.append((url, kw))
        return self.answers.get(url) or FT.Fetched(self.text, url, "plain", 200, HOST, "note")


@pytest.fixture
def fetcher(monkeypatch):
    f = Fetcher()
    monkeypatch.setattr(T.fetching, "fetch_posting", f)
    return f


def posting(key, **kw):
    return sim.posting(key, f"Co {key}", f"Role {key}", **kw)


def setup(tmp_path, replies, postings=None, config=None, tracker_rows=None):
    me = sim.make_me(tmp_path, postings or [posting("a")], tracker_rows, profile=False)
    (me / "profile.md").write_text(PROFILE)
    ctx, server = sim.context(tmp_path, replies, me=me, config=config or Config().validate())
    return ctx, server, me


def go(ctx, keys):
    return T.STAGE.run(Action("triage", T.batch_key(keys), "t", payload={"keys": keys}), ctx)


def stored(me, key="a"):
    return store.load(me)[key]


def fit_json(me, key="a"):
    return json.loads((me / "fit" / f"{rank.fit_key(key)}.json").read_text())


# ── propose ──
def test_proposes_batches_of_new_and_unreachable_newest_first(tmp_path):
    ps = [posting(f"p{i}", first_seen=f"2026-10-{10 + i:02d}", posted_date=None) for i in range(7)]
    ps += [posting("u", status="unreachable", fetch_attempts=1), posting("r", status="ranked", score=70),
           posting("x", status="expired"), posting("k"), posting("t")]
    me = sim.make_me(tmp_path, ps, [("Co t", "Role t", "drafted")])
    ctx, _ = sim.context(tmp_path, me=me)
    actions = T.STAGE.propose(ctx.snapshot)
    keys = [k for a in actions for k in a.payload["keys"]]
    assert [len(a.payload["keys"]) for a in actions] == [5, 4] and len(set(keys)) == 9
    assert {"u", "k"} <= set(keys) and not {"r", "x", "t"} & set(keys)         # ranked, expired and tracked are not triaged
    assert keys.index("p6") < keys.index("p0")                                 # newest first
    assert actions[0].priority > actions[1].priority and all(a.stage == "triage" for a in actions)
    assert T.STAGE.spec.shape == "panel" and T.STAGE.spec.rank == 20 and T.STAGE.spec.roles == ("planner", "judge")


def test_an_unreachable_posting_rests_between_tries_and_nothing_is_proposed_without_a_profile(tmp_path):
    me = sim.make_me(tmp_path, [posting("u", status="unreachable")])
    ctx, _ = sim.context(tmp_path, me=me)
    assert len(T.STAGE.propose(ctx.snapshot)) == 1
    ctx.store.put(T.TRIED, {"u": (sim.NOW - timedelta(hours=1)).isoformat()})
    assert T.STAGE.propose(snapshot.take(me, ctx.store, ctx.config, sim.NOW)) == []
    ctx.store.put(T.TRIED, {"u": (sim.NOW - timedelta(hours=7)).isoformat()})
    assert len(T.STAGE.propose(snapshot.take(me, ctx.store, ctx.config, sim.NOW))) == 1
    bare = sim.make_me(tmp_path / "bare", [posting("a")], profile=False)
    ctx2, _ = sim.context(tmp_path / "bare", me=bare)
    assert T.STAGE.propose(ctx2.snapshot) == []


# ── the panel ──
def test_sub_scores_are_averaged_by_code_and_both_harnesses_are_asked(tmp_path, fetcher):
    ctx, server, me = setup(tmp_path, [reply(sc(80, 70, 60, 76), eligibility=[]), reply(sc(60, 50, 40, 56), eligibility=[])])
    out = go(ctx, ["a"])
    assert out.terminal == "success"
    models = [r["model"] for r in server.requests]
    assert len(models) == 2 and models[0] != models[1] and models[0].split("/")[0] != models[1].split("/")[0]
    data = fit_json(me)
    assert data["scores"] == sc(70, 60, 50, 66) and data["total"] == 63 == F.score(sc(70, 60, 50, 66))
    assert [p["harness"] for p in data["panel"]] == models and [p["total"] for p in data["panel"]] == [73, 53]
    assert stored(me)["status"] == "ranked" and stored(me)["rank_score"] == 63
    assert out.produced["harness"] == models[0]


def test_a_veto_by_any_panel_member_is_the_union_and_holds_whatever_the_average(tmp_path, fetcher):
    ctx, _, me = setup(tmp_path, [reply(sc(100, 100, 100, 100)), reply(sc(100, 100, 100, 100), **VETO)])
    assert go(ctx, ["a"]).terminal == "success"
    data = fit_json(me)
    assert data["total"] == 100 and data["shortlisted"] is False
    assert any("citizenship" in v for v in data["vetoes"]) and [p["vetoes"] for p in data["panel"]][0] == []
    entry = stored(me)
    assert entry["rank_score"] == 100 and R.vetoes(entry)
    assert snapshot.take(me, ctx.store, ctx.config, sim.NOW).state_of("a") == "vetoed"


def test_a_veto_by_the_first_member_alone_also_vetoes_and_flags_are_unioned(tmp_path, fetcher):
    flag = {"location": {"onsite_days": None, "relocation_required": False, "travel": "frequent", "text": "hybrid, 2 days on site",
                         "deal_breaker_hits": []}}
    ctx, _, me = setup(tmp_path, [reply(**VETO), reply(**flag)])
    go(ctx, ["a"])
    data = fit_json(me)
    assert any("citizenship" in v for v in data["vetoes"]) and any("frequent travel" in f for f in data["flags"])


def test_requirements_and_synonyms_come_from_the_first_validated_evaluation(tmp_path, fetcher):
    second = copy.deepcopy(GOOD)
    second["requirements"] = [r for r in second["requirements"] if r["term"] != "Kafka"]
    second["strengths"] = ["Python and SQL pipelines at scale"]
    ctx, _, me = setup(tmp_path, [reply(), {"files": {"fit.json": json.dumps(second)}, "cost": 0.01}])
    go(ctx, ["a"])
    assert "Kafka" in [r["term"] for r in fit_json(me)["requirements"]]


def test_one_evaluation_alone_is_used_and_the_fit_says_so(tmp_path, fetcher):
    ctx, server, me = setup(tmp_path, [reply(sc(80, 70, 60, 76))] + [bad()] * 4)
    out = go(ctx, ["a"])
    data = fit_json(me)
    assert out.terminal == "success" and len(data["panel"]) == 1 and data["scores"] == sc(80, 70, 60, 76)
    assert "only one evaluation" in data["panel_note"] and server.requests[1]["model"] in data["panel_note"]
    assert any("only one evaluation" in n for n in out.notes)
    assert stored(me)["status"] == "ranked"


def test_the_first_failing_and_the_second_passing_uses_the_second(tmp_path, fetcher):
    ctx, server, me = setup(tmp_path, [bad()] * 4 + [reply(sc(60, 60, 60, 60))])
    assert go(ctx, ["a"]).terminal == "success"
    assert fit_json(me)["panel"][0]["harness"] == server.requests[-1]["model"]


def test_without_judge_triage_one_evaluation_is_made(tmp_path, fetcher):
    ctx, server, me = setup(tmp_path, [reply()], config=Config(judge_triage=False).validate())
    assert go(ctx, ["a"]).terminal == "success"
    data = fit_json(me)
    assert len(server.requests) == 1 and len(data["panel"]) == 1 and "panel_note" not in data


# ── weights from the playbook ──
def test_the_playbooks_weights_decide_the_total_and_band_and_are_recorded(tmp_path, fetcher):
    scores = sc(100, 40, 40, 40)
    ctx, _, me = setup(tmp_path, [reply(scores, eligibility=[]), reply(scores, eligibility=[])])
    ctx.playbook.weights = {"technical": 45, "experience": 15, "behavioural": 10, "career": 30}
    go(ctx, ["a"])
    data = fit_json(me)
    assert data["weights"] == ctx.playbook.weights and data["total"] == 67 != F.score(scores)
    assert data["band"] == "good" and stored(me)["rank_score"] == 67
    assert [p["total"] for p in data["panel"]] == [67, 67]


def test_default_weights_without_a_playbook_and_invalid_weights_block(tmp_path, fetcher):
    ctx, server, me = setup(tmp_path, [reply(eligibility=[]), reply(eligibility=[])])
    ctx.playbook = None
    go(ctx, ["a"])
    assert fit_json(me)["weights"] == F.WEIGHTS
    ctx2, server2, me2 = setup(tmp_path / "two", [])
    ctx2.playbook.weights = {"technical": 90, "experience": 5, "behavioural": 3, "career": 2}
    out = go(ctx2, ["a"])
    assert out.terminal == "blocked" and "weights" in out.failures[0] and server2.requests == [] and stored(me2)["status"] == "new"


def test_fit_weights_parameter_in_the_library():
    w = {"technical": 45, "experience": 15, "behavioural": 10, "career": 30}
    assert F.score(sc(100, 0, 0, 0), w) == 45 and F.score(sc(100, 0, 0, 0)) == 30
    fit, failures = F.build(GOOD, PROFILE, POSTING, "demo", w)
    assert failures == [] and fit.weights == w and fit.as_dict()["weights"] == w
    assert F.build(GOOD, PROFILE, POSTING, "demo")[0].weights == F.WEIGHTS
    with pytest.raises(ValueError):
        F.Fit("demo", 1, 1, 1, 1, fit.gates, (), (), (), {"technical": 50})


# ── fetching ──
def test_only_fetched_text_is_scored_and_a_404_is_not_closed(tmp_path, monkeypatch):
    f = Fetcher({"https://co.example/a": FT.Unreachable("HTTP 404"), "https://co.example/b": FT.Closed("the page says closed")})
    monkeypatch.setattr(T.fetching, "fetch_posting", f)
    ps = [posting("a", url="https://co.example/a"), posting("b", url="https://co.example/b")]
    ctx, server, me = setup(tmp_path, [], ps)
    out = go(ctx, ["a", "b"])
    assert server.requests == [] and not (me / "fit").exists()                   # nothing fetched, nothing scored
    assert stored(me, "a")["status"] == "unreachable" and stored(me, "a")["fetch_attempts"] == 1
    assert stored(me, "b")["status"] == "expired" and "rank_score" not in stored(me, "a")
    assert out.terminal == "success" and ctx.store.get(T.TRIED)["a"]
    assert f.asked[0][1]["acknowledged"] is False


def test_the_terms_acknowledgement_is_passed_to_the_fetch_ladder(tmp_path, fetcher):
    ctx, _, _ = setup(tmp_path, [reply(), reply()], config=Config(acknowledge_terms=True).validate())
    go(ctx, ["a"])
    assert fetcher.asked[0][1]["acknowledged"] is True


def test_an_unreachable_posting_is_given_up_after_three_failures_48_hours_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(T.fetching, "fetch_posting", Fetcher({"https://co.example/a": FT.Unreachable("HTTP 403")}))
    first = (sim.NOW - timedelta(hours=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ctx, _, me = setup(tmp_path, [], [posting("a", url="https://co.example/a", status="unreachable", fetch_attempts=2, first_unreachable=first)])
    go(ctx, ["a"])
    assert stored(me)["status"] == "gave_up"
    ctx, _, me = setup(tmp_path / "soon", [], [posting("a", url="https://co.example/a", status="unreachable", fetch_attempts=2,
                                                        first_unreachable=(sim.NOW - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))])
    go(ctx, ["a"])
    assert stored(me)["status"] == "unreachable" and stored(me)["fetch_attempts"] == 3          # retried later, not given up yet


def test_a_deadline_on_the_fetched_page_that_has_passed_expires_the_posting(tmp_path, monkeypatch):
    got = FT.Fetched(POSTING, "u", "board", 200, HOST, "n", deadline="2026-10-01")
    monkeypatch.setattr(T.fetching, "fetch_posting", lambda url, **kw: got)
    ctx, _, me = setup(tmp_path, [reply(), reply()])
    go(ctx, ["a"])
    assert stored(me)["status"] == "expired"


# ── stalled, exhausted, sweep ──
def test_every_evaluation_failing_validation_is_stalled_with_the_verbatim_failures(tmp_path, fetcher):
    ctx, server, me = setup(tmp_path, [bad()] * 8)
    out = go(ctx, ["a"])
    assert out.terminal == "stalled" and len(server.requests) == 8
    assert any("scores.experience must be an integer from 0 to 100, not None." in f for f in out.failures)
    assert stored(me)["status"] == "new" and not (me / "fit").exists()


def test_exhausted_before_any_work_makes_no_fetch_and_no_turn_but_still_sweeps(tmp_path, fetcher):
    ps = [posting("a"), posting("old", status="ranked", score=70, deadline="2026-10-01")]
    ctx, server, me = setup(tmp_path, [], ps)
    ctx.gateway.ledger.stop = "budget"
    out = go(ctx, ["a"])
    assert out.terminal == "exhausted" and server.requests == [] and fetcher.asked == []
    assert stored(me)["status"] == "new" and stored(me, "old")["status"] == "expired"


def test_exhausted_mid_batch_keeps_what_finished(tmp_path, fetcher):
    ctx, server, me = setup(tmp_path, [{**reply(), "cost": 0.2}], [posting("a"), posting("b", first_seen="2026-10-01")],
                            config=Config(judge_triage=False, action_budget_usd=0.15).validate())
    out = go(ctx, ["a", "b"])
    assert out.terminal == "exhausted" and len(server.requests) == 1 and len(fetcher.asked) == 1
    assert stored(me, "a")["status"] == "ranked" and stored(me, "b")["status"] == "new"


def test_the_sweep_always_runs_and_expires_ranked_postings_past_their_deadline(tmp_path, fetcher):
    ps = [posting("a"), posting("old", status="ranked", score=70, deadline="2026-10-01"), posting("ok", status="ranked", score=70)]
    ctx, _, me = setup(tmp_path, [reply(eligibility=[]), reply(eligibility=[])], ps)
    out = go(ctx, ["a"])
    assert stored(me, "old")["status"] == "expired" and stored(me, "ok")["status"] == "ranked"
    assert any("sweep: 2 checked, 1 expired" in n for n in out.notes)


# ── negative space ──
def test_triage_never_touches_the_tracker_profile_queue_or_archive(tmp_path, fetcher):
    ctx, _, me = setup(tmp_path, [reply(), reply(**VETO)], [posting("a")], tracker_rows=[("Old Co", "Old Role", "drafted")])
    before = snapshot.fingerprint(me)
    tracker_bytes = (me / "tracker.csv").read_bytes()
    out = go(ctx, ["a"])
    assert out.terminal == "success"
    assert snapshot.fingerprint(me) == before and (me / "tracker.csv").read_bytes() == tracker_bytes
    assert (me / "profile.md").read_text() == PROFILE
    assert not (me / "queue").exists() and not (me / "applications").exists() and ctx.store.queue() == []
    assert stored(me)["status"] == "ranked"
    with pytest.raises(AssertionError):
        assert snapshot.take(me, ctx.store, ctx.config, sim.NOW).state_of("a") == "drafted"


def test_a_posting_that_changed_meanwhile_is_left_as_it_is(tmp_path, fetcher):
    ctx, _, me = setup(tmp_path, [reply(), reply()])
    with store.transaction(me) as s:
        s["a"]["status"] = "skipped"
    go(ctx, ["a"])
    assert stored(me)["status"] == "skipped"

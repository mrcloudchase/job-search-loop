"""The draft stage: what it proposes, best-of-N and the judge, escalation, the maker-checker pair, and what it must never do.

The verifier is a stand-in (like tests/test_apply.py): a CV containing BAD fails with a page-count message, one containing BREAK
fails too. The posting text is pre-seeded where triage would have cached it, so no network is involved."""
from __future__ import annotations

import builtins
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

import sim
from jobloop import apply, archive, rank, tracker
from jobloop.engine import config as cfgmod, invariants, playbook, queue, runner, snapshot
from jobloop.engine.spec import Action
from jobloop.stages import draft as stage
from jobloop.verify import Result

DEEPSEEK, CODEX, REVIEWER = "claude-deepseek/deepseek-chat", "codex", "claude-reviewer/deepseek-chat"
URL = "https://www.jobindex.dk/jobannonce/h1"
APP = "northwind_analytics__data_engineer"
REVIEW = ('[]\n\nMissed keywords and requirements: none.\nCompany and department angles: none.\n'
          'Action-oriented reframing: none.\nTone and style: no issues.')
CLAIM = {"claim": "Builds inspection robots for offshore wind", "url": "https://www.northwind.example/about",
         "quote": "Northwind Analytics builds autonomous inspection robots for offshore wind farms."}


def cfg(**kw):
    return cfgmod.Config(**kw).validate()


class Checker:
    """The container verifier's stand-in: BAD or BREAK in the CV fails a check."""

    def __init__(self):
        self.calls = []

    def __call__(self, cv, cover, ctx, **kw):
        self.calls.append((cv, cover, ctx))
        if "BAD" in cv or "BREAK" in cv:
            return Result(("page count: the CV has 3 pages",))
        pdf = lambda text: SimpleNamespace(pdf=b"%PDF-" + text.encode())
        return Result((), documents=SimpleNamespace(cv=pdf(cv), cover=pdf(cover)), coverage=SimpleNamespace(table=lambda: "| Term | Status |"),
                      checks=(("compile", True),))


def draft(cv="cv one", cover="cover one"):
    return {"files": {"cv.tex": cv, "cover.tex": cover}, "cost": 0.1}


def scores(overall, failed=()):
    items = {str(n): {"verdict": "pass", "evidence": "fine"} for n in range(1, 25)}
    for n, why in failed:
        items[str(n)] = {"verdict": "fail", "evidence": why}
    return {"files": {"scores.json": json.dumps({"items": items, "overall": overall, "reasons": "Reads well. Needs one fix."})}, "cost": 0.1}


REVIEW_REPLY = {"answer": REVIEW, "cost": 0.1}


def spec(key="p1", company="Northwind Analytics", title="Data Engineer", score=80, **kw):
    return {"key": key, "company": company, "title": title, "score": score, **kw}


def world(tmp_path, monkeypatch, replies=(), specs=None, *, rounds=1, research="claims", tracker_rows=None, **config):
    """A candidate folder with ranked postings (each with a stored fit, a cached posting text and a research record), and a Context."""
    specs = specs or [spec()]
    postings, notes = [], {}
    for s in specs:
        s = dict(s)
        key, score = s.pop("key"), s.pop("score")
        fit, has_research, text = s.pop("fit", score), s.pop("research", True), s.pop("text", None)
        s.setdefault("url", URL)
        s.setdefault("status", "ranked")
        postings.append(sim.posting(key, score=score, **s))
        notes[key] = (fit, has_research, text)
    me = sim.make_me(tmp_path, postings=postings, tracker_rows=tracker_rows)
    for p in postings:
        fit, has_research, text = notes[p["key"]]
        if fit is not None:
            sim.write_fit(me, p["key"], total=fit, requirements=[{"term": "Python", "kind": "required", "backed": True, "quote": "Python", "synonyms": []}])
        (me / "postings").mkdir(exist_ok=True)
        (me / "postings" / f"{rank.fit_key(p['key'])}.txt").write_text(text or f"{p['title']} at {p['company']}\n\nWe need Python and SQL. Reference: NW-1\n")
        app = archive.key(p["company"], p["title"])
        (me / "locate").mkdir(exist_ok=True)                                         # the locate stage has already looked, and found nothing
        (me / "locate" / f"{app}.json").write_text(json.dumps({"found": False, "tried": [], "date": "2026-10-19"}))
        if has_research and research:
            (me / "research").mkdir(exist_ok=True)
            claims = [CLAIM] if research == "claims" else []
            (me / "research" / f"{app}.json").write_text(json.dumps({"company": p["company"], "domain": "", "claims": claims, "dropped": 0,
                                                                     "skipped": "" if claims else "nothing verifiable", "date": "2026-10-19"}))
    config.setdefault("judge_drafts", True)
    ctx, server = sim.context(tmp_path, list(replies), me=me, config=cfg(**config))
    (me / "playbook" / "v0001" / "strategy.json").write_text(json.dumps({"best_of": ctx.config.best_of, "min_fit": 60}))   # the playbook decides best_of
    ctx.playbook = playbook.read(me, 1)
    checker = Checker()
    monkeypatch.setattr(stage, "DEPS", apply.Deps(verify=checker))
    monkeypatch.setattr(stage, "ROUNDS", rounds)
    monkeypatch.setattr(stage, "FETCH", lambda *a, **k: pytest.fail("the cached posting text must be used"))
    return ctx, server, checker


def one(ctx, key=APP):
    actions = stage.STAGE.propose(ctx.snapshot)
    return next(a for a in actions if a.key == key)


def models(server):
    return [r["model"] for r in server.requests]


def files_of(request):
    return {f["filename"]: f["file_data"] for f in request["input"] if f["type"] == "input_file"}


def draft_json(ctx, key=APP):
    return json.loads((ctx.me / "applications" / key / "draft.json").read_text())


def statuses(ctx):
    path = ctx.me / "tracker.csv"
    return [r.status for r in tracker.read_rows(path)] if path.exists() else []


# ── propose ───────────────────────────────────────────────────────────────────────────────────────
def strategy(ctx, **values):
    path = ctx.me / "playbook" / "v0001" / "strategy.json"
    path.write_text(json.dumps({"best_of": 2, "min_fit": 60, **values}))
    ctx.playbook = playbook.read(ctx.me, 1)


def test_propose_offers_only_what_may_be_drafted_best_fit_first(tmp_path, monkeypatch):
    veto = [{"name": "location", "verdict": "FAIL", "notes": ["more than 3 days on site"]}]
    ctx, _, _ = world(tmp_path, monkeypatch, specs=[
        spec("a", "Acme", "Engineer", 70), spec("b", "Bravo", "Engineer", 90), spec("c", "Charlie", "Engineer", 85, gates=veto),
        spec("d", "Delta", "Engineer", 50), spec("e", "Echo", "Engineer", 95, research=False), spec("f", "Foxtrot", "Engineer", 88, fit=None),
        spec("g", "Golf", "Engineer", 91), spec("h", "Hotel", "Engineer", 89, status="new"), spec("i", "India", "Engineer", 92)],
        tracker_rows=[("Golf", "Engineer", "drafted")])
    (ctx.me / "applications" / "india__engineer").mkdir(parents=True)                                 # an archive folder already there
    assert [a.key for a in stage.STAGE.propose(ctx.snapshot)] == ["bravo__engineer", "acme__engineer"]
    first = stage.STAGE.propose(ctx.snapshot)[0]
    assert first.stage == "draft" and first.payload["posting_key"] == "b" and first.priority == 90


def test_propose_uses_the_playbooks_floor_and_research_may_be_switched_off(tmp_path, monkeypatch):
    ctx, _, _ = world(tmp_path, monkeypatch, specs=[spec("a", "Acme", "Engineer", 70), spec("b", "Bravo", "Engineer", 80, research=False)])
    assert [a.key for a in stage.STAGE.propose(ctx.snapshot)] == ["acme__engineer"]
    strategy(ctx, min_fit=75)
    assert stage.STAGE.propose(ctx.snapshot) == []                                                      # 70 is under the playbook's floor
    off, _, _ = world(tmp_path / "off", monkeypatch, specs=[spec("b", "Bravo", "Engineer", 80, research=False)], disabled_stages=["research"])
    assert [a.key for a in stage.STAGE.propose(off.snapshot)] == ["bravo__engineer"]


def test_propose_stops_at_max_new_drafts_for_the_current_pass(tmp_path, monkeypatch):
    ctx, _, _ = world(tmp_path, monkeypatch, specs=[spec("a", "Acme", "Engineer", 70), spec("b", "Bravo", "Engineer", 90), spec("c", "Charlie", "Engineer", 80)],
                      max_new_drafts=2)
    assert [a.key for a in stage.STAGE.propose(ctx.snapshot)] == ["bravo__engineer", "charlie__engineer"]
    ctx.store.record_attempt(ctx.run_id, "draft", "bravo__engineer", "r", CODEX, "2026-10-19T12:00:00", "2026-10-19T12:01:00", "success", 0, 0, [], [])
    assert [a.key for a in stage.STAGE.propose(ctx.snapshot)] == ["bravo__engineer"]                 # one place left (its tracker row is not in this snapshot)
    ctx.store.record_attempt(ctx.run_id, "draft", "charlie__engineer", "r", CODEX, "2026-10-19T12:00:00", "2026-10-19T12:01:00", "success", 0, 0, [], [])
    assert stage.STAGE.propose(ctx.snapshot) == []
    ctx.store.finish_run(ctx.run_id, "success", 0, 0, "", {})                                           # the next pass starts afresh
    ctx.store.start_run("test", "{}")
    assert len(stage.STAGE.propose(ctx.snapshot)) == 2


def test_run_refuses_what_propose_would_not_offer(tmp_path, monkeypatch):
    veto = [{"name": "location", "verdict": "FAIL", "notes": ["more than 3 days on site"]}]
    ctx, server, _ = world(tmp_path, monkeypatch, specs=[spec("a", "Acme", "Engineer", 70, gates=veto), spec("b", "Bravo", "Engineer", 80),
                                                         spec("c", "Charlie", "Engineer", 80, fit=None), spec("d", "Delta", "Engineer", 80, research=False)],
                           tracker_rows=[("Bravo", "Engineer", "drafted")])
    run = lambda app, key: stage.STAGE.run(Action("draft", app, "x", payload={"posting_key": key}), ctx)
    out = run("acme__engineer", "a")
    assert out.terminal == "blocked" and out.failures[0].startswith("vetoed")
    assert run("bravo__engineer", "b").terminal == "no_op"
    assert run("charlie__engineer", "c").terminal == "blocked"
    out = run("delta__engineer", "d")
    assert out.terminal == "blocked" and "no research record" in out.failures[0]
    strategy(ctx, min_fit=95)
    ctx.playbook = playbook.read(ctx.me, 1)
    ctx2, _, _ = world(tmp_path / "f", monkeypatch, specs=[spec("a", "Acme", "Engineer", 70)])
    ctx2.playbook = ctx.playbook
    assert stage.STAGE.run(Action("draft", "acme__engineer", "x", payload={"posting_key": "a"}), ctx2).failures == ["fit 70 is below the floor 95"]
    assert server.requests == [] and statuses(ctx) == ["drafted"]


# ── the drafting ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("first,second,winner,reviewer,mean", [((6, 6), 9, CODEX, REVIEWER, 9.0), ((9, 9), 5, DEEPSEEK, CODEX, 9.0), ((7, 7), 7, DEEPSEEK, CODEX, 7.0)])
def test_best_of_two_picks_the_judged_best_and_ties_keep_the_first(tmp_path, monkeypatch, first, second, winner, reviewer, mean):
    replies = [draft("cv one"), draft("cv two"), scores(first[0]), scores(first[1]), scores(second),       # drafts, then judges (not the drafter's family)
               REVIEW_REPLY, draft("cv final", "cover final")]
    ctx, server, _ = world(tmp_path, monkeypatch, replies)
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "success", out
    assert models(server) == [DEEPSEEK, CODEX, CODEX, REVIEWER, REVIEWER, reviewer, winner]            # draft 1 is another family; judges are not the maker's
    data = draft_json(ctx)
    assert data["harnesses"] == {"drafter": winner, "reviewer": reviewer} and data["best_of"] == 2
    assert data["judge"]["mean"] == mean
    assert (ctx.me / "applications" / APP / "cv.tex").read_text() == "cv final"
    scores_stored = {(j["subject"], j["judge"]): j["score"] for j in ctx.store.judgments(APP)}
    assert scores_stored[("draft-candidate-0", CODEX)] == first[0] and scores_stored[("draft-candidate-1", REVIEWER)] == second
    assert {j["subject"] for j in ctx.store.judgments(APP, "draft")} == {"draft"} and ctx.store.judgments(APP, "draft")


def test_draft_json_follows_the_contract(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, [draft(), REVIEW_REPLY, draft("cv final")], [spec(deadline="2026-11-30")], best_of=1, judge_drafts=False)
    out = stage.STAGE.run(one(ctx), ctx)
    data = draft_json(ctx)
    assert out.terminal == "success" and "first pass" in " ".join(out.notes) and out.produced["harness"] == DEEPSEEK
    assert set(data) >= {"key", "posting_key", "company", "role", "url", "fit", "deadline", "host_kind", "gaps", "claims", "judge", "first_pass",
                         "harnesses", "best_of", "notes"}
    assert data["key"] == APP and data["posting_key"] == "p1" and data["fit"] == 80 and data["deadline"] == "2026-11-30"
    assert data["first_pass"] is True and "first pass" in " ".join(data["notes"]) and data["host_kind"] == "portal"
    assert data["claims"] == [CLAIM] and data["judge"] == {"mean": None, "judges": []} and data["gaps"] == []
    assert statuses(ctx) == ["drafted"] and (ctx.me / "applications" / APP / "cv.pdf").exists()
    row = tracker.read_rows(ctx.me / "tracker.csv")[0]
    assert row.key == APP and row.deadline == "2026-11-30"


def test_a_draft_that_needed_a_fix_round_is_not_a_first_pass(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, [draft("BAD cv"), draft("cv fixed"), REVIEW_REPLY, draft("cv final")], best_of=1, judge_drafts=False)
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "success" and not any("first pass" in n for n in out.notes)
    assert draft_json(ctx)["first_pass"] is False and not any("first pass" in n for n in draft_json(ctx)["notes"])


def test_failures_go_back_verbatim_then_a_draft_that_never_passes_is_dropped(tmp_path, monkeypatch):
    replies = [draft("BAD one"), draft("BAD two"),                                                       # draft 0: fails, one fix round, still fails: dropped
               draft("cv good"), REVIEW_REPLY, draft("cv final")]
    ctx, server, _ = world(tmp_path, monkeypatch, replies, judge_drafts=False)
    out = stage.STAGE.run(one(ctx), ctx)
    fix = server.requests[1]
    assert fix["previous_response_id"] == "resp_1" and fix["input"][0]["text"] == "page count: the CV has 3 pages" and fix["model"] == DEEPSEEK
    assert models(server)[2] == CODEX and "previous_response_id" not in server.requests[2]             # draft 1: a fresh conversation, another family
    assert out.terminal == "success" and draft_json(ctx)["harnesses"]["drafter"] == CODEX and draft_json(ctx)["first_pass"] is True
    assert any("1 of 2 drafts passed" in n for n in out.notes)


def test_no_passing_draft_is_stalled_with_the_verbatim_failures_and_writes_nothing(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, [draft("BAD a"), draft("BAD b"), draft("BAD c"), draft("BAD d")])
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "stalled" and out.failures == ["page count: the CV has 3 pages"] and out.produced["harness"] == DEEPSEEK
    assert statuses(ctx) == [] and not (ctx.me / "tracker.csv").exists() and not (ctx.me / "applications" / APP).exists()
    assert ctx.store.judgments(APP) == [] and "review" not in " ".join(models(server))


def test_a_stalled_attempt_moves_the_drafter_and_reviewer_down_the_ladder(tmp_path, monkeypatch):
    replies = [draft(), scores(8), REVIEW_REPLY, draft("cv final")]
    ctx, server, _ = world(tmp_path, monkeypatch, replies, best_of=1)
    ctx.store.record_attempt(ctx.run_id, "draft", APP, "r", DEEPSEEK, "2026-10-18T12:00:00", "2026-10-18T12:01:00", "stalled", 0, 0, ["x"], [])
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "success" and out.produced["harness"] == CODEX
    assert models(server) == [CODEX, REVIEWER, REVIEWER, CODEX]                                        # drafter codex now; judge and reviewer are not codex
    assert draft_json(ctx)["harnesses"] == {"drafter": CODEX, "reviewer": REVIEWER}


def test_the_engine_stalls_then_escalates_to_the_next_harness_and_succeeds(tmp_path, monkeypatch):
    replies = [draft("BAD a"), draft("BAD b"),                                                           # pass 1, attempt 1: deepseek never passes
               draft("cv good"), scores(8), REVIEW_REPLY, draft("cv final")]                           # attempt 2 (same pass): codex passes
    ctx, server, _ = world(tmp_path, monkeypatch, replies, best_of=1)
    result = runner.run_pass(ctx.me, [stage.STAGE], config=ctx.config, transport=server.transport(), now=lambda: sim.NOW, out=lambda s: None,
                             playbook_loader=lambda me, store: playbook.load(me, store, defaults={"best_of": 1}))
    assert result.terminal == "success" and result.violations == []
    assert models(server)[:2] == [DEEPSEEK, DEEPSEEK] and models(server)[2] == CODEX
    assert statuses(ctx) == ["drafted"]
    terminals = [a["terminal"] for a in ctx.store.attempts("draft", APP)]
    assert terminals == ["stalled", "success"]


def test_the_reviewer_is_never_the_drafters_family(tmp_path, monkeypatch):
    for n, ladders in enumerate(({}, {"reviewer": [DEEPSEEK], "judge": [DEEPSEEK]}, {"drafter": [CODEX], "reviewer": ["codex/x", REVIEWER]})):
        sub = tmp_path / str(n)
        replies = [draft(), scores(8), scores(8), REVIEW_REPLY, draft("cv final")]
        ctx, server, _ = world(sub, monkeypatch, replies, best_of=1, judge_drafts=False, ladders={**cfgmod.DEFAULT_LADDERS, **ladders})
        out = stage.STAGE.run(one(ctx), ctx)
        data = draft_json(ctx)
        assert out.terminal == "success"
        drafter, reviewer = data["harnesses"]["drafter"], data["harnesses"]["reviewer"]
        if reviewer:
            assert drafter.split("/")[0] != reviewer.split("/")[0]
        else:                                                                                            # no other family on the ladder: no review, said so
            assert any("not reviewed" in n for n in data["notes"]) and len(server.requests) == 1


def test_judge_failures_drive_one_revision_that_is_kept_when_it_passes_and_scores_no_lower(tmp_path, monkeypatch):
    fails = [(3, "the letter never names the Spark gap"), (11, "the second CV page is thin")]
    replies = [draft("cv A", "cover A"), scores(6, fails), scores(6, fails),                           # judged by two other families
               draft("cv B", "cover B"), scores(8), scores(8),                                          # the revision, re-judged
               REVIEW_REPLY, draft("cv final", "cover final")]
    ctx, server, checker = world(tmp_path, monkeypatch, replies, best_of=1)
    out = stage.STAGE.run(one(ctx), ctx)
    revision = server.requests[3]
    assert revision["model"] == DEEPSEEK and revision["previous_response_id"] == "resp_1"
    text = revision["input"][0]["text"]
    assert "item 3: the letter never names the Spark gap" in text and "item 11: the second CV page is thin" in text
    assert files_of(revision) == {"cv.tex": "cv A", "cover.tex": "cover A"}
    assert out.terminal == "success" and any("revision kept" in n for n in out.notes)
    assert server.requests[-1]["previous_response_id"] == "resp_4"                                      # the reviewer's revise continues the kept revision
    data = draft_json(ctx)
    assert data["judge"]["mean"] == 8.0 and data["judge"]["judges"][0]["failed"] == []
    assert [c[0] for c in checker.calls] == ["cv A", "cv B", "cv final"]


def test_a_judge_revision_that_breaks_a_check_is_discarded(tmp_path, monkeypatch):
    fails = [(3, "the letter never names the Spark gap")]
    replies = [draft("cv A"), scores(6, fails), scores(6, fails),
               draft("cv BREAK"), draft("cv BREAK again"),                                              # the revision fails its check; one fix round fails too
               REVIEW_REPLY, draft("cv final")]
    ctx, server, _ = world(tmp_path, monkeypatch, replies, best_of=1)
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "success" and any("revision discarded: it did not pass every check" in n for n in out.notes)
    assert server.requests[-1]["previous_response_id"] == "resp_1"                                      # the review continues the pre-revision draft
    assert draft_json(ctx)["judge"]["mean"] == 6.0 and (ctx.me / "applications" / APP / "cv.tex").read_text() == "cv final"


def test_a_judge_revision_that_lowers_the_score_is_discarded(tmp_path, monkeypatch):
    fails = [(3, "the letter never names the Spark gap")]
    replies = [draft("cv A"), scores(7, fails), scores(7, fails), draft("cv B"), scores(5), scores(5), REVIEW_REPLY, draft("cv final")]
    ctx, server, _ = world(tmp_path, monkeypatch, replies, best_of=1)
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "success" and any("its score dropped" in n for n in out.notes)
    assert server.requests[-1]["previous_response_id"] == "resp_1" and draft_json(ctx)["judge"]["mean"] == 7.0


def test_a_single_passing_draft_is_judged_once_and_left_alone_without_failed_items(tmp_path, monkeypatch):
    replies = [draft(), scores(8), scores(8), REVIEW_REPLY, draft("cv final")]
    ctx, server, _ = world(tmp_path, monkeypatch, replies, best_of=1)
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "success" and models(server) == [DEEPSEEK, CODEX, REVIEWER, CODEX, DEEPSEEK]
    assert not any("revision" in n for n in out.notes)


def test_the_playbook_style_addendum_reaches_the_drafter_only(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, [draft(), REVIEW_REPLY, draft("cv final")], best_of=1, judge_drafts=False)
    (ctx.me / "playbook" / "v0001" / "style.md").write_text("- Prefer short sentences\n")
    ctx.playbook = playbook.read(ctx.me, 1)
    strategy(ctx, best_of=1)
    stage.STAGE.run(one(ctx), ctx)
    drafter, reviewer, revise = server.requests
    assert "Prefer short sentences" in drafter["instructions"] and "Prefer short sentences" in revise["instructions"]
    assert "Prefer short sentences" not in reviewer["instructions"]


# ── what travels where ────────────────────────────────────────────────────────────────────────────
def test_research_claims_travel_as_a_file_and_the_profile_goes_only_to_drafter_reviewer_and_judge(tmp_path, monkeypatch):
    replies = [draft(), scores(8), REVIEW_REPLY, draft("cv final")]
    ctx, server, _ = world(tmp_path, monkeypatch, replies, best_of=1, ladders={**cfgmod.DEFAULT_LADDERS, "judge": [REVIEWER]})
    stage.STAGE.run(one(ctx), ctx)
    drafter, judge_turn, reviewer, revise = server.requests
    assert CLAIM["claim"] in files_of(drafter)["research.md"] and CLAIM["claim"] in files_of(reviewer)["research.md"]
    for body in server.requests:
        assert CLAIM["claim"] not in body["instructions"] and CLAIM["claim"] not in body["input"][0]["text"]
        assert "We need Python" not in body["instructions"] and body["model"] != "claude-research/deepseek-chat"
    assert "profile.md" in files_of(judge_turn) and "profile.md" in files_of(drafter) and "profile.md" in files_of(reviewer)
    assert "research.md" not in files_of(judge_turn)
    assert apply.NOTICE in drafter["instructions"] and apply.NOTICE in reviewer["instructions"]


def test_without_a_researched_claim_the_letter_is_told_to_make_none(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, [draft(), REVIEW_REPLY, draft("cv final")], best_of=1, judge_drafts=False,
                           research="skipped")
    out = stage.STAGE.run(one(ctx), ctx)
    assert "Make no claims about the company" in files_of(server.requests[0])["research.md"]
    assert out.terminal == "success" and draft_json(ctx)["claims"] == []


# ── the source host ───────────────────────────────────────────────────────────────────────────────
def test_an_unverified_host_is_blocked_with_a_needs_decision_entry_and_nothing_is_drafted(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, specs=[spec(url="https://jobs.evil.example/123")])
    action = one(ctx)                                                                                    # proposed once, so the person is told
    out = stage.STAGE.run(action, ctx)
    assert out.terminal == "blocked" and out.failures[0].startswith("unverified source host: jobs.evil.example")
    entry = ctx.store.queue_item(APP)
    assert entry["status"] == "needs_decision" and json.loads(entry["data"])["kind"] == "unverified_source_host"
    assert server.requests == [] and statuses(ctx) == [] and not (ctx.me / "applications" / APP).exists()
    assert stage.STAGE.propose(ctx.snapshot) == []                                                       # and not offered again while it waits


def test_a_parked_host_is_looked_at_again_once_trusted_hosts_covers_it(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, specs=[spec(url="https://jobs.evil.example/123")])
    stage.STAGE.run(one(ctx), ctx)
    assert stage.STAGE.propose(ctx.snapshot) == []
    ctx.snapshot.config.trusted_hosts = ["evil.example"]
    assert [a.key for a in stage.STAGE.propose(ctx.snapshot)] == [APP]


def test_a_posting_text_read_from_an_unverified_host_is_not_drafted_either(tmp_path, monkeypatch):
    from jobloop import fetch
    from jobloop.guards import hosts
    ctx, server, _ = world(tmp_path, monkeypatch)
    (ctx.me / "postings" / f"{rank.fit_key('p1')}.txt").unlink()
    monkeypatch.setattr(stage, "FETCH", lambda url, **kw: fetch.Fetched("Data Engineer at Northwind Analytics\nPython", "https://jobs.aggregator.example/1", "careers",
                                                                       200, hosts.HostVerdict("unverified", "jobs.aggregator.example", None, "no"), "n"))
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "blocked" and out.failures[0].startswith("unverified source host") and server.requests == []
    assert ctx.store.queue_item(APP)["status"] == "needs_decision"


def test_nothing_ever_asks_on_the_terminal(tmp_path, monkeypatch):
    def no_input(*a, **k):
        raise AssertionError("input() was called in an unattended pass")
    monkeypatch.setattr(builtins, "input", no_input)
    ctx, server, _ = world(tmp_path, monkeypatch, [draft(), scores(8), scores(8), REVIEW_REPLY, draft("cv final")], best_of=1)
    assert stage.STAGE.run(one(ctx), ctx).terminal == "success"
    prepared = apply.Prepared("text", "", SimpleNamespace(company="A", role="B"), None, "profile")
    with pytest.raises(apply.NeedsDecision):
        apply.Application.for_engine(ctx.me, None, prepared, drafter=DEEPSEEK, reviewer=CODEX).deps.confirm("Continue anyway?")


def test_a_question_only_the_person_can_answer_becomes_a_queue_entry(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, [draft()], best_of=1)

    def ask(self, *a, **k):
        return self.deps.confirm("Draft this although the experience match is low?")
    monkeypatch.setattr(apply.Application, "attempt", ask)
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "blocked" and "needs a person" in out.failures[0]
    assert ctx.store.queue_item(APP)["status"] == "needs_decision" and statuses(ctx) == []


# ── stopping ──────────────────────────────────────────────────────────────────────────────────────
def test_exhausted_before_any_draft_leaves_the_tracker_and_archive_untouched(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch)
    ctx.gateway.ledger.stop = "budget"
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "exhausted" and server.requests == []
    assert not (ctx.me / "tracker.csv").exists() and not (ctx.me / "applications" / APP).exists() and ctx.store.judgments() == []


def test_a_stop_after_a_passing_draft_still_records_it_as_drafted_without_a_review(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, [{"files": draft()["files"], "cost": 5.0}], best_of=1)
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "exhausted" and len(server.requests) == 1
    assert statuses(ctx) == ["drafted"] and draft_json(ctx)["harnesses"]["reviewer"] == ""
    assert any("not reviewed" in n for n in draft_json(ctx)["notes"])


# ── the engine around it ──────────────────────────────────────────────────────────────────────────
def test_a_pass_drafts_at_most_max_new_drafts_and_writes_only_drafted(tmp_path, monkeypatch):
    specs = [spec("a", "Acme", "Engineer", 70), spec("b", "Bravo", "Engineer", 90), spec("c", "Charlie", "Engineer", 80)]
    replies = [draft("cv b"), REVIEW_REPLY, draft("cv b final"), draft("cv c"), REVIEW_REPLY, draft("cv c final")]
    ctx, server, _ = world(tmp_path, monkeypatch, replies, specs, max_new_drafts=2, best_of=1, judge_drafts=False)
    before = tracker.read_rows(ctx.me / "tracker.csv") if (ctx.me / "tracker.csv").exists() else ()
    result = runner.run_pass(ctx.me, [stage.STAGE], config=ctx.config, transport=server.transport(), now=lambda: sim.NOW, out=lambda s: None,
                             playbook_loader=lambda me, store: playbook.load(me, store, defaults={"best_of": 1}))
    assert before == () and result.violations == [] and result.terminal == "success" and result.actions == 2
    assert {r.key: r.status for r in tracker.read_rows(ctx.me / "tracker.csv")} == {"bravo__engineer": "drafted", "charlie__engineer": "drafted"}
    assert not (ctx.me / "applications" / "acme__engineer").exists()
    assert (ctx.me / "applications" / "bravo__engineer" / "draft.json").exists()
    assert (ctx.me / "profile.md").read_text() == sim.PROFILE.read_text()


def test_the_same_application_is_never_drafted_twice(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, [draft(), REVIEW_REPLY, draft("cv final")], best_of=1, judge_drafts=False)
    assert stage.STAGE.run(one(ctx), ctx).terminal == "success"
    from jobloop.engine import snapshot
    ctx.snapshot = snapshot.take(ctx.me, ctx.store, ctx.config, ctx.now + timedelta(hours=1))
    assert stage.STAGE.propose(ctx.snapshot) == []
    assert stage.STAGE.run(Action("draft", APP, "again", payload={"posting_key": "p1"}), ctx).terminal == "no_op"
    assert len(server.requests) == 3


# ── an unverified host the person can resolve: the employer's own site, trusted_hosts, or an approval ──
DPG = "dpg_media__data_engineer"
OK_REPLIES = [draft(), REVIEW_REPLY, draft("cv final")]


def dpg(url, **config):
    return dict(specs=[spec(company="DPG Media", title="Data Engineer", url=url)], best_of=1, judge_drafts=False, **config)


def test_the_employers_own_site_is_accepted_and_drafted_with_the_note_and_the_truthful_host_kind(tmp_path, monkeypatch):
    ctx, server, checker = world(tmp_path, monkeypatch, OK_REPLIES, **dpg("https://vacatures.dpgmedia.nl/vacature/1"))
    before = snapshot.fingerprint(ctx.me)
    out = stage.STAGE.run(one(ctx, DPG), ctx)
    data = draft_json(ctx, DPG)
    assert out.terminal == "success" and statuses(ctx) == ["drafted"] and ctx.store.queue_item(DPG) is None
    assert data["host_trust"] == "employer" and data["host_kind"] == "unverified"
    assert any(n.startswith("source host accepted: vacatures.dpgmedia.nl is the employer's own site") for n in data["notes"])
    assert checker.calls and invariants.violations(before, snapshot.fingerprint(ctx.me)) == []        # the verifiers ran; only `drafted` was written


@pytest.mark.parametrize("url", ["https://www.dpgmedia.nl/x", "https://careers.dpgmedia.nl/x", "https://jobs.dpgmedia.nl/x", "https://karriere.dpgmedia.nl/x"])
def test_careers_style_prefixes_of_the_employers_site_are_accepted(tmp_path, monkeypatch, url):
    ctx, _, _ = world(tmp_path, monkeypatch, OK_REPLIES, **dpg(url))
    assert stage.STAGE.run(one(ctx, DPG), ctx).terminal == "success" and draft_json(ctx, DPG)["host_trust"] == "employer"


def test_a_configured_company_domain_makes_a_host_the_employers(tmp_path, monkeypatch):
    ctx, _, _ = world(tmp_path, monkeypatch, OK_REPLIES, specs=[spec(url="https://apply.nw-group.example/1")], best_of=1, judge_drafts=False,
                      company_domains={"northwind analytics": "nw-group.example"})
    assert stage.STAGE.run(one(ctx), ctx).terminal == "success" and draft_json(ctx)["host_trust"] == "employer"


@pytest.mark.parametrize("url", ["https://it.whatjobs.com/job/1", "https://startup.jobs/job/1", "https://dpgmedia-jobs.evil.io/1",
                                 "https://dpgmedia.evil.io/1", "https://evil-dpgmedia.nl/1", "https://evil.io/dpgmedia"])
def test_aggregators_and_look_alikes_stay_blocked_without_approval(tmp_path, monkeypatch, url):
    ctx, server, _ = world(tmp_path, monkeypatch, **dpg(url))
    out = stage.STAGE.run(one(ctx, DPG), ctx)
    entry = ctx.store.queue_item(DPG)
    data = json.loads(entry["data"])
    assert out.terminal == "blocked" and out.failures[0].startswith("unverified source host")
    assert entry["status"] == "needs_decision" and data["kind"] == "unverified_source_host" and data["host"] in url
    assert f"jobloop queue approve {DPG}" in data["how"] and "if you checked the employer and the link" in data["how"]
    assert server.requests == [] and statuses(ctx) == [] and not (ctx.me / "applications" / DPG).exists()


def test_trusted_hosts_match_on_a_label_boundary_only(tmp_path, monkeypatch):
    ctx, _, _ = world(tmp_path, monkeypatch, OK_REPLIES, **dpg("https://nl.whatjobs.com/job/1", trusted_hosts=["whatjobs.com"]))
    assert stage.STAGE.run(one(ctx, DPG), ctx).terminal == "success"
    data = draft_json(ctx, DPG)
    assert data["host_trust"] == "trusted" and data["host_kind"] == "unverified" and "trusted_hosts" in " ".join(data["notes"])
    for n, url in enumerate(["https://evil-whatjobs.com/1", "https://whatjobs.com.evil.io/1"]):
        ctx, server, _ = world(tmp_path / f"x{n}", monkeypatch, **dpg(url, trusted_hosts=["whatjobs.com"]))
        assert stage.STAGE.run(one(ctx, DPG), ctx).terminal == "blocked" and server.requests == [] and statuses(ctx) == []


def test_an_approval_covers_that_application_only_and_persists_across_passes(tmp_path, monkeypatch):
    other = spec("p2", "Other Corp", "Data Engineer", url="https://startup.jobs/job/2")
    ctx, server, _ = world(tmp_path, monkeypatch, OK_REPLIES, specs=[spec(company="DPG Media", title="Data Engineer", url="https://it.whatjobs.com/1"), other],
                           best_of=1, judge_drafts=False)
    assert all(stage.STAGE.run(a, ctx).terminal == "blocked" for a in stage.STAGE.propose(ctx.snapshot))
    assert stage.STAGE.propose(ctx.snapshot) == []                                                     # both wait for the person
    queue.approve(ctx.me, ctx.store, DPG)
    assert ctx.store.queue_item(DPG) is None and statuses(ctx) == [] and server.requests == []          # approval drafts nothing itself
    ctx2 = sim.context(tmp_path, list(OK_REPLIES), me=ctx.me, config=ctx.config)[0]                     # a later pass: only the event remains
    proposed = [a.key for a in stage.STAGE.propose(ctx2.snapshot)]
    assert proposed == [DPG]
    out = stage.STAGE.run(stage.STAGE.propose(ctx2.snapshot)[0], ctx2)
    assert out.terminal == "success" and draft_json(ctx2, DPG)["host_trust"] == "approved" and statuses(ctx2) == ["drafted"]
    assert ctx2.store.queue_item("other_corp__data_engineer")["status"] == "needs_decision"             # the other one is still not trusted


def test_an_approved_posting_whose_text_came_from_an_unverified_host_is_drafted_with_the_note(tmp_path, monkeypatch):
    from jobloop import fetch
    from jobloop.guards import hosts
    ctx, _, _ = world(tmp_path, monkeypatch, OK_REPLIES, best_of=1, judge_drafts=False)
    (ctx.me / "postings" / f"{rank.fit_key('p1')}.txt").unlink()
    monkeypatch.setattr(stage, "FETCH", lambda url, **kw: fetch.Fetched("Data Engineer at Northwind Analytics\nPython", "https://jobs.aggregator.example/1", "careers",
                                                                       200, hosts.HostVerdict("unverified", "jobs.aggregator.example", None, "no"), "n"))
    ctx.store.add_event(queue.APPROVED, APP, {"host": ""})
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "success" and draft_json(ctx)["host_trust"] == "approved"


def test_the_text_host_that_is_the_employers_own_site_is_accepted(tmp_path, monkeypatch):
    from jobloop import fetch
    from jobloop.guards import hosts
    ctx, _, _ = world(tmp_path, monkeypatch, OK_REPLIES, best_of=1, judge_drafts=False)
    (ctx.me / "postings" / f"{rank.fit_key('p1')}.txt").unlink()
    monkeypatch.setattr(stage, "FETCH", lambda url, **kw: fetch.Fetched("Data Engineer at Northwind Analytics\nPython", "https://careers.northwind.example/1", "careers",
                                                                       200, hosts.HostVerdict("unverified", "careers.northwind.example", None, "no"), "n"))
    assert stage.STAGE.run(one(ctx), ctx).terminal == "success" and draft_json(ctx)["host_trust"] == "employer"


def test_an_accepted_host_does_not_waive_a_failing_verifier(tmp_path, monkeypatch):
    ctx, _, _ = world(tmp_path, monkeypatch, [draft("BAD"), draft("BAD")], **dpg("https://vacatures.dpgmedia.nl/1"), rounds=1)
    out = stage.STAGE.run(one(ctx, DPG), ctx)
    assert out.terminal == "stalled" and statuses(ctx) == [] and not (ctx.me / "applications" / DPG / "draft.json").exists()


# ── the employer's own posting (locate) ───────────────────────────────────────────────────────────
ATS_URL = "https://boards.greenhouse.io/northwind/jobs/7"


def test_an_unlocated_aggregator_posting_waits_for_the_locate_stage(tmp_path, monkeypatch):
    ctx, _, _ = world(tmp_path, monkeypatch, specs=[spec(url="https://nl.whatjobs.com/job/1")])
    (ctx.me / "locate" / f"{APP}.json").unlink()
    assert stage.STAGE.propose(ctx.snapshot) == []                                 # not parked, not blocked: it is waiting


def test_a_located_posting_is_drafted_from_the_employers_ats_page_and_its_link_is_the_apply_link(tmp_path, monkeypatch):
    ctx, server, _ = world(tmp_path, monkeypatch, [draft(), REVIEW_REPLY, draft("cv final")], best_of=1, judge_drafts=False,
                           specs=[spec(url="https://nl.whatjobs.com/job/1")])
    (ctx.me / "locate" / f"{APP}.json").write_text(json.dumps({
        "found": True, "url": ATS_URL, "ats": "greenhouse", "title": "Data Engineer", "why": "x",
        "description": "Data Engineer at Northwind Analytics\n\nWe need Python and SQL. Reference: NW-1\n"}))
    out = stage.STAGE.run(one(ctx), ctx)
    assert out.terminal == "success", out.failures
    assert draft_json(ctx)["url"] == ATS_URL and ctx.store.queue_item(APP) is None


def test_a_record_that_points_off_an_ats_is_not_used(tmp_path, monkeypatch):
    from jobloop.stages import locate
    ctx, _, _ = world(tmp_path, monkeypatch, specs=[spec(url="https://nl.whatjobs.com/job/1")])
    (ctx.me / "locate" / f"{APP}.json").write_text(json.dumps({"found": True, "url": "https://evil.example/job", "ats": "lever"}))
    assert locate.located(ctx.me, APP) is None

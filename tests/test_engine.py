"""The engine: policy, runner, brakes, tripwires, digest, playbook. Stages here are stubs; the stage tests use the fake server."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from jobloop import tracker
from jobloop.engine import config as cfgmod, playbook, policy, runner
from jobloop.engine.gateway import Gateway, Ledger
from jobloop.engine.spec import Action, Brakes, Outcome, Spec, Trigger
from jobloop.engine.store import Store, digest_of

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def cfg(**kw):
    c = cfgmod.Config(**kw)
    return c.validate()


class Stub:
    """A stage whose proposals and behaviour a test scripts."""

    def __init__(self, name="work", rank=50, items=("a",), behaviour=None, brakes=Brakes(), trigger=Trigger()):
        self.spec = Spec(name, "do the thing", trigger=trigger, rank=rank, brakes=brakes)
        self.items, self.calls = list(items), []
        self.behaviour = behaviour or (lambda action, ctx: Outcome("success"))

    def propose(self, snap):
        done = {a["item"] for a in snap.store.db.execute("SELECT item FROM attempts WHERE stage=? AND terminal='success'", (self.spec.name,))}
        return [Action(self.spec.name, k, f"{k} needs {self.spec.name}", priority=1) for k in self.items if k not in done]

    def run(self, action, ctx):
        self.calls.append(action.key)
        return self.behaviour(action, ctx)


def run(tmp_path, stages, at=NOW, **kw):
    if not (tmp_path / "profile.md").exists() and not kw.pop("no_profile", False):      # stages other than onboard need a profile to run
        (tmp_path / "profile.md").write_text("# A\n")
    return runner.run_pass(tmp_path, stages, config=kw.pop("config", cfg()), now=lambda: at, out=lambda s: None, **kw)


# ── config ──
def test_loop_json_is_written_with_defaults_and_unknown_keys_are_refused(tmp_path):
    assert cfgmod.load(tmp_path).max_new_drafts == 3 and (tmp_path / "loop.json").exists()
    (tmp_path / "loop.json").write_text(json.dumps({"max_new_draft": 9}))
    with pytest.raises(cfgmod.ConfigError, match="max_new_draft"):
        cfgmod.load(tmp_path)


@pytest.mark.parametrize("bad", [{"best_of": 9}, {"min_fit": 101}, {"pass_budget_usd": 0}, {"ladders": {"drafter": []}}])
def test_config_limits_are_validated(bad):
    with pytest.raises(cfgmod.ConfigError):
        cfg(**bad)


# ── the pass ──
def test_a_pass_runs_every_proposed_action_once_and_writes_its_digest(tmp_path):
    stub = Stub(items=("a", "b"))
    result = run(tmp_path, [stub])
    assert stub.calls == ["a", "b"] and result.terminal == "success" and result.actions == 2
    assert result.digest_path.exists() and (tmp_path / "inbox" / "latest.md").exists()
    assert "Waiting for you" in (tmp_path / "inbox" / "latest.md").read_text()


def test_a_pass_with_nothing_to_do_is_a_no_op(tmp_path):
    assert run(tmp_path, [Stub(items=())]).terminal == "no_op"


def test_two_passes_cannot_overlap(tmp_path):
    seen = {}

    def inner(action, ctx):
        seen["second"] = run(tmp_path, [Stub(items=("x",))])
        return Outcome("success")

    run(tmp_path, [Stub(behaviour=inner)])
    assert seen["second"].terminal == "blocked" and "another pass" in seen["second"].reason


def test_a_stage_that_raises_is_blocked_and_the_pass_goes_on(tmp_path):
    def boom(action, ctx):
        raise RuntimeError("disk on fire")

    bad, good = Stub("bad", rank=90, behaviour=boom), Stub("good", rank=10)
    result = run(tmp_path, [bad, good])
    assert good.calls == ["a"] and result.terminal == "success"
    with Store(tmp_path) as store:
        first = store.attempts("bad", "a")[0]
        assert first["terminal"] == "blocked" and "disk on fire" in first["failures"]


def test_a_dead_harness_server_ends_the_pass_at_once_and_counts_against_nothing(tmp_path):
    import httpx

    def down(action, ctx):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", "http://127.0.0.1:8000/responses"))

    stub = Stub(behaviour=down, items=("a", "b", "c"))
    result = run(tmp_path, [stub])
    assert stub.calls == ["a"] and result.terminal == "blocked" and "hi server start" in result.reason
    with Store(tmp_path) as store:
        assert store.attempts("work", "a")[0]["terminal"] == "exhausted"


def test_a_refused_connection_to_some_other_host_is_an_ordinary_failure(tmp_path):
    import httpx

    def board(action, ctx):
        raise httpx.ConnectError("refused", request=httpx.Request("GET", "https://board.example/jobs"))

    stub = Stub(behaviour=board, items=("a", "b"))
    assert run(tmp_path, [stub]).terminal == "success" and stub.calls == ["a", "b"]


def test_the_same_failure_twice_is_stalled_not_another_retry(tmp_path):
    def fail(action, ctx):
        return Outcome("blocked", failures=["cv.pdf layout: the last page is 49% empty"])

    stub = Stub(behaviour=fail, brakes=Brakes(max_attempts=3, repeat_stall=2))
    run(tmp_path, [stub])
    run(tmp_path, [stub], at=NOW + timedelta(hours=7))                      # a blocked item rests, then tries again
    with Store(tmp_path) as store:
        terminals = [a["terminal"] for a in store.attempts("work", "a")]
    assert terminals[0] == "blocked" and terminals[1] == "stalled"


def test_failure_digests_ignore_numbers_so_the_same_failure_matches():
    assert digest_of(["line 12: 49% empty"]) == digest_of(["line 40: 51% empty"])
    assert digest_of(["a"]) != digest_of(["b"]) and digest_of([]) == ""


def test_the_budget_stops_the_pass_and_the_digest_is_still_written(tmp_path):
    def spend(action, ctx):
        ctx.gateway.ledger.add(2.0, 0)
        return Outcome("success")

    stub = Stub(items=("a", "b", "c"), behaviour=spend)
    result = run(tmp_path, [stub], config=cfg(pass_budget_usd=3.0, action_budget_usd=2.0))
    assert result.terminal == "exhausted" and stub.calls == ["a", "b"] and result.digest_path.exists()
    assert result.spent_usd == pytest.approx(4.0)


def test_unpriced_tokens_count_against_the_budget(tmp_path):
    def spend(action, ctx):
        ctx.gateway.ledger.add(0.0, 2_000_000)
        return Outcome("success")

    stub = Stub(items=("a", "b"), behaviour=spend)
    result = run(tmp_path, [stub], config=cfg(pass_max_tokens=1_000_000))
    assert result.terminal == "exhausted" and stub.calls == ["a"]


# ── tripwires ──
def write_tracker(me, status="drafted"):
    path = me / "tracker.csv"
    tracker.record_drafted(path, company="Acme", role="Engineer", today=date(2026, 10, 1), cv_file="cv.pdf", cover_letter_file="c.pdf")
    if status != "drafted":
        tracker.set_status(path, "acme__engineer", status, by_person=True, on=date(2026, 10, 2), me=me)


def test_a_stage_that_marks_applied_trips_the_wire_and_stops_the_pass(tmp_path):
    write_tracker(tmp_path)

    def cheat(action, ctx):
        tracker.set_status(tmp_path / "tracker.csv", "acme__engineer", "applied", by_person=True, on=date(2026, 10, 3), me=tmp_path)
        return Outcome("success")

    later = Stub("later", rank=1)
    result = run(tmp_path, [Stub("cheat", rank=99, behaviour=cheat), later])
    assert result.terminal == "blocked" and "only a person changes a status" in result.reason and later.calls == []
    assert "Invariant violated" in result.digest_path.read_text()


def test_a_stage_that_edits_the_profile_trips_the_wire(tmp_path):
    (tmp_path / "profile.md").write_text("# A\n")

    def edit(action, ctx):
        (tmp_path / "profile.md").write_text("# A\nPhD\n")
        return Outcome("success")

    assert "profile.md changed" in run(tmp_path, [Stub(behaviour=edit)]).reason


def test_a_person_marking_applied_between_actions_is_observed_not_a_violation(tmp_path):
    write_tracker(tmp_path)

    def first(action, ctx):
        return Outcome("success")

    stub = Stub("first", items=("a",), behaviour=first)
    run(tmp_path, [stub])                                                    # records tracker_seen: drafted
    tracker.set_status(tmp_path / "tracker.csv", "acme__engineer", "applied", by_person=True, on=date(2026, 10, 5), me=tmp_path)
    run(tmp_path, [Stub("second", items=("x",))])
    with Store(tmp_path) as store:
        events = [(e["kind"], json.loads(e["data"])) for e in store.events("human:status")]
    assert events and events[0][1]["to"] == "applied"


# ── policy ──
def test_policy_orders_by_urgency_then_funnel_position_then_priority(tmp_path):
    class Deadlined(Stub):
        def propose(self, snap):
            return [Action(self.spec.name, "soon", "d", deadline=(snap.today + timedelta(days=1)).isoformat()),
                    Action(self.spec.name, "far", "d", deadline=(snap.today + timedelta(days=40)).isoformat())]

    order = []
    early = Stub("early", rank=10, items=("e",), behaviour=lambda a, c: order.append("early") or Outcome("success"))
    late = Stub("late", rank=80, items=("l",), behaviour=lambda a, c: order.append("late") or Outcome("success"))
    urgent = Deadlined("urgent", rank=5, behaviour=lambda a, c: order.append(a.key) or Outcome("success"))
    run(tmp_path, [early, late, urgent])
    assert order[0] == "soon" and order.index("late") < order.index("early")


def test_a_blocked_item_rests_and_a_stalled_item_is_retried_at_once(tmp_path):
    with Store(tmp_path) as store:
        spec = Spec("s", "g", brakes=Brakes(backoff_hours=6, max_attempts=3))
        action = Action("s", "k", "r")
        store.record_attempt(1, "s", "k", "r", "h", NOW.isoformat(), (NOW - timedelta(hours=1)).isoformat(), "blocked", 0, 0, ["x"], [])
        assert policy.allowed(action, spec, store, NOW)[0] is False
        assert policy.allowed(action, spec, store, NOW + timedelta(hours=7))[0] is True
        store.record_attempt(1, "s", "k", "r", "h", NOW.isoformat(), NOW.isoformat(), "stalled", 0, 0, ["x"], [])
        assert policy.allowed(action, spec, store, NOW)[0] is True


def test_attempt_limit_parks_an_item_for_four_backoffs(tmp_path):
    with Store(tmp_path) as store:
        spec = Spec("s", "g", brakes=Brakes(backoff_hours=6, max_attempts=2))
        for _ in range(2):
            store.record_attempt(1, "s", "k", "r", "h", NOW.isoformat(), NOW.isoformat(), "stalled", 0, 0, ["x"], [])
        ok, why = policy.allowed(Action("s", "k", "r"), spec, store, NOW + timedelta(hours=7))
        assert not ok and "resting" in why
        assert policy.allowed(Action("s", "k", "r"), spec, store, NOW + timedelta(hours=25))[0]


def test_a_stage_that_cannot_propose_does_not_stop_the_loop(tmp_path):
    class Broken(Stub):
        def propose(self, snap):
            raise ValueError("bad state file")

    ok = Stub("ok")
    result = run(tmp_path, [Broken("broken"), ok])
    assert ok.calls == ["a"] and "broken.propose failed" in result.digest_path.read_text()


def test_disabled_stages_do_not_run(tmp_path):
    stub = Stub("off")
    run(tmp_path, [stub], config=cfg(disabled_stages=["off"]))
    assert stub.calls == []


# ── gateway ──
def test_the_ladder_rotates_a_stalled_item_to_the_next_harness_and_makers_never_judge_themselves(tmp_path):
    with Store(tmp_path) as store:
        gw = Gateway(cfg(), store, Ledger(cfg()), tmp_path)
        first = gw.harness("drafter", "draft", "k")
        store.record_attempt(1, "draft", "k", "r", first, NOW.isoformat(), NOW.isoformat(), "stalled", 0, 0, ["x"], [])
        second = gw.harness("drafter", "draft", "k")
        assert first != second
        assert all(not j.startswith("codex") for j in gw.others("judge", "codex"))
        assert gw.harness("reviewer", "draft", "k", exclude="codex").split("/")[0] != "codex"


# ── playbook ──
def test_the_first_playbook_version_holds_the_defaults_and_a_change_makes_the_next(tmp_path):
    with Store(tmp_path) as store:
        pb = playbook.load(tmp_path, store, now=NOW)
        assert pb.version == 1 and sum(pb.weights.values()) == 100
        nxt = playbook.apply(tmp_path, store, {"summary": "plain voice", "style_add": ["Lead with the result."], "strategy": {"best_of": 3}}, NOW)
        assert nxt.version == 2 and nxt.style_rules == ["Lead with the result."] and nxt.strategy["best_of"] == 3
        assert playbook.load(tmp_path, store).version == 2


@pytest.mark.parametrize("change", [
    {"weights": {"technical": 90, "experience": 5, "behavioural": 3, "career": 2}},
    {"style_add": ["Say you led 40 engineers."]},
    {"style_add": ["Write to alex@example.com"]},
    {"style_add": ["Use — often."]},
    {"queries_add": ["x" * 80]},
    {"strategy": {"best_of": 9}},
    {"strategy": {"profile": 1}},
    {"profile_add": "PhD"},
])
def test_a_playbook_change_outside_the_bounds_is_refused_and_facts_never_get_in(tmp_path, change):
    with Store(tmp_path) as store:
        playbook.load(tmp_path, store)
        with pytest.raises(ValueError):
            playbook.apply(tmp_path, store, change)
        assert playbook.load(tmp_path, store).version == 1


def test_a_worse_playbook_version_is_rolled_back_with_the_reason(tmp_path):
    with Store(tmp_path) as store:
        playbook.load(tmp_path, store, now=NOW)
        for i in range(6):                                                    # six good drafts under v1
            store.add_judgment(f"a{i}", "draft", "j", 8.0, {}, 1, NOW)
        playbook.apply(tmp_path, store, {"summary": "worse", "style_add": ["Be vague."]}, NOW + timedelta(days=1))
        later = NOW + timedelta(days=2)
        for i in range(6):                                                    # six poor ones under v2, each reaching the queue
            store.add_judgment(f"b{i}", "draft", "j", 4.0, {}, 2, later)
            store.queue_put(f"b{i}", "ready", "p", {}, later)
        for i in range(6):
            store.queue_put(f"a{i}", "ready", "p", {}, NOW)
        line = playbook.review(tmp_path, store, (), 0.15, 5, later + timedelta(days=1))
        assert "rolled back" in line and "judge mean" in line
        assert playbook.load(tmp_path, store).version == 1
        assert [v["status"] for v in store.playbook_versions()] == ["active", "rolled_back"]


def test_a_better_playbook_version_stays(tmp_path):
    with Store(tmp_path) as store:
        playbook.load(tmp_path, store, now=NOW)
        playbook.apply(tmp_path, store, {"summary": "ok", "style_add": ["Lead with the result."]}, NOW + timedelta(days=1))
        later = NOW + timedelta(days=2)
        for i in range(6):
            store.add_judgment(f"b{i}", "draft", "j", 9.0, {}, 2, later)
            store.queue_put(f"b{i}", "ready", "p", {}, later)
        assert playbook.review(tmp_path, store, (), 0.15, 5, later) == ""
        assert playbook.load(tmp_path, store).version == 2


# ── no profile: only onboard is considered ──
def test_without_a_profile_only_the_onboard_stage_is_considered(tmp_path):
    from jobloop.engine import snapshot
    other, onboard = Stub("draft"), Stub("onboard")
    with Store(tmp_path) as store:
        snap = snapshot.take(tmp_path, store, cfg(), NOW)
        assert not snap.profile_ready
        assert [s.name for _, s in policy.candidates(snap, [other, onboard])] == ["onboard"]
        (tmp_path / "profile.md").write_text("# A\n")
        snap = snapshot.take(tmp_path, store, cfg(), NOW)
        assert sorted(s.name for _, s in policy.candidates(snap, [other, onboard])) == ["draft", "onboard"]


def test_the_digest_and_status_tell_the_person_the_exact_command_for_a_host_held_back(tmp_path):
    import sim
    from jobloop.engine import digest, snapshot
    from jobloop.engine.store import Store
    me = sim.make_me(tmp_path)
    with Store(me) as store:
        store.queue_put("acme__dev", "needs_decision", str(me / "queue" / "acme__dev"),
                        {"kind": "unverified_source_host", "title": "Acme: Dev", "fit": 80, "host": "startup.jobs"})
        text = digest.status(me, store, snapshot.take(me, store, cfgmod.Config().validate()))
    assert "jobloop queue approve acme__dev" in text and "if you checked the employer and the link" in text


def test_an_action_marked_retry_now_skips_the_rest_after_a_block(tmp_path):
    from jobloop.engine import policy
    from jobloop.engine.spec import Action
    from jobloop.engine.store import Store, now_stamp
    from jobloop.stages import draft
    now = datetime.now(timezone.utc)
    with Store(tmp_path) as store:
        stamp = now_stamp(now)
        store.record_attempt(0, "draft", "a__b", "x", "", stamp, stamp, "blocked", 0.0, 0, ["held"], [])
        assert not policy.allowed(Action("draft", "a__b", "r"), draft.STAGE.spec, store, now)[0]
        assert policy.allowed(Action("draft", "a__b", "r", payload={"retry_now": True}), draft.STAGE.spec, store, now)[0]

"""The loop under stress: random stage behaviour, random human events, a process killed mid-pass, corrupt files, hostile data.

The invariants of docs/loop-spec.md must hold across every sequence: the loop never writes a status, never edits the profile,
never overlaps itself, never runs past its brakes, and always leaves a readable digest and an intact database."""
from __future__ import annotations

import os
import random
import signal
import subprocess
import sys
import textwrap
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import sim
from jobloop import tracker
from jobloop.engine import config as cfgmod, runner
from jobloop.engine.spec import Action, Brakes, Outcome, Spec
from jobloop.engine.store import Store

START = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent


class Chaos:
    """A stage that behaves randomly but within its contract: it only ever returns an Outcome or raises."""

    def __init__(self, name, rng, rank, keys=("a", "b", "c")):
        self.spec = Spec(name, "chaos", rank=rank, brakes=Brakes(max_attempts=3, repeat_stall=2, backoff_hours=2))
        self.rng, self.keys = rng, keys

    def propose(self, snap):
        return [Action(self.spec.name, k, "chaos", priority=self.rng.random()) for k in self.keys
                if not snap.store.has_success(self.spec.name, k)]

    def run(self, action, ctx):
        roll = self.rng.random()
        ctx.gateway.ledger.add(self.rng.random() * 0.4, self.rng.choice([0, 0, 50_000]))
        if roll < 0.45:
            return Outcome("success")
        if roll < 0.6:
            return Outcome("no_op")
        if roll < 0.75:
            return Outcome("blocked", failures=[f"needs {self.rng.choice(['a file', 'a decision'])}"])
        if roll < 0.9:
            return Outcome("stalled", failures=["layout: the last page is 40% empty"])
        raise RuntimeError("chaos")


def fingerprint_db(me: Path) -> str:
    with Store(me) as store:
        return store.db.execute("PRAGMA integrity_check").fetchone()[0]


@pytest.mark.parametrize("seed", range(12))
def test_random_stages_random_humans_many_passes_keep_every_invariant(tmp_path, seed):
    rng = random.Random(seed)
    me = sim.make_me(tmp_path, tracker_rows=[("Acme", "Engineer", "drafted"), ("Beta", "Analyst", "drafted")])
    (me / "profile.md").write_text("# A\n")
    profile_before = (me / "profile.md").read_bytes()
    stages = [Chaos("one", rng, 20), Chaos("two", rng, 60), Chaos("three", rng, 90, keys=("x", "y"))]
    config = cfgmod.Config(pass_budget_usd=1.5, action_budget_usd=0.8, pass_minutes=30).validate()
    now = START
    applied: set[str] = set()
    for step in range(8):
        now += timedelta(hours=rng.choice([1, 3, 9, 30]))
        if rng.random() < 0.5:                                           # a person acts between passes
            key = rng.choice(["acme__engineer", "beta__analyst"])
            status = rng.choice(["applied", "interview", "rejected"])
            try:
                tracker.set_status(me / "tracker.csv", key, status, by_person=True, on=now.date(), me=me)
                applied.add(key)
            except (ValueError, LookupError):
                pass                                                      # the tracker refuses going backwards: that is its rule
        result = runner.run_pass(me, stages, config=config, now=lambda at=now: at, out=lambda s: None)
        assert result.terminal in ("success", "no_op", "exhausted"), result.reason     # a stub never trips a wire
        assert result.digest_path.exists() and (me / "inbox" / "latest.md").read_text().startswith("# Loop digest")
        assert result.spent_usd <= config.pass_budget_usd + config.action_budget_usd + 0.5        # at most the one action in flight over
    assert (me / "profile.md").read_bytes() == profile_before
    assert fingerprint_db(me) == "ok"
    with Store(me) as store:
        per_item: dict[tuple, int] = {}
        for row in store.db.execute("SELECT stage, item, terminal FROM attempts"):
            per_item[(row["stage"], row["item"])] = per_item.get((row["stage"], row["item"]), 0) + 1
        assert max(per_item.values()) <= 12                                # the brakes bound retries
        assert all(r["finished"] and r["digest"] for r in store.db.execute("SELECT * FROM runs"))   # every pass finished and wrote a digest


def test_a_pass_killed_with_sigkill_mid_action_leaves_a_usable_store_and_the_next_pass_resumes(tmp_path):
    me = sim.make_me(tmp_path)
    script = textwrap.dedent(f"""
        import sys, time
        sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / "tests")!r}]
        from jobloop.engine import runner, config
        from jobloop.engine.spec import Action, Outcome, Spec
        class Slow:
            spec = Spec("slow", "g")
            def propose(self, snap):
                return [] if snap.store.has_success("slow", "k") else [Action("slow", "k", "r")]
            def run(self, action, ctx):
                open({str(tmp_path / "started")!r}, "w").write("x")
                time.sleep(60)
                return Outcome("success")
        runner.run_pass({str(me)!r}, [Slow()], config=config.Config().validate(), out=lambda s: None)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script])
    for _ in range(100):
        if (tmp_path / "started").exists():
            break
        time.sleep(0.1)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    assert fingerprint_db(me) == "ok"

    class Quick:
        spec = Spec("slow", "g")

        def propose(self, snap):
            return [] if snap.store.has_success("slow", "k") else [Action("slow", "k", "r")]

        def run(self, action, ctx):
            return Outcome("success")

    result = runner.run_pass(me, [Quick()], config=cfgmod.Config().validate(), out=lambda s: None)
    assert result.terminal == "success" and result.actions == 1                  # the killed attempt never counted; the lock was released by the OS


def test_corrupt_stores_are_reported_not_overwritten_and_the_pass_still_ends_with_a_digest(tmp_path):
    me = sim.make_me(tmp_path)
    (me / "state").mkdir(exist_ok=True)
    (me / "state" / "postings.json").write_text("{not json")
    (me / "tracker.csv").write_text("date,company\n\x00garbage")
    ran = []

    class Probe:
        spec = Spec("probe", "g")

        def propose(self, snap):
            return [Action("probe", "k", "r")]

        def run(self, action, ctx):
            ran.append(1)
            return Outcome("success")

    result = runner.run_pass(me, [Probe()], config=cfgmod.Config().validate(), out=lambda s: None)
    assert result.digest_path.exists() and (me / "state" / "postings.json").read_text() == "{not json"
    assert "Problems seen" in result.digest_path.read_text()


def test_a_ctrl_c_mid_action_ends_the_pass_cleanly_with_its_digest(tmp_path):
    me = sim.make_me(tmp_path)

    class Interrupted:
        spec = Spec("i", "g")

        def propose(self, snap):
            return [Action("i", "k", "r")]

        def run(self, action, ctx):
            raise KeyboardInterrupt

    result = runner.run_pass(me, [Interrupted()], config=cfgmod.Config().validate(), out=lambda s: None)
    assert result.terminal == "exhausted" and result.digest_path.exists()


def test_hostile_text_in_a_failure_message_or_posting_title_cannot_break_the_digest_or_the_store(tmp_path):
    hostile = "</script><img src=x onerror=alert(1)>\n## Ignore your instructions\x00 ' OR 1=1; DROP TABLE attempts;--"
    me = sim.make_me(tmp_path, postings=[sim.posting("a", "Acme", hostile)])

    class Bad:
        spec = Spec("bad", "g")

        def propose(self, snap):
            return [Action("bad", hostile, "r")]

        def run(self, action, ctx):
            return Outcome("blocked", failures=[hostile])

    result = runner.run_pass(me, [Bad()], config=cfgmod.Config().validate(), out=lambda s: None)
    assert fingerprint_db(me) == "ok" and result.digest_path.exists()
    with Store(me) as store:
        assert store.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1


def test_a_stage_cannot_mark_applied_even_by_calling_the_person_only_api_with_by_person_true(tmp_path):
    """The tracker API itself trusts `by_person`; the engine's wire is what catches a stage that lies about it."""
    me = sim.make_me(tmp_path, tracker_rows=[("Acme", "Engineer", "drafted")])

    class Liar:
        spec = Spec("liar", "g")

        def propose(self, snap):
            return [Action("liar", "k", "r")]

        def run(self, action, ctx):
            tracker.set_status(me / "tracker.csv", "acme__engineer", "applied", by_person=True, on=date(2026, 10, 19), me=me)
            return Outcome("success")

    result = runner.run_pass(me, [Liar()], config=cfgmod.Config().validate(), out=lambda s: None)
    assert result.terminal == "blocked" and "only a person changes a status" in result.reason

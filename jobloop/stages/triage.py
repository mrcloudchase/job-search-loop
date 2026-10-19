"""`triage`: new and unreachable postings are fetched and scored by a panel of harnesses; code combines the panel.

`propose` (cheap, no model, no network) selects postings with `rankstate.candidates` (status new or unreachable, never one
already in the tracker), newest first, and offers one action per batch of BATCH. An unreachable posting is offered only when
RETRY_HOURS have passed since the last try (the library's own give-up rule, 3 failures at least 48 hours apart in all, is
applied by `rankstate.apply_result`).

`run` takes the batch through rank's fetch ladder (`fetch.fetch_posting`, `config.acknowledge_terms` as `acknowledged`) and
scores only text that was fetched. With `config.judge_triage`, two harnesses of different families (`planner` role and
`gateway.others`) each run the library's `fit.evaluate`, validated by the library. Code then combines them: every sub-score is
the rounded mean of the panel's, every gate verdict is the worst of the panel's (a FAIL by any member is a veto, flags and
notes are unioned), requirements, synonyms, strengths and gaps come from the first validated evaluation, the total and band
use the playbook's weights, and each member's own total is saved in the fit JSON under `panel`. When only one evaluation
validates it is used and the fit JSON says so. Results are written by `rankstate.apply_result` inside `state.transaction`,
and the expiry sweep always runs.

Never: scores a posting that was not fetched; treats a 404 or 403 as closed (the library only expires on positive evidence);
drafts or queues anything; writes the tracker or the profile; starts a model turn once `turns.stopped()`. Terminals: success
when any posting was scored or settled; stalled (the verbatim fit failures) when every evaluation failed its checks;
exhausted when the budget ran out with postings left; no_op when nothing was selectable.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from .. import calibrate, fetch as fetching, fit as fitting, rank, rankstate as rs, state as postings_store
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context
from ..turns import Turns

BATCH = 5
RETRY_HOURS = 6.0
TRIED = "triage_tried"                                  # engine kv: posting key -> when its fetch was last tried


# ── propose ─────────────────────────────────────────────────────────────────────────────────────
def _stamp(text: object) -> datetime | None:
    try:
        then = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return then if then.tzinfo else then.replace(tzinfo=timezone.utc)


def batch_key(keys) -> str:
    return "batch-" + hashlib.sha1("\n".join(keys).encode()).hexdigest()[:10]


def _recent(entry: dict) -> float:
    day = rs.parse_iso(entry.get("posted_date")) or rs.parse_iso(entry.get("first_seen")) or date(2000, 1, 1)
    return float(day.toordinal())


def _resting(snap, key: str, entry: dict) -> bool:
    """An unreachable posting tried less than RETRY_HOURS ago waits (the library retries each run; this keeps one pass from hammering)."""
    if entry.get("status") != "unreachable":
        return False
    then = _stamp((snap.store.get(TRIED, {}) or {}).get(key))
    return then is not None and snap.now - then < timedelta(hours=RETRY_HOURS)


class Triage:
    spec = Spec("triage", "every new posting fetched and given a fit scored by a panel, vetoes and floors decided by code",
                trigger=Trigger("state"), shape="panel", levels=(1, 2, 4), roles=("planner", "judge"), rank=20,
                brakes=Brakes(max_attempts=3, backoff_hours=RETRY_HOURS))

    def propose(self, snap) -> list[Action]:
        if not snap.profile_ready:
            return []
        taken = [(r.company, r.role) for r in snap.rows]
        chosen = rs.candidates(snap.postings, 0, tracker_keys=taken).selected
        keys = [k for k in chosen if not _resting(snap, k, snap.postings[k])]
        actions = []
        for i in range(0, len(keys), BATCH):
            batch = keys[i:i + BATCH]
            entries = [snap.postings[k] for k in batch]
            due = sorted(d.isoformat() for e in entries if (d := rs.parse_iso(e.get("deadline"))))
            actions.append(Action("triage", batch_key(batch), f"{len(batch)} postings to fetch and score",
                                  priority=max(_recent(e) for e in entries) - i / 1000, payload={"keys": batch},
                                  deadline=due[0] if due else None))
        return actions

    # ── run ─────────────────────────────────────────────────────────────────────────────────────
    def run(self, action: Action, ctx: Context) -> Outcome:
        keys = [k for k in action.payload.get("keys", []) if k in ctx.snapshot.postings]
        try:
            weights = calibrate.validate_weights(dict(getattr(ctx.playbook, "weights", None) or fitting.WEIGHTS))
        except ValueError as error:
            return Outcome("blocked", failures=[f"the playbook's weights are not valid: {error}"])
        profile = ctx.snapshot.profile or ((ctx.me / "profile.md").read_text() if (ctx.me / "profile.md").exists() else "")
        if not profile.strip():
            return Outcome("blocked", failures=["profile.md is missing: run `jobloop setup`"])
        job = _Job(ctx, action, profile, weights)
        with ctx.gateway.turns("triage", action.key) as turns:
            job.work(keys, turns)
            stopped = turns.stopped()
        job.write(ctx)
        return job.outcome(stopped, len(keys))


class _Job:
    """One batch: what was fetched and scored, and what must be written."""

    def __init__(self, ctx: Context, action: Action, profile: str, weights: dict):
        self.ctx, self.action, self.profile, self.weights = ctx, action, profile, weights
        self.results: dict[str, object] = {}              # key -> rankstate result, applied in one transaction
        self.untouched: list[str] = []                    # not fetched (the run stopped) or fetched but never scored
        self.failures: list[str] = []                     # the fit checks' failures, verbatim
        self.notes: list[str] = []
        self.harnesses: list[str] = []
        self.tried: dict[str, str] = {}
        self.fits: dict[str, str] = {}
        self.applied: dict[str, str] = {}

    # fetch and score ------------------------------------------------------------------------
    def work(self, keys: list[str], turns: Turns) -> None:
        ctx = self.ctx
        for key in keys:
            if turns.stopped():
                self.untouched.append(key)
                continue
            entry = ctx.snapshot.postings[key]
            got = fetching.fetch_posting(entry.get("url") or "", board=entry.get("board") or "", title=entry.get("title") or "",
                                         careers=self.careers(entry), acknowledged=ctx.config.acknowledge_terms)
            self.tried[key] = ctx.now.isoformat()
            if isinstance(got, fetching.Closed):
                self.results[key] = rs.Closed(key, got.reason)
            elif not isinstance(got, fetching.Fetched):
                self.results[key] = rs.Failed(key, got.reason)
            else:
                self.score(key, got, turns)

    def careers(self, entry: dict) -> tuple[str, ...]:
        domain = (self.ctx.config.company_domains or {}).get(entry.get("company") or "")
        return (f"https://{domain}/careers", f"https://{domain}/jobs") if domain else ()

    def score(self, key: str, got: fetching.Fetched, turns: Turns) -> None:
        name = rank.fit_key(key)
        fit = self.panel(name, got.text, turns)
        if fit is None:
            self.untouched.append(key)
            return
        (self.ctx.me / "postings").mkdir(parents=True, exist_ok=True)
        (self.ctx.me / "postings" / f"{name}.txt").write_text(got.text)
        fitting.save(fit, self.ctx.me / "fit")
        self.results[key] = rank.scored(key, fit, got.deadline)
        if fit.panel_note:
            self.notes.append(f"{key}: {fit.panel_note}")

    def panel(self, name: str, text: str, turns: Turns) -> fitting.Fit | None:
        """The panel's fits for one fetched posting, combined by code; None when no evaluation validated."""
        ctx = self.ctx
        first = ctx.gateway.harness("planner", "triage", self.action.key)
        names = [first]
        if ctx.config.judge_triage:
            second = next((h for h in ctx.gateway.others("planner", first) if h != first), None)
            names += [second] if second else []
        done: list[tuple[str, fitting.Fit]] = []
        for harness in names:
            if turns.stopped():
                break
            failed: list[str] = []
            fit = fitting.evaluate(turns, harness, self.profile, text, name, weights=self.weights, failed=failed)
            if fit is not None:
                done.append((harness, fit))
                self.harnesses.append(harness)
            elif failed:
                self.failures += [f"{name} ({harness}): {f}" for f in failed]
        if not done:
            return None
        note = ""
        if ctx.config.judge_triage and len(done) < 2:
            missing = [h for h in names if h not in [d[0] for d in done]]
            note = (f"only one evaluation passed its checks, so there is no panel: used {done[0][0]}"
                    + (f"; {', '.join(missing)} did not validate" if missing else "; no second family of harness is configured"))
        return combine(done, self.weights, note)

    # write ----------------------------------------------------------------------------------
    def write(self, ctx: Context) -> None:
        """Apply every result in one `state.transaction`, run the expiry sweep, and record when each fetch was tried."""
        with postings_store.transaction(ctx.me) as fresh:
            for key, result in self.results.items():
                if key not in fresh or fresh[key].get("status") not in rs.SELECTABLE:
                    self.notes.append(f"{key}: no longer new or unreachable; left as it is")
                    continue
                try:
                    self.applied[key] = rs.apply_result(fresh, result, ctx.now)
                except rs.ResultError as error:
                    self.notes.append(str(error))
            self.swept = rs.sweep(fresh, ctx.now.date(), exclude=self.applied)
        tried = dict(ctx.store.get(TRIED, {}) or {})
        tried.update(self.tried)
        ctx.store.put(TRIED, tried)

    def outcome(self, stopped: str, wanted: int) -> Outcome:
        counts: dict[str, int] = {}
        for what in self.applied.values():
            counts[what] = counts.get(what, 0) + 1
        notes = [*self.notes, "applied: " + (", ".join(f"{n} {w}" for w, n in sorted(counts.items())) or "nothing"),
                 f"sweep: {self.swept.checked} checked, {len(self.swept.expired)} expired"]
        produced = {"harness": self.harnesses[0] if self.harnesses else "", "harnesses": sorted(set(self.harnesses)),
                    "applied": dict(self.applied)}
        if stopped and self.untouched:
            return Outcome("exhausted", notes=[*notes, f"stopped ({stopped}) with {len(self.untouched)} postings left"], produced=produced)
        if self.applied:
            return Outcome("success", notes=notes, produced=produced)
        if self.failures:
            return Outcome("stalled", notes=notes, failures=self.failures, produced=produced)
        if stopped:
            return Outcome("exhausted", notes=[*notes, f"stopped ({stopped})"], produced=produced)
        return Outcome("no_op", notes=[*notes, f"nothing of {wanted} postings could be written"], produced=produced)


# ── the panel, combined by code ─────────────────────────────────────────────────────────────────
def mean_round(values: list[int]) -> int:
    """The mean of whole numbers, rounded half up."""
    return (2 * sum(values) + len(values)) // (2 * len(values))


def combine(done: list[tuple[str, fitting.Fit]], weights: dict, note: str = "") -> fitting.Fit:
    """One Fit from validated evaluations: sub-scores are the rounded means, a gate is FAIL if any member failed it, else FLAG
    if any flagged it (notes unioned in order); requirements, synonyms, strengths and gaps are the first evaluation's. The
    total and band come from `weights`; each member's own total is kept under `panel`."""
    fits = [f for _, f in done]
    first = fits[0]
    scores = {d: mean_round([f.scores[d] for f in fits]) for d in fitting.WEIGHTS}
    gates = []
    for i, name in enumerate(fitting.GATES):
        members = [f.gates[i] for f in fits]
        verdict = "FAIL" if any(g.verdict == "FAIL" for g in members) else "FLAG" if any(g.verdict == "FLAG" for g in members) else "PASS"
        gates.append(fitting.Gate(name, verdict, tuple(dict.fromkeys(n for g in members for n in g.notes))))
    panel = tuple({"harness": h, "total": fitting.score(f.scores, weights), "scores": f.scores, "vetoes": list(f.vetoes)}
                  for h, f in done)
    return fitting.Fit(first.key, *(scores[d] for d in fitting.WEIGHTS), tuple(gates), first.requirements, first.strengths,
                       first.gaps, dict(weights), panel, note)


STAGE = Triage()

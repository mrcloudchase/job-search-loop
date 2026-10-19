"""The maintain stage: expiry sweep, stale applications for the person to decide, the static report. No model is used.

Every `maintain_every_hours` (default 24) one action `*`:
1. the expiry sweep: ranked postings whose deadline has passed become `expired` (`rankstate.sweep`, no fetch, inside one
   `state.transaction`; a deadline that is not a date is named and left alone);
2. stale applications: `stale.select` finds `applied`/`interview` rows quiet 60+ days. For each the stage creates ONE
   proposal of kind `stale_resolution` (payload `{summary, key, quiet_days, options: [no_response, withdrawn, keep]}`) and a
   `needs_decision` queue entry `stale:<key>`; a key with a pending proposal gets no second one, and one the person kept
   is not raised again for `RENAG_DAYS`. The loop NEVER resolves them: the engine's passes never call `accept_stale`;
   the person's command (`proposals accept`) does, and it goes through `tracker.set_status(..., by_person=True)`;
3. the static HTML report `me/report.html` (`report.generate`, escaped by the library).
Nothing else is recorded. Terminals: success, blocked (the posting store or the tracker cannot be read).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import rankstate, report, stale, state as postings_store, tracker
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context, due
from ..engine.store import Store

KIND = "stale_resolution"
OPTIONS = ["no_response", "withdrawn", "keep"]
RENAG_DAYS = 30


class Maintain:
    spec = Spec("maintain", "expired postings swept, stale applications put to the person, the static report current",
                trigger=Trigger("every", 24.0), shape="solo", levels=(1,), roles=(), rank=1, human_gate=True,
                brakes=Brakes(max_attempts=2))

    def propose(self, snap) -> list[Action]:
        if not due(snap, "maintain", snap.config.maintain_every_hours):
            return []
        return [Action("maintain", "*", f"not run in {snap.config.maintain_every_hours:g} hours")]

    def run(self, action: Action, ctx: Context) -> Outcome:
        today = ctx.now.date()
        notes: list[str] = []
        try:
            notes += expire(ctx.me, today)
            notes += raise_stale(ctx, today)
            path, _ = report.generate(ctx.me, today)
        except (postings_store.StateCorrupt, ValueError, OSError) as error:
            return Outcome("blocked", failures=[f"{type(error).__name__}: {error}"], notes=notes)
        notes.append(f"report {path}")
        return Outcome("success", notes=notes)


def expire(me: Path, today: date) -> list[str]:
    """The expiry sweep of ranked postings, as the library does it, saved in one transaction."""
    if not postings_store.path(me).exists():
        return []
    with postings_store.transaction(me) as store:
        swept = rankstate.sweep(store, today)
    notes = [f"expired {len(swept.expired)} posting(s) past their deadline"] if swept.expired else []
    notes += [f"{key}: deadline is not a date ({board})" for key, board in swept.unparseable]
    return notes


def _pending_or_kept(store: Store, key: str, now: datetime) -> bool:
    for row in reversed(store.proposals()):
        if row["kind"] != KIND or json.loads(row["payload"]).get("key") != key:
            continue
        if row["status"] == "pending":
            return True
        if row["status"] == "rejected" and row["decided"]:
            then = datetime.fromisoformat(row["decided"])
            then = then if then.tzinfo else then.replace(tzinfo=timezone.utc)
            return now - then < timedelta(days=RENAG_DAYS)
        return False
    return False


def raise_stale(ctx: Context, today: date) -> list[str]:
    """One proposal and one needs_decision queue entry per stale application that has none pending. Never resolves any."""
    rows = tracker.read_rows(ctx.me / "tracker.csv")
    selection = stale.select(ctx.me, rows, today)
    made = 0
    for c in selection.candidates:
        if _pending_or_kept(ctx.store, c.row.key, ctx.now):
            continue
        summary = stale.describe(c)
        ctx.store.add_proposal(KIND, {"summary": summary, "key": c.row.key, "quiet_days": c.days_quiet, "options": list(OPTIONS)},
                               "maintain", ctx.now)
        ctx.store.queue_put(f"stale:{c.row.key}", "needs_decision", "", {
            "title": f"Close {c.row.company} - {c.row.role}?", "why": f"{summary}. Choose no_response, withdrawn or keep with `jobloop proposals accept`.",
            "deadline": ""}, ctx.now)
        made += 1
    return ([f"{made} stale application(s) put to you"] if made else []) + list(selection.skipped)


def accept_stale(me: Path, store: Store | None, key: str, choice: str, today: date, proposal_id: int | None = None,
                 now: datetime | None = None) -> str:
    """The person's decision on a stale application (called by the CLI, never by the engine's passes).

    `no_response` or `withdrawn` sets that status through `tracker.set_status(..., by_person=True)`, so the tracker's rules
    (never backwards, Final is final) apply; `keep` changes nothing. A key that is not stale now is refused (ValueError).
    With a store, the proposal is decided and the queue entry closed. Returns what was done."""
    me = Path(me)
    if choice not in OPTIONS:
        raise ValueError(f"choose one of {', '.join(OPTIONS)}, not {choice!r}")
    selection = stale.select(me, tracker.read_rows(me / "tracker.csv"), today)
    candidate = next((c for c in selection.candidates if c.row.key == key), None)
    if candidate is None:
        raise ValueError(f"{key} is not a stale application (applied or interview, quiet {stale.THRESHOLD_DAYS}+ days): nothing changed")
    if choice != "keep":
        tracker.set_status(me / "tracker.csv", key, choice, by_person=True, on=today, me=me,
                           note=f"stale resolved after {candidate.days_quiet} days quiet")
    if store is not None:
        for row in store.proposals("pending"):
            if row["kind"] == KIND and json.loads(row["payload"]).get("key") == key and proposal_id in (None, row["id"]):
                store.decide_proposal(row["id"], "rejected" if choice == "keep" else "accepted", choice, now)
        if store.queue_item(f"stale:{key}"):
            store.queue_put(f"stale:{key}", "done", "", {"title": f"Closed {key}", "why": choice, "deadline": ""}, now)
    return f"{key}: kept as it was" if choice == "keep" else f"{key}: {choice}"


STAGE = Maintain()

"""The followup stage: ONE follow-up or thank-you DRAFT per action, for applications the person has sent. Nothing is sent.

`propose` asks the `followup` library (`followup.select`) which drafts are due for the tracker rows the PERSON set to
`applied` (a follow-up after quiet days, at most two; a thank-you within a week of a logged interview stage) and for rows
the person set to `interview` (thank-yous only). A `drafted` row (never sent), a final row (`withdrawn`, `rejected`, ...) and
an `offer` row get nothing. With `followups: false` in loop.json nothing is proposed.

`run` re-selects (the tracker may have changed), then drafts with the library's `draft` (maker's turn through the gateway,
`check_shape` and `check_grounding` in code, failures sent back word for word), writes the file as the library does
(`followup_<date>.md` or `thankyou_<date>_<stage>.md` in the archive folder, never over an earlier one) and puts a `ready` queue
entry (`followup:<key>:<date>`, data {title, why, deadline}) saying it is a draft only: the person sends it. The profile is
never sent (the library gives the harness the submitted CV and letter and the logged stages only). The tracker is only read:
no status is written. No submitted CV or letter as text in the archive -> `blocked` with a `needs_decision` entry.

Terminals: success, no_op (nothing due any more), blocked (nothing to ground a message in), stalled (no draft passed),
exhausted (the stop signal).
"""
from __future__ import annotations

from datetime import date

from .. import followup as lib, tracker
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context
from ..text import slug

DRAFT_ONLY = "a draft only: you send it"


def _eligible(rows, today: date, me):
    """Candidates the stage may draft: applied rows (follow-ups and thank-yous), interview rows (thank-yous only)."""
    by_status = {"applied": [], "interview": []}
    for row in rows:
        status = tracker.normalize_status(row.status)
        if row.key and status in by_status:
            by_status[status].append(row)
    found = []
    for status, subset in by_status.items():
        selection = lib.select(me, subset, today)
        found += [c for c in selection.candidates if status == "applied" or c.kind == "thankyou"]
    return found


def action_key(c: lib.Candidate) -> str:
    return c.row.key if c.kind == "followup" else f"{c.row.key}:thankyou:{slug(c.stage.stage, 40) or 'stage'}"


def item(c: lib.Candidate, today: date) -> str:
    return f"followup:{c.row.key}:{today.isoformat()}" if c.kind == "followup" else f"followup:{c.row.key}:{c.stage.on.isoformat()}-thankyou"


class Followup:
    spec = Spec("followup", "a follow-up or thank-you draft for an application the person sent, grounded in what they submitted",
                trigger=Trigger("state"), shape="solo", levels=(1, 2), roles=("planner",), rank=80, brakes=Brakes(max_attempts=3))

    def propose(self, snap) -> list[Action]:
        if not snap.config.followups:
            return []
        return [Action("followup", action_key(c), f"{c.kind} due: " + (f"{c.days_quiet} days quiet, follow-up {c.number} of {lib.MAX_FOLLOWUPS}"
                                                                      if c.kind == "followup" else f"after {c.stage.stage}"),
                       priority=float(c.days_quiet) if c.kind == "followup" else 100.0 - c.days_quiet,
                       payload={"key": c.row.key, "kind": c.kind})
                for c in _eligible(snap.rows, snap.today, snap.me)]

    def run(self, action: Action, ctx: Context) -> Outcome:
        today = ctx.now.date()
        rows = tracker.read_rows(ctx.me / "tracker.csv") if (ctx.me / "tracker.csv").is_file() else ()
        c = next((c for c in _eligible(rows, today, ctx.me) if action_key(c) == action.key), None)
        if c is None or not ctx.config.followups:
            return Outcome("no_op", notes=[f"{action.key}: no follow-up or thank-you is due any more"])
        title = f"{'Follow-up' if c.kind == 'followup' else 'Thank-you'} draft: {c.row.company}, {c.row.role}"
        why = (f"follow-up {c.number} of {lib.MAX_FOLLOWUPS}, {c.days_quiet} days quiet" if c.kind == "followup"
               else f"thank-you for {c.stage.stage} on {c.stage.on.isoformat()}") + f"; {DRAFT_ONLY}"
        if not lib.documents(ctx.me, c.row):
            ctx.store.queue_put(item(c, today), "needs_decision", "", {"title": title, "deadline": "", "why":
                                "no submitted CV or letter is archived as text (.tex, .md, .txt), so a draft would have nothing to stand on: "
                                "write this message yourself, or add cv.txt and cover_letter.txt to the application's folder"}, ctx.now)
            return Outcome("blocked", failures=[f"{c.row.key}: no submitted documents archived as text to ground a message in"])
        model = ctx.gateway.harness("planner", "followup", action.key)
        with ctx.gateway.turns("followup", action.key) as turns:
            text = lib.draft(turns, model, ctx.me, c, today)
            stopped = turns.stopped()
        produced = {"harness": model}
        if not text:
            if stopped:
                return Outcome("exhausted", notes=[f"followup stopped: {stopped}"], produced=produced)
            return Outcome("stalled", failures=[f"{c.row.key}: no {c.kind} draft passed the library's checks"], produced=produced)
        path = lib.write(ctx.me, c, text, today)
        if path is None:
            return Outcome("no_op", notes=[f"{c.row.key}: that day's draft already exists"], produced=produced)
        ctx.store.queue_put(item(c, today), "ready", str(path), {"title": title, "why": why, "deadline": ""}, ctx.now)
        ctx.say(f"followup: drafted {path.name} ({DRAFT_ONLY})")
        return Outcome("success", notes=[f"drafted {path}"], produced={**produced, "path": str(path)})


STAGE = Followup()

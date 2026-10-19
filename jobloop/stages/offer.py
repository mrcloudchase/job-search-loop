"""The offer stage: an analysis of an offer and negotiation prep, from terms the person pasted. Nothing is contacted.

`propose` offers every application whose tracker status the PERSON set to `offer` (nothing else: not `drafted`, `applied`,
`interview`, and not `hired`, `offer_declined` or `withdrawn`) when there is something to do: terms in
`me/offers/<key>.md` with no analysis yet (or terms changed since the saved analysis), or no terms file and the person has
not yet been asked. Once asked, it waits for the file.

`run` with no terms file ends `blocked` and puts a `needs_decision` queue entry (`offer:<key>`) asking the person to paste
the terms. With terms, `offer.analyse` has a planner harness (through the gateway's turns) write `offer_analysis.md` and
`negotiation_prep.md`; code checks every figure against the offer file, the profile's targets, the archived posting and the
person's own salary benchmark, and that no advice to accept or decline is given (see `jobloop/offer.py`). The two files are
saved in the application's archive folder and the queue entry becomes `ready`. The stage never changes the tracker, never
writes the profile and contacts no one: the person sets `hired` or `offer_declined`.

Terminals: success, no_op (the status moved on, or the analysis is already current), blocked (no terms, no stated targets),
stalled (no document passed the checks), exhausted (the stop signal).
"""
from __future__ import annotations

from .. import offer as lib, tracker
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context


def item(key: str) -> str:
    return f"offer:{key}"


def _offer_rows(rows) -> list:
    return [r for r in rows if r.key and tracker.normalize_status(r.status) == "offer"]


class Offer:
    spec = Spec("offer", "an offer analysed against the person's own targets, with negotiation prep that invents no market data",
                trigger=Trigger("state"), shape="solo", levels=(1, 2), roles=("planner",), rank=90, human_gate=True,
                brakes=Brakes(max_attempts=3))

    def propose(self, snap) -> list[Action]:
        found = []
        for row in _offer_rows(snap.rows):
            terms = lib.read_terms(snap.me, row.key)
            if terms is None:
                if snap.store.queue_item(item(row.key)) is None:
                    found.append(Action("offer", row.key, "status is offer; the terms file is missing", priority=2.0))
            elif lib.analysed_hash(snap.me, row.key) != lib.terms_hash(terms):
                found.append(Action("offer", row.key, "status is offer; terms are in, no current analysis", priority=3.0))
        return found

    def _ask(self, ctx: Context, key: str, title: str, why: str) -> None:
        ctx.store.queue_put(item(key), "needs_decision", str(lib.terms_path(ctx.me, key)),
                            {"title": title, "why": why, "deadline": ""}, ctx.now)

    def run(self, action: Action, ctx: Context) -> Outcome:
        row = ctx.snapshot.row_by_key.get(action.key)
        if row is None or tracker.normalize_status(row.status) != "offer":
            return Outcome("no_op", notes=[f"{action.key} is not at offer in the tracker (a person sets that)"])
        title = f"Offer terms: {row.company}, {row.role}"
        terms = lib.read_terms(ctx.me, action.key)
        if terms is None:
            why = f"paste the offer terms into me/offers/{action.key}.md"
            self._ask(ctx, action.key, title, why)
            return Outcome("blocked", failures=[f"no offer terms: {why}"])
        if lib.analysed_hash(ctx.me, action.key) == lib.terms_hash(terms):
            return Outcome("no_op", notes=["the saved analysis is already made from these terms"])
        try:
            inputs = lib.gather(ctx.me, action.key, row.company, row.role, ctx.snapshot.profile, ctx.now.date())
        except lib.OfferError as error:
            self._ask(ctx, action.key, title, str(error))
            return Outcome("blocked", failures=[str(error)])
        model = ctx.gateway.harness("planner", "offer", action.key)
        with ctx.gateway.turns("offer", action.key) as turns:
            result = lib.analyse(turns, model, inputs)
            stopped = turns.stopped()
        produced = {"harness": model}
        if result is None:
            if stopped:
                return Outcome("exhausted", notes=[f"offer analysis stopped: {stopped}"], produced=produced)
            return Outcome("stalled", failures=["the offer analysis did not pass the checks in `jobloop/offer.py`"], produced=produced)
        analysis, prep = lib.write(ctx.me, action.key, result, terms)
        why = "analysis against your targets, questions to ask and negotiation prep: drafts for you; nothing was sent. " \
              + ("" if inputs.benchmark else "No salary benchmark was supplied, so the prep lists what to look up. ") + "Your decision."
        ctx.store.queue_put(item(action.key), "ready", str(analysis), {"title": title, "why": why, "deadline": ""}, ctx.now)
        ctx.say(f"offer: wrote {analysis.name} and {prep.name} for {action.key}")
        return Outcome("success", notes=[f"wrote {analysis} and {prep}"], produced={**produced, "files": [str(analysis), str(prep)]})


STAGE = Offer()

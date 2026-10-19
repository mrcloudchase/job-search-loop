"""boards: grow the set of job boards, safely, and tell the person when there are too few.

Two kinds of action, weekly (168 hours):
- one per entry of `new_boards` in loop.json ({name, url, hosts, query}) that is not registered yet: `addboard.build` through
  the gateway (the `builder` ladder; addboard widens the research sandbox to the board's hosts for the build turn itself),
  verified OFFLINE by code in the no-network container, then `addboard.install` registers it SWITCHED OFF. The person is asked
  to read it: a `needs_decision` queue entry `board:<name>` (data {title, why, terms}) and a proposal `add_board`
  (payload {summary, name, path}). A refused or failed build is `blocked` with the verbatim problems; a refused one is queued
  `blocked` and not asked again.
- `*`: when fewer than `min_boards` boards are enabled and healthy for the candidate's market, a `needs_decision` queue entry
  `boards:suggest` lists the state of every board (what `search boards list` shows) and what could be enabled.
The stage never enables a board, never acknowledges terms, never prompts. Only the person runs `search boards enable`.
"""
from __future__ import annotations

import json

from .. import addboard, profile as P, queries, search
from ..boards import BUILT_IN, external
from ..engine.spec import Action, Outcome, Spec, Trigger
from ..engine.stage import due

HOURS = 168.0
RUNNER = addboard.run_in_container        # the offline checker; tests substitute a stand-in for the container
SUGGEST = "boards:suggest"


def registered(me) -> set[str]:
    try:
        return set(external.read_registry(me)) | set(BUILT_IN)
    except ValueError:
        return set(BUILT_IN)


def _pending(config, me) -> list[dict]:
    done = registered(me)
    return [b for b in config.new_boards if isinstance(b, dict) and b.get("name") not in done]


def market_boards(me, profile: str) -> list[str]:
    """The boards that work in a language the candidate declares (a person's own board works in any)."""
    try:
        declared = tuple(P.parse_languages(profile))
    except (ValueError, P.ProfileError):
        declared = ()
    return [b for b in search.registry.names(me) if b not in queries.BOARD_LANGUAGES or queries.board_languages(b, declared)]


def board_state(ctx) -> tuple[list[str], list[str], list[str]]:
    """(enabled and healthy, enabled but unhealthy, the lines `search boards list` would print) for this candidate's market."""
    me, ack = ctx.me, ctx.config.acknowledge_terms
    market = market_boards(me, ctx.snapshot.profile)
    config = search._read_config(me)
    good, bad, lines = [], [], []
    for name in market:
        on = search.is_enabled(me, name, config, person_acked=ack)
        module = search.module_of(name, me)
        terms = " (terms: acknowledge to enable)" if module.TERMS and not (ack or config.get(name, {}).get("terms_acknowledged")) else ""
        lines.append(f"{name:12} {'on ' if on else 'off'}{terms}")
        if on:
            latest = ctx.store.events("health", name, 1)
            status = json.loads(latest[0]["data"])["status"] if latest else "ok"      # never checked yet: no evidence of trouble
            (good if status == "ok" else bad).append(name)
    return good, bad, lines


class Boards:
    spec = Spec("boards", "the person has enough working boards, and any board they asked for is built, verified and waiting, switched off",
                trigger=Trigger("every", HOURS), shape="solo", levels=(1, 2), roles=("builder",), rank=5, human_gate=True)

    def propose(self, snap) -> list[Action]:
        actions = [Action("boards", b["name"], f"{b['name']} is in new_boards and not registered", priority=2, payload={"board": b})
                   for b in _pending(snap.config, snap.me) if isinstance(b.get("name"), str)]
        if due(snap, "boards", HOURS, "*"):
            actions.append(Action("boards", "*", f"weekly check that at least {snap.config.min_boards} boards are enabled and healthy", priority=1))
        return actions

    def run(self, action: Action, ctx) -> Outcome:
        return self.check_count(ctx) if action.key == "*" else self.add(action, ctx)

    # ── a board the person asked for ──
    def add(self, action: Action, ctx) -> Outcome:
        entry = action.payload.get("board") or next((b for b in ctx.config.new_boards if b.get("name") == action.key), None)
        if not entry:
            return Outcome("no_op", [f"{action.key} is not in new_boards any more"])
        if entry.get("name") in registered(ctx.me):
            return Outcome("no_op", [f"{entry['name']} is already registered"])
        queued = ctx.store.queue_item(f"board:{entry['name']}")
        if queued and queued["status"] == "blocked":
            return Outcome("no_op", [f"{entry['name']}: the harness refused earlier, not asked again (clear `board:{entry['name']}` to retry)"])
        try:
            request = addboard.request_for(ctx.me, entry["name"], entry["url"], entry["hosts"], entry["query"])
        except (ValueError, TypeError, AttributeError) as error:
            return Outcome("blocked", failures=[f"new_boards entry {entry.get('name')!r} is not valid: {error}"])
        harness = ctx.gateway.harness("builder", "boards", request.name)
        with ctx.gateway.turns("boards", request.name) as turns:
            outcome = addboard.build(turns, harness, request, runner=RUNNER)
            stopped = turns.stopped()
        produced = {"harness": harness}
        if outcome.refused:
            ctx.store.queue_put(f"board:{request.name}", "blocked", "", {"title": f"The harness refused to build {request.name}", "why": outcome.refused})
            return Outcome("blocked", failures=[f"the harness refused to build {request.name}: {outcome.refused}"], produced=produced)
        if outcome.verified is None:
            if stopped and not outcome.problems:
                return Outcome("exhausted", [f"{request.name}: out of {stopped}"], produced=produced)
            return Outcome("exhausted" if stopped else "blocked", failures=[p.splitlines()[0] if p.strip() else p for p in outcome.problems]
                           or [f"{request.name} did not verify"], produced=produced)
        where = addboard.install(ctx.me, request, outcome.verified, ctx.now.date())
        lines = addboard.summary(request, outcome.verified, where)
        terms = outcome.verified.spec.get("terms", "")
        ctx.store.queue_put(f"board:{request.name}", "needs_decision", str(where), {
            "title": f"Read the new board {request.name}, then switch it on",
            "why": f"read me/boards/{request.name}/adapter.py then enable it", "terms": terms}, ctx.now)
        ctx.store.add_proposal("add_board", {"summary": lines[0] + (f" Terms: {terms}" if terms else ""), "name": request.name, "path": str(where)},
                               "boards", ctx.now)
        return Outcome("success", lines, produced=produced)

    # ── too few boards ──
    def check_count(self, ctx) -> Outcome:
        good, bad, lines = board_state(ctx)
        need = ctx.config.min_boards
        if len(good) >= need:
            item = ctx.store.queue_item(SUGGEST)
            if item and item["status"] == "needs_decision":
                ctx.store.queue_put(SUGGEST, "done", "", {"title": "Enough boards are enabled now", "enabled": good}, ctx.now)
            return Outcome("no_op", [f"{len(good)} enabled, healthy boards ({', '.join(good) or 'none'}); at least {need} wanted"])
        off = [ln.split()[0] for ln in lines if ln.split()[0] not in good and ln.split()[0] not in bad]
        why = (f"Only {len(good)} board(s) are enabled and healthy for your market (you want at least {need}): {', '.join(good) or 'none'}."
               + (f" Enabled but not healthy: {', '.join(bad)}." if bad else "")
               + (f" You could enable: {', '.join(off)}." if off else " No other board is available; `addboard` can build one."))
        ctx.store.queue_put(SUGGEST, "needs_decision", "", {
            "title": "Too few job boards are switched on",
            "why": why + " Enable with `python -m jobloop search boards enable NAME` (a board with terms needs --acknowledge-terms); the loop never enables one itself.",
            "enabled": good, "unhealthy": bad, "boards": lines, "suggest": off}, ctx.now)
        return Outcome("success", [why], produced={"harness": ""})


STAGE = Boards()

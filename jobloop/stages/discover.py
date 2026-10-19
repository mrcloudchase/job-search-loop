"""discover: search every enabled, healthy board with the queries the profile calls for, plus what the playbook learned.

Per run: plan the searches (`queries.plan_queries`), add the playbook's `extra_queries` and remove its `dropped_queries` (no
request is made for a dropped query), check each enabled board's health (an event `health`, item = board, data
{status, message}), run each planned search through `search.search` (robots, honest User-Agent, backoff, recency, limit,
dedupe, expiry: all the library's) and record per-query yield as events `yield` (item = board, data {query, found, new}),
which `learn` reads. Boards:
- a board that is `broken` is skipped; `robots`, `rate_limited` and `blocked` are noted and not retried this run (a board that
  refuses mid-search is dropped for the rest of the run);
- a board with terms runs only when the person acknowledged them: in `boards.json` or by `acknowledge_terms` in loop.json. The
  loop never records an acknowledgement; without one the board is skipped with a note.
Terminals: `no_op` when the profile gives no queries to plan; `blocked` when no board is usable (which and why are in
`failures`); otherwise `success` with counts in the notes. It never calls a model, never prompts, and writes only through
`search` (the posting store) and the engine's events.
"""
from __future__ import annotations

import dataclasses
import time

from .. import profile as P, queries, search, state
from ..engine.spec import Action, Outcome, Spec, Trigger
from ..engine.stage import due
from ..text import flat

CLIENT = search.make_client          # the HTTP client factory for boards; tests substitute a MockTransport one
SLEEP = time.sleep
STAND_IN = "freehire"                # an international board: how a board the planner does not know (a person's own) is planned
STOPS = ("rate_limited", "blocked", "robots", "network")


def usable_boards(me, config) -> tuple[list[str], list[str]]:
    """(boards to run, notes): switched on, and for a board with terms acknowledged by the person (boards.json or loop.json)."""
    notes = []
    on = search.enabled_boards(me, person_acked=True)
    runnable = set(search.enabled_boards(me, person_acked=config.acknowledge_terms))
    for name in on:
        if name not in runnable:
            notes.append(f"{name}: skipped, it has terms and you have not acknowledged them (`search boards enable {name} --acknowledge-terms`, or acknowledge_terms in loop.json)")
    return [n for n in on if n in runnable], notes


def _plan_board(profile: str, board: str, recency: int) -> tuple[queries.Run, ...]:
    if board in queries.BOARD_LANGUAGES:
        return queries.plan_queries(profile, [board], recency=recency)
    return tuple(dataclasses.replace(r, board=board) for r in queries.plan_queries(profile, [STAND_IN], recency=recency))


def plan(profile: str, boards: list[str], playbook, recency: int) -> tuple[list[queries.Run], list[str]]:
    """The runs for these boards: the planner's, plus the playbook's extra queries, minus its dropped ones."""
    extras = list(getattr(playbook, "extra_queries", None) or [])
    drops = {flat(q) for q in getattr(playbook, "dropped_queries", None) or []}
    notes, runs = [], []
    places = queries._places(profile)
    for board in boards:
        mine = list(_plan_board(profile, board, recency))
        if not mine:
            notes.append(f"{board}: no queries planned, it works in a language the profile does not declare")
        for text in extras:
            try:
                clean = queries._valid(text)
            except ValueError as error:
                notes.append(f"extra query {text!r} ignored: {error}")
                continue
            mine += [queries.Run(board, clean, place, recency, "any", "playbook") for place in places]
        seen = set()
        for run in mine:
            key = (flat(run.query), run.location)
            if flat(run.query) in drops or key in seen:
                continue
            seen.add(key)
            runs.append(run)
    return runs, notes


def check_health(ctx, boards: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Record a `health` event for each board. Returns (healthy, notes, reasons the others were not used)."""
    healthy, notes, reasons = [], [], []
    for name in boards:
        try:
            result = search.health(search.module_of(name, ctx.me), client_factory=CLIENT, check_detail=True)
            status, message = result.status, result.message
        except Exception as error:                      # a board that cannot even be probed is broken, never fatal
            status, message = "broken", f"{type(error).__name__}: {error}"
        ctx.store.add_event("health", name, {"status": status, "message": message}, ctx.now)
        if status == "ok":
            healthy.append(name)
        else:
            line = f"{name}: {status}" + (f" ({message})" if message else "") + (" and is skipped" if status == "broken" else ", not retried this run")
            notes.append(line)
            reasons.append(line)
    return healthy, notes, reasons


def run_searches(ctx, runs: list[queries.Run], boards: list[str]) -> tuple[dict, list[str], list[str], int]:
    """Each distinct (query, place) over the boards that plan it. Returns (counts, notes, failures, boards that answered)."""
    groups: dict[tuple[str, str], list[str]] = {}
    for run in runs:
        if run.board in boards:
            groups.setdefault((run.query, run.location), []).append(run.board)
    live, answered = list(boards), set()
    totals = {"searches": 0, "found": 0, "new": 0, "updated": 0, "closed": 0, "tracked": 0}
    notes, failures = [], []
    for (text, place), names in groups.items():
        names = [n for n in dict.fromkeys(names) if n in live]
        if not names:
            continue
        report = search.search(names, [text], ctx.me, location=place, recency_days=ctx.config.recency_days, limit=ctx.config.search_limit,
                               verify_open=True, today=ctx.now.date(), client_factory=CLIENT, sleep=SLEEP, person_acked=ctx.config.acknowledge_terms)
        totals["searches"] += 1
        totals["updated"] += len(report.updated)
        totals["closed"] += report.closed
        totals["tracked"] += report.tracked
        for outcome in report.outcomes:
            new = sum(1 for e in report.new if e.get("board") == outcome.board)
            if outcome.status == "ok":
                answered.add(outcome.board)
                ctx.store.add_event("yield", outcome.board, {"query": text, "found": outcome.found, "new": new}, ctx.now)
            else:
                failures.append(f"{outcome.board}: {outcome.status} {outcome.message}".strip())
                if outcome.status in STOPS:
                    live.remove(outcome.board)
                    notes.append(f"{outcome.board}: {outcome.status}, dropped for the rest of this run" + (f" ({outcome.message})" if outcome.message else ""))
            totals["found"] += outcome.found
            totals["new"] += new
            notes += [f"{outcome.board}: looks degraded: {s}" for s in outcome.symptoms]
    return totals, notes, failures, len(answered)


class Discover:
    spec = Spec("discover", "every healthy enabled board searched with the profile's queries, health and yield recorded",
                trigger=Trigger("every", 12.0), shape="solo", levels=(1,), roles=(), rank=10)

    def propose(self, snap) -> list[Action]:
        if not snap.profile_ready or not due(snap, "discover", snap.config.discover_every_hours):
            return []
        return [Action("discover", "*", f"no successful discover in the last {snap.config.discover_every_hours:g} hours", priority=1)]

    def run(self, action: Action, ctx) -> Outcome:
        profile = (ctx.me / "profile.md").read_text() if (ctx.me / "profile.md").is_file() else ""
        try:
            if not profile.strip():
                raise ValueError("there is no profile")
            _plan_board(profile, STAND_IN, ctx.config.recency_days)
        except (ValueError, P.ProfileError) as error:
            return Outcome("no_op", [f"no queries can be planned from the profile: {error}"], produced={"harness": ""})
        try:
            boards, notes = usable_boards(ctx.me, ctx.config)
        except (ValueError, search.BoardsConfigCorrupt) as error:
            return Outcome("blocked", failures=[f"boards.json cannot be read: {error}"], produced={"harness": ""})
        if not boards:
            why = notes or ["no board is switched on (`search boards enable NAME`)"]
            return Outcome("blocked", notes, failures=["no board is usable: " + "; ".join(why)], produced={"harness": ""})
        healthy, health_notes, reasons = check_health(ctx, boards)
        notes += health_notes
        if not healthy:
            return Outcome("blocked", notes, failures=["no board is usable: " + "; ".join(reasons)], produced={"harness": ""})
        runs, plan_notes = plan(profile, healthy, ctx.playbook, ctx.config.recency_days)
        notes += plan_notes
        if not runs:
            return Outcome("no_op", notes + ["no search is planned for the healthy boards"], produced={"harness": ""})
        try:
            totals, more, failures, answered = run_searches(ctx, runs, healthy)
        except (PermissionError, ValueError, state.StateCorrupt, search.BoardsConfigCorrupt) as error:
            return Outcome("blocked", notes, failures=[f"{type(error).__name__}: {error}"], produced={"harness": ""})
        notes = [f"{totals['searches']} searches over {', '.join(healthy)}: {totals['found']} found, {totals['new']} new, "
                 f"{totals['updated']} updated, {totals['closed']} closed, {totals['tracked']} already in the tracker"] + notes + more
        if not answered:
            return Outcome("blocked", notes, failures=["no board answered a search: " + "; ".join(dict.fromkeys(failures))], produced={"harness": ""})
        return Outcome("success", notes, produced={"harness": "", **totals})


STAGE = Discover()

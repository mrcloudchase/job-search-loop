"""One pass of the outer loop: observe, choose, run one bounded step, check, commit; repeat until nothing is eligible or a brake fires.

The whole control flow of the loop is here and nothing else is: stages do the work, the policy chooses, the gateway spends,
the invariants watch. A pass always ends by writing its digest, whatever happened.
"""
from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from .. import archive, tracker
from ..term import DIM, OFF, RED, YELLOW, say
from . import config as configuration, digest, invariants, policy, snapshot
from .gateway import Gateway, Ledger
from .spec import Outcome
from .stage import Context, Stage
from .store import Store, digest_of, now_stamp

MAX_ACTIONS = 200
MAX_PER_ITEM_PER_PASS = 3


@dataclass
class PassResult:
    run_id: int = 0
    terminal: str = "no_op"            # success (did work), no_op (nothing eligible), blocked (a tripwire or a lock), exhausted
    reason: str = ""
    actions: int = 0
    spent_usd: float = 0.0
    tokens: int = 0
    digest_path: Path | None = None
    violations: list[str] = field(default_factory=list)


def observe(snap: snapshot.Snapshot) -> None:
    """Record what a person did since the last look: tracker status changes, queue items marked done. These are events the stages react to."""
    seen = snap.store.get("tracker_seen", {})
    current = {r.key: r.status for r in snap.rows if r.key}
    for key, status in current.items():
        if seen.get(key) != status:
            snap.store.add_event("human:status" if seen.get(key) else "tracker:new", key, {"from": seen.get(key), "to": status}, snap.now)
        entry = snap.store.queue_item(key) if tracker.normalize_status(status) not in (None, "drafted") else None
        if entry is not None and entry["status"] != "done":         # the person moved the application on: its queue entry is handled
            snap.store.queue_put(key, "done", entry["path"], json.loads(entry["data"] or "{}"), snap.now)
    snap.store.put("tracker_seen", current)


def _stage_index(stages: list) -> dict[str, object]:
    index = {s.spec.name: s for s in stages}
    if len(index) != len(stages):
        raise ValueError("two stages share a name")
    return index


def run_pass(me: Path | str, stages: list[Stage], *, config: configuration.Config | None = None, mode: str = "once",
             transport: httpx.BaseTransport | None = None, now: Callable[[], datetime] | None = None,
             playbook_loader: Callable[[Path, Store], object] | None = None, out: Callable[[str], None] = say) -> PassResult:
    me = Path(me)
    clock = now or (lambda: datetime.now(timezone.utc))
    config = config or configuration.load(me)
    result = PassResult()
    lock_path = me / "engine" / "pass"
    try:
        lock = archive.locked(lock_path, timeout=0.0)
        lock.__enter__()
    except TimeoutError:
        result.terminal, result.reason = "blocked", "another pass is running"
        out(f"{YELLOW}another pass is running; this one does nothing{OFF}")
        return result
    store = Store(me)
    try:
        return _pass(me, stages, config, mode, transport, clock, playbook_loader, out, store, result)
    finally:
        store.close()
        lock.__exit__(None, None, None)


def _pass(me, stages, config, mode, transport, clock, playbook_loader, out, store: Store, result: PassResult) -> PassResult:
    index = _stage_index(stages)
    ledger = Ledger(config)
    gateway = Gateway(config, store, ledger, me, transport)
    result.run_id = store.start_run(mode, json.dumps(asdict(config)), clock())
    skip: set[tuple[str, str]] = set()
    tries: dict[tuple[str, str], int] = {}
    problems: list[str] = []
    out(f"{DIM}pass {result.run_id}: {len(stages)} stages, budget ${config.pass_budget_usd:.2f}, {config.pass_minutes:.0f} min{OFF}")
    try:
        while result.actions < MAX_ACTIONS:
            snap = snapshot.take(me, store, config, clock())
            observe(snap)
            playbook = playbook_loader(me, store) if playbook_loader else None       # a stage may have changed it (or rolled it back) a moment ago
            if ledger.exhausted():
                result.terminal, result.reason = "exhausted", f"pass {ledger.exhausted()} limit reached"
                break
            chosen = policy.next_action(snap, stages, skip)
            problems += [p for p in snap.problems if p not in problems]
            if chosen is None:
                result.terminal = "success" if result.actions else "no_op"
                result.reason = "nothing eligible"
                break
            action, spec = chosen
            stage = index[action.stage]
            key = (action.stage, action.key)
            tries[key] = tries.get(key, 0) + 1
            before = snapshot.fingerprint(me)
            spent_before, tokens_before = ledger.usd, ledger.tokens
            started = now_stamp(clock())
            ctx = Context(me=me, config=config, store=store, gateway=gateway, snapshot=snap, run_id=result.run_id, playbook=playbook, say=out)
            out(f"\n▶ {action.stage} {action.key}: {action.reason}")
            try:
                outcome = stage.run(action, ctx)
            except KeyboardInterrupt:
                ledger.stop = "interrupted"
                gateway.api.cancel()
                outcome = Outcome("exhausted", notes=["interrupted"])
            except Exception as error:
                if _server_down(error, config):
                    outcome = Outcome("exhausted", failures=[f"the harness-interface server is not reachable at {config.server}"],
                                      notes=["start it with `hi server start`; nothing was lost and no attempt counts against this item"])
                    result.terminal, result.reason = "blocked", f"the harness-interface server is not reachable at {config.server} (`hi server start`)"
                    store.record_attempt(result.run_id, action.stage, action.key, action.reason, "", started, now_stamp(clock()),
                                         outcome.terminal, 0.0, 0, outcome.failures, outcome.notes)
                    result.actions += 1
                    out(f"{RED}{result.reason}{OFF}")
                    break
                outcome = Outcome("blocked", failures=[f"{type(error).__name__}: {error}"], notes=[traceback.format_exc().splitlines()[-3:][0][:200]])
            after = snapshot.fingerprint(me)
            tripped = invariants.violations(before, after, snapshot.take(me, store, config, clock()))
            outcome.cost_usd = round(ledger.usd - spent_before, 6)
            outcome.tokens = ledger.tokens - tokens_before
            outcome = _brake(outcome, spec, store, action)
            store.record_attempt(result.run_id, action.stage, action.key, action.reason, str(outcome.produced.get("harness", "")), started,
                                 now_stamp(clock()), outcome.terminal, outcome.cost_usd, outcome.tokens, outcome.failures, outcome.notes)
            result.actions += 1
            out(f"  → {outcome.terminal}" + (f" ({outcome.failures[0][:100]})" if outcome.failures else "") + f"  ${outcome.cost_usd:.2f}")
            if tripped:
                result.violations = tripped
                result.terminal, result.reason = "blocked", "an invariant was violated: " + "; ".join(tripped)
                store.add_event("invariant", action.key, tripped, clock())
                out(f"{RED}INVARIANT VIOLATED: {'; '.join(tripped)}{OFF}")
                break
            if outcome.terminal in ("success", "no_op") or tries[key] >= MAX_PER_ITEM_PER_PASS:
                skip.add(key)
            if outcome.terminal == "exhausted" and ledger.exhausted():
                result.terminal, result.reason = "exhausted", f"pass {ledger.exhausted()} limit reached"
                break
        else:
            result.terminal, result.reason = "exhausted", f"{MAX_ACTIONS} actions in one pass"
    finally:
        result.spent_usd, result.tokens = round(ledger.spent_usd, 4), ledger.tokens
        snap = snapshot.take(me, store, config, clock())
        result.digest_path = digest.write(me, store, snap, result, problems, config, clock())
        store.finish_run(result.run_id, result.terminal, result.spent_usd, result.tokens, str(result.digest_path), {
            "actions": result.actions, "reason": result.reason, "violations": result.violations}, clock())
        out(f"\n{result.terminal}: {result.reason}; {result.actions} actions, ${result.spent_usd:.2f}; digest {result.digest_path}")
    return result


def _server_down(error: Exception, config) -> bool:
    """A refused or timed-out connection to the harness-interface server itself (not to a board or an employer's site)."""
    if not isinstance(error, httpx.TransportError):
        return False
    try:
        return error.request.url.host in config.server and (not error.request.url.port or f":{error.request.url.port}" in config.server)
    except RuntimeError:                                     # an httpx error raised without a request
        return False


def _brake(outcome: Outcome, spec, store: Store, action) -> Outcome:
    """The no-progress brake: the same failure as the last attempt, again, is `stalled` (and the ladder moves on), not another retry."""
    if outcome.terminal in ("blocked", "stalled") and outcome.failures:
        tries = store.attempts(action.stage, action.key)
        digest = digest_of(outcome.failures)
        repeats = 0
        for t in reversed(tries):
            if t["failure_digest"] == digest and t["terminal"] in ("blocked", "stalled"):
                repeats += 1
            else:
                break
        if repeats + 1 >= spec.brakes.repeat_stall:
            outcome.terminal = "stalled"
            outcome.notes.append(f"the same failure {repeats + 1} times: stalled")
    return outcome

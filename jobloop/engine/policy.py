"""Which action next: plain code over the stages' proposals, the attempt history and the pass's limits.

Order: what a person is waiting on soonest, then the stage furthest down the funnel (finish work in progress before starting
more), then the stage's own priority. An item that has stalled rests; a stalled item is retried at once on the next harness
of its ladder until the stage's attempt limit; an item that was blocked rests for the backoff.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .snapshot import Snapshot
from .spec import Action, Spec
from .stage import Stage
from .store import Store

URGENT_DAYS = 3


def _when(stamp: str) -> datetime:
    then = datetime.fromisoformat(stamp)
    return then if then.tzinfo else then.replace(tzinfo=timezone.utc)


def allowed(action: Action, spec: Spec, store: Store, now: datetime) -> tuple[bool, str]:
    """Whether the attempt history lets this action run now, and why not."""
    tries = store.attempts(action.stage, action.key)
    if not tries:
        return True, ""
    last = tries[-1]
    failures = [t for t in tries if t["terminal"] in ("stalled", "blocked", "exhausted")]
    if last["terminal"] in ("success", "no_op"):
        return True, ""
    if last["terminal"] == "exhausted":
        return True, ""
    rested = now - _when(last["finished"]) >= timedelta(hours=spec.brakes.backoff_hours)
    if len(failures) >= spec.brakes.max_attempts:
        return (now - _when(last["finished"]) >= timedelta(hours=spec.brakes.backoff_hours * 4),
                f"{len(failures)} attempts without success; resting")
    if last["terminal"] == "stalled":
        return True, ""                                     # the next attempt escalates to the next harness at once
    return rested, "blocked; resting"


def urgency(action: Action, today: date) -> int:
    """0 for an action a person is waiting on within URGENT_DAYS, else 1 (sorts first)."""
    if not action.deadline:
        return 1
    try:
        left = (date.fromisoformat(action.deadline) - today).days
    except ValueError:
        return 1
    return 0 if left <= URGENT_DAYS else 1


def candidates(snap: Snapshot, stages: list[Stage], skip: set[tuple[str, str]] = frozenset()) -> list[tuple[Action, Spec]]:
    found = []
    for stage in stages:
        if stage.spec.name in snap.config_disabled():
            continue
        try:
            proposed = stage.propose(snap)
        except Exception as error:                          # a stage that cannot even propose must not stop the loop
            snap.problems.append(f"{stage.spec.name}.propose failed: {type(error).__name__}: {error}")
            continue
        for action in proposed:
            if (action.stage, action.key) in skip:
                continue
            ok, why = allowed(action, stage.spec, snap.store, snap.now)
            if ok:
                found.append((action, stage.spec))
    return found


def next_action(snap: Snapshot, stages: list[Stage], skip: set[tuple[str, str]] = frozenset()) -> tuple[Action, Spec] | None:
    found = candidates(snap, stages, skip)
    if not found:
        return None
    found.sort(key=lambda pair: (urgency(pair[0], snap.today), -pair[1].rank, -pair[0].priority, pair[0].key))
    return found[0]

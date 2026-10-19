"""The numbers the loop is judged by and improves against. Computed from the engine database and the tracker, never stored."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .. import tracker
from .store import Store

DRAFTING_STAGES = ("research", "draft", "package")


@dataclass(frozen=True)
class Window:
    accepted: int                      # applications that reached the person's queue as ready
    cost_per_accepted: float | None    # dollars spent on researching, drafting and packaging them, per accepted application
    first_pass_rate: float | None      # drafts that passed every check without a fix round
    judge_mean: float | None           # mean overall judge score (1 to 10) of the delivered drafts
    applied: int
    interviews: int
    offers: int


def _since(since: str | None) -> str:
    return since or "0000"


def window(store: Store, rows=(), since: str | None = None, version: int | None = None) -> Window:
    """Metrics over everything after `since` (an ISO stamp), or for the judgments made under one playbook version."""
    ready = [r for r in store.queue() if r["status"] in ("ready", "done") and r["created"] >= _since(since)]
    spent = store.db.execute(
        "SELECT COALESCE(SUM(cost_usd),0) FROM attempts WHERE stage IN (%s) AND finished >= ?" % ",".join("?" * len(DRAFTING_STAGES)),
        (*DRAFTING_STAGES, _since(since))).fetchone()[0]
    firsts = [json.loads(a["notes"] or "[]") for a in store.db.execute(
        "SELECT notes FROM attempts WHERE stage='draft' AND terminal='success' AND finished >= ?", (_since(since),))]
    first_pass = [1.0 if any("first pass" in n for n in notes) else 0.0 for notes in firsts]
    sql = "SELECT score FROM judgments WHERE subject='draft' AND at >= ?" + (" AND version=?" if version is not None else "")
    scores = [r["score"] for r in store.db.execute(sql, (_since(since), version) if version is not None else (_since(since),))]
    statuses = [r.status for r in rows]
    reached = lambda *s: sum(1 for x in statuses if x in s)
    return Window(len(ready), (spent / len(ready)) if ready else None, (sum(first_pass) / len(first_pass)) if first_pass else None,
                  (sum(scores) / len(scores)) if scores else None, reached(*(set(tracker.STATUSES) - {"drafted"})),
                  reached("interview", "offer", "hired", "offer_declined"), reached("offer", "hired", "offer_declined"))


def render(w: Window) -> str:
    f = lambda v, fmt: "n/a" if v is None else fmt.format(v)
    return (f"accepted (queue-ready) {w.accepted} · cost per accepted {f(w.cost_per_accepted, '${:.2f}')} · "
            f"first-pass rate {f(w.first_pass_rate, '{:.0%}')} · judge mean {f(w.judge_mean, '{:.1f}/10')} · "
            f"applied {w.applied} · interviews {w.interviews} · offers {w.offers}")

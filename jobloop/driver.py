"""From a CV to an application you can click apply on: `python loop.py`.

The driver runs the engine's passes until the queue holds `target` applications that are ready, nothing more can be done
without the person, or a ceiling is reached (passes, dollars). The person's part is three things and the driver asks for
each at the moment it matters: confirm the profile (once), say yes or no to a posting the loop will not trust by itself,
and click apply. It never submits, never marks anything applied and never edits the profile. All output goes through `say`;
the questions go through `ask`; the pass runner and the clock are injected so the whole thing is testable offline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .engine import config as configuration, queue, snapshot
from .engine.store import Store

READY, HELD = "ready", "needs_decision"


@dataclass
class Summary:
    reason: str = ""
    passes: int = 0
    spent_usd: float = 0.0
    ready: list[dict] = field(default_factory=list)
    held: list[dict] = field(default_factory=list)
    approved: list[str] = field(default_factory=list)


def _entry(row) -> dict:
    data = json.loads(row["data"]) if row["data"] else {}
    return {"key": row["item"], "status": row["status"], "path": row["path"], **data}


def queue_state(me: Path) -> tuple[list[dict], list[dict]]:
    with Store(me) as store:
        return [_entry(r) for r in store.queue(READY)], [_entry(r) for r in store.queue(HELD)]


def hosts_held(held: list[dict]) -> list[dict]:
    return [h for h in held if h.get("kind") == "unverified_source_host"]


def ask_about_holds(me: Path, held: list[dict], ask: Callable[[str], str], say: Callable[[str], None]) -> list[str]:
    """One question per posting the loop will not trust by itself: y approves this application, t also trusts its host, anything
    else leaves it. Returns the keys approved."""
    approved = []
    for item in hosts_held(held):
        say(f"\n{item.get('title')}  (fit {item.get('fit')})\n  found on {item.get('host') or item.get('url')}: {item.get('url')}")
        answer = ask("  Is this the employer's real posting, so the loop may draft it? [y = yes, t = yes and trust this host, N = no] ").strip().lower()
        if answer in ("y", "yes", "t", "trust"):
            with Store(me) as store:
                say("  " + queue.approve(me, store, item["key"], host_too=answer in ("t", "trust")).splitlines()[0])
            approved.append(item["key"])
    return approved


def drive(me: Path, passer: Callable[[], object], *, target: int = 3, max_passes: int = 6, budget_usd: float = 40.0,
          ask: Callable[[str], str] | None = None, say: Callable[[str], None] = print) -> Summary:
    """Passes until `target` applications are ready. `passer()` runs one engine pass and returns its PassResult."""
    summary = Summary()
    idle = 0
    while True:
        ready, held = queue_state(me)
        summary.ready, summary.held = ready, held
        if len(ready) >= target:
            summary.reason = f"{len(ready)} applications are ready"
            return summary
        if summary.passes >= max_passes:
            summary.reason = f"stopped after {max_passes} passes"
            return summary
        if summary.spent_usd >= budget_usd:
            summary.reason = f"stopped at the ${budget_usd:.0f} ceiling"
            return summary
        result = passer()
        summary.passes += 1
        summary.spent_usd += float(getattr(result, "spent_usd", 0.0) or 0.0)
        say(f"  pass {summary.passes}: {result.terminal}, {result.actions} actions, ${getattr(result, 'spent_usd', 0.0):.2f}")
        if result.terminal == "blocked":
            summary.reason = f"a pass was blocked: {result.reason}"
            ready, held = queue_state(me)
            summary.ready, summary.held = ready, held
            return summary
        ready, held = queue_state(me)
        summary.ready, summary.held = ready, held
        if result.terminal == "no_op":
            idle += 1
            approved = ask_about_holds(me, held, ask, say) if ask and hosts_held(held) and len(ready) < target else []
            summary.approved += approved
            if approved:
                idle = 0
                continue
            if idle >= 1:
                summary.reason = "nothing more the loop can do without you" if held else "nothing more to do right now (no new postings fit)"
                return summary
        else:
            idle = 0


def report(summary: Summary, say: Callable[[str], None] = print) -> None:
    say(f"\n{summary.reason}. {summary.passes} passes, about ${summary.spent_usd:.2f}.")
    if summary.ready:
        say("\nReady for you to apply (the loop never submits):")
        for item in summary.ready:
            say(f"  {item.get('title')}  (fit {item.get('fit')}, deadline {item.get('deadline') or 'none'})\n"
                f"    apply here: {item.get('url')}\n    your files: {item.get('path')}/  (README.md has the answers and the checklist)")
        say("\nAfter you apply:  python -m jobloop tracker set <key> applied   (the loop then handles follow-ups and interview prep)")
    still = hosts_held(summary.held)
    if still:
        say(f"\n{len(still)} postings are held because they were found on hosts the loop will not trust by itself; "
            "`python loop.py` asks about them, or `python -m jobloop queue approve <key>`.")
    other = [h for h in summary.held if h not in still]
    for item in other:
        say(f"  needs you: {item.get('title')}: {item.get('question') or item.get('reason')}")

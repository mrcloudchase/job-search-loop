"""What a pass tells the person: me/engine/digests/<time>.md, copied to me/inbox/latest.md, and `status`.

The digest is the loop's only voice. It says what the standing authorisation was, what ran and how it ended, what is waiting
for the person (soonest deadline first), what is parked and why, what the loop changed about itself, and what it cost.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import metrics
from .config import Config
from .snapshot import Snapshot
from .store import Store

STATUS_WORDS = {"success": "ok", "no_op": "nothing to do", "blocked": "BLOCKED", "stalled": "STALLED", "exhausted": "ran out of time or money"}


def _queue_lines(store: Store, today: str) -> list[str]:
    rows = []
    for r in store.queue():
        if r["status"] == "done":
            continue
        data = json.loads(r["data"] or "{}")
        rows.append((data.get("deadline") or "9999", r["status"], r["item"], data, r["path"]))
    rows.sort(key=lambda x: (x[1] != "ready", x[0]))
    out = []
    for deadline, status, item, data, path in rows:
        due = f" · deadline {data['deadline']}" if data.get("deadline") else ""
        fit = f" · fit {data['fit']}" if data.get("fit") not in (None, "") else ""          # a follow-up draft or a report has no fit
        out.append(f"- **{status}** `{item}` · {data.get('title', '')}{fit}{due}\n  {data.get('why', '')} → `{path}`")
    return out


def _parked(store: Store) -> list[str]:
    rows = store.db.execute(
        "SELECT a.stage, a.item, a.terminal, a.failures, a.finished FROM attempts a JOIN "
        "(SELECT stage, item, MAX(id) id FROM attempts GROUP BY stage, item) l ON a.id = l.id "
        "WHERE a.terminal IN ('blocked','stalled') ORDER BY a.finished DESC LIMIT 30").fetchall()
    out = []
    for r in rows:
        failures = json.loads(r["failures"] or "[]")
        out.append(f"- `{r['stage']}` `{r['item']}`: {r['terminal']}" + (f" — {failures[0][:140]}" if failures else ""))
    return out


def _playbook_lines(store: Store, run_id: int) -> list[str]:
    """What the loop changed about itself in this pass: playbook versions applied or proposed, and a rollback."""
    row = store.db.execute("SELECT started FROM runs WHERE id=?", (run_id,)).fetchone()
    since = row["started"] if row else ""
    found = []
    for e in sorted(store.events("playbook_rollback", limit=20) + store.events("learn", limit=20), key=lambda e: e["id"]):
        if e["at"] < since:
            continue
        data = json.loads(e["data"] or "{}")
        if e["kind"] == "playbook_rollback":
            found.append(data.get("line", "a playbook version was rolled back"))
        else:
            found += [n for n in data.get("notes", []) if n.startswith(("playbook v", "self-improvement is off"))]
    return found


def render(store: Store, snap: Snapshot, result, problems: list[str], config: Config) -> str:
    lines = [f"# Loop digest {snap.now.strftime('%Y-%m-%d %H:%M')} UTC", "",
             f"**Pass {result.run_id}: {STATUS_WORDS.get(result.terminal, result.terminal)}** — {result.reason or 'done'}. "
             f"{result.actions} actions, ${result.spent_usd:.2f} ({result.tokens:,} unpriced tokens)."]
    if result.violations:
        lines += ["", "## ⚠ Invariant violated (the pass was stopped)", *[f"- {v}" for v in result.violations]]
    lines += ["", "## Authorised by loop.json",
              f"up to {config.max_new_drafts} new drafts a pass, fit ≥ {config.min_fit}, best of {config.best_of}; budget ${config.pass_budget_usd:.2f} / "
              f"{config.pass_minutes:.0f} min; self-improvement {'on' if config.self_improve else 'off'}. The loop never submits, never marks applied, "
              "never edits your profile."]
    queue = _queue_lines(store, snap.today.isoformat())
    lines += ["", "## Waiting for you", *(queue or ["Nothing right now."])]
    pending = store.proposals("pending")
    if pending:
        lines += ["", "## Proposals to decide (`jobloop proposals`)"]
        for p in pending:
            payload = json.loads(p["payload"])
            lines.append(f"- #{p['id']} **{p['kind']}**: {payload.get('summary', '')[:200]}")
    lines += ["", "## This pass"]
    for a in store.attempts_in_run(result.run_id):
        notes = json.loads(a["notes"] or "[]")
        failures = json.loads(a["failures"] or "[]")
        extra = (" — " + (failures[0] if failures else notes[0] if notes else ""))[:170] if (failures or notes) else ""
        lines.append(f"- `{a['stage']}` `{a['item']}` → **{a['terminal']}** ${a['cost_usd']:.2f}{extra}")
    if not store.attempts_in_run(result.run_id):
        lines.append("Nothing ran.")
    changed = _playbook_lines(store, result.run_id)
    if changed:
        lines += ["", "## What the loop changed about itself", *[f"- {c}" for c in changed]]
    parked = _parked(store)
    if parked:
        lines += ["", "## Parked (will retry on the next harness, or rest)", *parked]
    if problems:
        lines += ["", "## Problems seen", *[f"- {p}" for p in problems[:10]]]
    lines += ["", "## Numbers", metrics.render(metrics.window(store, snap.rows))]
    active = [v for v in store.playbook_versions() if v["status"] == "active"]
    if active:
        lines.append(f"Playbook v{active[0]['version']} is active: {json.loads(active[0]['change']).get('summary', '')}")
    return "\n".join(lines) + "\n"


def write(me: Path, store: Store, snap: Snapshot, result, problems: list[str], config: Config, now: datetime) -> Path:
    text = render(store, snap, result, problems, config)
    folder = me / "engine" / "digests"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now.strftime('%Y-%m-%d-%H%M%S')}-pass{result.run_id}.md"
    path.write_text(text)
    inbox = me / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "latest.md").write_text(text)
    if config.notify_file:
        Path(config.notify_file).expanduser().write_text(text)
    return path


def status(me: Path, store: Store, snap: Snapshot) -> str:
    """The current picture, without running anything."""
    runs = store.runs(3)
    lines = ["# Status", ""]
    if runs:
        last = runs[0]
        lines.append(f"Last pass: #{last['id']} {last['terminal'] or 'running'} at {last['finished'] or last['started']} (${last['spent_usd'] or 0:.2f})")
    else:
        lines.append("No pass has run yet.")
    lines.append(f"Profile: {'ready' if snap.profile_ready else 'MISSING: run `jobloop setup`'} · postings {len(snap.postings)} · tracker rows {len(snap.rows)}")
    states: dict[str, int] = {}
    for key in snap.postings:
        states[snap.state_of(key)] = states.get(snap.state_of(key), 0) + 1
    lines.append("Postings by state: " + (", ".join(f"{k} {v}" for k, v in sorted(states.items())) or "none"))
    queue = _queue_lines(store, snap.today.isoformat())
    lines += ["", "## Waiting for you", *(queue or ["Nothing."])]
    pending = store.proposals("pending")
    lines.append(f"\nProposals pending: {len(pending)}")
    lines += ["", metrics.render(metrics.window(store, snap.rows))]
    return "\n".join(lines) + "\n"

"""The person's side of the queue: what to read, what to open, and how to say "I have dealt with this one".

`me/queue/<key>/` is built by the package stage. These helpers only read it, and `done` only marks the engine's queue row.
`done` never touches the tracker: the person submits the application, then runs `jobloop tracker set <key> applied` themselves,
because only a person decides that an application was sent. Everything here returns plain text for the terminal; nothing prompts.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import archive
from .store import Store

ORDER = {"ready": 0, "needs_decision": 1, "blocked": 2}
LEFT_OVER = "9999-12-31"
MAX_SHOWN = 8000


class QueueError(ValueError):
    """A queue request that cannot be met, worded for the person who typed it."""


def entry_dir(me: Path | str, key: str) -> Path:
    """me/queue/<key>, checked to be one folder directly inside me/queue (the key is an archive key)."""
    if not archive.is_key(key):
        raise QueueError(f"{key!r} is not an application key; `jobloop queue list` shows them")
    base = Path(me) / "queue"
    folder = base / key
    if folder.resolve().parent != base.resolve():
        raise QueueError(f"{folder} would leave {base}")
    return folder


def _data(row) -> dict:
    try:
        data = json.loads(row["data"] or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _deadline(row) -> str:
    value = str(_data(row).get("deadline") or "")
    return value if value[:1].isdigit() else ""


def list_text(store: Store) -> str:
    """Open queue entries, soonest deadline first (no deadline last), then ready before needs_decision before blocked."""
    rows = [r for r in store.queue() if r["status"] != "done"]
    finished = len(store.queue("done"))
    if not rows:
        return "The queue is empty." + (f" ({finished} done)" if finished else "")
    rows.sort(key=lambda r: (_deadline(r) or LEFT_OVER, ORDER.get(r["status"], 9), r["created"], r["item"]))
    lines = [f"{len(rows)} in the queue" + (f", {finished} done" if finished else "") + ":", ""]
    for row in rows:
        data = _data(row)
        deadline = _deadline(row)
        title = data.get("title") or row["item"]
        fit = f"  fit {data['fit']}" if data.get("fit") not in (None, "") else ""
        lines.append(f"  {row['status']:<14} {('deadline ' + deadline) if deadline else 'no deadline':<22} {title}{fit}")
        lines.append(f"  {'':<14} {row['item']}")
    lines += ["", "Read one with `jobloop queue show <key>`; after you have submitted it, `jobloop queue done <key>`."]
    return "\n".join(lines)


def show_text(me: Path | str, store: Store, key: str) -> str:
    """One entry as the person reads it: its status, where it is, the checklist, and the files in it."""
    row = store.queue_item(key)
    if row is None:
        known = ", ".join(r["item"] for r in store.queue()) or "nothing is queued yet"
        raise QueueError(f"no queue entry {key!r} ({known})")
    data = _data(row)
    if not archive.is_key(key):             # a follow-up draft, interview pack, offer analysis, report or question: one file, not an application folder
        return _show_entry(row, data)
    folder = entry_dir(me, key)
    out = [f"{data.get('title') or key}  [{row['status']}]", f"folder: {folder}"]
    if data.get("url"):
        out.append(f"apply:  {data['url']}")
    readme = folder / "README.md"
    out.append("")
    try:
        out.append(readme.read_text(encoding="utf-8").rstrip())
    except OSError:
        out.append("(the checklist is missing: the entry will be rebuilt on the next pass)")
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []
    if files:
        out += ["", "files: " + ", ".join(files)]
    return "\n".join(out)


def _show_entry(row, data: dict) -> str:
    """An entry that is not an application: what it is, why, and the file it points at (the draft, pack or report itself)."""
    out = [f"{data.get('title') or row['item']}  [{row['status']}]"]
    if data.get("why"):
        out.append(str(data["why"]))
    path = Path(row["path"]) if row["path"] else None
    if path is not None and path.is_file():
        out += [f"file: {path}", "", path.read_text(encoding="utf-8", errors="replace")[:MAX_SHOWN].rstrip()]
    elif path is not None and str(path):
        out.append(f"where: {path}")
    return "\n".join(out)


def done(store: Store, key: str) -> str:
    """Mark the queue row `done` and say what to do next. The tracker is not touched: submitting is the person's act."""
    row = store.queue_item(key)
    if row is None:
        raise QueueError(f"no queue entry {key!r}; `jobloop queue list` shows them")
    store.queue_put(key, "done", row["path"], _data(row))
    if not archive.is_key(key):
        return f"{key} is marked done in the queue."
    return (f"{key} is marked done in the queue. It is not marked applied: "
            f"now run: jobloop tracker set {key} applied after you have submitted")

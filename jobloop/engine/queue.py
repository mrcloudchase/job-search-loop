"""The person's side of the queue: what to read, what to open, and how to say "I have dealt with this one".

`me/queue/<key>/` is built by the package stage. These helpers only read it, and `done` only marks the engine's queue row.
Entries that are not applications (`followup:`, `interview:`, `offer:`, `upskill:`, `boards:` ... keys) point at one file and `show` prints it.
`done` never touches the tracker: the person submits the application, then runs `jobloop tracker set <key> applied` themselves,
because only a person decides that an application was sent. Everything here returns plain text for the terminal; nothing prompts.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import archive
from ..guards import hosts as host_check
from . import config as config_module
from .store import now_stamp, Store

APPROVED = "human:approve_host"     # engine event: the person approved this application's source host (item = key, data {host})
HOST_KIND = "unverified_source_host"
APPROVE_HOW = "Run `jobloop queue approve {key}` if you checked the employer and the link."
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


def how_line(key: str, data: dict) -> str:
    """For an entry held back for its source host: the exact command that resolves it, else ""."""
    return APPROVE_HOW.format(key=key) + " (`--host` also trusts the host from now on.)" if data.get("kind") == HOST_KIND else ""


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
        if how_line(row["item"], data):
            lines.append(f"  {'':<14} {how_line(row['item'], data)}")
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
    if data.get("kind") == HOST_KIND:         # nothing was drafted, so there is no folder: say what is held back and how to decide
        return "\n".join([f"{data.get('title') or key}  [{row['status']}]", f"apply:  {data.get('url') or '(no link)'}", "",
                          str(data.get("question") or data.get("reason") or ""), "", how_line(key, data)])
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


def approve(me: Path | str, store: Store, key: str, host_too: bool = False, now=None) -> str:
    """The person's yes to an application held back for its unverified source host. Records the event the draft stage reads (for
    THIS application only) and removes the queue row so the stage proposes it again; with `host_too` the host is also added to
    `trusted_hosts` in loop.json. It drafts nothing and never touches the tracker."""
    row = store.queue_item(key)
    if row is None:
        raise QueueError(f"no queue entry {key!r}; `jobloop queue list` shows them")
    data = _data(row)
    if data.get("kind") != HOST_KIND:
        what = {"ready": "an application that is ready for you", "done": "an entry you already marked done",
                "blocked": "an entry that is blocked"}.get(row["status"], "an entry waiting for a decision")
        if data.get("kind"):
            what = f"an entry of kind {data['kind']!r}"
        raise QueueError(f"{key} is {what}, not one held back for an unverified source host, so there is nothing to approve here; "
                         f"`jobloop queue show {key}` says what it is")
    host = str(data.get("host") or "")
    if host_too:
        problem = host_check.trusted_entry_problem(host) if host else "the entry names no host"
        if problem:
            raise QueueError(f"{key}: the host {host or '(none)'!r} cannot be added to trusted_hosts: {problem}")
        _trust_host(Path(me) / config_module.NAME, host)
    store.add_event(APPROVED, key, {"host": host}, now)
    store.queue_delete(key)
    stamp = now_stamp(now)                     # the person's yes ends the rest the blocked attempt put on this application
    store.record_attempt(0, "draft", key, "approved by the person", "", stamp, stamp, "no_op", 0.0, 0, [], ["approved by you: try again now"])
    return (f"{key}: you approved this application{(' on ' + host) if host else ''}. "
            + (f"{host} is now in trusted_hosts in loop.json. " if host_too else "")
            + "Nothing was drafted yet: the next pass drafts it (every check still runs). The tracker is untouched.")


def _trust_host(path: Path, host: str) -> None:
    """Append the host to trusted_hosts in loop.json, keeping every other key; the file is replaced atomically."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as error:
        raise QueueError(f"{path} cannot be read ({error}); fix it, then approve again") from None
    if not isinstance(data, dict):
        raise QueueError(f"{path} is not a JSON object; fix it, then approve again")
    hosts = data.get("trusted_hosts") if isinstance(data.get("trusted_hosts"), list) else []
    if host not in hosts:
        hosts = [*hosts, host]
    path.parent.mkdir(parents=True, exist_ok=True)
    archive.write_atomically(path, (json.dumps({**data, "trusted_hosts": hosts}, indent=2) + "\n").encode())


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

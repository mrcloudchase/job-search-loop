"""state.py: the posting store, `me/state/postings.json`: every posting the loop has ever seen, keyed by `jobkey.make_key`.

The store in memory is a plain dict `key -> entry`; on disk it is `{"postings": {key: entry}}`. Search and rank share it.
An entry has these fields, and fields are only ever added, never renamed or restructured:
  search: key, title, company, url, alt_urls, board, source ('board' or 'fallback'), posted_date, deadline, first_seen,
          last_seen, locations (list), status, tags (list)
  rank:   rank_score, rank_verdict, rank_date, gates, strengths, gaps, fetch_attempts, first_unreachable, last_fetch_error
What it promises:
- a missing or empty file is an empty store; a file that is not a store (bad JSON, wrong shape) is NEVER overwritten: loading
  it and saving over it both raise `StateCorrupt` and say how to recover;
- writes are atomic (a reader sees the old file or the new one) and read-modify-write is locked (`transaction`);
- `upsert` never downgrades a settled status (applied, skipped, gave_up, expired), never loses a rank field or an unknown
  field, never overwrites a known date with nothing, and never replaces a board posting's provenance with a fallback's.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from jobloop import archive, jobkey

STATUSES = ("new", "ranked", "unreachable", "gave_up", "expired", "skipped", "applied")
SETTLED = frozenset(("gave_up", "expired", "skipped", "applied"))
SOURCES = ("board", "fallback")
LIST_FIELDS = ("locations", "tags", "alt_urls")


class StateCorrupt(ValueError):
    """The store on disk is not a store. Nothing is overwritten until a person moves it aside or repairs it."""


def path(me: Path | str) -> Path:
    return Path(me) / "state" / "postings.json"


def _read(file: Path) -> dict:
    if not file.exists():
        return {}
    text = file.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    problem = f"{file} is not a posting store"
    try:
        body = json.loads(text)
    except ValueError as e:
        raise StateCorrupt(f"{problem} (invalid JSON: {e}); move it aside or repair it, nothing was changed") from None
    postings = body.get("postings") if isinstance(body, dict) else None
    if not isinstance(postings, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in postings.items()):
        raise StateCorrupt(f"{problem} (expected {{\"postings\": {{key: entry}}}}); move it aside or repair it, nothing was changed")
    return postings


def _write(file: Path, state: dict) -> None:
    _read(file)                                  # a corrupt file is refused here, before anything replaces it
    file.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"postings": state}, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    archive.write_atomically(file, data.encode("utf-8"))


def load(me: Path | str) -> dict:
    """The store: key -> entry. Empty if the file is missing or empty; `StateCorrupt` if it is not a store."""
    return _read(path(me))


def save(me: Path | str, state: dict) -> None:
    """Replace the store with `state`, under the lock. Prefer `transaction`, which reloads before changing."""
    file = path(me)
    with archive.locked(file):
        _write(file, state)


@contextmanager
def transaction(me: Path | str):
    """Load the store under the lock, yield it to change, and save it on a clean exit; an exception saves nothing."""
    file = path(me)
    with archive.locked(file):
        state = _read(file)
        yield state
        _write(file, state)


def _union(old: list, new: list) -> list:
    seen = {x.casefold() if isinstance(x, str) else x for x in old}
    out = list(old)
    for x in new:
        if (x.casefold() if isinstance(x, str) else x) not in seen:
            seen.add(x.casefold() if isinstance(x, str) else x)
            out.append(x)
    return out


def upsert(state: dict, found: dict, today: date | None = None) -> dict:
    """Add a posting to the store, or fold it into the entry already there, and return that entry.

    `found` has key, title, company, url and optionally board, source, posted_date, deadline, locations, tags, status."""
    key = found["key"]
    if (found.get("source") or "board") not in SOURCES or (found.get("status") or "new") not in STATUSES:
        raise ValueError(f"unknown source or status in {key}: {found.get('source')!r}, {found.get('status')!r}")
    day = (today or date.today()).isoformat()
    entry = state.get(key)
    if entry is None:
        entry = {"key": key, "title": "", "company": "", "url": "", "board": None, "source": found.get("source") or "board", "posted_date": None,
                 "deadline": None, "first_seen": day, "last_seen": day, "locations": [], "status": found.get("status") or "new", "tags": [], "alt_urls": []}
        state[key] = entry
    for field in ("title", "company", "url", "board"):
        if not entry.get(field) and found.get(field):
            entry[field] = found[field]
    for field in ("posted_date", "deadline"):
        if found.get(field) and (field == "deadline" or not entry.get(field)):
            entry[field] = found[field]
    if found.get("source") == "board":
        entry["source"] = "board"
    for field in LIST_FIELDS:
        entry[field] = _union(entry.get(field) or [], list(found.get(field) or []))
    other = found.get("url")
    if other and other != entry["url"] and other not in entry["alt_urls"]:
        entry["alt_urls"].append(other)
    entry.setdefault("first_seen", day)
    entry["last_seen"] = day
    incoming = found.get("status")
    if incoming == "expired" and entry.get("status") not in SETTLED:
        entry["status"] = "expired"
    return entry


def url_index(state: dict) -> dict[str, str]:
    """canonical URL -> key, over every entry's main and alternative addresses, whatever the key rule that made the key."""
    return {jobkey.canonical_url(u): key for key, entry in state.items()
            for u in [entry.get("url"), *(entry.get("alt_urls") or [])] if u}

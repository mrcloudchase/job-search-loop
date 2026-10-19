"""Proposals: what a person decides. `accept` is the ONLY code path in the product (besides `jobloop setup`) that writes profile.md.

A stage that learns something about the person (a public page, their edits to a draft) or wants a change that only a person
may approve adds a proposal (`Store.add_proposal`); nothing happens until `accept`. Kinds:

- `profile_enrichment` {summary, section, text, source_url, quote}: a verified fact from a page the person named.
- `profile_fact` {summary, section, text, evidence, source?}: a fact learned from the person's own edits; `evidence` is their words.
- `playbook_change` {summary, change}: a change `learn` did not auto-apply; accepting applies it with `playbook.apply`.
- `add_board` {summary, board, enable}: a board the loop built, switched off. Accepting records that the person read it, and
  switches it on only if `enable` is true and its terms are acknowledged (in loop.json or boards.json).

A profile addition is additive (one new source-tagged bullet at the end of the stated section; no existing line changes), is
re-checked with the profile's own structure and grounding checks against the evidence in the proposal, and is written atomically
(the previous profile is kept in state/profile.previous.md). A refusal leaves the proposal pending with the reason in its note.
An unknown kind is refused, never guessed at.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import archive, profile, search
from ..research import MIN_QUOTE
from ..text import flat
from . import config as configuration, playbook
from .store import Store

ADDITIVE_KINDS = ("profile_enrichment", "profile_fact")
KINDS = ADDITIVE_KINDS + ("playbook_change", "add_board")
ADDITIONS = "Confirmed additions"
SECTIONS = (ADDITIONS, "Projects", "Certifications", "Publications", "Awards")   # where a bullet may be added
MAX_TEXT = 400


@dataclass(frozen=True)
class Result:
    ok: bool
    reason: str = ""            # why it was refused, or what was done
    applied: str = ""           # the line added to the profile, if any


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def list_pending(store: Store) -> list[dict]:
    """Pending proposals, oldest first: {id, kind, created, source, summary, payload, note}."""
    out = []
    for row in store.proposals("pending"):
        payload = _payload(row)
        out.append({"id": row["id"], "kind": row["kind"], "created": row["created"], "source": row["source"],
                    "summary": str(payload.get("summary") or row["kind"]), "payload": payload, "note": row["note"] or ""})
    return out


def _payload(row) -> dict:
    try:
        data = json.loads(row["payload"])
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _refuse(store: Store, proposal_id: int, reason: str, now: datetime) -> Result:
    """The proposal stays pending; the reason is kept with it and recorded."""
    with store.tx() as db:
        db.execute("UPDATE proposals SET note=? WHERE id=? AND status='pending'", (reason[:500], proposal_id))
    store.add_event("proposal:refused", str(proposal_id), {"reason": reason[:500]}, now)
    return Result(False, reason)


def _done(store: Store, proposal_id: int, now: datetime, note: str, applied: str = "") -> Result:
    store.decide_proposal(proposal_id, "accepted", note, now)
    store.add_event("proposal:accepted", str(proposal_id), {"note": note, "applied": applied}, now)
    return Result(True, note, applied)


def reject(store: Store, proposal_id: int, now: datetime | None = None, note: str = "") -> Result:
    """The person says no. Nothing is written anywhere; a rejected enrichment is not proposed again."""
    now = _now(now)
    row = next((r for r in store.proposals("pending") if r["id"] == proposal_id), None)
    if row is None:
        return Result(False, f"no pending proposal #{proposal_id}")
    store.decide_proposal(proposal_id, "rejected", note, now)
    store.add_event("proposal:rejected", str(proposal_id), {"kind": row["kind"], "note": note}, now)
    return Result(True, "rejected")


def accept(me: Path | str, store: Store, proposal_id: int, now: datetime | None = None, *,
           ignored: Callable[[Path], bool | None] = profile.git_ignored) -> Result:
    """The person says yes: apply the proposal if it passes every check, else refuse and leave it pending."""
    me, now = Path(me), _now(now)
    row = next((r for r in store.proposals() if r["id"] == proposal_id), None)
    if row is None:
        return Result(False, f"no proposal #{proposal_id}")
    if row["status"] != "pending":
        return Result(False, f"proposal #{proposal_id} is already {row['status']}")
    payload, kind = _payload(row), row["kind"]
    try:
        if kind == "profile_enrichment":
            return _accept_addition(me, store, proposal_id, payload, _enrichment_evidence(payload, now), now, ignored)
        if kind == "profile_fact":
            return _accept_addition(me, store, proposal_id, payload, _fact_evidence(payload, now), now, ignored)
        if kind == "playbook_change":
            return _accept_playbook(me, store, proposal_id, payload, now)
        if kind == "add_board":
            return _accept_board(me, store, proposal_id, payload, now)
    except (ValueError, PermissionError, OSError, profile.ProfileError) as error:
        return _refuse(store, proposal_id, f"{type(error).__name__}: {error}", now)
    return _refuse(store, proposal_id, f"unknown proposal kind {kind!r}: nothing was done", now)


# ── additions to the profile ──
@dataclass(frozen=True)
class Evidence:
    text: str               # what the source says (the quote, or the person's own words)
    tag: str                # the source tag written after the bullet
    missing: str = ""       # why there is no usable evidence


def _enrichment_evidence(payload: dict, now: datetime) -> Evidence:
    url, quote = str(payload.get("source_url") or ""), " ".join(str(payload.get("quote") or "").split())
    if not url.lower().startswith("https://"):
        return Evidence("", "", "an enrichment needs the https page it came from (source_url)")
    if len(quote) < MIN_QUOTE:
        return Evidence("", "", f"an enrichment needs the verbatim quote it was verified with (at least {MIN_QUOTE} characters)")
    day = now.date().isoformat()
    return Evidence(f"{quote}\n{url}\nchecked {day}", f"public page {url}, checked {day}")


def _fact_evidence(payload: dict, now: datetime) -> Evidence:
    words = " ".join(str(payload.get("evidence") or "").split())
    if not words:
        return Evidence("", "", "a fact learned from your edits needs your own words as evidence")
    day = now.date().isoformat()
    source = " ".join(str(payload.get("source") or "your own edit").split())
    return Evidence(f"{words}\naccepted {day}", f"{source}, accepted {day}")


def _accept_addition(me: Path, store: Store, proposal_id: int, payload: dict, evidence: Evidence, now: datetime, ignored) -> Result:
    if evidence.missing:
        return _refuse(store, proposal_id, evidence.missing, now)
    text, section = " ".join(str(payload.get("text") or "").split()), str(payload.get("section") or ADDITIONS).strip()
    if not text or len(text) > MAX_TEXT or text.startswith(("#", "-")):
        return _refuse(store, proposal_id, f"the text is empty, over {MAX_TEXT} characters, or looks like a heading or a list marker", now)
    if section not in SECTIONS:
        return _refuse(store, proposal_id, f"a profile addition goes under one of: {', '.join(SECTIONS)}, not {section!r}", now)
    target = me / "profile.md"
    if not target.is_file():
        return _refuse(store, proposal_id, "there is no profile.md to add to: run `jobloop setup`", now)
    profile.preflight(me, ignored)
    with archive.locked(target):
        old = target.read_text()
        line = f"- {text} (source: {evidence.tag})"
        if line in old.splitlines():                                    # a crash after the write, before the decision
            return _done(store, proposal_id, now, "already in the profile", line)
        if flat(text) in flat(old):
            return _refuse(store, proposal_id, "the profile already says this; reject the proposal", now)
        new = insert(old, section, line)
        failures = introduced_failures(old, new, evidence.text)
        if failures:
            return _refuse(store, proposal_id, "the profile's own checks refuse it: " + " | ".join(failures[:3]), now)
        (me / "state").mkdir(exist_ok=True)
        archive.write_atomically(me / "state" / "profile.previous.md", old.encode())
        archive.write_atomically(target, new.encode())
    return _done(store, proposal_id, now, f"added under '{section}'", line)


def insert(md: str, section: str, line: str) -> str:
    """`md` with one new bullet at the end of `## section` (the section is created at the end if it is missing). Nothing else changes."""
    lines = md.splitlines()
    heading = f"## {section}"
    if heading not in lines:
        lines += ["", heading]
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    while end > start and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, line)
    return "\n".join(lines) + "\n"


def introduced_failures(old: str, new: str, evidence: str) -> list[str]:
    """The profile checks `new` fails that `old` did not: structure, and every number, date, link, email and phone grounded in
    the profile itself or the evidence. The evidence sits under a context source name, so its years are not demanded back."""
    sources = {"profile.md": old, profile.CONTEXT_PREFIX + "evidence": evidence}
    before = set(profile.profile_failures(old, sources))
    return [f for f in profile.profile_failures(new, sources) if f not in before]


# ── the other kinds ──
def _accept_playbook(me: Path, store: Store, proposal_id: int, payload: dict, now: datetime) -> Result:
    change = payload.get("change", {k: v for k, v in payload.items() if k != "summary"})
    pb = playbook.apply(me, store, change, now)                          # ValueError (out of bounds) is a refusal
    return _done(store, proposal_id, now, f"playbook version {pb.version} is active")


def _accept_board(me: Path, store: Store, proposal_id: int, payload: dict, now: datetime) -> Result:
    board = payload.get("board")
    if not isinstance(board, str) or not re.fullmatch(r"[a-z0-9_-]+", board):
        return _refuse(store, proposal_id, "the proposal names no board", now)
    if payload.get("enable") is not True:
        return _done(store, proposal_id, now, f"{board} read; it stays switched off")
    config = configuration.load(me)
    search.set_enabled(me, board, True, acknowledged_terms=config.acknowledge_terms, today=now.date())
    return _done(store, proposal_id, now, f"{board} switched on")

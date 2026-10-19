"""The rank command's per-posting state machine, as pure functions over a plain `dict[key, entry]` the caller hands in.

    new ──fetched, scored──► ranked          (a passed deadline makes it expired instead)
    new/unreachable ──page says closed──► expired
    new/unreachable ──could not fetch──► unreachable ──3 failures, 48 h apart in all──► gave_up
    ranked ──stored deadline passed (the sweep, no fetch)──► expired

It promises: a 404, a 403 or any failure to fetch is never `expired` (only positive evidence is: the page says closed,
a 410, a redirect to a listing, a board's closed signal, a past deadline); a job beyond --limit is deferred and untouched,
never dropped; an unparseable date is treated as absent and reported, never compared or guessed; `applied` and `skipped`
entries are never touched; a result for an unknown key or a bad score is an error and writes nothing. Functions mutate the
dict they are given (and say so); the clock is always a parameter. Only rank fields are added to an entry."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from . import archive

URGENT_DAYS = 7
OLD_DAYS = 30
GIVE_UP_ATTEMPTS = 3
GIVE_UP_AFTER = timedelta(hours=48)
ERROR_CAP = 200
KEPT_NOTES = 3
RETRY_FIELDS = ("fetch_attempts", "first_unreachable", "last_fetch_error")
RANK_FIELDS = ("status", "deadline", "rank_score", "rank_verdict", "rank_date", "gates", "strengths", "gaps", *RETRY_FIELDS)
RANKABLE = frozenset({"new", "ranked", "unreachable", "expired", "gave_up"})
SELECTABLE = frozenset({"new", "unreachable"})
REVIVABLE = SELECTABLE | {"ranked", "expired", "gave_up"}
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


class ResultError(ValueError):
    """A result that cannot be written: nothing was changed."""


# ── results: what the fetch and the fit say about one posting ───────────────────────────────────
@dataclass(frozen=True)
class Scored:
    """A fetched, evaluated posting. `gates` are dicts {name, verdict, notes}; `deadline` is what the fresh page said."""
    key: str
    score: int
    verdict: str
    gates: tuple[dict, ...]
    strengths: tuple[str, ...]
    gaps: tuple[str, ...]
    deadline: str | None = None


@dataclass(frozen=True)
class Closed:
    """Positive evidence the posting is closed. `reason` names it (never "404")."""
    key: str
    reason: str


@dataclass(frozen=True)
class Failed:
    """The posting could not be fetched. Says nothing about whether it is open."""
    key: str
    error: str


Result = Scored | Closed | Failed


# ── dates, defensively ──────────────────────────────────────────────────────────────────────────
def parse_iso(text: object) -> date | None:
    """A strict YYYY-MM-DD date, else None: ASAP, DD.MM.YYYY, free text and non-strings are treated as absent."""
    if not isinstance(text, str) or not _ISO.fullmatch(text.strip()):
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def utc(moment: datetime) -> datetime:
    """A naive time is taken as UTC."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def stamp(moment: datetime) -> str:
    return utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_stamp(text: object) -> datetime | None:
    try:
        return utc(datetime.fromisoformat(text.replace("Z", "+00:00"))) if isinstance(text, str) else None
    except ValueError:
        return None


# ── selection ───────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Selection:
    """Who is ranked this run (`selected`, in order), who waits (`deferred`), and the counts the report states."""
    selected: tuple[str, ...]
    deferred: tuple[str, ...]
    eligible: int
    excluded_by_tracker: int
    total_entries: int


def projection(entry: dict) -> dict:
    """The compact view of a posting a harness or a report may be handed; everything else stays in the store."""
    return {k: entry.get(k) for k in ("key", "title", "company", "url", "board", "deadline", "posted_date")}


def _notes(entry: dict) -> str:
    return " ".join(str(n) for field in ("strengths", "gaps") if isinstance(entry.get(field), list) for n in entry[field])


def candidates(state: dict[str, dict], limit: int = 10, focus: str = "", include_all: bool = False,
               tracker_keys: Iterable[tuple[str, str]] = ()) -> Selection:
    """Who to rank. By default `new` and `unreachable` entries (an unreachable one is retried every run; the 48 hour
    floor is on giving up, not on trying). `include_all` adds ranked, expired and gave_up, never skipped or applied.
    Anything whose (company, title) is in the tracker is out of scope whatever the flags. `focus` keeps entries whose
    title, company or stored strengths/gaps contain the text. Beyond `limit` (0 = no cap) entries are deferred."""
    taken = {(archive.slug_part(c), archive.slug_part(r)) for c, r in tracker_keys}
    wanted = (REVIVABLE if include_all else SELECTABLE)
    pool, excluded = [], 0
    for key, entry in state.items():
        if entry.get("status") not in wanted:
            continue
        if (archive.slug_part(entry.get("company") or ""), archive.slug_part(entry.get("title") or "")) in taken:
            excluded += 1
            continue
        if focus and focus.casefold() not in " ".join((str(entry.get("title") or ""), str(entry.get("company") or ""),
                                                      _notes(entry))).casefold():
            continue
        pool.append(key)
    pool.sort(key=lambda k: state[k].get("first_seen") or "", reverse=True)                 # newest first, then
    pool.sort(key=lambda k: parse_iso(state[k].get("deadline")) or date.max)                # soonest deadline first
    if limit < 0:
        raise ValueError(f"--limit must be 0 (no cap) or more, not {limit}")
    cut = len(pool) if limit == 0 else limit
    return Selection(tuple(pool[:cut]), tuple(pool[cut:]), len(pool), excluded, len(state))


# ── write-back ──────────────────────────────────────────────────────────────────────────────────
def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _clear_retry(entry: dict) -> None:
    for field in RETRY_FIELDS:
        entry.pop(field, None)


def _plain(text: object) -> str:
    return " ".join(str(text).split())[:ERROR_CAP]


def _score_problems(result: Scored) -> list[str]:
    problems = []
    if isinstance(result.score, bool) or not isinstance(result.score, int) or not 0 <= result.score <= 100:
        problems.append(f"score must be an integer from 0 to 100, not {result.score!r}")
    if not result.verdict or not isinstance(result.verdict, str):
        problems.append("a verdict is required")
    return problems


def apply_result(state: dict[str, dict], result: Result, now: datetime) -> str:
    """Write one result into `state` and return what happened: ranked, expired, unreachable or gave_up. Raises
    ResultError, changing nothing, for an unknown key, an entry rank may not touch, or a bad score. The deadline stored
    is only replaced by a fresh one that parses (absence, or text like ASAP, is not a correction), so the sweep keeps
    working; a passed deadline is positive evidence and expires the posting."""
    if not isinstance(result, (Scored, Closed, Failed)):
        raise ResultError(f"not a rank result: {type(result).__name__}")
    entry = state.get(result.key)
    if entry is None:
        raise ResultError(f"no posting {result.key!r} in the store")
    if entry.get("status") not in RANKABLE:
        raise ResultError(f"{result.key}: status {entry.get('status')!r} is never changed by rank")
    if isinstance(result, Closed):
        _clear_retry(entry)
        entry["status"] = "expired"
        return "expired"
    if isinstance(result, Failed):
        return _unreachable(entry, result, now)
    problems = _score_problems(result)
    if problems:
        raise ResultError(f"{result.key}: " + "; ".join(problems))
    fresh = result.deadline if parse_iso(result.deadline) or not parse_iso(entry.get("deadline")) else None
    if fresh:
        entry["deadline"] = fresh
    _clear_retry(entry)
    due = parse_iso(entry.get("deadline"))
    if due is not None and due < utc(now).date():
        entry["status"] = "expired"
        for field in ("rank_score", "rank_verdict", "rank_date"):
            entry.pop(field, None)
        return "expired"
    entry.update(status="ranked", rank_score=result.score, rank_verdict=result.verdict, rank_date=utc(now).date().isoformat(),
                 gates=[dict(g) for g in result.gates], strengths=list(result.strengths[:KEPT_NOTES]),
                 gaps=list(result.gaps[:KEPT_NOTES]))
    return "ranked"


def _unreachable(entry: dict, result: Failed, now: datetime) -> str:
    due = parse_iso(entry.get("deadline"))
    if due is not None and due < utc(now).date():
        _clear_retry(entry)
        entry["status"] = "expired"
        return "expired"
    first = _read_stamp(entry.get("first_unreachable"))
    attempts = _count(entry.get("fetch_attempts")) + 1
    entry.update(fetch_attempts=attempts, first_unreachable=stamp(first or now), last_fetch_error=_plain(result.error))
    if attempts >= GIVE_UP_ATTEMPTS and first is not None and utc(now) - first >= GIVE_UP_AFTER:
        entry["status"] = "gave_up"
        return "gave_up"
    entry["status"] = "unreachable"
    return "unreachable"


# ── the sweep ───────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Swept:
    checked: int
    expired: tuple[str, ...]
    closing_soon: tuple[str, ...]                   # by deadline
    unparseable: tuple[tuple[str, str], ...]        # (key, board) whose stored deadline is not a date


def sweep(state: dict[str, dict], today: date, exclude: Iterable[str] = ()) -> Swept:
    """Expire `ranked` entries (not in `exclude`) whose stored deadline is before `today`, with no fetch. Entries with
    no deadline are never expired; one that is not a date is reported with its board and left alone. Mutates `state`."""
    skip = set(exclude)
    checked, expired, soon, bad = 0, [], [], []
    for key, entry in state.items():
        if entry.get("status") != "ranked" or key in skip:
            continue
        checked += 1
        raw = entry.get("deadline")
        due = parse_iso(raw)
        if due is None:
            if isinstance(raw, str) and raw.strip():
                bad.append((key, str(entry.get("board") or entry.get("source") or "")))
        elif due < today:
            entry["status"] = "expired"
            expired.append(key)
        elif (due - today).days <= URGENT_DAYS:
            soon.append(key)
    soon.sort(key=lambda k: (parse_iso(state[k]["deadline"]), k))
    return Swept(checked, tuple(expired), tuple(soon), tuple(bad))


# ── flags: signals, never vetoes ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Flags:
    urgent: str = ""                    # "deadline in 3 days (2026-10-22)"; empty when not urgent
    old: str = ""                       # "posted 2024-05-13, 29 months ago"
    unparseable: tuple[str, ...] = ()   # the fields (deadline, posted_date) whose text is not a date


def flags(entry: dict, today: date) -> Flags:
    """Urgent: a deadline 0-7 days away. Old: posted more than 30 days ago, with the age spelled out and any future
    deadline noted. An absent or unparseable date gives no flag and no guess; an unparseable one is named."""
    bad = tuple(f for f in ("deadline", "posted_date") if isinstance(entry.get(f), str) and entry[f].strip()
                and parse_iso(entry[f]) is None)
    due, posted = parse_iso(entry.get("deadline")), parse_iso(entry.get("posted_date"))
    urgent = old = ""
    if due is not None and 0 <= (due - today).days <= URGENT_DAYS:
        days = (due - today).days
        urgent = f"deadline {'today' if days == 0 else 'tomorrow' if days == 1 else f'in {days} days'} ({due.isoformat()})"
    if posted is not None and (today - posted).days > OLD_DAYS:
        days = (today - posted).days
        age = f"{days} days ago" if days < 60 else f"{days // 30} months ago"
        old = f"posted {posted.isoformat()}, {age}"
        if due is not None and due >= today:
            old += f"; deadline {due.isoformat()} is still ahead"
    return Flags(urgent, old, bad)


def vetoes(entry: dict) -> tuple[str, ...]:
    """Every FAIL gate note stored on the entry (they quote the posting): a veto whatever the score."""
    return tuple(f"{g['name']}: {n}" for g in entry.get("gates") or [] if g.get("verdict") == "FAIL"
                 for n in g.get("notes") or ["failed"])


def gate_flags(entry: dict) -> tuple[str, ...]:
    """Every FLAG gate note (location, language, eligibility, permit): shown beside the job, never a veto."""
    return tuple(f"{g['name']}: {n}" for g in entry.get("gates") or [] if g.get("verdict") == "FLAG"
                 for n in g.get("notes") or ["flagged"])


# ── ordering ────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Arranged:
    shortlist: tuple[str, ...]
    below: tuple[str, ...]
    vetoed: tuple[str, ...]


def arrange(state: dict[str, dict], keys: Iterable[str], today: date, top: int = 5) -> Arranged:
    """Ranked entries by score descending, an urgent deadline winning ties; the first `top` are the shortlist. A vetoed
    entry is never in either list: it is listed apart."""
    ranked = [k for k in keys if state[k].get("status") == "ranked"]
    order = sorted(ranked, key=lambda k: (-int(state[k].get("rank_score") or 0), not flags(state[k], today).urgent, k))
    clean = [k for k in order if not vetoes(state[k])]
    return Arranged(tuple(clean[:top]), tuple(clean[top:]), tuple(k for k in order if vetoes(state[k])))


def merge(fresh: dict[str, dict], ranked: dict[str, dict], keys: Iterable[str]) -> None:
    """Copy only the fields rank writes, for `keys`, from the store rank worked on into a freshly loaded one, so a search
    that ran meanwhile loses nothing. A key that has vanished is skipped; a rank field rank dropped is dropped. Mutates `fresh`."""
    for key in keys:
        if key in fresh and key in ranked:
            for field in RANK_FIELDS:
                if field in ranked[key]:
                    fresh[key][field] = ranked[key][field]
                else:
                    fresh[key].pop(field, None)

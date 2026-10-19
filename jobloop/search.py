"""search.py: run the job boards a person has switched on, and keep what they found in the posting store.

Boards are Python adapters in a fixed registry (`boards.BUILT_IN`). They run IN THIS PROCESS: the adapter module is imported and
`module.search(Query, Client)` is called with a `boards.http.Client` that is the only thing touching the network. An adapter is
never run in a harness and never through a shell. A person's own board (`jobloop/addboard.py`) is the one exception to "in this process":
`boards/external.py` runs it as a separate, time-limited program with a scrubbed environment and checks what it prints. What it promises:
- one board failing never stops the others: each board ends with an `Outcome` (ok, blocked, rate_limited, robots, network,
  parse, not_found, invalid), and a board that refuses us (blocked, rate_limited, robots, network) is not asked again;
- a board is on or off in `me/boards.json`; with nothing recorded it follows the adapter's DEFAULT_ENABLED (the Danish boards
  are off); a board with TERMS runs only after an explicit, recorded acknowledgement, and never without one;
- what a board cannot filter is filtered here (recency, default 14 days; the limit); a posting whose deadline has passed is not
  stored, and one already stored is marked expired; a posting a board's detail page reports closed is not stored;
- a posting is deduplicated by URL (tracking parameters ignored, whatever key an older rule made) and by canonical key;
  the same company and title posted for several places becomes ONE entry with a `locations` list and the `mass_posted` tag,
  a caution only: nothing is excluded or scored lower because of it;
- a new posting whose company and role are already in me/tracker.csv is stored as skipped with the tag `tracked` and never
  presented as new;
- posted_date, deadline, board and source are stored with every entry; an existing entry's status and rank fields are untouched;
- `health` makes at most a few tiny requests inside a hard time budget and tells a broken portal from a rate-limited one.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from jobloop import archive, boards as registry, jobkey, state, tracker
from jobloop.boards.contract import BoardError, Query, parse_date, recent, result
from jobloop.boards.http import Client
from jobloop.term import GREEN, RED, YELLOW, say

HARNESS = False
DEFAULT_RECENCY_DAYS = 14
MASS_POSTED_AT = 2
MASS_POSTED = "mass_posted"
VERIFY_LIMIT = 25
MAX_REQUESTS = 60                # per board per run
MIN_GAP = 0.3                    # seconds between two requests to one board
RUN_SECONDS = 600.0
STOPS_BOARD = frozenset(("blocked", "rate_limited", "robots", "network"))
SENTINELS = ("engineer", "manager")
HEALTH_LOCATION = {"linkedin": "Remote"}        # LinkedIn refuses a search with no location
_CLOSED = re.compile(r"closed|expired|removed|taken down|no longer", re.I)


class BoardsConfigCorrupt(ValueError):
    """me/boards.json is not a board settings file. It is never overwritten."""


class BudgetExceeded(Exception):
    """A health check ran out of requests or time."""


# ── which boards are on ──
def module_of(name: str, me: Path | str | None = None):
    """The adapter called `name`: a built-in one, or (with `me`) one of this person's registered external boards, which
    boards/external.py runs as a separate program."""
    return registry.load(name, me)


def _config_file(me: Path | str) -> Path:
    return Path(me) / "boards.json"


def _read_config(me: Path | str) -> dict:
    file = _config_file(me)
    if not file.exists() or not file.read_text(encoding="utf-8").strip():
        return {}
    try:
        body = json.loads(file.read_text(encoding="utf-8"))
    except ValueError:
        body = None
    boards = body.get("boards") if isinstance(body, dict) else None
    if not isinstance(boards, dict) or not all(isinstance(v, dict) for v in boards.values()):
        raise BoardsConfigCorrupt(f"{file} is not a board settings file ({{\"boards\": {{name: {{...}}}}}}); move it aside or repair it")
    return boards


def acknowledged(me: Path | str, name: str) -> bool:
    return bool(_read_config(me).get(name, {}).get("terms_acknowledged"))


def is_enabled(me: Path | str, name: str, config: dict | None = None, person_acked: bool = False) -> bool:
    """On if recorded on (or, with nothing recorded, the adapter's default), and, for a board with TERMS, acknowledged.
    `person_acked` is the person's own standing acknowledgement given by the caller (the loop's `acknowledge_terms`)."""
    module = module_of(name, me)
    config = _read_config(me) if config is None else config
    on = config.get(name, {}).get("enabled")
    on = module.DEFAULT_ENABLED if on is None else bool(on)
    return on and (not module.TERMS or person_acked or bool(config.get(name, {}).get("terms_acknowledged")))


def enabled_boards(me: Path | str, person_acked: bool = False) -> list[str]:
    config = _read_config(me)
    return [name for name in registry.names(me) if is_enabled(me, name, config, person_acked)]


def set_enabled(me: Path | str, name: str, on: bool, acknowledged_terms: bool = False, today: date | None = None) -> None:
    """Switch a board on or off (recorded in me/boards.json). Switching a board with TERMS on needs the acknowledgement."""
    module = module_of(name, me)
    file = _config_file(me)
    with archive.locked(file):
        boards = _read_config(me)
        entry = dict(boards.get(name, {}))
        if on and module.TERMS and not (acknowledged_terms or entry.get("terms_acknowledged")):
            raise PermissionError(f"{name} has terms: {module.TERMS} Confirm personal, low-volume use with --acknowledge-terms.")
        entry["enabled"] = on
        if on and module.TERMS:
            entry.setdefault("terms_acknowledged", (today or date.today()).isoformat())
        boards[name] = entry
        archive.write_atomically(file, (json.dumps({"boards": boards}, indent=1, sort_keys=True) + "\n").encode())


def require_runnable(me: Path | str, module, person_acked: bool = False) -> None:
    """A board with TERMS is never run, even when asked for by name, until its terms are acknowledged."""
    name = module.NAME
    if module.TERMS and not (person_acked or acknowledged(me, name)):
        raise PermissionError(f"{name} has terms: {module.TERMS} Run `boards enable {name} --acknowledge-terms` first.")


def make_client(module, *, transport=None, sleep: Callable[[float], None] = time.sleep, timeout: float | None = None,
                retries: int | None = None) -> Client:
    """The HTTP client an adapter gets: its declared hosts, its own timeout and retries, and robots.txt checked (waived only for
    a board whose acknowledged terms permit pages its robots.txt disallows)."""
    waive = bool(module.TERMS) and getattr(module, "ACK_WAIVES_ROBOTS", False)
    return Client(module.NAME, module.HOSTS, transport=transport, sleep=sleep, robots=(lambda url: True) if waive else None,
                  timeout=timeout if timeout is not None else getattr(module, "TIMEOUT", 20.0),
                  retries=retries if retries is not None else getattr(module, "RETRIES", 3))


# ── a search run ──
@dataclass(frozen=True)
class Outcome:
    board: str
    status: str                  # ok or a BoardError kind
    found: int = 0
    message: str = ""
    symptoms: tuple[str, ...] = ()   # signs of a degraded parser, noticed for free in what the board returned


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)
    new: list[dict] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)
    closed: int = 0
    duplicates: int = 0
    tracked: int = 0


def is_closed(error: BoardError) -> bool:
    """Did a detail page say the posting is closed? Only a not_found that says so: a block or a rate limit never does."""
    return error.kind == "not_found" and bool(_CLOSED.search(str(error)))


def _queries(queries, location: str, recency_days: int | None, limit: int) -> list[Query]:
    return [q if isinstance(q, Query) else Query(q, location, recency_days, limit) for q in queries]


def _run_board(module, queries: list[Query], http, known, verify: list[int], today: date) -> tuple[Outcome, list[dict], int]:
    """One board over every query. A refusal stops this board only; a bad query or a parse failure moves on to the next query."""
    found: list[dict] = []
    closed, status, message = 0, "ok", ""
    for query in queries:
        try:
            rows = [r for r in (result(x) for x in module.search(query, http)) if r]
        except BoardError as e:
            status, message = status if status != "ok" else e.kind, message or str(e)
            if e.kind in STOPS_BOARD:
                break
            continue
        except BudgetExceeded as e:
            status, message = status if status != "ok" else "invalid", message or str(e)
            break
        except Exception as e:
            status, message = status if status != "ok" else "parse", message or f"{type(e).__name__}: {e}"
            continue
        found += recent(rows, query.recency_days, today)[: query.limit]
    for row in found:
        if verify[0] <= 0 or status in STOPS_BOARD or known(row):
            continue
        verify[0] -= 1
        try:
            row["deadline"] = row["deadline"] or (result(module.detail(row["id"], http)) or {}).get("deadline")
        except BoardError as e:
            if is_closed(e):
                row["closed"] = True
                closed += 1
            elif e.kind in STOPS_BOARD:
                status, message = e.kind, f"while checking postings are still open: {e}"
        except BudgetExceeded:
            break
        except Exception as e:
            status, message = "parse", f"while checking postings are still open: {type(e).__name__}: {e}"
            break
    return Outcome(module.NAME, status, len(found), message), found, closed


_MARKUP = re.compile(r"&(?:[a-z]+|#\d+);|</?[a-z][^>]*>", re.I)


def degraded(rows: list[dict], stored_from_board: int = 0) -> tuple[str, ...]:
    """Signs a board's parser has gone stale, read from results already in hand (no request): no result names a company,
    titles still holding entities or HTML, or nothing returned at all from a board that has stored postings before."""
    symptoms = []
    if not rows and stored_from_board:
        symptoms.append(f"returned nothing although {stored_from_board} stored postings came from it")
    if len(rows) >= 2 and not any(r["company"] for r in rows):
        symptoms.append("no result names a company")
    if any(_MARKUP.search(r["title"]) for r in rows):
        symptoms.append("titles hold undecoded entities or HTML")
    return tuple(symptoms)


def _posting(row: dict, board: str) -> dict:
    return {"key": jobkey.make_key(row["company"], row["title"], row["url"]), "title": row["title"], "company": row["company"] or "",
            "url": row["url"], "board": board, "source": "board", "posted_date": _day(row["date"]), "deadline": _day(row["deadline"]),
            "locations": [row["location"]] if row["location"] else []}


def _day(text: str | None) -> str | None:
    found = parse_date(text)
    return found.isoformat() if found else None


def search(boards: list[str], queries, me: Path | str, *, location: str = "", recency_days: int | None = DEFAULT_RECENCY_DAYS,
           limit: int = 20, verify_open: bool = False, today: date | None = None, modules: dict | None = None,
           client_factory: Callable[..., Client] = make_client, sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.monotonic, person_acked: bool = False) -> Report:
    """Search the named boards for each query and store what is found. Refuses before any request if a board's terms are
    not acknowledged or a query is invalid. Each board gets at most MAX_REQUESTS requests, MIN_GAP seconds apart. `modules` replaces the registry (tests); `client_factory(module)` builds each client."""
    today = today or date.today()
    plan = _queries(queries, location, recency_days, limit)
    if not plan:
        raise ValueError("no query given")
    loaded = {name: (modules or {}).get(name) or module_of(name, me) for name in boards}
    for module in loaded.values():
        require_runnable(me, module, person_acked)
    stored = state.load(me)
    seen_urls = state.url_index(stored)

    def known(row: dict) -> bool:
        return jobkey.canonical_url(row["url"]) in seen_urls or jobkey.make_key(row["company"], row["title"], row["url"]) in stored

    report, merged, budget = Report(), {}, [VERIFY_LIMIT if verify_open else 0]
    with ExitStack() as stack:
        for name, module in loaded.items():
            http = _Metered(stack.enter_context(client_factory(module)), MAX_REQUESTS, RUN_SECONDS, clock, MIN_GAP, sleep)
            outcome, rows, closed = _run_board(module, plan, http, known, budget, today)
            history = sum(1 for e in stored.values() if e.get("board") == name)
            symptoms = degraded(rows, history) if outcome.status == "ok" else ()
            report.outcomes.append(Outcome(outcome.board, outcome.status, outcome.found, outcome.message, symptoms))
            report.closed += closed
            for row in rows:
                posting = _posting(row, name)
                if row.get("closed"):
                    posting["status"] = "expired"
                if posting["deadline"] and posting["deadline"] < today.isoformat():
                    posting["status"] = "expired"
                    report.closed += 1
                if posting["key"] in merged:
                    report.duplicates += 1
                    _merge(merged[posting["key"]], posting)
                else:
                    merged[posting["key"]] = {**posting, "alt_urls": []}
    if merged:
        _store(me, merged, report, today)
    return report


def _merge(into: dict, other: dict) -> None:
    """The same posting seen twice in one run (another board, or another place): one posting with every location and address."""
    into["locations"] += other["locations"]
    into["alt_urls"].append(other["url"])
    into["deadline"] = into["deadline"] or other["deadline"]
    into["posted_date"] = into["posted_date"] or other["posted_date"]
    if other.get("status") == "expired":
        into["status"] = "expired"


def _store(me: Path | str, merged: dict, report: Report, today: date) -> None:
    rows = tracker.read_rows(Path(me) / "tracker.csv")
    with state.transaction(me) as stored:
        urls = state.url_index(stored)
        for key, posting in merged.items():
            older = next((urls[u] for u in map(jobkey.canonical_url, [posting["url"], *posting["alt_urls"]]) if u in urls), key)
            target = key if key in stored else older
            if target not in stored and tracker.match_rows(rows, posting["company"], posting["title"]):
                state.upsert(stored, {**posting, "key": target, "status": "skipped", "tags": ["tracked"]}, today)
                report.tracked += 1
                continue
            before = json.dumps({**stored[target], "last_seen": None}, sort_keys=True) if target in stored else None
            entry = state.upsert(stored, {**posting, "key": target}, today)
            if len(entry["locations"]) >= MASS_POSTED_AT and MASS_POSTED not in entry["tags"]:
                entry["tags"].append(MASS_POSTED)
            if before is None:
                if entry["status"] != "expired":
                    report.new.append(entry)
            elif before != json.dumps({**entry, "last_seen": None}, sort_keys=True):
                report.updated.append(entry)


# ── health ──
@dataclass(frozen=True)
class Health:
    board: str
    status: str                  # ok, rate_limited, blocked, robots, broken
    message: str = ""
    requests: int = 0


class _Metered:
    """A client that counts its requests, stops at a limit of requests or seconds, and waits `gap` seconds between requests.
    robots.txt reads are not counted."""

    def __init__(self, client, max_requests: int, seconds: float, clock: Callable[[], float], gap: float = 0.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.client, self.max_requests, self.clock, self.gap, self.sleep = client, max_requests, clock, gap, sleep
        self.deadline, self.used = clock() + seconds, 0

    def _tick(self) -> None:
        if self.used >= self.max_requests or self.clock() > self.deadline:
            raise BudgetExceeded(f"stopped after {self.used} requests: the limit of {self.max_requests} requests or the time is used")
        if self.used and self.gap:
            self.sleep(self.gap)
        self.used += 1

    def get(self, *args, **kwargs):
        self._tick()
        return self.client.get(*args, **kwargs)

    def post_json(self, *args, **kwargs):
        self._tick()
        return self.client.post_json(*args, **kwargs)

    def json(self, *args, **kwargs):
        self._tick()
        return self.client.json(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.client, name)


def readable(text: str | None) -> bool:
    """Decoded text a person could read: something there, no entities or HTML left in it, no replacement characters."""
    return bool(text and text.strip()) and not _MARKUP.search(text) and "\ufffd" not in text


def health(module, *, client_factory: Callable[..., Client] = make_client, max_requests: int = 4, seconds: float = 30.0,
           clock: Callable[[], float] = time.monotonic, location: str | None = None, check_detail: bool = False) -> Health:
    """Is this board answering? One tiny search (limit 3), and if it comes back empty, one more with a common word. A 429 or a
    block is never evidence of breakage: it is reported as such, with no retries. `check_detail` also reads the first result's
    detail page, which must hold readable text. Only the requests and time the budget allows are used."""
    location = HEALTH_LOCATION.get(module.NAME, "") if location is None else location
    with client_factory(module, timeout=min(seconds, getattr(module, "TIMEOUT", 20.0)), retries=0) as http:
        budgeted = _Metered(http, max_requests, seconds, clock)
        try:
            for word in SENTINELS:
                rows = [r for r in (result(x) for x in module.search(Query(word, location, None, 3), budgeted)) if r]
                if rows:
                    break
            else:
                return Health(module.NAME, "broken", "two tiny searches came back empty", budgeted.used)
            if check_detail:
                posting = None
                for row in rows:
                    try:
                        posting = result(module.detail(row["id"], budgeted))
                        break
                    except BoardError as e:
                        if e.kind == "invalid":         # an external ad the board links to: its page is not the board's
                            continue
                        if e.kind != "not_found":
                            raise
                        return Health(module.NAME, "ok", "the first posting has gone, so its detail page was not checked", budgeted.used)
                else:
                    return Health(module.NAME, "ok", "every result was an external ad, so no detail page was checked", budgeted.used)
                if not posting or not readable(posting["description"]) or not readable(posting["title"]):
                    return Health(module.NAME, "broken", "a detail page did not hold readable decoded text", budgeted.used)
            return Health(module.NAME, "ok", "", budgeted.used)
        except BoardError as e:
            status = e.kind if e.kind in ("rate_limited", "blocked", "robots") else "broken"
            return Health(module.NAME, status, f"{e.kind}: {e}", budgeted.used)
        except BudgetExceeded as e:
            return Health(module.NAME, "broken", str(e), budgeted.used)


# ── the command line ──
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--me", default="me", help="the folder holding your data")
    parser.add_argument("--query", "-q", action="append", default=[], help="a search; repeat for several")
    parser.add_argument("--board", action="append", default=[], help="a board to use; default: the boards that are on")
    parser.add_argument("--location", "-l", default="")
    parser.add_argument("--recency-days", type=int, default=DEFAULT_RECENCY_DAYS)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--verify-open", action="store_true", help=f"ask each new posting's board if it is still open (at most {VERIFY_LIMIT})")
    parser.add_argument("--plan", action="store_true", help="print the searches your profile's queries call for and make no request")
    parser.add_argument("--focus", help="with --plan: a function or skill to put first, or `broad` for every function")
    parser.add_argument("--language", action="append", default=[], help="with --plan: only this declared language; repeatable")
    sub = parser.add_subparsers(dest="action")
    boards = sub.add_parser("boards", help="list, enable or disable boards")
    boards.add_argument("what", choices=("list", "enable", "disable"))
    boards.add_argument("name", nargs="?", help="a built-in board or one of your own")
    boards.add_argument("--acknowledge-terms", action="store_true", help="a board with terms: confirm personal, low-volume use")
    check = sub.add_parser("health", help="is each board answering?")
    check.add_argument("name", nargs="*", help="default: the boards that are on")
    sub.add_parser("audit", help="check the posting store's keys and URLs (reports only)")


def _boards_command(args) -> int:
    if args.what == "list":
        config = _read_config(args.me)
        for name in registry.names(args.me):
            on = is_enabled(args.me, name, config)
            terms = " (terms: acknowledge to enable)" if module_of(name, args.me).TERMS and not config.get(name, {}).get("terms_acknowledged") else ""
            say(f"{name:12} {'on ' if on else 'off'}{terms}", GREEN if on else "")
        return 0
    if not args.name:
        say(f"boards {args.what} needs a board name: {', '.join(registry.names(args.me))}", RED)
        return 1
    try:
        set_enabled(args.me, args.name, args.what == "enable", args.acknowledge_terms)
    except PermissionError as e:
        say(str(e), RED)
        return 1
    say(f"{args.name} is now {'on' if args.what == 'enable' else 'off'}")
    return 0


def _health_command(args) -> int:
    names = args.name or enabled_boards(args.me)
    for name in names:
        require_runnable(args.me, module_of(name, args.me))
    worst = 0
    for name in names:
        h = health(module_of(name, args.me), check_detail=True)
        worst = worst or int(h.status in ("broken", "blocked", "robots"))
        say(f"{name:12} {h.status}  {h.message}", GREEN if h.status == "ok" else YELLOW if h.status == "rate_limited" else RED)
    return worst


def main(args) -> int:
    try:
        return _dispatch(args)
    except (PermissionError, state.StateCorrupt, BoardsConfigCorrupt, BoardError, ValueError) as e:
        say(str(e), RED)
        return 1


def _dispatch(args) -> int:
    if args.action == "boards":
        return _boards_command(args)
    if args.action == "health":
        return _health_command(args)
    if args.action == "audit":
        report = jobkey.audit(state.load(args.me))
        say(f"{report.entries} postings; malformed keys {len(report.malformed)}, legacy keys {len(report.legacy)}, "
            f"duplicate URLs {len(report.duplicate_urls)}, keys that drifted from the current rule {report.drifted}")
        return int(report.failed)
    if getattr(args, "plan", False):
        from jobloop import queries                     # queries imports this module, so it is imported here
        return queries.main(args)
    if not args.query:
        raise ValueError("give at least one --query")
    names = args.board or enabled_boards(args.me)
    if not names:
        raise ValueError("no board is on: `boards enable NAME` (freehire is on by default)")
    report = search(names, args.query, args.me, location=args.location, recency_days=args.recency_days, limit=args.limit,
                    verify_open=args.verify_open)
    for o in report.outcomes:
        say(f"{o.board:12} {o.status}  {o.found} found  {o.message}", GREEN if o.status == "ok" else YELLOW)
        if o.symptoms:
            h = health(module_of(o.board, args.me))
            say(f"{o.board:12} looks degraded ({'; '.join(o.symptoms)}); probe says {h.status} {h.message}", YELLOW if h.status == "ok" else RED)
    say(f"{len(report.new)} new, {len(report.updated)} updated, {report.closed} closed, {report.duplicates} duplicates, "
        f"{report.tracked} already in the tracker")
    for entry in report.new:
        spread = f"posted in {len(entry['locations'])} places: " if MASS_POSTED in entry['tags'] else ""
        say(f"  {entry['key']}  {entry['title']} at {entry['company'] or '?'} ({spread}{'; '.join(entry['locations']) or '?'})")
    return int(not any(o.status == "ok" for o in report.outcomes))

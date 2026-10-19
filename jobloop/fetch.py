"""Fetching one posting's text, plain code, escalating one rung at a time, and every rung under the same rules.

    1. the board's own `detail` (its adapter, in-process)
    2. a plain fetch of the posting URL through `boards.http.Client` (honest User-Agent, robots-gated, one host, size and
       time limits)
    3. ONLY after a plain 403, and only after a robots.txt check that fails closed: one retry with ordinary browser
       headers. Never after a 429, a login wall or a challenge page: those are reported, not evaded
    4. the employer's own careers page, from candidate URLs the caller supplies (nothing is searched here); a page counts
       only if it names the job's title, so a different job is never scored

It returns `Fetched` (the text, where it came from, how the host classifies) or a typed failure: `Closed` only for
positive evidence (the page says so, a 410, a redirect to a listing page, a board's closed signal), otherwise
`Unreachable`. A 404 or a 403 is NEVER `Closed`. Whatever comes back is untrusted: HTML comments, scripts and styles are
removed and tags dropped before anything else touches it."""
from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from . import boards
from .boards.contract import BoardError
from .boards.http import Client
from .guards import hosts
from .text import flat

SOURCES = ("board", "plain", "browser-headers", "careers")
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
MIN_CHARS = 200
MAX_CAREERS = 3
LISTING_WORDS = frozenset({"jobs", "job", "careers", "career", "vacancies", "positions", "openings", "search", "stillinger",
                           "ledige-stillinger", "karriere", "jobs-search", "job-search", "all-jobs"})
CLOSED_PHRASES = re.compile(
    r"no longer (?:accepting applications|available|open)|(?:job|position|posting|vacancy|role) (?:has )?(?:been )?(?:filled|expired|closed)"
    r"|(?:job|position|posting|vacancy|role) is (?:closed|no longer)|not accepting (?:any )?applications"
    r"|stillingen er (?:ikke længere ledig|besat|lukket)|ansøgningsfristen er (?:udløbet|overskredet)|opslaget er udløbet",
    re.I)
LOGIN_WALL = re.compile(r"(?:sign|log) ?in to (?:view|see|continue|apply)|please (?:sign|log) ?in|create an account to (?:view|see)", re.I)
BOARD_CLOSED = re.compile(r"\bclosed\b|no longer accepting", re.I)
_STATUS = re.compile(r"\((\d{3})\)")


@dataclass(frozen=True)
class Fetched:
    text: str                       # untrusted posting text, comments/scripts/tags removed
    url: str                        # where it was read
    source: str                     # one of SOURCES
    status: int
    host: hosts.HostVerdict         # portal / ats / unverified, for the URL the text came from
    note: str                       # aggregator vs employer, in words
    deadline: str | None = None     # only a board's detail states one

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, not {self.source!r}")


@dataclass(frozen=True)
class Closed:
    reason: str                     # the positive evidence


@dataclass(frozen=True)
class Unreachable:
    reason: str                     # plain text; what each rung found


Outcome = Fetched | Closed | Unreachable


def clean(markup: str) -> str:
    """Posting text from a page: comments, scripts, styles and tags removed, entities decoded, whitespace folded."""
    text = re.sub(r"<!--.*?-->", " ", markup, flags=re.S)
    text = re.sub(r"<(script|style|noscript|template|svg)\b.*?</\1\s*>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<(?:br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]*>", " ", text))
    return "\n".join(line for line in (" ".join(l.split()) for l in text.splitlines()) if line)


def installed_hosts() -> tuple[str, ...]:
    """Every host the built-in boards may contact: what `classify` calls a portal."""
    return tuple(h for name in boards.BUILT_IN for h in boards.load(name).HOSTS)


def board_for(url: str) -> str:
    """The built-in board whose host (or a subdomain of it) the URL is on, else "": the board's own `detail` is tried first."""
    host, why_not = hosts.split_url(url)
    if why_not:
        return ""
    for name in boards.BUILT_IN:
        for apex in boards.load(name).HOSTS:
            apex = apex.removeprefix("www.")
            if host == apex or host.endswith("." + apex):
                return name
    return ""


def describe(verdict: hosts.HostVerdict) -> str:
    return {"portal": "aggregator: a job board, not the employer",
            "ats": "employer's applicant-tracking system (an official ATS host)",
            "unverified": "host not verified as an employer or a board"}[verdict.kind] + f" ({verdict.reason})"


def listing_redirect(original: str, final: str) -> bool:
    """Did a redirect end on a listing or home page instead of the posting? Positive evidence it is gone."""
    a, b = urlsplit(original), urlsplit(final)
    if (a.path.rstrip("/"), a.query) == (b.path.rstrip("/"), b.query):
        return False
    segments = [s for s in b.path.split("/") if s]
    return not segments or (segments[-1].lower() in LISTING_WORDS and not b.query)


def judge(text: str) -> str:
    """Why a fetched page is not a posting, or "" if it reads as one: closed (positive), a login wall, or empty."""
    if len(text) < MIN_CHARS and not CLOSED_PHRASES.search(text):
        return "unreadable: the page has almost no text"
    if CLOSED_PHRASES.search(text[:1500]):
        return "closed"
    if LOGIN_WALL.search(text[:3000]) and len(text) < 1500:
        return "login wall"
    return ""


def _status(error: BoardError, default: int = 0) -> int:
    found = _STATUS.search(str(error))
    return int(found.group(1)) if found else default


def fetch_posting(url: str, *, board: str = "", title: str = "", careers: tuple[str, ...] = (),
                  installed: tuple[str, ...] | None = None, transport=None, robots: Callable[[str], bool] | None = None,
                  sleep: Callable[[float], None] = time.sleep, acknowledged: bool = False) -> Outcome:
    """Climb the ladder for one posting and return Fetched, Closed or Unreachable (see the module docstring). `title`
    gates the careers rung; `acknowledged` waives robots only for a board whose terms allow it, as the board CLI does."""
    installed = installed_hosts() if installed is None else installed
    board = board or board_for(url)
    host, why_not = hosts.split_url(url)
    if why_not:
        return Unreachable(f"not a fetchable posting URL: {why_not}")
    notes: list[str] = []
    kit = {"transport": transport, "sleep": sleep, "retries": 2}

    def done(text: str, where: str, source: str, status: int, deadline: str | None = None) -> Fetched:
        verdict = hosts.classify(where, installed)
        return Fetched(text, where, source, status, verdict, describe(verdict), deadline)

    if board in boards.BUILT_IN:
        outcome = _board(url, board, kit, acknowledged, robots, done, notes)
        if outcome:
            return outcome
    if "#" in url:
        notes.append("the URL ends in a #fragment: it points at a listing, not one posting")
    else:
        found = _plain(url, host, kit, robots, done, notes)
        if found:
            return found
    for candidate in careers[:MAX_CAREERS]:
        found = _careers(candidate, title, kit, robots, done, notes, installed)
        if found:
            return found
    return Unreachable("; ".join(notes) or "no rung of the ladder could read the posting")


def _board(url, board, kit, acknowledged, robots, done, notes) -> Outcome | None:
    adapter = boards.load(board)
    waive = bool(adapter.TERMS and getattr(adapter, "ACK_WAIVES_ROBOTS", False) and acknowledged)
    try:
        with Client(adapter.NAME, adapter.HOSTS, robots=(lambda u: True) if waive else robots,
                    timeout=getattr(adapter, "TIMEOUT", 20.0), **kit) as http:
            found = adapter.detail(url, http)
    except BoardError as error:
        if error.kind == "not_found" and BOARD_CLOSED.search(str(error)):
            return Closed(f"the board marks it closed: {' '.join(str(error).split())[:150]}")
        notes.append(f"{board} detail: {error.kind}: {' '.join(str(error).split())[:150]}")
        return None
    text = clean("\n".join(filter(None, (f"# {found.get('title')}", f"Company: {found.get('company')}" if found.get("company") else "",
                                         f"Location: {found.get('location')}" if found.get("location") else "",
                                         found.get("description")))))
    if len(text) < MIN_CHARS:
        notes.append(f"{board} detail: almost no text")
        return None
    return done(text, found.get("url") or url, "board", 200, found.get("deadline"))


def _read(response, url, source, done, notes) -> Outcome | None:
    final = str(response.request.url)
    if listing_redirect(url, final):
        return Closed(f"the page redirected to a listing page ({final[:150]})")
    text = clean(response.text)
    problem = judge(text)
    if problem == "closed":
        return Closed(f"the page says the position is closed ({final[:150]})")
    if problem:
        notes.append(f"{source}: {problem}")
        return None
    return done(text, final, source, response.status_code)


def _plain(url, host, kit, robots, done, notes) -> Outcome | None:
    """Rung 2, and rung 3 after a plain 403 only. Returns an outcome, or None to go on to the careers page."""
    url = url.split("#", 1)[0]
    try:
        with Client("posting", (host,), robots=robots, **kit) as http:
            try:
                return _read(http.get(url, html=True), url, "plain", done, notes)
            except BoardError as error:
                if not (error.kind == "blocked" and _status(error) == 403):
                    return _failure(error, notes)
                notes.append("plain fetch: 403, the client was refused")
            if not http.allowed(url):                                   # rung 3: fails closed, once
                notes.append("browser-headers retry not made: robots.txt does not clearly allow it")
                return None
            try:
                return _read(http.get(url, headers=BROWSER_HEADERS, html=True), url, "browser-headers", done, notes)
            except BoardError as error:
                return _failure(error, notes, "browser-headers retry")
    except BoardError as error:
        return _failure(error, notes)


def _failure(error: BoardError, notes: list[str], where: str = "plain fetch") -> Outcome | None:
    status = _status(error)
    if error.kind == "not_found" and status == 410:
        return Closed("the page answered 410 Gone")
    reason = {"blocked": "refused, or a login wall or challenge page (not evaded)", "rate_limited": "rate limited (429, not evaded)",
              "not_found": f"HTTP {status or 404}, which says nothing about whether it is open",
              "robots": "robots.txt does not allow it"}.get(error.kind, f"{error.kind}: {' '.join(str(error).split())[:120]}")
    notes.append(f"{where}: {reason}")
    return None


def _careers(candidate, title, kit, robots, done, notes, installed) -> Outcome | None:
    host, why_not = hosts.split_url(candidate)
    if why_not:
        notes.append(f"careers page skipped: {why_not}")
        return None
    try:
        with Client("careers", (host,), robots=robots, **kit) as http:
            response = http.get(candidate, html=True)
    except BoardError as error:
        return _failure(error, notes, f"careers page {host}")
    text = clean(response.text)
    problem = judge(text)
    if problem or not title or flat(title) not in flat(text):
        notes.append(f"careers page {host}: " + (problem or "does not name this job"))
        return None
    return done(text, str(response.request.url), "careers", response.status_code)

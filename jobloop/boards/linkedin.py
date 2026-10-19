"""LinkedIn's public guest job pages, for PERSONAL, LOW-VOLUME use only. Run as `python -m jobloop.boards.linkedin`.

Position, stated plainly. LinkedIn's robots.txt disallows `/jobs-guest/` and its terms restrict automated access. This
adapter exists only because its owner chose to include LinkedIn for personal use. So it is off by default
(`DEFAULT_ENABLED = False`), `contract.run_cli` refuses to run until the person passes `--acknowledge-terms`, and that
acknowledgement is the ONLY thing that lets it past the robots disallow (`ACK_WAIVES_ROBOTS = True`). Without the waiver
the shared client's robots gate refuses every request here.

It never logs in and never evades a block. A 429, a challenge page or a login wall is reported as `rate_limited` or
`blocked` and is not worked around; once one has happened in a run, no further request is made. Volume is kept low: at
most MAX_PAGES search pages per search, MAX_REQUESTS requests per run, and a DELAY_SECONDS pause between requests.

A search needs a location (LinkedIn does), and `recency_days` becomes the board's time-posted facet. Search cards the page
marks as closed are dropped. `detail` raises `BoardError("not_found", "closed")` for a posting the page marks "No longer
accepting applications", and `BoardError("blocked"/"parse", ...)` for a page that shows neither a posting nor a closed
banner, because "no banner" on an empty or challenge page is not proof that a posting is open.
"""
from __future__ import annotations

import html
import re
import sys
from urllib.parse import urlparse

from . import contract
from .contract import BoardError, Query

RETRIES = 0                                    # a 429 or a challenge stops the run: no retrying around a refusal
NAME = "linkedin"
HOSTS = ("www.linkedin.com",)
DEFAULT_ENABLED = False
TERMS = ("LinkedIn's terms restrict automated access and its robots.txt disallows these pages. Personal use only: your own job "
         "search, at low volume, never for bulk or commercial use; the responsibility is yours.")
ACK_WAIVES_ROBOTS = True

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
VIEW_URL = "https://www.linkedin.com/jobs/view/{id}"
PAGE_SIZE = 10
MAX_PAGES = 3
MAX_REQUESTS = 12
DELAY_SECONDS = 1.5
ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"

CLOSED_TEXT = re.compile(r"no longer accepting applications", re.I)
CLOSED_CLASS = re.compile(r"""class=["'][^"']*\bclosed-job\b""", re.I)
DESCRIPTION_CLASS = re.compile(r"show-more-less-html__markup|description__text")
BLOCK_MARKERS = re.compile(r"authwall|sign in|join now|log ?in|http-equiv=[\"']?refresh|challenge|captcha|<script", re.I)


def clean_text(fragment: str | None) -> str | None:
    """Text of an HTML fragment: tags stripped, entities decoded once, whitespace collapsed; None if nothing is left."""
    if fragment is None:
        return None
    text = html.unescape(re.sub(r"<[^>]*>", " ", fragment)).replace("�", "")
    return " ".join(text.split()) or None


def _tag(chunk: str, name: str, css: str) -> tuple[str, str] | None:
    """The first <name> element whose class mentions `css`, as (opening tag, inner html), or None."""
    m = re.search(rf"(<{name}\b[^>]*{css}[^>]*>)(.*?)</{name}>", chunk, re.S | re.I)
    return (m.group(1), m.group(2)) if m else None


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf"""\b{name}=(?:"([^"]*)"|'([^']*)')""", tag)
    return html.unescape(m.group(1) if m.group(1) is not None else m.group(2)) if m else None


def _link(href: str | None) -> str | None:
    """An https LinkedIn url without its query string (tracking), or None."""
    parts = urlparse((href or "").split("?")[0].strip())
    host = (parts.hostname or "").lower()
    ok = parts.scheme == "https" and (host == "linkedin.com" or host.endswith(".linkedin.com")) and not parts.username
    return parts.geturl() if ok else None


def build_params(query: Query, page: int = 0) -> dict[str, str]:
    """The search request's query string. `start` is always sent; recency becomes the board's time-posted facet in seconds."""
    params = {"keywords": query.text, "location": query.location, "start": str(page * PAGE_SIZE)}
    if query.recency_days is not None:
        params["f_TPR"] = f"r{query.recency_days * 86400}"
    return params


def parse_cards(body: str) -> list[dict]:
    """The postings in a search fragment. Every card is parsed on its own, so a broken one cannot hide the others; a card
    with no id or no title is skipped, and a card the page marks closed is dropped."""
    cards = []
    for chunk in body.split("urn:li:jobPosting:")[1:]:
        m = re.match(r"\d+", chunk)
        if not m or CLOSED_CLASS.search(chunk) or CLOSED_TEXT.search(chunk):
            continue
        id_ = m.group()
        title_el, link = _tag(chunk, "h3", "base-search-card__title"), re.search(r"<a\b[^>]*base-card__full-link[^>]*>", chunk)
        sr = _tag(chunk, "span", "sr-only")
        title = clean_text(title_el[1] if title_el else None) or clean_text(sr[1] if sr else None)
        if not title:
            continue
        company_el = _tag(chunk, "h4", "base-search-card__subtitle")
        company_a = re.search(r"<a\b[^>]*>", company_el[1]) if company_el else None
        place = _tag(chunk, "span", "job-search-card__location")
        when = re.search(r"<time\b[^>]*job-search-card__listdate[^>]*>", chunk)
        cards.append({
            "id": id_, "title": title, "company": clean_text(company_el[1]) if company_el else None,
            "company_url": _link(_attr(company_a.group(), "href")) if company_a else None,
            "location": clean_text(place[1]) if place else None,
            "date": (_attr(when.group(), "datetime") or None) if when else None, "deadline": None,
            "url": _link(_attr(link.group(), "href") if link else None) or VIEW_URL.format(id=id_), "description": None})
    return cards


def extract_div(body: str) -> str | None:
    """The inner html of the description block, counting nested divs so it is not cut at the first closing one."""
    start = re.search(rf"""<div\b[^>]*class=["'][^"']*(?:{DESCRIPTION_CLASS.pattern})[^"']*["'][^>]*>""", body)
    if not start:
        return None
    depth = 1
    for tok in re.finditer(r"<div\b|</div>", body[start.end():]):
        depth += 1 if tok.group().startswith("<div") else -1
        if depth == 0:
            return body[start.end():start.end() + tok.start()]
    return body[start.end():]


def description_text(fragment: str) -> str | None:
    """A description as plain text that keeps paragraph and list boundaries as line breaks; None if it is empty."""
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"</(?:p|li|ul|ol|div|h[1-6])>", "\n", text, flags=re.I)
    lines = (clean_text(line) or "" for line in re.sub(r"<[^>]*>", "", text).split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() or None


def page_state(body: str) -> str:
    """"closed" if the top card of a detail page carries the closed banner, "no-banner" if it shows a posting without one,
    "unknown" if it shows no posting at all (empty, challenge or login page). Only the part before the description counts,
    so recruiter text that quotes the phrase or the class name cannot close a live posting."""
    if not body.strip():
        return "unknown"
    marker = DESCRIPTION_CLASS.search(body)
    head = body[:marker.start()] if marker else body
    if CLOSED_CLASS.search(head) or CLOSED_TEXT.search(head):
        return "closed"
    has_title = _tag(body, "h[12]", r"(?:top-card-layout__title|topcard__title)") is not None
    return "no-banner" if has_title or marker else "unknown"


def parse_detail(body: str, id_: str) -> dict:
    """One posting from a detail fragment: the contract's fields plus company_url, criteria and state. There is no date on
    this endpoint, and no apply url on purpose (it would only repeat the job-view url)."""
    title = _tag(body, "h[12]", r"(?:top-card-layout__title|topcard__title)")
    org = re.search(r"(<a\b[^>]*topcard__org-name-link[^>]*>)(.*?)</a>", body, re.S)
    place = _tag(body, "span", "topcard__flavor--bullet")
    block = extract_div(body)
    criteria = {}
    for li in re.findall(r"<li\b.*?</li>", body, re.S):
        label, value = _tag(li, "h3", "description__job-criteria-subheader"), _tag(li, "span", "description__job-criteria-text")
        if label and value:
            criteria[(clean_text(label[1]) or "").lower()] = clean_text(value[1])
    return {"id": id_, "title": (clean_text(title[1]) if title else None) or "(untitled)",
            "company": clean_text(org.group(2)) if org else None, "company_url": _link(_attr(org.group(1), "href")) if org else None,
            "location": clean_text(place[1]) if place else None, "date": None, "deadline": None, "url": VIEW_URL.format(id=id_),
            "description": description_text(block) if block else None,
            "criteria": {k: criteria.get(k) for k in ("seniority level", "employment type", "job function", "industries")},
            "state": page_state(body)}


def normalize_id(ref: str) -> str:
    """The posting number in a bare id, a jobPosting urn, a slug ending in the id, or a job-view url on linkedin.com. Any
    other host, look-alike host, userinfo trick or non-job linkedin url is refused, before any request."""
    ref = ref.strip()
    if m := re.fullmatch(r"(?:urn:li:jobPosting:)?(\d{6,})", ref) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*-(\d{6,})", ref):
        return m.group(1)
    parts = urlparse(ref if "://" in ref else "https://" + ref)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (parts.scheme in ("http", "https") and not parts.username and not parts.password and port is None
            and (host == "linkedin.com" or host.endswith(".linkedin.com"))):
        if m := re.fullmatch(r"/jobs/view/(?:|[^/]*-)(\d{6,})/?", parts.path):
            return m.group(1)
    raise BoardError("invalid", f"not a LinkedIn job id or job-view url: {ref[:80]!r}")


def _get(http, url: str, params: dict | None = None) -> str:
    """One polite, capped request. After a block or rate limit in this run nothing more is sent."""
    if getattr(http, "linkedin_stopped", None):
        raise http.linkedin_stopped
    sent = getattr(http, "linkedin_requests", 0)
    if sent >= MAX_REQUESTS:
        raise BoardError("rate_limited", f"linkedin: stopped after {MAX_REQUESTS} requests in one run, to keep volume low")
    if sent:
        http.sleep(DELAY_SECONDS)
    http.linkedin_requests = sent + 1
    try:
        return http.get(url, params=params, headers={"Accept": ACCEPT}, html=True).text
    except BoardError as e:
        if e.kind in ("blocked", "rate_limited"):
            http.linkedin_stopped = e
        raise


def _refuse_unexpected_page(body: str) -> None:
    kind = "blocked" if BLOCK_MARKERS.search(body) else "parse"
    raise BoardError(kind, "linkedin sent a page with no postings that looks like a login or challenge page; not worked around"
                     if kind == "blocked" else "linkedin sent a page in an unexpected shape")


def search(query: Query, http) -> list[dict]:
    """Postings for a query: at most MAX_PAGES pages of 10, stopping early on an empty page or once `limit` is reached."""
    if not query.location.strip():
        raise BoardError("invalid", "linkedin needs a location (for example --location 'Berlin, Germany' or 'Remote')")
    found: list[dict] = []
    for page in range(min(MAX_PAGES, -(-query.limit // PAGE_SIZE))):
        body = _get(http, SEARCH_URL, build_params(query, page))
        if body.strip() and "urn:li:jobPosting:" not in body and BLOCK_MARKERS.search(body):
            _refuse_unexpected_page(body)
        cards = [c for c in parse_cards(body) if all(c["id"] != f["id"] for f in found)]
        found += cards
        if not cards or len(found) >= query.limit:
            break
    return found[:query.limit]


def detail(ref: str, http) -> dict:
    """One posting by id or url. A closed posting raises BoardError("not_found", "closed"); a page showing no posting raises."""
    id_ = normalize_id(ref)
    body = _get(http, DETAIL_URL.format(id=id_))
    state = page_state(body)
    if state == "closed":
        raise BoardError("not_found", "closed")
    if state == "unknown":
        _refuse_unexpected_page(body)
    posting = parse_detail(body, id_)
    return {f: posting[f] for f in contract.FIELDS}


if __name__ == "__main__":
    sys.exit(contract.run_cli(sys.modules[__name__]))

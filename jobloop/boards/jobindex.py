"""Jobindex (jobindex.dk), the Danish job board. Off by default: it is opt-in for a Danish job search.

Search reads the one page `/jobsoegning?q=...` (the `var Stash` JSON in its HTML) and falls back to the RSS feed only
when robots.txt refuses the HTML page. Jobindex's robots.txt disallows paging, sorting, the age filter and queries of
three or more words, so none of those is ever requested: at most two words go in `q`, any further words, the recency
window, the sort (newest first) and the limit are applied here, on the one page of 20 postings the board returns.
`detail` fetches `/jobannonce/<id>` and reads whichever of three page shapes it gets. The click-through `/c?` link is
never fetched. Archived and past-deadline postings are dropped from search; a deadline is the Danish calendar date.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .contract import BoardError, Query, parse_date, recent, run_cli
from .http import Client

NAME = "jobindex"
HOSTS = ("www.jobindex.dk",)
DEFAULT_ENABLED = False
TERMS = ""
ACK_WAIVES_ROBOTS = False

BASE = "https://www.jobindex.dk"
PAGE_SIZE = 20
MAX_QUERY_WORDS = 2
COPENHAGEN = ZoneInfo("Europe/Copenhagen")
ID = re.compile(r"[A-Za-z]\d+")
MONTHS = {m: i for i, names in enumerate(
    ("januar jan", "februar feb", "marts march mar", "april apr", "maj may", "juni june jun", "juli july jul",
     "august aug", "september sep sept", "oktober october okt oct", "november nov", "december dec"), 1) for m in names.split()}
LABEL = r"(?:Ansøgningsfrist|Application due|Application deadline|Deadline|Frist)"
DATE = (r"(\d{4}-\d{2}-\d{2}|\d{1,2}[-./]\d{1,2}[-./]\d{4}|\d{1,2}\.?\s+[A-Za-zæøåÆØÅ]+\.?\s+\d{4})")
RATED = re.compile(r"have rated\s+(.+?)\s+on Jobindex", re.I)


def today() -> date:
    return datetime.now(COPENHAGEN).date()


def danish_date(text: str | None) -> str | None:
    """The Danish calendar date of an ISO date or timestamp. A UTC 'Z' stamp is converted (23:00:00Z is already tomorrow
    in Denmark), never sliced; a plain date stays as it is."""
    if not text:
        return None
    text = text.strip()
    if len(text) <= 10:
        return d.isoformat() if (d := parse_date(text)) else None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(COPENHAGEN).date().isoformat()


def to_iso_date(text: str | None) -> str | None:
    """YYYY-MM-DD from an ISO date, DD-MM-YYYY (also . or /) or a long form such as '13. september 2026'; else None."""
    if not text:
        return None
    text = text.strip()
    try:
        if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?", text):
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        if m := re.fullmatch(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", text):
            return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
        if (m := re.fullmatch(r"(\d{1,2})\.?\s+([A-Za-zæøåÆØÅ]+)\.?\s+(\d{4})", text)) and m[2].lower() in MONTHS:
            return date(int(m[3]), MONTHS[m[2].lower()], int(m[1])).isoformat()
    except ValueError:
        return None
    return None


def job_url(job_id: str) -> str:
    return f"{BASE}/jobannonce/{job_id}"


def build_url(ref: str) -> tuple[str, str]:
    """(id, canonical url) for a posting id or a jobindex url. Anything else is refused before a request is made, and the
    raw input is never fetched: the url is rebuilt from the id."""
    ref = (ref or "").strip()
    if not ref:
        raise BoardError("invalid", "detail needs a posting id or a jobindex.dk/jobannonce url")
    if ID.fullmatch(ref):
        return ref, job_url(ref)
    parts = urlparse(ref)
    host = (parts.hostname or "").lower()
    if (parts.scheme in ("http", "https") and (host == "jobindex.dk" or host.endswith(".jobindex.dk"))
            and not parts.username and not parts.password
            and (m := re.match(r"/(?:jobannonce|vis-job)/([A-Za-z]\d+)(?:/|$)", parts.path))):
        return m[1], job_url(m[1])
    raise BoardError("invalid", f"not a jobindex posting id or url (like h1699736): {ref[:80]!r}")


def parse_stash(page: str) -> dict:
    """The search response inside `var Stash = {...}`, found at any depth. A page without it is an error, never zero
    results: that is a changed layout or a block page."""
    m = re.search(r"var\s+Stash\s*=\s*", page)
    if not m:
        raise BoardError("parse", "jobindex search page has no Stash data: the layout changed or the page is a block page")
    try:
        stash, _ = json.JSONDecoder().raw_decode(page, m.end())
    except ValueError:
        raise BoardError("parse", "jobindex Stash data is cut off or not JSON") from None

    def find(node: object) -> dict | None:
        if isinstance(node, dict):
            if isinstance(node.get("searchResponse"), dict) and isinstance(node["searchResponse"].get("results"), list):
                return node["searchResponse"]
            return next((f for v in node.values() if (f := find(v))), None)
        if isinstance(node, list):
            return next((f for v in node if (f := find(v))), None)
        return None

    found = find(stash)
    if found is None:
        raise BoardError("parse", "jobindex Stash data has no searchResponse.results: the layout changed")
    return found


def strip_tags(markup: str) -> str:
    return " ".join(htmllib.unescape(re.sub(r"<[^>]*>", " ", markup or "")).split())


def posting_from_stash(raw: dict) -> dict:
    """One Stash search result in the contract's shape. ASAP means no deadline and wins over any date."""
    tid = raw.get("tid") if isinstance(raw.get("tid"), str) else ""
    company = raw.get("company") if isinstance(raw.get("company"), dict) else {}
    features = (raw.get("geojson") or {}).get("features") if isinstance(raw.get("geojson"), dict) else None
    feature_title = features[0].get("properties", {}).get("title") if features and isinstance(features[0], dict) else None
    if raw.get("apply_deadline_asap"):
        deadline = None
    elif isinstance(raw.get("apply_deadline"), str):
        deadline = danish_date(raw["apply_deadline"])
    else:
        deadline = danish_date(raw.get("lastdate")) if isinstance(raw.get("lastdate"), str) else None
    return {"id": tid, "title": raw.get("headline"), "company": company.get("name") or raw.get("companytext"),
            "location": raw.get("area") or feature_title, "date": raw.get("firstdate"), "deadline": deadline,
            "url": job_url(tid) if tid else raw.get("share_url") or raw.get("url"), "description": None,
            "_archived": bool(raw.get("is_archived")), "_text": strip_tags(raw.get("html") or "")}


def postings_from_rss(feed: bytes) -> list[dict]:
    """The postings in the RSS feed. It has no deadline, and the area lives in the escaped card in each description."""
    try:
        items = ET.fromstring(feed).findall("./channel/item")
    except ET.ParseError:
        raise BoardError("parse", "jobindex RSS feed is not XML") from None
    out = []
    for item in items:
        def text(tag: str) -> str:
            return (item.findtext(tag) or "").strip()

        card = text("description")
        link = re.search(r"/vis-job/([A-Za-z]\d+)", text("link"))
        h4 = re.search(r"<h4>(.*?)</h4>", card, re.S | re.I)
        area = re.search(r'jix_robotjob--area">([^<]*)<', card)
        headline, title = (strip_tags(h4[1]) if h4 else ""), " ".join(text("title").split())
        company = title[len(headline) + 2:] if headline and title.startswith(headline + ", ") else None
        try:
            published = parsedate_to_datetime(text("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            published = None
        out.append({"id": link[1] if link else "", "title": headline or title, "company": company or None,
                    "location": area[1].strip() if area else None, "date": published, "deadline": None,
                    "url": job_url(link[1]) if link else None, "description": None, "_archived": False,
                    "_text": strip_tags(card)})
    return out


def search_terms(query: Query) -> tuple[str, list[str], bool]:
    """(the `q` to send, the words that could not be sent, whether the location rides in q). `q` holds at most two words
    because robots.txt disallows more; the location joins it only if it fits."""
    words, place = query.text.split(), query.location.split()
    if len(words) + len(place) <= MAX_QUERY_WORDS:
        return " ".join(words + place), [], True
    return " ".join(words[:MAX_QUERY_WORDS]), words[MAX_QUERY_WORDS:], False


def search(query: Query, http: Client) -> list[dict]:
    """Up to 20 current postings, newest first. Everything robots.txt does not allow us to ask for is done here."""
    q, extra, in_q = search_terms(query)
    try:
        page = http.get(f"{BASE}/jobsoegning", params={"q": q}, html=True).text
        found = [posting_from_stash(r) for r in parse_stash(page)["results"] if isinstance(r, dict)]
    except BoardError as e:
        if e.kind != "robots":
            raise
        found = postings_from_rss(http.get(f"{BASE}/jobsoegning.rss", params={"q": q}, html=True).content)
    place = "" if in_q else query.location.lower()
    kept = []
    for p in found:
        text = " ".join(str(p.get(k) or "") for k in ("title", "company", "location", "_text")).lower()
        expired = p["_archived"] or (p["deadline"] is not None and p["deadline"] < today().isoformat())
        if expired or not all(w.lower() in text for w in extra) or (place and p["location"] and place not in p["location"].lower()):
            continue
        kept.append({k: v for k, v in p.items() if not k.startswith("_")})
    newest_first = sorted(kept, key=lambda p: p["date"] or "", reverse=True)
    return recent(newest_first, query.recency_days, today())[: min(query.limit, PAGE_SIZE)]


BLOCKS = {"p", "div", "li", "ul", "ol", "br", "tr", "table", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}
VOID = {"br", "img", "meta", "link", "input", "hr", "source", "area", "base", "col", "wbr"}
HIDDEN = {"head", "script", "style", "template", "noscript", "svg"}
FULL = {"jd-description", "job-text", "location"}
FACTS = ("jd-location", "jd-deadline", "jd-type", "jd-workhours", "jd-description", "job-text", "location")


class Page(HTMLParser):
    """What a posting page holds, in one pass: meta tags (attributes in any order), title, first h1, first time element,
    the `jd-*` fact blocks, the job text, the workplace span, and the visible text with comments, scripts and styles left out."""

    def __init__(self, markup: str):
        super().__init__(convert_charrefs=True)
        self.meta, self.title, self.h1, self.time = {}, "", "", None
        self.facts: dict[str, list[str]] = {}
        self.workplace: str | None = None
        self.body: list[str] = []
        self._hidden = 0
        self._open: list[tuple[str, str | None]] = []
        self._in: set[str] = set()
        self._in_title = False
        self._workplace_seen = False
        self._p_done: set[str] = set()
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        classes = a.get("class", "").split()
        if tag == "meta":
            for key in ("property", "name", "itemprop"):
                if key in a and "content" in a:
                    self.meta.setdefault(a[key].lower(), a["content"])
        if tag == "time" and self.time is None and a.get("datetime"):
            self.time = a["datetime"]
        if tag in HIDDEN:
            self._hidden += 1
        if tag not in VOID:
            fact = next((c for c in classes if c in FACTS and c not in self.facts), None)
            self._open.append((tag, fact))
            if fact:
                self.facts.setdefault(fact, [])
                self._in.add(fact)
            if any(c.startswith("workplace") for c in classes):
                self._workplace_seen = True
            if tag == "span" and "empty" in classes and self._workplace_seen and self.workplace is None:
                self._open[-1] = (tag, "workplace")
                self._in.add("workplace")
        if tag in BLOCKS and not self._hidden:
            self._emit("\n")
            if tag == "li":
                self._emit("- ")
        self._in_title = tag == "title"

    def handle_endtag(self, tag):
        if tag in HIDDEN and self._hidden:
            self._hidden -= 1
        while self._open and tag not in VOID:
            top, fact = self._open.pop()
            if fact:
                self._in.discard(fact)
            if top == tag:
                break
        if tag in BLOCKS and not self._hidden:
            self._emit("\n")
        self._in_title = False
        if tag == "p":
            self._p_done.update(self._in - FULL)

    def handle_data(self, data):
        if self._in_title:
            self.title += " ".join(data.split())
        if self._hidden:
            return
        if any(t == "h1" for t, _ in self._open) and not self.h1:
            self.h1 = " ".join(data.split())
        self._emit(data)

    def _emit(self, text: str):
        self.body.append(text)
        for fact in self._in:
            if fact == "workplace":
                if text.strip() and self.workplace is None:
                    self.workplace = " ".join(text.split())
                continue
            if fact in FULL or (fact not in self._p_done and any(t == "p" for t, _ in self._open)):
                self.facts[fact].append(text)


def visible(parts: list[str]) -> str:
    """Readable text: one space inside a line, one line break between blocks, no blank runs."""
    lines = (" ".join(line.split()) for line in "".join(parts).split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def fact(page: Page, key: str) -> str | None:
    return visible(page.facts.get(key, [])) or None


def find_deadline(text: str) -> str | None:
    """The date within 300 characters after a deadline label in the visible text, as YYYY-MM-DD."""
    for m in re.finditer(LABEL, text, re.I):
        if (d := re.search(DATE, text[m.end(): m.end() + 300])) and (iso := to_iso_date(d[1])):
            return iso
    return None


def parse_detail(markup: str, job_id: str) -> dict:
    """A posting page in the contract's shape. id and url are always the jobindex ones, never the page's og:url (which on
    a passthrough page is the employer's ad system). Company is never an ad system's brand."""
    page = Page(markup)
    meta = page.meta
    title = meta.get("og:title") or meta.get("name") or page.h1 or None
    if not title:
        raise BoardError("parse", f"jobindex posting {job_id} has no title: the layout changed")
    title = " ".join(htmllib.unescape(title).split())
    text = visible(page.body)
    company = None
    if " - " in page.title:
        prefix, _, suffix = page.title.partition(" - ")
        company = prefix.strip() if suffix.strip() == title and prefix.strip() else None
    if company is None and (m := RATED.search(" ".join(text.split()))):
        company = m[1]
    description = fact(page, "jd-description") or fact(page, "job-text") or text
    summary = meta.get("og:description") or meta.get("description") or ""
    if len(description) < 100 and len(summary.strip()) > len(description):
        description = summary.strip()
    return {"id": job_id, "title": title, "company": company,
            "location": fact(page, "jd-location") or page.workplace or fact(page, "location"),
            "date": danish_date(page.time) if page.time else None,
            "deadline": to_iso_date(fact(page, "jd-deadline")) or find_deadline(text),
            "url": job_url(job_id), "description": description or None}


def detail(ref: str, http: Client) -> dict:
    """One posting by id or url. A page that does not mention the id (a removed ad answered by a search page) is
    not_found, not a made-up posting."""
    job_id, url = build_url(ref)
    markup = http.get(url, html=True).text
    if job_id not in markup:
        raise BoardError("not_found", f"jobindex has no live posting {job_id}: it was removed or has expired")
    return parse_detail(markup, job_id)


if __name__ == "__main__":
    sys.exit(run_cli(sys.modules[__name__]))

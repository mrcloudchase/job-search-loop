"""Jobdanmark (jobdanmark.dk), a Danish job board. Off by default: a person opts in when their market is Denmark.

`search` asks the board's own search API (a JSON POST, 30 postings a page; the board has no recency filter, so recency
is filtered here), `detail` reads the posting's HTML page, which today carries no JSON-LD, so the rendered layout is the
main path. The lookups (`autocomplete`, `locations`, `categories`) find the ids and names the API's filters need.
Promises: every request goes through `boards.http.Client` (honest User-Agent, https on jobdanmark.dk only, robots.txt
checked before every request, backoff); a filterless search, which would return the whole database, is refused; a slug
the board no longer has is "not_found" however the board answers (it 301-redirects to a generic search page with HTTP
200, so the page the answer ends on is checked, not the status); everything read from the board is untrusted text.
Run it as `python -m jobloop.boards.jobdanmark search --query ...`.
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from . import contract
from .contract import BoardError, Query
from .http import CHALLENGE, Client

NAME = "jobdanmark"
HOSTS = ("jobdanmark.dk",)
DEFAULT_ENABLED = False
TERMS = ""
ACK_WAIVES_ROBOTS = False

BASE = "https://jobdanmark.dk"
JOB_TYPES = ("fuldtid", "deltid", "fleksjob", "elev", "studiejob", "praktik")
MAX_PAGES = 5
NOT_FOUND_PHRASES = ("page not found", "siden blev ikke fundet")
BLOCKS = frozenset({"p", "li", "ul", "ol", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section"})
VOID = frozenset({"br", "img", "meta", "link", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "param", "track"})
GROUP_FILTERS = {"kommune": "municipality", "postnummer": "zip_code", "region": "region"}


def clean_text(text: str) -> str:
    """Text without the stray malformed closing tags the board leaves in its pages (`</ li >`). Opening tags and
    angle brackets that are author text (`<T>`, `x < y`) stay."""
    return re.sub(r"<\s*/\s*[A-Za-z][A-Za-z0-9]*\s*>", "", text)


def to_contract_date(text: object) -> str | None:
    """`DD-MM-YYYY` (optionally followed by a time such as `22.59`) as `YYYY-MM-DD`; anything else, including the free
    text "Løbende" (rolling), is None."""
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})(?:\s+\d{1,2}[.:]\d{2})?", str(text or "").strip())
    if not m:
        return None
    iso = f"{m[3]}-{m[2]}-{m[1]}"
    return iso if contract.parse_date(iso) else None


def extract_city(address: object) -> str | None:
    """The city after the postcode of an address ("Lautruphøj 2, 2750 Ballerup", "2670, Greve"); None if there is no
    postcode. The last postcode wins, so a four-digit street number is never taken for it."""
    found = list(re.finditer(r"(?<!\d)\d{4}(?!\d)(?:\s*,\s*|\s+)(?![\d\s])(.+)$", clean_text(str(address or ""))))
    return " ".join(found[-1][1].split()) or None if found else None


def normalize_slug(ref: str) -> str:
    """The slug of a posting from a bare slug, a `/job/<slug>` path or a jobdanmark.dk url; anything else is refused."""
    ref = (ref or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", ref):
        return ref
    parts = urlparse(ref)
    if parts.scheme in ("", "https") and (parts.hostname or "jobdanmark.dk").lower() == "jobdanmark.dk" and not parts.username:
        m = re.fullmatch(r"/job/([A-Za-z0-9_-]+)/?", parts.path)
        if m:
            return m[1]
    raise BoardError("invalid", f"not a Jobdanmark posting id or url: {ref[:80]!r}; give the id search printed or https://jobdanmark.dk/job/<id>")


# --- requests ---------------------------------------------------------------------------------------------------------

def _post_json(http: Client, url: str, body: dict) -> object:
    """A JSON POST under the Client's rules (hosts, robots, backoff, blocked/not-found mapping). The shared Client only
    has GET, so this repeats its policy for POST; it goes when the Client grows a POST."""
    http._check(url)
    if not http.allowed(url):
        raise BoardError("robots", f"robots.txt does not allow {NAME} to fetch {url[:100]}")
    for attempt in range(http.retries + 1):
        try:
            reply = http.http.post(url, json=body)
        except httpx.TimeoutException:
            raise BoardError("network", f"{NAME} did not answer in time") from None
        except httpx.HTTPError as e:
            raise BoardError("network", f"{NAME}: {type(e).__name__}: {e}") from None
        if reply.status_code != 429 and reply.status_code < 500:
            break
        if attempt == http.retries:
            raise BoardError("rate_limited" if reply.status_code == 429 else "network",
                             f"{NAME} answered {reply.status_code} after {attempt + 1} tries")
        http.sleep(http._delay(attempt, reply.headers.get("retry-after")))
    status, ctype = reply.status_code, reply.headers.get("content-type", "")
    if status in (401, 403) or (status == 200 and "html" in ctype and CHALLENGE.search(reply.text[:3000])):
        raise BoardError("blocked", f"{NAME} refused the request ({status}); it is not retried or worked around")
    if status in (404, 410):
        raise BoardError("not_found", f"{NAME} has no such page ({status})")
    if status >= 300 or len(reply.content) > http.max_bytes:
        raise BoardError("invalid", f"{NAME} answered {status}")
    try:
        return reply.json()
    except ValueError:
        raise BoardError("parse", f"{NAME} did not send JSON") from None


def _lookup(http: Client, path: str, **params: str) -> list:
    """A GET of one of the board's JSON lookups; robots.txt is checked for it like for a page."""
    data = http.json(f"{BASE}{path}", params=params or None, html=True)
    if not isinstance(data, list):
        raise BoardError("parse", f"{NAME} {path} did not send a list")
    return data


# --- search -----------------------------------------------------------------------------------------------------------

def build_filters(text: str = "", category: int | None = None, jobtitle: int | None = None, municipality: str = "",
                  zip_code: str = "", region: str = "") -> list[dict]:
    """The API's filters in its order. Category and jobtitle values are integers, the rest strings, and every filter
    has the `displayText` the server insists on."""
    out: list[dict] = []
    for kind, value in (("freetext", text), ("category", category), ("jobtitle", jobtitle), ("municipality", municipality),
                        ("zip", zip_code), ("region", region)):
        if value is None or value == "":
            continue
        out.append({"type": kind, "value": value if kind in ("category", "jobtitle") else str(value), "displayText": str(value)})
    return out


def build_body(filters: list[dict], job_types: str | list[str] = ()) -> dict:
    """The search request body. Job types (comma-separated text or a list) are validated. With neither filters nor job
    types the server would answer with its whole database, so that is refused."""
    if isinstance(job_types, str):
        job_types = job_types.split(",")
    types = [t.strip().lower() for t in job_types if t.strip()]
    bad = [t for t in types if t not in JOB_TYPES]
    if bad:
        raise BoardError("invalid", f"unknown Jobdanmark job type {bad[0]!r}; use {', '.join(JOB_TYPES)}")
    if not filters and not types:
        raise BoardError("invalid", f"a {NAME} search with no filter returns the whole database; give a query")
    return {"jobTypes": types, "filters": filters, "locationMode": "Text", "distance": 50}


def normalise(item: object) -> dict | None:
    """One search item in the contract's shape, or None if it is not a posting on jobdanmark.dk. Only the contract's
    fields survive; the board's logos and covers are dropped."""
    if not isinstance(item, dict) or not isinstance(item.get("url"), str):
        return None
    parts = urlparse(item["url"].strip())
    m = re.fullmatch(r"/job/([^/?#]+)/?", parts.path)
    if not m or (parts.netloc and (parts.scheme != "https" or parts.hostname != "jobdanmark.dk")):
        return None
    return {"id": m[1], "title": item.get("title"), "company": item.get("companyName"),
            "location": extract_city(item.get("companyAddress")), "date": to_contract_date(item.get("publishedDate")),
            "deadline": to_contract_date(item.get("applicationDeadline")), "url": f"{BASE}/job/{m[1]}", "description": None}


def search_page(http: Client, body: dict, page: int = 1) -> dict:
    """One page (1-indexed, 30 postings) as {"meta": {currentPage, totalItems, itemsPrPage, totalPages}, "items": [...]}."""
    if page < 1:
        raise BoardError("invalid", f"pages start at 1, not {page}")
    data = _post_json(http, f"{BASE}/api/jobsearch/search/{page}", body)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise BoardError("parse", f"{NAME} search did not send items")
    meta = {k: data.get(k) for k in ("currentPage", "totalItems", "itemsPrPage", "totalPages")}
    return {"meta": meta, "items": [r for r in map(normalise, data["items"]) if r]}


def location_filter(http: Client, place: str) -> dict:
    """The `build_filters` keywords for a place name or postcode: a postcode is used as it is; a name is looked up and
    its exact municipality, else the first municipality, postal district or region the board offers. No match is an error."""
    place = " ".join(place.split())
    if re.fullmatch(r"\d{4}", place):
        return {"zip_code": place}
    wanted = place.casefold()
    best: tuple[int, dict] | None = None
    for group in locations(http, place):
        kind = GROUP_FILTERS.get(str(group.get("title", "")).casefold())
        for item in group["items"] if kind else ():
            value = item.get("value") if isinstance(item, dict) else None
            if value is None:
                continue
            rank = 0 if kind == "municipality" and str(value).casefold() == wanted else 1 if kind == "municipality" else 2
            if best is None or rank < best[0]:
                best = (rank, {kind: str(value)})
    if best is None:
        raise BoardError("invalid", f"{NAME} knows no place called {place!r}; try a municipality name or a postcode")
    return best[1]


def search(query: Query, http: Client) -> list[dict]:
    """Postings matching the query text (and place), newest pages first as the board orders them, paged until `limit`
    postings dated within the recency window are found or the results end."""
    filters = build_filters(text=query.text, **(location_filter(http, query.location) if query.location else {}))
    found: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        got = search_page(http, build_body(filters), page)
        found += contract.recent(got["items"], query.recency_days)
        if len(found) >= query.limit or page >= (got["meta"]["totalPages"] or 0):
            break
    return found[: query.limit]


# --- lookups ----------------------------------------------------------------------------------------------------------

def _spread(groups: list[dict], limit: int | None) -> list[dict]:
    """Groups with items only, `limit` items in all, filling the first group before the next."""
    out = []
    for group in groups:
        items = list(group["items"])[: limit if limit is None else max(limit, 0)]
        if limit is not None:
            limit -= len(items)
        if items:
            out.append({**group, "items": items})
    return out


def filter_autocomplete(groups: list, query: str) -> list[dict]:
    """Autocomplete groups reduced to items whose text contains the query (the API answers with every category
    whatever was asked); items without text are skipped, a group without items is tolerated, empty groups go."""
    needle, out = query.casefold(), []
    for group in groups:
        if not isinstance(group, dict):
            continue
        items = [i for i in group.get("items") or [] if isinstance(i, dict) and isinstance(i.get("text"), str) and needle in i["text"].casefold()]
        if items:
            out.append({**group, "items": items})
    return out


def _required(query: str, what: str) -> str:
    query = " ".join((query or "").split())
    if not query:
        raise BoardError("invalid", f"{what} needs a query")
    return query


def autocomplete(http: Client, query: str, limit: int | None = None) -> list[dict]:
    """Job titles and categories matching `query`, as groups {title, items: [{id, text, value, category, slug}]}."""
    query = _required(query, "autocomplete")
    return _spread(filter_autocomplete(_lookup(http, "/api/search/autocomplete", q=query), query), limit)


def locations(http: Client, query: str, limit: int | None = None) -> list[dict]:
    """Municipalities and postal districts matching `query`; an item's `value` is what the municipality or zip filter takes."""
    query = _required(query, "locations")
    groups = [g for g in _lookup(http, "/api/search/locations", q=query) if isinstance(g, dict) and isinstance(g.get("items"), list)]
    return _spread(groups, limit)


def categories(http: Client, limit: int | None = None) -> list[dict]:
    """The categories with their ids and live counts: [{id, title, helpText, count}]."""
    return [c for c in _lookup(http, "/api/categorycount/getcounts") if isinstance(c, dict)][:limit]


# --- detail -----------------------------------------------------------------------------------------------------------

class _Page(HTMLParser):
    """What a posting page holds: JSON-LD scripts, the page's own address, the posting's title, company, overview list
    and description lines. `whole=True` reads a fragment (a JSON-LD description) as all description."""

    def __init__(self, whole: bool = False):
        super().__init__(convert_charrefs=True)
        self.whole, self.stack, self.caps, self.found = whole, [], [], {}
        self.overview, self.ld, self.lines, self.text = [], [], [], []
        self.destination, self.has_details, self._line, self._raw, self._ld = None, False, [], None, None

    def _open_in(self, cls: str) -> bool:
        return any(cls in classes for _, classes in self.stack)

    def _region(self) -> bool:
        return self.whole or (self._open_in("job-list-details") and not self._open_in("job-details-head"))

    def _break(self) -> None:
        line = " ".join(clean_text("".join(self._line)).split())
        if line:
            self.lines.append(line)
        self._line = []

    def _capture(self, key: str) -> None:
        if key == "overview" or (key not in self.found and not any(c[0] == key for c in self.caps)):
            self.caps.append([key, len(self.stack), []])

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "meta" and a.get("property") == "og:url" and not self.destination:
            self.destination = a.get("content")
        if tag == "link" and "canonical" in a.get("rel", "").split():
            self.destination = a.get("href")
        if tag in ("script", "style"):
            self._raw = tag
            self._ld = [] if tag == "script" and a.get("type", "").lower() == "application/ld+json" else None
            return
        if tag == "br" and self._region():
            self._break()
        if tag in VOID:
            return
        self.stack.append((tag, frozenset(a.get("class", "").split())))
        classes, head = self.stack[-1][1], self._open_in("job-details-head")
        if "job-list-details" in classes:
            self.has_details = True
        if tag == "li" and any(t == "ul" and "job-overview" in c for t, c in self.stack):
            self._capture("overview")
        if self._open_in("job-list-details") and "title" in classes:
            self._capture("title")
        if head and tag == "a" and a.get("href", "").startswith("/virksomheder/"):
            self._capture("company_link")
        if head and tag == "strong":
            self._capture("company_strong")
        if tag in ("h1", "h3"):
            self._capture(tag)
        if tag == "title":
            self._capture("doc_title")
        if tag in BLOCKS and self._region():
            self._break()

    def handle_endtag(self, tag):
        if tag == self._raw:
            if self._ld is not None:
                self.ld.append("".join(self._ld))
            self._raw, self._ld = None, None
            return
        tags = [t for t, _ in self.stack]
        if tag in VOID or tag not in tags:
            return
        if tag in BLOCKS and self._region():
            self._break()
        del self.stack[len(tags) - 1 - tags[::-1].index(tag):]
        for cap in [c for c in self.caps if c[1] > len(self.stack)]:
            self.caps.remove(cap)
            text = " ".join(clean_text("".join(cap[2])).split())
            if cap[0] == "overview":
                self.overview.append(text)
            else:
                self.found.setdefault(cap[0], text)

    def handle_data(self, data):
        if self._raw:
            if self._ld is not None:
                self._ld.append(data)
            return
        self.text.append(data)
        for cap in self.caps:
            cap[2].append(data)
        if self._region():
            self._line.append(data)

    def read(self, html: str) -> "_Page":
        self.feed(html)
        self.close()
        self._break()
        return self


def render_description(fragment: object) -> str | None:
    """A description as plain text. HTML becomes one line per block element with entities decoded; plain text is kept as is."""
    text = str(fragment or "").strip()
    if not re.search(r"<\s*/?\s*[A-Za-z]|&[#\w]+;", text):
        return clean_text(text) or None
    return "\n".join(_Page(whole=True).read(text).lines) or None


def _job_posting(page: _Page) -> dict | None:
    """The first schema.org JobPosting among the page's JSON-LD scripts (alone or in an array); broken scripts are skipped."""
    for script in page.ld:
        try:
            data = json.loads(script)
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            kinds = node.get("@type") if isinstance(node, dict) else None
            if kinds == "JobPosting" or (isinstance(kinds, list) and "JobPosting" in kinds):
                return node
    return None


def _iso(value: object) -> str | None:
    d = contract.parse_date(str(value) if value else None)
    return d.isoformat() if d else None


def _from_json_ld(node: dict, slug: str) -> dict:
    org = node.get("hiringOrganization")
    place = node.get("jobLocation")
    place = place[0] if isinstance(place, list) and place else place
    address = place.get("address") if isinstance(place, dict) else None
    return {"id": slug, "title": clean_text(str(node.get("title") or "")) or None,
            "company": org.get("name") if isinstance(org, dict) else None,
            "location": address.get("addressLocality") if isinstance(address, dict) else None,
            "date": _iso(node.get("datePosted")), "deadline": _iso(node.get("validThrough")),
            "url": f"{BASE}/job/{slug}", "description": render_description(node.get("description"))}


def _from_layout(page: _Page, slug: str) -> dict:
    """A posting from the rendered layout: title, company, the overview list (Udgivet, Arbejdssted, Ansøgningsfrist) and the text."""
    found = page.found
    title = found.get("title") or found.get("h1") or re.sub(r"\s*\|\s*jobdanmark\s*$", "", found.get("h3", ""), flags=re.I)
    overview = {}
    for entry in page.overview:
        label, _, value = entry.partition(":")
        overview.setdefault(label.strip().casefold(), value.strip())
    workplace = overview.get("arbejdssted") or None
    return {"id": slug, "title": title or None, "company": found.get("company_link") or found.get("company_strong") or None,
            "location": extract_city(workplace) or workplace, "date": to_contract_date(overview.get("udgivet")),
            "deadline": to_contract_date(overview.get("ansøgningsfrist")), "url": f"{BASE}/job/{slug}",
            "description": "\n".join(page.lines) or None}


def parse_detail(html: str, slug: str) -> dict:
    """A posting page as the contract's fields. A page that ends somewhere other than /job/<slug>, or is a not-found
    page, is "not_found"; a page with no posting title is a "parse" error."""
    page = _Page().read(html)
    if page.destination and urlparse(page.destination).path.rstrip("/").casefold() != f"/job/{slug}".casefold():
        raise BoardError("not_found", f"{NAME} has no posting {slug!r}: the board sent {page.destination[:100]} instead")
    node = _job_posting(page)
    if node is None and not page.has_details:
        phrases = " ".join([page.found.get("doc_title", ""), *page.text]).casefold()
        raise BoardError("not_found" if any(p in phrases for p in NOT_FOUND_PHRASES) else "parse",
                         f"{NAME} page for {slug!r} holds no posting")
    out = _from_json_ld(node, slug) if node else _from_layout(page, slug)
    if not out["title"]:
        raise BoardError("parse", f"{NAME} page for {slug!r} has no title")
    low = out["title"].casefold()
    if low.startswith("404") or any(p in low for p in NOT_FOUND_PHRASES):
        raise BoardError("not_found", f"{NAME} has no posting {slug!r}")
    return out


def detail(ref: str, http: Client) -> dict:
    slug = normalize_slug(ref)
    reply = http.get(f"{BASE}/job/{slug}", html=True)
    return parse_detail(reply.content.decode("utf-8", "replace"), slug)


if __name__ == "__main__":
    sys.exit(contract.run_cli(sys.modules[__name__]))

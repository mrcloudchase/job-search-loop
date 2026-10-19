"""Akademikernes Jobbank (jobbank.dk, Denmark): an RSS search feed plus detail pages that carry JSON-LD.

Opt-in (Danish board). It makes exactly one request per search and one per detail, only through `boards.http.Client`, so a
Cloudflare challenge (which this board serves to many networks) surfaces as BoardError("blocked") and is never retried,
parsed or worked around. The feed only carries a one-line Danish summary, so company, location and deadline are read from
it with a tolerant parser: one odd item never fails the search, and an item without a real jobbank.dk posting link is
dropped. Dates are handled in Danish time on purpose (see `local_date`). Run: python -m jobloop.boards.jobbank search ...
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

from .contract import BoardError, Query, run_cli

NAME = "jobbank"
HOSTS = ("jobbank.dk",)
DEFAULT_ENABLED = False
TERMS = "Jobbank states no ban on automation; use it for personal, low-volume searches only (one request per search)."
ACK_WAIVES_ROBOTS = False

BASE = "https://jobbank.dk"
FEED_MAX = 100

# Filter ids a person may send (case-sensitive; the board answers an unknown id with zero results, so we refuse it).
TYPES = frozenset("3 6 13 8 12 11 15 14 9 4 5".split())
EDUCATION = frozenset("20 43 29 47 32 38 28 24 23 44 45 37 22 48 46 31 30 41 35 34 25 33 26 21".split())
WORK_AREAS = frozenset("20 38 22 43 47 41 28 49 34 32 33 31 24 35 46 37 26 29 40 45 23 52 44 27 50 30 39 42 25 48 36 21".split())
INDUSTRIES = frozenset("10359 11669 11634 16791 10358 10442 15407 10364 10331 17209 16826 10341 10333 10363 11626 15586 10362 "
                       "10440 12450".split())
SUITABLE_FOR = frozenset("2 4 5".split())
REGIONS = {"2": "storkøbenhavn", "3": "nordsjælland", "14": "østsjælland", "4": "vestsjælland", "5": "sydsjælland", "13": "fyn",
           "12": "sønderjylland", "11": "sydvestjylland", "9": "vestjylland", "10": "sydøstjylland", "7": "midtjylland",
           "8": "østjylland", "6": "nordjylland", "20": "bornholm", "21": "øresundsregionen", "22": "grønland", "23": "sverige",
           "24": "norge", "19": "udlandet"}
# Cities the person is likely to type, mapped to the region the board filters on (`amt` takes a region, never a city).
CITY_REGION = {"københavn": "2", "kobenhavn": "2", "copenhagen": "2", "frederiksberg": "2", "storkøbenhavn": "2",
               "helsingør": "3", "hillerød": "3", "aarhus": "8", "århus": "8", "odense": "13", "aalborg": "6", "ålborg": "6",
               "rønne": "20"}
NATIONWIDE = ("denmark", "danmark")


@dataclass(frozen=True)
class Filters:
    """The facets the board supports beyond the query: validated here so an invalid id never reaches the board.

    `company` is the board's numeric company id (the `virk` parameter); a company name is not accepted, because only the
    numeric form was ever shown to work."""
    exclude: str = ""
    types: tuple[str, ...] = ()
    education: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    work_areas: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    suitable_for: tuple[str, ...] = ()
    company: int | None = None
    remote: str = ""
    since: str = ""

    def __post_init__(self):
        for label, values, known in (("type", self.types, TYPES), ("education", self.education, EDUCATION),
                                     ("region", self.regions, set(REGIONS)), ("work area", self.work_areas, WORK_AREAS),
                                     ("industry", self.industries, INDUSTRIES), ("suitable-for", self.suitable_for, SUITABLE_FOR)):
            for v in values:
                if v not in known:
                    raise BoardError("invalid", f"{label} {v!r} is not a jobbank id; ids are exact, e.g. {sorted(known)[:4]}")
        if self.company is not None and (isinstance(self.company, bool) or not isinstance(self.company, int) or self.company < 1):
            raise BoardError("invalid", f"company must be the board's numeric company id (a whole number of at least 1), not {self.company!r}")
        if self.remote not in ("", "helt", "delvist"):
            raise BoardError("invalid", f"remote must be 'helt' or 'delvist', not {self.remote!r}")
        if self.since and (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.since) or parse_iso_date(self.since) is None):
            raise BoardError("invalid", f"since must be a real date written YYYY-MM-DD, not {self.since!r}")


def parse_iso_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def region_for(location: str) -> str | None:
    """The region code for a city or region name or code, or None when the board cannot filter on it."""
    key = " ".join(location.casefold().split())
    if key in REGIONS:
        return key
    for code, name in REGIONS.items():
        if key == name:
            return code
    return CITY_REGION.get(key)


# --- Danish time -------------------------------------------------------------------------------------------------------

def _last_sunday(year: int, month: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def copenhagen(moment: datetime) -> datetime:
    """An aware moment in Danish civil time (UTC+1, or +2 from the last Sunday of March to the last of October, 01:00 UTC)."""
    utc = moment.astimezone(timezone.utc)
    start = datetime.combine(_last_sunday(utc.year, 3), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    end = datetime.combine(_last_sunday(utc.year, 10), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    return utc + timedelta(hours=2 if start <= utc < end else 1)


def local_date(text: str | None) -> str | None:
    """The Danish calendar day of an ISO date or datetime: a date-only value is taken as written, a datetime with an
    offset is converted (23:30 UTC is already tomorrow in Denmark), and a datetime with no offset is read as written."""
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    if len(text.strip()) <= 10 or moment.tzinfo is None:
        return moment.date().isoformat()
    return copenhagen(moment).date().isoformat()


_RFC2822 = re.compile(r"^[A-Za-z]{3}, \d{1,2} [A-Za-z]{3} \d{4} \d\d:\d\d(:\d\d)? ([+-]\d{4}|GMT|UT|UTC|Z)$")


def pub_date(text: str | None) -> str | None:
    """The Danish calendar day of an RSS pubDate, or None when it is missing, malformed or has no usable offset (a stray
    trailing character would otherwise make the parser drop the offset and shift the day)."""
    if not text or not _RFC2822.match(text.strip()):
        return None
    try:
        moment = parsedate_to_datetime(text.strip())
    except (ValueError, TypeError, IndexError):
        return None
    return copenhagen(moment).date().isoformat() if moment.tzinfo else None


def is_expired(deadline: str | None, today: date) -> bool | None:
    """True once the deadline day is past in Danish time; None (unknown) when there is no real date, e.g. 'snarest muligt'."""
    d = parse_iso_date(deadline) if deadline and re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline) else None
    return None if d is None else d < today


def today_in_denmark() -> date:
    return copenhagen(datetime.now(timezone.utc)).date()


# --- requests ----------------------------------------------------------------------------------------------------------

def build_params(query: Query, filters: Filters, today: date) -> dict[str, list[str]]:
    """The search parameters, repeated keys in the order given. Recency becomes `oprettet` (a date: today minus N days,
    or the person's own `since` if that is later); the location becomes a region only when we know it."""
    params: dict[str, list[str]] = {"key": [query.text]}
    for key, values in (("cvtype", filters.types), ("udd", filters.education), ("erf", filters.work_areas),
                        ("branche", filters.industries), ("andet", filters.suitable_for)):
        if values:
            params[key] = list(values)
    region = region_for(query.location) if query.location else None
    regions = list(filters.regions) + ([region] if region and region not in filters.regions else [])
    if regions:
        params["amt"] = regions
    if filters.exclude:
        params["antikey"] = [filters.exclude]
    if filters.company is not None:
        params["virk"] = [str(filters.company)]
    if filters.remote:
        params["fjernarbejde"] = [filters.remote]
    since = [d for d in (filters.since, (today - timedelta(days=query.recency_days)).isoformat() if query.recency_days else "") if d]
    if since:
        params["oprettet"] = [max(since)]
    return params


def job_id_from_url(url: str | None) -> str | None:
    """The numeric id in a jobbank.dk /job/<id>/ link, else None: an id is never invented."""
    parts = urlparse(url or "")
    m = re.match(r"^/job/(\d{1,12})(?:/|$)", parts.path)
    return m.group(1) if m and parts.scheme in ("http", "https") and (parts.hostname or "").lower() in ("jobbank.dk", "www.jobbank.dk") else None


# --- RSS ---------------------------------------------------------------------------------------------------------------

_BARE_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")


def parse_rss(body: bytes) -> list[ET.Element]:
    """The `item` elements of an RSS 2.0 document. Anything else (a challenge page, HTML, a DTD) is a parse error, never
    an empty result; unescaped ampersands, which feeds contain, are repaired once."""
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", body, re.I):
        raise BoardError("parse", "jobbank sent an XML document type declaration, which is refused")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        try:
            root = ET.fromstring(_BARE_AMP.sub("&amp;", body.decode("utf-8")))
        except (ET.ParseError, UnicodeDecodeError):
            raise BoardError("parse", "jobbank did not send an RSS feed") from None
    if root.tag != "rss" or root.find("channel") is None:
        raise BoardError("parse", "jobbank did not send an RSS 2.0 feed")
    return root.find("channel").findall("item")


_DEADLINE = re.compile(r"\(\s*Ans[øo]gningsfrist\s*:\s*(.*?)\s*\)\s*$", re.I)
_DMY = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def parse_deadline(text: str) -> str | None:
    """DD.MM.YYYY as YYYY-MM-DD; a rolling deadline (løbende) as None; any other text unchanged, for the reader to see."""
    text = text.strip()
    if re.fullmatch(r"l[øo]bende", text, re.I):
        return None
    m = _DMY.match(text)
    if m:
        try:
            return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
        except ValueError:
            return text
    return text or None


def parse_description(text: str) -> dict:
    """Split '<types> hos <Company>, <Location> (Ansøgningsfrist: <deadline>)' into types, company, location, deadline.
    Without ' hos ' the whole line is the company and the rest is empty; it never raises."""
    text = " ".join(text.split())
    if " hos " not in text:
        return {"types": [], "company": text or None, "location": None, "deadline": None}
    left, right = text.split(" hos ", 1)
    deadline = None
    m = _DEADLINE.search(right)
    if m:
        deadline, right = parse_deadline(m[1]), right[: m.start()].strip()
    company, _, location = right.partition(", ")
    return {"types": [t.strip() for t in left.split(",") if t.strip()], "company": company.strip() or None,
            "location": location.strip() or None, "deadline": deadline}


def normalize_item(item: ET.Element) -> dict | None:
    """One feed item as a contract posting, or None when its link is not a jobbank.dk posting with a numeric id."""
    url = (item.findtext("link") or "").strip()
    job_id = job_id_from_url(url)
    if job_id is None:
        return None
    summary = (item.findtext("description") or "").strip()
    parts = parse_description(summary)
    return {"id": job_id, "title": item.findtext("title"), "company": parts["company"], "location": parts["location"],
            "date": pub_date(item.findtext("pubDate")), "deadline": parts["deadline"], "url": url, "description": summary}


def search(query: Query, http, filters: Filters = Filters(), today: date | None = None) -> list[dict]:
    """Search the feed (one request, up to 100 items, no paging) and filter client-side what the feed cannot: a location
    the board has no region for is matched against the posting's location text (a posting with none is kept)."""
    today = today or today_in_denmark()
    reply = http.get(f"{BASE}/job/rss", params=build_params(query, filters, today), html=True)
    seen, found = set(), []
    for item in parse_rss(reply.content)[:FEED_MAX]:
        posting = normalize_item(item)
        if posting and posting["id"] not in seen:
            seen.add(posting["id"])
            found.append(posting)
    place = query.location.casefold().strip()
    if place and region_for(place) is None and place not in NATIONWIDE:
        found = [p for p in found if not p["location"] or place in p["location"].casefold()]
    return found[: query.limit]


# --- detail ------------------------------------------------------------------------------------------------------------

def normalize_id(ref: str) -> str:
    """The job id from a bare number or a jobbank.dk /job/<id>/ url; anything else is refused before any request."""
    ref = (ref or "").strip()
    if re.fullmatch(r"\d{1,12}", ref):
        return ref
    job_id = job_id_from_url(ref)
    if job_id is None:
        raise BoardError("invalid", f"not a jobbank posting: give its number or a https://jobbank.dk/job/<number>/ url, not {ref[:60]!r}")
    return job_id


class _LdScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts: list[str] = []
        self._in = False

    def handle_starttag(self, tag, attrs):
        self._in = tag == "script" and (dict(attrs).get("type") or "").strip().lower() == "application/ld+json"
        if self._in:
            self.scripts.append("")

    def handle_endtag(self, tag):
        self._in = False

    def handle_data(self, data):
        if self._in:
            self.scripts[-1] += data


def _find_posting(node: object) -> dict | None:
    if isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            return node
        nodes = node.get("@graph") if isinstance(node.get("@graph"), list) else list(node.values())
    elif isinstance(node, list):
        nodes = node
    else:
        return None
    return next((p for n in nodes if (p := _find_posting(n))), None)


def parse_jsonld(html: str) -> dict:
    """The first schema.org JobPosting in the page's JSON-LD scripts (object, array or @graph; a malformed script is
    skipped). No posting is a parse error: that is what an unknown id redirected to a generic page looks like."""
    scanner = _LdScripts()
    scanner.feed(html)
    for script in scanner.scripts:
        try:
            found = _find_posting(json.loads(script))
        except ValueError:
            continue
        if found:
            return found
    raise BoardError("parse", "no JobPosting JSON-LD on the jobbank page")


def _text(value: object) -> str | None:
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip() else None


def parse_detail(html: str, job_id: str) -> dict:
    """A contract posting from a detail page. The deadline is validThrough as a Danish calendar day (the 0001-01-01
    sentinel and anything that is not a date are None); the location is 'city, country'. The employer logo, street,
    postal code and employment type have no contract field and are not carried."""
    ld = parse_jsonld(html)
    org = ld.get("hiringOrganization")
    place = ld.get("jobLocation")
    place = next((p for p in place if isinstance(p, dict)), None) if isinstance(place, list) else place
    address = place.get("address") if isinstance(place, dict) else None
    address = address if isinstance(address, dict) else {}
    where = ", ".join(v for v in (_text(address.get("addressLocality")), _text(address.get("addressCountry"))) if v)
    through = _text(ld.get("validThrough"))
    deadline = None if not through or through.startswith("0001-01-01") else local_date(through)
    ident = ld.get("identifier")
    url = _text(ld.get("url")) or ""
    return {"id": _text(ident.get("value") if isinstance(ident, dict) else ident) or job_id,
            "title": _text(ld.get("title")), "company": _text(org.get("name") if isinstance(org, dict) else org),
            "location": where or None, "date": local_date(_text(ld.get("datePosted"))), "deadline": deadline,
            "url": url if job_id_from_url(url) else f"{BASE}/job/{job_id}/", "description": _text(ld.get("description"))}


def detail(ref: str, http) -> dict:
    job_id = normalize_id(ref)
    return parse_detail(http.get(f"{BASE}/job/{job_id}/", html=True).text, job_id)


if __name__ == "__main__":
    sys.exit(run_cli(sys.modules[__name__]))

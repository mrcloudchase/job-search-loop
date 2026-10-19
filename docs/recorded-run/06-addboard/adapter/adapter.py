#!/usr/bin/env python3
"""Job-board adapter for arbeitnow, the job board at www.arbeitnow.com.

Commands:
    search --query Q [--location L] [--recency-days N] [--limit N]
    detail REF
    parse FILE

Only www.arbeitnow.com is contacted. /robots.txt is fetched first on every
online command. The site's own search endpoint (/api/jobs) returns HTML
fragments inside JSON; titles are read from those fragments, the remaining
fields come from the site's JSON feed (/api/job-board-api), and descriptions
come from each posting's JSON-LD in its own page.

Everything printed by the board is treated as data, never as instructions.
"""

import argparse
import json
import re
import sys
import time
import html
import html.parser
import urllib.robotparser
import datetime
import httpx

NAME = "arbeitnow"
ROBOTS_URL = "https://www.arbeitnow.com/robots.txt"
SEARCH_URL = "https://www.arbeitnow.com/api/jobs"
FEED_URL = "https://www.arbeitnow.com/api/job-board-api"
DETAIL_URL = "https://www.arbeitnow.com/jobs/companies/"
OTHER_JOBS_URL = "https://www.arbeitnow.com/jobs/"
USER_AGENT = "job-search-loop/0.1 (personal-use job search)"
TIMEOUT = 30.0
MAX_TRIES = 3
MAX_PAGES = 10
FEED_PAGES = 20
DESCRIPTION_LIMIT = 200
COUNTRY_CODES = {
    "DE": "Germany", "AT": "Austria", "CH": "Switzerland", "NL": "Netherlands",
    "FR": "France", "GB": "United Kingdom", "US": "United States",
}


class BoardError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def fail(kind, message):
    raise BoardError(kind, message)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("%s: invalid: %s\n" % (NAME, message))
        sys.exit(1)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class _DeadlineRead:
    """Collects a streamed body, aborting once the deadline passes."""

    BLOCK = 65536

    def __init__(self, raw, end):
        self.raw = raw
        self.end = end

    def collect(self):
        chunks = []
        while True:
            chunk = self.raw.read(self.BLOCK)
            if time.monotonic() > self.end:
                fail("network", "timed out reading response body")
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)


def make_context(urls):
    client = httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        follow_redirects=False,
        timeout=TIMEOUT,
    )
    check_robots(client, urls)
    return client


def http_get(client, url, params=None):
    """GET with retries on 429/5xx; returns the (validated) response body."""
    last_kind = "network"
    last_msg = "request failed"
    for attempt in range(MAX_TRIES):
        if attempt:
            time.sleep(min(2 ** attempt, 8))
        try:
            resp = client.get(url, params=params, stream=True)
            try:
                status = resp.status_code
                if 200 <= status <= 299:
                    body = _DeadlineRead(resp.stream, time.monotonic() + TIMEOUT).collect()
                    return body.decode("utf-8", "replace")
            finally:
                resp.close()
        except httpx.TimeoutException:
            last_kind, last_msg = "network", "timed out fetching %s" % url
            continue
        except httpx.HTTPError as exc:
            last_kind, last_msg = "network", "network error fetching %s: %s" % (url, exc)
            continue
        status = resp.status_code
        if 200 <= status <= 299:
            return resp.text
        if status == 429:
            last_kind, last_msg = "rate_limited", "rate limited (429) by %s" % url
            continue
        if status in (401, 403):
            fail("blocked", "access denied (%d) by %s" % (status, url))
        if status in (404, 410):
            fail("not_found", "not found (%d): %s" % (status, url))
        if 500 <= status <= 599:
            last_kind, last_msg = "network", "server error (%d) from %s" % (status, url)
            continue
        if 300 <= status <= 399:
            fail("blocked", "unexpected redirect (%d) from %s" % (status, url))
        fail("network", "unexpected status %d from %s" % (status, url))
    fail(last_kind, last_msg)


def check_robots(client, urls):
    text = http_get(client, ROBOTS_URL)
    parser = urllib.robotparser.RobotFileParser()
    try:
        parser.parse(text.splitlines())
    except Exception:
        fail("robots", "could not understand robots.txt")
    for url in urls:
        allowed = parser.can_fetch(USER_AGENT, url)
        if allowed is None:
            allowed = parser.can_fetch("*", url)
        if not allowed:
            fail("robots", "robots.txt disallows %s" % url)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def iso_date(value):
    if not isinstance(value, str):
        return None
    m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", value)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def ts_to_iso(ts):
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


class TextExtractor(html.parser.HTMLParser):
    BLOCK = {
        "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
        "tr", "table", "section", "article", "blockquote", "pre", "hr",
        "dd", "dt", "header", "footer", "figure", "figcaption",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "li":
            self._break()
            self.parts.append("- ")
        elif tag in self.BLOCK:
            self._break()

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK:
            self._break()

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            if self.skip:
                self.skip -= 1
        elif tag in self.BLOCK:
            self._break()

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def _break(self):
        self.parts.append("\n\n")

    def text(self):
        raw = "".join(self.parts)
        paras = []
        for chunk in re.split(r"\n\s*\n", raw):
            flat = re.sub(r"\s+", " ", chunk).strip()
            if flat:
                paras.append(flat)
        return "\n\n".join(paras)


def html_to_text(markup):
    if not isinstance(markup, str) or not markup.strip():
        return None
    parser = TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        pass
    return parser.text() or None


def is_board_url(url):
    """True for a whole posting URL on the board, i.e. one of the literal prefixes."""
    if not isinstance(url, str):
        return False
    for prefix in (DETAIL_URL, OTHER_JOBS_URL):
        if url.startswith(prefix) and " " not in url and "@" not in url:
            return True
    return False


class CardParser(html.parser.HTMLParser):
    """Reads job cards out of the HTML fragment returned by /api/jobs.

    Each card is a list item with a data-slug, containing a title link
    marked data-job-item-link and a posting-time element.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.cur = None
        self.link_depth = None
        self.buf = None
        self.time_seen = 0
        self.depth = 0
        self.company_depth = None
        self.company_buf = []
        self.location_depth = None
        self.location_buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li" and a.get("data-slug") and self.cur is None:
            self.cur = {
                "slug": a["data-slug"], "title": None, "company": None,
                "location": None, "date": None, "url": None,
            }
            self.cards.append(self.cur)
            self.depth = 1
            self.time_seen = 0
            self.company_depth = None
            self.location_depth = None
            return
        if self.cur is None:
            return
        if tag == "li":
            self.depth += 1
        if self.cur["url"] is None and a.get("data-link"):
            self.cur["url"] = a["data-link"].split("?", 1)[0] or None
        if tag == "a" and a.get("data-job-item-link") == "true" and self.link_depth is None:
            self.link_depth = self.depth
            self.buf = []
        if tag == "a" and a.get("itemprop") == "hiringOrganization" and self.company_depth is None:
            self.company_depth = self.depth
            self.company_buf = []
        if tag == "span" and a.get("class") == "text-gray-600" and self.location_depth is None:
            self.location_depth = self.depth
            self.location_buf = []
        if tag == "time" and self.time_seen < 2:
            self.cur["date"] = self.cur["date"] or iso_date(a.get("datetime"))
            self.time_seen += 1

    def handle_startendtag(self, tag, attrs):
        if self.cur is not None and tag == "time" and self.time_seen < 2:
            self.cur["date"] = self.cur["date"] or iso_date(dict(attrs).get("datetime"))
            self.time_seen += 1

    def handle_endtag(self, tag):
        if self.cur is None:
            return
        if tag == "a" and self.link_depth is not None and self.link_depth == self.depth:
            self.cur["title"] = self._flat(self.buf)
            self.link_depth = None
            self.buf = None
        if tag == "a" and self.company_depth is not None and self.company_depth == self.depth:
            self.cur["company"] = self._flat(self.company_buf)
            self.company_depth = None
        if tag == "span" and self.location_depth is not None and self.location_depth == self.depth:
            self.cur["location"] = self._flat(self.location_buf)
            self.location_depth = None
        if tag == "li":
            self.depth -= 1
            if self.depth == 0:
                self.cur = None

    def handle_data(self, data):
        if self.buf is not None:
            self.buf.append(data)
        if self.company_depth is not None:
            self.company_buf.append(data)
        if self.location_depth is not None:
            self.location_buf.append(data)

    @staticmethod
    def _flat(parts):
        text = re.sub(r"\s+", " ", "".join(parts)).strip()
        return text or None


def parse_search_payload(payload):
    if not isinstance(payload, dict):
        fail("parse", "search response is not a JSON object")
    fragment = payload.get("data")
    if not isinstance(fragment, str):
        fail("parse", "search response has no job list")
    parser = CardParser()
    try:
        parser.feed(fragment)
        parser.close()
    except Exception as exc:
        fail("parse", "could not parse search results: %s" % exc)
    cards = [c for c in parser.cards if c.get("title") and c.get("slug")]
    return cards, payload


# --------------------------------------------------------------------------
# Data assembly
# --------------------------------------------------------------------------

def card_id(slug):
    """The posting number at the end of a slug, else the slug itself."""
    tail = slug.rsplit("-", 1)
    if len(tail) == 2 and tail[1].isdigit():
        return tail[1]
    return slug


def record(job_id, title, company, location, date, deadline, url, description):
    return {
        "id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "date": date,
        "deadline": deadline,
        "url": url,
        "description": description,
    }


def feed_lookup(client, slugs, query):
    """Map slug -> job object from the JSON feed (newest first, paginated).

    The feed ignores search terms, so pages are read until every wanted slug
    is found, the feed ends, or FEED_PAGES pages have been read.
    """
    found = {}
    wanted = set(slugs)
    page = 1
    while wanted - set(found) and page <= FEED_PAGES:
        text = http_get(client, FEED_URL, params={"page": str(page)})
        try:
            body = json.loads(text)
        except ValueError:
            fail("parse", "job API response is not JSON")
        jobs = body.get("data") if isinstance(body, dict) else None
        if not isinstance(jobs, list):
            fail("parse", "job API response has no data list")
        for job in jobs:
            if isinstance(job, dict) and job.get("slug") in wanted:
                found[job["slug"]] = job
        links = body.get("links")
        if not isinstance(links, dict) or not links.get("next"):
            break
        page += 1
    return found


def detail_data(client, url):
    """Fetch a posting page and return its JSON-LD JobPosting fields."""
    page = http_get(client, url)
    node = extract_ld_json(page)
    if not node:
        return None
    org = node.get("hiringOrganization")
    company = org.get("name") if isinstance(org, dict) else None
    ident = node.get("identifier")
    if isinstance(ident, dict):
        job_id = ident.get("value")
    elif isinstance(ident, str):
        job_id = ident
    else:
        job_id = None
    return {
        "id": job_id,
        "title": node.get("title") if isinstance(node.get("title"), str) else None,
        "company": company if isinstance(company, str) else None,
        "location": ld_location(node),
        "date": iso_date(node.get("datePosted")),
        "deadline": iso_date(node.get("validThrough")),
        "description": html_to_text(node.get("description")),
    }


def extract_ld_json(page):
    for m in re.finditer(
        r'<script[^>]*type\s*=\s*"application/ld\+json"[^>]*>(.*?)</script>', page, re.S | re.I
    ):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        nodes = data
        if isinstance(data, dict):
            nodes = data.get("@graph") if isinstance(data.get("@graph"), list) else [data]
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                return node
    return None


def ld_location(node):
    loc = node.get("jobLocation")
    if isinstance(loc, list):
        names = [ld_location({"jobLocation": x}) for x in loc]
        names = [n for n in names if n]
        return ", ".join(names) if names else None
    if not isinstance(loc, dict):
        return None
    addr = loc.get("address")
    if not isinstance(addr, dict):
        name = loc.get("name")
        return name if isinstance(name, str) and name.strip() else None
    country = addr.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    region = addr.get("addressRegion")
    code = country.strip().upper() if isinstance(country, str) else ""
    country_name = COUNTRY_CODES.get(code, "").upper()
    if isinstance(region, str) and region.strip().upper() in (code, country_name):
        region = None
    if code in COUNTRY_CODES:
        country = COUNTRY_CODES[code]
    pieces = []
    for value in (addr.get("addressLocality"), region, country):
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if text.lower() in [p.lower() for p in pieces]:
                continue
            pieces.append(text)
    return ", ".join(pieces) if pieces else None


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_search(args):
    client = make_context((
        "https://www.arbeitnow.com/api/jobs?search=x",
        "https://www.arbeitnow.com/api/job-board-api?page=1",
    ))
    raws = []
    seen = set()
    page = 1
    total_pages = 1
    while page <= total_pages and page <= MAX_PAGES and len(raws) < args.limit:
        params = {
            "page": str(page),
            "search": args.query,
            "sort_by": "newest",
            "date_posted": "all",
            "category": "",
            "tags": "",
            "locale": "en",
        }
        text = http_get(client, SEARCH_URL, params=params)
        try:
            payload = json.loads(text)
        except ValueError:
            fail("parse", "search response is not JSON")
        cards, payload = parse_search_payload(payload)
        last = payload.get("last_page")
        if isinstance(last, int) and last > 0:
            total_pages = last
        for card in cards:
            if card["slug"] in seen:
                continue
            seen.add(card["slug"])
            raws.append(card)
            if len(raws) >= args.limit:
                break
        page += 1

    if args.recency_days is not None:
        cutoff = (datetime.date.today() - datetime.timedelta(days=args.recency_days)).isoformat()
        raws = [r for r in raws if r.get("date") and r["date"] >= cutoff]

    if args.location:
        needle = args.location.strip().lower()
        raws = [r for r in raws if needle in (r.get("location") or "").lower()]

    feed = feed_lookup(client, [r["slug"] for r in raws], args.query)

    results = []
    for raw in raws:
        job = feed.get(raw["slug"]) or {}
        url = job.get("url")
        if not (isinstance(url, str) and is_board_url(url)):
            url = None
        location = raw.get("location") or job.get("location")
        company = job.get("company_name") or raw.get("company")
        date = raw.get("date") or ts_to_iso(job.get("created_at"))
        results.append(record(
            card_id(raw["slug"]), raw["title"], company, location or None, date, None, url, None
        ))

    for rec, raw in zip(results, raws):
        if rec["url"] and len(results) <= DESCRIPTION_LIMIT:
            extra = detail_data(client, rec["url"])
            if extra:
                rec["description"] = extra["description"]
                rec["deadline"] = extra["deadline"] or rec["deadline"]
                rec["company"] = rec["company"] or extra["company"]
                rec["location"] = rec["location"] or extra["location"]
            time.sleep(0.3)

    if args.location:
        needle = args.location.strip().lower()
        results = [r for r in results if needle in (r.get("location") or "").lower()]

    return {
        "meta": {
            "board": NAME,
            "count": len(results),
            "query": args.query,
            "location": args.location,
            "recency_days": args.recency_days,
        },
        "results": results,
    }


def ref_to_url(ref):
    ref = ref.strip()
    if not ref:
        fail("invalid", "empty job reference")
    if is_board_url(ref):
        return ref
    return DETAIL_URL + ref


def cmd_detail(args):
    url = ref_to_url(args.ref)
    client = make_context((url,))
    data = detail_data(client, url)
    if not data:
        fail("parse", "no job posting data found on the page")
    job_id = data["id"] or url.rstrip("/").rsplit("/", 1)[-1]
    result = record(
        job_id, data["title"], data["company"], data["location"],
        data["date"], data["deadline"], url, data["description"],
    )
    return {"meta": {"board": NAME, "count": 1}, "results": [result]}


def cmd_parse(args):
    try:
        with open(args.file, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        fail("invalid", "cannot read file %s: %s" % (args.file, exc.strerror or "error"))
    try:
        payload = json.loads(text)
    except ValueError:
        fail("parse", "file is not valid JSON")
    fragment = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(fragment, str) and "\\" in fragment:
        # API fragments sometimes carry an extra HTML layer escaping
        # quotes and slashes (\" and \/); strip that backslash layer.
        payload["data"] = fragment.replace('\\"', '"').replace("\\/", "/")
    cards, payload = parse_search_payload(payload)
    results = []
    for card in cards:
        results.append(record(
            card_id(card["slug"]), card["title"], card.get("company"), card.get("location"),
            card.get("date"), None, card.get("url"), None,
        ))
    total = payload.get("total")
    return {
        "meta": {
            "board": NAME,
            "count": len(results),
            "total_available": total if isinstance(total, int) else None,
            "source": args.file,
        },
        "results": results,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    parser = ArgumentParser(prog="adapter.py")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    sp = sub.add_parser("search")
    sp.add_argument("--query", required=True)
    sp.add_argument("--location")
    sp.add_argument("--recency-days", type=int)
    sp.add_argument("--limit", type=int, default=20)

    dp = sub.add_parser("detail")
    dp.add_argument("ref")

    pp = sub.add_parser("parse")
    pp.add_argument("file")
    return parser


def validate(args):
    if args.command == "search":
        query = (args.query or "").strip()
        if not query or query.startswith("-"):
            fail("invalid", "query must be non-empty and must not start with '-'")
        if args.limit is None or not 1 <= args.limit <= 200:
            fail("invalid", "limit must be between 1 and 200")
        if args.recency_days is not None and args.recency_days < 1:
            fail("invalid", "recency-days must be at least 1")
        if args.location is not None and not args.location.strip():
            fail("invalid", "location must not be empty")


def main(argv):
    args = build_parser().parse_args(argv)
    validate(args)
    if args.command == "search":
        output = cmd_search(args)
    elif args.command == "detail":
        output = cmd_detail(args)
    else:
        output = cmd_parse(args)
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BoardError as exc:
        sys.stderr.write("%s: %s: %s\n" % (NAME, exc.kind, exc.message))
        sys.exit(1)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.stderr.write("%s: network: interrupted\n" % NAME)
        sys.exit(1)
    except Exception as exc:
        sys.stderr.write("%s: network: unexpected error: %s\n" % (NAME, exc))
        sys.exit(1)

"""Nordlys Jobs (a fictional board): search, detail and an offline parse of a saved search response."""
import argparse
import html
import json
import re
import sys
import time
from urllib import robotparser

import httpx

NAME = "nordlys-jobs"
BASE = "https://jobs.nordlys.example"
USER_AGENT = "job-search-loop/0.1 (personal-use job search)"
FIELDS = ("id", "title", "company", "location", "date", "deadline", "url", "description")
RETRIES = 3


class Fail(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Fail("invalid", message)


def text_of(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(value))).split()) if value is not None else None


def posting(raw):
    path = raw.get("path")
    return {"id": raw.get("id"), "title": text_of(raw.get("title")), "company": text_of(raw.get("company")),
            "location": text_of(raw.get("city")), "date": raw.get("posted"), "deadline": raw.get("closing"),
            "url": BASE + path if isinstance(path, str) and path.startswith("/") else None,
            "description": text_of(raw.get("summary"))}


def parse_search(text):
    try:
        jobs = json.loads(text)["jobs"]
    except (ValueError, KeyError, TypeError):
        raise Fail("parse", "the response is not a Nordlys job list")
    rows = [posting(j) for j in jobs if isinstance(j, dict)]
    return [r for r in rows if r["title"] and r["url"]]


def envelope(results, **meta):
    return {"meta": {"board": NAME, "count": len(results), **meta}, "results": results}


def allowed(client, url):
    """robots.txt is read first; a robots.txt that cannot be read means no."""
    reply = client.get(BASE + "/robots.txt")
    if reply.status_code == 404:
        return True
    if reply.status_code != 200:
        return False
    rules = robotparser.RobotFileParser()
    rules.parse(reply.text.splitlines())
    return rules.can_fetch(USER_AGENT, url)


def fetch(client, path, params=None):
    url = BASE + path
    if not allowed(client, url):
        raise Fail("robots", "robots.txt does not allow " + path)
    for attempt in range(RETRIES + 1):
        reply = client.get(url, params=params)
        if reply.status_code == 429 or reply.status_code >= 500:
            if attempt == RETRIES:
                raise Fail("rate_limited" if reply.status_code == 429 else "network", "gave up after %d tries" % (attempt + 1))
            time.sleep(2 ** attempt)
            continue
        if reply.status_code in (401, 403):
            raise Fail("blocked", "the board refused the request")
        if reply.status_code == 404:
            raise Fail("not_found", "no such page")
        return reply.text
    raise Fail("network", "no answer")


def session():
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=False)


def run(args):
    if args.command == "parse":
        try:
            with open(args.file, encoding="utf-8") as f:
                return envelope(parse_search(f.read()))
        except OSError as e:
            raise Fail("not_found", "cannot read %s: %s" % (args.file, e.strerror))
    try:
        with session() as client:
            if args.command == "search":
                if not 1 <= args.limit <= 200 or args.recency_days < 1 or not args.query.strip() or args.query.startswith("-"):
                    raise Fail("invalid", "query, limit (1-200) or recency-days (1 or more) is not valid")
                found = parse_search(fetch(client, "/api/jobs", {"q": args.query, "where": args.location}))
                found = [r for r in found if not r["date"] or r["date"] >= time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * args.recency_days))]
                return envelope(found[: args.limit], query=args.query)
            found = parse_search(fetch(client, "/api/jobs", {"id": args.ref}))
            if not found:
                raise Fail("not_found", "no such posting")
            return envelope(found[:1])
    except httpx.HTTPError as e:
        raise Fail("network", "%s: %s" % (type(e).__name__, e))


def main(argv):
    parser = Parser(prog=NAME)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    s = sub.add_parser("search")
    s.add_argument("--query", "-q", required=True)
    s.add_argument("--location", "-l", default="")
    s.add_argument("--recency-days", type=int, default=14)
    s.add_argument("--limit", type=int, default=20)
    d = sub.add_parser("detail")
    d.add_argument("ref")
    p = sub.add_parser("parse")
    p.add_argument("file")
    try:
        print(json.dumps(run(parser.parse_args(argv)), ensure_ascii=False))
    except Fail as e:
        print("%s: %s: %s" % (NAME, e.kind, e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

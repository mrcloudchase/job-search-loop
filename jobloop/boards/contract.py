"""The one contract every job-board adapter keeps, so the loop treats LinkedIn and Jobnet the same way.

An adapter module defines `NAME`, `HOSTS` (every host it may contact), `DEFAULT_ENABLED`, `TERMS` (a personal-use or
terms note, or ""), and two functions: `search(query, http) -> list[dict]` and `detail(ref, http) -> dict`, where
`http` is a `boards.http.Client`. Both return results in the shape below. Run as a program (`python -m
jobloop.boards.NAME search --query ...`) it prints `{"meta": {...}, "results": [...]}` as JSON; any failure goes to
stderr as one line and the exit code is 1. Adapters run as plain code in the loop, never inside a harness.

Results are untrusted data: every field is coerced to short text, a missing value is `None` (never omitted), and a
url that is not http(s) drops the posting.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

FIELDS = ("id", "title", "company", "location", "date", "deadline", "url", "description")
LIMITS = {"id": 200, "title": 300, "company": 200, "location": 200, "date": 40, "deadline": 40, "url": 1000, "description": 60000}
KINDS = ("blocked", "rate_limited", "not_found", "invalid", "network", "parse", "robots")


class BoardError(Exception):
    """Why a board could not answer. `kind` tells a broken portal from a rate-limited or blocked one."""

    def __init__(self, kind: str, message: str):
        assert kind in KINDS, kind
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Query:
    """What a person asked for. Each adapter maps what its board supports and the loop filters the rest client-side."""
    text: str
    location: str = ""
    recency_days: int | None = 14
    limit: int = 20

    def __post_init__(self):
        text = " ".join(self.text.split())
        if not text or text.startswith("-") or len(text) > 200:
            raise BoardError("invalid", f"a query is 1 to 200 characters and does not start with '-': {text[:40]!r}")
        if not 1 <= self.limit <= 200:
            raise BoardError("invalid", f"limit must be between 1 and 200, not {self.limit}")
        if self.recency_days is not None and self.recency_days < 1:
            raise BoardError("invalid", f"recency must be at least 1 day, not {self.recency_days}")
        object.__setattr__(self, "text", text)


def clean(value: object, field: str) -> str | None:
    """A field as short single-line text (a description keeps its line breaks), or None if it says nothing."""
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value)
    text = text.strip() if field == "description" else " ".join(text.split())
    return text[: LIMITS[field]] or None


def result(raw: dict) -> dict | None:
    """One posting in the contract's shape, or None if it is not a posting: no title, or no http(s) url."""
    out = {f: clean(raw.get(f), f) for f in FIELDS}
    if not out["title"] or not out["url"] or not re.match(r"https?://[^\s/]+", out["url"]):
        return None
    out["id"] = out["id"] or out["url"]
    return out


def parse_date(text: str | None) -> date | None:
    """The calendar date at the start of an ISO date or datetime, or None."""
    try:
        return date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def recent(results: list[dict], days: int | None, today: date | None = None) -> list[dict]:
    """The client-side recency filter, for boards that can't filter: keep a posting dated within `days`, and keep one
    with no date at all, because a missing date says nothing about age."""
    if days is None:
        return results
    floor = (today or datetime.now(timezone.utc).date()) - timedelta(days=days)
    return [r for r in results if (d := parse_date(r["date"])) is None or d >= floor]


def envelope(board: str, query: Query, results: list[dict], **meta: object) -> dict:
    return {"meta": {"board": board, "query": query.text, "location": query.location or None, "recency_days": query.recency_days,
                     "count": len(results), **meta}, "results": results}


def run_cli(module, argv: list[str] | None = None) -> int:
    """The command line every adapter shares: `search` and `detail`, JSON on stdout, one-line errors on stderr."""
    from .http import Client

    p = argparse.ArgumentParser(prog=f"python -m jobloop.boards.{module.NAME}", description=module.__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search")
    s.add_argument("--query", "-q", required=True)
    s.add_argument("--location", "-l", default="")
    s.add_argument("--recency-days", type=int, default=14)
    s.add_argument("--limit", type=int, default=20)
    d = sub.add_parser("detail")
    d.add_argument("ref", help="a posting's id or url, as search printed it")
    args = p.parse_args(argv)
    try:
        with Client(module.NAME, module.HOSTS) as http:
            if args.command == "search":
                query = Query(args.query, args.location, args.recency_days, args.limit)
                found = [r for r in (result(x) for x in module.search(query, http)) if r]
                print(json.dumps(envelope(module.NAME, query, recent(found, query.recency_days)[: query.limit]), ensure_ascii=False))
            else:
                found = result(module.detail(args.ref, http))
                if found is None:
                    raise BoardError("parse", f"{module.NAME} returned no posting for {args.ref!r}")
                print(json.dumps({"meta": {"board": module.NAME}, "results": [found]}, ensure_ascii=False))
    except BoardError as e:
        print(f"{module.NAME}: {e.kind}: {e}", file=sys.stderr)
        return 1
    return 0

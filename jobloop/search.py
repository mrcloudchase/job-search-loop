"""Search: run your enabled portals as plain code, drop what you have already seen and what has closed, fetch
each remaining posting in full, and rank them by fit. Triage scores only: they come from the posting text and
your profile, and `apply` is where a posting gets a real evaluation. A posting that can't be fetched is never
ranked on its title; it is tried again next time."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
from datetime import date
from pathlib import Path

from . import fit as fitting
from . import portals
from .term import BOLD, DIM, GREEN, OFF, RED, YELLOW, clip, say
from .turns import Api, Turns

HARNESS = True
MIN_POSTING = 200                                                           # fewer characters is not a posting
RETRY = ("new", "unreachable")                                              # what a later search may try again


def load_seen(me: Path) -> dict:
    path = me / "seen.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_seen(me: Path, seen: dict) -> None:
    me.mkdir(parents=True, exist_ok=True)
    temp = me / "seen.json.tmp"
    temp.write_text(json.dumps(seen, indent=1) + "\n")
    os.replace(temp, me / "seen.json")


def closed(job: portals.Job, today: date) -> bool:
    """A deadline that has passed. A date that doesn't parse says nothing."""
    try:
        return date.fromisoformat(job.deadline[:10]) < today
    except ValueError:
        return False


def fresh(found: list[portals.Job], seen: dict, again: bool, today: date) -> tuple[list[portals.Job], list[portals.Job]]:
    """Split what portals found into what to look at now and what has closed. Within a search the same posting
    from two portals counts once, and a posting seen before is skipped unless it is waiting for a retry."""
    keep, expired, keys = [], [], set()
    for job in found:
        if job.key in keys:
            continue
        keys.add(job.key)
        if closed(job, today):
            expired.append(job)
        elif again or seen.get(job.key, {}).get("status", "new") in RETRY:
            keep.append(job)
    return keep, expired


def posting_text(job: portals.Job, full: str) -> str:
    return f"# {job.title}\n\nCompany: {job.company}\nLocation: {job.location}\nDeadline: {job.deadline or 'not stated'}\nURL: {job.url}\n\n{full}\n"


def run(args: argparse.Namespace, api: Api, today: date | None = None) -> list[tuple[portals.Job, fitting.Fit]]:
    today, me = today or date.today(), Path(args.me)
    profile, seen, chosen = Path(args.profile).read_text(), load_seen(me), portals.registry(me)
    chosen = [p for p in chosen if p["enabled"] and (not args.portals or p["name"] in args.portals)]
    if not chosen:
        raise SystemExit("no portals enabled: `python -m jobloop portals import SKILLS_DIR`, then `portals enable NAME`")
    by_name, found = {p["name"]: p for p in chosen}, []
    for portal in chosen:
        for query in args.query:
            try:
                got = portals.search(portal, query, args.limit, args.location)
                say(f"  {portal['name']} · {query!r}: {len(got)} postings")
                found += got
            except (RuntimeError, ValueError, OSError) as e:
                say(f"{YELLOW}  {portal['name']} · {query!r}: {clip(e, 150)}{OFF}")
    keep, expired = fresh(found, seen, args.all, today)
    for job in expired:
        seen[job.key] = {"status": "expired", "title": job.title, "company": job.company, "url": job.url}
    say(f"  {len(found)} found · {len(found) - len(keep) - len(expired)} already seen or repeated · {len(expired)} closed · {len(keep)} to look at"
        + (f" · ranking the first {args.rank}" if len(keep) > args.rank else ""))
    ranked = []
    with Turns(api, me / "fit" / "log.jsonl", args.minutes, args.budget) as turns:
        for job in keep[: args.rank]:
            entry = seen.setdefault(job.key, {"first_seen": today.isoformat()})
            entry.update(title=job.title, company=job.company, url=job.url, portal=job.portal, deadline=job.deadline)
            try:
                full = job.description if len(job.description) >= MIN_POSTING else portals.detail(by_name[job.portal], job)
            except (RuntimeError, OSError) as e:
                full = ""
                entry["fetch_error"] = clip(e, 200)
            if len(full) < MIN_POSTING:
                entry["status"] = "unreachable"
                continue
            if turns.stopped():
                break
            text = posting_text(job, full)
            (me / "postings").mkdir(parents=True, exist_ok=True)
            (me / "postings" / f"{job.key}.md").write_text(text)
            result = fitting.evaluate(turns, args.model, profile, text)
            if result is None:
                continue                                                    # still "new": the next search tries again
            result = dataclasses.replace(result, key=job.key)
            fitting.save(result, me / "fit")
            entry.update(status="vetoed" if result.vetoes else "ranked", total=result.total, band=result.band)
            ranked.append((job, result))
    save_seen(me, seen)
    return sorted(ranked, key=lambda r: (not r[1].vetoes, r[1].total), reverse=True)


def show(ranked: list[tuple[portals.Job, fitting.Fit]], minimum: int, me: str = "me") -> None:
    say(f"\n{BOLD}Ranked by fit{OFF} {DIM}(triage scores from the posting text; `apply` evaluates for real){OFF}")
    for i, (job, fit) in enumerate(ranked, 1):
        mark = f"{GREEN}GO   {OFF}" if fit.go(minimum) else f"{RED}NO-GO{OFF}"
        flags = "".join(f" ⚠ {g}" for g, v in (("location", fit.location), ("language", fit.language)) if v == "FLAG")
        say(f"  {i:>2}. {fit.total:>3} {mark} {clip(job.title, 50)} · {clip(job.company, 30)} · {job.location}"
            + (f" · deadline {job.deadline}" if job.deadline else "") + flags + "".join(f" {RED}· {v}{OFF}" for v in fit.vetoes))
        say(f"      {DIM}{job.url}{OFF}")
    if ranked:
        key = ranked[0][1].key
        say(f"\n  {DIM}next, for the top one: python -m jobloop apply --profile {me}/profile.md --posting {me}/postings/{key}.md --keywords {me}/fit/{key}.keywords{OFF}")


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--query", action="append", required=True, help="what to search for; repeat for several")
    p.add_argument("--profile", default="me/profile.md")
    p.add_argument("--me", default="me", help="your folder (gitignored)")
    p.add_argument("--portals", type=lambda s: s.split(","), help="only these portals, by name (default: all enabled)")
    p.add_argument("--location", default="", help="for portals that need one")
    p.add_argument("--limit", type=int, default=20, help="postings to take from each portal, per query")
    p.add_argument("--rank", type=int, default=10, help="postings to fetch and score this run; the rest wait for the next")
    p.add_argument("--all", action="store_true", help="look at postings you have seen before too")
    p.add_argument("--model", default="claude-code", help="the harness that scores fit, optionally harness/model")
    p.add_argument("--min-score", type=int, default=60)


def main(args: argparse.Namespace) -> int:
    show(run(args, Api(args.server)), args.min_score, args.me)
    return 0

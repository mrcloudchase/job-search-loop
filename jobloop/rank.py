"""`rank`: triage the postings the search found, from their text, and present a shortlist a person can act on.

For each selected posting (new, or unreachable and being retried) it fetches the text (fetch.py's ladder), has a harness
report FACTS about the posting against the profile (fit.evaluate; the score, band and vetoes are computed by code), and
writes the result into the posting store with rankstate. Then, always, it expires ranked postings whose stored deadline has
passed (no fetch). It promises: a posting that was not fetched is never scored; a 404 or a 403 is never "closed"; a job
past --limit is deferred, not dropped; a vetoed job is never in the shortlist and is listed with the reason; nothing is
marked applied and the tracker is never written. The scores are triage from posting text, nothing more."""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from . import fetch as fetching
from . import fit as fitting
from . import rankstate as rs
from .term import BOLD, DIM, GREEN, OFF, RED, YELLOW, say
from .text import slug

HARNESS = True

URGENT = "\U0001F525"
WARN = "⚠"
TRIAGE = ("These are triage scores from the posting text alone. When you apply, the full evaluation runs again with "
          "company research; a triage score never replaces it.")


@dataclass
class Run:
    selection: rs.Selection
    ranked: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    gave_up: list[str] = field(default_factory=list)
    unscored: list[str] = field(default_factory=list)       # fetched but no fit passed, or the run ran out of time or money
    errors: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)  # why a posting is expired
    touched: set[str] = field(default_factory=set)
    swept: rs.Swept = rs.Swept(0, (), (), ())


def fit_key(key: str) -> str:
    """A file-safe, unique name for a store key (keys may hold characters a fit key may not)."""
    return f"{slug(key, 60) or 'posting'}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def scored(key: str, fit, deadline: str | None) -> rs.Scored:
    return rs.Scored(key, fit.total, fit.band, tuple({"name": g.name, "verdict": g.verdict, "notes": list(g.notes)} for g in fit.gates),
                     fit.strengths, fit.gaps, deadline)


def run(state: dict[str, dict], *, today: date, now: datetime, score: Callable, stopped: Callable[[], str],
        fit_dir: Path, postings_dir: Path, limit: int = 10, focus: str = "", include_all: bool = False,
        tracker_keys=(), fetcher: Callable = fetching.fetch_posting, careers_for: Callable[[dict], tuple[str, ...]] = lambda e: (),
        acknowledged: bool = False) -> Run:
    """Select, fetch, score and write back; then sweep. `score(posting_text, fit_key) -> Fit | None` runs the harness
    turn. Mutates `state`; the caller saves it. Never raises for one bad posting: that posting is listed instead."""
    out = Run(rs.candidates(state, limit, focus, include_all, tracker_keys))
    say(f"Ranking {len(out.selection.selected)} postings; {len(out.selection.deferred)} deferred to the next run.")
    for key in out.selection.selected:
        entry = state[key]
        if stopped():
            out.unscored.append(key)
            continue
        got = fetcher(entry.get("url") or "", board=entry.get("board") or "", title=entry.get("title") or "",
                      careers=careers_for(entry), acknowledged=acknowledged)
        if isinstance(got, fetching.Fetched):
            name = fit_key(key)
            fit = score(got.text, name)
            if fit is None:
                out.unscored.append(key)
                continue
            postings_dir.mkdir(parents=True, exist_ok=True)
            (postings_dir / f"{name}.txt").write_text(got.text)
            fitting.save(fit, fit_dir)
            result = scored(key, fit, got.deadline)
        elif isinstance(got, fetching.Closed):
            result = rs.Closed(key, got.reason)
            out.reasons[key] = got.reason
        else:
            result = rs.Failed(key, got.reason)
        try:
            what = rs.apply_result(state, result, now)
        except rs.ResultError as error:
            out.errors.append(str(error))
            continue
        out.touched.add(key)
        getattr(out, what).append(key)
        if what == "expired" and key not in out.reasons:
            out.reasons[key] = "its deadline has passed"
    out.swept = rs.sweep(state, today, exclude=out.selection.selected)
    out.touched.update(out.swept.expired)
    return out


# ── presentation ────────────────────────────────────────────────────────────────────────────────
def where(entry: dict) -> str:
    return f"{entry.get('title') or '?'} — {entry.get('company') or '?'}"


def marks(entry: dict, today: date) -> list[str]:
    f = rs.flags(entry, today)
    return ([f"{URGENT} {f.urgent}"] if f.urgent else []) + ([f"{WARN} {f.old}"] if f.old else [])


def row(state: dict, key: str, today: date, number: int | None = None) -> list[str]:
    entry = state[key]
    head = f"{number}. " if number else "- "
    lines = [f"{head}{entry.get('rank_score')}  {entry.get('rank_verdict')}  {where(entry)}  {'  '.join(marks(entry, today))}".rstrip(),
             f"   {entry.get('url')}"]
    lines += [f"   {WARN} {note}" for note in rs.gate_flags(entry)]
    return lines


def render(out: Run, state: dict[str, dict], today: date, top: int = 5) -> str:
    """The report, built only from stored fields (never from a harness's prose) and always with each posting's URL."""
    arranged = rs.arrange(state, out.ranked, today, top)
    swept, sel = out.swept, out.selection
    lines = [f"{BOLD}Rank{OFF}: {len(out.ranked)} ranked ({len(arranged.shortlist)} shortlisted, {len(arranged.below)} below "
             f"the shortlist, {len(arranged.vetoed)} vetoed), {len(out.expired)} expired, {len(out.unreachable)} could not be "
             f"fetched, {len(out.gave_up)} gave up, {len(out.unscored)} not scored.",
             f"Sweep: {swept.checked} ranked postings checked against their stored deadlines, {len(swept.expired)} expired, "
             f"{len(swept.closing_soon)} closing soon."]
    if sel.excluded_by_tracker:
        lines.append(f"{sel.excluded_by_tracker} already in the tracker, left out.")
    if sel.deferred:
        lines.append(f"{len(sel.deferred)} more are waiting (deferred, still {', '.join(sorted({state[k]['status'] for k in sel.deferred}))}): "
                     "run rank again to continue.")
    if not sel.selected:
        lines.append("Nothing to rank. Run the search to find new postings.")
    if arranged.shortlist:
        lines += ["", f"{GREEN}{BOLD}Shortlist{OFF}"]
        for n, key in enumerate(arranged.shortlist, 1):
            lines += row(state, key, today, n)
            lines += [f"   + {s}" for s in state[key].get("strengths") or []] + [f"   - {g}" for g in state[key].get("gaps") or []]
    if arranged.below:
        lines += ["", "Below the shortlist"] + [l for k in arranged.below for l in row(state, k, today)]
    if arranged.vetoed or out.expired:
        lines += ["", f"{RED}Excluded{OFF} (a veto holds whatever the score)"]
        for key in arranged.vetoed:
            lines += [f"- {state[key].get('rank_score')}  {where(state[key])}  {state[key].get('url')}"]
            lines += [f"   VETO {v}" for v in rs.vetoes(state[key])]
        for key in out.expired:
            lines += [f"- expired: {where(state[key])}  {state[key].get('url')}", f"   {out.reasons.get(key, '')}"]
    if swept.expired:
        lines += ["", "Retired by the sweep (stored deadline passed)"] + [f"- {where(state[k])}  {state[k].get('url')}" for k in swept.expired]
    if swept.closing_soon:
        lines += ["", f"{YELLOW}Closing soon{OFF}"] + [f"- {URGENT} {state[k]['deadline']}  {where(state[k])}  {state[k].get('url')}"
                                                       for k in swept.closing_soon]
    if out.unreachable:
        lines += ["", f"{YELLOW}Could not fetch, will retry{OFF}"]
        lines += [l for k in out.unreachable for l in (f"- {where(state[k])}  {state[k].get('url')}",
                                                       f"   attempt {state[k].get('fetch_attempts')}: {state[k].get('last_fetch_error')}")]
    if out.gave_up:
        lines += ["", f"{RED}Gave up, check manually{OFF} (they may still be live)"]
        lines += [l for k in out.gave_up for l in (f"- {where(state[k])}  {state[k].get('url')}",
                                                   f"   last error: {state[k].get('last_fetch_error')}")]
    if out.unscored:
        lines += ["", "Not scored this run (fetched, but no passing evaluation or the run stopped); they stay as they were"]
        lines += [f"- {where(state[k])}  {state[k].get('url')}" for k in out.unscored]
    if out.errors:
        lines += ["", f"{RED}Errors{OFF}"] + [f"- {e}" for e in out.errors]
    unread = {(state[k].get("board") or "?", f) for k in out.ranked for f in rs.flags(state[k], today).unparseable}
    unread |= {(board or "?", "deadline") for _, board in swept.unparseable}
    if unread:
        lines += ["", f"{DIM}Dates that could not be read (treated as absent): "
                  + ", ".join(f"{what} from {board}" for board, what in sorted(unread)) + OFF]
    return "\n".join([*lines, "", TRIAGE])


# ── the command ─────────────────────────────────────────────────────────────────────────────────
def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--limit", type=int, default=10, help="postings scored this run; 0 means no cap; the rest wait")
    p.add_argument("--top", type=int, default=5, help="how many go on the shortlist (does not change how many are scored)")
    p.add_argument("--all", action="store_true", help="re-rank everything except skipped and applied postings")
    p.add_argument("--focus", default="", help="only postings whose title, company, strengths or gaps contain this text")
    p.add_argument("--me", default="me", help="the personal-data folder")
    p.add_argument("--model", default="claude-code", help="a harness name, optionally harness/model")
    p.add_argument("--server", default="http://127.0.0.1:8000", help="the harness-interface server")
    p.add_argument("--rounds", type=int, default=3, help="turns the harness gets to fix failed checks")
    p.add_argument("--minutes", type=float, default=30)
    p.add_argument("--budget", type=float, default=5.0, help="dollars, as the harnesses report them")
    p.add_argument("--acknowledge-terms", action="store_true", help="a board with terms: confirm personal, low-volume use")


def main(args: argparse.Namespace, today: date | None = None, now: datetime | None = None) -> int:
    from contextlib import ExitStack

    from . import state as store
    from . import tracker
    from .turns import Api, Turns

    me = Path(args.me)
    if args.limit < 0 or args.top < 1:
        say(f"{RED}--limit must be 0 or more and --top 1 or more{OFF}")
        return 2
    if not store.path(me).exists():
        say(f"{RED}no postings yet ({store.path(me)}): run the search first{OFF}")
        return 1
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    state = store.load(me)
    tracker_file = me / "tracker.csv"
    taken = [(r.company, r.role) for r in tracker.read_rows(tracker_file)] if tracker_file.exists() else []
    with ExitStack() as stack:
        cache: dict = {}

        def score(posting: str, name: str):
            if "turns" not in cache:
                profile_file = me / "profile.md"
                if not profile_file.exists():
                    raise SystemExit(f"{profile_file} is missing: write your profile first")
                cache["profile"] = profile_file.read_text()
                cache["turns"] = stack.enter_context(Turns(Api(args.server), me / "state" / "rank-log.jsonl", args.minutes, args.budget))
            return fitting.evaluate(cache["turns"], args.model, cache["profile"], posting, name, args.rounds)

        out = run(state, today=today, now=now, score=score, stopped=lambda: cache["turns"].stopped() if "turns" in cache else "",
                  fit_dir=me / "fit", postings_dir=me / "postings", limit=args.limit, focus=args.focus, include_all=args.all,
                  tracker_keys=taken, acknowledged=args.acknowledge_terms)
    with store.transaction(me) as fresh:
        rs.merge(fresh, state, out.touched)
    say(render(out, state, today, args.top))
    return 1 if out.errors else 0

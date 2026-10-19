"""stale.py: applications that have gone quiet for good, and the person's decision to close each one as `no_response`.

Which rows: `applied` and `interview` only. A `drafted` row was never sent, so nobody can be silent about it; an `offer`
is waiting on the person, not on the employer. A row is stale when it has been quiet 60 days or more, counted from the
latest of its date, the dated entries in its notes and the dates of the interview stages in its outcome.md. A row whose
dates or outcome.md cannot be read is named and left alone, never guessed.

What it promises: selecting is a pure read; nothing is written unless the person confirms THAT row, one confirmation per
row (there is no "all"); a confirmed row becomes `no_response` through the tracker's person path (`by_person=True`), so
the tracker's own rules (never backwards, Final is final) still apply and the archive's outcome.md is settled with it.
Nothing is sent to anyone.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jobloop import archive, tracker
from jobloop.term import say

STATUSES = ("applied", "interview")
THRESHOLD_DAYS = tracker.STALE_AFTER_DAYS
CALIBRATION_OFFER_AT = 3


def load_outcome(me: Path, row: tracker.Row) -> archive.Outcome | None:
    """The row's outcome.md, or None when it has none. Raises ValueError (naming the problem) when it cannot be trusted."""
    return archive.read_outcome(me, row.key) if row.key else None


def quiet(row: tracker.Row, today: date, outcome: archive.Outcome | None) -> int | None:
    """Days since the latest sign of life: the tracker's days quiet, or a recorded interview stage that is more recent.
    A stage dated after `today` is a plan, not contact. None when nothing parses."""
    days = [d for d in (tracker.days_quiet(row, today),
                        *((today - s.on).days for s in (outcome.stages if outcome else ()) if s.on <= today))
            if d is not None]
    return min(days) if days else None


@dataclass(frozen=True)
class Candidate:
    row: tracker.Row
    days_quiet: int
    followups: int


@dataclass(frozen=True)
class Selection:
    candidates: tuple[Candidate, ...]
    skipped: tuple[str, ...]


def select(me: Path, rows, today: date, threshold: int = THRESHOLD_DAYS) -> Selection:
    """The stale applications, most quiet first, and a line for every row that had to be left out and why."""
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        raise ValueError(f"the stale threshold is a whole number of days, 1 or more; got {threshold!r}")
    found, skipped = [], []
    for row in rows:
        if tracker.normalize_status(row.status) not in STATUSES:
            continue
        name = row.key or row.company
        try:
            days = quiet(row, today, load_outcome(Path(me), row))
        except ValueError as error:
            skipped.append(f"{name}: left alone, its outcome.md cannot be read ({error})")
            continue
        if days is None:
            skipped.append(f"{name}: no date that parses, so no days-quiet figure")
        elif days >= threshold:
            found.append(Candidate(row, days, tracker.followups_sent(row.notes)))
    return Selection(tuple(sorted(found, key=lambda c: -c.days_quiet)), tuple(skipped))


def describe(candidate: Candidate) -> str:
    row = candidate.row
    return (f"{row.company} - {row.role}: {row.status}, applied {row.date}, {candidate.days_quiet} days quiet, "
            f"{candidate.followups} follow-ups sent")


@dataclass(frozen=True)
class Result:
    resolved: tuple[str, ...]
    kept: tuple[str, ...]
    refused: tuple[str, ...]

    @property
    def calibration_due(self) -> bool:
        return len(self.resolved) >= CALIBRATION_OFFER_AT


def resolve(me: Path, selection: Selection, today: date, confirm: Callable[[str], bool]) -> Result:
    """Ask `confirm` about each candidate in turn; only a True answer for a row moves that row to `no_response`.
    Anything else (False, or a refusal by the tracker) leaves the row exactly as it was."""
    me = Path(me)
    resolved, kept, refused = [], [], []
    for candidate in selection.candidates:
        if confirm(describe(candidate)) is not True:
            kept.append(candidate.row.key)
            continue
        try:
            tracker.set_status(me / "tracker.csv", candidate.row.key, "no_response", by_person=True, on=today, me=me,
                               note=f"stale resolved after {candidate.days_quiet} days quiet")
        except (ValueError, LookupError) as error:
            refused.append(f"{candidate.row.key}: {error}")
        else:
            resolved.append(candidate.row.key)
    return Result(tuple(resolved), tuple(kept), tuple(refused))


# ── the command ──
def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--me", default="me", help="your personal folder (default: me)")
    p.add_argument("--days", type=int, default=THRESHOLD_DAYS, help=f"quiet days that make an application stale (default {THRESHOLD_DAYS})")


def _ask(question: str) -> bool:
    return input(f"{question}\n  close as no_response? [y/N] ").strip().lower() in ("y", "yes")


def main(args: argparse.Namespace, today: date | None = None, confirm: Callable[[str], bool] = _ask) -> int:
    """List the stale applications and ask about each one. Nothing is closed without a yes for that row."""
    me, today = Path(args.me), today or date.today()
    try:
        selection = select(me, tracker.read_rows(me / "tracker.csv"), today, args.days)
    except ValueError as error:
        say(str(error))
        return 2
    for line in selection.skipped:
        say(line)
    if not selection.candidates:
        say(f"No applied or interview application has been quiet {args.days} days or more.")
        return 0
    result = resolve(me, selection, today, confirm)
    for line in result.refused:
        say(f"refused: {line}")
    say(f"closed as no_response: {len(result.resolved)}; left as they were: {len(result.kept) + len(result.refused)}")
    if result.calibration_due:
        say("Several applications are settled: `calibrate` can compare the outcomes with your fit scores.")
    return 2 if result.refused else 0

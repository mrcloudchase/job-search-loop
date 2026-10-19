"""calibrate.py: what the final outcomes say about the fit scores, as proposals the person accepts or refuses one by one.

Evidence: every application whose tracker status is Final (except `withdrawn`, which says nothing about the employer)
and for which a fit was saved in me/fit/. An application PROGRESSED when it was hired or the offer was declined, or when
interview stages were recorded in its outcome.md (a rejection after an interview still progressed). The fit's raw
dimension scores are used, never its stored total, so changing weights cannot distort the comparison. A fit file is
matched to an application by its key with hyphens and underscores ignored (acme-ml-engineer is acme__ml_engineer).

Proposals (they only ever describe a change; computing them writes nothing):
- `weights`: the dimension whose scores best separate progressed from not-progressed moves up 5 points and the one that
  separates worst moves down 5, from the DEFAULT weights, so running again never drifts further. Each weight stays between
  10 and 45 and the four weights always sum to 100;
- `gate_review`: a gate that FLAGGED in two or more applications that progressed and in none that did not: its
  deal-breaker may be stricter than the evidence supports. Profile.md is never edited; this is a note for the person.
Fewer than 8 usable outcomes, or fewer than 3 on either side, proposes nothing.

Applying: `apply` writes me/state/calibration.json only for proposals the person confirmed, one by one, refusing weights
that break the rules above. An unconfirmed proposal changes nothing; confirming the same proposal twice changes nothing
more. fit.py reads the file through `load_weights`.
"""
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jobloop import archive, stale, tracker
from jobloop.fit import GATES, WEIGHTS
from jobloop.term import say

MIN_OUTCOMES = 8
MIN_EACH_SIDE = 3
MIN_GAP = 10.0
STEP = 5
MIN_WEIGHT, MAX_WEIGHT = 10, 45
MIN_GATE_HITS = 2
FILE = "calibration.json"
KINDS = ("weights", "gate_review")
PROGRESSED_STATUSES = ("hired", "offer_declined")


def validate_weights(weights: object) -> dict[str, int]:
    """The weights if they are the fit's four dimensions, whole numbers within bounds, summing to 100; else ValueError."""
    if not isinstance(weights, dict) or set(weights) != set(WEIGHTS):
        raise ValueError(f"weights must name exactly {', '.join(WEIGHTS)}")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in weights.values()):
        raise ValueError("every weight is a whole number")
    if any(not MIN_WEIGHT <= v <= MAX_WEIGHT for v in weights.values()):
        raise ValueError(f"every weight stays between {MIN_WEIGHT} and {MAX_WEIGHT}")
    if sum(weights.values()) != 100:
        raise ValueError(f"the weights must sum to 100, not {sum(weights.values())}")
    return {d: weights[d] for d in WEIGHTS}


def _path(me: Path) -> Path:
    return Path(me) / "state" / FILE


def _read(me: Path) -> dict:
    path = _path(me)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"{path} is not valid JSON ({error}); fix or delete it") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold one JSON object; fix or delete it")
    return data


def load_weights(me: Path) -> dict[str, int]:
    """The weights in force: the confirmed ones from calibration.json, else the fit's defaults. A file that holds weights
    breaking the rules is an error, never silently replaced."""
    data = _read(me)
    return validate_weights(data["weights"]) if "weights" in data else dict(WEIGHTS)


# ── the evidence ──
@dataclass(frozen=True)
class Sample:
    key: str
    status: str
    progressed: bool
    scores: dict[str, int]
    flagged: tuple[str, ...]


@dataclass(frozen=True)
class Proposal:
    kind: str
    summary: str
    evidence: tuple[str, ...]
    change: dict

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {self.kind!r}")


@dataclass(frozen=True)
class Proposals:
    items: tuple[Proposal, ...]
    notes: tuple[str, ...]


def _squash(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())


def _fits(me: Path) -> tuple[dict[str, dict], list[str]]:
    found, notes = {}, []
    for path in sorted((Path(me) / "fit").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            scores = data["scores"]
            if set(scores) != set(WEIGHTS) or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 100
                                                  for v in scores.values()):
                raise ValueError("scores are not the four 0-100 dimensions")
            data["gates"] = {g["name"]: g["verdict"] for g in data["gates"]}
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            notes.append(f"{path.name}: not used ({error})")
            continue
        found[_squash(str(data.get("key") or path.stem))] = data
    return found, notes


def samples(me: Path, rows) -> tuple[tuple[Sample, ...], tuple[str, ...]]:
    """The usable final outcomes, and a line for each final row or fit file that could not be used and why."""
    me = Path(me)
    fits, notes = _fits(me)
    out = []
    for row in rows:
        status = tracker.normalize_status(row.status)
        if status not in tracker.FINAL or status == "withdrawn" or not row.key:
            continue
        fit = fits.get(_squash(row.key))
        if fit is None:
            notes.append(f"{row.key}: {status}, but no saved fit to compare it with")
            continue
        try:
            outcome = stale.load_outcome(me, row)
        except ValueError as error:
            notes.append(f"{row.key}: not used, its outcome.md cannot be read ({error})")
            continue
        out.append(Sample(row.key, status, status in PROGRESSED_STATUSES or bool(outcome and outcome.stages),
                          {d: fit["scores"][d] for d in WEIGHTS},
                          tuple(g for g in GATES if fit["gates"].get(g) == "FLAG")))
    return tuple(out), tuple(notes)


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values)


def _weights_proposal(went: list[Sample], not_went: list[Sample]) -> Proposal | None:
    separation = {d: _mean(s.scores[d] for s in went) - _mean(s.scores[d] for s in not_went) for d in WEIGHTS}
    best, worst = max(WEIGHTS, key=lambda d: separation[d]), min(WEIGHTS, key=lambda d: separation[d])
    if separation[best] - separation[worst] < MIN_GAP:
        return None
    proposed = {**WEIGHTS, best: WEIGHTS[best] + STEP, worst: WEIGHTS[worst] - STEP}
    try:
        validate_weights(proposed)
    except ValueError:
        return None
    means = "; ".join(f"{d}: progressed {_mean(s.scores[d] for s in went):.1f} vs not {_mean(s.scores[d] for s in not_went):.1f}"
                      for d in WEIGHTS)
    return Proposal("weights", f"raise {best} to {proposed[best]} and lower {worst} to {proposed[worst]} "
                    f"(from the default {WEIGHTS[best]} and {WEIGHTS[worst]})",
                    (f"{len(went)} progressed, {len(not_went)} did not", means,
                     "progressed: " + ", ".join(s.key for s in went), "did not: " + ", ".join(s.key for s in not_went)),
                    {"weights": proposed})


def _gate_proposals(went: list[Sample], not_went: list[Sample]) -> list[Proposal]:
    out = []
    for gate in GATES:
        hits = [s for s in went if gate in s.flagged]
        if len(hits) >= MIN_GATE_HITS and not any(gate in s.flagged for s in not_went):
            out.append(Proposal("gate_review", f"the {gate} gate flagged {len(hits)} applications that all went on to an "
                                "interview or beyond: consider whether that deal-breaker is a hard line",
                                (f"flagged on {gate} and progressed: " + ", ".join(s.key for s in hits),
                                 f"flagged on {gate} and did not progress: none"), {"gate_review": gate}))
    return out


def propose(me: Path, rows=None) -> Proposals:
    """What the outcomes suggest, with the evidence. Reads only; nothing is written and nothing is applied."""
    me = Path(me)
    rows = tracker.read_rows(me / "tracker.csv") if rows is None else rows
    found, notes = samples(me, rows)
    went, not_went = [s for s in found if s.progressed], [s for s in found if not s.progressed]
    if len(found) < MIN_OUTCOMES or min(len(went), len(not_went)) < MIN_EACH_SIDE:
        return Proposals((), (f"Too little to learn from: {len(found)} final outcomes with a saved fit ({len(went)} progressed, "
                              f"{len(not_went)} did not); at least {MIN_OUTCOMES} and {MIN_EACH_SIDE} on each side are needed.",
                              *notes))
    items = [p for p in (_weights_proposal(went, not_went),) if p] + _gate_proposals(went, not_went)
    current = load_weights(me)
    items = [p for p in items if p.kind != "weights" or p.change["weights"] != current]
    return Proposals(tuple(items), tuple(notes))


# ── applying what the person confirmed ──
def _merge(data: dict, proposal: Proposal, today: date) -> dict:
    data = {"version": 1, **data}
    if proposal.kind == "weights":
        data["weights"] = validate_weights(proposal.change.get("weights"))
    else:
        gate = proposal.change.get("gate_review")
        if gate not in GATES:
            raise ValueError(f"a gate review names one of {', '.join(GATES)}, not {gate!r}")
        data["gate_reviews"] = sorted({*data.get("gate_reviews", []), gate})
    entry = {"kind": proposal.kind, "change": proposal.change, "evidence": list(proposal.evidence)}
    history = data.setdefault("applied", [])
    if not any({k: e.get(k) for k in entry} == entry for e in history):
        history.append({**entry, "on": today.isoformat()})
    return data


def apply(me: Path, proposals: Proposals, confirm: Callable[[Proposal], bool], today: date) -> tuple[Proposal, ...]:
    """Ask `confirm` about each proposal; write calibration.json for the ones it answers True to and for no others.
    A proposal that breaks the weight rules raises before anything is written."""
    chosen = tuple(p for p in proposals.items if confirm(p) is True)
    if not chosen:
        return ()
    path = _path(me)
    with archive.locked(path):
        data = _read(me)
        for proposal in chosen:
            data = _merge(data, proposal, today)
        archive.write_atomically(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())
    return chosen


# ── the command ──
def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--me", default="me", help="your personal folder (default: me)")
    p.add_argument("--list", action="store_true", help="show the proposals and the evidence; ask nothing, change nothing")


def _ask(proposal: Proposal) -> bool:
    say(f"\n{proposal.kind}: {proposal.summary}")
    for line in proposal.evidence:
        say(f"  {line}")
    return input("  apply this? [y/N] ").strip().lower() in ("y", "yes")


def main(args: argparse.Namespace, today: date | None = None, confirm: Callable[[Proposal], bool] = _ask) -> int:
    me, today = Path(args.me), today or date.today()
    try:
        proposals = propose(me)
    except ValueError as error:
        say(str(error))
        return 2
    for note in proposals.notes:
        say(note)
    if not proposals.items:
        return 0
    if args.list:
        for p in proposals.items:
            say(f"{p.kind}: {p.summary}")
            for line in p.evidence:
                say(f"  {line}")
        return 0
    try:
        applied = apply(me, proposals, confirm, today)
    except ValueError as error:
        say(str(error))
        return 2
    say(f"applied {len(applied)} of {len(proposals.items)}; {_path(me)} " + ("updated" if applied else "unchanged"))
    return 0

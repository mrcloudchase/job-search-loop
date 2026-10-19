"""The playbook: what the loop has learned about this candidate's search, as versioned files, changed only inside bounds code checks.

me/playbook/vNNNN/ holds weights.json (the fit's four weights), style.md (writing-style rules added to the drafter's brief),
queries.json (extra and dropped search queries), strategy.json (drafts per posting, the fit floor) and meta.json. A change is
proposed from outcomes, the person's edits, judge scores and search yield; `validate` bounds it; `apply` writes the next
version and makes it active; `review` compares the active version's metrics with its parent's and rolls it back, with
the reason in the digest, if it is worse. The profile's facts are never in the playbook.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import calibrate, fit as fitting
from . import metrics
from .store import Store

FILES = ("weights.json", "style.md", "queries.json", "strategy.json", "meta.json")
MAX_RULES, MAX_RULE_CHARS = 12, 160
MAX_QUERIES, MAX_QUERY_CHARS = 12, 60
STRATEGY_BOUNDS = {"best_of": (1, 4), "min_fit": (30, 90)}
_FORBIDDEN_IN_RULE = re.compile(r"\d|https?://|www\.|@|—|–")


@dataclass
class Playbook:
    version: int
    weights: dict[str, int]
    style_rules: list[str] = field(default_factory=list)
    extra_queries: list[str] = field(default_factory=list)
    dropped_queries: list[str] = field(default_factory=list)
    strategy: dict = field(default_factory=dict)

    def style_addendum(self) -> str:
        return "\n".join(f"- {r}" for r in self.style_rules)


def _root(me: Path) -> Path:
    return Path(me) / "playbook"


def _dir(me: Path, version: int) -> Path:
    return _root(me) / f"v{version:04d}"


def _write(me: Path, pb: Playbook, parent: int | None, change: dict) -> None:
    folder = _dir(me, pb.version)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "weights.json").write_text(json.dumps(pb.weights) + "\n")
    (folder / "style.md").write_text(pb.style_addendum() + ("\n" if pb.style_rules else ""))
    (folder / "queries.json").write_text(json.dumps({"extra": pb.extra_queries, "drop": pb.dropped_queries}) + "\n")
    (folder / "strategy.json").write_text(json.dumps(pb.strategy) + "\n")
    (folder / "meta.json").write_text(json.dumps({"version": pb.version, "parent": parent, "change": change}) + "\n")


def read(me: Path, version: int) -> Playbook:
    folder = _dir(me, version)
    queries = json.loads((folder / "queries.json").read_text())
    rules = [l[2:].strip() for l in (folder / "style.md").read_text().splitlines() if l.startswith("- ")]
    return Playbook(version, json.loads((folder / "weights.json").read_text()), rules, queries["extra"], queries["drop"],
                    json.loads((folder / "strategy.json").read_text()))


def load(me: Path | str, store: Store, defaults: dict | None = None, now: datetime | None = None) -> Playbook:
    """The active playbook; version 1 (the defaults, or the confirmed calibration weights) is created on first use."""
    me = Path(me)
    for row in store.playbook_versions():
        if row["status"] == "active":
            return read(me, row["version"])
    try:
        weights = calibrate.load_weights(me)
    except ValueError:
        weights = dict(fitting.WEIGHTS)
    pb = Playbook(1, weights, [], [], [], {"best_of": 2, "min_fit": 60, **(defaults or {})})
    _write(me, pb, None, {"summary": "the defaults"})
    store.playbook_add(1, None, {"summary": "the defaults"}, "candidate", now)
    store.playbook_set(1, "active", "initial", now)
    return pb


def validate(change: object, current: Playbook) -> list[str]:
    """Why a proposed change is out of bounds (empty if it is fine). Style rules are rules, never facts."""
    if not isinstance(change, dict):
        return ["a change is an object"]
    allowed = {"summary", "weights", "style_add", "style_drop", "queries_add", "queries_drop", "strategy"}
    problems = [f"unknown change key {k!r}" for k in change if k not in allowed]
    if "weights" in change:
        try:
            calibrate.validate_weights(change["weights"])
        except ValueError as error:
            problems.append(f"weights: {error}")
    rules = [r for r in current.style_rules if r not in change.get("style_drop", [])] + list(change.get("style_add", []))
    for rule in change.get("style_add", []):
        if not isinstance(rule, str) or not rule.strip() or len(rule) > MAX_RULE_CHARS:
            problems.append(f"a style rule is one sentence of at most {MAX_RULE_CHARS} characters")
        elif _FORBIDDEN_IN_RULE.search(rule):
            problems.append(f"style rule {rule[:40]!r} holds a number, link, address or dash: rules carry no facts")
    if len(rules) > MAX_RULES:
        problems.append(f"at most {MAX_RULES} style rules")
    queries = [q for q in current.extra_queries if q not in change.get("queries_drop", [])] + list(change.get("queries_add", []))
    for q in change.get("queries_add", []):
        if not isinstance(q, str) or not q.strip() or len(q) > MAX_QUERY_CHARS or "\n" in q:
            problems.append(f"a query is one line of at most {MAX_QUERY_CHARS} characters")
    if len(queries) > MAX_QUERIES:
        problems.append(f"at most {MAX_QUERIES} extra queries")
    for name, value in (change.get("strategy") or {}).items():
        low, high = STRATEGY_BOUNDS.get(name, (None, None))
        if low is None:
            problems.append(f"unknown strategy setting {name!r}")
        elif isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            problems.append(f"strategy.{name} is a whole number {low} to {high}")
    return problems


def apply(me: Path | str, store: Store, change: dict, now: datetime | None = None) -> Playbook:
    """Write the change as the next version and make it active. Raises ValueError if it is out of bounds."""
    me = Path(me)
    current = load(me, store, now=now)
    problems = validate(change, current)
    if problems:
        raise ValueError("; ".join(problems))
    nxt = Playbook(max(v["version"] for v in store.playbook_versions()) + 1,
                   change.get("weights", current.weights),
                   [r for r in current.style_rules if r not in change.get("style_drop", [])] + list(change.get("style_add", [])),
                   [q for q in current.extra_queries if q not in change.get("queries_drop", [])] + list(change.get("queries_add", [])),
                   list(current.dropped_queries) + [q for q in change.get("queries_drop", []) if q not in current.dropped_queries],
                   {**current.strategy, **(change.get("strategy") or {})})
    _write(me, nxt, current.version, change)
    store.playbook_add(nxt.version, current.version, change, "candidate", now)
    store.playbook_set(nxt.version, "active", change.get("summary", ""), now)
    if nxt.weights != current.weights:
        _publish_weights(me, nxt.weights)
    return nxt


def _publish_weights(me: Path, weights: dict) -> None:
    """The fit reads the confirmed weights from state/calibration.json; the active playbook's weights are published there."""
    path = Path(me) / "state" / calibrate.FILE
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except ValueError:
            data = {}
    data["weights"] = weights
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n")


def rollback(me: Path | str, store: Store, reason: str, now: datetime | None = None) -> Playbook | None:
    """Make the active version's parent active again. None when the active version is the first."""
    me = Path(me)
    versions = {v["version"]: v for v in store.playbook_versions()}
    active = next((v for v in versions.values() if v["status"] == "active"), None)
    if active is None or active["parent"] is None:
        return None
    store.playbook_set(active["version"], "rolled_back", reason, now)
    store.playbook_set(active["parent"], "active", f"restored after v{active['version']}: {reason}", now)
    pb = read(me, active["parent"])
    _publish_weights(me, pb.weights)
    return pb


def review(me: Path | str, store: Store, rows, margin: float, after: int, now: datetime | None = None) -> str:
    """Compare the active version's metrics with its parent's; roll back if it is worse. Returns a line for the digest ('' if nothing to say)."""
    versions = {v["version"]: v for v in store.playbook_versions()}
    active = next((v for v in versions.values() if v["status"] == "active"), None)
    if active is None or active["parent"] is None or not active["activated"]:
        return ""
    parent = versions[active["parent"]]
    mine = metrics.window(store, rows, since=active["activated"], version=active["version"])
    if mine.accepted < after:
        return ""
    before = metrics.window(store, rows, since=parent["activated"] or "0000", version=parent["version"])
    worse = []
    if mine.judge_mean is not None and before.judge_mean and mine.judge_mean < before.judge_mean * (1 - margin):
        worse.append(f"judge mean {mine.judge_mean:.1f} against {before.judge_mean:.1f}")
    if mine.first_pass_rate is not None and before.first_pass_rate is not None and mine.first_pass_rate < before.first_pass_rate - margin:
        worse.append(f"first-pass rate {mine.first_pass_rate:.0%} against {before.first_pass_rate:.0%}")
    if mine.cost_per_accepted is not None and before.cost_per_accepted and mine.cost_per_accepted > before.cost_per_accepted * (1 + margin):
        worse.append(f"cost per accepted ${mine.cost_per_accepted:.2f} against ${before.cost_per_accepted:.2f}")
    if worse:
        reason = "worse than v%d: %s" % (parent["version"], "; ".join(worse))
        rollback(me, store, reason, now)
        return f"Playbook v{active['version']} rolled back ({reason})."
    return ""

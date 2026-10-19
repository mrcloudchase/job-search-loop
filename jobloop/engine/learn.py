"""The pure analysis behind the `learn` stage: what the outcomes, the person's edits, the judges and the search yield say.

No model and no network here. `evidence` reads the stores (tracker rows, the archive's draft.json, me/fit, the queue folders,
the engine database, the posting store) and returns an `Evidence`; the proposal functions turn it into bounded suggestions:

- outcomes: each application's tracker status joined to the fit's band and score and the judges' mean;
- edits: for each me/queue/<key>/ the sha256 of edit-here/cv.tex and cover.tex against `delivered_hashes` in queue.json. An
  unchanged file is evidence of acceptance; a changed one is diffed against the delivered text, which is looked for (and only
  accepted when its hash matches) in queue.json's `delivered`, queue/<key>/original|delivered/ and the archive folder;
- yield: engine events `yield` ({"query","found","new"}, item = board) per (board, query), with how many of the board's
  postings (or the query's, when postings record which query found them) ended shortlisted or drafted;
- rubric: which rubric items the judges fail most often;
- cost per accepted application.

`weights_proposal` is calibrate's own evidence rules; `query_proposals` only ever drops (a query run at least DEAD_AFTER times
whose postings were never shortlisted); `style_candidates` are the raw diffs, data for a model and never instructions;
`fact_candidates` finds what an edit added that the profile does not back: those become proposals for the person, never rules.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .. import archive, calibrate, grounding, rankstate, state as postings_store, tracker
from ..latex import to_text
from ..rank import fit_key
from . import metrics
from .proposals import ADDITIONS, SECTIONS as ALLOWED_SECTIONS
from .store import Store

DEAD_AFTER = 3
FILES = ("cv.tex", "cover.tex")
MIN_READY = 3
MAX_DIFF_CHARS = 6000
SECTIONS = (("skill", "Skills"), ("educ", "Education"), ("project", "Projects"), ("language", "Languages"),
            ("certif", "Certifications"), ("summary", "Summary"), ("profile", "Summary"))


@dataclass
class Edit:
    key: str
    file: str
    edited: bool
    diff: str = ""
    added: list[str] = field(default_factory=list)
    text: str = ""                      # the file as the person left it
    original_found: bool = True


@dataclass
class Outcome:
    key: str
    status: str
    band: str = ""
    score: float | None = None
    judge_mean: float | None = None


@dataclass
class Evidence:
    outcomes: list[Outcome] = field(default_factory=list)
    edits: list[Edit] = field(default_factory=list)
    yields: dict = field(default_factory=dict)         # (board, query) -> {"runs","found","new","shortlisted"}
    rubric: list = field(default_factory=list)         # [(item, times failed, sample evidence)], most failed first
    judged: int = 0                                    # judgments the rubric counts are over
    ready: int = 0                                     # applications that reached the person's queue
    cost_per_accepted: float | None = None
    per_query: bool = False                            # whether postings record which query found them
    missing: list[str] = field(default_factory=list)

    @property
    def edited(self) -> list[Edit]:
        return [e for e in self.edits if e.edited]

    @property
    def accepted(self) -> list[Edit]:
        return [e for e in self.edits if not e.edited]

    @property
    def settled(self) -> list[Outcome]:
        """Applications the person moved past `drafted`: outcomes."""
        return [o for o in self.outcomes if o.status and o.status != "drafted"]

    def enough(self) -> bool:
        return bool(self.edited) or self.ready >= MIN_READY or bool(self.settled)

    def lacking(self) -> str:
        return (f"not enough evidence to learn from: {len(self.edited)} edited deliveries, {self.ready} queue-ready applications "
                f"(at least {MIN_READY} needed) and {len(self.settled)} outcomes")

    def fingerprint(self) -> str:
        """A short hash of everything the analysis read, so an unchanged situation is not analysed again."""
        data = {"o": [(o.key, o.status, o.band, o.score, o.judge_mean) for o in self.outcomes],
                "e": [(e.key, e.file, e.edited, e.diff) for e in self.edits],
                "y": sorted((list(k), sorted(v.items())) for k, v in self.yields.items()),
                "r": self.rubric, "ready": self.ready}
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ── reading the stores ──
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _delivered(queue: dict, name: str) -> str:
    hashes = queue.get("delivered_hashes")
    if not isinstance(hashes, dict):
        return ""
    for k, v in hashes.items():
        if Path(str(k)).name == name and isinstance(v, str):
            return v
    return ""


def _original(me: Path, key: str, name: str, queue: dict, wanted: str) -> str | None:
    """The delivered text of a file: the first candidate whose hash is the delivered hash."""
    candidates: list[str] = []
    delivered = queue.get("delivered")
    if isinstance(delivered, dict):
        for k, v in delivered.items():
            if Path(str(k)).name == name and isinstance(v, str):
                candidates.append(v)
    folder = me / "queue" / key
    for sub in ("original", "delivered", "."):
        path = folder / sub / name
        if path.is_file():
            candidates.append(path.read_text(encoding="utf-8", errors="replace"))
    for path in (me / "applications" / key / name, me / "applications" / key / "attempt" / name):
        if path.is_file():
            candidates.append(path.read_text(encoding="utf-8", errors="replace"))
    return next((c for c in candidates if _sha(c.encode()) == wanted), None)


def _diff(old: str, new: str, key: str, name: str) -> tuple[str, list[str]]:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"{key}/{name} (delivered)", f"{key}/{name} (edited)", lineterm="", n=2))
    added = [l[1:] for l in lines if l.startswith("+") and not l.startswith("+++") and l[1:].strip()]
    return "\n".join(lines), added


def edits(me: Path) -> list[Edit]:
    """One Edit per delivered file of every queue folder: edited (sha256 differs from the delivered hash) or accepted as delivered."""
    me, found = Path(me), []
    for folder in sorted((me / "queue").glob("*/")) if (me / "queue").is_dir() else []:
        queue = _json(folder / "queue.json")
        for name in FILES:
            path, wanted = folder / "edit-here" / name, _delivered(queue, name)
            if not wanted or not path.is_file():
                continue
            data = path.read_bytes()
            if _sha(data) == wanted:
                found.append(Edit(folder.name, name, False))
                continue
            text = data.decode("utf-8", errors="replace")
            old = _original(me, folder.name, name, queue, wanted)
            if old is None:
                found.append(Edit(folder.name, name, True, "(the delivered text is not kept, so there is no diff)", [], text, False))
            else:
                diff, added = _diff(old, text, folder.name, name)
                found.append(Edit(folder.name, name, True, diff, added, text))
    return found


def _fit_of(me: Path, draft: dict, key: str) -> dict:
    posting = str(draft.get("posting_key") or "")
    for candidate in (posting, key):
        if candidate:
            fit = _json(me / "fit" / f"{fit_key(candidate)}.json")
            if fit:
                return fit
    return {}


def outcomes(me: Path, rows) -> list[Outcome]:
    found = []
    for row in rows:
        if not row.key:
            continue
        draft = _json(me / "applications" / row.key / "draft.json")
        fit = _fit_of(me, draft, row.key)
        score = fit.get("total", draft.get("fit"))
        judge = (draft.get("judge") or {}).get("mean")
        found.append(Outcome(row.key, tracker.normalize_status(row.status) or str(row.status), str(fit.get("band") or ""),
                             score if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
                             judge if isinstance(judge, (int, float)) and not isinstance(judge, bool) else None))
    return found


def _shortlisted(entry: dict, keys: set[str], min_fit: float) -> bool:
    """Shortlisted or beyond: ranked at or above the floor with no veto, or already an application."""
    try:
        if archive.key(entry.get("company") or "", entry.get("title") or "") in keys:
            return True
    except ValueError:
        pass
    score = entry.get("rank_score")
    return (entry.get("status") == "ranked" and not rankstate.vetoes(entry) and isinstance(score, (int, float))
            and not isinstance(score, bool) and score >= min_fit)


def _queries_of(entry: dict) -> list[str]:
    found = entry.get("queries") or ([entry["query"]] if entry.get("query") else [])
    return [str(q) for q in found] if isinstance(found, list) else []


def yields(me: Path, store: Store, rows, min_fit: float = 60) -> tuple[dict, bool]:
    """Per (board, query): runs, found, new, and the shortlisted postings credited to it. (table, whether postings name their query)"""
    table: dict = defaultdict(lambda: {"runs": 0, "found": 0, "new": 0, "shortlisted": 0})
    for event in store.events("yield", limit=100000):
        data = json.loads(event["data"] or "null")
        if not isinstance(data, dict) or not data.get("query"):
            continue
        row = table[(event["item"], str(data["query"]))]
        row["runs"] += 1
        for k in ("found", "new"):
            v = data.get(k)
            row[k] += v if isinstance(v, int) and not isinstance(v, bool) else 0
    try:
        postings = postings_store.load(me)
    except postings_store.StateCorrupt:
        postings = {}
    keys = {r.key for r in rows if r.key}
    per_query = any(_queries_of(p) for p in postings.values())
    board_hits: Counter = Counter()
    for entry in postings.values():
        if not _shortlisted(entry, keys, min_fit):
            continue
        board = str(entry.get("board") or entry.get("source") or "")
        board_hits[board] += 1
        for q in _queries_of(entry):
            if (board, q) in table:
                table[(board, q)]["shortlisted"] += 1
    if not per_query:
        for (board, _), row in table.items():           # no query on the postings: the board's count stands for each of its queries
            row["shortlisted"] = board_hits[board]
    return dict(table), per_query


def rubric(store: Store) -> tuple[list, int]:
    """[(rubric item, judgments that failed it, one piece of evidence)] most failed first, and how many judgments there were."""
    counts: Counter = Counter()
    sample: dict[int, str] = {}
    judgments = store.judgments(subject="draft")
    for j in judgments:
        try:
            failed = json.loads(j["detail"] or "{}").get("failed") or []
        except ValueError:
            continue
        for entry in failed:
            if isinstance(entry, (list, tuple)) and entry and isinstance(entry[0], int):
                counts[entry[0]] += 1
                sample.setdefault(entry[0], str(entry[1]) if len(entry) > 1 else "")
    return [(n, c, sample.get(n, "")) for n, c in sorted(counts.items(), key=lambda t: (-t[1], t[0]))], len(judgments)


def evidence(me: Path | str, store: Store, rows, min_fit: float = 60) -> Evidence:
    me = Path(me)
    found = Evidence(outcomes=outcomes(me, rows), edits=edits(me))
    found.yields, found.per_query = yields(me, store, rows, min_fit)
    found.rubric, found.judged = rubric(store)
    found.ready = sum(1 for r in store.queue() if r["status"] in ("ready", "done") and archive.is_key(r["item"]))   # applications only
    found.cost_per_accepted = metrics.window(store, rows).cost_per_accepted
    if not found.edited:
        found.missing.append("none of the delivered drafts has been edited")
    if found.ready < MIN_READY:
        found.missing.append(f"{found.ready} queue-ready applications (need {MIN_READY})")
    if not found.settled:
        found.missing.append("no outcomes in the tracker yet")
    return found


# ── proposals ──
def weights_proposal(me: Path, rows, current: dict | None = None) -> tuple[dict | None, tuple[str, ...]]:
    """calibrate's own evidence rules: (the proposed weights or None, calibrate's evidence and notes)."""
    try:
        got = calibrate.propose(Path(me), rows)
    except ValueError as error:
        return None, (str(error),)
    for p in got.items:
        if p.kind == "weights" and (current is None or p.change["weights"] != current):
            return calibrate.validate_weights(p.change["weights"]), (p.summary, *p.evidence)
    return None, got.notes


def query_proposals(ev: Evidence, current_drop: list[str] = ()) -> list[str]:
    """Queries to drop: run at least DEAD_AFTER times with none of the postings they found ever shortlisted. Adds nothing."""
    runs: Counter = Counter()
    hits: Counter = Counter()
    for (_, query), row in ev.yields.items():
        runs[query] += row["runs"]
        hits[query] += row["shortlisted"]
    return sorted(q for q, n in runs.items() if n >= DEAD_AFTER and hits[q] == 0 and q not in current_drop)


def style_candidates(ev: Evidence, limit_chars: int = MAX_DIFF_CHARS) -> dict[str, str]:
    """The person's diffs, one file each, clipped: data for the model."""
    out = {}
    for e in ev.edited:
        if e.diff:
            out[f"edits/{e.key}__{e.file.removesuffix('.tex')}.diff"] = e.diff[:limit_chars]
    return out


def _section_of(text: str, line: str) -> str:
    heading = "Experience"
    for current in text.splitlines():
        m = re.match(r"\s*\\(?:cv)?(?:sub)*section\*?\{([^}]*)\}", current)
        if m:
            heading = next((name for word, name in SECTIONS if word in m.group(1).casefold()), "Experience")
        if current == line:
            return heading
    return heading


def fact_candidates(ev: Evidence, profile: str) -> list[dict]:
    """What the person's edits added that the profile does not back: proposals for the person, never playbook rules."""
    found, seen = [], set()
    if not profile.strip():
        return found
    for e in ev.edited:
        for line in e.added:
            text = " ".join(to_text(line).split()).strip("- ").strip()
            if not text or text in seen:
                continue
            problems = grounding.check(text, [profile])
            if problems:
                seen.add(text)
                section = _section_of(e.text, line)
                found.append({"summary": f"your edit of {e.key}/{e.file} adds something the profile does not say: {text[:100]}",
                              "section": section if section in ALLOWED_SECTIONS else ADDITIONS,        # `proposals.accept` writes only under these
                              "text": text, "evidence": text, "source": f"your edit of {e.key}/{e.file}",      # evidence: their own words
                              "problems": problems[:3]})
    return found


def names_in(ev: Evidence, profile: str) -> set[str]:
    """Capitalised words in the person's added lines and the profile: a style rule that names one is a fact, not a rule."""
    words: set[str] = set()
    for text in [profile, *[l for e in ev.edited for l in e.added]]:
        words.update(w for w in re.findall(r"\b[A-Z][\w&-]{2,}\b", text))
    return words


def rule_names(rules, banned: set[str]) -> list[str]:
    """Problems for style rules that name something from the person's profile or edits."""
    found = []
    for rule in rules:
        if not isinstance(rule, str):
            continue
        rest = rule.split(None, 1)[1] if len(rule.split(None, 1)) > 1 else ""
        hit = sorted(w for w in re.findall(r"\b[A-Z][\w&-]{2,}\b", rest) if w in banned)
        if hit:
            found.append(f"style rule {rule[:40]!r} names {', '.join(hit)}: rules are general, they carry no names")
    return found


# ── what the model is given ──
def failure_lines(ev: Evidence, top: int = 8) -> str:
    if not ev.rubric:
        return "No judge has failed a rubric item yet.\n"
    return "".join(f"- item {n}: failed in {c} of {ev.judged} judgments; e.g. {why[:200]}\n" for n, c, why in ev.rubric[:top])


def yield_table(ev: Evidence) -> str:
    rows = ["| board | query | runs | found | new | shortlisted |", "|---|---|---|---|---|---|"]
    rows += [f"| {b} | {q} | {r['runs']} | {r['found']} | {r['new']} | {r['shortlisted']} |" for (b, q), r in sorted(ev.yields.items())]
    return "\n".join(rows) + "\n"


def outcome_lines(ev: Evidence) -> str:
    f = lambda v: "n/a" if v is None else f"{v:g}"
    return "".join(f"- {o.key}: {o.status}, fit {f(o.score)} {o.band}, judge mean {f(o.judge_mean)}\n" for o in ev.outcomes) or "No applications yet.\n"

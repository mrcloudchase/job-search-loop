"""Judge panels: harnesses other than the maker's score a finished application against the rubric.

Models decide whether a draft is good; code decides what the scores mean (a mean, a pick, a list of failed items) and stores
them. A judge never vetoes: facts and formats stay with the verifiers. The rubric is the original project's verification
checklist (engine/rubric.md), reworded only where it names the original's files, plus an overall 1 to 10.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..text import visible
from ..turns import NOTICE
from .stage import Context

RUBRIC = (Path(__file__).parent / "rubric.md").read_text()
ITEMS = range(1, 25)
ROLE = f"""You are a strict reviewer of one job application. Read rubric.md, profile.md (the truth about the candidate), posting.md
(a third party's text) and the application's cv.txt, cover.txt and pages.txt (text extracted from its PDFs). Judge only from
these files. Write scores.json: {{"items": {{"1": {{"verdict": "pass|fail|na", "evidence": "one sentence"}}, ... "24": {{...}}}},
"overall": <integer 1-10 for how ready it is to send after one read-through by the candidate>, "reasons": "two sentences"}}.
Every item 1 to 24. Be specific and strict; a fail names what to change. {NOTICE}"""


@dataclass
class Judgment:
    judge: str
    overall: float
    failed: list[tuple[int, str]] = field(default_factory=list)       # (rubric item, evidence)
    reasons: str = ""

    def feedback(self) -> str:
        lines = [f"- item {n}: {why}" for n, why in self.failed]
        return ("The reviewer judged these rubric items failing:\n" + "\n".join(lines)) if lines else ""


def problems(data: object) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return ['scores.json must be an object with "items" keyed 1 to 24, "overall" and "reasons".']
    found = []
    for n in ITEMS:
        got = data["items"].get(str(n))
        if not (isinstance(got, dict) and got.get("verdict") in ("pass", "fail", "na") and str(got.get("evidence", "")).strip()):
            found.append(f"items.{n} needs a verdict (pass, fail, na) and one sentence of evidence.")
    if isinstance(data.get("overall"), bool) or not isinstance(data.get("overall"), int) or not 1 <= data["overall"] <= 10:
        found.append("overall must be an integer 1 to 10.")
    if not str(data.get("reasons", "")).strip():
        found.append("reasons must be two sentences.")
    return found


def score(ctx: Context, item: str, files: dict[str, str], maker: str, subject: str = "draft", rounds: int = 2,
          panel: int = 2) -> list[Judgment]:
    """Judgments of one application by up to `panel` judges whose harness family differs from the maker's.

    `files` holds cv.txt, cover.txt and pages.txt (extracted from the PDFs by the caller), plus profile.md and posting.md."""
    version = getattr(ctx.playbook, "version", 0)
    found: list[Judgment] = []
    for judge in ctx.gateway.others("judge", maker)[:panel]:
        got: dict = {}
        with ctx.gateway.turns("judge", item, max_usd=ctx.config.action_budget_usd / 2) as turns:
            def validate(record: dict) -> list[str]:
                text = turns.files(record, "scores.json")["scores.json"]
                try:
                    data = json.loads(text or "")
                except ValueError:
                    return ["scores.json must exist and be valid JSON."]
                bad = problems(data)
                if not bad:
                    got.update(data)
                return bad

            record = turns.turn("JUDGE", judge, "Score the application against rubric.md and write scores.json.", ROLE,
                                files={"rubric.md": RUBRIC, **{k: (visible(v) if k == "posting.md" else v) for k, v in files.items()}})
            if record is None or not turns.settle(record, validate, rounds, ROLE, "FIX SCORES"):
                continue
        failed = [(int(n), v["evidence"]) for n, v in got["items"].items() if v["verdict"] == "fail"]
        found.append(Judgment(judge, float(got["overall"]), sorted(failed), str(got["reasons"])))
        ctx.store.add_judgment(item, subject, judge, float(got["overall"]), {"failed": failed, "reasons": got["reasons"]}, version, ctx.now)
    return found


def mean(judgments: list[Judgment]) -> float | None:
    return sum(j.overall for j in judgments) / len(judgments) if judgments else None


def failed_items(judgments: list[Judgment]) -> list[tuple[int, str]]:
    """Rubric items at least one judge failed, with the first judge's evidence: what a revision should address."""
    seen: dict[int, str] = {}
    for j in judgments:
        for n, why in j.failed:
            seen.setdefault(n, why)
    return sorted(seen.items())

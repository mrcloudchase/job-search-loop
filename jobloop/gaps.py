"""Skill-gap analysis: which skills your best-fit postings ask for that your profile doesn't back. The table is
plain arithmetic over the fits `fit` and `search` have saved, so it needs no harness; only postings that are
a moderate fit or better count, or the jobs you shouldn't chase would dominate it. `--plan` also has a harness
write a study order for the top gaps; it does no web search, so it gives no links."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .term import BOLD, DIM, GREEN, OFF, RED, say
from .text import flat
from .turns import Api, Turns

HARNESS = True
FLOOR = 45                                                                  # a moderate fit; below it a posting is not worth chasing

PLANNER = """You are the study planner in a job-search loop. You write plan.md: an order in which one candidate should
close the skill gaps listed in gaps.json, the ones their best-fit postings ask for most.

- For each gap, in the order you recommend: why it comes at that place, what a first small project to build would be
  (concrete, doable alone in a week or two), and what the candidate can honestly say about it while they learn.
- Never tell the candidate to claim a skill they have not yet used. profile.md is the whole truth about them.
- You have no web access: give no links and no course names you cannot be sure exist.
- Write plan.md in the working directory, and nothing else."""


def tally(folder: Path | str) -> list[dict]:
    """The gaps across every saved fit that counts, most asked-for first."""
    count, weight, names, where = Counter(), defaultdict(int), defaultdict(Counter), defaultdict(list)
    for path in sorted(Path(folder).glob("*.json")):
        fit = json.loads(path.read_text())
        if fit.get("total", 0) < FLOOR or any(fit.get(g) == "FAIL" for g in ("location", "language")):
            continue
        for term, status, _ in fit["requirements"]:
            if status == "gap":
                key = flat(term)
                count[key] += 1
                weight[key] += fit["total"]
                names[key][re.sub(r"\s+", " ", term).strip()] += 1
                where[key].append(f"{fit['title']} · {fit['company']}")
    return [{"gap": names[k].most_common(1)[0][0], "postings": n, "weight": weight[k], "examples": where[k][:3]}
            for k, n in sorted(count.items(), key=lambda kv: (-kv[1], -weight[kv[0]], kv[0]))]


def check(plan: str, top: list[dict]) -> list[str]:
    failures = []
    left_out = [g["gap"] for g in top if flat(g["gap"]) not in flat(plan)]
    if left_out:
        failures.append(f"plan.md must cover every gap in gaps.json; missing: {', '.join(left_out)}.")
    links = re.findall(r"https?://\S+|www\.\S+", plan)
    if links:
        failures.append(f"Remove the links ({', '.join(links[:3])}); you have no web access, so none can be trusted.")
    return failures


def plan(turns: Turns, model: str, profile: str, top: list[dict], rounds: int = 2) -> str | None:
    built: list[str] = []

    def validate(record: dict) -> list[str]:
        text = turns.files(record, "plan.md")["plan.md"]
        failures = check(text, top) if text is not None else ["plan.md must exist in the working directory."]
        if not failures:
            built.append(text)
        return failures

    record = turns.turn("PLAN", model, "Write plan.md for the gaps in gaps.json.", PLANNER,
                        files={"profile.md": profile, "gaps.json": json.dumps(top, indent=1)})
    return built[-1] if record and turns.settle(record, validate, rounds, PLANNER) else None


def show(table: list[dict], top: int) -> None:
    if not table:
        say("  No gaps: either no fit is saved yet (run `search` or `fit`), or none is a moderate fit or better.")
        return
    say(f"\n{BOLD}Skills your best-fit postings ask for that your profile doesn't back{OFF}")
    biggest = table[0]["postings"]
    for row in table[:top]:
        say(f"  {RED}{'█' * max(1, round(12 * row['postings'] / biggest)):<12}{OFF} {row['postings']:>3} postings  {BOLD}{row['gap']}{OFF}  {DIM}{'; '.join(row['examples'][:2])}{OFF}")


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--fits", default="me/fit", help="the folder `fit` and `search` save to")
    p.add_argument("--profile", default="me/profile.md")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--plan", metavar="MODEL", help="also have this harness write a study order, to --out")
    p.add_argument("--out", default="me/plan.md")


def main(args: argparse.Namespace) -> int:
    table = tally(args.fits)
    show(table, args.top)
    if args.plan and table:
        with Turns(Api(args.server), Path(args.fits) / "plan.log.jsonl", args.minutes, args.budget) as turns:
            text = plan(turns, args.plan, Path(args.profile).read_text(), table[: args.top])
        if text is None:
            say(f"{RED}no study plan passed the checks{OFF}")
            return 1
        Path(args.out).write_text(text)
        say(f"\n  {GREEN}study order written{OFF}: {args.out}")
    return 0

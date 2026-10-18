"""Profile setup: build the profile every other step is grounded in, from your CV and your notes. The harness
writes it; the code checks it against the sources. Nothing in it may be new: every number, email, phone and
link must appear in what you gave, and every year you gave must still be there. What the sources don't say
is written "(not provided)", and listed for you to fill in. Read the result before you rely on it."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from .term import BOLD, GREEN, OFF, RED, YELLOW, say
from .text import flat, numbers
from .turns import Api, Turns

HARNESS = True
SECTIONS = ("Contact", "Summary", "Experience", "Education", "Skills", "Languages", "Preferences")

PROFILER = """You are the profile builder in a job-search loop. You turn a candidate's CV and notes into profile.md, the one
file every later step treats as the whole truth about them.

- The source files are the only facts. Never add, infer, round or improve: not an employer, a title, a date, a
  number, a skill, a link. Copy contact details exactly.
- What the sources do not say is written "(not provided)". Never fill a gap with a guess.
- Layout, in this order: "# Full Name", then "## Contact" (email, phone, location, links), "## Summary" (two or three
  sentences from the sources), "## Experience" (one "### Title, Employer (City), Month Year - Month Year" per job,
  newest first, each with its bullets, numbers kept exactly), "## Education", "## Skills" ("- Strong: ...",
  "- Working: ...", and "- Not yet: ..." only for skills the notes say the candidate lacks), "## Languages", and
  "## Preferences" ("- Wants: ...", "- Deal-breakers: ...", from the notes).
- Write profile.md in the working directory, and nothing else."""


def check(profile: str, sources: str) -> list[str]:
    """Everything checkable about a built profile against the text it was built from."""
    failures, source = [], flat(sources)
    if not re.match(r"# \S", profile):
        failures.append('profile.md must start with "# Full Name".')
    missing = [s for s in SECTIONS if not re.search(rf"^## {s}\b", profile, re.M)]
    if missing:
        failures.append(f"profile.md is missing these sections: {', '.join('## ' + m for m in missing)}.")
    loose = sorted(numbers(profile) - numbers(sources))
    if loose:
        failures.append(f"profile.md has numbers that are not in the sources: {', '.join(loose)}. Use only what the sources say.")
    invented = [x for x in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|https?://\S+|www\.\S+", profile) if flat(x.rstrip(".,;)")) not in source]
    if invented:
        failures.append(f"profile.md has contact details or links that are not in the sources: {', '.join(invented)}.")
    digits = re.sub(r"\D", "", sources)
    phones = [p for p in re.findall(r"\+?\d[\d ()\-]{6,}\d", profile) if re.sub(r"\D", "", p) not in digits]
    if phones:
        failures.append(f"profile.md has a phone number that is not in the sources: {', '.join(phones)}.")
    lost = sorted(set(re.findall(r"\b(?:19|20)\d\d\b", sources)) - set(re.findall(r"\b(?:19|20)\d\d\b", profile)))
    if lost:
        failures.append(f"these years are in the sources but not in profile.md: {', '.join(lost)}. Keep every job, degree and date.")
    return failures


def build(turns: Turns, model: str, sources: dict[str, str], rounds: int = 3) -> str | None:
    """The profile, or None if the harness never produced one that passes."""
    built: list[str] = []

    def validate(record: dict) -> list[str]:
        text = turns.files(record, "profile.md")["profile.md"]
        failures = check(text, "\n".join(sources.values())) if text is not None else ["profile.md must exist in the working directory."]
        if not failures:
            built.append(text)
        return failures

    record = turns.turn("PROFILE", model, "Build profile.md from the files given: the CV and the candidate's notes.", PROFILER, files=sources)
    return built[-1] if record and turns.settle(record, validate, rounds, PROFILER) else None


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cv", action="append", required=True, help="a CV as text, markdown or LaTeX; repeat for several")
    p.add_argument("--notes", help="a file of notes: what you want, your deal-breakers, what you can't do yet")
    p.add_argument("--out", default="me/profile.md", help="where the profile goes (your gitignored folder)")
    p.add_argument("--model", default="claude-code", help="a harness name, optionally harness/model")
    p.add_argument("--force", action="store_true", help="overwrite an existing profile")


def main(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists() and not args.force:
        raise SystemExit(f"{out} exists; --force replaces it (pass it as a --cv to keep what it says)")
    sources = {f"{i}_{Path(p).name}": Path(p).read_text() for i, p in enumerate([*args.cv, *([args.notes] if args.notes else [])], 1)}
    with Turns(Api(args.server), out.parent / "profile.log.jsonl", args.minutes, args.budget) as turns:
        profile = build(turns, args.model, sources)
    if profile is None:
        say(f"{RED}no profile passed the checks{OFF}")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(profile)
    say(f"\n  {GREEN}{BOLD}profile written{OFF}: {out}")
    blanks = [l.strip() for l in profile.splitlines() if "(not provided)" in l]
    if blanks:
        say(f"  {YELLOW}the sources didn't say; add these in your notes and run again:{OFF}")
        for line in blanks:
            say(f"    {line}")
    say("  Read it all before you rely on it: every later step treats it as true.")
    return 0

"""followup.py: follow-up and thank-you DRAFTS for applications that are waiting on an answer. Nothing is ever sent.

There is no send path in this module: no mail, message, portal or network call of its own (a harness turn is the only
thing it talks to, and it is asked for text). A draft is a file in the application's archive folder; the person reads it,
changes it and sends it themselves. Making a draft changes no status and writes nothing to the tracker.

Who gets one, decided by code from the tracker and the application's outcome.md:
- a follow-up: an open row that was actually sent (never `drafted`), quiet 10 days or more (the latest of its date, dated
  notes and recorded interview stages, and of the previous draft), with fewer than two follow-ups made or logged, and no
  interview still ahead. The third is never offered: after two, the person decides whether to close it as `no_response`;
- a thank-you: for an interview stage recorded in outcome.md within the last 7 days that has none yet. It does not count
  as a follow-up.

What the harness may use: the role in `instructions`; the submitted CV and cover letter from the archive and the recorded
stages (date, name, format, people; never the feedback) as files. Never the profile: an unsubmitted fact has no place in
a message about a submitted application. What code checks before a draft is kept: every email, link, number, date and
role line it states is in those documents (grounding), no person is named who is not the contact or an interviewer, no
earlier conversation is invented, the length, the writing-style rules, the language of the letter, the greeting, a sign-off,
and (for a follow-up) a question and no filler. Failures go back to the harness word for word.
"""
from __future__ import annotations

import argparse
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jobloop import archive, grounding, letter, stale, style, tracker
from jobloop.term import RED, say
from jobloop.text import flat, slug
from jobloop.turns import Api, Turns

HARNESS = True

DEFAULT_THRESHOLD_DAYS = tracker.FOLLOWUP_AFTER_DAYS
MAX_FOLLOWUPS = tracker.FOLLOWUPS_BEFORE_REST
THANKYOU_WINDOW_DAYS = 7
FOLLOWUP_WORDS = (60, 120)
THANKYOU_SENTENCES = (2, 3)
THANKYOU_MAX_WORDS = 90
TEXT_SUFFIXES = (".tex", ".md", ".txt")
KINDS = ("followup", "thankyou")

_FOLLOWUP_FILE = re.compile(r"followup_(\d{4}-\d{2}-\d{2})\.md")
_GREETING = re.compile(r"^(?:dear|hi|hello|kære|hej|sehr geehrte[r]?|liebe[r]?|geachte|beste)\b", re.I)
_HONORIFIC_NAME = re.compile(r"\b(?:Mr|Mrs|Ms|Mx|Dr|Hr|Fru|Herr|Frau|Dhr|Mevr)\.?\s+([A-ZÆØÅÄÖÜ][\w'-]+)")
_GENERIC_ADDRESS = ("team", "hiring", "recruit", "rekruttering", "ansættelse", "hr ", "personal", "werving")
_EARLIER_CONTACT = re.compile(
    r"\b(?:our|the|last|your)\s+(?:interview|conversation|call|meeting|chat|discussion|samtale|møde|samtalen|mødet)\b"
    r"|\bwhen we (?:spoke|met|talked|last)\b|\bthank you for (?:the|your) (?:interview|time|conversation)\b"
    r"|\btak for (?:samtalen|mødet)\b", re.I)
_FILLER = ("just checking in", "touch base", "touching base", "circle back", "circling back", "bumping this",
           "bare lige", "just wanted to check")


@dataclass(frozen=True)
class Candidate:
    row: tracker.Row
    kind: str
    days_quiet: int
    number: int                              # which follow-up this would be (1 or 2); 0 for a thank-you
    stage: archive.Stage | None = None
    staged: bool = False                     # whether any interview stage is recorded for the application

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {self.kind!r}")
        if (self.kind == "thankyou") != (self.stage is not None):
            raise ValueError("a thank-you is for one recorded stage, and a follow-up has none")


@dataclass(frozen=True)
class Selection:
    candidates: tuple[Candidate, ...]
    skipped: tuple[str, ...]


# ── who gets a draft ──
def _folder(me: Path, row: tracker.Row) -> Path:
    return archive.folder_for(me, row.key)


def _followup_dates(folder: Path) -> list[date]:
    """The days on which follow-up drafts were made, read from the file names in the application's folder."""
    names = [p.name for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []
    return sorted(date.fromisoformat(m[1]) for name in names if (m := _FOLLOWUP_FILE.fullmatch(name)))


def _thankyou_name(stage: archive.Stage) -> str:
    return f"thankyou_{stage.on.isoformat()}_{slug(stage.stage, 40) or 'stage'}.md"


def _for_row(me: Path, row: tracker.Row, today: date, threshold: int | None) -> tuple[list[Candidate], str]:
    """The drafts due for one sent, open row (a threshold of None means: whatever the quiet time), or why none."""
    outcome = stale.load_outcome(me, row)
    folder = _folder(me, row)
    made = _followup_dates(folder)
    stages = outcome.stages if outcome else ()
    due: list[Candidate] = []
    for stage in stages:
        if 0 <= (today - stage.on).days <= THANKYOU_WINDOW_DAYS and not (folder / _thankyou_name(stage)).exists():
            due.append(Candidate(row, "thankyou", (today - stage.on).days, 0, stage, True))
    sent = max(len(made), tracker.followups_sent(row.notes))
    if sent >= MAX_FOLLOWUPS:
        return due, f"{row.key}: {sent} follow-ups already; no third is offered. Close it as no_response if you are done waiting"
    if any(s.on > today and s.outcome == "pending" for s in stages):
        return due, f"{row.key}: an interview is still ahead, so no follow-up"
    days = stale.quiet(row, today, outcome)
    if days is None:
        return due, f"{row.key}: no date that parses, so no days-quiet figure"
    if made:
        days = min(days, (today - made[-1]).days)
    if threshold is None or days >= threshold:
        due.append(Candidate(row, "followup", days, sent + 1, None, bool(stages)))
    return due, ""


def select(me: Path, rows: Iterable[tracker.Row], today: date, threshold: int = DEFAULT_THRESHOLD_DAYS,
           only: Iterable[str] | None = None) -> Selection:
    """Every draft that is due, and a line for each row left out and why. `only` names application keys: those are drafted
    whatever their quiet time, but never when drafted, final, already at the follow-up cap, or with an interview ahead."""
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        raise ValueError(f"the follow-up threshold is a whole number of days, 1 or more; got {threshold!r}")
    me, wanted = Path(me), None if only is None else set(only)
    rows = list(rows)
    found, skipped = [], []
    if wanted is not None:
        skipped += [f"{k}: no such application in the tracker" for k in sorted(wanted - {r.key for r in rows})]
    for row in rows:
        if wanted is not None and row.key not in wanted:
            continue
        if not tracker.is_open(row.status):
            skipped += [f"{row.key}: already {row.status}, nothing to chase"] if wanted is not None else []
            continue
        if not tracker.is_chaseable(row.status):
            skipped += [f"{row.key}: drafted, never sent, so nobody is waiting to hear back"] if wanted is not None else []
            continue
        try:
            due, why = _for_row(me, row, today, None if wanted is not None else threshold)
        except ValueError as error:
            skipped.append(f"{row.key or row.company}: left alone, {error}")
            continue
        found += due
        if why and (wanted is not None or not due):
            skipped.append(why)
    return Selection(tuple(found), tuple(skipped))


def render(selection: Selection) -> str:
    out = ["  #  application                                kind       quiet  would be"]
    for n, c in enumerate(selection.candidates, 1):
        what = f"thank-you for {c.stage.stage}" if c.stage else f"follow-up {c.number} of {MAX_FOLLOWUPS}"
        out.append(f"{n:>3}  {c.row.key[:42]:<42} {c.kind:<10} {c.days_quiet:>4}d  {what}")
    return "\n".join([*out, *selection.skipped]) if selection.candidates else "\n".join(["No follow-up is due.", *selection.skipped])


# ── what the harness sees ──
def documents(me: Path, row: tracker.Row) -> dict[str, str]:
    """The submitted CV and cover letter as text, by archived file name. A PDF is not read (no text is guessed from it)."""
    folder = _folder(me, row)
    return {name: (folder / name).read_text(encoding="utf-8", errors="replace")
            for name in archive.read(me, row.key).documents if Path(name).suffix in TEXT_SUFFIXES}


def stages_text(outcome: archive.Outcome | None) -> str:
    """The recorded stages as the harness may use them: date, name, format and people. Feedback and notes stay private."""
    lines = [f"- {s.on.isoformat()}: {s.stage}" + (f" ({s.format})" if s.format else "")
             + (f" with {', '.join(s.people)}" if s.people else "") for s in (outcome.stages if outcome else ())]
    return "\n".join(lines) if lines else "No interview stage is recorded."


def application_text(c: Candidate, today: date) -> str:
    row = c.row
    return "\n".join([f"Company: {row.company}", f"Role: {row.role}", f"Applied: {row.date}",
                      f"Contact person: {row.contact_person or 'none recorded'}",
                      f"Channel: {row.channel or 'unknown'}", f"Today: {today.isoformat()}",
                      f"Draft: {'thank-you for ' + c.stage.stage + ' on ' + c.stage.on.isoformat() if c.stage else 'follow-up ' + str(c.number)}"])


def language_of(docs: dict[str, str]) -> str | None:
    """The language of the submitted cover letter (else the CV), or None when it cannot be decided."""
    ordered = sorted(docs.items(), key=lambda kv: not kv[0].startswith("cover_letter"))
    return next((lang for _, text in ordered if (lang := letter.detect(text))), None)


# ── what code checks ──
def _lines(text: str) -> list[str]:
    return [l.strip() for l in text.strip().splitlines() if l.strip()]


def _split(text: str) -> tuple[str, list[str]]:
    """(subject or "", the other non-empty lines)."""
    lines = _lines(text)
    if lines and re.match(r"subject\s*:", lines[0], re.I):
        return lines[0].split(":", 1)[1].strip(), lines[1:]
    return "", lines


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if re.search(r"\w", s)])


def _names(c: Candidate) -> tuple[str, ...]:
    people = c.stage.people if c.stage else ()
    return tuple(n for n in (c.row.contact_person, *people) if n)


def check_shape(text: str, c: Candidate, docs: dict[str, str]) -> list[str]:
    """Everything about a draft that needs no source documents: length, structure, people, invented stages, style."""
    subject, body = _split(text)
    failures = []
    if c.row.channel == "portal" and subject:
        failures.append("this goes through the employer's portal as plain text: remove the Subject line")
    if c.row.channel != "portal" and not subject:
        failures.append(f"start with a line 'Subject: ...' that carries the role title ({c.row.role})")
    elif subject and flat(c.row.role) not in flat(subject):
        failures.append(f"the Subject line must reuse the application's headline, the role title ({c.row.role})")
    if not body or not _GREETING.match(body[0]):
        return failures + ["the message must open with a greeting line (Dear ..., Hi ..., Kære ...)"]
    greeting = body[0]
    names = _names(c)
    if names:
        if not any(part in re.findall(r"\w+", flat(greeting)) for n in names for part in map(flat, n.split()) if len(part) > 1):
            failures.append(f"the greeting must address {' or '.join(names)}, who the records name; got {greeting!r}")
    elif not any(g in flat(greeting) + " " for g in _GENERIC_ADDRESS) and flat(c.row.company) not in flat(greeting):
        failures.append("no contact person is recorded, so address the team or company, not a named person")
    allowed = {flat(part).strip(".") for n in names for part in n.split()}
    failures += [f"{m[0]!r} names a person the records do not: address only {', '.join(names) or 'the team'}"
                 for m in _HONORIFIC_NAME.finditer(text) if flat(m[1]) not in allowed]
    closings = {x for xs in letter.CLOSINGS.values() for x in xs}
    ending = next((i for i in range(len(body) - 1, 0, -1) if flat(body[i]).rstrip(",.") in closings), None)
    if ending is None or ending < len(body) - 3:
        failures.append("end with a sign-off line (Sincerely, Kind regards, Med venlig hilsen, ...)")
    middle = " ".join(body[1:ending] if ending else body[1:])
    if c.kind == "followup":
        words = letter.words(" ".join(body))
        low, high = FOLLOWUP_WORDS
        if not low <= words <= high:
            failures.append(f"the follow-up has {words} words; it must be {low} to {high}")
        if "?" not in middle:
            failures.append("ask one polite question about the timeline; a follow-up without a question is only a nudge")
        failures += [f"cut the filler {f!r}" for f in _FILLER if f in flat(text)]
        if not c.staged and (m := _EARLIER_CONTACT.search(text)):
            failures.append(f"{m[0]!r} refers to an earlier conversation or interview; no stage is recorded for this application")
    else:
        count, (low, high) = _sentences(middle), THANKYOU_SENTENCES
        if not low <= count <= high:
            failures.append(f"a thank-you is {low} to {high} sentences between the greeting and the sign-off; this has {count}")
        if letter.words(middle) > THANKYOU_MAX_WORDS:
            failures.append(f"a thank-you stays brief: at most {THANKYOU_MAX_WORDS} words between the greeting and the sign-off")
        if "?" in middle:
            failures.append("a thank-you asks nothing: leave the timeline question out")
    expected = language_of(docs)
    written = letter.detect(text)
    if expected and written and written != expected:
        failures.append(f"write in the language of the submitted letter ({expected}), not {written}")
    return failures + style.check(text, (expected or "en",))


def check_grounding(text: str, c: Candidate, docs: dict[str, str], stages: str, today: date) -> list[str]:
    """Every fact the draft states must be in the submitted documents, the recorded stages or the application header."""
    return grounding.check(text, [*docs.values(), stages, application_text(c, today)], today)


# ── the harness turn ──
ROLE = """You draft ONE short message for a job applicant about an application that is waiting on an answer. You write
text; you do not send anything and have no way to.

- application.md says who the message is for, which application it is about, and whether it is a follow-up or a thank-you
  note. stages.md lists the interview stages that are recorded, if any. The other files are the CV and cover letter that
  were SUBMITTED for this application.
- Every claim comes from those files and nothing else: no skill, employer, project, number, date, link, e-mail address,
  stage, conversation or person that is not in them. Do not write digits unless the files hold the same number.
- Write in the language of the cover letter, first person, warm and direct. No cliches, no dashes used as punctuation.
- Open with a greeting. Address the contact person if application.md names one; if a stage lists people, a thank-you goes
  to them; otherwise address the team in the letter's language. Close with a sign-off line and the applicant's name as it
  signs the cover letter.
- Follow-up (60 to 120 words): one sentence of real interest in the role, one concrete reminder of value taken from the
  submitted materials, and one polite question about the timeline. No pressure. No "just checking in".
- Thank-you (2 to 3 sentences between the greeting and the sign-off): brief appreciation for the stage in application.md,
  one specific thing from the stage that you may state from stages.md only. No question.
- If Channel in application.md is portal, plain text with no subject line. Otherwise the first line is
  `Subject: ` followed by a line that contains the role title.
- Write the message to draft.md in the working directory and nothing else. The loop checks it and sends back whatever fails."""


def draft(turns: Turns, model: str, me: Path, c: Candidate, today: date, rounds: int = 3) -> str | None:
    """The harness's draft after code has checked it; failures go back word for word. None if it never passed or the run stopped."""
    docs = documents(Path(me), c.row)
    if not docs:
        say(f"{c.row.key}: no submitted CV or cover letter as .tex, .md or .txt in the archive, so there is nothing to ground a message in")
        return None
    outcome = stale.load_outcome(Path(me), c.row)
    stages = stages_text(outcome)
    kept: list[str] = []

    def validate(record: dict) -> list[str]:
        text = turns.files(record, "draft.md")["draft.md"]
        if text is None or not text.strip():
            return ["draft.md must exist in the working directory and hold the message."]
        failures = check_shape(text, c, docs) + check_grounding(text, c, docs, stages, today)
        if not failures:
            kept[:] = [text.strip()]
        return failures

    record = turns.turn("DRAFT", model, f"Write the {'thank-you note' if c.stage else 'follow-up'} described in application.md.",
                        ROLE, files={"application.md": application_text(c, today), "stages.md": stages, **docs})
    return kept[0] if record and turns.settle(record, validate, rounds, ROLE, "FIX DRAFT") and kept else None


def write(me: Path, c: Candidate, text: str, today: date) -> Path | None:
    """Save the draft in the application's folder, never over an earlier one. None when that day's draft already exists.
    The tracker is not touched."""
    name = _thankyou_name(c.stage) if c.stage else f"followup_{today.isoformat()}.md"
    path = archive.folder_for(Path(me), c.row.key, create=True) / name
    return path if archive.create_new(path, (text.strip() + "\n").encode()) else None


# ── the command ──
def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("keys", nargs="*", help="application keys to draft for now, whatever their quiet time (`list` shows them)")
    p.add_argument("--all", action="store_true", help="draft for every application that is due")
    p.add_argument("--days", type=int, default=DEFAULT_THRESHOLD_DAYS, help=f"quiet days before a follow-up is due (default {DEFAULT_THRESHOLD_DAYS})")
    p.add_argument("--model", default="claude-code", help="a harness name, optionally harness/model")
    p.add_argument("--me", default="me", help="your personal folder (default: me)")
    p.add_argument("--server", default="http://127.0.0.1:8000", help="the harness-interface server")
    p.add_argument("--rounds", type=int, default=3, help="turns the harness gets to fix failed checks")
    p.add_argument("--minutes", type=float, default=20)
    p.add_argument("--budget", type=float, default=5.0, help="dollars, as the harnesses report them")


def main(args: argparse.Namespace, today: date | None = None, api: Api | None = None) -> int:
    """Show what is due; draft for the keys named (or all with --all). The drafts are files for you to send yourself."""
    me, today = Path(args.me), today or date.today()
    try:
        selection = select(me, tracker.read_rows(me / "tracker.csv"), today, args.days, args.keys or None)
    except ValueError as error:
        say(str(error))
        return 2
    say(render(selection))
    if not (args.keys or args.all) or not selection.candidates:
        return 0
    failed = 0
    with Turns(api or Api(args.server), me / "state" / "followup-log.jsonl", args.minutes, args.budget) as turns:
        for c in selection.candidates:
            text = draft(turns, args.model, me, c, today, args.rounds)
            path = write(me, c, text, today) if text else None
            if path:
                say(f"drafted {path}  (a draft only: you send it)")
            else:
                failed += 1
                say(f"{c.row.key}: no draft was kept", RED)
        say(turns.summary())
    return 1 if failed else 0

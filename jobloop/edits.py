"""The reviewer's structured edits, and the loop applying them itself.

The reviewer answers with a JSON array of edits {file, old_string, new_string, reason} plus four narrative categories. This
module parses that out of surrounding prose (anything malformed is rejected with a message, never guessed at) and applies
the edits to the drafts. An edit is applied only when its file is exactly cv.tex or cover.tex, its old_string occurs
exactly once in that file, it changes something, and the result introduces no finding the text did not already have: no
ungrounded number, date, link, employer or title (grounding), no style violation (style), no unescaped LaTeX special or
forbidden command (latex), no gap term on the CV (keywords), and no run of words copied from the posting that the sources
do not back. Only an edit with reason "grounding" that deletes text skips the claim checks (removing an ungrounded claim is
always allowed); it still must not break the LaTeX. Edits apply in order, each judged on the text the earlier ones left;
a rejected edit never blocks the others. Everything the reviewer wrote is untrusted data. Pure functions, no I/O.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from jobloop import grounding, keywords, latex, style
from jobloop.text import flat

FILES = ("cv.tex", "cover.tex")
REASONS = ("keyword match", "company angle", "reframing", "style", "grounding")
CATEGORIES = {"missed keywords and requirements": ("missed keyword", "keyword", "requirement"),
              "company and department angles": ("company", "department"),
              "action-oriented reframing": ("action", "reframing"),
              "tone and style": ("tone", "style")}
COPIED_RUN = 6                      # words in a row taken from the posting that the sources do not back
_FIELDS = {"file", "old_string", "new_string", "reason"}


@dataclass(frozen=True)
class Edit:
    file: str
    old_string: str
    new_string: str
    reason: str


@dataclass(frozen=True)
class Rejected:
    number: int                     # 1-based position in the reviewer's array
    why: str
    edit: Edit | None = None        # None when the item could not even be read as an edit


@dataclass(frozen=True)
class Outcome:
    texts: Mapping[str, str]
    applied: tuple[Edit, ...]
    rejected: tuple[Rejected, ...]

    def feedback(self) -> str:
        """The rejected edits as verbatim feedback for the drafter; empty when every edit was applied."""
        if not self.rejected:
            return ""
        lines = ["The loop could not apply these edits:"]
        for r in self.rejected:
            where = f" ({r.edit.file}, reason {r.edit.reason!r}: {_clip(r.edit.old_string)!r})" if r.edit else ""
            lines.append(f"- edit {r.number}{where}: {r.why}")
        lines.append("Re-read the file as it is now (earlier edits may have shifted the text) and send a corrected edit, "
                     "or make the change by hand if it is still right.")
        return "\n".join(lines)


@dataclass(frozen=True)
class Review:
    edits: tuple[Edit, ...]
    rejected: tuple[Rejected, ...]
    narrative: Mapping[str, str]
    problems: tuple[str, ...]       # what makes the whole answer unusable: re-ask the reviewer with these


def _clip(text: str, width: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 3] + "..."


def _edit(item: dict, number: int) -> Edit | Rejected:
    if set(item) != _FIELDS:
        return Rejected(number, f"an edit has exactly the fields file, old_string, new_string, reason; this one has "
                                f"{', '.join(sorted(item)) or 'none'}")
    if not all(isinstance(item[k], str) for k in _FIELDS):
        return Rejected(number, "file, old_string, new_string and reason must all be text")
    edit = Edit(item["file"], item["old_string"], item["new_string"], item["reason"].strip().casefold())
    if edit.reason not in REASONS:
        return Rejected(number, f"reason must be one of {', '.join(REASONS)}, not {item['reason']!r}", edit)
    if not edit.old_string:
        return Rejected(number, "old_string is empty, so it cannot be located exactly once", edit)
    return edit


def parse_edits(answer: str) -> tuple[tuple[Edit, ...], tuple[Rejected, ...], list[str]]:
    """The edits in a reviewer's answer: the one JSON array of objects in it, wherever it sits in the prose.

    Returns (edits, rejected items, problems). More than one non-empty array is ambiguous and a problem; no array at
    all is a problem ([] says "no edits")."""
    arrays, i, decoder = [], 0, json.JSONDecoder()
    while (i := answer.find("[", i)) >= 0:
        try:
            value, end = decoder.raw_decode(answer, i)
        except ValueError:
            i += 1
            continue
        if isinstance(value, list) and all(isinstance(v, dict) for v in value):
            arrays.append(value)
            i = end
        else:
            i += 1
    full = [a for a in arrays if a]
    if len(full) > 1:
        return (), (), [f"the answer holds {len(full)} separate JSON arrays of edits; send exactly one"]
    if not arrays:
        return (), (), ["the answer holds no JSON array of edits; send [] if there are none"]
    edits, rejected = [], []
    for n, item in enumerate(full[0] if full else [], 1):
        got = _edit(item, n)
        (edits if isinstance(got, Edit) else rejected).append(got)
    return tuple(edits), tuple(rejected), []


def narrative(answer: str) -> dict[str, str]:
    """The text under each of the four narrative headings (a heading is a short line that opens with its label)."""
    heads = []
    for m in re.finditer(r"^[ \t]*(?:#+|[*\d.)\-]+)?[ \t*_]*([^\n]{3,80})$", answer, re.M):
        title = flat(re.sub(r"[*_#]", "", m.group(1)))
        for name, labels in CATEGORIES.items():
            if any(title.startswith(label) for label in labels) and not any(h[0] == name for h in heads):
                heads.append((name, m.start(), m.end(), re.sub(r"^[^:]*:", "", m.group(1)).strip(" *_") if ":" in m.group(1) else ""))
                break
    found = {}
    for k, (name, _, end, inline) in enumerate(heads):
        stop = heads[k + 1][1] if k + 1 < len(heads) else len(answer)
        found[name] = (inline + "\n" + answer[end:stop]).strip()
    return found


def parse_review(answer: str) -> Review:
    """Part A (the edits) and Part B (the four narrative categories) of a reviewer's answer. A missing category is a
    problem: the reviewer is asked again, because 'no issues' is an answer and silence is not."""
    edits, rejected, problems = parse_edits(answer)
    notes = narrative(re.sub(r"```.*?```", "", answer, flags=re.S))
    problems += [f'the review has no "{name}" section; write it, even if only "no issues"'
                 for name in CATEGORIES if not notes.get(name)]
    return Review(edits, rejected, notes, tuple(problems))


def _where(finding: str) -> str:
    return re.sub(r"\bline \d+: ", "", finding)


def _introduced(before: list[str], after: list[str]) -> list[str]:
    """The findings in `after` that `before` did not already have (line numbers ignored)."""
    seen = Counter(_where(f) for f in before)
    out = []
    for f in after:
        if seen[_where(f)] > 0:
            seen[_where(f)] -= 1
        else:
            out.append(f)
    return out


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", flat(text))


def _copied(new: str, old: str, posting: str, sources: str) -> list[str]:
    """Runs of COPIED_RUN words the edit adds that are in the posting (hidden comments included) and not in the sources."""
    def runs(words):
        return {" ".join(words[i:i + COPIED_RUN]) for i in range(len(words) - COPIED_RUN + 1)}
    theirs, mine, before = runs(_words(posting)), runs(_words(sources)), runs(_words(old))
    return sorted(r for r in runs(_words(new)) if r in theirs and r not in mine and r not in before)


def _judge(edit: Edit, old_text: str, new_text: str, sources: list[str], gaps: Iterable[str], posting: str,
           languages, today: date | None, banned) -> list[str]:
    problems = _introduced(latex.check(old_text), latex.check(new_text))
    if edit.reason == "grounding" and not edit.new_string.strip():
        return problems
    problems += _introduced(grounding.check(old_text, sources, today), grounding.check(new_text, sources, today))
    problems += _introduced(style.check(old_text, languages, banned), style.check(new_text, languages, banned))
    if edit.file == "cv.tex":
        problems += [f'"{g}" is a gap the profile does not back; it must never be added to the CV'
                     for g in gaps if keywords.mentions(g, new_text) and not keywords.mentions(g, old_text)]
    problems += [f'it copies "{r}" from the posting, which the sources do not back'
                 for r in _copied(new_text, old_text, posting, "\n".join(sources))]
    return problems


def apply_edits(edits: Iterable[Edit], texts: Mapping[str, str], sources: Iterable[str], *, gaps: Iterable[str] = (),
                posting: str = "", languages=("en",), today: date | None = None,
                banned: Mapping[str, tuple[str, ...]] | None = None) -> Outcome:
    """Apply the edits one by one to cv.tex and cover.tex (`texts`), keeping only those that pass every check.

    `sources` are the texts a claim may be backed by (profile, master CV); `gaps` the requirement terms the profile
    does not back; `posting` the untrusted posting, so text lifted from it is caught."""
    current, applied, rejected = dict(texts), [], []
    sources, gaps = list(sources), list(gaps)
    for number, edit in enumerate(edits, 1):
        if edit.file not in FILES or edit.file not in current:
            rejected.append(Rejected(number, f"{edit.file!r} is not one of the documents ({', '.join(FILES)}); "
                                             "an edit names exactly cv.tex or cover.tex", edit))
            continue
        text = current[edit.file]
        count = text.count(edit.old_string)
        if count != 1:
            rejected.append(Rejected(number, f"old_string matches {count} times in {edit.file}; it must match exactly once "
                                             + ("(copy the text exactly as it is now)" if not count else
                                                "(add surrounding words to make it unique)"), edit))
            continue
        if edit.new_string == edit.old_string:
            rejected.append(Rejected(number, "new_string equals old_string, so nothing changes", edit))
            continue
        at = text.index(edit.old_string)
        new_text = text[:at] + edit.new_string + text[at + len(edit.old_string):]
        problems = _judge(edit, text, new_text, sources, gaps, posting, languages, today, banned)
        if problems:
            rejected.append(Rejected(number, "applying it would be wrong: " + "; ".join(problems), edit))
            continue
        current[edit.file] = new_text
        applied.append(edit)
    return Outcome(current, tuple(applied), tuple(rejected))

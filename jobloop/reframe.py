"""Reframing is emphasis, never substance: the "interview backtrack test" as code.

Each CV bullet is matched to the profile bullet it grew from (its anchor, the one sharing most words). Reordering, synonyms and
stressing one aspect of that bullet are fine. A distinctive term (a tool, a number, a capitalised name) the bullet adds is judged
by where the profile has it:

- ok     every distinctive term is in the anchor's own role (a bullet drawn from the Summary or Skills may name any held term);
- flag   a term comes only from a different role or from Education: two things merged into one implied claim, or adjacent
         work described in terms the person used elsewhere. The person decides: keep, soften or drop;
- never  a term is nowhere in the profile (a line saying "not yet" backs nothing): experience the person does not hold.

Pure functions, no I/O. Limits: only distinctive terms are judged, a bullet labelled "Topic: ..." (a competency) is exempt from flag, a bullet is read on its first line, and a bullet that
shares no word with any profile bullet has no anchor, so only its unseen terms can fail it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jobloop.latex import to_text
from jobloop.text import claimed, flat

GENERAL = {"Summary", "Skills", "Contact", "Languages", "Preferences"}
_STOP = frozenset("the and for with from into that this are was were over under per via all its our their your than then also".split())
_WORD = re.compile(r"[^\W_]+(?:[+#][^\W_]*)*", re.U)


@dataclass(frozen=True)
class Level:
    name: str                       # "ok", "flag" or "never"
    terms: tuple[str, ...] = ()     # the terms that decided it
    why: str = ""


def stem(word: str) -> str:
    w = flat(word)
    return (w[:-1] if len(w) > 3 and w.endswith("s") else w)[:6]


def _units(profile: str) -> list[tuple[str, str]]:
    """(origin, text) for every profile line: a role heading or a section name is the origin; a wrapped bullet is one unit."""
    out, section, role = [], "", ""
    for line in claimed(profile).splitlines():
        if line.startswith("## "):
            section, role = line[3:].strip(), ""
        elif line.startswith("###"):
            role = line.lstrip("#").strip()
            out.append((role, role, False))
        elif line.strip():
            if out and line[:1].isspace() and out[-1][2]:
                out[-1] = (out[-1][0], out[-1][1] + " " + line.strip(), True)
            else:
                out.append((role or section, line.strip(), line.lstrip().startswith(("-", "*"))))
    return [(origin, text) for origin, text, _ in out]


def _tokens(text: str) -> list[tuple[str, bool]]:
    """(word, distinctive): a number, a word with a capital inside it or after the first word, or a word of the skills list."""
    found, first = [], True
    for m in _WORD.finditer(text):
        w = m.group()
        found.append((w, bool(re.search(r"\d", w)) or (not first and w[:1].isupper()) or bool(re.search(r"[a-z][A-Z]", w))))
        first = False
    return found


def level(bullet: str, profile: str) -> Level:
    """How far one CV bullet is from the profile: ok, flag or never."""
    units = _units(profile)
    vocabulary = {stem(w) for origin, text in units if origin == "Skills" for w, _ in _tokens(text)}
    words = [(w, d or stem(w) in vocabulary) for w, d in _tokens(re.sub(r"^[-*\u2022\s]+", "", bullet)) if len(w) > 2 and flat(w) not in _STOP]
    mine = {stem(w) for w, _ in words}
    held = [(origin, {stem(w) for w, _ in _tokens(text)}) for origin, text in units]
    unseen = [w for w, d in words if d and not any(stem(w) in tokens for _, tokens in held)]
    if unseen:
        return Level("never", tuple(unseen), "is in no part of the profile")
    anchor = max(held, key=lambda h: len(mine & h[1]), default=None)
    if anchor is None or not mine & anchor[1] or anchor[0] in GENERAL:
        return Level("ok")
    borrowed = [w for w, d in words if d and stem(w) not in anchor[1]
                and not any(stem(w) in tokens for origin, tokens in held if origin == anchor[0])]
    return Level("flag", tuple(borrowed), "comes from a different role or from Education than the rest of the bullet") if borrowed else Level("ok")


def bullets(cv: str) -> list[str]:
    """The bullet lines of a CV, from .tex or plain text."""
    text = to_text(cv) if re.search(r"\\(?:begin|item|documentclass)\b", cv) else cv
    return [m.group(1) for line in text.splitlines() if (m := re.match(r"^\s*[-*\u2022]\s+(.*\S)", line))]


def split(cv: str, profile: str) -> tuple[list[str], list[str]]:
    """(stretch, never): messages for the bullets to show the person (flag) and the ones that fail the gate (never)."""
    stretch, never = [], []
    for b in bullets(cv):
        got = level(b, profile)
        terms = ", ".join(f'"{t}"' for t in got.terms)
        if got.name == "never":
            never.append(f'never: "{b}": {terms} {got.why}: remove it, a skill or tool not held is never claimed')
        elif got.name == "flag" and not re.match(r"[^:]{1,40}:", b):          # a labelled competency draws on every role
            stretch.append(f'flag: "{b}": {terms} {got.why}')
    return stretch, never


def backtrack(cv: str, profile: str) -> list[str]:
    """The interview backtrack test: every bullet that could not be explained without backtracking, with its reason."""
    return [f"could you explain this without backtracking? {m}" for m in sum(split(cv, profile), [])]

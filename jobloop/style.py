"""The writing-style rules as code: what a drafted CV or letter may never contain.

Pure functions over text. Every finding is a message for the drafter that names the line and quotes the offending
words, so a harness can fix exactly that. An empty list means the text passes. Nothing here judges meaning: it checks
what a rule can be checked mechanically (dashes, listed cliches and hedges, buzzwords with no evidence, tone slips,
AI-tooling mentions that do not name Claude Code, doubled words) and leaves the rest to the reviewer.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

EM_DASHES = "\u2014\u2015\u2e3a\u2e3b"

BANNED_PHRASES: dict[str, tuple[str, ...]] = {
    "en": ("passionate about", "great fit", "perfect fit", "leverage my skills", "hit the ground running", "drive results",
           "synergy", "synergies", "think outside the box", "proven track record", "results-driven", "go-getter",
           "fast-paced environment", "dynamic environment", "excited about the opportunity", "i am writing to apply"),
    "da": ("brænder for", "resultatorienteret", "synergi", "den helt rette kandidat", "springe ud i det",
           "dynamisk arbejdsmiljø", "proven track record"),
}
HEDGES: dict[str, tuple[str, ...]] = {
    "en": ("i think i could", "i hope", "i feel that", "i believe i", "i would be lucky", "sorry", "unfortunately",
           "i am not sure", "i might be able", "just a"),
    "da": ("jeg tror", "jeg håber", "desværre", "beklageligvis", "måske kan jeg", "bare en"),
}
BUZZWORDS: dict[str, tuple[str, ...]] = {
    "en": ("team player", "self-starter", "detail-oriented", "hard-working", "hardworking", "motivated", "proactive",
           "dynamic", "highly skilled", "excellent communicator"),
    "da": ("teamplayer", "selvstarter", "detaljeorienteret", "hårdtarbejdende", "motiveret", "proaktiv", "dynamisk"),
}
_AI_TOOLING = re.compile(r"\b(?:agentic|ai[- ]assisted|ai[- ]tool\w*|ai[- ]coding|coding (?:agent|assistant)|llm)s?\b", re.I)
_THIRD_PERSON = re.compile(r"\bthe candidate\b|\b(?:was|were|been)\s+(?:\w+ly\s+)?\w+\s+by\s+(?:me|myself)\b", re.I)
_DOUBLED = re.compile(r"\b([^\W\d_]+)\s+\1\b", re.I)
_ALLOWED_DOUBLES = {"had had", "that that"}


def locate(text: str, start: int, end: int | None = None, pad: int = 30) -> str:
    """'line N: "...quote..."' for the span: how every finding points at the offending words."""
    end = start + 1 if end is None else end
    quote = text[max(0, start - pad):end + pad].replace("\n", " ").strip()
    return f'line {text.count(chr(10), 0, start) + 1}: "{quote}"'


def _phrases(text: str, table: Mapping[str, tuple[str, ...]], languages, why: str) -> list[str]:
    found = []
    for lang in languages:
        for phrase in table.get(lang, ()):
            for m in re.finditer(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.I):
                found.append((m.start(), f'{locate(text, m.start(), m.end())} contains "{phrase}": {why}'))
    return [msg for _, msg in sorted(found)]


def dashes(text: str) -> list[str]:
    """Em-dashes, triple hyphens and spaced double hyphens or en-dashes: use a comma or a period."""
    pattern = rf"[{EM_DASHES}]|---|(?<=\s)(?:--|\u2013)(?=\s)|(?<=[^\W\d_])--(?=[^\W\d_])"
    return [f"{locate(text, m.start(), m.end())} is a dash used as punctuation: use a comma or a period instead"
            for m in re.finditer(pattern, text)]


def banned_phrases(text: str, languages=("en",), banned: Mapping[str, tuple[str, ...]] | None = None) -> list[str]:
    return _phrases(text, BANNED_PHRASES if banned is None else banned, languages, "cut the cliche or say it concretely")


def hedging(text: str, languages=("en",)) -> list[str]:
    return _phrases(text, HEDGES, languages, 'be direct: "I bring X, shown by Y"')


def unbacked_claims(text: str, languages=("en",)) -> list[str]:
    """A buzzword in a sentence that holds no number is a claim with no evidence."""
    found = []
    for m in re.finditer(r"[^.!?\n]+", text):
        if re.search(r"\d", m.group()):
            continue
        for lang in languages:
            for word in BUZZWORDS.get(lang, ()):
                hit = re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", m.group(), re.I)
                if hit:
                    found.append(f'{locate(text, m.start() + hit.start(), m.start() + hit.end())} claims "{word}" '
                                 "with no example or number: show it with a specific fact or cut it")
    return found


def tone(text: str) -> list[str]:
    """First person, active voice: no 'the candidate', no 'was built by me'."""
    return [f"{locate(text, m.start(), m.end())} is not first-person active voice: write 'I built', not the passive"
            for m in _THIRD_PERSON.finditer(text)]


def ai_tooling(text: str) -> list[str]:
    """Any sentence about agentic coding or AI tooling must name Claude Code."""
    found = []
    for m in re.finditer(r"[^.!?\n]+", text):
        hit = _AI_TOOLING.search(m.group())
        if hit and "claude code" not in m.group().casefold():
            found.append(f'{locate(text, m.start() + hit.start(), m.start() + hit.end())} mentions AI tooling '
                         "without naming Claude Code: name the tool explicitly")
    return found


def doubled_words(text: str) -> list[str]:
    """The one spelling slip a program can see without a dictionary: 'the the'."""
    return [f'{locate(text, m.start(), m.end())} repeats "{m.group(1)}"'
            for m in _DOUBLED.finditer(text) if m.group().casefold() not in _ALLOWED_DOUBLES]


def check(text: str, languages=("en",), banned: Mapping[str, tuple[str, ...]] | None = None) -> list[str]:
    """Every style finding for a drafted document, in the person's declared languages."""
    return (dashes(text) + banned_phrases(text, languages, banned) + hedging(text, languages)
            + unbacked_claims(text, languages) + tone(text) + ai_tooling(text) + doubled_words(text))

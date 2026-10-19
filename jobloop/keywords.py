"""Keyword coverage: which of the posting's requirements the CV shows, and which it must never show.

Each requirement is classified against the profile and the CV: covered (the posting's own term, or a trivial inflection),
synonym-only (the CV has the concept under another term), missing-have (the profile backs it and the CV lacks it: a
failure) or missing-gap (the profile does not back it: left missing, and a failure if the CV claims it anyway, because a
gap is never stuffed). A required gap must be acknowledged in the letter when one is given. Lines of the profile that say
"not yet" back nothing. Pure functions; the table is the report the original asked for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jobloop.latex import to_text
from jobloop.text import claimed, flat

STATUSES = ("covered", "synonym-only", "missing-have", "missing-gap")


@dataclass(frozen=True)
class Requirement:
    """A term the posting asks for, how important it is, and the other terms for the same concept.

    `language` is the language of `term`; synonyms are in whatever language the CV may use."""
    term: str
    priority: str = "required"
    synonyms: tuple[str, ...] = ()
    language: str | None = None

    def __post_init__(self):
        if self.priority not in ("required", "preferred"):
            raise ValueError(f"priority of {self.term!r} must be required or preferred, not {self.priority!r}")
        if not self.term.strip():
            raise ValueError("a requirement needs a term")


@dataclass(frozen=True)
class Row:
    keyword: str
    priority: str
    status: str
    note: str


@dataclass(frozen=True)
class Coverage:
    rows: tuple[Row, ...]
    failures: tuple[str, ...]
    findings: tuple[str, ...]

    def table(self) -> str:
        """The coverage table: Keyword, Priority, Status, Note."""
        lines = ["| Keyword | Priority | Status | Note |", "|---|---|---|---|"]
        lines += [f"| {r.keyword} | {r.priority} | {r.status} | {r.note} |" for r in self.rows]
        return "\n".join(lines)


def _plain(text: str) -> str:
    return to_text(text) if re.search(r"\\(?:begin|item|section|textbf|cventry)\b", text) else text


def _norm(text: str) -> str:
    return " ".join(flat(_plain(text)).replace("\u2019", "'").replace("-", " ").split())    # a PDF's "team- level" is "team level"


def mentions(term: str, text: str) -> bool:
    """The term appears whole, ignoring case, hyphens and spacing, allowing a trivial inflection (s, es, ed, d, ing)."""
    return bool(re.search(r"(?<!\w)" + re.escape(_norm(term)) + r"(?:s|es|ed|d|ing|'s)?(?!\w)", _norm(text)))


def _any(terms, text: str) -> str | None:
    return next((t for t in terms if mentions(t, text)), None)


def cover(requirements, profile: str, cv: str, letter: str | None = None, cv_language: str = "en") -> Coverage:
    """Classify every requirement; returns the table, the failures that must be fixed, and the findings to act on."""
    backing = claimed(profile)
    rows, failures, findings = [], [], []
    for r in requirements:
        terms = (r.term,) + tuple(r.synonyms)
        backed = _any(terms, backing)
        in_cv, synonym = mentions(r.term, cv), _any(r.synonyms, cv)
        if not backed:
            status, note = "missing-gap", "the profile does not back it; left missing, never stuffed"
            if in_cv or synonym:
                failures.append(f'"{synonym if not in_cv else r.term}" is on the CV but the profile does not back '
                                f'"{r.term}": remove it (a gap is never stuffed)')
                note = "CV claims a gap"
            elif letter is not None and r.priority == "required" and not _any(terms, _plain(letter)):
                failures.append(f'"{r.term}" is a required gap but the letter does not acknowledge it: bridge it honestly '
                                "to the adjacent real experience")
                note = "gap not acknowledged in the letter"
        elif in_cv:
            status, note = "covered", "the posting's own term is on the CV"
        elif synonym:
            status = "synonym-only"
            note = f'the CV says "{synonym}"'
            if r.language and r.language != cv_language:
                note += f" (posting in {r.language}, CV in {cv_language})"
            true_term = "the profile uses the term" if mentions(r.term, backing) else "the profile backs only the synonym"
            findings.append(f'"{r.term}" appears only as "{synonym}" on the CV ({true_term}): use the posting\'s own '
                            "term where it is true")
        else:
            status, note = "missing-have", f'the profile backs it (as "{backed}") but the CV lacks it'
            failures.append(f'"{r.term}" is backed by the profile but missing from the CV: add it to an experience bullet '
                            "where it is true")
        rows.append(Row(r.term, r.priority, status, note))
    return Coverage(tuple(rows), tuple(failures), tuple(findings))

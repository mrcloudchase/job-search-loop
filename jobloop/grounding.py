"""Grounding: every fact in a drafted document must be in the sources.

A number, year, month-year date, email, phone, link, employer, job title or role period that the document states but the
sources (the profile, plus the master CV or a posting reference line when the caller passes them) do not is returned as
a message for the drafter. Dates are compared normalised, so 03/2022, March 2022, 2022-03 and "marts 2022" are one
date. A role line must name an employer and title from one profile role and carry exactly that role's period: a
shortened, padded or year-only period is ungrounded. Lines of the profile that say "not yet" back nothing. Pure functions;
a .tex document is read through latex.to_text first. Limits: a role is recognised only when its title, employer and
dates share one line; employers and titles in running prose are not checked.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from jobloop.latex import to_text
from jobloop.style import locate
from jobloop.text import claimed, flat, numbers

MONTH_NAMES = {
    "en": "january february march april may june july august september october november december "
          "jan feb mar apr jun jul aug sep sept oct nov dec".split(),
    "da": "januar februar marts april maj juni juli august september oktober november december okt".split(),
    "de": "januar februar märz april mai juni juli august september oktober november dezember mär dez".split(),
    "nl": "januari februari maart april mei juni juli augustus september oktober november december".split(),
}
_FULL = {w: i + 1 for names in MONTH_NAMES.values() for i, w in enumerate(names[:12])}
MONTH_NUMBER = {w: _FULL.get(w) or next(m for full, m in _FULL.items() if full.startswith(w))
                for names in MONTH_NAMES.values() for w in names}
PRESENT = r"present|current|now|nu|nuværende|heute|heden|actueel"
_WORD = "|".join(sorted({w for names in MONTH_NAMES.values() for w in names}, key=len, reverse=True))
_PATTERNS = (
    ("dmy", re.compile(rf"\b(\d{{1,2}})\.?\s+({_WORD})\.?,?\s+(\d{{4}})\b", re.I)),
    ("mdy", re.compile(rf"\b({_WORD})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,\s*(\d{{4}})\b", re.I)),
    ("my", re.compile(rf"\b({_WORD})\.?\s+(\d{{4}})\b", re.I)),
    ("iso", re.compile(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b")),
    ("num", re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")),
    ("nummy", re.compile(r"\b(\d{1,2})[/.](\d{4})\b")),
    ("year", re.compile(r"\b(?:19|20)\d{2}\b")),
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LINK = re.compile(r"(?:https?://|www\.)[^\s<>{}|]+|\b(?:[\w-]+\.)+(?:com|org|net|io|dev|dk|nl|eu|me|app|ai|co|de|uk)\b(?:/[^\s<>{}|]*)?", re.I)
_PLUS_PHONE = re.compile(r"\+\d[\d ()./-]{5,}\d")
_PHONE = re.compile(r"(?<![\w/])\(?\d[\d ()./-]{6,}\d")
_ROLE = re.compile(r"^#{2,4}\s*([^,\n]+),\s*([^(\n]+?)\s*\(([^)\n]+)\),\s*(.+)$", re.M)


@dataclass(frozen=True)
class Found:
    """A date as written: day and month may be absent; `word` is the month name used, in lower case."""
    year: int
    month: int | None
    day: int | None
    word: str
    start: int
    end: int


@dataclass(frozen=True)
class Role:
    title: str
    employer: str
    city: str
    start: tuple[int, int | None]
    end: tuple[int, int | None] | None


def dates(text: str) -> list[Found]:
    """Every date in the text, whatever its notation, in reading order. Each character is read once."""
    found, work = [], text
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(work):
            g = m.groups()
            if kind == "dmy":
                day, word, year = int(g[0]), g[1], int(g[2])
                month = MONTH_NUMBER[word.casefold()]
            elif kind == "mdy":
                word, day, year = g[0], int(g[1]), int(g[2])
                month = MONTH_NUMBER[word.casefold()]
            elif kind == "my":
                word, day, year, month = g[0], None, int(g[1]), MONTH_NUMBER[g[0].casefold()]
            elif kind == "iso":
                word, day, year, month = "", int(g[2]) if g[2] else None, int(g[0]), int(g[1])
            elif kind == "num":
                a, b = int(g[0]), int(g[1])
                day, month = (b, a) if b > 12 else (a, b)
                word, year = "", int(g[2])
            elif kind == "nummy":
                word, day, month, year = "", None, int(g[0]), int(g[1])
            else:
                word, day, month, year = "", None, None, int(m.group())
            if month is not None and not 1 <= month <= 12:
                continue
            found.append(Found(year, month, day, word.casefold(), m.start(), m.end()))
            work = work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]
    return sorted(found, key=lambda f: f.start)


def roles(profile: str) -> tuple[Role, ...]:
    """The experience headings of a profile: `### Title, Employer (City), Month Year - Month Year|present`."""
    out = []
    for m in _ROLE.finditer(profile):
        title, employer, city, period = (g.strip() for g in m.groups())
        found = dates(period)
        if not found or len(found) > 2:
            continue
        ends_now = bool(re.search(rf"\b(?:{PRESENT})\b", period, re.I))
        if len(found) == 1 and not ends_now:
            continue
        end = None if ends_now else (found[1].year, found[1].month)
        out.append(Role(title, employer, city, (found[0].year, found[0].month), end))
    return tuple(out)


def _link_key(link: str) -> str:
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", link.casefold()).rstrip("/.,;:)")


def _number_key(number: str) -> str:
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", number):
        return number.replace(",", "")
    return number.replace(",", ".")


@dataclass(frozen=True)
class Facts:
    """What a text states, with the spans of the claims a document must ground."""
    emails: tuple[tuple[str, int, int], ...]
    links: tuple[tuple[str, int, int], ...]
    phones: tuple[tuple[str, int, int], ...]
    dates: tuple[Found, ...]
    numbers: frozenset[str]
    number_spans: tuple[tuple[str, int, int], ...]


def _blank(text: str, spans) -> str:
    for start, end in spans:
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def facts(text: str) -> Facts:
    """Emails, links, phones, dates and plain numbers of a text; each character is read as only one of them."""
    emails = [(m.group().casefold(), m.start(), m.end()) for m in _EMAIL.finditer(text)]
    work = _blank(text, [(s, e) for _, s, e in emails])
    links = [(_link_key(m.group()), m.start(), m.end()) for m in _LINK.finditer(work)]
    work = _blank(work, [(s, e) for _, s, e in links])
    phones = [(re.sub(r"\D", "", m.group()), m.start(), m.end()) for m in _PLUS_PHONE.finditer(work)]
    work = _blank(work, [(s, e) for _, s, e in phones])
    found = dates(work)
    work = _blank(work, [(f.start, f.end) for f in found])
    later = [(re.sub(r"\D", "", m.group()), m.start(), m.end()) for m in _PHONE.finditer(work)
             if len(re.sub(r"\D", "", m.group())) >= 8]
    work = _blank(work, [(s, e) for _, s, e in later])
    spans = [(_number_key(m.group()), m.start(), m.end()) for m in re.finditer(r"\d+(?:[.,]\d+)*", work)]
    return Facts(tuple(emails), tuple(links), tuple(phones + later), tuple(found),
                 frozenset(_number_key(n) for n in numbers(work)), tuple(spans))


def _document(doc: str) -> str:
    return to_text(doc, keep_links=True) if re.search(r"\\(?:begin|documentclass|item|href)\b", doc) else doc


def _grounded_dates(doc: Facts, src: Facts, today: date | None) -> list[tuple[Found, str]]:
    months = {(f.year, f.month) for f in src.dates if f.month}
    years = {f.year for f in src.dates}
    bad = []
    for f in doc.dates:
        if today and (f.year, f.month) == (today.year, today.month) and f.day in (None, today.day) and f.month:
            continue
        if f.month and (f.year, f.month) not in months:
            bad.append((f, "date"))
        elif not f.month and f.year not in years:
            bad.append((f, "year"))
    return bad


def _parts(line: str, pos: list[Found]) -> list[str]:
    """The pieces of a role line once its dates are taken out: titles, employers, cities, in folded form."""
    rest = _blank(line, [(f.start, f.end) for f in pos])
    pieces = re.split(r"\s\|\s|\s[-\u2013\u2014]+\s|--|,|;|[()]|\bto\b|\btil\b|\bbis\b|\btot\b", rest)
    pieces = [re.sub(r"^[-*\u2022\s]+|[-\s]+$", "", flat(p)) for p in pieces]
    return [p for p in pieces if p and not re.fullmatch(rf"(?:{PRESENT}|[-\u2013\u2014\s])+", p)]


def _role_lines(text: str, known: tuple[Role, ...]) -> list[str]:
    """Short lines that carry a period and look like a role heading: a range, a present, or a known employer."""
    employers = [flat(r.employer) for r in known]
    out, lines = [], text.split("\n")
    for i, line in enumerate(lines):
        pos = dates(line)
        if not pos or len(_blank(line, [(f.start, f.end) for f in pos]).split()) > 14:
            continue
        if len(pos) >= 2 or re.search(rf"\b(?:{PRESENT})\b", line, re.I) or any(e in flat(line) for e in employers):
            nxt = next((l for l in lines[i + 1:] if l.strip()), "")
            if not any(e in flat(line) for e in employers) and not dates(nxt) and len(nxt.split()) <= 8 \
                    and any(e in flat(nxt) for e in employers):
                line = f"{line.rstrip()} | {nxt.strip()}"     # a PDF's title and dates line, then the employer's line below it
            out.append(line)
    return out


def _check_roles(text: str, known: tuple[Role, ...]) -> list[str]:
    found = []
    vocabulary = {flat(v) for r in known for v in (r.title, r.employer, r.city)}
    for line in _role_lines(text, known):
        pos = dates(line)
        where = f'role line "{line.strip()}"'
        for part in _parts(line, pos):
            if part not in vocabulary:
                found.append(f'{where}: "{part}" is not a job title, employer or city in the sources')
        candidates = [r for r in known if flat(r.employer) in flat(line)]
        if not candidates:
            found.append(f"{where}: names no employer from the sources on this line, so its dates cannot be grounded")
            continue
        role = next((r for r in candidates if flat(r.title) in flat(line)), candidates[0])
        if flat(role.title) not in flat(line):
            found.append(f'{where}: the title at {role.employer} in the sources is "{role.title}"')
        expected = [role.start] + ([role.end] if role.end else [])
        written = [(f.year, f.month) for f in pos]
        now = bool(re.search(rf"\b(?:{PRESENT})\b", line, re.I))
        if written != expected or now != (role.end is None):
            period = "present" if role.end is None else f"{role.end[0]}-{role.end[1]:02d}"
            found.append(f"{where}: the period must be exactly the profile's, {role.start[0]}-{role.start[1]:02d} to "
                         f"{period} (no year or month dropped, none invented)")
    return found


def check(doc: str, sources: Iterable[str], today: date | None = None) -> list[str]:
    """What the document states that the sources do not back. `today` lets a letter carry its own date."""
    text = _document(doc)
    joined = "\n".join(claimed(s) for s in sources)
    src, mine = facts(joined), facts(text)
    found: list[tuple[int, str]] = []
    for kind, ours, theirs in (("email", mine.emails, {v for v, *_ in src.emails}),
                               ("phone number", mine.phones, None), ("link", mine.links, None)):
        for value, start, end in ours:
            if kind == "phone number":
                ok = any(p.endswith(value) for p, *_ in src.phones) and len(value) >= 7
            elif kind == "link":
                ok = any(l == value or l.startswith(value.rstrip("/") + "/") for l, *_ in src.links)
            else:
                ok = value in theirs
            if not ok:
                found.append((start, f"{locate(text, start, end)} is {"an" if kind == "email" else "a"} {kind} that is in none of the sources"))
    for f, kind in _grounded_dates(mine, src, today):
        found.append((f.start, f"{locate(text, f.start, f.end)} is {"an" if kind == "email" else "a"} {kind} that is in none of the sources"))
    for value, start, end in mine.number_spans:
        if value not in src.numbers:
            found.append((start, f"{locate(text, start, end)} is a number that is in none of the sources"))
    known = roles(joined)
    if known or _role_lines(text, known):
        found += [(0, msg) for msg in _check_roles(text, known)]
    return [msg for _, msg in sorted(found, key=lambda t: t[0])]

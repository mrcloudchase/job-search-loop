"""The cover letter's checkable rules: language, body budget, structure.

The letter is read as plain text (latex.to_text turns a .tex letter into that). The BODY is what lies between the
salutation line and the sign-off line: the address block, headline, date, salutation, sign-off and signature are not
counted. The body must be 250 to 300 words. The letter is written in the language of the posting (read without its HTML
comments), the CV in the profile's language; a text whose language cannot be decided is a finding, never a pass.
Salutation, closing, opening, bullets, reference, headline and date are checked here. Every function returns messages for
the drafter; an empty list means it passes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from jobloop.grounding import MONTH_NAMES, dates
from jobloop.style import locate
from jobloop.text import flat, visible

LOW, HIGH = 250, 300
MIN_HITS = 5

_RAW_STOPWORDS = {
    "en": "the and of to in is that for with as on are this be at from by or an we you your our it will have has who which "
          "can i my me a was were their they more not but if all about into than also".split(),
    "da": "og i er det at en til på af for med som den de der har vi du jeg ikke om kan vil et var fra ud sig også os min "
          "vores dig din dit mig hos så men eller skal være blevet jer hvor nu efter over under meget alle bliver kunne".split(),
    "de": "und der die das ist nicht ein eine mit für auf von zu den dem des im wir sie ich auch sich werden sind bei oder "
          "wie als über nach aus um".split(),
    "nl": "de het een en van ik te dat die in is niet op aan met voor zijn er maar om ook als bij wij je u naar uit dan "
          "wordt worden heeft hebben zich over".split(),
}
STOPWORDS = {lang: frozenset(w for w in words
                             if not any(w in other for l, other in _RAW_STOPWORDS.items() if l != lang))
             for lang, words in _RAW_STOPWORDS.items()}
GENERIC_SALUTATIONS = {
    "en": ("dear hiring manager",),
    "da": ("kære rekrutteringsansvarlig", "kære ansættelsesudvalg", "til rette vedkommende"),
    "de": ("sehr geehrte damen und herren",),
    "nl": ("geachte heer/mevrouw", "geachte heer of mevrouw"),
}
CLOSINGS = {
    "en": ("sincerely", "best regards", "kind regards", "yours sincerely", "yours faithfully", "regards"),
    "da": ("med venlig hilsen", "venlig hilsen", "bedste hilsner", "mvh"),
    "de": ("mit freundlichen grüßen", "mit freundlichen grüssen"),
    "nl": ("met vriendelijke groet", "met vriendelijke groeten"),
}
_SALUTATION = re.compile(r"^(?:dear|hi|hello|kære|hej|til|sehr geehrte|liebe|geachte|beste)\b", re.I)
_GENERIC_HEADLINE = re.compile(r"^\s*(?:application for|application to|ansøgning (?:til|om)|bewerbung (?:als|um)|sollicitatie)\b", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•])\s+(.*)")


@dataclass(frozen=True)
class Letter:
    header: str
    salutation: str
    body: str
    closing: str


def detect(text: str) -> str | None:
    """The language of the text by stopwords, or None when it cannot be decided (too short or too mixed)."""
    words = re.findall(r"[^\W\d_]+", flat(text))
    scores = sorted(((sum(w in sw for w in words), lang) for lang, sw in STOPWORDS.items()), reverse=True)
    (best, lang), (second, _) = scores[0], scores[1]
    return lang if best >= MIN_HITS and best >= 1.5 * second else None


def split(text: str) -> Letter | None:
    """The letter cut into header, salutation, body and sign-off, or None when either line cannot be found."""
    lines = text.strip().split("\n")
    closings = tuple(c for cs in CLOSINGS.values() for c in cs)
    start = next((i for i, l in enumerate(lines) if _SALUTATION.match(l.strip())), None)
    if start is None:
        return None
    end = next((i for i in range(len(lines) - 1, start, -1)
                if flat(lines[i]).rstrip(",.") in closings), None)
    if end is None:
        return None
    return Letter("\n".join(lines[:start]).strip(), lines[start].strip(), "\n".join(lines[start + 1:end]).strip(),
                  "\n".join(lines[end:]).strip())


def words(body: str) -> int:
    """Words of the body: bullet markers are not words, numbers and hyphenated words each count as one."""
    return len(re.findall(r"\w+(?:[-'’./]\w+)*", re.sub(r"(?m)^\s*[-*•]\s+", "", body)))


def _parts(text: str) -> tuple[Letter | None, list[str]]:
    letter = split(text)
    if letter is None:
        return None, ["the letter has no salutation line followed by a sign-off line, so its body cannot be found: "
                      "write a salutation (Dear ..., Kære ...) and a sign-off (Sincerely, Med venlig hilsen, ...)"]
    return letter, []


def check_budget(text: str, low: int = LOW, high: int = HIGH) -> list[str]:
    letter, found = _parts(text)
    if letter is None:
        return found
    n = words(letter.body)
    if n < low:
        return [f"the letter body has {n} words, {low - n} too few: it must be {low} to {high} words "
                "(salutation, sign-off and address block are not counted)"]
    if n > high:
        return [f"the letter body has {n} words, {n - high} too many: it must be {low} to {high} words "
                "(cut by relevance; do not shrink margins or spacing)"]
    return []


def check_language(letter_text: str, posting: str, cv_text: str | None = None, cv_language: str | None = None) -> list[str]:
    """The letter follows the posting's language; the CV follows the profile setting (English when unset), never the posting."""
    found, wanted = [], detect(visible(posting))
    mine = detect(letter_text)
    if wanted is None:
        found.append("the posting's language cannot be decided from its text, so the letter's language cannot be checked: "
                     "state it explicitly")
    if mine is None:
        found.append("the letter's language cannot be decided from its text (too short or mixed): write it fully in one language")
    elif wanted and mine != wanted:
        found.append(f"the letter is in {mine} but the posting is in {wanted}: write the letter in the posting's language")
    if cv_text is not None:
        setting, cv = cv_language or "en", detect(cv_text)
        if cv is None:
            found.append("the CV's language cannot be decided from its text")
        elif cv != setting:
            found.append(f"the CV is in {cv} but the profile's CV language is {setting}: the CV language is never switched per posting")
    return found


def check_salutation(text: str, addressee: str | None, language: str) -> list[str]:
    """Named person when the posting names one, otherwise the generic salutation of the letter's language."""
    letter, found = _parts(text)
    if letter is None:
        return found
    head = flat(letter.salutation)
    if addressee:
        return [] if flat(addressee) in head else [f'the salutation "{letter.salutation}" must name {addressee}, '
                                                    "the contact the posting gives"]
    options = GENERIC_SALUTATIONS.get(language, ())
    if not any(head.startswith(o) for o in options):
        return [f'the salutation "{letter.salutation}" names no one the posting names: use "{options[0].title()}" '
                "(never invent a name)" if options else f'no generic salutation is known for "{language}"']
    return []


def check_closing(text: str, language: str) -> list[str]:
    letter, found = _parts(text)
    if letter is None:
        return found
    sign_off = flat(letter.closing.split("\n")[0]).rstrip(",.")
    options = CLOSINGS.get(language, ())
    return [] if sign_off in options else [f'the sign-off "{sign_off}" does not suit a letter in {language}: use one of '
                                           + ", ".join(f'"{o}"' for o in options)]


def check_bullets(text: str, low: int = 3, high: int = 5) -> list[str]:
    """Three to five bullets in the body, each opening differently."""
    letter, found = _parts(text)
    if letter is None:
        return found
    items = [m.group(1) for l in letter.body.split("\n") if (m := _BULLET.match(l))]
    if not low <= len(items) <= high:
        found.append(f"the letter body has {len(items)} bullets: it needs {low} to {high}")
    openings = [flat(i).split(" ")[0] for i in items if i.strip()]
    repeated = sorted({o for o in openings if openings.count(o) > 1})
    return found + [f'bullets open with the same word "{o}": vary the openings' for o in repeated]


def check_opening(text: str, company: str, role: str) -> list[str]:
    """The first paragraph of the body names this company and this role: an opening that fits any employer fails."""
    letter, found = _parts(text)
    if letter is None:
        return found
    opening = flat(re.split(r"\n\s*\n", letter.body)[0])
    return [f'the opening paragraph does not name "{n}": tailor it to this role and company'
            for n in (company, role) if flat(n) not in opening]


def check_reference(text: str, reference: str | None) -> list[str]:
    """The posting's reference or job ID, when it has one, is quoted in the letter."""
    return [] if not reference or flat(reference) in flat(text) else [f'the letter does not quote the posting\'s reference "{reference}"']


def check_headline(text: str) -> list[str]:
    """The headline names the specialty and a keyword of the posting, not 'Application for ...'."""
    letter = split(text)
    if letter is None:
        return []
    return [f'the headline "{l.strip()}" is generic: name your specialty plus a keyword from the posting'
            for l in letter.header.split("\n") if _GENERIC_HEADLINE.match(l)]


def check_date(text: str, today: date, language: str) -> list[str]:
    """The letter is dated today, with the month written in the letter's own language."""
    letter, found = _parts(text)
    if letter is None:
        return found
    written = dates(letter.header)
    if not written:
        return ["the letter carries no date in its header: date it today"]
    mine = written[-1]
    found = []
    if (mine.year, mine.month, mine.day) != (today.year, today.month, today.day):
        found.append(f"{locate(letter.header, mine.start, mine.end)} is not today ({today.isoformat()})")
    if mine.word and mine.word not in MONTH_NAMES.get(language, ()):
        found.append(f'the date uses the month "{mine.word}", which is not {language}: write the date in the letter\'s language')
    return found


def check(text: str, *, posting: str, company: str, role: str, today: date, addressee: str | None = None,
          reference: str | None = None, cv_text: str | None = None, cv_language: str | None = None) -> list[str]:
    """Every letter finding. The letter's language is the posting's; when it cannot be decided, that is the finding."""
    found = check_language(text, posting, cv_text, cv_language) + check_budget(text)
    language = detect(visible(posting))
    if language is not None:
        found += check_salutation(text, addressee, language) + check_closing(text, language) + check_date(text, today, language)
    return found + check_bullets(text) + check_opening(text, company, role) + check_reference(text, reference) + check_headline(text)

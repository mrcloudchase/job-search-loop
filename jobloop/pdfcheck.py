"""What an ATS would read from a CV's text layer, checked by code over the text `pdfinfo` and `pdftotext` printed.

Pure functions: no subprocess, no file I/O. The caller runs the tools (with `PDFINFO_ARGV` and `TEXT_ARGV`) and hands this
module their output, or None when a tool is missing or failed. Every check returns `Finding`s whose messages can be sent
verbatim back to a drafter. The promises:

- Nothing passes silently. With no text (or no page count) the verdict is `UNVERIFIED`, never `VERIFIED`, and carries the
  reason; a gate must treat anything but `VERIFIED` as not ready unless the person waived it.
- The date check reads the RAW layer. Folding maps the en dash back to `-` and would hide the very defect it looks for.
- Comparison folding (`normalize_for_compare`) is for matching only; it never changes what is dumped or reported.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

VERIFIED, FAILED, UNVERIFIED = "verified", "failed", "unverified"

# `-enc UTF-8` is mandatory: xpdf builds default to Latin-1 and would turn a correct non-ASCII CV into U+FFFD.
TEXT_ARGV = ("pdftotext", "-layout", "-enc", "UTF-8")
PDFINFO_ARGV = ("pdfinfo",)

DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
HSPACE = r"[^\S\r\n]"
_YEAR = r"(?<!\d)(?:19|20)\d\d(?!\d)"
_OUTER_PUNCTUATION = ".,;:!?()[]{}\"'"
_MONTHS = frozenset(
    "jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july august september "
    "october november december maj okt".split()
)
_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u00ad": None, "\u200b": None, "\u2060": None, "\ufeff": None,
})


class PdfInfoError(ValueError):
    """`pdfinfo` output that does not state a page count: an error, never a guessed count."""


@dataclass(frozen=True)
class Finding:
    """One thing wrong, named by the check that found it, with a message fit to send back to a drafter."""

    check: str
    message: str


@dataclass(frozen=True)
class Verdict:
    """The outcome of a group of checks. `extractor` names the tool that produced the text, for the report."""

    status: str
    findings: tuple[Finding, ...] = ()
    extractor: str = ""

    def __post_init__(self) -> None:
        if self.status not in (VERIFIED, FAILED, UNVERIFIED):
            raise ValueError(f"unknown verdict status {self.status!r}")
        if self.status == FAILED and not self.findings:
            raise ValueError("a failed verdict must carry at least one finding")
        if self.status == VERIFIED and self.findings:
            raise ValueError("a verified verdict cannot carry findings")

    @property
    def verified(self) -> bool:
        return self.status == VERIFIED

    def feedback(self) -> str:
        """The findings as one block of text, each line naming its check and the extractor, to send back verbatim."""
        tag = f" ({self.extractor})" if self.extractor else ""
        return "\n".join(f"[{f.check}{tag}] {f.message}" for f in self.findings)


def verdict_of(findings: list[Finding], extractor: str = "") -> Verdict:
    return Verdict(FAILED if findings else VERIFIED, tuple(findings), extractor)


def unverified(check: str, reason: str, extractor: str = "") -> Verdict:
    """The verdict for a check that could not run. It is never a pass."""
    return Verdict(UNVERIFIED, (Finding(check, f"unverified: {reason}"),), extractor)


def parse_page_count(pdfinfo: str) -> int:
    """The page count from `pdfinfo` output. A missing or zero `Pages:` line is an error, not a guess."""
    found = re.search(r"^Pages:\s+(\d+)\s*$", pdfinfo, re.M)
    if not found or int(found.group(1)) < 1:
        raise PdfInfoError("pdfinfo printed no 'Pages: N' line, so the page count is unknown; the PDF may be damaged")
    return int(found.group(1))


def check_page_count(pdfinfo: str, expected: int) -> list[Finding]:
    """The page count must EQUAL `expected`: it is a fixed length, not a maximum."""
    try:
        found = parse_page_count(pdfinfo)
    except PdfInfoError as error:
        return [Finding("page-count", str(error))]
    if found != expected:
        return [Finding("page-count", f"The PDF has {found} page(s); it must have exactly {expected}.")]
    return []


def normalize_for_compare(text: str) -> str:
    """Text as it is matched: ligatures and accents composed (NFKC), soft hyphens dropped, typographic dashes and
    quotes made ASCII, runs of whitespace one space. Case is kept. Used on both sides of a comparison, never on a dump."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(_FOLD).split())


def check_min_chars(text: str, minimum: int) -> list[Finding]:
    """The layer must hold at least `minimum` normalised characters; an all-whitespace layer has none."""
    found = len(normalize_for_compare(text))
    if found < max(minimum, 1):
        return [Finding(
            "text-layer",
            f"The text layer holds {found} character(s); at least {max(minimum, 1)} are needed. The PDF is empty or "
            "image-only, and an ATS would read nothing.",
        )]
    return []


def find_garbled_text(text: str) -> list[Finding]:
    """Glyph-id markers `(cid:N)` and U+FFFD replacement characters mean the fonts do not map to text."""
    findings = []
    cids = re.findall(r"\(cid:\d+\)", text)
    if cids:
        findings.append(Finding(
            "garbled-text",
            f"The text layer contains {len(cids)} glyph-id marker(s) such as {cids[0]}. The font has no text mapping: "
            "use a font with a ToUnicode map (for pdflatex load fontenc T1 and a Type 1 font) and recompile.",
        ))
    replacements = text.count("\ufffd")
    if replacements:
        findings.append(Finding(
            "garbled-text",
            f"The text layer contains {replacements} U+FFFD replacement character(s). Some character is not "
            "encoded as text in the PDF; replace it with plain text or a supported glyph and recompile.",
        ))
    return findings


def check_required_text(text: str, required: tuple[str, ...]) -> list[Finding]:
    """Every string must appear in the layer (compared folded, case-sensitive, no fuzzy matching)."""
    layer = normalize_for_compare(text)
    return [
        Finding("required-text", f"{item!r} is in the source but not in the PDF's text layer; an ATS would not see it.")
        for item in required if normalize_for_compare(item) not in layer
    ]


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def check_contact_literal(text: str, email: str, phone: str) -> list[Finding]:
    """The profile's email and phone must be literal, contiguous text. A detail carried only by an icon or a link is
    invisible to an ATS. The phone matches on its digits inside one run of digits and separators on a single line."""
    findings = []
    if email and email.casefold() not in normalize_for_compare(text).casefold():
        findings.append(Finding(
            "contact",
            f"The email {email!r} is not literal text in the PDF's text layer. Print the address itself, not an icon or "
            "a link label, and keep it on one line.",
        ))
    if phone:
        wanted = _digits(phone)
        runs = re.findall(rf"\+?\d(?:[\d()./\-]|{HSPACE})*\d", normalize_for_compare_lines(text))
        if not wanted or not any(wanted in _digits(run) for run in runs):
            findings.append(Finding(
                "contact",
                f"The phone number {phone!r} is not literal text in the PDF's text layer. Print the number itself, "
                "digits together on one line, not an icon or a link.",
            ))
    return findings


def normalize_for_compare_lines(text: str) -> str:
    """Like `normalize_for_compare` but keeps line breaks, so a match cannot run across two lines."""
    return "\n".join(" ".join(line.split()) for line in unicodedata.normalize("NFKC", text).translate(_FOLD).splitlines())


def _line_index(lines: list[str], wanted: str) -> int | None:
    target = normalize_for_compare(wanted).casefold()
    for index, line in enumerate(lines):
        if normalize_for_compare(line).casefold().rstrip(":") == target:
            return index
    return None


def check_reading_order(text: str, name: str, headings: tuple[str, ...]) -> list[Finding]:
    """The extraction must open with the name and contact lines, then show the section headings in the expected order,
    each as its own line. A multi-column or sidebar layout is where this breaks: headings merge into other text."""
    lines = text.splitlines()
    findings = []
    positions: list[tuple[str, int]] = []
    for heading in headings:
        index = _line_index(lines, heading)
        if index is None:
            findings.append(Finding(
                "reading-order",
                f"The heading {heading!r} is not on a line of its own in the extracted text. A multi-column or sidebar "
                "layout merges headings into other text and scrambles the order an ATS reads: use one column.",
            ))
        else:
            positions.append((heading, index))
    for (earlier, a), (later, b) in zip(positions, positions[1:]):
        if b < a:
            findings.append(Finding(
                "reading-order",
                f"The heading {later!r} is extracted before {earlier!r}, but the CV must read {earlier!r} first. The "
                "text layer is out of order (an ATS reads it in that order): use one column and keep sections in sequence.",
            ))
    if positions and name:
        first = min(index for _, index in positions)
        before = "\n".join(lines[:first])
        if normalize_for_compare(name).casefold() not in normalize_for_compare(before).casefold():
            findings.append(Finding(
                "reading-order",
                f"The name {name!r} does not come before the first heading ({positions[0][0]!r}) in the extracted text. "
                "The CV must open with the name and contact lines.",
            ))
    return findings


@dataclass(frozen=True)
class DateHit:
    """A date range joined by something other than an ASCII hyphen: the whole line and the offending character."""

    line: str
    dash: str


def _is_year(token: str) -> bool:
    return re.fullmatch(r"(?:19|20)\d\d", token.strip(_OUTER_PUNCTUATION)) is not None


def _bare_number(token: str) -> bool:
    """A quantity (`1500`, `600k`, `1,234.56`, `2.0M`, `20%`) as opposed to a date. A dotted or comma-grouped token that
    holds a year group (`01.2016`, `31.07.2024`) is a date, and composites such as `03/2016` are never bare."""
    stripped = token.strip(_OUTER_PUNCTUATION)
    if not re.fullmatch(r"\d+(?:[.,]\d+)*[kKmM%]?", stripped):
        return False
    groups = re.split(r"[.,]", re.sub(r"[kKmM%]$", "", stripped))
    return not (len(groups) > 1 and any(_is_year(group) for group in groups))


def _carries_year(token: str, following: str) -> bool:
    if re.search(_YEAR, token):
        return True
    return token.strip(_OUTER_PUNCTUATION).casefold() in _MONTHS and re.search(_YEAR, following) is not None


def find_non_ascii_date_ranges(raw_text: str) -> list[DateHit]:
    """Date ranges joined by an en/em dash, hyphen variant or minus sign, found in the RAW layer, in document order.
    A year (or a month then a year) on either side is enough, so `Mar 2016 – Jul 2016` and `2016 – Present` are caught.
    A range of bare numbers that are not both years (`EUR 1500–2000`) is a quantity and is left alone. A dash never
    pairs with a year on another line."""
    hits = []
    for line in raw_text.splitlines():
        for index, char in enumerate(line):
            if char not in DASHES:
                continue
            left = re.search(r"\S*$", re.sub(rf"{HSPACE}+$", "", line[:index])).group()
            after = re.sub(rf"^{HSPACE}+", "", line[index + 1:]).split()
            right, following = (after[0] if after else ""), (after[1] if len(after) > 1 else "")
            if _bare_number(left) and _bare_number(right) and not (_is_year(left) and _is_year(right)):
                continue
            if _carries_year(left, "") or _carries_year(right, following):
                hits.append(DateHit(" ".join(line.split()), char))
    return hits


def check_ascii_dates(raw_text: str) -> list[Finding]:
    """Every date range must use a single ASCII hyphen, or a Workday-style import drops the date."""
    hits = find_non_ascii_date_ranges(raw_text)
    if not hits:
        return []
    listed = "; ".join(f"U+{ord(hit.dash):04X} in {hit.line[:80]!r}" for hit in hits)
    return [Finding(
        "ascii-dates",
        f"{len(hits)} date range(s) use a character other than the ASCII hyphen: {listed}. ATS imports split ranges "
        "on '-' and drop the date. Write each range with one ASCII hyphen, like 2016-2024 or Mar 2016 - Jul 2016 "
        "(in LaTeX a single '-', not '--').",
    )]


def check_date_pairs(raw_text: str, lone_years_ok: tuple[str, ...] = ()) -> list[Finding]:
    """A line that ends on one lone year (or a year and a dash) is a start with no end: an ATS imports it without an
    end date. Each role and degree needs a start and an end (or Present), EXCEPT a year the person's own profile gives
    alone (a graduation year): requiring a start year there would make the drafter invent one."""
    findings = []
    for line in raw_text.splitlines():
        text = " ".join(line.split())
        if any(text.endswith(year) and re.search(rf"[^\d]{year}$", text) for year in lone_years_ok):
            continue
        if len(re.findall(_YEAR, text)) == 1 and re.search(rf"(?:^|[\s(/])(?:19|20)\d\d\)?(?:\s*[-{DASHES}])?$", text):
            findings.append(Finding(
                "date-pairs",
                f"The line {text[:80]!r} ends on a single year, which imports as a start with no end. Give the entry "
                "both a start and an end year (like 2019-2024), or 'Present' for a current one.",
            ))
    return findings


@dataclass(frozen=True)
class TextLayerSpec:
    """What the CV's text layer must show. `headings` are the section headings in expected order, `required` further
    strings from the rendered source (employers, titles). The page count, name and contact come from the person."""

    pages: int
    name: str
    email: str
    phone: str
    headings: tuple[str, ...]
    required: tuple[str, ...] = ()
    min_chars: int = 400
    lone_years_ok: tuple[str, ...] = ()       # years the profile gives alone on an entry, such as a graduation year

    def __post_init__(self) -> None:
        if self.pages < 1 or not self.name.strip() or not self.email.strip() or not self.headings:
            raise ValueError("a TextLayerSpec needs pages >= 1, a name, an email and the expected section headings")


def verify_cv_text_layer(pdfinfo: str | None, text: str | None, spec: TextLayerSpec, extractor: str = "pdftotext") -> Verdict:
    """All ATS text-layer checks for a CV, always including the date checks. With no `pdfinfo` or no `text` (a tool
    missing or failing) the verdict is UNVERIFIED, never a pass. The cover letter does not go through here: its
    page count is `check_page_count` alone."""
    if text is None or pdfinfo is None:
        missing = "the text layer" if text is None else "the page count"
        return unverified(
            "text-layer",
            f"{missing} could not be read (is poppler-utils, or the check container, available?). The CV's ATS "
            "parseability is not verified; record this in the report and do not mark the CV ready without a waiver.",
            extractor,
        )
    findings = check_page_count(pdfinfo, spec.pages)
    findings += check_min_chars(text, spec.min_chars)
    findings += find_garbled_text(text)
    findings += check_contact_literal(text, spec.email, spec.phone)
    findings += check_reading_order(text, spec.name, spec.headings)
    findings += check_required_text(text, spec.required)
    findings += check_ascii_dates(text)
    findings += check_date_pairs(text, spec.lone_years_ok)
    return verdict_of(findings, extractor)

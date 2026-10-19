"""Page layout problems found in the geometry `pdftotext -bbox-layout` prints: holes, stranded headings, orphaned entry
headers, text in the footer band, pages that end early.

Pure functions over the XHTML text; no subprocess, no file I/O. The caller runs the tool (`BBOX_ARGV`) and passes its
output, or calls `skipped` when the tool is missing or crashed. The promises:

- A broken or missing extractor is never a layout verdict: it gives an UNVERIFIED verdict that blocks "ready" unless waived.
- Page count is not measured here; `pdfcheck.check_page_count` owns it, in one place.
- Every threshold is a field of `LayoutProfile`. The defaults were calibrated on A4 with a two-page CV; the fields marked
  "template-dependent" must be re-derived for each template and paper size (the templates builder owns that).
"""
from __future__ import annotations

import re
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from jobloop.pdfcheck import Finding, Verdict, unverified, verdict_of

BBOX_ARGV = ("pdftotext", "-bbox-layout", "-enc", "UTF-8")


@dataclass(frozen=True)
class LayoutProfile:
    """Thresholds for the layout checks, in points unless a fraction. Frozen: a template passes its own profile."""

    gap_limit_pt: float = 100.0  # biggest top-to-top distance between body lines (about seven blank lines)
    blank_line_pt: float = 14.0  # only used to say "about N blank lines" in a message
    footer_band_pt: float = 90.0  # template-dependent: height of the bottom band that holds the page number
    footer_lines_allowed: int = 1  # template-dependent: distinct line tops allowed in the band (1 = a page number; 0 = none)
    bottom_limit_fraction: float = 0.25  # a non-final page may leave this share of its height empty
    last_page_thin_fraction: float = 0.35  # the final page of a multi-page document may leave this share empty
    heading_height_ratio: float = 1.25  # template-dependent: a line this much taller than the median line is a heading
    indent_pt: float = 8.0  # template-dependent: a line this far past the left margin is indented (bullets, wraps)
    set_in_limit_pt: float = 60.0  # template-dependent: text set further in than this is not where the body resumes
    same_line_pt: float = 3.0  # tops this close are one printed line (a marker or bold word sits a fraction off)
    list_markers: str = "•‣▪●·"  # bullet glyphs only: '-', '*', dashes and U+25CB are text

    def __post_init__(self) -> None:
        positive = (self.gap_limit_pt, self.blank_line_pt, self.footer_band_pt, self.heading_height_ratio,
                    self.indent_pt, self.set_in_limit_pt, self.same_line_pt)
        if min(positive) <= 0 or self.footer_lines_allowed < 0 or not self.list_markers:
            raise ValueError("LayoutProfile needs positive thresholds, footer_lines_allowed >= 0 and list markers")
        if not (0 < self.bottom_limit_fraction <= 1 and 0 < self.last_page_thin_fraction <= 1):
            raise ValueError("LayoutProfile fractions must be in (0, 1]")


@dataclass(frozen=True)
class Word:
    text: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass(frozen=True)
class Line:
    """One line as pdftotext grouped it: words left to right. Geometry is derived from the words."""

    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def left(self) -> float:
        return min(word.x_min for word in self.words)

    @property
    def top(self) -> float:
        return min(word.y_min for word in self.words)

    @property
    def bottom(self) -> float:
        return max(word.y_max for word in self.words)

    @property
    def height(self) -> float:
        return max(word.y_max - word.y_min for word in self.words)

    def has_word_character(self) -> bool:
        return re.search(r"\w", self.text) is not None


@dataclass(frozen=True)
class Page:
    width: float
    height: float
    lines: tuple[Line, ...]  # top to bottom, then left to right


class LayoutUnavailable(ValueError):
    """The bbox output cannot be read: a broken extractor, not a broken document."""


def parse_bbox_layout(xhtml: str) -> tuple[Page, ...]:
    """Pages, lines and words from `pdftotext -bbox-layout` output."""
    try:
        root = ET.fromstring(xhtml)
        pages = []
        for page in (e for e in root.iter() if e.tag.rpartition("}")[2] == "page"):
            lines = []
            for line in (e for e in page.iter() if e.tag.rpartition("}")[2] == "line"):
                words = tuple(
                    Word(w.text or "", *(float(w.attrib[k]) for k in ("xMin", "yMin", "xMax", "yMax")))
                    for w in line if w.tag.rpartition("}")[2] == "word"
                )
                if words:
                    lines.append(Line(words))
            pages.append(Page(float(page.attrib["width"]), float(page.attrib["height"]),
                              tuple(sorted(lines, key=lambda l: (l.top, l.left)))))
    except (ET.ParseError, KeyError, ValueError) as error:
        raise LayoutUnavailable(f"the -bbox-layout output could not be parsed ({error})") from error
    if not pages:
        raise LayoutUnavailable("the -bbox-layout output holds no pages")
    return tuple(pages)


def skipped(returncode: int | None, stderr: str = "") -> Verdict:
    """The verdict when `pdftotext -bbox-layout` is missing (`returncode` None) or exited non-zero: UNVERIFIED, with both
    known causes named, because a broken extractor says nothing about the document."""
    first = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
    if returncode is None:
        return unverified("layout", "pdftotext was not found, so the layout was not measured. Install poppler-utils or "
                          "use the check container; record the skip in the report and do not mark the PDF ready "
                          "without a waiver.")
    detail = first or f"exit code {returncode}"
    return unverified("layout", f"pdftotext could not produce -bbox-layout output ({detail}). Two known causes: an xpdf "
                      "build shadowing poppler (it has no -bbox-layout), and poppler older than 26.05 aborting on a "
                      "PDF Info dictionary with an empty string, as hyperref writes. The layout was not measured; "
                      "record the skip and do not mark the PDF ready without a waiver.")


def text_x(line: Line, profile: LayoutProfile) -> float:
    """Where a line's text starts: after any leading list-marker glyphs, else its left edge."""
    for word in line.words:
        if word.text not in profile.list_markers:
            return word.x_min
    return line.left


def body_lines(page: Page, profile: LayoutProfile) -> tuple[Line, ...]:
    """Lines above the footer band; the band holds the page number and is not body."""
    return tuple(line for line in page.lines if line.top < page.height - profile.footer_band_pt)


def footer_tops(page: Page, profile: LayoutProfile) -> list[float]:
    return sorted({round(line.top, 1) for line in page.lines if line.top >= page.height - profile.footer_band_pt})


def largest_gap(page: Page, profile: LayoutProfile) -> tuple[float, float]:
    """The biggest top-to-top distance between consecutive distinct body-line tops, and the y it starts at.
    Measured top to top, so a tall line inflates it by its own height: deliberately over-detecting."""
    tops = sorted({round(line.top, 1) for line in body_lines(page, profile)})
    gaps = [(b - a, a) for a, b in zip(tops, tops[1:])]
    return max(gaps) if gaps else (0.0, 0.0)


def bottom_space(page: Page, profile: LayoutProfile) -> float:
    """Page height minus the bottom of the lowest body line (the footer is ignored); the whole height if no body."""
    body = body_lines(page, profile)
    return page.height - max((line.bottom for line in body), default=0.0)


def is_heading(page: Page, line: Line, profile: LayoutProfile) -> bool:
    """A line taller than the page's median body line by the heading ratio (line height stands in for font size)."""
    heights = [l.height for l in body_lines(page, profile)]
    median = statistics.median(heights) if heights else 0.0
    return median > 0 and line.height > median * profile.heading_height_ratio


@dataclass(frozen=True)
class PageMeasure:
    number: int
    text_top: float
    text_bottom: float
    bottom_space_pt: float
    bottom_space_percent: float
    largest_gap_pt: float
    largest_gap_y: float


def measure(pages: tuple[Page, ...], profile: LayoutProfile) -> tuple[PageMeasure, ...]:
    """Per-page extents: where body text starts and stops, bottom whitespace, and the largest vertical gap."""
    result = []
    for number, page in enumerate(pages, 1):
        body = body_lines(page, profile)
        gap, gap_y = largest_gap(page, profile)
        space = bottom_space(page, profile)
        result.append(PageMeasure(
            number, min((l.top for l in body), default=0.0), max((l.bottom for l in body), default=0.0),
            space, 100 * space / page.height, gap, gap_y,
        ))
    return tuple(result)


def format_measurements(measures: tuple[PageMeasure, ...]) -> str:
    return "\n".join(
        f"page {m.number}: text y {m.text_top:.0f}-{m.text_bottom:.0f}pt, bottom space {m.bottom_space_pt:.0f}pt "
        f"({m.bottom_space_percent:.0f}%), largest gap {m.largest_gap_pt:.0f}pt at y={m.largest_gap_y:.0f}"
        for m in measures
    )


def find_orphans(pages: tuple[Page, ...], profile: LayoutProfile) -> list[Finding]:
    """At each page break: a section heading as the last line, an entry header whose bullets start the next page, or a
    lone bullet marker split from its text. A wrapped bullet is read by where its TEXT starts, not its left edge."""
    bodies = [body_lines(page, profile) for page in pages]
    everything = [line for body in bodies for line in body]
    if not everything:
        return []
    margin = min(line.left for line in everything)
    findings = []
    for number, (page, body, next_body) in enumerate(zip(pages, bodies, bodies[1:]), 1):
        if not body or not next_body:
            continue
        last_top = max(line.top for line in body)
        printed = [line for line in body if line.top >= last_top - profile.same_line_pt]
        wordy = [line for line in printed if line.has_word_character()]
        heading = next((line for line in wordy if is_heading(page, line, profile)), None)
        if heading:
            findings.append(Finding(
                "layout",
                f"Page {number} ends on the section heading {heading.text[:60]!r}; its content starts on page "
                f"{number + 1}. Keep the heading with its content: shorten what comes before it so the section fits "
                "on the page, or move a shorter section above it. Never stretch the page.",
            ))
            continue
        opener = next((line for line in next_body
                       if line.has_word_character() and text_x(line, profile) - margin <= profile.set_in_limit_pt), None)
        if opener is None:
            continue
        if not wordy:
            if min(line.left for line in printed) - margin <= profile.indent_pt and opener.left - margin > profile.indent_pt:
                findings.append(Finding(
                    "layout",
                    f"A bullet is split across the break between page {number} and {number + 1}: its marker ends the page "
                    f"and its text ({opener.text[:60]!r}) starts the next. Keep them together by shortening content above.",
                ))
        elif text_x(opener, profile) - min(text_x(line, profile) for line in wordy) > profile.indent_pt:
            shown = max(wordy, key=lambda line: line.top).text[:60]
            findings.append(Finding(
                "layout",
                f"The entry header {shown!r} is the last thing on page {number} but its bullets ({opener.text[:60]!r}) "
                f"start page {number + 1}. Keep the entry together: add \\needspace before this entry, or shorten "
                "content above it so the header moves with its bullets. Never stretch the page.",
            ))
    return findings


def check_layout(pages: tuple[Page, ...], profile: LayoutProfile = LayoutProfile()) -> Verdict:
    """All layout checks over parsed pages. Page count is deliberately not one of them."""
    findings = []
    last = len(pages)
    for number, page in enumerate(pages, 1):
        if not body_lines(page, profile):
            findings.append(Finding("layout", f"Page {number} contains no text outside the footer band."))
            continue
        gap, gap_y = largest_gap(page, profile)
        if gap > profile.gap_limit_pt:
            findings.append(Finding(
                "layout",
                f"Page {number} has a vertical hole of {gap:.0f}pt (about {gap / profile.blank_line_pt:.0f} blank "
                f"lines) starting at y={gap_y:.0f}pt. An unbreakable block that did not fit jumped to the next page: "
                "shorten the entry that follows the hole so it fits, never stretch the page.",
            ))
        if len(footer_tops(page, profile)) > profile.footer_lines_allowed:
            findings.append(Finding(
                "layout",
                f"Page {number} has text in the bottom {profile.footer_band_pt:.0f}pt footer band beyond the page "
                "number: body text has spilled into it. Cut content; never use \\enlargethispage or negative vspace.",
            ))
        empty = bottom_space(page, profile) / page.height
        if number < last and empty > profile.bottom_limit_fraction:
            findings.append(Finding(
                "layout",
                f"Page {number} ends {100 * empty:.0f}% early (limit {100 * profile.bottom_limit_fraction:.0f}%), which "
                "reads as a broken page break. Check for a block that jumped to the next page and shorten around it.",
            ))
        if number == last and last > 1 and empty > profile.last_page_thin_fraction:
            findings.append(Finding(
                "layout",
                f"The last page is {100 * empty:.0f}% empty (limit {100 * profile.last_page_thin_fraction:.0f}%), so "
                "the document looks unfinished. Restore the highest-relevance content that was cut.",
            ))
    findings += find_orphans(pages, profile)
    return verdict_of(findings, "pdftotext -bbox-layout")


def verify_layout(xhtml: str | None, profile: LayoutProfile = LayoutProfile()) -> Verdict:
    """`check_layout` over raw bbox output; None (tool missing or failed: see `skipped`) or unreadable output is
    UNVERIFIED, never a pass."""
    if xhtml is None:
        return skipped(None)
    try:
        return check_layout(parse_bbox_layout(xhtml), profile)
    except LayoutUnavailable as error:
        return unverified("layout", f"{error}; the layout was not measured.", "pdftotext -bbox-layout")


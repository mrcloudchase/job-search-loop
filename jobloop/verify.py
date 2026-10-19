"""Every check on a drafted CV and cover letter, in one place, run by code outside any harness. It compiles both in a
throwaway container, then judges the PDFs and their sources: LaTeX safety and escaping, exact page counts, the CV's ATS text
layer (literal email and phone, no garbled glyphs, reading order, ASCII date ranges), page layout, the writing-style rules,
the letter's body budget, salutation, language and reference, grounding of every number, date, employer and title in the
profile, and the keyword-coverage table (a gap is never stuffed, a requirement the profile backs is never missing).

Each failure is a message written to be sent back to the drafter word for word. A check that could not run is `unverified`,
which fails unless the person has waived it: a tool that is missing never counts as a pass."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from . import consistency, grounding, keywords, latex, latexrun, layout, letter, pdfcheck, profile as profiles, reframe, style


@dataclass(frozen=True)
class Context:
    """What the checks need to know about this application. Built once, from the profile, the posting and the fit."""

    profile: str
    posting: str                                    # as fetched (comments included, so a hidden claim can be recognised)
    requirements: tuple[keywords.Requirement, ...]
    company: str
    role: str
    today: date
    reference: str | None = None
    addressee: str | None = None
    cv_language: str = "en"
    letter_language: str = "en"                    # the posting's language
    company_claims: tuple[str, ...] = ()            # verified research, which a letter may state
    cv_pages: int = 2
    letter_pages: int = 1
    layout_profile: layout.LayoutProfile = layout.LayoutProfile()
    waive_unverified: bool = False                  # the person's explicit decision, recorded in the report
    dump_dir: Path | None = None                    # where each PDF's raw text layer is written, for diagnosis


@dataclass(frozen=True)
class Result:
    failures: tuple[str, ...]
    unverified: tuple[str, ...] = ()
    documents: latexrun.Documents | None = None
    coverage: keywords.Coverage | None = None
    findings: tuple[str, ...] = ()                  # things to act on that don't fail the checks (synonym-only terms)
    stretch: tuple[str, ...] = ()                   # CV bullets that reframe too far: shown to the person to keep, soften or drop
    checks: tuple[tuple[str, bool], ...] = ()       # every check that ran, in order, and whether it passed

    @property
    def passed(self) -> bool:
        return not self.failures and self.documents is not None


def contact(profile_md: str) -> tuple[str, str, str]:
    """The name, email and phone the CV's text layer must show, read from the profile's Contact section."""
    name = profiles.parse_name(profile_md)
    try:
        body = profiles.section(profile_md, "Contact")
    except ValueError:
        return name, "", ""
    return name, (re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", body) or [""])[0], (re.search(r"\+\d[\d ()./-]{5,}\d", body) or [""])[0]


def source_findings(cv_tex: str, cover_tex: str, cv_language: str = "en") -> list[str]:
    """What is wrong in the sources before anything is compiled: unsafe commands, unescaped specials, a way of fitting
    that is not cutting content, and the CV's structure rules."""
    found = [("cv.tex", f) for f in latex.check(cv_tex) + latex.cv_rules(cv_tex, cv_language)]
    return [f"{name}: {finding}" for name, finding in found + [("cover.tex", f) for f in latex.check(cover_tex) + latex.letter_rules(cover_tex)]]


def write_dump(folder: Path, name: str, text: str) -> str | None:
    """Write a PDF's RAW text layer (never the folded text the checks compare) to `<folder>/<name>.txt`, with parent folders
    made and exactly one trailing newline. Returns what to tell the drafter if it could not be written, else None."""
    path = Path(folder) / f"{name}.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    except OSError as error:
        return f"the text dump {path} could not be written ({error.strerror or type(error).__name__})"
    return None


READ_FAILURES = ("pdf", "pdfinfo", "text")          # without these nothing else can be judged; the others only lose a check


def verdicts(name: str, verdict: pdfcheck.Verdict, failures: list[str], unverified: list[str]) -> None:
    if verdict.status == pdfcheck.FAILED:
        failures.append(f"{name}: " + verdict.feedback())
    elif verdict.status == pdfcheck.UNVERIFIED:
        unverified.append(f"{name}: " + verdict.feedback())


def judge(documents: latexrun.Documents, cv_tex: str, cover_tex: str, ctx: Context) -> Result:
    """Judge two rendered documents. Compile failures come back word for word and stop here."""
    failures: list[str] = []
    for rendered in (documents.cv, documents.cover):
        if not rendered.ok:
            failures.append(rendered.failure_report())
    if failures:
        return Result(tuple(failures), documents=documents, checks=(("compile", False),))
    unverified: list[str] = []
    checks: list[tuple[str, bool]] = [("compile", True)]
    for rendered in (documents.cv, documents.cover):      # the text goes to disk BEFORE any check, so a failure leaves it
        if ctx.dump_dir is not None and (why := write_dump(ctx.dump_dir, rendered.name, rendered.text)):
            failures.append(why)
    unread = [f"{r.name}.pdf: {e}" for r in (documents.cv, documents.cover) for e in r.tool_errors if e.split(":")[0] in READ_FAILURES]
    if unread:
        return Result(tuple(failures + unread), documents=documents, checks=(("compile", True), ("PDF read", False)))

    def mark(label: str, before: int) -> None:
        checks.append((label, len(failures) + len(unverified) == before))

    name, email, phone = contact(ctx.profile)
    headings = tuple(re.findall(r"\\section\{([^}]*)\}", cv_tex))
    cv, cover = documents.cv, documents.cover
    lone = tuple(dict.fromkeys(re.findall(r"(?:^|[\s(,])((?:19|20)\d\d)\)?\s*$", ctx.profile, re.M)))   # a year the profile gives alone
    spec = pdfcheck.TextLayerSpec(pages=ctx.cv_pages, name=name, email=email, phone=phone, headings=headings or ("Profile",),
                                  lone_years_ok=lone)
    before = len(failures) + len(unverified)
    verdicts("cv.pdf", pdfcheck.verify_cv_text_layer(cv.pdfinfo, cv.text, spec), failures, unverified)
    mark("CV page count and ATS text layer", before)
    before = len(failures) + len(unverified)
    verdicts("cover.pdf", pdfcheck.verdict_of(pdfcheck.check_page_count(cover.pdfinfo, ctx.letter_pages), "pdfinfo"), failures, unverified)
    mark("letter page count", before)
    before = len(failures) + len(unverified)
    for label, rendered in (("cv.pdf", cv), ("cover.pdf", cover)):
        verdicts(label + " layout", layout.verify_layout(rendered.bbox, ctx.layout_profile), failures, unverified)
    mark("page layout", before)
    reference = f"Reference: {ctx.reference}" if ctx.reference else ""
    stretch, never = reframe.split(cv_tex, ctx.profile)
    cv_found = style.check(cv.text, (ctx.cv_language,)) + grounding.check(cv.text, [ctx.profile, reference], ctx.today) + never
    letter_found = (style.check(cover.text, (ctx.letter_language,)) + letter.check(
        cover.text, posting=ctx.posting, company=ctx.company, role=ctx.role, today=ctx.today, addressee=ctx.addressee,
        reference=ctx.reference, cv_text=cv.text, cv_language=ctx.cv_language)
        + grounding.check(cover.text, [ctx.profile, reference, *ctx.company_claims], ctx.today)
        + consistency.titles(cover.text, ctx.profile))
    before = len(failures) + len(unverified)
    failures += [f"cv.pdf: {m}" for m in cv_found]
    mark("CV style and grounding", before)
    before = len(failures) + len(unverified)
    failures += [f"cover.pdf: {m}" for m in letter_found]
    mark("letter rules, style and grounding", before)
    before = len(failures) + len(unverified)
    # a phrase counts as present if either text extractor shows it: pypdf-style extractors can put spaces around punctuation
    cv_text = cv.text if cv.text_raw in ("", cv.text) else cv.text + "\n" + cv.text_raw
    coverage = keywords.cover(ctx.requirements, ctx.profile, cv_text, cover.text, ctx.cv_language)
    failures += [f"keywords: {m}" for m in coverage.failures]
    mark("keyword coverage", before)
    if unverified and not ctx.waive_unverified:
        failures += [f"{m}\n(this check could not run, so it is not a pass)" for m in unverified]
    return Result(tuple(failures), tuple(unverified), documents, coverage, tuple(coverage.findings), stretch=tuple(stretch), checks=tuple(checks))


def verify(cv_tex: str, cover_tex: str, ctx: Context, *, compile_documents=latexrun.compile_documents,
           image: str = latexrun.DEFAULT_IMAGE, **templates) -> Result:
    """The whole gate. Source problems stop it before the (slow) compile; a compile failure stops it before the PDF checks."""
    early = source_findings(cv_tex, cover_tex, ctx.cv_language)
    if early:
        return Result(tuple(early))
    result = judge(compile_documents(cv_tex, cover_tex, image, **templates), cv_tex, cover_tex, ctx)
    return replace(result, checks=(("LaTeX source", True), *result.checks))

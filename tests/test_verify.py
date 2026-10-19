"""verify.py: every check on a drafted pair, and what must never pass."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from jobloop import keywords, latexrun, verify

FIX = Path(__file__).parent / "fixtures"
PROFILE = (FIX.parent.parent / "fixtures" / "profile.md").read_text().replace(" (fictional candidate, for tests and demos)", "")
POSTING = (FIX / "writing" / "posting_en.md").read_text()
LETTER = (FIX / "writing" / "letter_en.txt").read_text()
CV = """Alex Fernsby
Rotterdam, Netherlands | Mobile +1 555 0100 | alex.fernsby@example.com

Profile
Data engineer with 7 years of experience building batch and streaming pipelines for retail and logistics companies.
Strongest in Python, SQL and Apache Airflow, with incremental dbt models in production.

Core Competencies
Pipelines: Apache Airflow, Kafka, PostgreSQL and BigQuery, 40 million events per day.
Modelling: dbt, SQL and Python, a nightly rebuild cut from 6 hours to 55 minutes.
Leadership: mentors two junior engineers and leads the weekly data-modelling review.

Professional Experience
Senior Data Engineer, Tidewater Logistics (Rotterdam), March 2022 - present
Owns the shipment-events pipeline and the delivery-performance mart, built on incremental dbt models and Apache Airflow.
Data Engineer, Marlow Retail Group (Utrecht), June 2018 - February 2022
Built the first Airflow deployment and migrated 45 cron jobs onto it; designed a star schema used by 12 analysts.
Junior Analyst, Delft Energy Cooperative (Delft), September 2016 - May 2018
Automated monthly reports with Python and SQL, saving the team about 3 days each month.

Education
MSc Computer Science (data systems), Delft University of Technology, 2016
BSc Computer Science, Delft University of Technology, 2014
""" + "\n".join(["Skills: Python, SQL, PostgreSQL, Apache Airflow, dbt, Kafka, BigQuery, Docker."] * 2)
CV_TEX = "\\documentclass{moderncv}\n\\begin{document}\n\\section{Profile}\n\\section{Core Competencies}\n\\section{Professional Experience}\n\\section{Education}\n\\end{document}\n"
COVER_TEX = "\\documentclass{jobletter}\n\\begin{document}\nHello.\n\\end{document}\n"
REQS = (keywords.Requirement("Airflow"), keywords.Requirement("dbt"), keywords.Requirement("Scala"))
CTX = verify.Context(profile=PROFILE, posting=POSTING, requirements=REQS, company="Brightwell Analytics", role="Senior Data Engineer",
                     today=date(2026, 10, 19), reference="BW-DE-0417", cv_language="en", letter_language="en", waive_unverified=True)


def rendered(name, text, pages, ok=True, bbox=""):
    return latexrun.Rendered(name=name, ok=ok, pdf=b"%PDF" if ok else None, log_tail="", errors=("! Undefined control sequence.",) if not ok else (),
                             timed_out=False, pdfinfo=f"Pages:           {pages}\n", text=text, text_raw=text, bbox=bbox, fonts="", tool_errors=())


def docs(cv=CV, letter=LETTER, cv_pages=2, letter_pages=1):
    return latexrun.Documents(cv=rendered("cv", cv, cv_pages), cover=rendered("cover", letter, letter_pages))


def judge(documents=None, ctx=CTX, cv_tex=CV_TEX, cover_tex=COVER_TEX):
    return verify.judge(documents or docs(), cv_tex, cover_tex, ctx)


def failing(result):
    return "\n".join(result.failures)


def test_a_good_pair_passes_when_only_the_unrunnable_layout_check_is_waived():
    result = judge()
    assert result.passed, failing(result)
    assert any("layout" in u for u in result.unverified) and result.coverage.rows


def test_a_check_that_could_not_run_is_never_a_pass_unless_the_person_waived_it():
    result = judge(ctx=replace(CTX, waive_unverified=False))
    assert not result.passed and "could not run, so it is not a pass" in failing(result)


def test_a_compile_failure_comes_back_with_latexs_own_error_and_stops_the_other_checks():
    broken = latexrun.Documents(cv=rendered("cv", "", 0, ok=False), cover=rendered("cover", LETTER, 1))
    result = judge(broken)
    assert not result.passed and "Undefined control sequence" in failing(result) and len(result.failures) == 1


def test_the_source_is_linted_before_anything_is_compiled():
    called = []
    result = verify.verify("\\documentclass{x}\\begin{document}50% sure \\write18{ls}\\end{document}", COVER_TEX, CTX,
                           compile_documents=lambda *a, **k: called.append(1))
    assert not result.passed and not called


def test_wrong_page_counts_fail_for_each_document():
    assert "cv.pdf" in failing(judge(docs(cv_pages=3))) and "3 page" in failing(judge(docs(cv_pages=3)))
    assert "cover.pdf" in failing(judge(docs(letter_pages=2)))


def test_a_gap_on_the_cv_is_never_accepted():
    result = judge(docs(cv=CV + "\nScala and Spark pipelines at scale"))
    assert not result.passed and "Scala" in failing(result)


def test_a_requirement_the_profile_backs_must_be_on_the_cv():
    result = judge(docs(cv=CV.replace("dbt", "d b t")))
    assert not result.passed and "dbt" in failing(result)


def test_a_required_gap_must_be_acknowledged_in_the_letter():
    result = judge(docs(letter=LETTER.replace("Scala or Spark", "that stack")))
    assert not result.passed and "Scala" in failing(result)


def test_an_em_dash_and_an_invented_number_are_caught_in_the_right_document():
    result = judge(docs(cv=CV + "\nLed 300 engineers \u2014 globally"))
    assert not result.passed and "cv.pdf" in failing(result)
    result = judge(docs(letter=LETTER.replace("40 million", "400 million")))
    assert not result.passed and "cover.pdf" in failing(result)


def test_the_cv_text_layer_must_show_the_literal_email_and_phone():
    result = judge(docs(cv=CV.replace("alex.fernsby@example.com", "email icon").replace("+1 555 0100", "phone icon")))
    assert not result.passed and "alex.fernsby@example.com" in failing(result)


def test_an_en_dash_in_a_cv_date_range_fails_because_it_breaks_import_into_applicant_systems():
    result = judge(docs(cv=CV.replace("March 2022 - present", "March 2022 \u2013 present")))
    assert not result.passed and "dash" in failing(result).lower()


def test_the_coverage_table_is_returned_for_the_report():
    table = judge().coverage.table()
    assert "| Airflow |" in table and "missing-gap" in table


def test_the_source_rules_stop_a_draft_that_fits_by_shrinking_before_any_compile():
    def never(*a, **k):
        raise AssertionError("nothing is compiled while the source breaks a rule")
    shrunk = CV_TEX.replace("\\section{Profile}", "\\newgeometry{margin=1cm}\n\\section{Profile}")
    result = verify.verify(shrunk, COVER_TEX, CTX, compile_documents=never)
    assert not result.passed and "cv.tex" in failing(result) and "page geometry" in failing(result)
    danish = verify.verify(CV_TEX, COVER_TEX, replace(CTX, cv_language="da"), compile_documents=never)
    assert "English heading" in failing(danish)


def test_the_checklist_lists_every_check_with_pass_or_fail():
    good = judge()
    labels = [label for label, _ in good.checks]
    assert labels == ["compile", "CV page count and ATS text layer", "letter page count", "page layout", "CV style and grounding",
                      "letter rules, style and grounding", "keyword coverage"]
    assert good.passed and [ok for _, ok in good.checks] == [True, True, True, False, True, True, True]   # layout was waived, not run
    bad = judge(docs(cv_pages=3))
    assert dict(bad.checks)["CV page count and ATS text layer"] is False and dict(bad.checks)["letter page count"] is True
    assert dict(judge(docs(cv=CV + "\nLed 300 engineers \u2014 globally")).checks)["CV style and grounding"] is False
    assert judge(docs(letter_pages=2)).checks[2] == ("letter page count", False)
    assert dict(judge(documents=latexrun.Documents(cv=rendered("cv", CV, 2, ok=False), cover=rendered("cover", LETTER, 1))).checks) == {"compile": False}


def test_a_phrase_missing_from_one_extractor_is_found_in_the_other():
    layout_only = CV.replace("Airflow", "Orchestrator")                    # this extractor mangled the term
    one = docs(cv=layout_only)
    assert any("Airflow" in f for f in judge(one).failures if f.startswith("keywords"))
    both = latexrun.Documents(cv=replace(one.cv, text_raw=CV), cover=one.cover)
    assert not any("Airflow" in f for f in judge(both).failures if f.startswith("keywords"))
    # negative space: a phrase in neither extractor is still missing, and a gap in the second one is still stuffing
    assert "dbt" in failing(judge(docs(cv=CV.replace("dbt", "d b t"))))
    stuffed = latexrun.Documents(cv=replace(one.cv, text_raw=CV + "\nScala"), cover=one.cover)
    assert "Scala" in failing(judge(stuffed))


def test_the_text_layer_is_dumped_raw_before_any_check_so_a_failed_run_leaves_it(tmp_path):
    en_dash = CV.replace("June 2018 - February 2022", "June 2018 \u2013 February 2022")
    result = judge(docs(cv=en_dash), ctx=replace(CTX, dump_dir=tmp_path / "deep" / "dumps"))
    assert not result.passed and any("U+2013" in f for f in result.failures)                  # the checks failed ...
    dump = (tmp_path / "deep" / "dumps" / "cv.txt").read_text()
    assert "June 2018 \u2013 February 2022" in dump                                           # ... and the dump is the raw layer
    assert dump.endswith("\n") and not dump.endswith("\n\n") and (tmp_path / "deep" / "dumps" / "cover.txt").read_text() == LETTER.rstrip("\n") + "\n"


def test_a_dump_that_cannot_be_written_is_a_failure_naming_the_path_not_a_crash(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    result = judge(ctx=replace(CTX, dump_dir=blocker / "dumps"))
    assert any(f"{blocker / 'dumps' / 'cv.txt'} could not be written" in f for f in result.failures)
    assert verify.write_dump(tmp_path, "ok", "no newline") is None and (tmp_path / "ok.txt").read_text() == "no newline\n"


def test_a_tool_that_could_not_read_the_pdf_stops_the_checks_with_its_message():
    broken = rendered("cv", "", 2)
    broken = replace(broken, pdfinfo="", tool_errors=("pdfinfo: could not read the PDF: Syntax Error: bad xref",))
    result = judge(latexrun.Documents(cv=broken, cover=rendered("cover", LETTER, 1)))
    assert result.failures == ("cv.pdf: pdfinfo: could not read the PDF: Syntax Error: bad xref",) and not result.passed
    assert dict(result.checks) == {"compile": True, "PDF read": False}                       # nothing is guessed from empty text
    fonts_only = replace(rendered("cv", CV, 2), tool_errors=("fonts: could not read the PDF: command failed",))
    assert judge(latexrun.Documents(cv=fonts_only, cover=rendered("cover", LETTER, 1))).passed  # a lost font list is not a lost text layer


def test_a_missing_pdf_is_a_verification_error_before_any_check():
    gone = replace(rendered("cv", "", 2), pdf=None, tool_errors=("pdf: cv.pdf is not a file, so nothing was extracted",))
    result = judge(latexrun.Documents(cv=gone, cover=rendered("cover", LETTER, 1)))
    assert not result.passed and "cv.pdf: pdf: cv.pdf is not a file" in failing(result) and "PDF read" in dict(result.checks)


def test_the_page_count_is_checked_before_the_layout_and_the_layout_runs_for_both_documents(monkeypatch):
    seen = []
    monkeypatch.setattr(verify.layout, "verify_layout", lambda bbox, profile: seen.append(bbox) or verify.pdfcheck.verdict_of([], "x"))
    both = latexrun.Documents(cv=rendered("cv", CV, 2, bbox="<cv-bbox>"), cover=rendered("cover", LETTER, 1, bbox="<cover-bbox>"))
    result = judge(both)
    assert seen == ["<cv-bbox>", "<cover-bbox>"]
    labels = [label for label, _ in result.checks]
    assert labels.index("CV page count and ATS text layer") < labels.index("letter page count") < labels.index("page layout")


def test_coverage_scored_on_extraction_not_source():
    source_has_it = CV_TEX + "% dbt, Airflow\n"                                       # the LaTeX source names the terms
    extracted = CV.replace("dbt", "d b t")                                             # the PDF text layer lost one of them
    result = judge(docs(cv=extracted), cv_tex=source_has_it)
    assert any("dbt" in f for f in result.failures)                                    # the missing term is judged on the extraction

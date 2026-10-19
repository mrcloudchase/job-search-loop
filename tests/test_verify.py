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

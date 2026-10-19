"""keywords.py: coverage of the posting's requirements, and the rule that a gap is never stuffed."""
from __future__ import annotations

from pathlib import Path

from jobloop.keywords import Requirement, cover

PROFILE = (Path(__file__).parent.parent / "fixtures" / "profile.md").read_text()
CV = (Path(__file__).parent.parent / "fixtures" / "cv.txt").read_text()

REQS = (
    Requirement("Python"), Requirement("Airflow"), Requirement("dbt"), Requirement("Great Expectations"),
    Requirement("Scala"), Requirement("Spark"), Requirement("Kafka", "preferred"),
)


def status(coverage, keyword):
    return next(r.status for r in coverage.rows if r.keyword == keyword)


def test_covered_verbatim_or_inflection():
    c = cover(REQS, PROFILE, CV)
    assert status(c, "Python") == "covered" and status(c, "dbt") == "covered"
    c = cover([Requirement("pipeline")], PROFILE, "I built pipelines for retail data.")
    assert status(c, "pipeline") == "covered"


def test_matching_ignores_case_hyphens_and_spacing():
    c = cover([Requirement("data quality")], "I wrote data-quality tests.", "Data  Quality tests on 30 tables")
    assert status(c, "data quality") == "covered"


def test_a_term_inside_a_longer_word_does_not_count():
    c = cover([Requirement("SQL")], "I use SQL.", "I use NoSQLish things.")
    assert status(c, "SQL") == "missing-have"


def test_missing_gap_never_added_to_cv():
    c = cover(REQS, PROFILE, CV + "\nSKILLS\nScala, Spark\n")
    assert status(c, "Scala") == "missing-gap" and status(c, "Spark") == "missing-gap"
    assert any('"Scala"' in f or "Scala" in f for f in c.failures)
    assert len([f for f in c.failures if "never stuffed" in f]) == 2


def test_unsupported_keyword_never_stuffed():
    c = cover([Requirement("Kubernetes")], PROFILE, "Deployed with Kubernetes on 3 clusters.")
    assert c.failures and "Kubernetes" in c.failures[0]


def test_a_gap_stuffed_through_a_synonym_is_also_a_failure():
    c = cover([Requirement("Spark", synonyms=("Apache Spark",))], PROFILE, "Wrote Apache Spark jobs.")
    assert c.failures and "never stuffed" in c.failures[0]


def test_a_gap_is_left_missing_without_failure_when_the_cv_does_not_claim_it():
    c = cover([Requirement("Scala", "preferred")], PROFILE, CV)
    assert status(c, "Scala") == "missing-gap" and c.failures == ()


def test_not_yet_lines_of_the_profile_back_nothing():
    assert status(cover([Requirement("Snowflake")], PROFILE, "Snowflake expert"), "Snowflake") == "missing-gap"


def test_missing_have_is_a_failure():
    c = cover([Requirement("Terraform")], PROFILE, CV.replace("Terraform, ", ""))
    assert status(c, "Terraform") == "missing-have"
    assert c.failures and "Terraform" in c.failures[0]


def test_synonym_only_asks_for_the_postings_own_term_and_is_not_a_failure():
    c = cover([Requirement("orchestration", synonyms=("scheduling",))], "Airflow scheduling and orchestration",
              "Owns Airflow scheduling for 45 jobs.")
    assert status(c, "orchestration") == "synonym-only"
    assert c.failures == () and 'use the posting\'s own term' in c.findings[0]


def test_posting_term_preferred_over_synonym_when_true():
    c = cover([Requirement("ETL", synonyms=("data pipelines",))], "ETL and data pipelines at scale.",
              "Built data pipelines.")
    assert status(c, "ETL") == "synonym-only"
    assert "the profile uses the term" in c.findings[0]


def test_cross_language_coverage_is_synonym_only():
    c = cover([Requirement("datakvalitet", synonyms=("data quality",), language="da")],
              "Data quality tests on 30 tables.", "Data quality tests on 30 tables.", cv_language="en")
    assert status(c, "datakvalitet") == "synonym-only"
    assert "posting in da, CV in en" in c.rows[0].note


def test_every_posting_requirement_matched_or_gapped():
    c = cover(REQS, PROFILE, CV)
    assert [r.keyword for r in c.rows] == [r.term for r in REQS]
    assert {r.status for r in c.rows} <= {"covered", "synonym-only", "missing-have", "missing-gap"}


def test_a_required_gap_must_be_acknowledged_in_the_letter():
    unaddressed = cover([Requirement("Scala")], PROFILE, CV, letter="I love pipelines.")
    assert unaddressed.failures and "does not acknowledge" in unaddressed.failures[0]
    bridged = cover([Requirement("Scala")], PROFILE, CV, letter="I have not worked with Scala; my closest work is SQL.")
    assert bridged.failures == ()
    preferred = cover([Requirement("Scala", "preferred")], PROFILE, CV, letter="I love pipelines.")
    assert preferred.failures == ()


def test_coverage_table_columns():
    table = cover(REQS, PROFILE, CV).table().splitlines()
    assert table[0] == "| Keyword | Priority | Status | Note |"
    assert any(l.startswith("| Scala | required | missing-gap |") for l in table)
    assert any(l.startswith("| Kafka | preferred | covered |") for l in table)


def test_a_tex_cv_is_read_as_text():
    c = cover([Requirement("Python")], PROFILE, r"\begin{document}\item \textbf{Python} pipelines\end{document}")
    assert status(c, "Python") == "covered"


def test_a_bad_priority_is_refused():
    import pytest
    with pytest.raises(ValueError):
        Requirement("Python", "nice")

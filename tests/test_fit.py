"""fit.py: the harness judges, the code decides. Evidence is checked, the total is computed, gates veto."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fake_server import FakeServer
from jobloop import fit
from jobloop.turns import Api, Turns

FIXTURES = Path(__file__).parent.parent / "fixtures"
PROFILE = (FIXTURES / "profile.md").read_text()
POSTING = (FIXTURES / "posting.md").read_text()

GOOD = {
    "title": "Senior Data Engineer", "company": "Brightwell Analytics",
    "scores": {"technical": 80, "experience": 70, "behavioral": 60, "career": 90},
    "location": "PASS", "language": "PASS",
    "requirements": [
        {"term": "Airflow", "status": "match", "evidence": "orchestrated with Apache Airflow"},
        {"term": "Scala", "status": "gap", "evidence": ""},
    ],
    "strengths": ["Airflow in production"], "gaps": ["No Scala"],
}


def parse(**changes):
    data = {**GOOD, **changes}
    return fit.parse(json.dumps(data), PROFILE, POSTING)


def test_a_good_fit_is_accepted_and_the_total_is_computed_by_the_code():
    result, failures = parse()
    assert failures == [] and result.total == 78  # 24 + 17.5 + 9 + 27 = 77.5
    assert result.band == "strong fit" and result.key == "brightwell-analytics_senior-data-engineer"


@pytest.mark.parametrize("total,name", [(75, "strong fit"), (74, "good fit"), (60, "good fit"), (59, "moderate fit"), (45, "moderate fit"),
                                        (44, "weak fit"), (30, "weak fit"), (29, "poor fit"), (0, "poor fit")])
def test_the_bands(total, name):
    assert fit.band(total) == name


def test_a_model_reported_total_is_ignored():
    result, _ = parse(total=100, band="strong fit")
    assert result.total == 78


def test_evidence_that_is_not_a_quote_from_the_profile_is_refused():
    reqs = [{"term": "Airflow", "status": "match", "evidence": "ten years of Airflow at Google"}]
    result, failures = parse(requirements=reqs)
    assert result is None and "not a quote from profile.md" in failures[0]


def test_a_skill_the_profile_lists_under_not_yet_cannot_be_a_match():
    reqs = [{"term": "Scala", "status": "match", "evidence": "Not yet: Scala, Spark, Kubernetes, Snowflake"}]
    assert "not a quote" in parse(requirements=reqs)[1][0]


def test_calling_a_backed_skill_a_gap_is_refused():
    assert "backs it" in parse(requirements=[{"term": "dbt", "status": "gap", "evidence": ""}])[1][0]


@pytest.mark.parametrize("term", ["5+ years building production data pipelines in Python", "", "   "])
def test_a_requirement_must_be_a_short_name_not_a_sentence(term):
    assert "short name" in parse(requirements=[{"term": term, "status": "gap", "evidence": ""}])[1][0]


def test_a_requirement_that_is_not_in_the_posting_is_refused():
    assert "not in the posting" in parse(requirements=[{"term": "Kubernetes", "status": "gap", "evidence": ""}])[1][0]


def test_a_requirement_only_in_a_hidden_comment_is_not_in_the_posting():
    reqs = [{"term": "best employer", "status": "gap", "evidence": ""}]
    assert "not in the posting" in parse(requirements=reqs)[1][0]


@pytest.mark.parametrize("scores", [{"technical": 101, "experience": 1, "behavioral": 1, "career": 1},
                                    {"technical": -1, "experience": 1, "behavioral": 1, "career": 1},
                                    {"technical": 50.5, "experience": 1, "behavioral": 1, "career": 1},
                                    {"technical": True, "experience": 1, "behavioral": 1, "career": 1},
                                    {"technical": 50, "experience": 1, "behavioral": 1}, {}])
def test_scores_outside_the_scale_or_missing_are_refused(scores):
    assert "scores must have" in parse(scores=scores)[1][0]


def test_a_gate_must_be_pass_flag_or_fail():
    assert "location must be" in parse(location="maybe")[1][0]


def test_not_json_is_refused_with_a_message_the_harness_can_act_on():
    assert fit.parse("Sure! Here is my evaluation:", PROFILE, POSTING)[1] == ["fit.json must be one JSON object, and nothing else."]


def test_a_failed_gate_vetoes_any_score():
    result, _ = parse(scores={"technical": 100, "experience": 100, "behavioral": 100, "career": 100}, location="FAIL")
    assert result.total == 100 and not result.go(60) and result.vetoes == ["location gate failed"]


def test_a_flagged_gate_does_not_veto_and_the_minimum_decides():
    result, _ = parse(language="FLAG")
    assert result.go(60) and not result.go(80)


def test_evaluate_sends_failures_back_and_returns_the_valid_fit(tmp_path):
    server = FakeServer([{"files": {"fit.json": "not json"}}, {"files": {"fit.json": json.dumps(GOOD)}}])
    with Turns(Api("http://x", server.transport()), tmp_path / "log.jsonl") as turns:
        result = fit.evaluate(turns, "claude-code", PROFILE, POSTING)
    assert result.total == 78
    assert server.requests[1]["previous_response_id"] == "resp_1" and "one JSON object" in server.requests[1]["input"][0]["text"]
    assert {f["filename"] for f in server.requests[0]["input"][1:]} == {"profile.md", "posting.md"}
    assert "untrusted" in server.requests[0]["instructions"]


def test_evaluate_returns_none_when_the_harness_never_gets_it_right(tmp_path):
    server = FakeServer([{"files": {"fit.json": "no"}}] * 3)
    with Turns(Api("http://x", server.transport()), tmp_path / "log.jsonl") as turns:
        assert fit.evaluate(turns, "claude-code", PROFILE, POSTING, rounds=2) is None


def test_save_writes_the_fit_and_one_keyword_per_requirement(tmp_path):
    result, _ = parse()
    path = fit.save(result, tmp_path)
    assert json.loads(path.read_text())["total"] == 78
    assert (tmp_path / f"{result.key}.keywords").read_text() == "Airflow\nScala\n"

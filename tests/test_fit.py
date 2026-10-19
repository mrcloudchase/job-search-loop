"""fit.py: the score, bands, gates and vetoes are computed by code; the harness reports only facts, and every
quote is checked."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fake_server import FakeServer
from jobloop import fit as F
from jobloop.turns import Api, Turns

FIX = Path(__file__).parent / "fixtures" / "fit"
PROFILE = (FIX / "profile.md").read_text()
POSTING = (FIX / "posting.md").read_text()
GOOD = json.loads((FIX / "good.json").read_text())


def data(**changes):
    d = copy.deepcopy(GOOD)
    d.update(changes)
    return d


def build(d=None, profile=PROFILE, posting=POSTING):
    return F.build(GOOD if d is None else d, profile, posting, "demo")


def ok(d=None, profile=PROFILE, posting=POSTING) -> F.Fit:
    fit, failures = build(d, profile, posting)
    assert failures == [] and fit is not None
    return fit


def refused(d, profile=PROFILE, posting=POSTING) -> str:
    fit, failures = build(d, profile, posting)
    assert fit is None and failures
    return "\n".join(failures)


def gate(fit, name):
    return next(g for g in fit.gates if g.name == name)


def scores(t, e, b, c):
    return {"technical": t, "experience": e, "behavioural": b, "career": c}


# ── the score ──
def test_weighted_score_example_72():
    assert F.score(scores(80, 70, 60, 70)) == 72      # 24 + 17.5 + 9 + 21 = 71.5 -> 72
    assert sum(F.WEIGHTS.values()) == 100


def test_rounding_half_up():
    assert F.score(scores(100, 100, 50, 50)) == 78     # 30 + 25 + 7.5 + 15 = 77.5 -> 78
    assert F.score(scores(1, 0, 0, 0)) == 0           # 0.3 -> 0


def test_boundary_scores_valid():
    assert F.score(scores(0, 0, 0, 0)) == 0 and F.score(scores(100, 100, 100, 100)) == 100


@pytest.mark.parametrize("bad", [True, 72.0, 72.5, float("nan"), float("inf"), 10**30, -1, 101, "80", None])
def test_invalid_scores_rejected_per_dimension(bad):
    for dimension in F.WEIGHTS:
        with pytest.raises(ValueError, match=dimension):
            F.score({**scores(50, 50, 50, 50), dimension: bad})
        text = refused(data(scores={**scores(50, 50, 50, 50), dimension: bad}))
        assert f"scores.{dimension}" in text


def test_missing_dimension_error_no_write(tmp_path):
    for d in ({}, None, scores(50, 50, 50, 50) | {"career": None}, {"technical": 50}):
        bad = data(scores=d)
        text = refused(bad)
        assert "scores" in text
    assert list(tmp_path.iterdir()) == []              # nothing was built, so there is nothing to save


def test_band_boundaries_75_60_45_30():
    table = {100: "strong", 75: "strong", 74: "good", 60: "good", 59: "moderate", 45: "moderate", 44: "weak",
             30: "weak", 29: "poor", 0: "poor"}
    assert {t: F.band(t) for t in table} == table


def test_band_to_recommendation_text():
    assert set(F.RECOMMENDATION) == {b for _, b in F.BANDS}
    assert F.RECOMMENDATION["strong"].startswith("Apply") and F.RECOMMENDATION["poor"] == "Skip."
    assert ok().recommendation == F.RECOMMENDATION[ok().band]


def test_a_harness_reported_total_band_and_gate_verdict_are_ignored():
    forged = data(total=99, band="strong", recommendation="Apply", vetoes=[], shortlisted=True,
                  gates={"language": "PASS", "location": "PASS"})
    forged["languages"] = [{"language": "Russian", "level": "B2", "required": True, "text": "Fluent English"}]
    forged["languages"][0]["text"] = "Fluent English with our international team"
    fit = ok(forged)
    assert fit.total == F.score(fit.scores) != 99
    assert gate(fit, "language").verdict == "FAIL" and not fit.shortlisted


# ── vetoes ──
def test_an_on_site_day_limit_is_judged_by_the_code_not_by_a_model_claimed_hit():
    breakers = F.read_deal_breakers(PROFILE)
    hit = [{"deal_breaker": "more than 3 days a week on site", "text": "hybrid, 2 days on site"}]
    base = {"onsite_days": 2, "relocation_required": False, "travel": "none", "text": "hybrid, 2 days on site"}
    assert F.location_gate({**base, "deal_breaker_hits": hit}, breakers, False).verdict == "PASS"
    assert F.location_gate({**base, "onsite_days": 4, "deal_breaker_hits": hit}, breakers, False).verdict == "FAIL"


def test_a_permit_requirement_must_quote_words_about_a_permit_not_the_location_line():
    loc = next(l for l in POSTING.splitlines() if l.strip() and not l.startswith("#"))
    bad = data(eligibility=[{"kind": "work_authorisation", "stance": "requires", "value": "right to work in Denmark",
                             "text": loc.strip(), "scope": "role"}])
    assert "does not talk about work authorisation" in refused(bad)
    assert "does not talk about work authorisation" not in "\n".join(build(data(eligibility=[
        {"kind": "work_authorisation", "stance": "requires", "value": "EU", "text": loc.strip() + " right to work in the EU", "scope": "role"}]))[1])


def test_location_verdict_rules():
    def location(**kw):
        return {"onsite_days": None, "relocation_required": False, "travel": "none", "text": "", "deal_breaker_hits": [], **kw}
    breakers = F.read_deal_breakers(PROFILE)
    q = "hybrid, 2 days on site"
    assert F.location_gate(location(onsite_days=2, text=q), breakers, False).verdict == "PASS"
    assert F.location_gate(location(onsite_days=3, text=q), breakers, False).verdict == "PASS"
    assert F.location_gate(location(onsite_days=4, text=q), breakers, False).verdict == "FAIL"
    assert F.location_gate(location(relocation_required=True, text=q), breakers, False).verdict == "FAIL"
    assert F.location_gate(location(relocation_required=True, text=q), breakers, True).verdict == "FLAG"
    assert F.location_gate(location(travel="frequent", text=q), breakers, False).verdict == "FLAG"
    assert F.location_gate(location(travel="occasional", text=q), breakers, False).verdict == "PASS"


def test_location_fail_vetoes_high_score():
    d = data(scores=scores(95, 95, 95, 95))
    d["location"] = {"onsite_days": 5, "relocation_required": False, "travel": "none", "text": "hybrid, 2 days on site",
                     "deal_breaker_hits": []}
    fit = ok(d)
    assert fit.total == 95 and fit.band == "strong"
    assert not fit.shortlisted and any(v.startswith("location:") for v in fit.vetoes)


def test_ninety_point_vetoed_job_not_ranked():
    d = data(scores=scores(90, 90, 90, 90))
    d["languages"] = [{"language": "Russian", "level": "", "required": True, "text": "Fluent English with our international team"}]
    fit = ok(d)
    assert fit.total == 90 and fit.shortlisted is False and fit.as_dict()["shortlisted"] is False


def test_a_deal_breaker_hit_vetoes_and_must_be_one_of_the_profiles():
    d = data()
    d["location"]["deal_breaker_hits"] = [{"deal_breaker": "on-call without compensation", "text": "hybrid, 2 days on site"}]
    assert gate(ok(d), "location").verdict == "FAIL"
    d["location"]["deal_breaker_hits"][0]["deal_breaker"] = "working on Sundays"
    assert "not one of profile.md's deal-breakers" in refused(d)


def test_unknown_verdict_value_rejected():
    for verdict in ("MAYBE", "pass", "", "OK"):
        with pytest.raises(ValueError):
            F.Gate("language", verdict)
    with pytest.raises(ValueError):
        F.Gate("salary", "PASS")


def test_a_failed_gate_score_is_recorded_but_never_counts():
    fit = ok(data(scores=scores(90, 90, 90, 90), eligibility=[
        {"kind": "citizenship", "stance": "requires", "value": "US citizen", "text": "Fluent English with our international team"}]))
    assert gate(fit, "eligibility").verdict == "FAIL"
    assert fit.total == 90 and not fit.shortlisted and fit.vetoes


def test_eligibility_runs_before_scoring_for_non_citizen():
    d = data(scores=scores(100, 100, 100, 100), eligibility=[
        {"kind": "citizenship", "stance": "requires", "value": "US citizen", "text": "US citizenship is required."}])
    fit = ok(d, posting=POSTING + "\nUS citizenship is required.\n")
    assert gate(fit, "eligibility").verdict == "FAIL" and not fit.shortlisted
    assert "US citizenship is required" in fit.vetoes[0]


# ── eligibility and permit ──
def item(kind, stance="requires", value="x", scope="role", text="Fluent English with our international team"):
    return {"kind": kind, "stance": stance, "value": value, "scope": scope, "text": text}


def test_eligibility_verdict_table():
    held = F.read_eligibility(PROFILE)
    nationality = {k: held[k] for k in ("citizenship", "security clearance")}      # no permit line, so no silence to flag
    assert F.eligibility_gate([item("citizenship", value="Dutch")], nationality).verdict == "PASS"
    assert F.eligibility_gate([item("citizenship", value="EU citizen")], F.read_eligibility("## Eligibility\n- Citizenship: Dutch, EU citizen\n")).verdict == "PASS"
    assert F.eligibility_gate([item("citizenship", value="US citizen")], held).verdict == "FAIL"
    assert F.eligibility_gate([item("security_clearance", value="SC clearance")], held).verdict == "FAIL"
    assert F.eligibility_gate([item("work_authorisation", value="EU citizen")], held).verdict == "PASS"
    assert F.eligibility_gate([item("work_authorisation", value="UK visa")], held).verdict == "FAIL"
    assert F.eligibility_gate([item("work_authorisation", "welcomes")], held).verdict == "PASS"
    assert F.eligibility_gate([item("citizenship", value="Dutch")], {}).verdict == "FLAG"     # the profile cannot answer


def test_silent_posting_marked_unverified():
    gate_ = F.eligibility_gate([], F.read_eligibility(PROFILE))
    assert gate_.verdict == "FLAG" and "silent" in gate_.notes[0] and "unverified" in gate_.notes[0]
    assert F.eligibility_gate([], F.read_eligibility("## Eligibility\n- Citizenship: Dutch\n")).verdict == "PASS"
    assert F.eligibility_gate([], F.read_eligibility("## Eligibility\n- Work authorisation: none\n")).verdict == "PASS"


def test_company_wide_statement_not_enough():
    held = F.read_eligibility(PROFILE)
    company = F.eligibility_gate([item("work_authorisation", "welcomes", scope="company")], held)
    assert company.verdict == "FLAG" and "not role-level" in company.notes[0]
    assert F.eligibility_gate([item("work_authorisation", "welcomes", scope="role")], held).verdict == "PASS"


def test_permit_timing_gate_separate():
    held = F.read_eligibility(PROFILE)
    hours = F.permit_gate([item("hours", value="45 hours per week")], held)
    assert hours.verdict == "FAIL" and "up to 40" in hours.notes[0]
    assert F.permit_gate([item("hours", value="40 per week")], held).verdict == "PASS"
    assert F.permit_gate([item("start_date", value="2026-02-01")], held).verdict == "FAIL"      # before the candidate can start
    assert F.permit_gate([item("start_date", value="2026-04-01")], held).verdict == "PASS"
    assert F.permit_gate([item("start_date", value="soon")], held).verdict == "FLAG"
    assert F.permit_gate([item("hours", value="40")], {}).verdict == "FLAG"
    assert F.eligibility_gate([item("hours", value="45")], held).verdict == "FLAG"             # hours never decide eligibility
    assert [g.name for g in ok().gates] == ["eligibility", "permit", "location", "language"]


def test_the_eligibility_item_shape_is_checked():
    assert "eligibility[0]" in refused(data(eligibility=[item("visa")]))
    assert "eligibility[0]" in refused(data(eligibility=[item("citizenship", "requires", value="")]))
    assert "eligibility[0]" in refused(data(eligibility=[item("citizenship", "silent")]))
    assert "posting's own visible words" in refused(data(eligibility=[item("citizenship", text="Dutch citizens only")]))


# ── language ──
def lang(language, level="", required=True):
    return {"language": language, "level": level, "required": required, "text": "x"}


def test_undeclared_language_fail_quotes_line():
    declared = F.read_languages(PROFILE)
    g = F.language_gate([{**lang("Russian", "fluent"), "text": "Fluent Russian required"}], declared)
    assert g.verdict == "FAIL" and '"Fluent Russian required"' in g.notes[0] and "does not list Russian" in g.notes[0]
    assert F.language_gate([lang("Russian", "", required=False)], declared).verdict == "FLAG"      # preferred: never a veto


def test_unlisted_required_language_is_a_fail_through_build():
    d = data()
    d["languages"] = [{"language": "Russian", "level": "fluent", "required": True, "text": "Fluent English with our international team"}]
    fit = ok(d)
    assert gate(fit, "language").verdict == "FAIL" and not fit.shortlisted
    assert F.read_languages("## Languages\n") == {}
    assert F.language_gate([lang("English", "A1")], {}).verdict == "FAIL"           # no table at all lists nothing


def test_flag_proceeds_and_quotes_both_levels():
    g = F.language_gate([{**lang("English", "fluent"), "text": "Fluent English wanted"}], F.read_languages(PROFILE))
    assert g.verdict == "FLAG" and "fluent" in g.notes[0] and "B1/B2" in g.notes[0] and "Fluent English wanted" in g.notes[0]
    fit = ok()
    assert gate(fit, "language").verdict == "FLAG" and fit.shortlisted and any(f.startswith("language:") for f in fit.flags)


def test_pass_for_level_at_or_below_or_unspecified():
    declared = F.read_languages(PROFILE)
    for level in ("", "unspecified", "A2", "B1", "conversational"):
        assert F.language_gate([lang("English", level)], declared).verdict == "PASS", level
    assert F.language_gate([lang("german", "A2")], declared).verdict == "PASS"        # names fold case
    assert F.language_gate([lang("German", "B1")], declared).verdict == "FLAG"
    assert F.language_gate([lang("English", "B2")], declared).verdict == "FLAG"          # a B1/B2 claim counts as B1
    assert F.language_gate([lang("Dutch", "native")], declared).verdict == "PASS"


def test_unsure_level_prefers_flag():
    declared = F.read_languages(PROFILE)
    assert F.language_gate([lang("English", "excellent communicator")], declared).verdict == "FLAG"    # fluent-class word
    assert F.language_gate([lang("English", "a feel for it")], declared).verdict == "FLAG"            # unreadable
    assert F.language_gate([lang("English", "B2")], {"english": "so-so"}).verdict == "FLAG"       # unreadable claim
    assert F.language_gate([lang("English", "B2")], {"english": ""}).verdict == "FLAG"
    assert F.level_rank("professional working proficiency", True) == F.CEFR["b2"]
    assert F.level_rank("Full professional proficiency", True) == F.CEFR["c1"]
    assert F.level_rank("Native or bilingual", True) == F.NATIVE
    assert F.level_rank("B2/C1", True) == F.CEFR["c1"] and F.level_rank("B1/B2", False) == F.CEFR["b1"]
    ranks = [F.CEFR[k] for k in ("a1", "a2", "b1", "b2", "c1", "c2")] + [F.NATIVE]
    assert ranks == sorted(ranks)


def test_language_gate_worked_example():
    declared = {"english": "B1", "dutch": "native"}
    verdicts = {name: F.language_gate([lang(*args)], declared).verdict for name, args in {
        "fluent russian": ("Russian", "fluent"), "fluent english": ("English", "fluent"),
        "conversational english": ("English", "conversational"), "english": ("English", "")}.items()}
    assert verdicts == {"fluent russian": "FAIL", "fluent english": "FLAG", "conversational english": "PASS", "english": "PASS"}


def test_ad_language_irrelevant_only_requirement_counts():
    german_ad = data(ad_language="German", languages=[])
    fit = ok(german_ad)
    assert gate(fit, "language").verdict == "PASS" and "ad_language" not in fit.as_dict()


def test_gate_result_persisted(tmp_path):
    d = data()
    d["languages"] = [{"language": "Russian", "level": "B2", "required": True, "text": "Fluent English with our international team"}]
    saved = json.loads(F.save(ok(d), tmp_path).read_text())
    language = next(g for g in saved["gates"] if g["name"] == "language")
    assert language["verdict"] == "FAIL" and "Fluent English" in language["notes"][0]
    assert saved["shortlisted"] is False and saved["vetoes"] and saved["rubric_version"] == F.RUBRIC_VERSION


def test_profile_readers_read_exactly_the_profile_format():
    assert F.read_languages(PROFILE) == {"english": "B1/B2", "dutch": "native", "german": "A2"}
    assert F.read_eligibility(PROFILE)["start date"] == "2026-03-01" and F.read_eligibility(PROFILE)["citizenship"] == "Dutch"
    assert F.read_deal_breakers(PROFILE) == ("more than 3 days a week on site", "on-call without compensation")
    assert F.onsite_limit(F.read_deal_breakers(PROFILE)) == 3 and F.onsite_limit(("no night shifts",)) is None
    assert F.read_languages("## Skills\n| Language | Level |\n| French | C1 |\n") == {}          # only the Languages section


# ── quotes and requirements ──
def test_a_forged_quote_is_rejected():
    d = data()
    d["requirements"][0]["quote"] = "Led a team of fifteen Python engineers"
    assert "quote must be profile.md's own words" in refused(d)
    d = data()
    d["requirements"][0]["quote"] = "Kafka"                                    # real words, but not about the term
    assert "containing 'Python'" in refused(d)


def test_a_not_yet_line_backs_nothing():
    d = data()
    d["requirements"][3] = {"term": "Scala", "kind": "required", "verdict": "backed", "quote": "Not yet: Scala, Spark"}
    assert "Not yet" in refused(d)
    d["requirements"][3]["quote"] = "Strong: Python, SQL, Apache Airflow, Kafka, BigQuery"
    assert "does not back 'Scala'" in refused(d)


def test_a_requirement_the_profile_backs_cannot_be_reported_as_a_gap_and_vice_versa():
    d = data()
    d["requirements"][0] = {"term": "Python", "kind": "required", "verdict": "gap", "quote": ""}
    assert "profile.md backs 'Python'" in refused(d)
    d = data()
    d["requirements"][3] = {"term": "Scala", "kind": "required", "verdict": "backed", "quote": "Wrote Python and SQL ingestion services"}
    assert "does not back 'Scala'" in refused(d)


def test_a_hidden_comment_requirement_is_not_a_requirement():
    d = data()
    d["requirements"].append({"term": "Haskell", "kind": "required", "verdict": "gap", "quote": ""})
    assert "Haskell" in POSTING and "HTML comments" in refused(d)


def test_terms_are_short_names_that_appear_in_the_posting():
    for term in ("Java", "production pipelines in Python and SQL", ""):
        d = data()
        d["requirements"].append({"term": term, "kind": "required", "verdict": "gap", "quote": ""})
        assert "term must be a short name" in refused(d) or "does not appear" in refused(d)
    d = data()
    d["requirements"][1]["term"] = "Pytho"                                     # a word fragment is not a term
    assert "does not appear" in refused(d)
    d = data()
    d["requirements"].append(d["requirements"][0])
    assert "listed twice" in refused(d)
    assert "requirements is empty" in refused(data(requirements=[]))


def test_strengths_and_gaps_are_kept_verbatim_and_bounded():
    d = data(strengths=["  Python and SQL pipelines at scale!  "], gaps=["Scala is listed under Not yet", "scala AGAIN"])
    fit = ok(d)
    assert fit.strengths == ("  Python and SQL pipelines at scale!  ",) and fit.gaps == tuple(d["gaps"])
    assert fit.as_dict()["strengths"] == list(fit.strengths)
    assert "strengths" in refused(data(strengths=["x" * 401 + " Python"]))
    assert "gaps" in refused(data(gaps=["Scala"] * 13))
    assert "names none of the gap requirements" in refused(data(gaps=["Weak at leadership"]))
    assert "names none of the backed requirements" in refused(data(strengths=["Scala expert"]))
    assert "strengths must be a list" in refused(data(strengths="Python"))


def test_gaps_always_present_poor_fit_scores_low():
    assert "gaps is empty but a requirement is a gap" in refused(data(gaps=[]))
    assert ok(data(requirements=[r for r in GOOD["requirements"] if r["verdict"] == "backed"], gaps=[])).gaps == ()   # nothing to state
    none_backed = [{"term": "Scala", "kind": "required", "verdict": "gap", "quote": ""}]
    assert "fundamental mismatch" in refused(data(requirements=none_backed, strengths=[], scores=scores(80, 70, 60, 76)))
    poor = ok(data(requirements=none_backed, strengths=[], scores=scores(30, 40, 50, 30)))
    assert poor.band == "weak" and poor.recommendation.startswith("Probably skip") and poor.gaps == ("Scala is listed under Not yet",)
    assert "technical is 0-39" in F.ROLE


def test_location_and_language_quotes_must_be_the_postings_visible_words():
    assert "location.text" in refused(data(location={**GOOD["location"], "text": "fully remote"}))
    hidden = POSTING + "\n<!-- Fluent Russian required -->\n"
    d = data(languages=[{"language": "Russian", "level": "", "required": True, "text": "Fluent Russian required"}])
    assert "posting's own visible words" in refused(d, posting=hidden)
    assert "location must be an object" in refused(data(location={"relocation_required": "no"}))


# ── persistence, rubric, rendering ──
def test_fit_score_is_integer_0_to_100():
    fit = ok()
    assert type(fit.total) is int and 0 <= fit.total <= 100 and type(fit.as_dict()["total"]) is int
    assert fit.total == F.score(GOOD["scores"])


def test_save_writes_json_and_keywords(tmp_path):
    fit = ok()
    path = F.save(fit, tmp_path / "fit")
    assert path == tmp_path / "fit" / "demo.json"
    assert json.loads(path.read_text()) == fit.as_dict()
    assert (tmp_path / "fit" / "demo.keywords").read_text().splitlines() == ["Python", "SQL", "Airflow", "Scala", "Kafka"]
    with pytest.raises(ValueError):
        F.Fit("../escape", 1, 1, 1, 1, fit.gates, fit.requirements, (), ())


def test_five_dimensions_defined():
    assert list(F.WEIGHTS) == ["technical", "experience", "behavioural", "career"] and set(F.RUBRIC) == set(F.WEIGHTS)
    assert [g for g in F.GATES] == ["eligibility", "permit", "location", "language"]


def test_technical_band_descriptions():
    for words in ("80-100", "60-79", "40-59", "0-39"):
        assert words in F.RUBRIC["technical"] and words in F.ROLE


def test_experience_matches_function_not_title():
    assert "never the literal title" in F.RUBRIC["experience"] and "never the literal title" in F.ROLE


def test_career_dimension_uses_motivation_and_life_situation():
    assert "motivation filter" in F.RUBRIC["career"] and "life situation" in F.RUBRIC["career"]


def test_framework_version_present(tmp_path):
    assert F.RUBRIC_VERSION >= 1 and json.loads(F.save(ok(), tmp_path).read_text())["rubric_version"] == F.RUBRIC_VERSION


def test_evaluation_output_sections():
    text = F.render(ok(data(eligibility=[item("citizenship", value="US citizen")])))
    for part in ("technical", "experience", "behavioural", "career", "VETO eligibility", "FLAG language", "Strengths:", "Gaps:",
                 "Recommendation:", "(VETOED)"):
        assert part in text


# ── the harness turn ──
def run(tmp_path, replies, **kw):
    server = FakeServer(replies)
    with Turns(Api("http://x", server.transport()), tmp_path / "log.jsonl") as turns:
        return F.evaluate(turns, "h/m", PROFILE, POSTING, **kw), server


def test_evaluate_sends_the_visible_posting_as_a_file_and_the_role_as_instructions(tmp_path):
    fit, server = run(tmp_path, [{"files": {"fit.json": json.dumps(GOOD)}}])
    body = server.requests[0]
    files = {p["filename"]: p["file_data"] for p in body["input"] if p["type"] == "input_file"}
    assert fit.key == "senior-data-engineer-brightwell-analytics-fictional-posting-for-tests"[:60].strip("-")
    assert "Haskell" not in files["posting.md"] and "Scala" in files["posting.md"] and files["profile.md"] == PROFILE
    assert "Haskell" not in json.dumps(body["input"][0]) and "untrusted" in body["instructions"]


def test_evaluate_sends_failures_back_verbatim_and_takes_the_fixed_file(tmp_path):
    forged = data()
    forged["requirements"][0]["quote"] = "Led fifteen engineers"
    fit, server = run(tmp_path, [{"files": {"fit.json": json.dumps(forged)}}, {"files": {"fit.json": json.dumps(GOOD)}}], key="k1")
    assert fit.key == "k1" and fit.requirements[0].quote == "Wrote Python and SQL ingestion services"
    assert server.requests[1]["previous_response_id"] == "resp_1"
    assert "quote must be profile.md's own words" in server.requests[1]["input"][0]["text"]


def test_evaluate_gives_none_when_the_file_never_passes(tmp_path):
    fit, server = run(tmp_path, [{}, {"files": {"fit.json": "not json"}}, {"files": {"fit.json": "[]"}}, {"files": {"fit.json": "{}"}}], rounds=3)
    assert fit is None and len(server.requests) == 4
    assert "fit.json must exist" in server.requests[1]["input"][0]["text"]
    assert "not valid JSON" in server.requests[2]["input"][0]["text"]


def test_main_saves_the_fit(tmp_path, monkeypatch):
    import argparse
    server = FakeServer([{"files": {"fit.json": json.dumps(GOOD)}}])
    monkeypatch.setattr(F, "Api", lambda url: Api(url, server.transport()))
    parser = argparse.ArgumentParser()
    F.add_arguments(parser)
    args = parser.parse_args(["--profile", str(FIX / "profile.md"), "--posting", str(FIX / "posting.md"),
                              "--model", "h", "--me", str(tmp_path), "--key", "demo"])
    assert F.HARNESS is True and F.main(args) == 0
    assert (tmp_path / "fit" / "demo.json").exists() and (tmp_path / "fit" / "demo.keywords").exists()


def test_language_fail_vetoes():
    d = data(scores=scores(90, 90, 90, 90))
    d["languages"] = [{"language": "Russian", "level": "", "required": True, "text": "Fluent English with our international team"}]
    fit = ok(d)
    assert not fit.shortlisted and fit.vetoes == tuple(v for v in fit.vetoes if v.startswith("language:"))
    assert "Fluent English with our international team" in fit.vetoes[0]

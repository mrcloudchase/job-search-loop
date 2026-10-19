"""style.py: the writing rules a drafted document must never break."""
from __future__ import annotations

from jobloop import style


def test_em_dash_and_double_hyphen_rejected():
    for bad in ("I built it \u2014 fast.", "I built it --- fast.", "I built it -- fast.", "I built it \u2013 fast.",
                "A pipeline--and a dashboard."):
        found = style.dashes(bad)
        assert found and "line 1" in found[0], bad


def test_ranges_and_hyphenated_words_are_not_dashes():
    assert style.dashes("2018--2022, 2018\u20132022, data-quality tests, step-by-step") == []


def test_no_emdash_in_cv_or_letter():
    letter = "Dear Hiring Manager,\nI know pipelines \u2014 and I own them.\nSincerely,\nAlex"
    found = style.check(letter)
    assert any("line 2" in f and "dash" in f for f in found)


def test_banned_phrases_flagged():
    found = style.banned_phrases("I am passionate about data and will hit the ground running to drive results.")
    quoted = " ".join(found)
    for phrase in ("passionate about", "hit the ground running", "drive results"):
        assert f'"{phrase}"' in quoted


def test_cliche_phrases_rejected():
    assert style.check("This is a great fit, and I will leverage my skills.")
    assert style.banned_phrases("synergies everywhere") != []


def test_banned_phrases_are_matched_in_the_declared_language_only():
    assert style.banned_phrases("Jeg brænder for data.", languages=("da",))
    assert style.banned_phrases("Jeg brænder for data.", languages=("en",)) == []
    assert style.banned_phrases("I am passionate about data.", languages=("da",)) == []


def test_the_banned_list_is_configurable():
    assert style.banned_phrases("we synergise", banned={"en": ("synergise",)})
    assert style.banned_phrases("I am passionate about it", banned={"en": ("synergise",)}) == []


def test_a_clean_text_has_no_findings():
    assert style.check("I own the shipment-events pipeline. It moves 40 million events per day.") == []


def test_buzzword_without_evidence_flagged():
    assert style.unbacked_claims("I am a team player.")
    assert style.unbacked_claims("Jeg er en teamplayer.", languages=("da",))


def test_buzzword_with_a_number_is_backed():
    assert style.unbacked_claims("As a team player I led 4 engineers to ship the mart.") == []


def test_hedging_phrases_flagged():
    found = style.hedging("I think I could contribute. Unfortunately I lack Scala.")
    assert len(found) == 2
    assert style.hedging("Jeg håber at bidrage.", languages=("da",))


def test_passive_voice_and_generic_trait_claims_flagged():
    assert style.tone("The candidate built the pipeline.")
    assert style.tone("The mart was rebuilt by me.")
    assert style.tone("I built the pipeline.") == []
    assert style.unbacked_claims("I am detail-oriented and hard-working.")


def test_ai_tooling_mentions_name_claude_code():
    assert style.ai_tooling("I use agentic coding every day.")
    assert style.ai_tooling("I use agentic coding with Claude Code every day.") == []
    assert style.ai_tooling("I mentor two engineers.") == []


def test_spelling_check_flags_typos():
    found = style.doubled_words("I built the the mart. They had had a plan.")
    assert len(found) == 1 and '"the"' in found[0]


def test_findings_quote_the_offending_text_and_the_line():
    found = style.banned_phrases("Line one.\nI am passionate about data.")
    assert found[0].startswith("line 2:") and "passionate about" in found[0]

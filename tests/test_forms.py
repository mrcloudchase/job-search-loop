"""forms.py: application-form answers are counted, grounded and style-checked by code, and never truncated."""
from __future__ import annotations

from pathlib import Path

import pytest

from jobloop import forms
from jobloop.forms import Question, check_answer, check_answers, count, load_questions

HERE = Path(__file__).parent / "fixtures"
PROFILE = (HERE / "profile" / "profile.md").read_text()
QUESTIONS = HERE / "edits" / "questions.json"
GOOD = {"intro": "I own the shipment-events pipeline at Tidewater Logistics, moving 40 million events per day from Kafka "
                 "into a PostgreSQL and BigQuery warehouse. I want to bring that to your data platform team.",
        "pitch": "I cut a nightly rebuild from 6 hours to 55 minutes.",
        "motivation": "I want to own a data platform end to end, and your team does that."}


def ask(answer, limit=None, unit="characters", gaps=()):
    return check_answer(Question("q", "Question", limit, unit), answer, [PROFILE], list(gaps))


def test_counts_with_and_without_spaces_and_words():
    size = count("one two\nthree")
    assert (size.characters, size.without_spaces, size.words) == (13, 11, 3)
    assert str(size) == "13 characters, 11 without spaces, 3 words"


def test_an_over_limit_answer_is_rejected_with_its_count_and_never_truncated():
    answer = "I cut a nightly rebuild from 6 hours to 55 minutes."
    found = ask(answer, limit=20)
    assert len(found) == 1 and f"is {len(answer)} characters, {len(answer) - 20} over the limit of 20" in found[0]
    assert "never truncates" in found[0]
    assert ask(answer, limit=len(answer)) == []


def test_the_limit_unit_decides_what_is_counted():
    answer = "I cut a nightly rebuild from 6 hours to 55 minutes."
    assert ask(answer, limit=41, unit="characters without spaces") == []
    assert ask(answer, limit=40, unit="characters without spaces")
    assert ask(answer, limit=11, unit="words") == []
    assert ask(answer, limit=10, unit="words")


def test_an_answer_with_a_number_that_the_profile_does_not_have_is_rejected():
    found = ask("I cut a nightly rebuild from 6 hours to 15 minutes.")
    assert len(found) == 1 and "number that is in none of the sources" in found[0]


def test_an_answer_with_an_invented_employer_role_is_rejected():
    found = ask("Senior Data Engineer, Initech Corp (Utrecht), March 2022 - present")
    assert found and any("Initech" in f or "employer" in f for f in found)


def test_an_answer_claiming_a_gap_is_rejected():
    found = ask("I tune Spark jobs.", gaps=["Spark"])
    assert len(found) == 1 and '"Spark"' in found[0]
    assert ask("I tune Airflow jobs.", gaps=["Spark"]) == []


def test_an_answer_with_an_em_dash_or_a_cliche_is_rejected():
    assert ask("I own the pipeline — end to end.")
    assert ask("I am passionate about data platforms.")


def test_the_answers_file_must_cover_exactly_the_questions():
    _, questions = load_questions(QUESTIONS)
    assert check_answers(questions, GOOD, [PROFILE], []) == []
    assert any("no answer for 'pitch'" in f for f in check_answers(questions, {k: v for k, v in GOOD.items() if k != "pitch"}, [PROFILE], []))
    assert any("'extra', which is not a question" in f for f in check_answers(questions, {**GOOD, "extra": "x"}, [PROFILE], []))
    assert check_answers(questions, ["not", "an object"], [PROFILE], []) and check_answers(questions, {"intro": 3}, [PROFILE], [])
    assert any("is empty" in f for f in check_answers(questions, {**GOOD, "intro": "  "}, [PROFILE], []))


def test_a_bad_question_file_or_unit_stops_before_any_turn(tmp_path):
    bad = tmp_path / "q.json"
    for body in ("not json", "{}", '{"employer": "X", "questions": []}',
                 '{"employer": "X", "questions": [{"id": "a", "question": "q"}, {"id": "a", "question": "r"}]}'):
        bad.write_text(body)
        with pytest.raises(SystemExit):
            load_questions(bad)
    bad.write_text('{"employer": "X", "questions": [{"id": "a", "question": "q", "unit": "pages"}]}')
    with pytest.raises(SystemExit):
        load_questions(bad)
    with pytest.raises(ValueError):
        Question("a", "q", limit=0)


def test_the_file_marks_its_notes_to_self_as_not_for_pasting_and_labels_every_answer():
    employer, questions = load_questions(QUESTIONS)
    text = forms.render(employer, questions, GOOD, PROFILE)
    assert text.startswith(f"APPLICATION FORM ANSWERS: {employer}") and "Nothing has been submitted" in text
    for q in questions:
        assert f"## {q.text}" in text and f"[{count(GOOD[q.id])}]" in text            # each field labelled with its counts
    assert "## Dates quick reference" in text
    note = text.index("## NOTE TO SELF (not for pasting)")
    assert note > text.index("## Dates quick reference") and all(g not in text[note:] for g in GOOD.values())   # no answer text after the note

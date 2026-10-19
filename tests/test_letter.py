"""letter.py: the cover letter's language, budget and structure."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from jobloop import letter
from jobloop.latex import to_text

FIX = Path(__file__).parent / "fixtures" / "writing"
EN, DA = (FIX / "letter_en.txt").read_text(), (FIX / "letter_da.txt").read_text()
POSTING_EN, POSTING_DA = (FIX / "posting_en.md").read_text(), (FIX / "posting_da.md").read_text()
TODAY = date(2026, 10, 19)


def with_body_words(n: int) -> str:
    return f"Dear Hiring Manager,\n{' '.join(['word'] * n)}\nSincerely,\nAlex Fernsby"


def test_letter_body_word_count_250_to_300():
    assert letter.check_budget(EN) == [] and letter.check_budget(DA) == []
    for n in (250, 300):
        assert letter.check_budget(with_body_words(n)) == [], n


def test_a_letter_over_or_under_budget_fails_with_the_overshoot():
    over = letter.check_budget(with_body_words(350))
    assert over and "350 words, 50 too many" in over[0]
    under = letter.check_budget(with_body_words(249))
    assert under and "1 too few" in under[0]
    assert letter.check_budget(with_body_words(301))


def test_address_block_salutation_signoff_and_signature_are_not_body():
    parts = letter.split(EN)
    assert parts.salutation == "Dear Hiring Manager,"
    assert "alex.fernsby@example.com" not in parts.body and "19 October" not in parts.body and "Alex Fernsby" not in parts.body and "Sincerely" not in parts.body
    assert parts.header.startswith("Alex Fernsby") and parts.closing == "Sincerely,\nAlex Fernsby"
    assert letter.words(parts.body) == 288
    padded = EN.replace("Alex Fernsby\nalex.", "Alex Fernsby " + "pad " * 80 + "\nalex.", 1)
    assert letter.check_budget(padded) == []


def test_bullet_markers_are_not_words_but_numbers_and_hyphenated_words_are():
    assert letter.words("- one two\n- three") == 3
    assert letter.words("40 million data-quality tests") == 4


def test_a_letter_without_salutation_or_signoff_cannot_pass():
    assert letter.check_budget("Just text " * 100)
    assert letter.check_budget("Dear Hiring Manager,\n" + "word " * 280)


def test_edit_does_not_net_grow_beyond_budget():
    edited = EN.replace("Thank you for your time and consideration.", "Thank you. " + "Extra words here. " * 6)
    found = letter.check_budget(edited)
    assert found and "too many" in found[0]


def test_letter_language_follows_posting_not_cv():
    assert letter.check_language(DA, POSTING_DA, cv_text=None) == []
    assert letter.check_language(EN, POSTING_EN) == []
    wrong = letter.check_language(EN, POSTING_DA)
    assert wrong and "letter is in en but the posting is in da" in wrong[0]
    cv_en = (Path(__file__).parent.parent / "fixtures" / "cv.txt").read_text()
    assert letter.check_language(DA, POSTING_DA, cv_text=cv_en) == []


def test_cv_language_from_profile_default_english():
    cv_en = (Path(__file__).parent.parent / "fixtures" / "cv.txt").read_text()
    assert letter.check_language(EN, POSTING_EN, cv_text=cv_en, cv_language=None) == []
    found = letter.check_language(EN, POSTING_EN, cv_text=DA, cv_language=None)
    assert any("CV is in da but the profile's CV language is en" in f for f in found)
    assert letter.check_language(DA, POSTING_DA, cv_text=DA, cv_language="da") == []


def test_the_posting_language_is_read_without_its_hidden_comments():
    assert letter.detect(POSTING_EN) in ("en", "da", None)
    assert letter.check_language(EN, POSTING_EN) == []


def test_undecidable_language_is_a_finding_not_a_pass():
    assert letter.detect("Hello there") is None
    found = letter.check_language("Hello there", POSTING_EN)
    assert any("letter's language cannot be decided" in f for f in found)
    assert any("posting's language cannot be decided" in f for f in letter.check_language(EN, "Tiny posting"))
    assert letter.detect("") is None


def test_mixed_language_text_is_undecidable():
    mixed = "the and of to in is that for with " + "og er det at en til på af med som"
    assert letter.detect(mixed) is None


def test_detector_tells_the_supported_languages_apart():
    assert letter.detect("Wir suchen einen Ingenieur, der die Daten mit uns auf einer Plattform für alle Teams ist nicht") == "de"
    assert letter.detect("Wij zoeken een ingenieur die het team naar een nieuw platform brengt en ook de data wordt gebruikt") == "nl"


def test_salutation_named_person_else_generic_in_language():
    assert letter.check_salutation(EN, None, "en") == []
    assert letter.check_salutation(DA, None, "da") == []
    named = EN.replace("Dear Hiring Manager,", "Dear Ms Jansen,")
    assert letter.check_salutation(named, "Ms Jansen", "en") == []
    assert letter.check_salutation(EN, "Ms Jansen", "en")


def test_a_salutation_never_invents_a_name():
    invented = EN.replace("Dear Hiring Manager,", "Dear Mr Smith,")
    found = letter.check_salutation(invented, None, "en")
    assert found and "never invent a name" in found[0]


def test_an_english_generic_salutation_in_a_danish_letter_fails():
    assert letter.check_salutation(DA.replace("Kære rekrutteringsansvarlig,", "Dear Hiring Manager,"), None, "da")


def test_local_closing_and_date_format():
    assert letter.check_closing(DA, "da") == [] and letter.check_closing(EN, "en") == []
    assert letter.check_closing(DA.replace("Med venlig hilsen", "Sincerely,"), "da")
    assert letter.check_date(DA, TODAY, "da") == [] and letter.check_date(EN, TODAY, "en") == []
    found = letter.check_date(DA.replace("19. oktober 2026", "19 October 2026"), TODAY, "da")
    assert found and "not da" in found[0]


def test_company_role_and_date_correct():
    assert letter.check_date(EN.replace("19 October 2026", "18 October 2026"), TODAY, "en")
    assert letter.check_date(EN.replace("19 October 2026\n", ""), TODAY, "en")
    assert letter.check_opening(EN, "Brightwell Analytics", "Senior Data Engineer") == []
    assert letter.check_opening(EN, "Other Corp", "Senior Data Engineer")[0].startswith('the opening paragraph does not name "Other Corp"')


def test_opening_names_role_and_company():
    generic = EN.replace("I am applying for the Senior Data Engineer role at Brightwell Analytics", "I am applying for a role")
    found = letter.check_opening(generic, "Brightwell Analytics", "Senior Data Engineer")
    assert len(found) == 2


def test_bullet_count_3_to_5_with_labels():
    assert letter.check_bullets(EN) == [] and letter.check_bullets(DA) == []
    two = "\n".join(l for l in EN.split("\n") if not l.startswith(("- Faster", "- Growing")))
    assert any("2 bullets" in f for f in letter.check_bullets(two))
    six = EN.replace("- Growing", "- A\n- B\n- C\n- Growing")
    assert any("7 bullets" in f for f in letter.check_bullets(six))


def test_bullet_count_and_variety():
    same = EN.replace("- Faster models", "- Pipelines at scale 2").replace("- Quality you", "- Pipelines other")
    found = letter.check_bullets(same)
    assert any('same word "pipelines"' in f for f in found)


def test_letter_addresses_posting_logistics():
    assert letter.check_reference(EN, "BW-DE-0417") == []
    assert letter.check_reference(EN, "ZZ-9") == ['the letter does not quote the posting\'s reference "ZZ-9"']
    assert letter.check_reference(EN, None) == []


def test_generic_headline_rejected():
    assert letter.check_headline(EN) == []
    generic = EN.replace("Data pipelines that forecasters can trust: dbt, Airflow and data-quality testing",
                         "Application for Senior Data Engineer")
    assert letter.check_headline(generic)
    assert letter.check_headline(DA.replace("Datastrømme, som prognoserne kan stole på: dbt, Airflow og test af datakvalitet",
                                           "Ansøgning til stilling som Senior Data Engineer"))


def test_a_tex_letter_is_checked_through_its_text():
    tex = ("\\begin{document}\n\\textbf{Alex}\\\\\n19 October 2026\\\\\nDear Hiring Manager,\n\n"
           + "word " * 270 + "\n\nSincerely,\\\\\nAlex Fernsby\n\\end{document}")
    assert letter.check_budget(to_text(tex)) == []


def test_the_whole_letter_check_passes_a_good_letter_and_collects_every_failure_of_a_bad_one():
    kw = dict(posting=POSTING_EN, company="Brightwell Analytics", role="Senior Data Engineer", today=TODAY,
              reference="BW-DE-0417")
    assert letter.check(EN, **kw) == []
    assert letter.check(DA, posting=POSTING_DA, company="Nordlys Analyse", role="Senior Data Engineer", today=TODAY,
                        reference="NL-DE-0231") == []
    bad = letter.check(DA, **kw)
    assert len(bad) >= 3
    assert any("posting is in en" in f for f in bad)

"""grounding.py: nothing in a drafted document that the sources do not say."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from jobloop import grounding

ROOT = Path(__file__).parent.parent
PROFILE = (ROOT / "fixtures" / "profile.md").read_text()
CV = (ROOT / "fixtures" / "cv.txt").read_text()
TEX = (Path(__file__).parent / "fixtures" / "writing" / "cv_ok.tex").read_text()


def check(doc, *extra, today=None):
    return grounding.check(doc, [PROFILE, *extra], today)


def test_the_fixture_cv_is_fully_grounded():
    assert check(CV) == []


def test_a_tex_cv_is_grounded_through_its_text():
    assert check(TEX) == []


def test_number_or_year_not_in_sources_blocked():
    found = check(CV.replace("12 analysts", "17 analysts").replace("2016\nBSc", "2015\nBSc"))
    assert any('"17 analysts' in f.replace("\n", " ") or "17" in f for f in found)
    assert any("2015" in f and "year" in f for f in found)


def test_an_invented_percentage_is_ungrounded_even_in_a_tex_cv():
    found = check(TEX.replace("70\\%", "80\\%"))
    assert len(found) == 1 and "80" in found[0] and "number" in found[0]


def test_a_number_from_the_posting_is_not_a_source_unless_passed():
    letter = "I have 5+ years of pipeline experience."
    assert check(letter) and "5" in check(letter)[0]
    assert check(letter, "Requirements: 5+ years building pipelines") == []


def test_thousands_separators_and_decimal_commas_are_one_number():
    assert grounding.check("Handled 2,000,000 rows.", ["2000000 rows a day"]) == []
    assert grounding.check("Saved 2,5 hours.", ["Saved 2.5 hours"]) == []


def test_dates_written_differently_are_one_date():
    sources = ["Worked there from March 2022."]
    for written in ("03/2022", "March 2022", "2022-03", "mar 2022", "marts 2022", "3.2022"):
        assert grounding.check(f"Joined {written}.", sources) == [], written
    assert grounding.check("Joined 04/2022.", sources)


def test_a_month_not_in_the_sources_is_blocked_even_when_the_year_is():
    found = check("Graduated June 2016.")
    assert found and "date" in found[0]


def test_a_year_alone_is_grounded_by_any_source_year():
    assert check("Graduated 2016.") == []
    assert check("Graduated 2012.")


def test_email_not_in_sources_blocked():
    found = check("Contact alex.fernsby@elsewhere.com")
    assert found and "email" in found[0]
    assert check("Contact ALEX.FERNSBY@example.com") == []


def test_phone_formats_are_compared_by_digits():
    assert check("Phone +1 555 0100") == []
    assert check("Phone +1-555-0100") == []
    found = check("Phone +1 555 0199")
    assert found and "phone" in found[0]


def test_links_compared_without_scheme_and_with_a_path_only_when_listed():
    assert check("Code: https://github.com/afernsby-example") == []
    assert check("Code: https://www.github.com/afernsby-example/") == []
    assert check("Code: https://github.com/other-person")
    assert check("Code: https://github.com/afernsby-example/secret-repo")
    assert check("Code: https://github.com/afernsby-example", "https://github.com/afernsby-example/pipelines") == []


def test_a_link_inside_an_href_is_checked():
    found = check(r"\begin{document}\href{https://evil.example.org/x}{my work}\end{document}")
    assert found and "link" in found[0]


def test_titles_dates_companies_match_profile():
    assert check("Senior Data Engineer - Tidewater Logistics, Rotterdam - March 2022 to present") == []
    invented_employer = check("Senior Data Engineer - Tidewater Group, Rotterdam - March 2022 to present")
    assert invented_employer and "tidewater group" in invented_employer[0]
    wrong_title = check("Principal Data Engineer - Tidewater Logistics, Rotterdam - March 2022 to present")
    assert any("principal data engineer" in f for f in wrong_title)
    swapped = check("Data Engineer - Tidewater Logistics, Rotterdam - March 2022 to present")
    assert any('title at Tidewater Logistics in the sources is "Senior Data Engineer"' in f for f in swapped)


def test_tenure_dates_never_shortened_or_padded():
    shortened = check("Senior Data Engineer - Tidewater Logistics, Rotterdam - June 2022 to present")
    assert shortened and "exactly the profile's" in shortened[0]
    padded = check("Data Engineer - Marlow Retail Group, Utrecht - June 2017 to February 2022")
    assert any("exactly the profile's" in f for f in padded)
    closed = check("Senior Data Engineer - Tidewater Logistics, Rotterdam - March 2022 to December 2025")
    assert any("exactly the profile's" in f for f in closed)


def test_bare_single_year_role_date_flagged():
    found = check("Junior Analyst - Delft Energy Cooperative, Delft - 2016")
    assert any("exactly the profile's" in f for f in found)


def test_a_role_date_with_a_year_dropped_is_flagged():
    found = check("Data Engineer - Marlow Retail Group, Utrecht - June 2018 to present")
    assert any("exactly the profile's" in f for f in found)


def test_a_role_line_must_carry_its_employer():
    assert any("names no employer" in f for f in check("Senior Data Engineer\nMarch 2022 to present"))
    found = check("Senior Data Engineer, March 2022 to present")
    assert any("names no employer" in f for f in found)


def test_a_pdf_role_heading_may_put_its_employer_on_the_line_below():
    heading = "Senior Data Engineer          March 2022 - present\nTidewater Logistics, Rotterdam"
    assert check(heading) == []
    assert any("exactly the profile's" in f for f in check(heading.replace("March 2022", "March 2021")))
    far = "Senior Data Engineer          March 2022 - present\n- Owns the pipeline of Tidewater Logistics shipment events every single day of the week"
    assert any("names no employer" in f for f in check(far))


def test_a_link_a_pdf_wrapped_at_its_hyphen_is_grounded_but_a_stranger_is_not():
    sources = ["Package: github.com/afernsby-example/freight-tests"]
    assert check("see github.com/afernsby-example/freight- tests", *sources) == []
    assert check("see github.com/other-person/freight- tests", *sources)
    assert check("see github.com/a-", *sources)


def test_a_pdf_heading_may_spread_title_employer_and_city_over_three_lines_or_columns():
    above = "Senior Data Engineer\nTidewater Logistics     March 2022 - present     Rotterdam"
    assert check(above) == []
    assert any("exactly the profile's" in f for f in check(above.replace("March 2022", "March 2020")))
    invented = "Principal Architect\nTidewater Logistics     March 2022 - present"
    assert any("Principal Architect" in f or "title" in f for f in check(invented))
    assert check("Professional Experience\nTidewater Logistics     March 2022 - present\nSenior Data Engineer") == []


def test_a_city_and_a_title_set_side_by_side_are_known_words_but_an_invented_title_is_not():
    assert check("Rotterdam Senior Data Engineer | Tidewater Logistics March 2022 - present") == []
    assert check("Rotterdam Chief Wizard | Tidewater Logistics March 2022 - present")


def test_a_letter_may_carry_today_and_nothing_else_new():
    letter = "19 October 2026\nDear Hiring Manager,"
    assert check(letter, today=date(2026, 10, 19)) == []
    assert check(letter)
    assert check("20 October 2026", today=date(2026, 10, 19))


def test_grounding_uses_only_the_sources_it_is_given():
    assert check("Led 99 engineers.")
    assert check("Led 99 engineers.", "Led 99 engineers at Tidewater Logistics.") == []


def test_a_not_yet_line_backs_nothing():
    found = grounding.check("I use 3 clusters.", ["Not yet: 3 clusters of Kubernetes"])
    assert found and "number" in found[0]


def test_findings_quote_the_text_and_say_the_line():
    found = check("fine\nI saved 77 hours")
    assert found[0].startswith("line 2:") and "77" in found[0]


def test_dates_parser_reads_every_notation_and_names_the_month_word():
    got = grounding.dates("03/2022 2022-03 19 October 2026 marts 2022 19.10.2026 Mar 2016 October 19, 2026 2016")
    assert [(f.year, f.month, f.day) for f in got] == [
        (2022, 3, None), (2022, 3, None), (2026, 10, 19), (2022, 3, None), (2026, 10, 19), (2016, 3, None),
        (2026, 10, 19), (2016, None, None)]
    assert got[2].word == "october" and got[3].word == "marts"


def test_roles_of_the_profile_are_parsed():
    r = grounding.roles(PROFILE)
    assert [x.employer for x in r] == ["Tidewater Logistics", "Marlow Retail Group", "Delft Energy Cooperative"]
    assert r[0].end is None and r[1].start == (2018, 6) and r[1].end == (2022, 2)


def test_a_fact_is_grounded_if_any_one_source_supports_it_and_nothing_else_does():
    master = "Led a team of 14 engineers in 2019."
    assert check("Led 14 engineers in 2019.") != []                          # not in the profile alone
    assert check("Led 14 engineers in 2019.", master) == []                  # the union of the sources backs it
    assert check("Led 15 engineers in 2019.", master)                        # an escalated number is in none of them
    assert check("Led 14 engineers in 2011.", master)                        # a changed date neither

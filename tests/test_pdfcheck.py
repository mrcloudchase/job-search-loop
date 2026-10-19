"""The ATS text-layer checks, over hand-built text and the real pdftotext/pdfinfo output of a small pdflatex CV."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jobloop import layout, pdfcheck
from jobloop.pdfcheck import (
    FAILED, UNVERIFIED, VERIFIED, PdfInfoError, TextLayerSpec, Verdict, check_ascii_dates, check_contact_literal,
    check_date_pairs, check_min_chars, check_page_count, check_reading_order, check_required_text, find_garbled_text,
    find_non_ascii_date_ranges, normalize_for_compare, parse_page_count, verify_cv_text_layer,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pdfcheck"
EN, EM, MINUS = "–", "—", "−"

GOOD_CV = """\
   Jane Example
jane@example.test   +45 12 34 56 78

Experience
Platform Engineer, Acme A/S                              2019-2024
  - Built the deployment pipeline for twelve services.
Developer, Beta ApS                                      2016-2019

Education
MSc Computer Science, Example University                 2014-2016

Skills
Python, Go, Kubernetes
"""
SPEC = TextLayerSpec(
    pages=2, name="Jane Example", email="jane@example.test", phone="+45 12 34 56 78",
    headings=("Experience", "Education", "Skills"), min_chars=50,
)
TWO_PAGES = "Pages:           2\n"


def checks(findings):
    return {f.check for f in findings}


# --- page count -------------------------------------------------------------------------------------------------

def test_page_count_is_read_from_pdfinfo():
    assert parse_page_count((FIXTURES / "cv.pdfinfo.txt").read_text()) == 2


def test_page_count_missing_is_error():
    with pytest.raises(PdfInfoError, match="no 'Pages: N' line"):
        parse_page_count("Title:  x\nPage size: A4\n")


def test_zero_pages_is_error_not_a_count():
    with pytest.raises(PdfInfoError):
        parse_page_count("Pages:           0\n")


def test_wrong_page_count_rejected():
    (finding,) = check_page_count("Pages: 3\n", 2)
    assert "3 page(s)" in finding.message and "exactly 2" in finding.message


def test_page_count_is_an_equality_not_a_maximum():
    assert check_page_count("Pages: 1\n", 2) != []
    assert check_page_count("Pages: 2\n", 2) == []


def test_unreadable_page_count_is_a_finding_not_a_guess():
    assert check_page_count("garbage", 2)[0].check == "page-count"


def test_custom_template_page_limit_overrides_default():
    assert check_page_count("Pages: 3\n", 3) == []
    spec = TextLayerSpec(3, "Jane Example", "jane@example.test", "+45 12 34 56 78", ("Experience",), min_chars=10)
    assert verify_cv_text_layer("Pages: 3\n", GOOD_CV, spec).verified


def test_page_count_has_one_implementation():
    assert hasattr(pdfcheck, "parse_page_count")
    assert not any("page_count" in name for name in dir(layout))


def test_letter_skips_ats_text_checks_but_keeps_page_and_layout():
    garbled_letter = "(cid:12) � no contact, no headings"
    assert check_page_count("Pages: 1\n", 1) == []
    assert check_page_count("Pages: 2\n", 1) != []
    assert find_garbled_text(garbled_letter) != []  # the CV gate would fail this text ...
    # ... but the letter's gate is page count plus layout: neither takes the text layer.
    assert layout.verify_layout(None).status == UNVERIFIED


# --- folding ----------------------------------------------------------------------------------------------------

def test_whitespace_collapsed():
    assert normalize_for_compare("  a \n\t b  c  ") == "a b c"


def test_decomposed_accents_fold_to_nfc():
    assert normalize_for_compare("Genève") == normalize_for_compare("Genève") == "Genève"


def test_accented_keyword_matches_precomposed():
    layer = "Based in Genève, Switzerland"
    assert check_required_text(layer, ("Genève",)) == []


def test_typographic_fold_table():
    folded = normalize_for_compare("‘a’ “b” x–y x—y x−y x‐y‑z‒ ―")
    assert folded == "'a' \"b\" x-y x-y x-y x-y-z- -"


def test_ligatures_and_soft_hyphens_fold():
    assert normalize_for_compare("oﬃce ﬁnance fi­nance") == "office finance finance"


def test_fold_is_symmetric():
    assert check_required_text("Master's degree, 2016-2024", ("Master’s degree", "2016–2024")) == []
    assert check_required_text("Master’s degree, 2016–2024", ("Master's degree", "2016-2024")) == []


def test_contains_matches_latex_substitutions():
    layer = "Master’s degree 2016–2024"
    assert check_required_text(layer, ("Master's degree", "2016-2024")) == []


def test_contains_folds_nfc_whitespace_and_typography():
    layer = "Master’s\n  degree in Genève"
    assert check_required_text(layer, ("Master's degree in Genève",)) == []


def test_contains_is_case_sensitive_substring():
    assert check_required_text("Kubernetes", ("kubernetes",)) != []
    assert check_required_text("Kubernetes platform", ("Kubernetes",)) == []
    assert check_required_text("Kuber netes", ("Kubernetes",)) != []


def test_missing_required_text_rejected():
    (finding,) = check_required_text(GOOD_CV, ("Acme A/S", "Gamma Oy"))
    assert "'Gamma Oy'" in finding.message


def test_source_text_missing_from_layer_fails():
    verdict = verify_cv_text_layer(TWO_PAGES, GOOD_CV, TextLayerSpec(**{**SPEC.__dict__, "required": ("Delta GmbH",)}))
    assert verdict.status == FAILED and "Delta GmbH" in verdict.feedback()


# --- text size and garbling -------------------------------------------------------------------------------------

def test_too_little_text_rejected():
    (finding,) = check_min_chars("short", 100)
    assert "5 character(s)" in finding.message and "100" in finding.message


def test_an_all_whitespace_layer_fails_even_with_minimum_zero():
    assert check_min_chars(" \n\t   ", 0) != []


def test_cid_marker_in_layer_fails():
    (finding,) = find_garbled_text("Jane (cid:123) Example")
    assert "(cid:123)" in finding.message and finding.check == "garbled-text"


def test_replacement_character_in_layer_fails():
    (finding,) = find_garbled_text("Gen�ve")
    assert "U+FFFD" in finding.message


def test_clean_text_has_no_garbling_findings():
    assert find_garbled_text(GOOD_CV) == []


def test_no_cid_no_replacement_char_text_present():
    spec = SPEC
    for broken in ("(cid:7)", "�"):
        verdict = verify_cv_text_layer(TWO_PAGES, GOOD_CV + broken, spec)
        assert verdict.status == FAILED and "garbled-text" in verdict.feedback()
    assert verify_cv_text_layer(TWO_PAGES, "", spec).status == FAILED


# --- contact ----------------------------------------------------------------------------------------------------

def test_email_and_phone_must_be_literal_text():
    assert check_contact_literal(GOOD_CV, "jane@example.test", "+45 12 34 56 78") == []
    icon_only = "Jane Example\nEnvelope MOBILE-ALT\nExperience"
    found = check_contact_literal(icon_only, "jane@example.test", "+45 12 34 56 78")
    assert len(found) == 2 and "email" in found[0].message and "phone" in found[1].message


def test_email_and_phone_literal_in_text_layer():
    verdict = verify_cv_text_layer(TWO_PAGES, GOOD_CV.replace("jane@example.test", "Envelope"), SPEC)
    assert verdict.status == FAILED and "email" in verdict.feedback()


def test_phone_matches_on_digits_whatever_the_separators():
    assert check_contact_literal("x@y.z 45-12-34-56-78", "x@y.z", "+45 12 34 56 78") == []
    assert check_contact_literal("x@y.z (+45) 12 34 56 78", "x@y.z", "+45 12 34 56 78") == []


def test_a_phone_split_across_lines_is_not_literal():
    assert check_contact_literal("x@y.z +45 12\n34 56 78", "x@y.z", "+45 12 34 56 78") != []


def test_a_different_number_is_not_the_phone():
    assert check_contact_literal("x@y.z +45 12 34 56 79", "x@y.z", "+45 12 34 56 78") != []


def test_an_email_split_by_a_line_break_is_not_literal():
    assert check_contact_literal("jane@example\n.test +45 12 34 56 78", "jane@example.test", "+45 12 34 56 78") != []


# --- reading order ----------------------------------------------------------------------------------------------

def test_section_headings_appear_in_expected_sequence():
    assert check_reading_order(GOOD_CV, "Jane Example", ("Experience", "Education", "Skills")) == []
    (finding,) = check_reading_order(GOOD_CV, "Jane Example", ("Skills", "Education"))
    assert "'Education' is extracted before 'Skills'" in finding.message


def test_headings_match_whatever_their_case_and_trailing_colon():
    assert check_reading_order("Jane Example\nEXPERIENCE:\nx\nEDUCATION\n", "Jane Example", ("Experience", "Education")) == []


def test_multicolumn_template_scramble_is_reported():
    scrambled = "Jane Example\nEducation      Experience\nMSc   Platform Engineer\nSkills\n"
    found = check_reading_order(scrambled, "Jane Example", ("Experience", "Education", "Skills"))
    assert [f.check for f in found] == ["reading-order", "reading-order"]
    assert "multi-column" in found[0].message and "'Experience'" in found[0].message


def test_the_name_must_come_before_the_first_heading():
    late = "Experience\nx\nJane Example\nEducation\n"
    (finding,) = check_reading_order(late, "Jane Example", ("Experience", "Education"))
    assert "open with the name" in finding.message


def test_lines_of_a_later_section_before_an_earlier_heading_are_out_of_order():
    interleaved = "Jane Example\nSkills\nPython\nExperience\nAcme\n"
    assert check_reading_order(interleaved, "Jane Example", ("Experience", "Skills")) != []


# --- ASCII date ranges ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("dash", ["‐", "‑", "‒", EN, EM, "―", MINUS])
def test_each_unicode_dash_and_minus_is_caught(dash):
    (hit,) = find_non_ascii_date_ranges(f"Acme 2016 {dash} 2024")
    assert hit.dash == dash


def test_ascii_hyphen_ranges_are_clean():
    assert find_non_ascii_date_ranges("2016-2024\nMar 2016 - Jul 2016\n2016 - Present") == []


@pytest.mark.parametrize("line", [f"Mar 2016 {EN} Jul 2016", f"2016 {EN} Present", f"{EN} 2024", f"Jan {EN} Mar 2016"])
def test_year_on_one_side_is_enough(line):
    assert len(find_non_ascii_date_ranges(line)) == 1


def test_year_like_digits_inside_longer_number_are_not_years():
    assert find_non_ascii_date_ranges(f"users 120000{EN}130000\nid 12000{EN}x") == []


def test_no_match_across_newline_either_direction():
    assert find_non_ascii_date_ranges(f"since 2016\n{EN} led the team") == []
    assert find_non_ascii_date_ranges(f"led the team {EN}\n2016 onwards") == []


def test_same_line_spaces_still_caught():
    assert len(find_non_ascii_date_ranges(f"1988 {EN} 1994")) == 1


@pytest.mark.parametrize("line", [
    f"EUR 600k{EN}1M", f"EUR 1500{EN}2000", f"2000{EN}5000 requests/s", f"1.5{EN}2.0M", f"20{EN}25", f"1,234.56{EN}2.0M",
])
def test_quantity_ranges_are_not_dates(line):
    assert find_non_ascii_date_ranges(line) == []


@pytest.mark.parametrize("line", [f"2016{EN}2024", f"1990{EN}2010 programme", f"Roles 2000{EN}2024"])
def test_two_bare_years_are_a_date(line):
    assert len(find_non_ascii_date_ranges(line)) == 1


@pytest.mark.parametrize("line", [
    f"03/2016{EN}07/2024", f"01.2016 {EN} 12.2024", f"2016-03{EN}2024-07", f"2016/17{EN}2018/19",
    f"2016/03{EN}2024/07", f"01.03.2016{EN}31.07.2024", f"2016{EN}07/2024",
])
def test_composite_numeric_dates_still_caught(line):
    assert len(find_non_ascii_date_ranges(line)) == 1


def test_bare_number_rules_with_year_groups_and_outer_punctuation():
    bare = pdfcheck._bare_number
    assert bare("1500") and bare("600k") and bare("1,234.56") and bare("2.0M") and bare("(20%)") and bare('"2016",')
    assert not bare("01.2016") and not bare("31.07.2024") and not bare("03/2016") and not bare("2016-03")
    assert not bare("EUR") and not bare("")


def test_standalone_year_beside_long_number_caught():
    assert len(find_non_ascii_date_ranges(f"2016{EN}2024 (120000 users)")) == 1


def test_hits_in_document_order_one_per_range():
    text = f"A 2016{EN}2018\nplain 2019-2020\nB  2020 {EM}  Present"
    hits = find_non_ascii_date_ranges(text)
    assert [(h.line, h.dash) for h in hits] == [(f"A 2016{EN}2018", EN), (f"B 2020 {EM} Present", EM)]


def test_ascii_dates_error_names_code_point_and_line():
    (finding,) = check_ascii_dates(f"Acme  2016{EN}2024\n" + "x" * 5)
    assert "U+2013 in 'Acme 2016–2024'" in finding.message and "1 date range" in finding.message
    assert "ASCII hyphen" in finding.message and "'--'" in finding.message


def test_ascii_dates_flags_unicode_dash_between_years():
    verdict = verify_cv_text_layer(TWO_PAGES, GOOD_CV.replace("2016-2019", f"2016{EN}2019"), SPEC)
    assert verdict.status == FAILED and "U+2013" in verdict.feedback()


def test_same_layer_passes_contains_but_fails_ascii_dates():
    layer = GOOD_CV.replace("2016-2019", f"2016{EN}2019")
    assert check_required_text(layer, ("2016-2019",)) == []  # folded comparison cannot see the defect
    assert check_ascii_dates(layer) != []  # the raw-layer check does


def test_ascii_dates_reads_raw_layer_not_folded():
    assert check_ascii_dates(normalize_for_compare(f"2016{EN}2024")) == []
    assert check_ascii_dates(f"2016{EN}2024") != []


def test_cv_gate_always_runs_ascii_dates():
    minimal = TextLayerSpec(1, "Jane Example", "jane@example.test", "+45 12 34 56 78", ("Experience",), min_chars=1)
    layer = GOOD_CV.replace("2019-2024", f"2019{EM}2024")
    verdict = verify_cv_text_layer("Pages: 1\n", layer, minimal)
    assert verdict.status == FAILED and "ascii-dates" in verdict.feedback()


def test_entries_with_a_single_year_flagged():
    (finding,) = check_date_pairs("Platform Engineer, Acme A/S      2019")
    assert "single year" in finding.message and "2019-2024" in finding.message


@pytest.mark.parametrize("line", ["Engineer, Acme   2019 -", "Engineer, Acme   Mar 2019", f"Engineer   2019 {EN}", "2019"])
def test_a_start_with_no_end_is_flagged(line):
    assert len(check_date_pairs(line)) == 1


@pytest.mark.parametrize("line", [
    "Engineer, Acme   2019-2024", "Engineer, Acme   2019 - Present", "Built 12 services in 2019 for Acme",
    "Engineer   03/2019 - 07/2024", "Phone +45 2019 1234",
])
def test_each_entry_has_start_and_end(line):
    assert check_date_pairs(line) == []


def test_every_entry_has_start_and_end_date():
    verdict = verify_cv_text_layer(TWO_PAGES, GOOD_CV.replace("2014-2016", "2016"), SPEC)
    assert verdict.status == FAILED and "date-pairs" in verdict.feedback()


# --- verdicts, extractor, unverified ----------------------------------------------------------------------------

def test_a_good_cv_is_verified():
    verdict = verify_cv_text_layer(TWO_PAGES, GOOD_CV, SPEC)
    assert verdict.status == VERIFIED and verdict.verified and verdict.findings == ()


def test_result_and_errors_name_the_extractor():
    ok = verify_cv_text_layer(TWO_PAGES, GOOD_CV, SPEC, extractor="pdftotext 22.12.0")
    assert ok.extractor == "pdftotext 22.12.0"
    bad = verify_cv_text_layer("Pages: 5\n", GOOD_CV, SPEC, extractor="pdftotext 22.12.0")
    assert bad.feedback().startswith("[page-count (pdftotext 22.12.0)]")
    assert all("(pdftotext 22.12.0)" in line for line in bad.feedback().splitlines())


def test_no_extractor_yields_unverified_status_not_pass():
    verdict = verify_cv_text_layer(TWO_PAGES, None, SPEC)
    assert verdict.status == UNVERIFIED and not verdict.verified
    assert "unverified" in verdict.feedback() and "waiver" in verdict.feedback()


def test_missing_extractors_fail_closed_with_install_hint():
    verdict = verify_cv_text_layer(None, None, SPEC)
    assert not verdict.verified and "poppler-utils" in verdict.feedback()


def test_missing_pdfinfo_is_unverified_not_pass():
    verdict = verify_cv_text_layer(None, GOOD_CV, SPEC)
    assert verdict.status == UNVERIFIED and "page count" in verdict.feedback()


def test_an_unverified_verdict_is_never_confused_with_a_pass():
    assert Verdict(UNVERIFIED, (pdfcheck.Finding("x", "y"),)).verified is False
    with pytest.raises(ValueError):
        Verdict(VERIFIED, (pdfcheck.Finding("x", "y"),))
    with pytest.raises(ValueError):
        Verdict(FAILED)
    with pytest.raises(ValueError):
        Verdict("passed")


def test_the_spec_refuses_missing_inputs():
    with pytest.raises(ValueError):
        TextLayerSpec(2, "", "a@b.c", "1", ("Experience",))
    with pytest.raises(ValueError):
        TextLayerSpec(2, "Jane", "a@b.c", "1", ())


def test_findings_read_as_instructions_to_a_drafter():
    verdict = verify_cv_text_layer("Pages: 3\n", GOOD_CV.replace("jane@example.test", ""), SPEC)
    assert verdict.status == FAILED
    assert {f.check for f in verdict.findings} == {"page-count", "contact"}


def test_text_argv_pins_layout_and_utf8_flags():
    assert pdfcheck.TEXT_ARGV == ("pdftotext", "-layout", "-enc", "UTF-8")
    assert pdfcheck.PDFINFO_ARGV == ("pdfinfo",)


# --- real tool output -------------------------------------------------------------------------------------------

REAL_SPEC = TextLayerSpec(
    pages=2, name="Jane Example", email="jane@example.test", phone="+45 12 34 56 78",
    headings=("Experience", "Education", "Skills"), min_chars=100,
)


def test_real_pdftotext_output_is_checked():
    verdict = verify_cv_text_layer((FIXTURES / "cv.pdfinfo.txt").read_text(), (FIXTURES / "cv.layout.txt").read_text(), REAL_SPEC)
    assert verdict.status == FAILED
    assert checks(verdict.findings) == {"ascii-dates"}  # the fixture's '2016--2019' is a real en dash
    assert "U+2013 in 'Developer, Beta ApS 2016–2019'" in verdict.feedback()


def test_real_soft_hyphen_in_source_does_not_break_the_word():
    assert "The office of finance." in (FIXTURES / "cv.layout.txt").read_text()


@pytest.mark.docker
def test_real_tools_in_the_check_container(tmp_path):
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    pdf_dir = tmp_path / "in"
    pdf_dir.mkdir()
    shutil.copy(FIXTURES / "cv.pdf", pdf_dir / "cv.pdf")
    script = " && ".join([
        f"{' '.join(pdfcheck.PDFINFO_ARGV)} /in/cv.pdf > /out/info.txt",
        f"{' '.join(pdfcheck.TEXT_ARGV)} /in/cv.pdf /out/text.txt",
    ])
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o777)
    subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "-v", f"{pdf_dir}:/in:ro", "-v", f"{out}:/out",
         "job-search-check", "sh", "-c", script],
        check=True, timeout=120,
    )
    verdict = verify_cv_text_layer((out / "info.txt").read_text(), (out / "text.txt").read_text(encoding="utf-8"), REAL_SPEC)
    assert checks(verdict.findings) == {"ascii-dates"}
    assert parse_page_count((out / "info.txt").read_text()) == 2

"""The layout checks, over hand-built -bbox-layout XHTML (A4 unless a test says otherwise) and one real pdflatex CV."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from jobloop import layout
from jobloop.layout import (
    LayoutProfile, LayoutUnavailable, bottom_space, check_layout, find_orphans, format_measurements, largest_gap,
    measure, parse_bbox_layout, skipped, text_x, verify_layout,
)
from jobloop.pdfcheck import FAILED, UNVERIFIED, VERIFIED

FIXTURES = Path(__file__).parent / "fixtures" / "pdfcheck"
PROFILE = LayoutProfile()
A4 = 841.89


def line(top, *words, h=10.0):
    """A line at `top`; each word is (x, text)."""
    return top, h, words


def page(*lines, height=A4, width=595.28):
    out = [f'<page width="{width}" height="{height}"><flow><block>']
    for top, h, words in lines:
        out.append("<line>")
        for x, text in words:
            safe = text.replace("&", "&amp;").replace("<", "&lt;")
            out.append(f'<word xMin="{x}" yMin="{top}" xMax="{x + 6 * len(text)}" yMax="{top + h}">{safe}</word>')
        out.append("</line>")
    out.append("</block></flow></page>")
    return "".join(out)


def doc(*pages):
    return (
        '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"><html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title></title></head><body><doc>{''.join(pages)}</doc></body></html>"
    )


def parsed(*pages):
    return parse_bbox_layout(doc(*pages))


def full_page(top=60, count=40, pitch=17.0, x=56.7):
    """A page of evenly spaced body lines, filling to about 90% of the height, with a page number."""
    body = [line(top + i * pitch, (x, f"line {i}")) for i in range(count)]
    return body + [line(795, (295, "1"))]


def messages(verdict):
    return "\n".join(f.message for f in verdict.findings)


# --- parsing ----------------------------------------------------------------------------------------------------

def test_bbox_parse_builds_pages_and_lines():
    pages = parsed(page(line(100, (56.7, "Hello"), (80, "world")), line(50, (56.7, "First"))))
    assert len(pages) == 1 and (pages[0].width, pages[0].height) == (595.28, A4)
    assert [l.text for l in pages[0].lines] == ["First", "Hello world"]
    assert pages[0].lines[1].left == 56.7 and pages[0].lines[1].top == 100


def test_lines_come_from_bbox_layout_and_sort_top_to_bottom():
    pages = parsed(page(line(300, (10, "c")), line(100, (200, "b")), line(100, (10, "a"))))
    assert [l.text for l in pages[0].lines] == ["a", "b", "c"]


def test_real_bbox_layout_output_parses():
    pages = parse_bbox_layout((FIXTURES / "cv.bbox.html").read_text(encoding="utf-8"))
    assert len(pages) == 2 and pages[0].height == pytest.approx(841.89, abs=0.01)
    assert pages[0].lines[0].text == "Jane Example"
    assert any(l.text == "jane@example.test" for l in pages[0].lines)


def test_non_ascii_words_parse():
    pages = parsed(page(line(100, (56.7, "Genève"), (90, "&"), (100, "café"))))
    assert pages[0].lines[0].text == "Genève & café"


def test_unparsable_or_empty_bbox_output_is_unavailable_not_a_verdict():
    for broken in ("<html><page", "", doc(), "<html xmlns='x'><body><page height='x' width='1'/></body></html>"):
        with pytest.raises(LayoutUnavailable):
            parse_bbox_layout(broken)
        assert verify_layout(broken).status == UNVERIFIED


def test_bbox_argv_pins_utf8():
    assert layout.BBOX_ARGV == ("pdftotext", "-bbox-layout", "-enc", "UTF-8")


# --- missing or broken extractor --------------------------------------------------------------------------------

def test_missing_poppler_degrades_to_skipped():
    verdict = verify_layout(None)
    assert verdict.status == UNVERIFIED and not verdict.verified
    assert "pdftotext was not found" in messages(verdict) and "waiver" in messages(verdict)


def test_layout_tool_missing_is_skipped_not_pass():
    assert skipped(None).status != VERIFIED and skipped(None).status != FAILED


def test_extractor_crash_degrades_to_skipped():
    verdict = skipped(99, "Error: unknown option -bbox-layout\nusage")
    assert verdict.status == UNVERIFIED
    assert "Error: unknown option -bbox-layout" in messages(verdict)


def test_skip_message_names_both_causes():
    text = messages(skipped(1, ""))
    assert "xpdf" in text and "26.05" in text and "hyperref" in text and "exit code 1" in text


def test_extractor_failure_is_never_a_layout_verdict():
    for verdict in (skipped(None), skipped(99, "boom"), verify_layout("<broken")):
        assert verdict.status == UNVERIFIED and verdict.status != FAILED


# --- thresholds -------------------------------------------------------------------------------------------------

def test_profile_is_frozen_and_validates():
    with pytest.raises(FrozenInstanceError):
        PROFILE.gap_limit_pt = 1  # type: ignore[misc]
    for bad in ({"gap_limit_pt": 0}, {"bottom_limit_fraction": 1.5}, {"footer_lines_allowed": -1}, {"list_markers": ""}):
        with pytest.raises(ValueError):
            LayoutProfile(**bad)


def test_thresholds_overridable_per_template():
    pages = parsed(page(line(100, (56.7, "a")), line(215, (56.7, "b")), line(700, (56.7, "c"))))
    assert "hole" in messages(check_layout(pages, PROFILE))
    assert "hole" not in messages(check_layout(pages, LayoutProfile(gap_limit_pt=500)))


def test_custom_template_footer_band_override():
    crowded = parsed(page(*full_page(), line(760, (56.7, "stray")), line(770, (56.7, "stray"))))
    assert "footer band" in messages(check_layout(crowded, PROFILE))
    assert "footer band" not in messages(check_layout(crowded, LayoutProfile(footer_band_pt=30)))


def test_letter_size_page_uses_its_own_height():
    short = 792.0
    lines = [line(60 + i * 17, (56.7, f"l{i}")) for i in range(36)]
    (letter,) = parsed(page(*lines, height=short))
    (a4,) = parsed(page(*lines))
    assert bottom_space(letter, PROFILE) == pytest.approx(short - 60 - 35 * 17 - 10)
    assert bottom_space(a4, PROFILE) == pytest.approx(A4 - 60 - 35 * 17 - 10)
    assert measure((letter,), PROFILE)[0].bottom_space_percent == pytest.approx(100 * bottom_space(letter, PROFILE) / short)


# --- per-page measures ------------------------------------------------------------------------------------------

def test_body_excludes_footer_band():
    (p,) = parsed(page(line(100, (56.7, "body")), line(A4 - 50, (295, "1"))))
    assert [l.text for l in layout.body_lines(p, PROFILE)] == ["body"]
    assert layout.footer_tops(p, PROFILE) == [round(A4 - 50, 1)]


def test_gap_reports_322pt_at_y78():
    (p,) = parsed(page(line(78, (56.7, "a")), line(400, (56.7, "b")), line(414, (56.7, "c"))))
    assert largest_gap(p, PROFILE) == (322.0, 78.0)


def test_gap_is_zero_with_fewer_than_two_distinct_tops():
    (one,) = parsed(page(line(100, (56.7, "a")), line(100.04, (200, "b"))))
    (none,) = parsed(page(line(A4 - 20, (295, "1"))))
    assert largest_gap(one, PROFILE) == (0.0, 0.0) and largest_gap(none, PROFILE) == (0.0, 0.0)


def test_bottom_space_ignores_footer():
    (p,) = parsed(page(line(100, (56.7, "a")), line(A4 - 30, (295, "1"))))
    assert bottom_space(p, PROFILE) == pytest.approx(A4 - 110)


def test_bottom_space_is_the_whole_page_when_there_is_no_body():
    (p,) = parsed(page(line(A4 - 30, (295, "1"))))
    assert bottom_space(p, PROFILE) == A4


def test_layout_measures_page_extents_and_gaps():
    m = measure(parsed(page(line(78, (56.7, "a")), line(400, (56.7, "b")))), PROFILE)[0]
    assert (m.number, m.text_top, m.text_bottom, m.largest_gap_pt, m.largest_gap_y) == (1, 78, 410, 322, 78)
    assert m.bottom_space_pt == pytest.approx(A4 - 410) and m.bottom_space_percent == pytest.approx(100 * (A4 - 410) / A4)


def test_report_prints_per_page_measurements():
    out = format_measurements(measure(parsed(page(line(78, (56.7, "a")), line(400, (56.7, "b")))), PROFILE))
    assert out == "page 1: text y 78-410pt, bottom space 432pt (51%), largest gap 322pt at y=78"


def test_taller_line_is_a_heading():
    (p,) = parsed(page(*[line(60 + i * 17, (56.7, f"l{i}")) for i in range(6)], line(200, (56.7, "Head"), h=14)))
    heading = next(l for l in p.lines if l.text == "Head")
    assert layout.is_heading(p, heading, PROFILE) and not layout.is_heading(p, p.lines[0], PROFILE)
    assert not layout.is_heading(p, heading, LayoutProfile(heading_height_ratio=2.0))


def test_a_page_with_median_height_zero_has_no_headings():
    (p,) = parsed(page(line(60, (56.7, "a"), h=0.0), line(80, (56.7, "b"), h=0.0), line(100, (56.7, "c"), h=5)))
    assert not layout.is_heading(p, p.lines[2], PROFILE)


def test_left_edge_separates_bullets_from_headers():
    (p,) = parsed(page(line(100, (56.7, "Header")), line(120, (70.4, "bullet"))))
    header, bullet = p.lines
    assert bullet.left - header.left > PROFILE.indent_pt


# --- markers ----------------------------------------------------------------------------------------------------

def test_text_start_sets_aside_leading_markers():
    (p,) = parsed(page(line(100, (58.9, "•"), (72.1, "Built"), (100, "it")), line(120, (56.7, "plain")),
                       line(140, (58.9, "•"))))
    bullet, plain, only_marker = p.lines
    assert text_x(bullet, PROFILE) == 72.1 and text_x(plain, PROFILE) == 56.7 and text_x(only_marker, PROFILE) == 58.9


def test_dashes_and_other_glyphs_are_text():
    for glyph in ("-", "*", "–", "—", "○"):
        (p,) = parsed(page(line(100, (58.9, glyph), (72.1, "text"))))
        assert text_x(p.lines[0], PROFILE) == 58.9
    for glyph in "•‣▪●·":
        (p,) = parsed(page(line(100, (58.9, glyph), (72.1, "text"))))
        assert text_x(p.lines[0], PROFILE) == 72.1


# --- problems ---------------------------------------------------------------------------------------------------

def tidy_one_page(*extra):
    return page(*full_page(), *extra)


def test_a_tidy_page_is_clean():
    verdict = check_layout(parsed(tidy_one_page()))
    assert verdict.status == VERIFIED and verdict.findings == ()


def test_clean_and_problem_verdicts():
    assert check_layout(parsed(tidy_one_page())).verified
    problem = check_layout(parsed(page(line(78, (56.7, "a")), line(400, (56.7, "b")))))
    assert problem.status == FAILED and problem.feedback().startswith("[layout (pdftotext -bbox-layout)]")


def test_hole_over_100pt_is_flagged():
    verdict = check_layout(parsed(page(*[line(60 + i * 17, (56.7, f"l{i}")) for i in range(20)],
                                       line(60 + 19 * 17 + 150, (56.7, "after the hole")),
                                       *[line(60 + 19 * 17 + 150 + (i + 1) * 17, (56.7, f"m{i}")) for i in range(12)])))
    assert "vertical hole of 150pt" in messages(verdict) and "y=383pt" in messages(verdict)


def test_hole_of_exactly_100pt_is_allowed_and_just_over_is_not():
    def with_gap(gap):
        return parsed(page(line(100, (56.7, "a")), line(100 + gap, (56.7, "b")), *full_page(top=100 + gap + 17, count=30)))

    assert "hole" not in messages(check_layout(with_gap(100)))
    assert "hole" in messages(check_layout(with_gap(100.5)))


def test_hole_message_says_shorten_following_entry():
    pages = parsed(page(line(78, (56.7, "a")), line(400, (56.7, "b"))))
    text = messages(check_layout(pages))
    assert "shorten the entry that follows the hole" in text and "never stretch" in text


def test_layout_never_reports_page_count_problems():
    for count in (1, 2, 5):
        verdict = check_layout(parsed(*[tidy_one_page() for _ in range(count)]))
        assert verdict.verified, verdict.feedback()
    assert "page count" not in messages(check_layout(parsed(page(line(78, (56.7, "a")), line(400, (56.7, "b")))))).lower()


def test_page_with_no_body_text_is_a_problem():
    verdict = check_layout(parsed(tidy_one_page(), page(line(A4 - 30, (295, "2")))))
    assert "Page 2 contains no text" in messages(verdict)


def test_nonfinal_page_ending_over_25_percent_early_fails():
    short = page(*[line(60 + i * 17, (56.7, f"l{i}")) for i in range(25)], line(795, (295, "1")))  # ends ~y=495
    verdict = check_layout(parsed(short, tidy_one_page()))
    assert "Page 1 ends 43% early" in messages(verdict)
    assert "Page 2" not in messages(verdict)


def test_non_final_page_ending_early_flagged_but_the_limit_is_inclusive_of_normal_pages():
    assert check_layout(parsed(tidy_one_page(), tidy_one_page())).verified


def test_last_page_over_35_percent_empty_fails():
    half = page(*[line(60 + i * 17, (56.7, f"l{i}")) for i in range(20)], line(795, (295, "2")))  # ends ~y=400
    verdict = check_layout(parsed(tidy_one_page(), half))
    assert "The last page is 53% empty" in messages(verdict) and "highest-relevance" in messages(verdict)


def test_a_one_page_document_is_exempt_from_the_last_page_rule():
    sparse = page(line(60, (56.7, "only")), line(75, (56.7, "page")))
    assert check_layout(parsed(sparse)).verified


def test_text_in_footer_band_fails():
    crowded = tidy_one_page(line(770, (56.7, "spilled body line")))
    text = messages(check_layout(parsed(crowded)))
    assert "footer band" in text and "enlargethispage" in text


def test_two_lines_in_footer_band_flagged():
    assert "footer band" in messages(check_layout(parsed(tidy_one_page(line(780, (56.7, "x"))))))


def test_exactly_one_line_in_the_footer_band_is_the_page_number():
    assert check_layout(parsed(tidy_one_page())).verified


def test_a_template_without_page_numbers_may_forbid_any_footer_text():
    strict = LayoutProfile(footer_lines_allowed=0)
    assert "footer band" in messages(check_layout(parsed(tidy_one_page()), strict))


# --- orphans and stranded headings ------------------------------------------------------------------------------

def pair(page_one_tail, page_two_head, fill=36):
    """Two pages: a filled first page whose last body lines are `page_one_tail`, then a second page that opens with
    `page_two_head` and has a little body below it."""
    first = [line(60 + i * 17, (56.7, f"line {i}")) for i in range(fill)] + list(page_one_tail)
    second = list(page_two_head) + [line(300 + i * 17, (56.7, f"more {i}")) for i in range(20)]
    return parsed(page(*first, line(795, (295, "1"))), page(*second, line(795, (295, "2"))))


TAIL_TOP = 60 + 36 * 17  # 672


def test_page_ending_on_section_heading_flagged():
    pages = pair([line(TAIL_TOP, (56.7, "Education"), h=14)], [line(60, (56.7, "MSc"))])
    (finding,) = find_orphans(pages, PROFILE)
    assert "Page 1 ends on the section heading 'Education'" in finding.message and "page 2" in finding.message


def test_header_orphaned_from_bullets_flagged():
    pages = pair([line(TAIL_TOP, (56.7, "Platform Engineer, Acme"))], [line(60, (70.4, "Built pipelines"))])
    (finding,) = find_orphans(pages, PROFILE)
    assert "entry header 'Platform Engineer, Acme'" in finding.message and "needspace" in finding.message


def test_clean_break_reports_nothing():
    pages = pair([line(TAIL_TOP, (56.7, "Platform Engineer, Acme"))], [line(60, (56.7, "Developer, Beta"))])
    assert find_orphans(pages, PROFILE) == []


def test_known_blind_spot_level_marker_line():
    """Without font data a header at the page end followed by an unindented description (its marker alone on a line, the text
    level with the header) reads as a wrapped line, so no orphan is reported. The stock templates indent bullets, so this
    does not arise for them; the blind spot is recorded here so a change to it is deliberate."""
    pages = pair([line(TAIL_TOP, (56.7, "Platform Engineer, Acme"))], [line(60, (56.7, "•")), line(75, (56.7, "Built pipelines"))])
    assert find_orphans(pages, PROFILE) == []
    indented = pair([line(TAIL_TOP, (56.7, "Platform Engineer, Acme"))], [line(60, (56.7, "•")), line(75, (72.1, "Built pipelines"))])
    assert len(find_orphans(indented, PROFILE)) == 1                            # the same break with indented text is caught


def test_stock_bullet_wrap_clean():
    pages = pair([line(TAIL_TOP, (58.9, "•"), (72.1, "Built the long pipeline"))], [line(60, (72.1, "for services"))])
    assert find_orphans(pages, PROFILE) == []


def test_wrapped_bullet_is_not_orphan():
    pages = pair([line(TAIL_TOP, (58.9, "•"), (72.1, "Built it"))], [line(60, (72.1, "wrapped"))])
    assert find_orphans(pages, PROFILE) == []


def test_symbol_or_dash_continuation_is_wrapped_bullet():
    for glyph in ("—", "±", "-"):
        pages = pair([line(TAIL_TOP, (58.9, "•"), (72.1, "Cut cost"))], [line(60, (72.1, f"{glyph} by half"))])
        assert find_orphans(pages, PROFILE) == []


def test_header_level_with_merged_bullet_is_orphan():
    pages = pair([line(TAIL_TOP, (59.5, "Degree, University"))], [line(60, (58.9, "•"), (71.6, "Thesis on X"))])
    (finding,) = find_orphans(pages, PROFILE)
    assert "Degree, University" in finding.message


def test_stock_header_variants_remain_reported():
    outer_marker_own_line = pair(
        [line(TAIL_TOP, (59.0, "○")), line(TAIL_TOP, (70.4, "Engineer, Acme"))], [line(60, (82.0, "Built it"))],
    )
    assert len(find_orphans(outer_marker_own_line, PROFILE)) == 1
    unlisted_marker_merged = pair([line(TAIL_TOP, (59.0, "○"), (70.4, "Engineer, Acme"))], [line(60, (72.0, "Built it"))])
    assert len(find_orphans(unlisted_marker_merged, PROFILE)) == 1  # U+25CB is text: the line keeps x=59.0, bullets are 13pt in
    degree = pair([line(TAIL_TOP, (56.7, "MSc, University"))], [line(60, (70.4, "Thesis"))])
    assert len(find_orphans(degree, PROFILE)) == 1


def test_printed_line_split_by_font_read_as_one():
    pages = pair(
        [line(TAIL_TOP, (58.9, "•"), (72.1, "Built the long")), line(TAIL_TOP + 1.3, (150, "bold"))],
        [line(60, (72.1, "continuation"))],
    )
    assert find_orphans(pages, PROFILE) == []


def test_text_x_ignores_marker_only_lines():
    pages = pair(
        [line(TAIL_TOP, (72.1, "Whole item")), line(TAIL_TOP + 1.3, (58.9, "•"))], [line(60, (72.1, "next item"))],
    )
    assert find_orphans(pages, PROFILE) == []


def test_marker_below_whole_item_not_split_bullet():
    pages = pair(
        [line(TAIL_TOP, (72.1, "Whole item")), line(TAIL_TOP + 1.3, (58.9, "•"))], [line(60, (56.7, "Next header"))],
    )
    assert find_orphans(pages, PROFILE) == []


def test_lone_marker_split_bullet_flagged():
    pages = pair([line(TAIL_TOP, (56.7, "•"))], [line(60, (72.1, "text of the bullet"))])
    (finding,) = find_orphans(pages, PROFILE)
    assert "bullet is split" in finding.message and "text of the bullet" in finding.message


def test_two_line_item_split_reads_as_wrapped():
    pages = pair([line(TAIL_TOP, (72.1, "First line of an item"))], [line(60, (72.1, "second line of it"))])
    assert find_orphans(pages, PROFILE) == []


def test_indent_judged_against_document_margin():
    first = [line(60 + i * 17, (70.4, f"bullet {i}")) for i in range(36)]
    second = [line(60, (70.4, "bullet page two")), line(300, (56.7, "Header"))]
    pages = parsed(page(*first, line(TAIL_TOP, (70.4, "bullet last")), line(795, (295, "1"))), page(*second, line(795, (295, "2"))))
    assert find_orphans(pages, PROFILE) == []  # the margin (56.7) is the document's, so 70.4 on both pages is level


def test_running_header_is_not_where_body_resumes():
    pages = pair(
        [line(TAIL_TOP, (56.7, "Developer, Beta"))],
        [line(40, (459.8, "Jane Example, CV")), line(60, (56.7, "Next header"))],
    )
    assert find_orphans(pages, PROFILE) == []


def test_orphan_behind_running_header_found():
    pages = pair(
        [line(TAIL_TOP, (56.7, "Developer, Beta"))],
        [line(40, (459.8, "Jane Example, CV")), line(60, (70.4, "Maintained billing"))],
    )
    assert len(find_orphans(pages, PROFILE)) == 1


def test_missing_opener_or_empty_pages_report_nothing():
    only_running_pages = parsed(
        page(*full_page(), line(TAIL_TOP, (56.7, "Developer"))),
        page(line(10, (459.8, "right")), line(30, (300, "centred")), line(A4 - 30, (295, "2"))),
    )
    footer_only = parsed(page(*full_page()), page(line(A4 - 30, (295, "2"))))
    assert find_orphans(only_running_pages, PROFILE) == []
    assert find_orphans(footer_only, PROFILE) == []
    assert find_orphans(parsed(page(line(A4 - 30, (295, "1")))), PROFILE) == []


def test_text_set_in_further_than_the_limit_is_never_where_body_resumes():
    far_only = parsed(
        page(*full_page(), line(TAIL_TOP, (56.7, "Developer"))),
        page(line(40, (130.0, "set in")), line(A4 - 30, (295, "2"))),
    )
    assert find_orphans(far_only, PROFILE) == []
    near = parsed(
        page(*full_page(), line(TAIL_TOP, (56.7, "Developer"))),
        page(line(40, (110.0, "set in")), line(A4 - 30, (295, "2"))),
    )
    assert len(find_orphans(near, PROFILE)) == 1


def test_orphaned_entry_header_or_heading_fails():
    pages = pair([line(TAIL_TOP, (56.7, "Platform Engineer, Acme"))], [line(60, (70.4, "Built pipelines"))])
    assert check_layout(pages).status == FAILED
    heading = pair([line(TAIL_TOP, (56.7, "Education"), h=14)], [line(60, (56.7, "MSc"))])
    assert check_layout(heading).status == FAILED


def test_problems_carry_actionable_hint():
    pages = parsed(
        page(*[line(60 + i * 17, (56.7, f"l{i}")) for i in range(25)], line(700, (56.7, "Education"), h=14), line(780, (56.7, "x")),
             line(790, (56.7, "y"))),
        page(line(60, (56.7, "MSc")), line(A4 - 30, (295, "2"))),
    )
    text = messages(check_layout(pages))
    for hint in ("shorten", "Keep the heading", "never use \\enlargethispage", "Restore"):
        assert hint in text


# --- real output ------------------------------------------------------------------------------------------------

def test_real_cv_has_a_hole_and_a_thin_last_page():
    verdict = verify_layout((FIXTURES / "cv.bbox.html").read_text(encoding="utf-8"))
    assert verdict.status == FAILED
    text = messages(verdict)
    assert "Page 1 has a vertical hole of 450pt" in text and "The last page is 89% empty" in text
    assert "footer band" not in text and "ends" not in text.replace("ends early", "")


@pytest.mark.docker
def test_real_bbox_layout_from_the_check_container(tmp_path):
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    pdf_dir, out = tmp_path / "in", tmp_path / "out"
    pdf_dir.mkdir()
    out.mkdir()
    out.chmod(0o777)
    shutil.copy(FIXTURES / "cv.pdf", pdf_dir / "cv.pdf")
    subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "-v", f"{pdf_dir}:/in:ro", "-v", f"{out}:/out", "job-search-check",
         *layout.BBOX_ARGV, "/in/cv.pdf", "/out/bbox.html"],
        check=True, timeout=120,
    )
    live = (out / "bbox.html").read_text(encoding="utf-8")
    verdict = verify_layout(live)
    assert verdict.status == FAILED and "vertical hole of 450pt" in messages(verdict)
    assert [m.number for m in measure(parse_bbox_layout(live), PROFILE)] == [1, 2]

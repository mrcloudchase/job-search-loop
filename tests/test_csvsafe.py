"""csvsafe.py: whatever goes into the tracker CSV comes out as one safe, unchanged-in-meaning field."""
from __future__ import annotations

import csv
import io

import pytest

from jobloop import csvsafe

HOSTILE = [
    'rejected, no feedback given', 'say "hi", then\nleave\r\nnow', "=HYPERLINK(\"http://evil\",\"x\")", "+1+1", "-2+3", "@SUM(A1)",
    "\t=1+1", "\r=1+1", " =1+1", "＝1+1", "＋cmd", "\u202eevil\u202c", "a\x00b\x07c\x1b[31m", "x" * 5000, "", "   ", ",", '"',
    "\u2028line\u2029sep", "e\u0301" * 400, "\ud800 lone surrogate", "ok; fine",
]


@pytest.mark.parametrize("value", HOSTILE)
def test_no_comma_quote_or_line_break_survives(value):
    out = csvsafe.field(value)
    assert not any(c in out for c in ',"\n\r\t\v\f\u2028\u2029')


@pytest.mark.parametrize("value", HOSTILE)
def test_nothing_that_starts_a_formula_survives_at_the_start(value):
    out = csvsafe.field(value)
    assert out == "" or out[0] not in "=+-@\t\r"


@pytest.mark.parametrize("value", HOSTILE)
def test_control_and_bidi_characters_are_gone(value):
    import unicodedata
    assert not any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Cn") for c in csvsafe.field(value))


@pytest.mark.parametrize("value", HOSTILE)
def test_a_value_roundtrips_through_the_csv_module_as_one_unquoted_field(value):
    out = csvsafe.field(value)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(["a", out, "b"])
    assert buf.getvalue() == f"a,{out},b\n"
    assert list(csv.reader(io.StringIO(buf.getvalue()))) == [["a", out, "b"]]


@pytest.mark.parametrize("value", HOSTILE)
def test_applying_it_twice_changes_nothing_the_second_time(value):
    once = csvsafe.field(value)
    assert csvsafe.field(once) == once and csvsafe.is_safe(once)


def test_a_comma_becomes_a_dash_separator_not_a_deletion():
    assert csvsafe.field("rejected, no feedback given") == "rejected - no feedback given"


def test_a_formula_start_is_neutralised_not_deleted():
    assert csvsafe.field("=1+1") == "'=1+1"
    assert csvsafe.field("\t=1+1") == "'=1+1"
    assert csvsafe.field("＝1+1") == "'=1+1"


def test_ordinary_text_passes_unchanged():
    for text in ("followed up 2026-01-05", "redrafted", "stale resolved no_response (2026-02-01)", "Novo Nordisk A/S", "Zoë Müller"):
        assert csvsafe.field(text) == text and csvsafe.is_safe(text)


def test_length_is_capped_and_says_so():
    out = csvsafe.field("x" * 5000)
    assert len(out) == csvsafe.FIELD_WIDTH and out.endswith("…")
    assert len(csvsafe.note("y" * 5000)) == csvsafe.NOTE_WIDTH


def test_it_never_raises_on_hostile_objects():
    class Bad:
        def __str__(self):
            raise RuntimeError("no")

    for value in (Bad(), None, b"\xff\xfe", 12, 1.5, object(), ["a,b"], {"x": 1}):
        assert isinstance(csvsafe.field(value), str)
    assert csvsafe.field(None) == "" and csvsafe.field(Bad()) == ""


def test_a_url_keeps_working_after_the_characters_that_break_csv_are_encoded():
    out = csvsafe.url('https://x.example/jobs?a=1,2&q="z" y\n')
    assert out == "https://x.example/jobs?a=1%2C2&q=%22z%22%20y"
    assert csvsafe.is_safe(out, csvsafe.URL_WIDTH)


def test_a_url_that_is_a_formula_is_neutralised():
    assert csvsafe.url("=cmd|' /C calc'!A0").startswith("'=")

"""latex.py: escaping, and the lint that catches what would silently break a document."""
from __future__ import annotations

from pathlib import Path

from jobloop import latex

FIXTURES = Path(__file__).parent / "fixtures" / "writing"


def test_all_special_characters_escaped_in_company_and_bullets():
    assert latex.escape("Bang & Olufsen") == r"Bang \& Olufsen"
    assert latex.escape("40% of $5 #1 a_b {x} ~ ^ \\") == (
        r"40\% of \$5 \#1 a\_b \{x\} \textasciitilde{} \textasciicircum{} \textbackslash{}")


def test_ampersand_in_employer_name_escaped():
    assert latex.escape("Johnson & Johnson") == r"Johnson \& Johnson"


def test_escaped_text_passes_the_check_whatever_it_holds():
    nasty = "50% of R&D at A_B #1 costs $5, {braces} ~tilde ^caret \\backslash"
    assert latex.check("\\begin{document}\n" + latex.escape(nasty) + "\n\\end{document}") == []


def test_unescaped_percent_in_body_rejected():
    found = latex.specials("\\begin{document}\nfailed loads dropped 70% over two quarters\n\\end{document}")
    assert found and found[0].startswith("line 2:") and "%" in found[0]


def test_percent_after_a_digit_at_line_end_is_still_rejected():
    assert latex.specials("failed loads dropped 70%")


def test_a_percent_that_truncates_the_line_is_rejected_with_its_line_number():
    found = latex.specials("fine\nGood text 100% of the work\nfine")
    assert len(found) == 1 and found[0].startswith("line 2:")


def test_comment_lines_and_line_end_percent_are_legitimate():
    assert latex.specials("% a comment, 100% fine\n\\item text%\n  more\n") == []


def test_escaped_percent_is_not_flagged():
    assert latex.specials(r"dropped 70\% over two quarters") == []


def test_other_unescaped_specials_flagged_with_line_numbers():
    tex = "ok\nBang & Olufsen\nuse #1\nfile a_b\nup^2 text\nprice $50\n"
    lines = {int(f.split(":")[0].split()[1]) for f in latex.specials(tex)}
    assert lines == {2, 3, 4, 5, 6}


def test_math_and_tabular_and_link_targets_are_not_flagged():
    tex = ("$x_1 + y^2$ is math\n\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}\n"
           "\\href{https://example.com/a_b?x=1&y=2#top}{site} \\url{http://x.org/a_b}\nDr.~Smith\n")
    assert latex.specials(tex) == []


def test_commands_and_macro_definitions_are_not_flagged():
    tex = "\\newcommand{\\entry}[3]{\\textbf{#1} \\hfill #3\\\\ \\emph{#2}}\n\\begin{document}\n\\entry{a}{b}{c}\n\\end{document}"
    assert latex.specials(tex) == []


def test_preamble_is_not_linted_when_there_is_a_document_body():
    assert latex.specials("\\usepackage[a=b]{x}\n\\def\\a#1{#1_}\n\\begin{document}\nclean\n\\end{document}") == []


def test_unbalanced_braces_reported():
    assert latex.specials("text {open\n")
    assert latex.specials("text close}\n")


def test_unclosed_math_dollar_reported():
    assert latex.specials("cost $x is high\n")


def test_a_backslash_that_starts_no_command_is_flagged():
    assert latex.specials("path C:\\ 5 and \\5 here")


def test_bracket_leading_item_is_braced():
    assert latex.bullet("[Confidential] project") == r"\item {[Confidential] project}"
    assert latex.bullet("100% done") == r"\item 100\% done"


def test_an_unbraced_bracket_leading_item_is_rejected():
    found = latex.specials("\\item [Confidential] project\n\\item {[Confidential]} project\n\\item fine\n")
    assert len(found) == 1 and found[0].startswith("line 1:")


def test_untrusted_text_cannot_inject_latex_commands():
    assert latex.escape(r"\input{/etc/passwd}") == r"\textbackslash{}input\{/etc/passwd\}"
    bad = "\\begin{document}\n\\input{/etc/passwd}\n\\immediate\\write18{rm -rf x}\n\\end{document}"
    found = latex.unsafe(bad)
    assert any("\\input" in f for f in found) and any("\\write" in f for f in found)


def test_input_in_the_preamble_of_a_template_is_not_flagged_but_write18_always_is():
    tex = "\\input{glyphtounicode}\n\\write18{x}\n\\begin{document}\nok\n\\end{document}"
    found = latex.unsafe(tex)
    assert len(found) == 1 and "\\write" in found[0]


def test_the_ok_cv_fixture_passes_the_whole_check():
    assert latex.check((FIXTURES / "cv_ok.tex").read_text()) == []


def test_to_text_gives_the_reader_text_and_undoes_escapes():
    tex = (FIXTURES / "cv_ok.tex").read_text()
    text = latex.to_text(tex)
    assert "Senior Data Engineer | Tidewater Logistics, Rotterdam | March 2022 -- present" in text
    assert "- Cut the nightly rebuild" in text and "70% over" in text
    assert "margin" not in text and "100% of nothing" not in text and "\\" not in text


def test_to_text_keeps_link_addresses_on_request():
    tex = r"\href{https://github.com/x}{my code}"
    assert latex.to_text(tex) == "my code"
    assert latex.to_text(tex, keep_links=True) == "my code <https://github.com/x>"

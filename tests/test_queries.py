"""queries.py: the search plan is bounded, by function, in every declared language, and makes no request."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from jobloop import profile as P, queries as Q, search

GOOD = (Path(__file__).parent / "fixtures" / "profile" / "profile.md").read_text()


def with_queries(block: str, base: str = GOOD) -> str:
    head = base.split("## Search queries")[0]
    return head + "## Search queries\n" + block.strip() + "\n"


FIVE = with_queries("""
### English
- Data pipeline engineering: data engineer; analytics engineer; data platform engineer
- Pipeline orchestration and quality: airflow engineer; data reliability engineer
- Data modelling: data modeller; warehouse engineer
- Team leadership: data team lead; engineering manager
- Reporting: bi developer; reporting analyst
- Adjacent roles: machine learning platform engineer; data architect
### Dutch
- Data pipeline engineering: data engineer; dataplatform specialist; data platform engineer
- Pipeline orchestration and quality: airflow engineer; data betrouwbaarheid engineer
- Data modelling: data modelleur; warehouse engineer
- Team leadership: data team lead; engineering manager
- Reporting: bi ontwikkelaar; rapportage analist
- Adjacent roles: machine learning platform engineer; data architect
### German
- Data pipeline engineering: Dateningenieur; Datenplattform Entwickler; Data Engineer
- Pipeline orchestration and quality: Airflow Entwickler; Datenqualität Ingenieur
- Data modelling: Datenmodellierer; Warehouse Entwickler
- Team leadership: Teamleiter Daten; Engineering Manager
- Reporting: BI Entwickler; Reporting Analyst
- Adjacent roles: Datenarchitekt; ML-Plattform Ingenieur
""")


def danish(text: str = GOOD) -> str:
    """The same person, who also declares Danish."""
    text = text.replace("| German | A2 |", "| German | A2 |\n| Danish | B2 |")
    return text.rstrip("\n") + """
### Danish
- Data pipeline engineering: data engineer; dataingeniør; data platform udvikler
- Pipeline orchestration and quality: airflow udvikler; datakvalitet ingeniør
- Adjacent roles: data arkitekt; maskinlæring platform ingeniør
"""


def texts(runs, board=None):
    """The queries in plan order, each once (a query is repeated for every place it is searched in)."""
    return list(dict.fromkeys(r.query for r in runs if board is None or r.board == board))


def functions(runs):
    return list(dict.fromkeys(r.function for r in runs))


def test_default_top3_categories_broad_all_focus_first():
    default = Q.plan_queries(FIVE, ["freehire"], per_board=100)
    assert functions(default) == ["Data pipeline engineering", "Pipeline orchestration and quality", "Data modelling"]
    broad = Q.plan_queries(FIVE, ["freehire"], focus="broad", per_board=100)
    assert functions(broad) == ["Data pipeline engineering", "Pipeline orchestration and quality", "Data modelling",
                                "Team leadership", "Reporting", "Adjacent roles"]
    focused = Q.plan_queries(FIVE, ["freehire"], focus="reporting", per_board=100)
    assert functions(focused)[0] == "focus: reporting" and functions(focused)[1] == "Reporting"
    assert len([f for f in functions(focused) if not f.startswith("focus")]) == 3


def test_a_focus_is_found_in_a_functions_queries_when_its_label_does_not_name_it():
    runs = Q.plan_queries(FIVE, ["freehire"], focus="warehouse", per_board=100)
    assert "Data modelling" in functions(runs)[:2]


def test_adjacent_roles_come_last_and_only_fill_a_short_list():
    runs = Q.plan_queries(FIVE, ["freehire"], per_board=100)
    assert P.ADJACENT not in functions(runs)
    short = with_queries("### English\n- A: a one; a two\n- Adjacent roles: arch one\n### Dutch\n- A: n een; n twee\n"
                         "- Adjacent roles: arch een\n### German\n- A: d eins\n- Adjacent roles: arch eins\n")
    assert functions(Q.plan_queries(short, ["freehire"], per_board=100)) == ["A", "Adjacent roles"]


def test_queries_emitted_per_declared_language():
    runs = Q.plan_queries(FIVE, ["freehire"], per_board=100)
    assert {r.language for r in runs} == {"English", "Dutch", "German"}
    assert "dataplatform specialist" in texts(runs) and "Dateningenieur" in texts(runs) and "data engineer" in texts(runs)


def test_category_has_multiple_title_variants():
    runs = Q.plan_queries(FIVE, ["freehire"], per_board=100)
    english = dict.fromkeys(r.query for r in runs if r.language == "English" and r.function == "Data pipeline engineering")
    assert list(english) == ["data engineer", "analytics engineer", "data platform engineer"]


def test_variants_come_round_by_round_so_every_function_has_its_first_title_before_any_second():
    runs = Q.plan_queries(FIVE, ["linkedin"], languages=("English",), per_board=100)
    assert texts(runs)[:3] == ["data engineer", "airflow engineer", "data modeller"]
    assert texts(runs).index("analytics engineer") > texts(runs).index("data modeller")


def test_a_danish_board_gets_danish_function_queries_only():
    runs = Q.plan_queries(danish(), ["jobindex"], per_board=100)
    assert runs and {r.language for r in runs} == {"Danish"}
    assert "dataingeniør" in texts(runs) and "data engineer" in texts(runs)        # Danish uses the English loan title too
    assert "Dateningenieur" not in texts(runs) and "dataplatform specialist" not in texts(runs)


def test_a_board_for_a_language_the_person_does_not_declare_is_never_planned():
    assert Q.plan_queries(GOOD, ["jobindex", "jobnet", "jobdanmark", "jobbank"]) == ()
    runs = Q.plan_queries(GOOD, ["freehire", "jobindex"])
    assert {r.board for r in runs} == {"freehire"}


def test_search_language_filter_semantics():
    # plan time: a declared language at a low level (German A2) is still searched; Danish, undeclared, is not
    runs = Q.plan_queries(GOOD, ["freehire", "jobindex"], per_board=100)
    assert "German" in {r.language for r in runs} and "jobindex" not in {r.board for r in runs}
    # a posting written in an undeclared language is not this module's concern: it plans only from the profile
    with pytest.raises(ValueError, match="does not declare Danish"):
        Q.plan_queries(GOOD, ["freehire"], languages=("Danish",))


def test_an_international_board_may_be_limited_to_some_declared_languages():
    runs = Q.plan_queries(FIVE, ["freehire"], languages=("dutch",), per_board=100)
    assert {r.language for r in runs} == {"Dutch"}


def test_focus_adds_custom_queries():
    runs = Q.plan_queries(FIVE, ["freehire"], languages=("English",), focus="airflow", per_board=100)
    custom = list(dict.fromkeys(r.query for r in runs if r.function.startswith("focus")))
    assert custom == ["airflow", "airflow engineer", "airflow data reliability engineer"]
    assert texts(runs)[:len(custom)] == custom
    assert not any(r.function.startswith("focus") for r in Q.plan_queries(FIVE, ["freehire"], per_board=100))


def test_runs_per_board_and_places_per_query_are_bounded():
    runs = Q.plan_queries(FIVE, ["freehire", "linkedin"], focus="broad", per_board=5)
    assert len([r for r in runs if r.board == "freehire"]) == 5 and len([r for r in runs if r.board == "linkedin"]) == 5
    assert len({r.location for r in Q.plan_queries(FIVE, ["freehire"], per_board=100)}) <= Q.PLACES
    with pytest.raises(ValueError, match="at least 1"):
        Q.plan_queries(FIVE, ["freehire"], per_board=0)


def test_location_tiers_built_from_constraints():
    assert P.parse_settings(GOOD) == P.Settings("en", ("Rotterdam", "Delft"), ("Utrecht",), ("Amsterdam",), ("Groningen",),
                                                "at most one hour of commuting each way")
    runs = Q.plan_queries(FIVE, ["freehire"], per_board=100)
    assert {r.location for r in runs} == {"Rotterdam", "Delft"}                  # ideal first; acceptable only if room
    assert not {r.location for r in runs} & {"Amsterdam", "Groningen"}            # borderline and too far are never searched
    no_ideal = GOOD.replace("- Ideal locations: Rotterdam; Delft\n", "")
    assert {r.location for r in Q.plan_queries(with_queries("### English\n- A: a\n### Dutch\n- A: b\n### German\n- A: c\n"
                                                           "- Adjacent roles: x\n", no_ideal), ["freehire"])} == {"Utrecht"}
    assert {r.location for r in Q.plan_queries(with_queries("### English\n- A: a\n### Dutch\n- A: b\n### German\n- A: c\n",
                                                           GOOD.split("## Settings")[0] + "## Preferences\n"), ["freehire"])} == {""}


def test_a_query_a_board_would_refuse_is_an_error_before_anything_is_planned():
    bad = with_queries("### English\n- A: --help; fine\n### Dutch\n- A: n\n### German\n- A: d\n")
    with pytest.raises(ValueError, match="does not start with '-'|start with"):
        Q.plan_queries(bad, ["freehire"])
    with pytest.raises(ValueError):
        Q.plan_queries(FIVE, ["freehire"], focus="-rf")
    with pytest.raises(ValueError, match="focus needs words"):
        Q.plan_queries(FIVE, ["freehire"], focus="  ")
    long = with_queries("### English\n- A: " + "x" * 201 + "\n### Dutch\n- A: n\n### German\n- A: d\n")
    with pytest.raises(ValueError):
        Q.plan_queries(long, ["freehire"])


def test_an_unknown_board_or_a_language_without_queries_is_refused_not_guessed():
    with pytest.raises(ValueError, match="do not know which language"):
        Q.plan_queries(FIVE, ["somewhere"])
    with pytest.raises(ValueError, match="no '### German' search queries"):
        Q.plan_queries(with_queries("### English\n- A: a\n### Dutch\n- A: b\n"), ["freehire"])


def test_the_plan_is_a_pure_function_of_the_profile():
    assert Q.plan_queries(FIVE, ["freehire", "linkedin"], recency=7) == Q.plan_queries(FIVE, ["freehire", "linkedin"], recency=7)
    run = Q.plan_queries(FIVE, ["freehire"], recency=7)[0]
    assert run.recency_days == 7 and run.as_query().recency_days == 7 and run.as_query().text == run.query


# ── the command: show the plan, request nothing ──
def me_with(tmp_path, text: str) -> Path:
    (tmp_path / "me").mkdir()
    (tmp_path / "me" / "profile.md").write_text(text)
    return tmp_path / "me"


def parse(*argv):
    parser = argparse.ArgumentParser()
    Q.add_arguments(parser)
    return parser.parse_args(argv)


def test_the_command_prints_the_plan_as_runnable_searches_and_requests_nothing(tmp_path, capsys, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a request-capable client was built")
    monkeypatch.setattr(search, "make_client", refuse)
    monkeypatch.setattr(search, "search", refuse)
    me = me_with(tmp_path, FIVE)
    assert Q.main(parse("--me", str(me), "--board", "freehire")) == 0
    out = capsys.readouterr().out
    assert "to run: search --board freehire --location Rotterdam --recency-days 14 --query 'data engineer'" in out
    assert "nothing has been requested" in out and Q.HARNESS is False


def test_the_search_command_has_a_plan_flag_that_makes_no_request(tmp_path, capsys, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the plan made a request")
    monkeypatch.setattr(search, "make_client", refuse)
    monkeypatch.setattr(search, "search", refuse)
    me = me_with(tmp_path, FIVE)
    parser = argparse.ArgumentParser()
    search.add_arguments(parser)
    args = parser.parse_args(["--me", str(me), "--plan", "--focus", "reporting", "--board", "freehire"])
    assert search.main(args) == 0
    assert "focus: reporting" in capsys.readouterr().out


def test_boards_that_are_off_and_match_the_languages_are_listed_with_the_command_to_switch_them_on(tmp_path, capsys):
    me = me_with(tmp_path, danish(FIVE))
    assert Q.main(parse("--me", str(me))) == 0
    out = capsys.readouterr().out
    assert "jobindex: off" in out and "search boards enable jobindex" in out
    assert "jobindex" not in search.enabled_boards(me)                           # planning switches nothing on


def test_a_board_left_out_for_its_language_is_said(tmp_path, capsys):
    me = me_with(tmp_path, FIVE)
    assert Q.main(parse("--me", str(me), "--board", "freehire", "--board", "jobindex")) == 0
    assert "jobindex: not planned, it works in Danish and your profile does not declare it" in capsys.readouterr().out


def test_the_command_refuses_without_a_profile_or_a_plan(tmp_path, capsys):
    assert Q.main(parse("--me", str(tmp_path / "nowhere"))) == 1
    assert "build your profile first" in capsys.readouterr().out
    me = me_with(tmp_path, GOOD)
    assert Q.main(parse("--me", str(me), "--board", "jobindex")) == 1
    assert Q.main(parse("--me", str(me), "--language", "Danish", "--board", "freehire")) == 1


def test_function_names_feed_experience_match():
    from jobloop import fit
    runs = Q.plan_queries(FIVE, ["freehire"], focus="broad", per_board=100)
    assert set(functions(runs)) == set(P.parse_search_queries(FIVE)["English"])          # categories are named by function
    assert "function and nature of the work" in fit.RUBRIC["experience"]                  # and the fit judges function, not title
    assert all(label in FIVE for label in functions(runs))                               # the labels travel in the profile the fit reads

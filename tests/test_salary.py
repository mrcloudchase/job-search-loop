"""salary.py: a benchmark from data the person supplies; bad data is reported, never guessed at, and never a traceback."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

import pytest

from jobloop import salary as S

FIX = Path(__file__).parent / "fixtures" / "salary"
DATA = json.loads((FIX / "benchmark.json").read_text())


def data(**changes):
    d = copy.deepcopy(DATA)
    d.update(changes)
    return d


def errors(d):
    return "\n".join(S.validate(d).errors)


def entry(**cats):
    return {"metadata": None, "companies": [{"name": "Acme", "categories": cats}]}


def shown(d, query="Acme"):
    return S.render(d, S.search(d, query))


# ── loading ──
def test_missing_salary_data_skips_step_without_error(tmp_path):
    assert S.load(tmp_path / "nothing.json") is None
    got = S.benchmark(tmp_path, "Acme")
    assert got.text is None and "skipped" in got.note
    args = argparse.Namespace(me=str(tmp_path), data=None, company="Acme", city="", validate=False, json=False, convert_to=None)
    assert S.main(args) == 0


def test_bom_and_plain_utf8_both_load(tmp_path):
    (tmp_path / "plain.json").write_text(json.dumps(DATA, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "bom.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(DATA, ensure_ascii=False).encode())
    assert S.load(tmp_path / "plain.json") == S.load(tmp_path / "bom.json") == DATA


def test_bom_does_not_mask_json_error(tmp_path):
    (tmp_path / "bad.json").write_bytes(b"\xef\xbb\xbf" + b'{"companies": [\n  {"name": oops}\n]}')
    with pytest.raises(S.SalaryDataError) as info:
        S.load(tmp_path / "bad.json")
    assert "line 2 column 12" in str(info.value) and "jobloop/salary.py" in str(info.value)


def test_invalid_json_reports_position_without_traceback(tmp_path, capsys):
    (tmp_path / "bad.json").write_text('{"companies": [,]}')
    args = argparse.Namespace(me=str(tmp_path), data=str(tmp_path / "bad.json"), company="x", city="", validate=False, json=False, convert_to=None)
    assert S.main(args) == 1
    out = capsys.readouterr().out
    assert "invalid JSON at line 1 column 16" in out and "Traceback" not in out


# ── validation ──
def test_duplicate_company_is_warning_not_error():
    d = data()
    d["companies"].append({"name": "NOVO NORDISK A/S", "categories": {}})
    result = S.validate(d)
    assert result.errors == () and len(result.warnings) == 1 and "companies[5]" in result.warnings[0] and "companies[0]" in result.warnings[0]


def test_duplicate_detection_is_case_insensitive():
    d = {"companies": [{"name": "Café Étoile"}, {"name": "CAFÉ ÉTOILE"}, {"name": "Café Étoile"}]}
    assert len(S.validate(d).warnings) == 2


def test_top_level_shape_errors():
    assert "top level must be an object" in errors([])
    assert "companies must be a list" in errors({"companies": {}})
    assert "companies must be a list" in errors({})
    assert "metadata must be an object" in errors({"metadata": [], "companies": []})
    assert errors({"metadata": None, "companies": []}) == "" and errors({"companies": []}) == ""


def test_entry_shape_errors():
    assert "companies[0] must be an object" in errors({"companies": ["x"]})
    for name in (None, "", "   ", " ", 7):
        assert "name must be a non-blank string" in errors({"companies": [{"name": name}]})
    assert "city must be a string" in errors({"companies": [{"name": "A", "city": 3}]})
    assert "categories must be an object" in errors({"companies": [{"name": "A", "categories": []}]})
    assert errors({"companies": [{"name": "A", "city": None, "categories": None}]}) == ""


def test_category_shape_errors():
    assert "categories.x must be an object" in errors(entry(x=5))
    assert "count must be a number" in errors(entry(x={"count": "12"}))
    assert "count must be a number" in errors(entry(x={"count": True}))
    assert "index must be a finite number or text" in errors(entry(x={"index": [1]}))
    assert errors(entry(x={"count": None, "index": None}, y={"index": "private"}, z={"count": 0})) == ""


@pytest.mark.parametrize("baseline", ["150", True, None, math.inf, math.nan, 10 ** 400, [], {}])
def test_baseline_rejects_text_bool_null_nonfinite_overflow(baseline):
    d = {"metadata": {"index_baseline": baseline}, "companies": []}
    assert "index_baseline must be a finite number" in errors(d)


def test_index_label_must_be_string():
    assert "index_label must be a string" in errors({"metadata": {"index_label": ["x"]}, "companies": []})


def test_lookup_with_bad_baseline_exits_clean(tmp_path, capsys):
    path = tmp_path / "b.json"
    path.write_text('{"metadata": {"index_baseline": "150"}, "companies": [{"name": "Acme", "categories": {"a": {"count": 1, "index": 120}}}]}')
    args = argparse.Namespace(me=str(tmp_path), data=str(path), company="Acme", city="", validate=False, json=False, convert_to=None)
    assert S.main(args) == 1
    out = capsys.readouterr().out
    assert "index_baseline must be a finite number" in out and "Traceback" not in out
    assert S.benchmark(tmp_path, "Acme", path=path).text is None


def test_validate_report_exit_codes(tmp_path, capsys):
    good, warn, bad = tmp_path / "g.json", tmp_path / "w.json", tmp_path / "b.json"
    good.write_text(json.dumps(DATA))
    warn.write_text(json.dumps({"companies": [{"name": "A"}, {"name": "a"}]}))
    bad.write_text("{\"companies\": 3}")
    run = lambda p: S.main(argparse.Namespace(me=str(tmp_path), data=str(p), company=None, city="", validate=True, json=False, convert_to=None))
    assert run(good) == 0 and "OK" in capsys.readouterr().out
    assert run(warn) == 0 and "warning 1" in capsys.readouterr().out
    assert run(bad) == 1 and "error 1" in capsys.readouterr().out


# ── rendering ──
def test_metadata_defaults_and_zero_baseline():
    d = {"companies": [{"name": "Acme", "categories": {"a": {"count": 3, "index": 165}}}]}
    assert "Index 165 (+65.0%)" in shown(d)
    assert "Index 165 (+65.0%)" in shown({**d, "metadata": None}) and "Index 165 (+65.0%)" in shown({**d, "metadata": {}})
    custom = {**d, "metadata": {"index_baseline": 150, "index_label": "Pay"}}
    assert "Pay 165 (+10.0%)" in shown(custom)
    absolute = {**d, "metadata": {"index_baseline": 0}}
    assert "Index 165" in shown(absolute) and "%" not in shown(absolute)


def test_percent_vs_baseline_formula():
    assert S.percent(118.5, 100) == " (+18.5%)" and S.percent(90, 100) == " (-10.0%)" and S.percent(100, 100) == " (+0.0%)"
    assert S.percent(5, 0) == "" and S.percent(1.0, 3.0) == " (-66.7%)"


def test_null_shapes_pass_validate_then_render():
    d = {"metadata": None, "companies": [{"name": "Acme", "categories": None, "staff": {"count": 4, "index": 110}}, {"name": "Beta", "categories": {}, "ops": {"count": 1}}]}
    assert S.validate(d).errors == ()
    text = S.render(d, S.search(d, "Acme"))
    assert "staff: n=4, Index 110 (+10.0%)" in text
    assert "ops: n=1, Index -" in S.render(d, S.search(d, "Beta"))


def test_zero_count_displayed():
    assert "n=0, Pay index 101 (+1.0%)" in shown(DATA, "Maersk")


def test_text_index_renders_verbatim():
    text = shown(DATA, "Nestle")
    assert "staff: n=30, Pay index private" in text and "%" not in text


def test_null_index_marked_suppressed_absent_index_dash():
    text = shown(DATA, "Novo Nordisk")
    assert "managers: n=4, Pay index N/A*" in text and "engineers: n=120, Pay index 118.5 (+18.5%)" in text
    assert "Pay index -" in shown(DATA, "Café Étoile")


def test_suppression_footnote_only_when_a_row_is_suppressed():
    assert S.FOOTNOTE in shown(DATA, "Novo Nordisk")
    assert S.FOOTNOTE not in shown(DATA, "Maersk") and "N/A" not in shown(DATA, "Café Étoile")


def test_count_only_dataset_not_suppressed():
    d = entry(a={"count": 8}, b={"count": 2})
    text = shown(d)
    assert "a: n=8, Index -" in text and "N/A" not in text


def test_empty_category_row_not_rendered():
    text = shown(entry(empty={}, full={"count": 1}))
    assert "empty" not in text and "full" in text


def test_absolute_salary_dataset_no_percent():
    d = {"metadata": {"index_baseline": 0, "index_label": "DKK"}, "companies": [{"name": "Acme", "categories": {"eng": {"count": 9, "index": 612000}}}]}
    assert S.validate(d).errors == () and "DKK 612000" in shown(d) and "%" not in shown(d)


def test_json_output_round_trips_non_ascii():
    matches = S.search(DATA, "腾讯")
    out = S.as_json(DATA, matches)
    assert "腾讯" in out and "\\u" not in out and json.loads(out)["matches"][0]["company"] == "腾讯"
    assert json.loads(S.as_json(DATA, S.search(DATA, "Maersk")))["matches"][0]["categories"]["all"]["count"] == 0


def test_only_the_matched_company_leaves_the_module():
    text = shown(DATA, "Maersk")
    assert "Nestl" not in text and "Novo" not in text and "腾讯" not in text


# ── names ──
def test_normalize_strips_suffixes_and_noise():
    assert S.normalize_company("Novo Nordisk A/S") == "novonordisk"
    assert S.normalize_company("Acme ApS (Denmark), Copenhagen branch") == "acme"
    for name in ("Foo I/S", "Foo P/S", "Foo K/S", "Foo IVS", "Foo AMBA", "Foo Holding", "Foo Group Denmark", "FOO"):
        assert S.normalize_company(name) == "foo", name
    assert S.normalize_company("A/S") == ""
    assert S.normalize_company("Foo Bar-Baz & Co.") == "foobarbazco"
    assert S.normalize_company("Foo Ltd", legal=()) == "fooltd"                          # the suffix list is configurable


def test_dotted_suffix_equals_undotted():
    assert S.normalize_company("Coop A.m.b.a.") == S.normalize_company("Coop amba") == S.normalize_company("Coop A.M.B.A") == "coop"


def test_non_latin_names_match_exactly():
    assert S.normalize_company("腾讯") == "腾讯" and S.normalize_company("東京 AI") == "東京ai"
    assert S.match_score("腾讯", "腾讯") == 100 and S.match_score("東京 AI", "東京 AI Holding") == 100
    assert [m.entry["name"] for m in S.search(DATA, "腾讯")] == ["腾讯"]


def test_nfc_equivalent_spelling_matches():
    assert S.match_score("Café Étoile", "Café Étoile") == 100
    assert [m.score for m in S.search(DATA, "Café Étoile")] == [100]


def test_nordic_fold_precedes_accent_strip():
    assert S.fold_accents("Mærsk") == "maersk" and S.fold_accents("Ørsted") == "orsted" and S.fold_accents("Åland") == "aaland"
    assert S.fold_accents("Nestlé") == "nestle" and S.fold_accents("Müller") == "muller" and S.fold_accents("Straße") == "strasse"


def test_ascii_query_finds_accented_company():
    assert [m.entry["name"] for m in S.search(DATA, "Nestle")] == ["Nestlé Suisse SA"]
    assert S.match_score("Maersk", "Mærsk") == 85 and S.match_score("Orsted", "Ørsted A/S") == 85
    assert S.match_score("Aaland", "Åland Group") == 85


def test_core_words():
    assert S.core_words("Novo Nordisk A/S") == ["novo", "nordisk"] and S.core_words("A/S") == []
    assert S.core_words("X Y Co") == ["co"]
    assert S.core_words("Café Étoile Holding") == ["café", "étoile"]


def test_score_ladder_values():
    assert S.match_score("Novo Nordisk", "Novo Nordisk A/S") == 100
    assert S.match_score("Novo Nordisk", "Novo Nordisk Foundation") == 80 + int(9 * 11 / 20)
    assert S.match_score("Nordisk", "Novo Nordisk Pharma") == 80 + int(9 * 7 / 16)
    assert S.match_score("Nestle", "Nestlé") == 85
    assert S.match_score("Nestle", "Nestlé International Foundation") == 75
    assert S.match_score("Nordisk Novo", "Novo Nordisk Holding Pharma") == int(30 + 2 / 2 * 40)
    assert S.match_score("Novo Insulin", "Novo Nordisk Pharma") == int(30 + 1 / 2 * 40)
    assert S.match_score("Novo", "Novo Nordisk") == 70
    assert S.match_score("Novo Nordisk", "Novo") == 50
    assert S.match_score("Novo Insulin Pumps", "Novo Nordisk Pharma") == int(30 + 1 / 3 * 40)


def test_short_query_needs_word_overlap():
    assert S.match_score("ab", "Something Unrelated Company") == 0
    assert S.match_score("qz", "qz") == 100
    assert S.match_score("qz", "Qz Industrial Holdings Corporation") == 70       # a core word, not a substring
    assert S.match_score("Novo", "Novo Nordisk Pharma Group") == 70
    assert S.match_score("Coca", "Coca Cola Company Denmark") == 70
    assert S.match_score("Pharma Corporation Giant", "ab") == 0


def test_empty_and_unrelated_score_zero():
    assert S.match_score("", "Acme") == 0 and S.match_score("Acme", "") == 0 and S.match_score("A/S", "Acme") == 0
    assert S.match_score("Zzz", "Acme") == 0 and S.match_score("Totally Different", "Acme") == 0


def test_min_score_and_sort_order():
    d = {"companies": [{"name": "Zed Novo"}, {"name": "Novo Nordisk A/S"}, {"name": "Novo Foods"}, {"name": "Nothing"}, {"name": "Novo"}]}
    got = S.search(d, "Novo Nordisk")
    assert [m.entry["name"] for m in got][0] == "Novo Nordisk A/S" and all(m.score >= S.MIN_SCORE for m in got)
    assert "Nothing" not in [m.entry["name"] for m in got]
    assert got == sorted(got, key=lambda m: (-m.score, m.entry["name"]))
    ties = S.search({"companies": [{"name": "Beta Novo"}, {"name": "Alpha Novo"}]}, "Novo Pharma")
    assert [m.entry["name"] for m in ties] == ["Alpha Novo", "Beta Novo"] and ties[0].score == ties[1].score


def test_city_filter_variants():
    d = {"companies": [{"name": "Acme", "city": "København"}, {"name": "Acme Two", "city": "Aarhus"}, {"name": "Acme Three", "city": None}, {"name": "Acme Four"}]}
    assert [m.entry["name"] for m in S.search(d, "Acme", "kobenhavn")] == ["Acme"]
    assert [m.entry["name"] for m in S.search(d, "Acme", "KØBEN")] == ["Acme"]
    assert [m.entry["name"] for m in S.search(d, "Acme", "aar")] == ["Acme Two"]
    assert len(S.search(d, "Acme")) == 4 and S.search(d, "Acme", "Oslo") == []


def test_no_match_is_reported_not_guessed(tmp_path, capsys):
    path = tmp_path / "b.json"
    path.write_text(json.dumps(DATA))
    got = S.benchmark(tmp_path, "Volkswagen", "Berlin", path=path)
    assert got.text is None and "no benchmark found for 'Volkswagen' in 'Berlin'" in got.note and "shorter name" in got.note
    args = argparse.Namespace(me=str(tmp_path), data=str(path), company="Volkswagen", city="", validate=False, json=False, convert_to=None)
    assert S.main(args) == 1 and "No benchmark found" in capsys.readouterr().out


def test_multiple_candidates_all_returned():
    d = {"companies": [{"name": "Acme Foods A/S"}, {"name": "Acme Robotics ApS"}, {"name": "Acme"}]}
    assert len(S.search(d, "Acme")) == 3


def test_benchmark_returns_the_text_for_a_match(tmp_path):
    (tmp_path / "salary").mkdir()
    (tmp_path / "salary" / "benchmark.json").write_text(json.dumps(DATA, ensure_ascii=False))
    got = S.benchmark(tmp_path, "Novo Nordisk")
    assert got.note == "" and "engineers: n=120" in got.text


# ── numbers ──
@pytest.mark.parametrize("cell,value", [("1 234", 1234.0), ("1 234", 1234.0), ("1 234", 1234.0), ("1 234", 1234.0),
                                        ("42 000,50", 42000.5), (" 7 ", 7.0), ("-12,5", -12.5), (118, 118.0), (118.5, 118.5)])
def test_unicode_space_grouping_parsed(cell, value):
    assert S.parse_number(cell) == value


@pytest.mark.parametrize("cell,value", [("1,234.56", 1234.56), ("1.234,56", 1234.56), ("1,234,567.89", 1234567.89),
                                        ("1.234.567,89", 1234567.89), ("12,0", 12.0), ("108,5", 108.5), ("0.123", 0.123), ("1,234,567", 1234567.0)])
def test_last_separator_is_decimal(cell, value):
    assert S.parse_number(cell) == value


@pytest.mark.parametrize("cell", ["1,234", "1.234", "+1,234", "-1.234", "999,999"])
def test_ambiguous_single_separator_skipped(cell):
    assert S.parse_number(cell) is None


@pytest.mark.parametrize("cell", ["1,23,4.5", "1.234,56,7", "12.34.56", "1,2,3", "1,234.5,6", ",5", "5,", "abc", "", "12 apples", "--1", None, True, [], "1e5"])
def test_malformed_numbers_are_skipped(cell):
    assert S.parse_number(cell) is None


@pytest.mark.parametrize("cell", ["Infinity", "NaN", "-inf", float("inf"), float("nan"), 10 ** 400, "9" * 400])
def test_nonfinite_values_never_emitted(cell):
    assert S.parse_number(cell) is None


# ── ingest ──
def ingest(text, **kw):
    return S.ingest_csv(text, **kw)


def test_ingest_csv_fixture_end_to_end():
    d = S.ingest_csv((FIX / "benchmark.csv").read_text(), source="benchmark")
    by = {c["name"]: c for c in d["companies"]}
    assert by["Acme A/S"]["categories"] == {"engineers": {"count": 1234, "index": 1234.56}, "managers": {"count": 3, "index": None}}
    assert by["Beta Group"]["categories"] == {"all_employees": {"count": 12, "index": 118.5}}
    assert by["Gamma ApS"]["categories"] == {"engineers": {"count": None, "index": None}} and by["Gamma ApS"]["city"] is None
    assert len(d["companies"]) == 3 and S.validate(d) == S.Validation((), ())


def test_ingest_output_validates_and_renders():
    d = ingest("company,city,category,count,index\nAcme,Aarhus,staff,10,120\n")
    assert S.validate(d) == S.Validation((), ()) and "N/A" not in shown(d) and "Index 120 (+20.0%)" in shown(d)
    assert json.dumps(d, allow_nan=False)


def test_count_int_index_float():
    cat = ingest("company,category,count,index\nA,x,12,118\n")["companies"][0]["categories"]["x"]
    assert cat == {"count": 12, "index": 118.0} and type(cat["count"]) is int and type(cat["index"]) is float
    assert ingest("company,category,count,index\nA,x,12.5,118\n")["companies"][0]["categories"]["x"]["count"] is None


def test_free_text_cell_skipped():
    assert ingest("company,category,count\nA,x,see notes\nB,y,5\n")["companies"][0]["categories"] == {}
    assert ingest("company,category,index\nA,x,n/a\n")["companies"][0]["categories"] == {}


def test_empty_pair_omitted_partial_pair_keeps_null():
    d = ingest("company,category,count,index\nA,x,,\nA,y,5,\nA,z,,130\n")
    assert d["companies"][0]["categories"] == {"y": {"count": 5, "index": None}, "z": {"count": None, "index": 130.0}}
    assert S.FOOTNOTE in shown(d, "A")


def test_blank_company_rows_skipped():
    d = ingest("company,count\n,1\n   ,2\n ,3\nReal,4\n")
    assert [c["name"] for c in d["companies"]] == ["Real"] and S.validate(d).errors == ()


def test_ragged_rows():
    d = ingest("company,city,category,count,index\nShort\nA,Oslo\nB,Bergen,staff,3\n")
    assert [c["name"] for c in d["companies"]] == ["Short", "A", "B"]
    assert d["companies"][0]["categories"] == {} and d["companies"][0]["city"] is None
    assert d["companies"][2]["categories"] == {"staff": {"count": 3, "index": None}}
    assert S.validate(d).errors == ()


def test_empty_ingest_fails_without_writing(tmp_path):
    source, out = tmp_path / "s.csv", tmp_path / "out.json"
    for text in ("company,city\n", "company,city\n,Oslo\n \n", ""):
        source.write_text(text)
        with pytest.raises(S.SalaryDataError, match="No data could be parsed|needs a 'company' column"):
            S.convert(source, out)
    assert not out.exists()
    source.write_text("name,count\nA,1\n")
    with pytest.raises(S.SalaryDataError, match="needs a 'company' column"):
        S.convert(source, out)


def test_convert_writes_utf8_json_that_loads_back(tmp_path):
    source, out = tmp_path / "s.csv", tmp_path / "out.json"
    source.write_bytes("﻿company,city,category,count,index\nÆgir Ørsted,København,staff,5,\"110,5\"\n".encode())
    S.convert(source, out)
    assert "Ægir Ørsted" in out.read_text(encoding="utf-8") and S.load(out)["companies"][0]["name"] == "Ægir Ørsted"


def test_metadata_defaults_and_overrides():
    meta = ingest("company,count\nA,1\n", source="survey")["metadata"]
    assert meta == {"source": "survey", "index_baseline": 100.0, "index_label": "Index", "description": "Index 100 = baseline"}
    assert type(meta["index_baseline"]) is float
    meta = ingest("company,count\nA,1\n", source="s", baseline=150, label="Pay", description="mine")["metadata"]
    assert (meta["index_baseline"], meta["index_label"], meta["description"]) == (150.0, "Pay", "mine")


def test_bare_count_index_pair_is_one_category():
    d = ingest("company,count,index\nA,5,110\n")
    assert list(d["companies"][0]["categories"]) == ["all_employees"] and S.FOOTNOTE not in shown(d)


def test_category_names_become_underscored():
    d = ingest("company,category,count\nA,Senior  Engineers-EU,5\n")
    assert list(d["companies"][0]["categories"]) == ["Senior_Engineers_EU"]


def test_lone_count_not_treated_as_index():
    cat = ingest("company,count\nA,40\n")["companies"][0]["categories"]["all_employees"]
    assert cat == {"count": 40} and "index" not in cat
    assert "%" not in shown(ingest("company,count\nAcme,40\n"))


def test_a_company_in_several_rows_is_one_entry_with_several_categories():
    d = ingest("company,city,category,count\nAcme,Oslo,a,1\nacme,OSLO,b,2\nAcme,Bergen,a,3\n")
    assert [(c["city"], list(c["categories"])) for c in d["companies"]] == [("Oslo", ["a", "b"]), ("Bergen", ["a"])]


def test_csv_data_is_found_and_used_by_the_loop(tmp_path):
    (tmp_path / "salary").mkdir()
    (tmp_path / "salary" / "benchmark.csv").write_bytes((FIX / "benchmark.csv").read_bytes())
    got = S.benchmark(tmp_path, "Beta")
    assert got.text and "all_employees: n=12, Index 118.5 (+18.5%)" in got.text

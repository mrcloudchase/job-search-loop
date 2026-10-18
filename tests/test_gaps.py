"""gaps.py: arithmetic over saved fits, and a plan that can't promise what it can't know."""
from __future__ import annotations

import json
from pathlib import Path

from fake_server import FakeServer
from jobloop import gaps
from jobloop.turns import Api, Turns

PROFILE = (Path(__file__).parent.parent / "fixtures" / "profile.md").read_text()


def save(folder, name, total, gap_terms, location="PASS", language="PASS"):
    reqs = [[t, "gap", ""] for t in gap_terms] + [["Python", "match", "Python"]]
    (folder / f"{name}.json").write_text(json.dumps({"title": name, "company": "Co", "total": total, "location": location,
                                                     "language": language, "requirements": reqs}))


def test_gaps_are_counted_across_fits_and_ordered_by_how_many_postings_ask(tmp_path):
    save(tmp_path, "a", 80, ["Scala", "Spark"])
    save(tmp_path, "b", 60, ["scala", "Kubernetes"])
    save(tmp_path, "c", 50, ["Scala  ", "Spark"])
    table = gaps.tally(tmp_path)
    assert [(g["gap"], g["postings"]) for g in table] == [("Scala", 3), ("Spark", 2), ("Kubernetes", 1)]
    assert table[0]["weight"] == 80 + 60 + 50 and len(table[0]["examples"]) == 3


def test_a_weak_fit_never_makes_a_gap_look_important(tmp_path):
    save(tmp_path, "weak", 44, ["COBOL"])
    save(tmp_path, "ok", 45, ["Scala"])
    assert [g["gap"] for g in gaps.tally(tmp_path)] == ["Scala"]


def test_a_posting_that_failed_a_gate_is_not_counted(tmp_path):
    save(tmp_path, "far", 90, ["COBOL"], location="FAIL")
    save(tmp_path, "lang", 90, ["Latin"], language="FAIL")
    save(tmp_path, "flag", 90, ["Scala"], location="FLAG")
    assert [g["gap"] for g in gaps.tally(tmp_path)] == ["Scala"]


def test_matches_are_not_gaps_and_an_empty_folder_is_no_gaps(tmp_path):
    assert gaps.tally(tmp_path) == []
    save(tmp_path, "a", 80, [])
    assert gaps.tally(tmp_path) == []


def test_a_plan_must_cover_every_gap_and_carry_no_links():
    top = [{"gap": "Scala"}, {"gap": "Spark"}]
    assert "missing: Spark" in gaps.check("1. Scala first.", top)[0]
    assert "Remove the links" in gaps.check("Scala, Spark. See https://scala-lang.org", top)[0]
    assert gaps.check("Scala, then SPARK.", top) == []


def test_the_plan_turn_sends_failures_back_and_gets_the_gaps_as_a_file(tmp_path):
    server = FakeServer([{"files": {"plan.md": "Scala only"}}, {"files": {"plan.md": "Scala, then Spark"}}])
    top = [{"gap": "Scala", "postings": 3}, {"gap": "Spark", "postings": 2}]
    with Turns(Api("http://x", server.transport()), tmp_path / "l.jsonl") as turns:
        assert gaps.plan(turns, "codex", PROFILE, top) == "Scala, then Spark"
    assert {f["filename"] for f in server.requests[0]["input"][1:]} == {"profile.md", "gaps.json"}
    assert "missing: Spark" in server.requests[1]["input"][0]["text"]

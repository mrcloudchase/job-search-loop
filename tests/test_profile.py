"""profile.py: nothing in the profile may be new."""
from __future__ import annotations

from pathlib import Path

import pytest

from fake_server import FakeServer
from jobloop import profile
from jobloop.turns import Api, Turns

FIXTURES = Path(__file__).parent.parent / "fixtures"
GOOD = (FIXTURES / "profile.md").read_text()
CV = "Alex Fernsby, alex.fernsby@example.com, +1 555 0100, github.com/afernsby-example. Senior Data Engineer 2022. Marlow 2018 2016 2014."
SOURCES = CV + "\n" + GOOD            # the CV and a set of notes that carry everything in the profile


def test_a_profile_made_only_from_its_sources_passes():
    assert profile.check(GOOD, SOURCES) == []


def test_it_must_start_with_a_name_and_have_every_section():
    assert "must start" in profile.check("Alex\n" + GOOD.split("\n", 1)[1], SOURCES)[0]
    failures = profile.check(GOOD.replace("## Languages", "## Spoken").replace("## Preferences", "## Wishes"), SOURCES)
    assert "## Languages, ## Preferences" in failures[0]


def test_an_invented_number_is_refused():
    assert "not in the sources: 9" in profile.check(GOOD.replace("40 million", "9 million"), SOURCES)[0]


def test_an_invented_email_or_link_is_refused():
    bad = GOOD.replace("alex.fernsby@example.com", "alex@fake.io").replace("github.com/afernsby-example", "https://linkedin.com/in/alex")
    failures = profile.check(bad, SOURCES)
    assert "alex@fake.io" in failures[0] and "https://linkedin.com/in/alex" in failures[0]


def test_an_invented_phone_number_is_refused_even_if_each_group_exists_elsewhere():
    sources = SOURCES + " 0100 555 1"
    assert "phone number" in profile.check(GOOD.replace("+1 555 0100", "+1 0100 555"), sources)[0]


def test_a_job_whose_year_was_dropped_is_refused():
    failures = profile.check(GOOD.replace("2018", "").replace("2016", ""), SOURCES + " 2018 2016")
    assert any("2016, 2018" in f or "2018" in f for f in failures)


def build(tmp_path, replies):
    server = FakeServer(replies)
    with Turns(Api("http://x", server.transport()), tmp_path / "log.jsonl") as turns:
        return profile.build(turns, "claude-code", {"1_cv.md": CV, "2_notes.md": GOOD}, rounds=2), server


def test_build_returns_the_profile_and_sends_failures_back(tmp_path):
    result, server = build(tmp_path, [{"files": {"profile.md": GOOD.replace("40 million", "9 million")}}, {"files": {"profile.md": GOOD}}])
    assert result == GOOD and "not in the sources" in server.requests[1]["input"][0]["text"]
    assert server.requests[1]["previous_response_id"] == "resp_1"


def test_build_gives_up_when_the_harness_keeps_inventing(tmp_path):
    result, server = build(tmp_path, [{"files": {"profile.md": GOOD.replace("40 million", "9 million")}}] * 3)
    assert result is None and len(server.requests) == 3


def test_the_sources_travel_as_files(tmp_path):
    _, server = build(tmp_path, [{"files": {"profile.md": GOOD}}])
    assert {f["filename"] for f in server.requests[0]["input"][1:]} == {"1_cv.md", "2_notes.md"}


def test_an_existing_profile_is_never_overwritten_without_force(tmp_path):
    import argparse
    out = tmp_path / "profile.md"
    out.write_text("mine")
    with pytest.raises(SystemExit, match="--force"):
        profile.main(argparse.Namespace(out=str(out), force=False, cv=[], notes=None))
    assert out.read_text() == "mine"

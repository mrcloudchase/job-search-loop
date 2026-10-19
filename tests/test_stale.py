"""stale.py: which silent applications are offered for closing, and that nothing closes without a yes for that row."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path

import pytest

from jobloop import archive, stale, tracker

TODAY = date(2026, 10, 19)


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def put_rows(me: Path, *rows: dict) -> Path:
    path = me / "tracker.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(tracker.HEADER)
        for row in rows:
            w.writerow([row.get(c, "") for c in tracker.HEADER])
    return path


def app(company, status="applied", days=70, **kw):
    return {"company": company, "role": "Dev", "status": status, "date": ago(days), "sector": "Energy", **kw}


@pytest.fixture
def me(tmp_path):
    return tmp_path / "me"


def keys(selection):
    return [c.row.company for c in selection.candidates]


def select(me, **kw):
    return stale.select(me, tracker.read_rows(me / "tracker.csv"), TODAY, **kw)


def test_only_applied_and_interview_rows_qualify(me):
    put_rows(me, app("A", "applied"), app("B", "interview"), app("C", "offer"), app("D", "drafted"), app("E", "hired"),
             app("F", "rejected"), app("G", "no_response"), app("H", "withdrawn"), app("I", "offer_declined"))
    assert sorted(keys(select(me))) == ["A", "B"]


def test_drafted_rows_never_swept(me):
    put_rows(me, app("Old draft", "drafted", days=900))
    assert select(me).candidates == () and select(me).skipped == ()


def test_default_sixty_day_threshold_and_override(me):
    put_rows(me, app("Fifty nine", days=59), app("Sixty", days=60))
    assert keys(select(me)) == ["Sixty"]
    assert sorted(keys(select(me, threshold=30))) == ["Fifty nine", "Sixty"]
    assert keys(select(me, threshold=61)) == []


@pytest.mark.parametrize("bad", [0, -5, True, "60", 2.5])
def test_a_threshold_that_is_not_a_positive_whole_number_is_refused(me, bad):
    put_rows(me, app("A"))
    with pytest.raises(ValueError, match="whole number of days"):
        select(me, threshold=bad)


def test_the_most_quiet_application_comes_first_with_its_days(me):
    put_rows(me, app("A", days=65), app("B", days=200), app("C", days=90))
    sel = select(me)
    assert [(c.row.company, c.days_quiet) for c in sel.candidates] == [("B", 200), ("C", 90), ("A", 65)]


def test_a_row_with_no_parseable_date_is_skipped_with_a_note_not_guessed(me):
    put_rows(me, app("Bad", date="last spring"), app("Good"))
    sel = select(me)
    assert keys(sel) == ["Good"] and len(sel.skipped) == 1 and "bad__dev" in sel.skipped[0]


def test_a_recent_dated_note_resets_the_clock(me):
    put_rows(me, app("Chased", days=100, notes=f"{ago(5)} recruiter replied"), app("Silent", days=100))
    assert keys(select(me)) == ["Silent"]


def test_an_interview_stage_in_outcome_md_resets_the_clock(me):
    put_rows(me, app("Spoke", "interview", days=100), app("Silent", days=100))
    archive.open_application(me, "Spoke", "Dev")
    archive.record_stage(me, "spoke__dev", on=TODAY - timedelta(days=10), stage="phone_screen")
    assert keys(select(me)) == ["Silent"]


def test_a_stage_dated_in_the_future_is_a_plan_not_contact(me):
    put_rows(me, app("Planned", "interview", days=100))
    archive.open_application(me, "Planned", "Dev")
    archive.record_stage(me, "planned__dev", on=TODAY + timedelta(days=3), stage="final_round")
    assert keys(select(me)) == ["Planned"]


def test_an_unreadable_outcome_md_leaves_the_row_alone_and_says_so(me):
    put_rows(me, app("Broken"))
    folder = archive.folder_for(me, "broken__dev", create=True)
    (folder / "outcome.md").write_text("not the format\n")
    sel = select(me)
    assert sel.candidates == () and "broken__dev" in sel.skipped[0] and "outcome.md" in sel.skipped[0]


def test_selecting_writes_nothing(me):
    path = put_rows(me, app("A"), app("B", days=10))
    before = path.read_bytes()
    select(me)
    assert path.read_bytes() == before and not (me / "applications").exists()


# ── resolving ──
def test_no_write_without_explicit_confirmation(me):
    path = put_rows(me, app("A"), app("B"))
    before = path.read_bytes()
    result = stale.resolve(me, select(me), TODAY, lambda question: False)
    assert path.read_bytes() == before and result.resolved == () and len(result.kept) == 2
    assert not (me / "applications").exists()


@pytest.mark.parametrize("answer", ["yes", "y", 1, None, "True"])
def test_only_a_true_answer_counts_as_a_confirmation(me, answer):
    path = put_rows(me, app("A"))
    before = path.read_bytes()
    stale.resolve(me, select(me), TODAY, lambda q: answer)
    assert path.read_bytes() == before


def test_each_row_is_asked_about_separately_and_only_the_confirmed_one_is_closed(me):
    put_rows(me, app("A", days=70), app("B", days=90), app("C", days=80))
    asked = []

    def confirm(question):
        asked.append(question)
        return "B - Dev" in question

    result = stale.resolve(me, select(me), TODAY, confirm)
    status = {r.company: r.status for r in tracker.read_rows(me / "tracker.csv")}
    assert status == {"A": "applied", "B": "no_response", "C": "applied"}
    assert len(asked) == 3 and result.resolved == ("b__dev",)


def test_a_confirmed_row_gets_no_response_a_marker_and_keeps_every_other_column(me):
    put_rows(me, app("A", days=70, contact_person="Ann", channel="portal", notes=f"{ago(70)} sent", fit_rating="71"))
    stale.resolve(me, select(me), TODAY, lambda q: True)
    row = tracker.read_rows(me / "tracker.csv")[0]
    assert row.status == "no_response" and "stale resolved after 70 days quiet" in row.notes
    assert row.notes.startswith(f"{ago(70)} sent; {TODAY.isoformat()} ")
    assert (row.date, row.company, row.role, row.sector, row.contact_person, row.channel, row.fit_rating) == (
        ago(70), "A", "Dev", "Energy", "Ann", "portal", "71")


def test_a_confirmed_row_settles_the_archive_outcome_too(me):
    put_rows(me, app("A", days=70))
    stale.resolve(me, select(me), TODAY, lambda q: True)
    outcome = archive.read_outcome(me, "a__dev")
    assert outcome.status == "no_response" and outcome.resolved == TODAY
    assert any("stale resolved after 70 days quiet" in n.text for n in outcome.notes)


def test_resolution_goes_through_the_tracker_person_path(me, monkeypatch):
    put_rows(me, app("A"))
    calls = []
    real = tracker.set_status
    monkeypatch.setattr(tracker, "set_status", lambda *a, **kw: calls.append(kw) or real(*a, **kw))
    stale.resolve(me, select(me), TODAY, lambda q: True)
    assert calls and calls[0]["by_person"] is True


def test_a_row_the_tracker_refuses_is_reported_and_the_rest_still_go_on(me):
    put_rows(me, app("Real"))
    ghost = stale.Candidate(tracker.Row(company="Ghost", role="Dev", status="applied", date=ago(90)), 90, 0)
    sel = stale.Selection((ghost, *select(me).candidates), ())
    result = stale.resolve(me, sel, TODAY, lambda q: True)
    assert result.resolved == ("real__dev",) and len(result.refused) == 1 and "ghost__dev" in result.refused[0]


def test_calibration_is_offered_at_three_resolutions_and_not_at_two(me):
    put_rows(me, app("A"), app("B"), app("C"))
    three = stale.resolve(me, select(me), TODAY, lambda q: True)
    assert three.calibration_due
    put_rows(me, app("A"), app("B"))
    assert not stale.resolve(me, select(me), TODAY, lambda q: True).calibration_due


# ── the command ──
def args(me, days=60):
    p = argparse.ArgumentParser()
    stale.add_arguments(p)
    return p.parse_args(["--me", str(me), "--days", str(days)])


def test_an_empty_sweep_says_so_asks_nothing_and_writes_nothing(me, capsys):
    path = put_rows(me, app("Fresh", days=3))
    before = path.read_bytes()

    def never(question):
        raise AssertionError("asked about nothing")

    assert stale.main(args(me), TODAY, never) == 0
    assert "quiet 60 days or more" in capsys.readouterr().out and path.read_bytes() == before


def test_the_sweep_asks_with_days_quiet_and_skipping_every_row_changes_nothing(me, capsys):
    path = put_rows(me, app("A", days=75))
    before, asked = path.read_bytes(), []
    assert stale.main(args(me), TODAY, lambda q: asked.append(q) and False) == 0
    assert "75 days quiet" in asked[0] and "applied" in asked[0]
    assert "left as they were: 1" in capsys.readouterr().out and path.read_bytes() == before


def test_the_command_refuses_a_bad_threshold_before_anything_is_asked(me, capsys):
    put_rows(me, app("A"))
    assert stale.main(args(me, days=0), TODAY, lambda q: True) == 2
    assert "whole number of days" in capsys.readouterr().out


def test_the_command_has_no_yes_to_all_option():
    p = argparse.ArgumentParser()
    stale.add_arguments(p)
    options = {o for a in p._actions for o in a.option_strings}
    assert options == {"-h", "--help", "--me", "--days"}

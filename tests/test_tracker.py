"""tracker.py: what the loop may write, and the one thing it must never say."""
from __future__ import annotations

import pytest

from jobloop import tracker


def test_the_loop_can_record_found_and_drafted(tmp_path):
    f = tmp_path / "t.csv"
    tracker.record(f, "acme_dev", "found", company="Acme", role="Dev")
    tracker.record(f, "acme_dev", "drafted", cv_file="cv.pdf")
    (row,) = tracker.rows(f)
    assert row["status"] == "drafted" and row["company"] == "Acme" and row["cv_file"] == "cv.pdf"


@pytest.mark.parametrize("status", ["applied", "interview", "offer", "rejected", "withdrawn", "anything"])
def test_the_loop_can_never_record_that_an_application_was_sent_or_anything_after(tmp_path, status):
    with pytest.raises(ValueError, match="a person sets the rest"):
        tracker.record(tmp_path / "t.csv", "k", status)
    assert not (tmp_path / "t.csv").exists()


def test_a_person_can_move_an_application_on(tmp_path):
    f = tmp_path / "t.csv"
    tracker.record(f, "k", "drafted")
    tracker.set_status(f, "k", "applied", "sent on the company site")
    assert tracker.rows(f)[0]["status"] == "applied" and tracker.rows(f)[0]["notes"] == "sent on the company site"


def test_setting_an_unknown_application_or_status_is_refused(tmp_path):
    f = tmp_path / "t.csv"
    tracker.record(f, "k", "drafted")
    with pytest.raises(SystemExit, match="no application"):
        tracker.set_status(f, "nope", "applied")
    with pytest.raises(SystemExit, match="status must be"):
        tracker.set_status(f, "k", "hired")


def test_recording_again_keeps_other_columns_and_the_first_date(tmp_path):
    f = tmp_path / "t.csv"
    tracker.record(f, "k", "found", company="Acme", fit="72")
    first = tracker.rows(f)[0]["date"]
    tracker.record(f, "k", "drafted")
    row = tracker.rows(f)[0]
    assert row["company"] == "Acme" and row["fit"] == "72" and row["date"] == first and len(tracker.rows(f)) == 1


def test_commas_and_quotes_survive_the_csv(tmp_path):
    f = tmp_path / "t.csv"
    tracker.record(f, "k", "found", company='Acme, "the best" Ltd')
    assert tracker.rows(f)[0]["company"] == 'Acme, "the best" Ltd'


def test_the_apply_loop_records_drafted_and_nothing_else(tmp_path):
    from jobloop import apply
    from test_apply import Checker, FakeServer, args, drafts
    from jobloop.turns import Api
    server = FakeServer([{"files": drafts()}, {"answer": "ok"}, {"files": drafts()}])
    apply.Loop(args(tmp_path / "o", tracker=str(tmp_path / "t.csv"), key="brightwell"), Api("http://x", server.transport()), Checker()).run()
    (row,) = tracker.rows(tmp_path / "t.csv")
    assert row["key"] == "brightwell" and row["status"] == "drafted"

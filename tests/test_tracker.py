"""tracker.py: the status vocabulary, never-backwards, the loop writing only `drafted`, CSV safety, atomic locked writes."""
from __future__ import annotations

import argparse
import csv
import inspect
import io
import itertools
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pytest

from jobloop import archive, tracker

TODAY = date(2026, 10, 19)
NINE = ("drafted", "applied", "interview", "offer", "hired", "rejected", "no_response", "offer_declined", "withdrawn")
HEADER_LINE = "date,company,sector,role,role_type,channel,status,contact_person,fit_rating,notes,cv_file,cover_letter_file,source,deadline\n"


@pytest.fixture
def path(tmp_path):
    return tmp_path / "me" / "tracker.csv"


def draft(path, company="Acme", role="ML Engineer", today=TODAY, **kw):
    kw.setdefault("cv_file", "applications/acme/cv.tex")
    kw.setdefault("cover_letter_file", "applications/acme/cover.tex")
    return tracker.record_drafted(path, company=company, role=role, today=today, **kw)


def person(path, name, status, on=TODAY, **kw):
    return tracker.set_status(path, name, status, by_person=True, on=on, **kw)


def row_for(path, company="Acme"):
    return [r for r in tracker.read_rows(path) if r.company == company][-1]


def seed(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── vocabulary ──
def test_status_vocabulary_is_exactly_the_nine_canonical_values():
    assert tracker.STATUSES == NINE


def test_final_set_matches_vocabulary():
    assert tracker.FINAL == {"hired", "rejected", "no_response", "offer_declined", "withdrawn"}
    assert tracker.OPEN == {"drafted", "applied", "interview", "offer"}
    assert tracker.OPEN | tracker.FINAL == set(NINE) and not tracker.OPEN & tracker.FINAL


def test_open_is_complement_of_final_and_unknown_is_open():
    for status in NINE:
        assert tracker.is_open(status) is (status not in tracker.FINAL)
    for unknown in ("", "ghosted", "in_progress", "interview_only", "APPLIEDD"):
        assert tracker.is_open(unknown)


def test_drafted_is_open_but_never_chaseable():
    assert tracker.is_open("drafted") and not tracker.is_chaseable("drafted")
    assert all(tracker.is_chaseable(s) for s in ("applied", "interview", "offer", "ghosted"))
    assert not any(tracker.is_chaseable(s) for s in tracker.FINAL)


def test_legacy_space_spellings_read_as_final_canonical():
    assert tracker.normalize_status("no response") == "no_response" and tracker.normalize_status("offer declined") == "offer_declined"
    assert not tracker.is_open("no response") and not tracker.is_open("Offer  Declined")


def test_status_normalisation_is_case_insensitive():
    assert tracker.normalize_status(" Applied ") == "applied" and tracker.normalize_status("REJECTED") == "rejected"
    assert tracker.normalize_status("applied now") is None


def test_writer_never_emits_space_spellings(path):
    draft(path)
    person(path, "acme__ml_engineer", "no response")
    text = path.read_text()
    assert "no_response" in text and "no response" not in text


def test_archive_enum_value_rejected_in_tracker_column(path):
    draft(path)
    for word in ("in_progress", "interview_only", "ghosted", ""):
        with pytest.raises(tracker.StatusRefused):
            person(path, "acme__ml_engineer", word)
    assert row_for(path).status == "drafted"


# ── never backwards ──
def test_status_regression_is_rejected_for_every_pair():
    for old, new in itertools.product(NINE, NINE):
        if old in tracker.FINAL and old != new:
            with pytest.raises(tracker.StatusRefused):
                tracker.advance(old, new)
        elif tracker.RANK[new] < tracker.RANK[old]:
            with pytest.raises(tracker.StatusRefused, match="backwards"):
                tracker.advance(old, new)
        else:
            assert tracker.advance(old, new) == new


def test_forward_jumps_are_allowed_and_same_status_is_a_no_op():
    assert tracker.advance("drafted", "interview") == "interview"
    assert tracker.advance("applied", "rejected") == "rejected"
    assert tracker.advance("interview", "interview") == "interview"
    assert tracker.advance("ghosted", "applied") == "applied"


def test_no_final_status_can_become_another_final_or_reopen():
    for old in tracker.FINAL:
        for new in set(NINE) - {old}:
            with pytest.raises(tracker.StatusRefused, match="final"):
                tracker.advance(old, new)


def test_a_refused_move_leaves_the_file_byte_identical(path):
    draft(path)
    person(path, "acme__ml_engineer", "interview")
    before = path.read_bytes()
    with pytest.raises(tracker.StatusRefused):
        person(path, "acme__ml_engineer", "applied")
    assert path.read_bytes() == before


def test_final_row_is_never_overwritten_and_new_row_appended(path):
    draft(path)
    person(path, "acme__ml_engineer", "rejected", note="no feedback")
    final = path.read_text().splitlines()[1]
    result = draft(path, today=date(2026, 12, 1))
    lines = path.read_text().splitlines()
    assert result.action == "added_after_final" and len(lines) == 3 and lines[1] == final
    assert [r.status for r in tracker.read_rows(path)] == ["rejected", "drafted"]
    assert [r.status for r in tracker.read_rows(path)] == ["rejected", "drafted"]


def test_a_person_can_advance_the_open_row_while_an_old_final_row_stays_final(path):
    draft(path)
    person(path, "acme__ml_engineer", "no_response")
    draft(path, today=date(2026, 12, 1))
    person(path, "acme__ml_engineer", "applied", on=date(2026, 12, 3))
    assert [(r.status, r.date) for r in tracker.read_rows(path)] == [("no_response", "2026-10-19"), ("applied", "2026-12-03")]


def test_setting_the_final_status_a_row_already_holds_changes_nothing(path):
    draft(path)
    person(path, "acme__ml_engineer", "hired", note="signed")
    before = path.read_bytes()
    person(path, "acme__ml_engineer", "hired", on=date(2027, 1, 1), note="again")
    assert path.read_bytes() == before


# ── the loop writes only drafted ──
def test_loop_cannot_write_any_status_except_drafted(path):
    assert "status" not in inspect.signature(tracker.record_drafted).parameters
    for status in ("applied", "hired", "offer_declined", "interview"):
        with pytest.raises(TypeError):
            draft(path, status=status)
    assert not path.exists()
    draft(path)
    assert {r.status for r in tracker.read_rows(path)} == {"drafted"}


def test_the_loop_refreshing_an_applied_row_never_changes_its_status(path):
    draft(path)
    person(path, "acme__ml_engineer", "interview")
    for _ in range(3):
        assert draft(path).action == "updated"
    assert row_for(path).status == "interview"
    assert {r.status for r in tracker.read_rows(path)} <= {"interview"}


def test_a_status_change_without_a_person_is_refused(path):
    draft(path)
    before = path.read_bytes()
    for flag in (False, None, 1, "yes", "True"):
        with pytest.raises(tracker.StatusRefused, match="person"):
            tracker.set_status(path, "acme__ml_engineer", "applied", by_person=flag, on=TODAY)
    with pytest.raises(TypeError):
        tracker.set_status(path, "acme__ml_engineer", "applied", on=TODAY)
    with pytest.raises(tracker.StatusRefused):
        tracker.add_external(path, company="Beta", role="Dev", applied_on=TODAY, by_person=False)
    assert path.read_bytes() == before


def test_the_loop_side_function_has_no_way_to_name_a_status_even_through_a_hostile_company(path):
    draft(path, company="x\nhired", role="applied", sector="offer", role_type="withdrawn")
    assert {r.status for r in tracker.read_rows(path)} == {"drafted"}


# ── new rows and redrafts ──
def test_missing_tracker_created_with_canonical_header(path):
    tracker.ensure_tracker(path)
    assert path.read_text() == HEADER_LINE and tracker.HEADER[-1] == "deadline" and len(tracker.HEADER) == 14
    assert tracker.read_rows(path) == ()


def test_new_row_status_drafted_with_document_paths(path):
    result = draft(path, fit_rating=72, source="https://acme.example/job/1", deadline="2026-11-01", channel="online")
    row = tracker.read_rows(path)[0]
    assert result.action == "added" and result.key == "acme__ml_engineer" == row.key
    assert (row.date, row.status, row.cv_file, row.cover_letter_file) == ("2026-10-19", "drafted", "applications/acme/cv.tex", "applications/acme/cover.tex")
    assert (row.fit_rating, row.source, row.deadline, row.channel) == ("72", "https://acme.example/job/1", "2026-11-01", "online")
    assert (row.sector, row.role_type, row.contact_person, row.notes) == ("", "", "", "")


def test_unstated_optional_fields_stay_empty_and_bad_values_are_refused(path):
    for bad in ({"fit_rating": "72/100"}, {"fit_rating": "great"}, {"fit_rating": 101}, {"fit_rating": -1}, {"fit_rating": True},
                {"deadline": "soon"}, {"deadline": "2026-13-45"}, {"deadline": "20261101"}, {"channel": "carrier pigeon"},
                {"cv_file": "a,b.tex"}, {"cover_letter_file": 'a"b.tex'}):
        with pytest.raises(ValueError):
            draft(path, **bad)
    assert not path.exists()


def test_fit_rating_must_be_bare_number():
    assert [tracker.validate_fit_rating(v) for v in (0, 100, "72", "72.5", None, "")] == ["0", "100", "72", "72.5", "", ""]


def test_redraft_open_row_keeps_status_and_date_unless_drafted(path):
    draft(path, fit_rating=60, source="https://a.example/1", deadline="2026-11-01")
    draft(path, today=date(2026, 10, 25), cv_file="new/cv.tex", cover_letter_file="new/cover.tex", fit_rating=75, source="https://a.example/2", deadline="2026-11-05")
    row = row_for(path)
    assert (row.status, row.date) == ("drafted", "2026-10-25")
    assert (row.cv_file, row.cover_letter_file, row.fit_rating, row.source, row.deadline) == ("new/cv.tex", "new/cover.tex", "75", "https://a.example/2", "2026-11-05")
    person(path, "acme__ml_engineer", "applied", on=date(2026, 10, 26))
    draft(path, today=date(2026, 11, 2), cv_file="newer/cv.tex")
    row = row_for(path)
    assert (row.status, row.date, row.cv_file) == ("applied", "2026-10-26", "newer/cv.tex")
    assert len(tracker.read_rows(path)) == 1


def test_redraft_without_deadline_keeps_existing_deadline(path):
    draft(path, deadline="2026-11-01", fit_rating=60, source="https://a.example/1")
    draft(path)
    row = row_for(path)
    assert (row.deadline, row.fit_rating, row.source) == ("2026-11-01", "60", "https://a.example/1")


def test_redraft_matches_company_and_role_case_insensitively(path):
    draft(path, company="Novo Nordisk A/S")
    assert draft(path, company="NOVO nordisk a/s", role="ml  engineer").action == "updated"
    assert len(tracker.read_rows(path)) == 1


def test_redrafted_marker_is_not_a_dated_contact(path):
    draft(path, today=date(2026, 9, 1))
    person(path, "acme__ml_engineer", "applied", on=date(2026, 9, 2))
    draft(path, today=date(2026, 10, 18))
    draft(path, today=date(2026, 10, 19))
    row = row_for(path)
    assert row.notes.split("; ").count("redrafted") == 1 and not re.search(r"\d{4}-\d{2}-\d{2}", row.notes)
    assert tracker.days_quiet(row, TODAY) == (TODAY - date(2026, 9, 2)).days


def test_leaving_drafted_sets_date_to_submission_date(path):
    draft(path, today=date(2026, 10, 1))
    person(path, "acme__ml_engineer", "applied", on=date(2026, 10, 5))
    assert row_for(path).date == "2026-10-05"
    draft(path, company="Beta", today=date(2026, 10, 1), role="Dev")
    person(path, "beta__dev", "rejected", on=date(2026, 10, 9))
    assert row_for(path, "Beta").date == "2026-10-09"
    person(path, "acme__ml_engineer", "interview", on=date(2026, 10, 12))
    assert row_for(path).date == "2026-10-05"


def test_status_update_appends_dated_note(path):
    draft(path)
    person(path, "acme__ml_engineer", "applied", on=date(2026, 10, 5), note="sent via portal")
    person(path, "acme__ml_engineer", "interview", on=date(2026, 10, 12), note="call booked")
    assert row_for(path).notes == "2026-10-05 sent via portal; 2026-10-12 call booked"


def test_unknown_key_is_reported_and_nothing_changes(path):
    draft(path)
    before = path.read_bytes()
    with pytest.raises(tracker.UnknownKey):
        person(path, "nope__nope", "applied")
    assert path.read_bytes() == before


# ── file safety ──
def test_update_preserves_all_other_fields_and_unknown_columns(path):
    seed(path, HEADER_LINE.rstrip() + ",extra\n" + '2026-10-01,Acme,Pharma,ML Engineer,perm,portal,applied,Jo,70,"a, quoted note",cv.tex,cl.tex,https://x,2026-11-01,keep me\n')
    person(path, "acme__ml_engineer", "interview", note="call")
    line = list(csv.reader(io.StringIO(path.read_text())))[1]
    assert line[:6] == ["2026-10-01", "Acme", "Pharma", "ML Engineer", "perm", "portal"]
    assert line[6] == "interview" and line[9] == "a, quoted note; 2026-10-19 call" and line[10:] == ["cv.tex", "cl.tex", "https://x", "2026-11-01", "keep me"]


def test_update_leaves_row_order_and_other_rows_unchanged(path):
    other = b'2026-09-01,Zed,,Dev,,,applied,,,"odd, quoted",,,,\r\n'
    path.parent.mkdir(parents=True)
    path.write_bytes(HEADER_LINE.encode() + other + b"2026-09-02,Acme,,ML Engineer,,,applied,,,,,,,\n" + b"weird line\n")
    person(path, "acme__ml_engineer", "interview")
    data = path.read_bytes()
    assert data.startswith(HEADER_LINE.encode() + other) and data.endswith(b"weird line\n")
    assert data.splitlines()[2].split(b",")[6] == b"interview"


def test_other_rows_byte_identical_after_write(path):
    draft(path, company="One", role="A")
    draft(path, company="Two", role="B")
    first = path.read_text().splitlines(keepends=True)
    draft(path, company="Three", role="C")
    draft(path, company="Two", role="B", fit_rating=50)
    now = path.read_text().splitlines(keepends=True)
    assert now[:2] == first[:2] and len(now) == 4


def test_legacy_header_gets_deadline_column_and_rows_untouched(path):
    legacy = HEADER_LINE.replace(",deadline", "")
    rows = "2026-09-01,Zed,,Dev,,,applied,,,,,,\n2026-09-02,Acme,,ML Engineer,,,applied,,,,,,\n"
    seed(path, legacy + rows)
    assert [r.deadline for r in tracker.read_rows(path)] == ["", ""] and path.read_text() == legacy + rows
    draft(path, company="New", role="Role", deadline="2026-11-01")
    lines = path.read_text().splitlines(keepends=True)
    assert lines[0] == HEADER_LINE and lines[1:3] == rows.splitlines(keepends=True) and lines[3].rstrip().endswith(",2026-11-01")


def test_thirteen_field_row_reads_empty_deadline(path):
    seed(path, HEADER_LINE.replace(",deadline", "") + "2026-09-02,Acme,,ML Engineer,,,applied,,,n,cv,cl,src\n")
    (row,) = tracker.read_rows(path)
    assert row.deadline == "" and row.source == "src" and tracker.deadline_flag(row.deadline, TODAY) == ""
    person(path, "acme__ml_engineer", "interview")
    assert path.read_text().splitlines()[1].count(",") == 13


def test_deadline_is_last_column():
    assert tracker.HEADER[-1] == "deadline" and tracker.HEADER[:13] == tuple(HEADER_LINE.split(",")[:13])


def test_a_header_missing_a_required_column_is_refused_untouched(path):
    seed(path, "date,company,role\n2026-01-01,A,B\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="header"):
        draft(path)
    assert path.read_bytes() == before


def test_a_row_with_a_stray_comma_is_not_rewritten_but_others_still_work(path):
    seed(path, HEADER_LINE + "2026-09-02,Acme,,ML Engineer,,,applied,,,a,b,c,d,e,f,g\n")
    with pytest.raises(ValueError, match="more fields"):
        person(path, "acme__ml_engineer", "interview")
    assert "applied" in path.read_text()


def test_crlf_files_stay_crlf(path):
    seed(path, HEADER_LINE.replace("\n", "\r\n"))
    path.write_bytes(path.read_bytes())
    draft(path)
    assert path.read_bytes().count(b"\r\n") == 2 and b"\n" not in path.read_bytes().replace(b"\r\n", b"")


def test_a_file_without_a_final_newline_gets_one_before_a_new_row(path):
    seed(path, HEADER_LINE + "2026-09-02,Zed,,Dev,,,applied,,,,,,,")
    draft(path)
    assert path.read_text().splitlines()[1] == "2026-09-02,Zed,,Dev,,,applied,,,,,,,"
    assert len(tracker.read_rows(path)) == 2


def test_a_write_leaves_no_temp_file_and_a_failed_write_leaves_the_file(path, monkeypatch):
    draft(path)
    before = path.read_bytes()
    monkeypatch.setattr(archive.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        draft(path, company="Beta", role="Dev")
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["tracker.csv", "tracker.csv.lock"]


def test_concurrent_writers_lose_no_row(path):
    tracker.ensure_tracker(path)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: draft(path, company=f"Company {i}", role="Dev"), range(40)))
    rows = tracker.read_rows(path)
    assert sorted(r.company for r in rows) == sorted(f"Company {i}" for i in range(40))


# ── CSV safety ──
HOSTILE = ['x, y "z"\nsecond', "=HYPERLINK(\"http://evil\")", "@SUM(1)", "+1", "-1", "\t=1", "‮evil", "a\x00b", "n" * 3000, "a,b,c,d,e,f,g,h"]


@pytest.mark.parametrize("value", HOSTILE)
def test_written_row_roundtrips_to_one_row_of_header_width(path, value):
    draft(path, company=f"Co {value}", role=f"Role {value}", sector=value, role_type=value, contact_person=value, source=value)
    key = tracker.read_rows(path)[0].key
    person(path, key, "applied", note=value)
    parsed = list(csv.DictReader(io.StringIO(path.read_text(), newline="")))
    assert len(parsed) == 1 and None not in parsed[0] and None not in parsed[0].values()
    assert list(parsed[0]) == list(tracker.HEADER)
    for line in path.read_text().splitlines()[1:]:
        assert len(next(csv.reader([line]))) == 14


@pytest.mark.parametrize("value", HOSTILE)
def test_all_text_fields_sanitised_on_write(path, value):
    draft(path, company=f"Co {value}", role="R", sector=value, role_type=value, contact_person=value)
    row = tracker.read_rows(path)[0]
    for text in (row.company, row.sector, row.role_type, row.contact_person):
        assert not any(c in text for c in ',"\n\r\t') and (not text or text[0] not in "=+-@")


@pytest.mark.parametrize("value", HOSTILE)
def test_notes_written_are_csv_safe(path, value):
    draft(path)
    person(path, "acme__ml_engineer", "applied", note=value)
    note = row_for(path).notes
    assert re.fullmatch(r"2026-10-19 [^,\"\n\r\t]*", note) and len(note) <= 330
    assert not any(c in note for c in ',"\n\r')


def test_note_with_comma_quote_newline_is_sanitised(path):
    draft(path)
    person(path, "acme__ml_engineer", "rejected", note='rejected, no "feedback"\ngiven')
    assert row_for(path).notes == "2026-10-19 rejected - no 'feedback' given"


def test_leading_formula_characters_are_neutralised(path):
    draft(path, company="=cmd", role="@x", sector="+1", role_type="-1", contact_person="\t=2", source="=HYPERLINK(1)")
    row = tracker.read_rows(path)[0]
    assert all(v[0] == "'" for v in (row.company, row.role, row.sector, row.role_type, row.contact_person, row.source))
    assert row.key == "cmd__x"


def test_every_notes_writer_uses_append_note(path):
    draft(path)
    draft(path)
    person(path, "acme__ml_engineer", "applied", note="a, b")
    notes = row_for(path).notes
    assert notes == "redrafted; 2026-10-19 a - b"
    assert tracker.append_note("old", "x, y") == "old; x - y" and tracker.append_note("", "x") == "x"
    assert tracker.append_note("old", "\x00") == "old" and tracker.append_note("", "") == ""


def test_fixed_format_markers_pass_sanitiser_unchanged():
    for marker in ("followed up 2026-01-05", "stale resolved no_response (2026-02-01)", "redrafted"):
        assert tracker.append_note("", marker) == marker


def test_hostile_company_name_cannot_escape_the_archive_through_the_tracker(path, tmp_path):
    draft(path, company="../../etc/x", role="a/b")
    key = tracker.read_rows(path)[0].key
    assert archive.is_key(key)
    person(path, key, "rejected", me=tmp_path / "me")
    assert (tmp_path / "me" / "applications" / key / "outcome.md").is_file()
    assert [p.name for p in (tmp_path / "me").iterdir() if p.is_dir()] == ["applications"]


# ── external, matching, archive in step ──
def test_unmatched_application_creates_row_from_user_input(path):
    row = tracker.add_external(path, company="Beta, Inc", role="Dev", applied_on=date(2026, 10, 1), channel="portal",
                               source="https://b.example/j?a=1,2", by_person=True)
    assert (row.status, row.date, row.channel, row.company, row.source) == ("applied", "2026-10-01", "portal", "Beta - Inc", "https://b.example/j?a=1%2C2")
    with pytest.raises(tracker.StatusRefused, match="already open"):
        tracker.add_external(path, company="beta inc", role="dev", applied_on=TODAY, by_person=True)


def test_match_is_case_insensitive_and_ambiguity_requires_choice(path):
    draft(path, company="Acme", role="ML Engineer")
    draft(path, company="Acme", role="Data Engineer")
    draft(path, company="Other", role="ML Engineer")
    rows = tracker.read_rows(path)
    assert len(tracker.match_rows(rows, "ACME")) == 2
    assert [r.role for r in tracker.match_rows(rows, "acme", "ml engineer")] == ["ML Engineer"]
    assert tracker.match_rows(rows, "nobody") == []
    with pytest.raises(tracker.UnknownKey):
        person(path, "acme", "applied")


def test_tracker_and_archive_updated_together(path, tmp_path):
    me = tmp_path / "me"
    draft(path)
    person(path, "acme__ml_engineer", "rejected", note="no feedback", me=me)
    out = archive.read_outcome(me, "acme__ml_engineer")
    assert out.status == "rejected" and out.resolved == TODAY and out.notes[0].text == "no feedback"
    person(path, "acme__ml_engineer", "rejected", note="no feedback", me=me)
    assert len(archive.read_outcome(me, "acme__ml_engineer").notes) == 1


def test_open_statuses_do_not_settle_the_archive(path, tmp_path):
    me = tmp_path / "me"
    draft(path)
    person(path, "acme__ml_engineer", "interview", me=me)
    assert not (me / "applications").exists()


def test_every_final_tracker_status_is_an_archive_status():
    assert tracker.FINAL <= set(archive.ARCHIVE_STATUSES)


def test_no_module_defines_its_own_final_status_set():
    for module in sorted(Path(tracker.__file__).parent.rglob("*.py")):
        if module.name in ("tracker.py", "archive.py"):
            continue
        for line in module.read_text().splitlines():
            assert not ({"no_response", "withdrawn", "offer_declined"} <= set(re.findall(r"[a-z_]+", line))), f"{module}: {line}"


# ── pipeline ──
def _row(**kw):
    return tracker.Row(**{"company": "Co", "role": "Dev", "status": "applied", "date": "2026-10-01", **kw})


def test_days_quiet_uses_latest_of_date_and_dated_notes():
    row = _row(date="2026-08-01", notes="followed up 2026-09-10; 2026-08-20 call; redrafted")
    assert tracker.days_quiet(row, TODAY) == 39
    assert tracker.days_quiet(_row(date="2026-10-09", notes="followed up 2026-09-10"), TODAY) == 10
    assert tracker.days_quiet(_row(date="2026-10-19"), TODAY) == 0


def test_days_quiet_ignores_dates_in_the_future_and_never_goes_negative():
    row = _row(date="2026-10-01", notes="interview on 2026-12-01")
    assert tracker.days_quiet(row, TODAY) == 18
    assert tracker.days_quiet(_row(date="2027-01-01"), TODAY) is None


def test_unparseable_dates_skipped_and_reported():
    rows = [_row(company="Bad", date="yesterday"), _row(company="Worse", date="2026-02-30"), _row(company="Good")]
    p = tracker.open_pipeline(rows, TODAY)
    assert [e.row.company for e in p.submitted] == ["Good"] and len(p.skipped) == 2
    assert p.skipped[0].startswith("bad__dev") and p.skipped[1].startswith("worse__dev")
    assert "no date that parses" in tracker.render_pipeline(p)
    for text in ("", "2026-1-5", "05/10/2026", None, 20261001):
        assert tracker.parse_date(text) is None


def test_followups_sent_counts_markers():
    assert tracker.followups_sent("followed up 2026-09-01; x; followed up 2026-09-12") == 2
    assert tracker.followups_sent("follow up soon; followed up yesterday") == 0


def test_pipeline_lists_only_open_rows_with_columns():
    rows = [_row(company="Open", date="2026-10-04", deadline="2026-10-30"), _row(company="Done", status="rejected"), _row(company="Legacy", status="no response")]
    p = tracker.open_pipeline(rows, TODAY)
    assert [e.row.company for e in p.submitted] == ["Open"] and p.submitted[0].days_quiet == 15 and p.submitted[0].followups == 0
    text = tracker.render_pipeline(p)
    assert "Open" in text and "Done" not in text and "Legacy" not in text and "15d" in text


def test_everything_resolved_is_said_plainly():
    assert tracker.render_pipeline(tracker.open_pipeline([_row(status="hired")], TODAY)).startswith("Every application is resolved.")
    assert tracker.render_pipeline(tracker.open_pipeline([], TODAY)).startswith("Every application is resolved.")


def test_drafted_rows_listed_separately_without_quiet_math():
    p = tracker.open_pipeline([_row(status="drafted", date="2025-01-01"), _row(company="Sent", date="2026-10-18")], TODAY)
    assert [e.row.status for e in p.drafted] == ["drafted"] and p.drafted[0].days_quiet is None and p.drafted[0].followups is None
    assert [e.row.company for e in p.submitted] == ["Sent"]
    assert not p.stale_due and not p.followup_due
    assert "Drafted, not yet submitted" in tracker.render_pipeline(p)


def test_drafted_is_excluded_from_followup_and_stale_hints_however_old():
    p = tracker.open_pipeline([_row(status="drafted", date="2020-01-01")], TODAY)
    assert (p.followup_due, p.stale_due) == (False, False)


def test_followup_hint_at_ten_days_and_under_two_followups():
    at = lambda days, notes="": tracker.open_pipeline([_row(date=(TODAY - timedelta(days=days)).isoformat(), notes=notes)], TODAY)
    assert not at(9).followup_due and at(10).followup_due
    assert at(30, "followed up 2026-10-01").followup_due
    assert not at(30, "followed up 2026-09-19; followed up 2026-09-25").followup_due


def test_stale_hint_at_sixty_days():
    at = lambda days: tracker.open_pipeline([_row(date=(TODAY - timedelta(days=days)).isoformat())], TODAY)
    assert not at(59).stale_due and at(60).stale_due


def test_deadline_flags_at_seven_day_boundary():
    flags = {d: tracker.deadline_flag((TODAY + timedelta(days=d)).isoformat(), TODAY) for d in (-1, 0, 7, 8)}
    assert flags == {-1: "missed", 0: "urgent", 7: "urgent", 8: ""}
    assert tracker.deadline_flag("", TODAY) == "" and tracker.deadline_flag("soon", TODAY) == ""


def test_passed_deadline_on_drafted_row_called_out_not_chased():
    p = tracker.open_pipeline([_row(status="drafted", company="Late", deadline="2026-10-01", date="2026-09-01")], TODAY)
    text = tracker.render_pipeline(p)
    assert "deadline passed, never sent: Late" in text and not p.followup_due and p.drafted[0].flag == "missed"


def test_blank_deadline_renders_blank():
    text = tracker.render_pipeline(tracker.open_pipeline([_row(status="drafted"), _row()], TODAY))
    assert "deadline" not in text.split("Drafted, not yet submitted")[1] and "urgent" not in text and "missed" not in text


# ── the CLI ──
def cli(tmp_path, *argv, today=TODAY):
    p = argparse.ArgumentParser()
    tracker.add_arguments(p)
    return tracker.main(p.parse_args(["--me", str(tmp_path / "me"), *argv]), today=today)


def test_cli_list_and_set_are_the_persons_commands(tmp_path, capsys):
    me_tracker = tmp_path / "me" / "tracker.csv"
    draft(me_tracker)
    assert cli(tmp_path, "list") == 0 and "Drafted, not yet submitted" in capsys.readouterr().out
    assert cli(tmp_path, "set", "acme__ml_engineer", "applied", "--note", "sent, today", "--date", "2026-10-20") == 0
    row = row_for(me_tracker)
    assert (row.status, row.date, row.notes) == ("applied", "2026-10-20", "2026-10-20 sent - today")


def test_cli_refusals_print_what_to_do_and_return_2(tmp_path, capsys):
    draft(tmp_path / "me" / "tracker.csv")
    assert cli(tmp_path, "set", "acme__ml_engineer", "interview") == 0
    capsys.readouterr()
    assert cli(tmp_path, "set", "acme__ml_engineer", "applied") == 2 and "backwards" in capsys.readouterr().out
    assert cli(tmp_path, "set", "nope__x", "applied") == 2
    assert cli(tmp_path, "set", "acme__ml_engineer", "offer", "--date", "2026/10/20") == 2


def test_cli_add_records_an_external_application(tmp_path):
    assert cli(tmp_path, "add", "--company", "Beta", "--role", "Dev", "--channel", "portal") == 0
    assert row_for(tmp_path / "me" / "tracker.csv", "Beta").status == "applied"


def test_one_canonical_header_constant_shared(path, tmp_path):
    draft(path)
    other = tmp_path / "other.csv"
    tracker.ensure_tracker(other)
    assert path.read_text().splitlines()[0] == other.read_text().splitlines()[0] == ",".join(tracker.HEADER)


def test_source_empty_for_pasted_text(path):
    draft(path)
    assert tracker.read_rows(path)[0].source == ""


def test_a_non_date_today_is_refused_before_anything_is_written(path):
    with pytest.raises(TypeError):
        draft(path, today="2026-10-19")
    assert not path.exists()

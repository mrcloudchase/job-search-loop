"""archive.py: one folder per application, nothing outside it, nothing overwritten, stages only ever appended."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from jobloop import archive

D = date(2026, 3, 2)


@pytest.fixture
def me(tmp_path):
    return tmp_path / "me"


def test_the_key_is_company_double_underscore_role():
    assert archive.key("Novo Nordisk A/S", "ML Engineer") == "novo_nordisk_a_s__ml_engineer"


@pytest.mark.parametrize("company", ["../../etc/passwd", "a/b", "a\\b", "..", ".", "/abs/olute", "C:\\Windows", "a\x00b", "~root",
                                     "$(rm -rf)", "a\nb", "con.", "..%2f..%2fx", "\u2215\u2215x", "\uff0e\uff0e/\uff0e\uff0e/x"])
def test_a_hostile_company_name_is_one_path_component_inside_the_archive(me, company):
    name = archive.key(company, "Role") if archive.slug_part(company) else None
    if name is None:
        with pytest.raises(ValueError):
            archive.key(company, "Role")
        return
    assert archive.is_key(name)
    folder = archive.folder_for(me, name, create=True)
    assert folder.parent == me / "applications" and folder.name == name
    assert "/" not in name and "\\" not in name and "." not in name and "\x00" not in name


@pytest.mark.parametrize("name", ["", ".", "..", "../x", "a/b", "a\\b", "/etc", ".hidden", "_x", "a b", "a\x00", "x" * 200, "a.b"])
def test_a_folder_name_that_is_not_a_derived_key_is_refused_before_anything_is_made(me, name):
    with pytest.raises(archive.ArchiveRefused):
        archive.folder_for(me, name, create=True)
    assert not (me / "applications").exists() or not list((me / "applications").iterdir())


def test_a_symlinked_folder_pointing_out_of_the_archive_is_refused(me, tmp_path):
    (me / "applications").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, me / "applications" / "acme__dev")
    with pytest.raises(archive.ArchiveRefused):
        archive.store_posting(me, "acme__dev", b"posting")
    assert list(outside.iterdir()) == []


def test_slug_rules_lowercase_underscores_and_collapsed_runs():
    assert archive.slug_part("  Novo   Nordisk A/S!! ") == "novo_nordisk_a_s"
    assert archive.slug_part("Zoë  Müller-Åberg") == "zoë_müller_åberg"
    assert archive.slug_part("株式会社 テスト") == "株式会社_テスト"


def test_an_empty_slug_raises_and_creates_nothing(me):
    for company, role in (("!!!", "Dev"), ("Acme", "///"), ("", "Dev")):
        with pytest.raises(ValueError, match="letter or digit"):
            archive.open_application(me, company, role)
    assert not me.exists()


def test_a_long_name_is_capped_by_bytes_with_a_hash_and_stays_unique():
    a = archive.key("C" * 400, "Role one")
    b = archive.key("C" * 400, "Role two")
    cjk = archive.key("株" * 300, "役割")
    assert all(len(k.encode()) <= archive.KEY_BYTES and archive.is_key(k) for k in (a, b, cjk))
    assert a != b and a == archive.key("C" * 400, "Role one")


def test_company_and_role_boundaries_cannot_be_confused():
    assert archive.key("a b", "c") != archive.key("a", "b c")


def test_the_posting_is_stored_byte_for_byte(me):
    posting = b"\xef\xbb\xbf<html>\r\n<!-- hidden -->  Dev &amp; Ops\x00\xff trailing  \r\n\r\n"
    name = archive.key("Acme", "Dev")
    assert archive.store_posting(me, name, posting) == "written"
    assert (me / "applications" / name / "job_posting.md").read_bytes() == posting


def test_a_posting_must_be_the_fetched_bytes_not_a_string(me):
    with pytest.raises(TypeError):
        archive.store_posting(me, "acme__dev", "text")


def test_no_posting_text_means_no_archive_write(me):
    for empty in (b"", b"  \n "):
        with pytest.raises(archive.ArchiveRefused):
            archive.store_posting(me, "acme__dev", empty)
    assert not me.exists()


def test_an_archived_posting_is_never_overwritten_and_rearchiving_is_idempotent(me):
    name = archive.key("Acme", "Dev")
    archive.store_posting(me, name, b"the posting that was submitted")
    assert archive.store_posting(me, name, b"a later fetch") == "kept"
    assert archive.store_posting(me, name, b"the posting that was submitted") == "kept"
    assert (me / "applications" / name / "job_posting.md").read_bytes() == b"the posting that was submitted"
    assert [p.name for p in (me / "applications" / name).iterdir()] == ["job_posting.md"]


def test_documents_are_copied_not_moved_and_never_overwritten(me, tmp_path):
    cv, letter = tmp_path / "cv.tex", tmp_path / "letter.PDF"
    cv.write_bytes(b"cv v1")
    letter.write_bytes(b"letter")
    name = archive.key("Acme", "Dev")
    assert archive.store_document(me, name, "cv", cv) == "written"
    assert archive.store_document(me, name, "cover_letter", letter) == "written"
    cv.write_bytes(b"cv v2 edited later")
    assert archive.store_document(me, name, "cv", cv) == "kept"
    folder = me / "applications" / name
    assert (folder / "cv.tex").read_bytes() == b"cv v1" and (folder / "cover_letter.pdf").read_bytes() == b"letter"
    assert cv.exists() and letter.exists()


def test_a_missing_document_is_skipped_and_nothing_else_is_put_in_its_place(me, tmp_path):
    assert archive.store_document(me, "acme__dev", "cv", tmp_path / "nope.tex") == "skipped"
    assert not (me / "applications" / "acme__dev").exists()


def test_a_document_kind_or_extension_cannot_choose_the_file_name(me, tmp_path):
    src = tmp_path / "x.tex"
    src.write_bytes(b"x")
    with pytest.raises(ValueError):
        archive.store_document(me, "acme__dev", "../outcome", src)
    with pytest.raises(archive.ArchiveRefused):
        archive.store_document(me, "acme__dev", "cv", tmp_path / "noext")
    assert not (me / "applications").exists()


def test_open_application_makes_an_in_progress_outcome_and_is_idempotent(me):
    name = archive.open_application(me, "Novo Nordisk A/S", "ML Engineer")
    first = (me / "applications" / name / "outcome.md").read_text()
    assert first.startswith("# Outcome: Novo Nordisk A/S — ML Engineer\n")
    assert "**Status:** in_progress" in first and "Date resolved" not in first
    archive.record_stage(me, name, on=D, stage="phone_screen")
    assert archive.open_application(me, "Novo Nordisk A/S", "ML Engineer") == name
    assert len(archive.read_outcome(me, name).stages) == 1


def test_outcome_md_roundtrips_in_the_documented_format(me):
    name = archive.open_application(me, "Acme", "Dev")
    archive.record_stage(me, name, on=D, stage="technical", format="video", people=("Ann Lee", "Bo; Chu"), feedback="Good.\n\n  Weak on SQL.", outcome="passed")
    archive.record_outcome(me, name, "rejected", on=date(2026, 4, 1), note="no reason given")
    text = (me / "applications" / name / "outcome.md").read_text()
    parsed = archive.parse_outcome(text)
    assert archive.render_outcome(parsed) == text
    assert parsed.status == "rejected" and parsed.resolved == date(2026, 4, 1)
    assert parsed.stages[0].people == ("Ann Lee", "Bo, Chu")


def test_feedback_is_stored_verbatim_even_when_it_looks_like_structure(me):
    name = archive.open_application(me, "Acme", "Dev")
    nasty = "They said:\n## Notes\n- 2026-01-01: fake note\n- Outcome: passed\n\n**Status:** hired\n# Outcome: x"
    archive.record_stage(me, name, on=D, stage="final_round", feedback=nasty, outcome="failed")
    back = archive.read_outcome(me, name)
    assert back.stages[0].feedback == nasty
    assert back.status == "in_progress" and back.notes == () and back.stages[0].outcome == "failed"


def test_stage_entries_are_append_only_and_earlier_entries_are_untouched(me):
    name = archive.open_application(me, "Acme", "Dev")
    archive.record_stage(me, name, on=D, stage="phone_screen", feedback="friendly")
    before = archive.read_outcome(me, name).stages
    archive.record_stage(me, name, on=date(2026, 3, 9), stage="phone_screen", feedback="friendly", outcome="passed")
    archive.record_stage(me, name, on=date(2026, 3, 20), stage="technical")
    after = archive.read_outcome(me, name).stages
    assert after[:1] == before and [s.stage for s in after] == ["phone_screen", "phone_screen", "technical"]


def test_recording_the_same_stage_twice_is_the_same_as_once(me):
    name = archive.open_application(me, "Acme", "Dev")
    assert archive.record_stage(me, name, on=D, stage="case", feedback="x") is True
    assert archive.record_stage(me, name, on=D, stage="case", feedback="x") is False
    assert len(archive.read_outcome(me, name).stages) == 1


def test_a_stage_needs_a_name_and_a_known_outcome(me):
    name = archive.open_application(me, "Acme", "Dev")
    with pytest.raises(ValueError):
        archive.record_stage(me, name, on=D, stage="  ")
    with pytest.raises(ValueError):
        archive.record_stage(me, name, on=D, stage="case", outcome="great")
    assert archive.read_outcome(me, name).stages == ()


def test_status_only_leaves_in_progress_on_resolution(me):
    name = archive.open_application(me, "Acme", "Dev")
    archive.record_stage(me, name, on=D, stage="case")
    assert archive.read_outcome(me, name).status == "in_progress"
    with pytest.raises(ValueError):
        archive.record_outcome(me, name, "in_progress", on=D)
    archive.record_outcome(me, name, "interview_only", on=date(2026, 5, 1))
    assert archive.read_outcome(me, name).resolved == date(2026, 5, 1)


def test_a_settled_outcome_is_never_changed_only_noted(me):
    name = archive.open_application(me, "Acme", "Dev")
    archive.record_outcome(me, name, "rejected", on=date(2026, 5, 1), note="email came")
    with pytest.raises(archive.ArchiveRefused):
        archive.record_outcome(me, name, "hired", on=date(2026, 6, 1))
    assert archive.record_outcome(me, name, "rejected", on=date(2026, 5, 1), note="email came") is False
    out = archive.read_outcome(me, name)
    assert out.status == "rejected" and out.resolved == date(2026, 5, 1) and len(out.notes) == 1


def test_the_archive_status_words_are_the_documented_ones_and_a_tracker_only_word_is_refused(me):
    assert archive.ARCHIVE_STATUSES[:6] == ("in_progress", "hired", "offer_declined", "rejected", "no_response", "interview_only")
    name = archive.open_application(me, "Acme", "Dev")
    for word in ("applied", "drafted", "offer", "Rejected"):
        with pytest.raises(ValueError):
            archive.record_outcome(me, name, word, on=D)


def test_recording_into_an_application_that_was_never_opened_is_refused(me):
    with pytest.raises(archive.ArchiveRefused):
        archive.record_stage(me, "acme__dev", on=D, stage="case")
    assert not me.exists() or not (me / "applications" / "acme__dev").exists()


def test_a_damaged_outcome_md_is_reported_not_guessed(me):
    name = archive.open_application(me, "Acme", "Dev")
    (me / "applications" / name / "outcome.md").write_text("# Outcome: Acme — Dev\n\n**Status:** hired\nrandom line\n")
    with pytest.raises(archive.OutcomeFormatError, match="line 4"):
        archive.read_outcome(me, name)


def test_reading_back_returns_only_the_named_files(me, tmp_path):
    name = archive.open_application(me, "Acme", "Dev")
    archive.store_posting(me, name, b"posting")
    src = tmp_path / "c.tex"
    src.write_bytes(b"cv")
    archive.store_document(me, name, "cv", src)
    folder = me / "applications" / name
    (folder / "interview_prep_technical.md").write_text("prep")
    (folder / "followup_2026-03-01.md").write_text("mail")
    (folder / "cv.tex.bak").write_text("junk")
    got = archive.read(me, name)
    assert got.posting == b"posting" and got.documents == ("cv.tex",) and got.outcome.status == "in_progress"
    assert archive.read(me, "nothing__here") == archive.Archive("nothing__here", None, (), None)


def test_two_writers_recording_stages_at_once_lose_nothing(me):
    from concurrent.futures import ThreadPoolExecutor
    name = archive.open_application(me, "Acme", "Dev")
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: archive.record_stage(me, name, on=D, stage=f"round_{i}"), range(20)))
    assert sorted(s.stage for s in archive.read_outcome(me, name).stages) == sorted(f"round_{i}" for i in range(20))


def test_the_atomic_write_leaves_no_temp_files_and_replaces_whole(tmp_path):
    target = tmp_path / "f.txt"
    archive.write_atomically(target, b"one")
    archive.write_atomically(target, b"two")
    assert target.read_bytes() == b"two" and [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_archive_layout_has_the_named_files_and_nothing_else(me, tmp_path):
    name = archive.open_application(me, "Acme", "Dev")
    archive.store_posting(me, name, b"p")
    for kind, ext in (("cv", "tex"), ("cover_letter", "tex")):
        src = tmp_path / f"{kind}.{ext}"
        src.write_bytes(b"x")
        archive.store_document(me, name, kind, src)
    assert {p.name for p in (me / "applications" / name).iterdir()} == {"cv.tex", "cover_letter.tex", "job_posting.md", "outcome.md"}


def test_notes_are_appended_with_dates_and_never_overwritten(me):
    name = archive.open_application(me, "Acme", "Dev")
    archive.record_outcome(me, name, "rejected", on=date(2026, 5, 1), note="first mail")
    archive.record_outcome(me, name, "rejected", on=date(2026, 5, 2), note="second mail\nwith detail")
    assert [(n.on, n.text) for n in archive.read_outcome(me, name).notes] == [
        (date(2026, 5, 1), "first mail"), (date(2026, 5, 2), "second mail\nwith detail")]


def test_no_response_is_only_ever_recorded_when_a_caller_says_so(me):
    name = archive.open_application(me, "Acme", "Dev")
    archive.record_stage(me, name, on=D, stage="phone_screen")
    assert archive.read_outcome(me, name).status == "in_progress"
    archive.record_outcome(me, name, "no_response", on=D)
    assert archive.read_outcome(me, name).status == "no_response"


def test_applications_dir_is_gitignored():
    ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text().splitlines()
    assert "me/" in ignore


def test_two_roles_at_one_company_get_distinct_folders_and_documents(tmp_path):
    a, b = archive.key("Northwind", "Data Engineer"), archive.key("Northwind", "Data Analyst")
    assert a != b and a.split("__")[0] == b.split("__")[0]
    for name in (a, b):
        archive.store_posting(tmp_path, name, name.encode())
    assert archive.folder_for(tmp_path, a) != archive.folder_for(tmp_path, b)
    assert (archive.folder_for(tmp_path, a) / archive.POSTING).read_bytes() == a.encode()
    assert sorted(p.name for p in (tmp_path / "applications").iterdir()) == sorted([a, b])

"""engine/learn.py: the pure analysis. Evidence from a populated candidate folder, and the bounded proposals taken from it."""
from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

import sim
from jobloop import archive, calibrate, tracker
from jobloop.fit import WEIGHTS
from jobloop.engine import learn
from jobloop.engine.store import Store

PROFILE = sim.PROFILE.read_text()
CV = "\\section*{Experience}\n\\item Owns the shipment-events pipeline.\n\\item Mentors two junior engineers.\n"
COVER = "Dear team,\nI would like to join you.\n"
NOW = sim.NOW


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def deliver(me, key, cv=CV, cover=COVER, edited_cv=None, edited_cover=None, keep_original=True):
    """A queue folder as the package stage leaves it, then as the person left it after editing."""
    folder = me / "queue" / key
    (folder / "edit-here").mkdir(parents=True)
    (folder / "original").mkdir()
    for name, original, edited in (("cv.tex", cv, edited_cv), ("cover.tex", cover, edited_cover)):
        (folder / "edit-here" / name).write_text(edited if edited is not None else original)
        if keep_original:
            (folder / "original" / name).write_text(original)
    (folder / "queue.json").write_text(json.dumps({"delivered_hashes": {"cv.tex": sha(cv), "cover.tex": sha(cover)}}))


def draft(me, key, posting_key, fit=80, judge=7.0, band="good"):
    folder = me / "applications" / key
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "draft.json").write_text(json.dumps({"key": key, "posting_key": posting_key, "fit": fit, "judge": {"mean": judge, "judges": []}}))
    sim.write_fit(me, posting_key, total=fit)


def populate(tmp_path, statuses=(("Acme", "Data Engineer", "applied"), ("Beta", "Analyst", "drafted"), ("Gamma", "Engineer", "drafted")), dead=True):
    """Three delivered applications: Acme's CV edited, Beta's untouched, Gamma's letter edited. Judges failed items 5 and 9."""
    postings = [sim.posting("p-acme", "Acme", "Data Engineer", status="ranked", score=80, board="linkedin"),
                sim.posting("p-beta", "Beta", "Analyst", status="ranked", score=75, board="linkedin"),
                sim.posting("p-gamma", "Gamma", "Engineer", status="ranked", score=70, board="linkedin"),
                sim.posting("p-dead", "Delta", "Dev", status="ranked", score=20, board="jobindex")]
    me = sim.make_me(tmp_path, postings, list(statuses))
    store = Store(me)
    deliver(me, "acme__data_engineer", edited_cv=CV + "\\item Cut the BigQuery bill by 90 percent at Zephyr Corp.\n")
    deliver(me, "beta__analyst")
    deliver(me, "gamma__engineer", edited_cover=COVER.replace("I would like to join you.", "I want to join you."))
    for key, posting in (("acme__data_engineer", "p-acme"), ("beta__analyst", "p-beta"), ("gamma__engineer", "p-gamma")):
        draft(me, key, posting, fit={"p-acme": 80, "p-beta": 75, "p-gamma": 70}[posting], judge=7.5)
        store.queue_put(key, "ready", f"queue/{key}", {}, NOW)
    for i, item in enumerate(("acme__data_engineer", "beta__analyst", "gamma__engineer")):
        store.add_judgment(item, "draft", "codex", 7.5, {"failed": [[5, "the opening is generic"], [9, f"cliche {i}"]] if i < 2 else [[5, "still generic"]]}, 1, NOW)
    for _ in range(3):
        store.add_event("yield", "linkedin", {"query": "data engineer", "found": 10, "new": 4}, NOW)
        if dead:
            store.add_event("yield", "jobindex", {"query": "dataingeniør", "found": 5, "new": 2}, NOW)
    store.add_event("yield", "linkedin", {"query": "etl", "found": 3, "new": 1}, NOW)
    return me, store


def rows_of(me):
    return tracker.read_rows(me / "tracker.csv")


# ── evidence ──
def test_edited_and_unedited_deliveries_are_told_apart_and_the_diff_is_the_persons_change(tmp_path):
    me, store = populate(tmp_path)
    ev = learn.evidence(me, store, rows_of(me))
    by = {(e.key, e.file): e for e in ev.edits}
    assert len(ev.edits) == 6
    assert by[("acme__data_engineer", "cv.tex")].edited and not by[("acme__data_engineer", "cover.tex")].edited
    assert not by[("beta__analyst", "cv.tex")].edited and not by[("beta__analyst", "cover.tex")].edited
    assert by[("gamma__engineer", "cover.tex")].edited
    acme = by[("acme__data_engineer", "cv.tex")]
    assert acme.original_found and "+\\item Cut the BigQuery bill by 90 percent at Zephyr Corp." in acme.diff
    assert "-I would like to join you." in by[("gamma__engineer", "cover.tex")].diff
    assert [(e.key, e.file) for e in ev.accepted if e.key == "beta__analyst"] == [("beta__analyst", "cv.tex"), ("beta__analyst", "cover.tex")]


def test_an_edit_whose_original_is_not_kept_is_flagged_edited_with_no_diff(tmp_path):
    me = sim.make_me(tmp_path)
    deliver(me, "acme__data_engineer", edited_cv=CV + "x\n", keep_original=False)
    with Store(me) as store:
        (edit,) = [e for e in learn.evidence(me, store, ()).edits if e.edited]
    assert not edit.original_found and "not kept" in edit.diff and edit.added == []


def test_a_queue_folder_without_hashes_or_files_is_ignored(tmp_path):
    me = sim.make_me(tmp_path)
    (me / "queue" / "x" / "edit-here").mkdir(parents=True)
    (me / "queue" / "x" / "queue.json").write_text("not json")
    with Store(me) as store:
        assert learn.evidence(me, store, ()).edits == []


def test_outcomes_join_the_tracker_status_to_the_fit_and_the_judge_mean(tmp_path):
    me, store = populate(tmp_path)
    by = {o.key: o for o in learn.evidence(me, store, rows_of(me)).outcomes}
    assert by["acme__data_engineer"].status == "applied" and by["acme__data_engineer"].score == 80
    assert by["acme__data_engineer"].band == "good" and by["acme__data_engineer"].judge_mean == 7.5
    assert by["beta__analyst"].status == "drafted"


def test_search_yield_is_aggregated_per_board_and_query_with_board_level_shortlisting(tmp_path):
    me, store = populate(tmp_path)
    ev = learn.evidence(me, store, rows_of(me))
    assert ev.yields[("linkedin", "data engineer")] == {"runs": 3, "found": 30, "new": 12, "shortlisted": 3}
    assert ev.yields[("linkedin", "etl")]["runs"] == 1
    assert ev.yields[("jobindex", "dataingeniør")] == {"runs": 3, "found": 15, "new": 6, "shortlisted": 0}
    assert ev.per_query is False


def test_yield_is_credited_to_the_query_when_postings_record_it(tmp_path):
    me, store = populate(tmp_path)
    state = json.loads((me / "state" / "postings.json").read_text())
    state["postings"]["p-acme"]["queries"] = ["data engineer"]
    state["postings"]["p-beta"]["query"] = "data engineer"
    (me / "state" / "postings.json").write_text(json.dumps(state))
    ev = learn.evidence(me, store, rows_of(me))
    assert ev.per_query and ev.yields[("linkedin", "data engineer")]["shortlisted"] == 2     # acme and beta name the query; gamma names none
    assert ev.yields[("linkedin", "etl")]["shortlisted"] == 0


def test_failed_rubric_items_are_counted_most_failed_first(tmp_path):
    me, store = populate(tmp_path)
    ev = learn.evidence(me, store, rows_of(me))
    assert ev.judged == 3 and [(n, c) for n, c, _ in ev.rubric] == [(5, 3), (9, 2)]
    assert "item 5: failed in 3 of 3" in learn.failure_lines(ev)


def test_ready_count_and_cost_per_accepted(tmp_path):
    me, store = populate(tmp_path)
    store.record_attempt(1, "draft", "acme__data_engineer", "r", "codex", "2026-10-19T00:00:00+00:00", "2026-10-19T00:10:00+00:00", "success", 3.0, 0, [], [])
    ev = learn.evidence(me, store, rows_of(me))
    assert ev.ready == 3 and ev.cost_per_accepted == pytest.approx(1.0)


def test_enough_evidence_needs_an_edit_three_ready_or_an_outcome(tmp_path):
    me = sim.make_me(tmp_path, tracker_rows=[("Acme", "Data Engineer", "drafted")])
    with Store(me) as store:
        ev = learn.evidence(me, store, rows_of(me))
        assert not ev.enough() and "not enough evidence" in ev.lacking() and len(ev.missing) == 3
    me2 = sim.make_me(tmp_path / "b", tracker_rows=[("Acme", "Data Engineer", "rejected")])
    with Store(me2) as store:
        assert learn.evidence(me2, store, rows_of(me2)).enough()


# ── proposals ──
def test_dead_queries_are_dropped_only_after_enough_runs_and_nothing_is_ever_added(tmp_path):
    me, store = populate(tmp_path)
    ev = learn.evidence(me, store, rows_of(me))
    assert learn.query_proposals(ev) == ["dataingeniør"]          # three runs, board never shortlisted; 'etl' has one run, 'data engineer' paid off
    assert learn.query_proposals(ev, ["dataingeniør"]) == []


def test_a_query_that_paid_off_on_any_board_is_kept(tmp_path):
    me, store = populate(tmp_path)
    for _ in range(3):
        store.add_event("yield", "jobindex", {"query": "data engineer", "found": 1, "new": 1}, NOW)
    ev = learn.evidence(me, store, rows_of(me))
    assert "data engineer" not in learn.query_proposals(ev)


def test_weights_come_from_calibrates_rules_and_not_below_its_thresholds(tmp_path):
    me, store = populate(tmp_path)
    weights, notes = learn.weights_proposal(me, rows_of(me))
    assert weights is None and "Too little" in notes[0]
    good, bad = {"technical": 80, "experience": 65, "behavioural": 50, "career": 70}, {"technical": 50, "experience": 60, "behavioural": 60, "career": 65}
    for i in range(10):
        went = i < 5
        company = f"{'Went' if went else 'Not'}{i}"
        tracker.record_drafted(me / "tracker.csv", company=company, role="Dev", today=date(2026, 10, 5), cv_file="cv.pdf", cover_letter_file="cover.pdf")
        tracker.set_status(me / "tracker.csv", archive.key(company, "Dev"), "hired" if went else "rejected", by_person=True, on=date(2026, 10, 8), me=me)
        from test_calibrate import save_fit
        save_fit(me, f"{company.lower()}-dev", good if went else bad)
    weights, notes = learn.weights_proposal(me, rows_of(me))
    assert weights == calibrate.validate_weights(weights) and weights["technical"] == WEIGHTS["technical"] + 5 and sum(weights.values()) == 100
    assert learn.weights_proposal(me, rows_of(me), current=weights)[0] is None


def test_style_candidates_are_the_raw_diffs_one_file_each(tmp_path):
    me, store = populate(tmp_path)
    files = learn.style_candidates(learn.evidence(me, store, rows_of(me)))
    assert sorted(files) == ["edits/acme__data_engineer__cv.diff", "edits/gamma__engineer__cover.diff"]
    assert "Zephyr Corp" in files["edits/acme__data_engineer__cv.diff"]


def test_a_fact_the_person_added_is_found_with_its_section_and_a_rewording_is_not(tmp_path):
    me, store = populate(tmp_path)
    facts = learn.fact_candidates(learn.evidence(me, store, rows_of(me)), PROFILE)
    assert len(facts) == 1
    assert "Zephyr Corp" in facts[0]["text"] and facts[0]["section"] == "Experience" and facts[0]["source"] == "your edit of acme__data_engineer/cv.tex"
    assert learn.fact_candidates(learn.evidence(me, store, rows_of(me)), "") == []


def test_style_rules_that_name_something_from_the_profile_or_the_edits_are_refused(tmp_path):
    me, store = populate(tmp_path)
    ev = learn.evidence(me, store, rows_of(me))
    banned = learn.names_in(ev, PROFILE)
    assert learn.rule_names(["Never mention Zephyr Corp in the opening."], banned)
    assert learn.rule_names(["Skip Tidewater when you open."], banned)
    assert not learn.rule_names(["Open with the result, not the greeting."], banned)

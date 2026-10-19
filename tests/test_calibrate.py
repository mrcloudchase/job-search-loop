"""calibrate.py: final outcomes compared with the saved fit scores; proposals only, applied one by one on the person's yes."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, timedelta

import pytest

from jobloop import archive, calibrate, fit, tracker

TODAY = date(2026, 10, 19)
PROGRESSED = {"technical": 80, "experience": 65, "behavioural": 50, "career": 70}
NOT_PROGRESSED = {"technical": 50, "experience": 60, "behavioural": 60, "career": 65}


@pytest.fixture
def me(tmp_path):
    return tmp_path / "me"


def save_fit(me, key, scores, flags=()):
    gates = tuple(fit.Gate(g, "FLAG" if g in flags else "PASS", ("flagged",) if g in flags else ()) for g in fit.GATES)
    fit.save(fit.Fit(key, *(scores[d] for d in fit.WEIGHTS), gates, (), (), ()), me / "fit")


def put_rows(me, rows):
    me.mkdir(parents=True, exist_ok=True)
    with open(me / "tracker.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(tracker.HEADER)
        for r in rows:
            w.writerow([r.get(c, "") for c in tracker.HEADER])


def world(me, went=5, not_went=5, flags=("location", "location"), extra_rows=()):
    """`went` applications that progressed (hired, offer_declined, then rejected after an interview) and `not_went` that did not."""
    rows = []
    statuses = ["hired", "offer_declined"] + ["rejected"] * 10
    for i in range(went):
        company = f"Went{i}"
        rows.append({"company": company, "role": "Dev", "status": statuses[i], "date": "2026-01-10"})
        save_fit(me, f"went{i}-dev", PROGRESSED, flags=("location",) if i < len(flags) else ())
        if statuses[i] == "rejected":
            archive.open_application(me, company, "Dev")
            archive.record_stage(me, f"went{i}__dev", on=date(2026, 2, 1), stage="phone_screen")
    for i in range(not_went):
        status = "rejected" if i % 2 else "no_response"
        rows.append({"company": f"Not{i}", "role": "Dev", "status": status, "date": "2026-01-10"})
        save_fit(me, f"not{i}-dev", NOT_PROGRESSED)
    put_rows(me, [*rows, *extra_rows])


def snapshot(me):
    return {str(p.relative_to(me)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(me.rglob("*")) if p.is_file()}


def kinds(proposals):
    return [p.kind for p in proposals.items]


# ── what is counted ──
def test_in_progress_not_counted_as_resolved(me):
    world(me, extra_rows=[{"company": "Open", "role": "Dev", "status": s} for s in ("drafted", "applied", "interview", "offer")])
    save_fit(me, "open-dev", PROGRESSED)
    found, _ = calibrate.samples(me, tracker.read_rows(me / "tracker.csv"))
    assert len(found) == 10 and not any(s.key == "open__dev" for s in found)


def test_withdrawn_and_applications_without_a_saved_fit_are_not_evidence_and_are_said_so(me):
    world(me, extra_rows=[{"company": "Quit", "role": "Dev", "status": "withdrawn"},
                          {"company": "Nofit", "role": "Dev", "status": "rejected"}])
    save_fit(me, "quit-dev", PROGRESSED)
    found, notes = calibrate.samples(me, tracker.read_rows(me / "tracker.csv"))
    assert len(found) == 10 and notes == ("nofit__dev: rejected, but no saved fit to compare it with",)


def test_a_rejection_after_an_interview_still_progressed_and_hired_and_declined_offers_progressed(me):
    world(me)
    found, _ = calibrate.samples(me, tracker.read_rows(me / "tracker.csv"))
    by = {s.key: s for s in found}
    assert all(by[f"went{i}__dev"].progressed for i in range(5)) and not any(by[f"not{i}__dev"].progressed for i in range(5))
    assert by["went2__dev"].status == "rejected"


def test_fit_files_match_applications_with_hyphens_and_underscores_ignored(me):
    world(me)
    assert (me / "fit" / "went0-dev.json").exists() and not (me / "fit" / "went0__dev.json").exists()
    assert len(calibrate.samples(me, tracker.read_rows(me / "tracker.csv"))[0]) == 10


def test_a_broken_fit_file_is_ignored_and_named_and_the_scores_come_from_dimensions_not_the_stored_total(me):
    world(me)
    (me / "fit" / "junk.json").write_text("{not json")
    (me / "fit" / "odd.json").write_text(json.dumps({"key": "odd", "scores": {"technical": 999}, "gates": []}))
    data = json.loads((me / "fit" / "went0-dev.json").read_text())
    data["total"], data["band"] = 0, "poor"
    (me / "fit" / "went0-dev.json").write_text(json.dumps(data))
    found, notes = calibrate.samples(me, tracker.read_rows(me / "tracker.csv"))
    assert len(found) == 10 and {n.split(":")[0] for n in notes} == {"junk.json", "odd.json"}
    assert next(s for s in found if s.key == "went0__dev").scores == PROGRESSED


# ── what is proposed ──
def test_too_little_data_proposes_nothing_and_states_the_minimum(me):
    world(me, went=3, not_went=4)
    proposals = calibrate.propose(me)
    assert proposals.items == () and "7 final outcomes" in proposals.notes[0] and "at least 8" in proposals.notes[0]


def test_eight_outcomes_with_three_on_each_side_is_enough(me):
    world(me, went=4, not_went=4)
    assert "weights" in kinds(calibrate.propose(me))


def test_a_lopsided_history_proposes_nothing(me):
    world(me, went=9, not_went=2)
    assert calibrate.propose(me).items == ()


def test_the_weights_proposal_moves_five_points_to_the_best_predictor_from_the_worst_and_shows_its_evidence(me):
    world(me)
    p = next(p for p in calibrate.propose(me).items if p.kind == "weights")
    assert p.change["weights"] == {"technical": 35, "experience": 25, "behavioural": 10, "career": 30}
    assert sum(p.change["weights"].values()) == 100
    text = "\n".join(p.evidence)
    assert "5 progressed, 5 did not" in text and "technical: progressed 80.0 vs not 50.0" in text
    assert "went0__dev" in text and "not4__dev" in text


def test_when_no_dimension_separates_the_outcomes_no_weights_are_proposed(me):
    world(me)
    for path in (me / "fit").glob("went*.json"):
        data = json.loads(path.read_text())
        data["scores"] = dict(NOT_PROGRESSED)
        path.write_text(json.dumps(data))
    assert "weights" not in kinds(calibrate.propose(me))


def test_a_gate_that_flagged_only_applications_that_progressed_is_proposed_for_review(me):
    world(me)
    p = next(p for p in calibrate.propose(me).items if p.kind == "gate_review")
    assert p.change == {"gate_review": "location"} and "flagged on location and progressed: went0__dev, went1__dev" in p.evidence[0]


def test_a_gate_that_also_flagged_an_application_that_did_not_progress_is_not_proposed(me):
    world(me)
    save_fit(me, "not0-dev", NOT_PROGRESSED, flags=("location",))
    assert "gate_review" not in kinds(calibrate.propose(me))


def test_recorder_never_writes_framework_files(me):
    world(me)
    (me / "profile.md").write_text("# Anna\n\n## Deal-breakers\n- onsite 5 days\n")
    before = snapshot(me)
    calibrate.propose(me)
    assert snapshot(me) == before and not (me / "state").exists()


# ── applying ──
def test_an_unconfirmed_proposal_changes_nothing(me):
    world(me)
    before = snapshot(me)
    assert calibrate.apply(me, calibrate.propose(me), lambda p: False, TODAY) == ()
    assert snapshot(me) == before and not (me / "state" / "calibration.json").exists()


@pytest.mark.parametrize("answer", ["yes", "y", 1, None])
def test_only_a_true_answer_is_a_confirmation(me, answer):
    world(me)
    calibrate.apply(me, calibrate.propose(me), lambda p: answer, TODAY)
    assert not (me / "state" / "calibration.json").exists()


def test_each_proposal_is_confirmed_separately(me):
    world(me)
    applied = calibrate.apply(me, calibrate.propose(me), lambda p: p.kind == "gate_review", TODAY)
    data = json.loads((me / "state" / "calibration.json").read_text())
    assert [p.kind for p in applied] == ["gate_review"] and "weights" not in data and data["gate_reviews"] == ["location"]
    assert calibrate.load_weights(me) == fit.WEIGHTS


def test_calibration_applies_only_with_confirmation_and_is_idempotent(me):
    world(me)
    calibrate.apply(me, calibrate.propose(me), lambda p: True, TODAY)
    first = (me / "state" / "calibration.json").read_bytes()
    assert calibrate.load_weights(me) == {"technical": 35, "experience": 25, "behavioural": 10, "career": 30}
    again = calibrate.propose(me)
    assert "weights" not in kinds(again)
    calibrate.apply(me, calibrate.Proposals(tuple(calibrate.propose(me).items), ()), lambda p: True, TODAY + timedelta(days=9))
    assert (me / "state" / "calibration.json").read_bytes() == first


def test_applying_the_same_weights_again_on_a_later_day_changes_nothing_more(me):
    world(me)
    items = calibrate.propose(me)
    calibrate.apply(me, items, lambda p: True, TODAY)
    first = (me / "state" / "calibration.json").read_bytes()
    calibrate.apply(me, items, lambda p: True, TODAY + timedelta(days=30))
    assert (me / "state" / "calibration.json").read_bytes() == first


def test_applying_never_edits_the_profile_or_the_saved_fits(me):
    world(me)
    (me / "profile.md").write_text("# Anna\n")
    before = snapshot(me)
    calibrate.apply(me, calibrate.propose(me), lambda p: True, TODAY)
    after = snapshot(me)
    assert {k: v for k, v in after.items() if not k.startswith("state/calibration.json")} == before


def bad(weights):
    return calibrate.Proposals((calibrate.Proposal("weights", "x", ("e",), {"weights": weights}),), ())


@pytest.mark.parametrize("weights", [
    {"technical": 40, "experience": 25, "behavioural": 15, "career": 30},      # sums to 110
    {"technical": 46, "experience": 14, "behavioural": 10, "career": 30},      # above the ceiling
    {"technical": 40, "experience": 30, "behavioural": 5, "career": 25},       # below the floor
    {"technical": 30, "experience": 25, "behavioural": 15},                    # a dimension missing
    {"technical": 30, "experience": 25, "behavioural": 15, "career": 30, "luck": 0},
    {"technical": 30.0, "experience": 25, "behavioural": 15, "career": 30},    # not whole numbers
    {"technical": True, "experience": 25, "behavioural": 15, "career": 59},
])
def test_weights_that_break_the_rules_are_refused_and_nothing_is_written(me, weights):
    (me / "state").mkdir(parents=True)
    with pytest.raises(ValueError):
        calibrate.apply(me, bad(weights), lambda p: True, TODAY)
    assert not (me / "state" / "calibration.json").exists()


def test_a_gate_review_must_name_a_real_gate(me):
    (me / "state").mkdir(parents=True)
    proposals = calibrate.Proposals((calibrate.Proposal("gate_review", "x", (), {"gate_review": "karma"}),), ())
    with pytest.raises(ValueError, match="gate"):
        calibrate.apply(me, proposals, lambda p: True, TODAY)
    assert not (me / "state" / "calibration.json").exists()


def test_an_unknown_proposal_kind_cannot_exist():
    with pytest.raises(ValueError):
        calibrate.Proposal("rewrite_profile", "x", (), {})


def test_the_weights_in_force_default_to_the_fits_and_a_corrupt_file_is_an_error_not_a_fallback(me):
    assert calibrate.load_weights(me) == fit.WEIGHTS
    (me / "state").mkdir(parents=True)
    (me / "state" / "calibration.json").write_text(json.dumps({"weights": {"technical": 90, "experience": 5, "behavioural": 3, "career": 2}}))
    with pytest.raises(ValueError):
        calibrate.load_weights(me)
    (me / "state" / "calibration.json").write_text("{nope")
    with pytest.raises(ValueError, match="not valid JSON"):
        calibrate.load_weights(me)


def test_accepted_weights_always_sum_to_one_hundred_within_bounds():
    for weights in ({"technical": 35, "experience": 25, "behavioural": 10, "career": 30}, dict(fit.WEIGHTS)):
        assert sum(calibrate.validate_weights(weights).values()) == 100
    assert calibrate.MIN_WEIGHT <= min(fit.WEIGHTS.values()) and max(fit.WEIGHTS.values()) <= calibrate.MAX_WEIGHT



"""rankstate.py: selection and deferral, the unreachable/expired/ranked transitions, the sweep, flags and ordering,
all as pure functions over a plain dict."""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone

import pytest

from jobloop import rankstate as R

TODAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
GATES = tuple({"name": n, "verdict": "PASS", "notes": []} for n in ("eligibility", "permit", "location", "language"))


def entry(key, **kw):
    base = {"key": key, "title": f"Role {key}", "company": f"Co {key}", "url": f"https://x.example/{key}", "board": "jobindex",
            "source": "board", "posted_date": None, "deadline": None, "first_seen": "2026-10-10", "last_seen": "2026-10-18",
            "locations": ["Aarhus"], "status": "new", "tags": []}
    return {**base, **kw}


def store(*entries):
    return {e["key"]: e for e in entries}


def scored(key, score=70, verdict="good", gates=GATES, deadline=None, strengths=("s1",), gaps=("g1",)):
    return R.Scored(key, score, verdict, tuple(copy.deepcopy(gates)), tuple(strengths), tuple(gaps), deadline)


def gate(name, verdict, *notes):
    return tuple({**g, "verdict": verdict, "notes": list(notes)} if g["name"] == name else g for g in GATES)


def later(hours):
    return NOW + timedelta(hours=hours)


# ── selection ──
def test_default_selects_new_and_unreachable_only():
    s = store(*(entry(k, status=k) for k in ("new", "unreachable", "ranked", "expired", "gave_up", "skipped", "applied")))
    assert set(R.candidates(s).selected) == {"new", "unreachable"}


def test_focus_matches_title_company_and_fit_notes():
    s = store(entry("a", title="Data Engineer"), entry("b", company="DataCorp"), entry("c", status="unreachable", strengths=["strong in AIRFLOW"]),
              entry("d", title="Nurse"))
    for text, want in (("data", {"a", "b"}), ("airflow", {"c"}), ("NURSE", {"d"}), ("zzz", set())):
        assert set(R.candidates(s, focus=text).selected) == want


def test_all_includes_everything_but_skipped():
    s = store(*(entry(k, status=k) for k in ("new", "unreachable", "ranked", "expired", "gave_up", "skipped", "applied")))
    assert set(R.candidates(s, include_all=True).selected) == {"new", "unreachable", "ranked", "expired", "gave_up"}


def test_deferred_count_and_status_unchanged():
    s = store(*(entry(f"k{i}", first_seen=f"2026-10-{10 + i:02d}") for i in range(5)))
    before = copy.deepcopy(s)
    sel = R.candidates(s, limit=2)
    assert len(sel.selected) == 2 and len(sel.deferred) == 3 and set(sel.selected + sel.deferred) == set(s)
    assert s == before and all(e["status"] == "new" for e in s.values())


def test_limit_zero_uncapped():
    s = store(*(entry(f"k{i}") for i in range(30)))
    sel = R.candidates(s, limit=0)
    assert len(sel.selected) == 30 and sel.deferred == ()
    assert len(R.candidates(s).selected) == 10                       # the default cap


def test_a_negative_limit_is_refused():
    with pytest.raises(ValueError, match="--limit"):
        R.candidates({}, limit=-1)


def test_tracker_exclusion_with_count():
    s = store(entry("a", company="Fjordline A/S", title="Data Engineer"), entry("b"), entry("c", status="ranked", company="Fjordline A/S", title="Data Engineer"))
    sel = R.candidates(s, tracker_keys=[("fjordline a-s", "DATA engineer")])
    assert sel.selected == ("b",) and sel.excluded_by_tracker == 1
    every = R.candidates(s, include_all=True, tracker_keys=[("Fjordline A/S", "Data Engineer")])
    assert every.excluded_by_tracker == 2 and "a" not in every.selected and "c" not in every.selected


def test_projection_is_compact():
    e = entry("a", strengths=["x"], gates=list(GATES), rank_score=80, last_fetch_error="boom")
    assert set(R.projection(e)) == {"key", "title", "company", "url", "board", "deadline", "posted_date"}


def test_candidate_output_counts():
    s = store(entry("a"), entry("b"), entry("c"), entry("d", status="ranked"), entry("e", company="Taken", title="Role e"))
    sel = R.candidates(s, limit=2, tracker_keys=[("Taken", "Role e")])
    assert (sel.eligible, len(sel.selected), len(sel.deferred), sel.excluded_by_tracker, sel.total_entries) == (3, 2, 1, 1, 5)


def test_unreachable_reselected_expired_not():
    s = store(entry("u", status="unreachable"), entry("x", status="expired"))
    assert R.candidates(s).selected == ("u",)


def test_soonest_deadline_is_selected_first_so_a_cap_defers_the_least_urgent():
    s = store(entry("late", deadline="2026-12-01"), entry("none"), entry("soon", deadline="2026-10-20"))
    assert R.candidates(s, limit=1).selected == ("soon",)


# ── unreachable, expired, give up ──
def fail(s, key, now, error="HTTP 404"):
    return R.apply_result(s, R.Failed(key, error), now)


def test_unreachable_fields_and_error_cap():
    s = store(entry("a"))
    assert fail(s, "a", NOW, "line one\n\n   line   two " + "x" * 400) == "unreachable"
    e = s["a"]
    assert e["status"] == "unreachable" and e["fetch_attempts"] == 1 and e["first_unreachable"] == "2026-10-19T09:00:00Z"
    assert e["last_fetch_error"].startswith("line one line two x") and len(e["last_fetch_error"]) == 200 and "\n" not in e["last_fetch_error"]


def test_first_unreachable_is_stable():
    s = store(entry("a"))
    fail(s, "a", NOW)
    fail(s, "a", later(5))
    assert s["a"]["first_unreachable"] == "2026-10-19T09:00:00Z" and s["a"]["fetch_attempts"] == 2


@pytest.mark.parametrize("bad", [None, "2", True, -3, 1.5, [], {}])
def test_bad_attempt_count_treated_as_zero(bad):
    s = store(entry("a", status="unreachable", fetch_attempts=bad))
    fail(s, "a", NOW)
    assert s["a"]["fetch_attempts"] == 1


def test_give_up_needs_three_attempts_and_48h():
    s = store(entry("a"))
    assert fail(s, "a", NOW) == "unreachable"
    assert fail(s, "a", later(24)) == "unreachable"
    assert fail(s, "a", later(48)) == "gave_up"
    e = s["a"]
    assert e["status"] == "gave_up" and e["fetch_attempts"] == 3 and e["last_fetch_error"] and e["first_unreachable"]
    two = store(entry("b"))
    fail(two, "b", NOW)
    assert fail(two, "b", later(60)) == "unreachable"               # 2 attempts, however long apart: not yet


def test_47h59_does_not_give_up():
    s = store(entry("a"))
    fail(s, "a", NOW)
    fail(s, "a", later(1))
    assert fail(s, "a", NOW + timedelta(hours=47, minutes=59)) == "unreachable"
    assert s["a"]["status"] == "unreachable"


def test_two_same_day_runs_do_not_exhaust_attempts():
    s = store(entry("a"))
    for hours in (0, 3, 6, 9, 12):
        assert fail(s, "a", later(hours)) in ("unreachable",)
    assert s["a"]["status"] == "unreachable"


def test_unreachable_past_deadline_expires():
    s = store(entry("a", deadline="2026-10-18", fetch_attempts=2, first_unreachable="2026-10-15T00:00:00Z", last_fetch_error="x"))
    assert fail(s, "a", NOW) == "expired"
    assert s["a"]["status"] == "expired" and not {"fetch_attempts", "first_unreachable", "last_fetch_error"} & set(s["a"])


def test_a_404_or_403_alone_never_expires_a_posting():
    s = store(entry("a"))
    for error in ("HTTP 404, which says nothing about whether it is open", "plain fetch: 403, the client was refused"):
        assert fail(s, "a", NOW, error) != "expired"
    assert s["a"]["status"] == "unreachable"


def test_success_clears_retry_fields():
    s = store(entry("a", status="unreachable", fetch_attempts=2, first_unreachable="2026-10-16T00:00:00Z", last_fetch_error="x"))
    assert R.apply_result(s, scored("a"), NOW) == "ranked"
    assert not {"fetch_attempts", "first_unreachable", "last_fetch_error"} & set(s["a"])


def test_agent_expired_immediate_and_clean():
    s = store(entry("a", status="unreachable", fetch_attempts=2, first_unreachable="2026-10-16T00:00:00Z", last_fetch_error="x"))
    assert R.apply_result(s, R.Closed("a", "the page says closed"), NOW) == "expired"
    assert s["a"]["status"] == "expired" and "fetch_attempts" not in s["a"] and "last_fetch_error" not in s["a"]


def test_expired_entry_revivable_with_all():
    s = store(entry("a", status="expired"), entry("g", status="gave_up", fetch_attempts=3))
    sel = R.candidates(s, include_all=True)
    assert set(sel.selected) == {"a", "g"}
    assert R.apply_result(s, scored("a"), NOW) == "ranked" and R.apply_result(s, scored("g"), NOW) == "ranked"
    assert s["g"]["status"] == "ranked" and "fetch_attempts" not in s["g"]


def test_result_types_are_the_only_statuses():
    s = store(entry("a"))
    for junk in ({"key": "a", "status": "scored"}, ("a", "expired"), "expired", None):
        with pytest.raises(R.ResultError, match="not a rank result"):
            R.apply_result(s, junk, NOW)
    assert s["a"]["status"] == "new"


def test_unknown_key_is_error():
    s = store(entry("a"))
    with pytest.raises(R.ResultError, match="no posting 'zz'"):
        R.apply_result(s, scored("zz"), NOW)
    assert s == store(entry("a"))


@pytest.mark.parametrize("status", ["applied", "skipped"])
def test_rank_never_changes_an_applied_or_skipped_entry(status):
    s = store(entry("a", status=status))
    for result in (scored("a"), R.Closed("a", "x"), R.Failed("a", "x")):
        with pytest.raises(R.ResultError):
            R.apply_result(s, result, NOW)
    assert s["a"]["status"] == status and "rank_score" not in s["a"]


# ── write-back ──
def test_ranked_entry_fields():
    s = store(entry("a"))
    assert R.apply_result(s, scored("a", 72, "good", gate("location", "FLAG", "frequent travel"), deadline="2026-11-15"), NOW) == "ranked"
    e = s["a"]
    assert (e["status"], e["rank_score"], e["rank_verdict"], e["rank_date"], e["deadline"]) == ("ranked", 72, "good", "2026-10-19", "2026-11-15")
    assert e["strengths"] == ["s1"] and e["gaps"] == ["g1"] and [g["name"] for g in e["gates"]] == ["eligibility", "permit", "location", "language"]


def test_verdict_never_overwrites_location_place():
    s = store(entry("a", locations=["Aarhus", "Remote"]))
    R.apply_result(s, scored("a", gates=gate("location", "FAIL", "relocation required")), NOW)
    assert s["a"]["locations"] == ["Aarhus", "Remote"] and "location" not in s["a"]


def test_veto_fields_persisted():
    s = store(entry("a"))
    R.apply_result(s, scored("a", 90, "strong", gate("language", "FAIL", 'posting requires Swedish: "svenska krävs"')), NOW)
    assert R.vetoes(s["a"]) == ('language: posting requires Swedish: "svenska krävs"',) and s["a"]["rank_score"] == 90


def test_null_deadline_keeps_stored():
    s = store(entry("a", deadline="2026-11-30"))
    R.apply_result(s, scored("a", deadline=None), NOW)
    assert s["a"]["deadline"] == "2026-11-30"


def test_fresh_deadline_replaces_stored():
    s = store(entry("a", deadline="2026-11-30"))
    R.apply_result(s, scored("a", deadline="2026-12-05"), NOW)
    assert s["a"]["deadline"] == "2026-12-05"


def test_an_unparseable_fresh_deadline_never_erases_a_real_stored_one():
    s = store(entry("a", deadline="2026-11-30"))
    R.apply_result(s, scored("a", deadline="ASAP"), NOW)
    assert s["a"]["deadline"] == "2026-11-30"
    t = store(entry("b"))
    assert R.apply_result(t, scored("b", deadline="ASAP"), NOW) == "ranked" and t["b"]["deadline"] == "ASAP"


def test_strengths_gaps_capped_verbatim_replaced():
    s = store(entry("a"))
    R.apply_result(s, scored("a", strengths=("<b>Python</b> & SQL", "two", "three", "four"), gaps=("g1", "g2")), NOW)
    assert s["a"]["strengths"] == ["<b>Python</b> & SQL", "two", "three"] and s["a"]["gaps"] == ["g1", "g2"]
    R.apply_result(s, scored("a", strengths=("only",), gaps=("new gap",)), NOW)
    assert s["a"]["strengths"] == ["only"] and s["a"]["gaps"] == ["new gap"]


def test_rescore_idempotent():
    s = store(entry("a", status="ranked"))
    R.apply_result(s, scored("a", 70), NOW)
    first = copy.deepcopy(s)
    R.apply_result(s, scored("a", 70), NOW)
    assert s == first
    R.apply_result(s, scored("a", 55, "moderate"), NOW)
    assert s["a"]["rank_score"] == 55 and s["a"]["rank_verdict"] == "moderate"


def test_apply_is_additive_only():
    e = entry("a", alt_urls=["https://y.example/a"], custom="kept")
    s = store(e)
    before = copy.deepcopy(e)
    R.apply_result(s, scored("a"), NOW)
    assert all(s["a"][k] == v for k, v in before.items() if k != "status")
    assert set(s["a"]) - set(before) <= set(R.RANK_FIELDS)


@pytest.mark.parametrize("bad", [True, 101, -1, 72.0, "72", None])
def test_a_bad_score_is_an_error_and_writes_nothing(bad):
    s = store(entry("a", deadline="2026-11-01"))
    before = copy.deepcopy(s)
    with pytest.raises(R.ResultError, match="score must be an integer"):
        R.apply_result(s, scored("a", bad, deadline="2026-12-24"), NOW)
    assert s == before


def test_scored_with_passed_deadline_expires():
    s = store(entry("a", fetch_attempts=2, first_unreachable="2026-10-16T00:00:00Z", status="unreachable"))
    assert R.apply_result(s, scored("a", deadline="2026-10-01"), NOW) == "expired"
    e = s["a"]
    assert e["status"] == "expired" and e["deadline"] == "2026-10-01" and not {"rank_score", "rank_verdict", "rank_date", "gates", "fetch_attempts"} & set(e)


def test_scored_with_stored_passed_deadline_expires_and_keeps_old_ranking_out():
    s = store(entry("a", deadline="2026-10-01", status="ranked", rank_score=80, rank_verdict="strong", rank_date="2026-10-05"))
    assert R.apply_result(s, scored("a"), NOW) == "expired" and "rank_score" not in s["a"]


def test_deadline_boundary_table():
    for deadline, stored, want, kept in (("2026-10-19", None, "ranked", "2026-10-19"), ("2026-10-20", None, "ranked", "2026-10-20"),
                                          ("ASAP", None, "ranked", "ASAP"), ("2026-12-01", "2026-10-01", "ranked", "2026-12-01"),
                                          ("2026-10-18", None, "expired", "2026-10-18")):
        s = store(entry("a", deadline=stored))
        assert R.apply_result(s, scored("a", deadline=deadline), NOW) == want and s["a"]["deadline"] == kept


def test_past_deadline_does_not_hide_score_error():
    s = store(entry("a", deadline="2026-10-01"))
    with pytest.raises(R.ResultError):
        R.apply_result(s, scored("a", 150), NOW)
    assert s["a"]["status"] == "new"


def test_clock_injection():
    s = store(entry("a"))
    naive = datetime(2026, 1, 5, 12, 0)
    R.apply_result(s, scored("a"), naive)
    assert s["a"]["rank_date"] == "2026-01-05"
    zoned = datetime(2026, 1, 5, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    t = store(entry("b"))
    R.apply_result(t, scored("b"), zoned)
    assert t["b"]["rank_date"] == "2026-01-06"                      # the clock is read as UTC


def test_ranked_not_reselected_by_default():
    s = store(entry("a"))
    R.apply_result(s, scored("a"), NOW)
    assert R.candidates(s).selected == () and R.candidates(s, include_all=True).selected == ("a",)


def test_merge_copies_only_rank_fields_and_loses_nothing_a_search_added():
    mine = store(entry("a"))
    R.apply_result(mine, scored("a"), NOW)
    fresh = store(entry("a", tags=["new-tag"], last_seen="2026-10-19"), entry("b"))
    R.merge(fresh, mine, ["a", "gone"])
    assert fresh["a"]["tags"] == ["new-tag"] and fresh["a"]["last_seen"] == "2026-10-19" and fresh["a"]["rank_score"] == 70
    assert "rank_score" not in fresh["b"] and "gone" not in fresh


# ── the sweep ──
def test_sweep_expire_closing_later():
    s = store(entry("past", status="ranked", deadline="2026-10-18"), entry("today", status="ranked", deadline="2026-10-19"),
              entry("week", status="ranked", deadline="2026-10-26"), entry("soon", status="ranked", deadline="2026-10-21"),
              entry("later", status="ranked", deadline="2026-10-27"))
    out = R.sweep(s, TODAY)
    assert out.expired == ("past",) and out.closing_soon == ("today", "soon", "week")
    assert s["past"]["status"] == "expired" and s["later"]["status"] == "ranked" and s["today"]["status"] == "ranked"


def test_sweep_scope_ranked_minus_excluded():
    s = store(*(entry(k, status=st, deadline="2026-10-01") for k, st in (("r", "ranked"), ("n", "new"), ("u", "unreachable"), ("x", "expired"),
                                                                       ("ap", "applied"), ("ex", "ranked"))))
    out = R.sweep(s, TODAY, exclude=["ex"])
    assert out.checked == 1 and out.expired == ("r",)
    assert s["ex"]["status"] == "ranked" and s["n"]["status"] == "new" and s["ap"]["status"] == "applied"


def test_sweep_ignores_missing_deadline():
    s = store(entry("a", status="ranked", first_seen="2020-01-01"), entry("b", status="ranked", deadline=""), entry("c", status="ranked", deadline=None))
    out = R.sweep(s, TODAY)
    assert out.expired == () and out.unparseable == () and all(e["status"] == "ranked" for e in s.values())


def test_sweep_reports_unparseable_with_portal():
    s = store(entry("a", status="ranked", deadline="ASAP", board="jobnet"), entry("b", status="ranked", deadline="31.12.2025", board="jobindex"),
              entry("c", status="ranked", deadline="2026-13-45", board="jobbank"))
    out = R.sweep(s, TODAY)
    assert out.expired == () and set(out.unparseable) == {("a", "jobnet"), ("b", "jobindex"), ("c", "jobbank")}
    assert all(e["status"] == "ranked" for e in s.values())


def test_sweep_is_pure_date_comparison():
    s = store(entry("a", status="ranked", deadline="2026-10-01", rank_score=80, strengths=["s"]))
    out = R.sweep(s, TODAY)
    assert out.expired == ("a",)
    assert s["a"]["rank_score"] == 80 and s["a"]["strengths"] == ["s"]            # nothing else on the entry changes


# ── flags ──
def test_flag_thresholds_7_and_30():
    def f(deadline=None, posted=None):
        return R.flags(entry("a", deadline=deadline, posted_date=posted), TODAY)
    assert R.URGENT_DAYS == 7 and R.OLD_DAYS == 30
    assert f(deadline="2026-10-26").urgent and not f(deadline="2026-10-27").urgent
    assert f(posted="2026-09-19").old == "" and f(posted="2026-09-18").old                      # 31 days is old, 30 is not
    assert f(deadline="2026-10-19").urgent == "deadline today (2026-10-19)" and "tomorrow" in f(deadline="2026-10-20").urgent
    assert f(deadline="2026-10-18").urgent == ""                                                # past: expired, not urgent


def test_urgent_within_7_days_wins_ties():
    s = store(entry("calm", status="ranked", rank_score=80, deadline="2026-12-01"), entry("rush", status="ranked", rank_score=80, deadline="2026-10-22"),
              entry("high", status="ranked", rank_score=90), entry("low-urgent", status="ranked", rank_score=60, deadline="2026-10-20"))
    assert R.arrange(s, list(s), TODAY).shortlist == ("high", "rush", "calm", "low-urgent")


def test_sort_score_then_urgency():
    s = store(entry("a", status="ranked", rank_score=71), entry("b", status="ranked", rank_score=72, deadline=None),
              entry("c", status="ranked", rank_score=71, deadline="2026-10-21"))
    assert R.arrange(s, ["a", "b", "c"], TODAY, top=2) == R.Arranged(("b", "c"), ("a",), ())


def test_nonparseable_deadline_skipped():
    f = R.flags(entry("a", deadline="next Friday", posted_date="last spring"), TODAY)
    assert f.urgent == "" and f.old == "" and f.unparseable == ("deadline", "posted_date")


def test_stale_over_30_days_flagged_with_age():
    assert R.flags(entry("a", posted_date="2026-08-30"), TODAY).old == "posted 2026-08-30, 50 days ago"
    assert R.flags(entry("a", posted_date="2024-05-13"), TODAY).old == "posted 2024-05-13, 29 months ago"


def test_stale_posting_never_excluded():
    s = store(entry("old", status="ranked", rank_score=80, posted_date="2024-05-13"), entry("new", status="ranked", rank_score=60))
    assert R.arrange(s, list(s), TODAY).shortlist == ("old", "new")


def test_stale_with_future_deadline_mentions_deadline():
    f = R.flags(entry("a", posted_date="2024-05-13", deadline="2026-11-30"), TODAY)
    assert "deadline 2026-11-30 is still ahead" in f.old


def test_missing_posted_date_no_flag_age_not_stored():
    s = store(entry("a", status="ranked", first_seen="2020-01-01"), entry("b", status="ranked", posted_date=""))
    before = copy.deepcopy(s)
    assert R.flags(s["a"], TODAY) == R.Flags() and R.flags(s["b"], TODAY) == R.Flags()
    assert s == before


def test_unparseable_posted_date_reported():
    assert R.flags(entry("a", posted_date="13/05/2024"), TODAY).unparseable == ("posted_date",)


# ── vetoes and ordering ──
def test_vetoed_job_is_listed_apart_and_never_shortlisted():
    s = store(entry("hi", status="ranked", rank_score=95, gates=list(gate("eligibility", "FAIL", 'citizenship required: "EU citizens only"'))),
              entry("ok", status="ranked", rank_score=50, gates=list(GATES)), entry("flag", status="ranked", rank_score=40,
                                                                                    gates=list(gate("location", "FLAG", "frequent travel"))))
    out = R.arrange(s, list(s), TODAY, top=5)
    assert out.vetoed == ("hi",) and out.shortlist == ("ok", "flag")
    assert R.gate_flags(s["flag"]) == ("location: frequent travel",) and R.gate_flags(s["hi"]) == ()


def test_arrange_leaves_out_everything_not_ranked():
    s = store(entry("a", status="expired", rank_score=99), entry("b", status="unreachable"), entry("c", status="ranked", rank_score=10))
    assert R.arrange(s, list(s), TODAY).shortlist == ("c",)

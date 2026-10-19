"""search.py: running the boards in-process, the board switches, dedupe and consolidation, the health check, and the shared
board behaviours (contract.py, http.py) that every adapter inherits."""
from __future__ import annotations

import argparse
import inspect
import json
import types
from datetime import date
from pathlib import Path

import httpx
import pytest

from jobloop import jobkey, search, state, tracker
from jobloop.boards import contract, http
from jobloop.boards.contract import BoardError, Query

TODAY = date(2026, 10, 19)
FIX = Path(__file__).parent / "fixtures"
ROWS = json.loads((FIX / "search" / "results_multicity.json").read_text())
HOST = "api.example.com"


def board(name="fake", search_fn=None, detail=None, enabled=True, terms="", **kw):
    return types.SimpleNamespace(NAME=name, HOSTS=(HOST,), DEFAULT_ENABLED=enabled, TERMS=terms, ACK_WAIVES_ROBOTS=False,
                                 search=search_fn or (lambda q, h: []), detail=detail or (lambda ref, h: {}), **kw)


def factory(module, **kw):
    return search.make_client(module, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), sleep=lambda s: None, **kw)


def run(boards, tmp_path, queries=("support",), **kw):
    mods = {b.NAME: b for b in boards}
    kw.setdefault("sleep", lambda s: None)
    return search.search(list(mods), list(queries), tmp_path, modules=mods, client_factory=factory, today=TODAY, **kw)


def fails(kind, message="no"):
    def raiser(q, h):
        raise BoardError(kind, message)
    return raiser


def rows_fn(rows):
    return lambda q, h: [dict(r) for r in rows]


# ── storing what is found ──
def test_results_are_stored_with_provenance_dates_board_and_source(tmp_path):
    report = run([board("alpha", rows_fn([ROWS[6]]))], tmp_path)
    (entry,) = report.new
    assert (entry["key"], entry["board"], entry["source"], entry["posted_date"], entry["deadline"], entry["status"]) == \
        ("fjord-systems_platform-engineer", "alpha", "board", "2026-10-15", "2026-11-30", "new")
    assert state.load(tmp_path) == {entry["key"]: entry} and entry["first_seen"] == "2026-10-19"
    assert report.outcomes == [search.Outcome("alpha", "ok", 1)]


def test_a_posting_with_no_date_is_stored_with_a_null_posted_date_never_inferred(tmp_path):
    undated = {**ROWS[6], "date": None, "deadline": None}
    (entry,) = run([board("alpha", rows_fn([undated]))], tmp_path).new
    assert entry["posted_date"] is None and entry["deadline"] is None


def test_the_default_window_is_14_days_and_what_a_board_cannot_filter_is_filtered_here(tmp_path):
    seen = []

    def fn(q, h):
        seen.append(q.recency_days)
        return [dict(ROWS[7]), dict(ROWS[6])]

    report = run([board("alpha", fn)], tmp_path)
    assert seen == [14] and [e["title"] for e in report.new] == ["Platform Engineer"]
    assert {e["title"] for e in run([board("alpha", fn)], tmp_path / "b", recency_days=None).new} == {"Platform Engineer", "Old Role"}


def test_the_limit_is_applied_after_the_board_answers_never_a_negative_slice(tmp_path):
    many = [{"title": f"T{i}", "company": "C", "url": f"https://x.io/{i}", "date": "2026-10-18"} for i in range(9)]
    assert len(run([board("alpha", rows_fn(many))], tmp_path, limit=3).new) == 3
    with pytest.raises(BoardError):
        run([board("alpha", rows_fn(many))], tmp_path / "b", limit=-1)


def test_a_query_given_as_a_string_gets_the_location_and_window(tmp_path):
    seen = []
    run([board("alpha", lambda q, h: seen.append(q) or [])], tmp_path, queries=["data  engineer"], location="Aarhus", recency_days=3)
    assert seen == [Query("data engineer", "Aarhus", 3, 20)]


def test_no_query_is_refused_before_any_request(tmp_path):
    with pytest.raises(ValueError, match="no query"):
        run([board("alpha", fails("network"))], tmp_path, queries=())


# ── one board failing never stops the others ──
@pytest.mark.parametrize("kind", contract.KINDS)
def test_one_failing_board_does_not_abort_the_run(tmp_path, kind):
    report = run([board("alpha", fails(kind)), board("beta", rows_fn([ROWS[6]]))], tmp_path)
    assert [(o.board, o.status) for o in report.outcomes] == [("alpha", kind), ("beta", "ok")] and len(report.new) == 1


def test_an_adapter_that_crashes_is_a_parse_failure_and_the_others_still_run(tmp_path):
    def crash(q, h):
        raise KeyError("surprise")

    report = run([board("alpha", crash), board("beta", rows_fn([ROWS[6]]))], tmp_path)
    assert report.outcomes[0].status == "parse" and "KeyError" in report.outcomes[0].message and len(report.new) == 1


@pytest.mark.parametrize("kind,asked", [("blocked", 1), ("rate_limited", 1), ("robots", 1), ("network", 1), ("invalid", 2), ("parse", 2)])
def test_a_board_that_refuses_us_is_not_asked_again_but_a_bad_query_moves_on(tmp_path, kind, asked):
    calls = []

    def fn(q, h):
        calls.append(q.text)
        raise BoardError(kind, "x")

    run([board("alpha", fn)], tmp_path, queries=["one", "two"])
    assert len(calls) == asked


def test_a_later_query_still_succeeds_after_an_earlier_one_was_invalid(tmp_path):
    def fn(q, h):
        if q.text == "one":
            raise BoardError("invalid", "needs a location")
        return [dict(ROWS[6])]

    report = run([board("alpha", fn)], tmp_path, queries=["one", "two"])
    assert report.outcomes[0].status == "invalid" and len(report.new) == 1


# ── what must never happen ──
def test_a_corrupt_state_file_stops_the_search_before_any_request_and_is_left_alone(tmp_path):
    file = state.path(tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text("{broken")
    calls = []
    with pytest.raises(state.StateCorrupt):
        run([board("alpha", lambda q, h: calls.append(1) or [dict(ROWS[6])])], tmp_path)
    assert calls == [] and file.read_text() == "{broken"


def test_a_board_with_terms_is_refused_before_any_request_until_acknowledged(tmp_path):
    calls = []
    guarded = board("alpha", lambda q, h: calls.append(1) or [], terms="Personal use only.")
    with pytest.raises(PermissionError, match="Personal use only"):
        run([guarded], tmp_path)
    assert calls == []


def test_search_never_uses_a_shell_a_subprocess_or_a_harness():
    source = inspect.getsource(search)
    assert search.HARNESS is False
    assert not any(word in source for word in ("subprocess", "os.system", "Popen", "Turns", "import turns"))


def test_the_adapter_receives_the_shared_http_client_in_process(tmp_path):
    seen = []
    run([board("alpha", lambda q, h: seen.append(h) or [])], tmp_path)
    assert len(seen) == 1 and isinstance(seen[0].client, http.Client)


# ── dedupe ──
def test_url_match_regardless_of_key(tmp_path):
    state.save(tmp_path, {"older-rule-key": {"key": "older-rule-key", "url": "https://jobs.example.com/fjord/600001?utm_source=old",
                                              "title": "Platform Engineer", "company": "Fjord", "status": "ranked", "tags": [], "locations": [],
                                              "alt_urls": [], "first_seen": "2026-09-01", "last_seen": "2026-09-01", "rank_score": 77}})
    report = run([board("alpha", rows_fn([ROWS[6]]))], tmp_path)
    stored = state.load(tmp_path)
    assert report.new == [] and list(stored) == ["older-rule-key"] and stored["older-rule-key"]["rank_score"] == 77
    assert stored["older-rule-key"]["last_seen"] == "2026-10-19"


def test_company_title_match_dedupes_across_boards_and_keeps_both_addresses(tmp_path):
    other = {**ROWS[6], "url": "https://other.example.org/p/9?ref=abc", "id": "9"}
    report = run([board("alpha", rows_fn([ROWS[6]])), board("beta", rows_fn([other]))], tmp_path)
    (entry,) = report.new
    assert entry["board"] == "alpha" and entry["alt_urls"] == ["https://other.example.org/p/9?ref=abc"] and report.duplicates == 1
    assert len(state.load(tmp_path)) == 1


def test_the_same_run_twice_adds_nothing_and_reports_nothing_new(tmp_path):
    run([board("alpha", rows_fn(ROWS[:7]))], tmp_path)
    before = state.load(tmp_path)
    report = run([board("alpha", rows_fn(ROWS[:7]))], tmp_path)
    assert report.new == [] and report.updated == [] and set(state.load(tmp_path)) == set(before)


def test_non_latin_postings_from_one_company_stay_separate_entries(tmp_path):
    report = run([board("alpha", rows_fn(ROWS[8:10]))], tmp_path)
    assert len(report.new) == 2 and len({e["key"] for e in report.new}) == 2 and all(jobkey.is_canonical(e["key"]) for e in report.new)


def test_every_stored_key_is_canonical(tmp_path):
    run([board("alpha", rows_fn(ROWS))], tmp_path, recency_days=None)
    assert all(jobkey.is_canonical(k) for k in state.load(tmp_path))


def test_tracker_pair_excluded_and_the_scrape_never_writes_the_tracker(tmp_path):
    tracker.record_drafted(tmp_path / "tracker.csv", company="FJORD systems", role="platform  engineer", today=TODAY,
                           cv_file="applications/x/cv.tex", cover_letter_file="applications/x/cover.tex")
    before = (tmp_path / "tracker.csv").read_bytes()
    report = run([board("alpha", rows_fn([ROWS[6], ROWS[0]]))], tmp_path)
    stored = state.load(tmp_path)
    assert [e["company"] for e in report.new] == ["Nordlys Staffing ApS"] and report.tracked == 1
    assert stored["fjord-systems_platform-engineer"]["status"] == "skipped" and stored["fjord-systems_platform-engineer"]["tags"] == ["tracked"]
    assert (tmp_path / "tracker.csv").read_bytes() == before


# ── mass posting ──
def test_multi_city_posting_consolidated_to_one_row(tmp_path):
    report = run([board("alpha", rows_fn(ROWS[:6]))], tmp_path)
    (entry,) = report.new
    assert entry["locations"] == ["Copenhagen", "Aarhus", "Odense", "Aalborg", "Esbjerg", "Randers"] and entry["tags"] == ["mass_posted"]
    assert len(entry["alt_urls"]) == 5 and report.duplicates == 5 and len(state.load(tmp_path)) == 1


def test_a_posting_in_one_place_is_not_mass_posted(tmp_path):
    (entry,) = run([board("alpha", rows_fn([ROWS[6]]))], tmp_path).new
    assert entry["tags"] == [] and len(entry["locations"]) == 1


def test_the_same_posting_on_two_boards_in_one_city_is_a_duplicate_not_a_mass_posting(tmp_path):
    twin = {**ROWS[6], "url": "https://b.example.org/1"}
    (entry,) = run([board("alpha", rows_fn([ROWS[6]])), board("beta", rows_fn([twin]))], tmp_path).new
    assert entry["tags"] == [] and entry["locations"] == ["Copenhagen"]


def test_a_later_run_in_another_city_adds_the_location_and_the_tag_to_the_stored_entry(tmp_path):
    run([board("alpha", rows_fn([ROWS[0]]))], tmp_path)
    report = run([board("alpha", rows_fn([ROWS[1]]))], tmp_path)
    (entry,) = report.updated
    assert entry["locations"] == ["Copenhagen", "Aarhus"] and entry["tags"] == ["mass_posted"] and report.new == []


def test_mass_posting_never_changes_fit_status_or_excludes(tmp_path):
    run([board("alpha", rows_fn([ROWS[0]]))], tmp_path)
    stored = state.load(tmp_path)
    key = next(iter(stored))
    stored[key].update(status="ranked", rank_score=90, rank_verdict="PASS")
    state.save(tmp_path, stored)
    run([board("alpha", rows_fn(ROWS[:6]))], tmp_path)
    entry = state.load(tmp_path)[key]
    assert (entry["status"], entry["rank_score"], entry["rank_verdict"]) == ("ranked", 90, "PASS") and "mass_posted" in entry["tags"]


def test_an_old_posting_with_a_future_deadline_is_dropped_by_the_window_and_kept_when_the_window_is_off(tmp_path):
    old = {**ROWS[7], "deadline": "2026-12-01"}
    assert run([board("alpha", rows_fn([old]))], tmp_path).new == []
    assert len(run([board("alpha", rows_fn([old]))], tmp_path / "all", recency_days=None).new) == 1


@pytest.mark.parametrize("status", state.STATUSES)
def test_all_statuses_count_as_seen(tmp_path, status):
    run([board("alpha", rows_fn([ROWS[6]]))], tmp_path)
    with state.transaction(tmp_path) as s:
        s["fjord-systems_platform-engineer"]["status"] = status
    report = run([board("alpha", rows_fn([ROWS[6]]))], tmp_path)
    assert report.new == [] and len(state.load(tmp_path)) == 1 and state.load(tmp_path)["fjord-systems_platform-engineer"]["status"] == status


def test_every_presented_posting_is_a_stored_entry(tmp_path):
    report = run([board("alpha", rows_fn(ROWS))], tmp_path, recency_days=None)
    stored = state.load(tmp_path)
    assert report.new and all(stored[e["key"]] == e for e in report.new)


@pytest.mark.parametrize("a,b,same", [("ACME_INC", "Acme, Inc.", True), ("soc-analyst", "SOC Analyst", True), ("Straße", "STRASSE", True),
                                       ("Café", "Cafe\u0301", True), ("腾讯", "阿里巴巴", False), ("Acme", "Acme Two", False)])
def test_identity_normalisation_cases(a, b, same):
    rows = (tracker.Row(company=a, role="Engineer"),)
    assert bool(tracker.match_rows(rows, b, "engineer")) is same


def test_a_tracker_with_a_byte_order_mark_and_any_column_order_is_read(tmp_path):
    columns = list(reversed(tracker.HEADER))
    row = {"role": "Platform Engineer", "company": "Fjord Systems", "status": "applied"}
    (tmp_path / "tracker.csv").write_bytes(("\ufeff" + ",".join(columns) + "\n" + ",".join(row.get(c, "") for c in columns) + "\n").encode())
    report = run([board("alpha", rows_fn([ROWS[6]]))], tmp_path)
    assert report.new == [] and report.tracked == 1


# ── closed postings ──
def test_expired_deadline_never_presented_but_kept_as_expired_so_it_is_not_re_examined(tmp_path):
    shut = {**ROWS[6], "deadline": "2026-10-01"}
    report = run([board("alpha", rows_fn([shut]))], tmp_path)
    assert report.new == [] and report.closed == 1 and [e["status"] for e in state.load(tmp_path).values()] == ["expired"]
    assert run([board("alpha", rows_fn([shut]))], tmp_path).new == []


def test_a_stored_posting_whose_deadline_passed_is_marked_expired(tmp_path):
    run([board("alpha", rows_fn([ROWS[6]]))], tmp_path)
    report = run([board("alpha", rows_fn([{**ROWS[6], "deadline": "2026-10-01"}]))], tmp_path)
    (entry,) = report.updated
    assert entry["status"] == "expired"


def test_a_deadline_of_today_is_still_open(tmp_path):
    assert len(run([board("alpha", rows_fn([{**ROWS[6], "deadline": "2026-10-19"}]))], tmp_path).new) == 1


def test_an_applied_posting_is_never_expired_by_a_search(tmp_path):
    run([board("alpha", rows_fn([ROWS[6]]))], tmp_path)
    with state.transaction(tmp_path) as s:
        s["fjord-systems_platform-engineer"]["status"] = "applied"
    run([board("alpha", rows_fn([{**ROWS[6], "deadline": "2026-10-01"}]))], tmp_path)
    assert state.load(tmp_path)["fjord-systems_platform-engineer"]["status"] == "applied"


@pytest.mark.parametrize("message,closed", [("closed", True), ("freehire posting x closed on 2026-10-01", True), ("jobindex has no live posting 1: it was removed or has expired", True),
                                            ("jobnet has no posting 1; it may have been taken down", True), ("example has no such page (404)", False)])
def test_only_a_not_found_that_says_closed_is_closed(message, closed):
    assert search.is_closed(BoardError("not_found", message)) is closed


@pytest.mark.parametrize("kind", ["blocked", "rate_limited", "robots", "network", "parse", "invalid"])
def test_a_403_or_any_failure_is_never_taken_for_closed(kind):
    assert not search.is_closed(BoardError(kind, "closed"))


def test_verify_open_drops_a_posting_the_board_says_is_closed_and_takes_the_deadline_from_detail(tmp_path):
    def detail(ref, h):
        if ref == "600002":
            raise BoardError("not_found", "closed")
        return {"title": "x", "url": "https://x.io/1", "deadline": "2026-12-24"}

    a, b = {**ROWS[6], "deadline": None, "id": "600001"}, {**ROWS[7], "date": "2026-10-16", "id": "600002"}
    report = run([board("alpha", rows_fn([a, b]), detail)], tmp_path, verify_open=True)
    assert [e["title"] for e in report.new] == ["Platform Engineer"] and report.new[0]["deadline"] == "2026-12-24" and report.closed == 1
    assert state.load(tmp_path)["fjord-systems_old-role"]["status"] == "expired"


def test_a_refused_detail_check_never_closes_a_posting_and_stops_checking(tmp_path):
    calls = []

    def detail(ref, h):
        calls.append(ref)
        raise BoardError("blocked", "403")

    report = run([board("alpha", rows_fn(ROWS[6:10]), detail)], tmp_path, verify_open=True, recency_days=None)
    assert len(calls) == 1 and len(report.new) == 4 and report.closed == 0 and report.outcomes[0].status == "blocked"


def test_verify_open_is_off_by_default_and_bounded(tmp_path):
    calls = []
    many = [{"title": f"T{i}", "company": f"C{i}", "url": f"https://x.io/{i}", "date": "2026-10-18"} for i in range(40)]
    run([board("alpha", rows_fn(many), lambda r, h: calls.append(r) or {})], tmp_path, limit=40)
    assert calls == []
    run([board("alpha", rows_fn(many), lambda r, h: calls.append(r) or {})], tmp_path / "b", limit=40, verify_open=True)
    assert len(calls) == search.VERIFY_LIMIT


def test_verify_open_asks_only_about_postings_not_already_stored(tmp_path):
    run([board("alpha", rows_fn([ROWS[6]]))], tmp_path)
    calls = []
    run([board("alpha", rows_fn([ROWS[6]]), lambda r, h: calls.append(r) or {})], tmp_path, verify_open=True)
    assert calls == []


def test_a_board_is_paced_and_capped_per_run_so_one_search_cannot_hammer_it(tmp_path):
    pauses = []

    def greedy(q, c):
        for n in range(200):
            c.get(f"https://{HOST}/page/{n}")
        return [dict(ROWS[6])]

    open_robots = lambda module, **kw: http.Client(module.NAME, module.HOSTS, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
                                                   robots=lambda url: True)
    mods = {"alpha": board("alpha", greedy)}
    report = search.search(["alpha"], ["x"], tmp_path, modules=mods, client_factory=open_robots, today=TODAY, sleep=pauses.append)
    assert report.outcomes[0].status == "invalid" and "60 requests" in report.outcomes[0].message
    assert len(pauses) == search.MAX_REQUESTS - 1 and set(pauses) == {search.MIN_GAP}


def test_a_crash_while_checking_openness_still_does_not_stop_the_other_boards(tmp_path):
    def boom(ref, c):
        raise KeyError("surprise")

    report = run([board("alpha", rows_fn([ROWS[8]]), boom), board("beta", rows_fn([ROWS[0]]))], tmp_path, verify_open=True)
    assert [o.status for o in report.outcomes] == ["parse", "ok"] and len(report.new) == 2


def test_a_date_that_is_not_iso_is_stored_as_null(tmp_path):
    (entry,) = run([board("alpha", rows_fn([{**ROWS[6], "date": "last Tuesday", "deadline": "soon"}]))], tmp_path).new
    assert entry["posted_date"] is None and entry["deadline"] is None


def test_search_ids_round_trip_into_detail_with_a_real_adapter(tmp_path):
    body = (FIX / "boards" / "freehire" / "search_agent.json").read_bytes()
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, content=body if request.url.path.endswith("/search") else (FIX / "boards" / "freehire" / "detail_open.json").read_bytes(),
                              headers={"content-type": "application/json"})

    def make(module, **kw):
        return search.make_client(module, transport=httpx.MockTransport(handler), sleep=lambda s: None)

    report = search.search(["freehire"], ["data engineer"], tmp_path, client_factory=make, today=TODAY, recency_days=None, verify_open=True, sleep=lambda s: None)
    details = [p for p in paths if "/jobs/" in p and not p.endswith("/search")]
    assert report.outcomes[0].status == "ok" and len(details) == len(report.new) and details


# ── which boards are on ──
def test_danish_boards_off_by_default(tmp_path):
    assert search.enabled_boards(tmp_path) == ["freehire"]
    assert not any(search.is_enabled(tmp_path, n) for n in ("jobindex", "jobnet", "jobbank", "jobdanmark", "linkedin"))


def test_disabled_board_skipped_and_reported(tmp_path, capsys):
    search.set_enabled(tmp_path, "freehire", False)
    assert search.enabled_boards(tmp_path) == []
    assert search.main(argparse.Namespace(me=str(tmp_path), action=None, query=["x"], board=[], location="", recency_days=14, limit=20, verify_open=False)) == 1
    assert "no board is on" in capsys.readouterr().out


def test_a_board_can_be_switched_on_and_off_and_the_choice_is_recorded(tmp_path):
    search.set_enabled(tmp_path, "jobnet", True)
    assert search.enabled_boards(tmp_path) == ["freehire", "jobnet"]
    search.set_enabled(tmp_path, "jobnet", False)
    assert search.enabled_boards(tmp_path) == ["freehire"]
    assert json.loads((tmp_path / "boards.json").read_text())["boards"]["jobnet"]["enabled"] is False


def test_a_board_with_terms_needs_an_explicit_recorded_acknowledgement(tmp_path):
    with pytest.raises(PermissionError, match="--acknowledge-terms"):
        search.set_enabled(tmp_path, "jobbank", True)
    assert "jobbank" not in search.enabled_boards(tmp_path) and not (tmp_path / "boards.json").exists()
    search.set_enabled(tmp_path, "jobbank", True, acknowledged_terms=True, today=TODAY)
    assert "jobbank" in search.enabled_boards(tmp_path)
    assert json.loads((tmp_path / "boards.json").read_text())["boards"]["jobbank"]["terms_acknowledged"] == "2026-10-19"
    search.set_enabled(tmp_path, "jobbank", False)
    search.set_enabled(tmp_path, "jobbank", True)            # the recorded acknowledgement stands
    assert "jobbank" in search.enabled_boards(tmp_path)


def test_enabled_in_the_file_without_an_acknowledgement_is_still_off(tmp_path):
    (tmp_path / "boards.json").write_text(json.dumps({"boards": {"linkedin": {"enabled": True}}}))
    assert "linkedin" not in search.enabled_boards(tmp_path)
    with pytest.raises(PermissionError):
        search.require_runnable(tmp_path, search.module_of("linkedin"))


def test_a_corrupt_board_settings_file_is_never_overwritten(tmp_path):
    (tmp_path / "boards.json").write_text("{nope")
    with pytest.raises(search.BoardsConfigCorrupt):
        search.enabled_boards(tmp_path)
    with pytest.raises(search.BoardsConfigCorrupt):
        search.set_enabled(tmp_path, "jobnet", True)
    assert (tmp_path / "boards.json").read_text() == "{nope"


def test_an_unknown_board_is_refused():
    with pytest.raises(ValueError, match="no built-in board"):
        search.set_enabled(Path("."), "monster", True)


def test_a_board_installed_with_an_install_script_cannot_exist_the_registry_is_fixed():
    with pytest.raises(ValueError, match="no built-in board"):
        search.module_of("../evil")
    from jobloop import boards
    assert boards.BUILT_IN == ("freehire", "linkedin", "jobindex", "jobnet", "jobbank", "jobdanmark")


def test_the_client_a_board_gets_uses_its_hosts_timeout_retries_and_waives_robots_only_with_acknowledged_terms():
    li = search.module_of("linkedin")
    c = search.make_client(li)
    assert (c.hosts, c.retries, c.allowed("https://www.linkedin.com/jobs")) == (("www.linkedin.com",), 0, True)
    assert search.make_client(search.module_of("jobindex")).http.timeout.read == 60.0
    assert search.make_client(search.module_of("freehire")).retries == 3
    assert search.make_client(search.module_of("jobbank"), retries=0).retries == 0


# ── a real adapter through the search ──
def test_a_real_adapter_through_search_stores_what_its_fixture_holds(tmp_path):
    body = (FIX / "boards" / "freehire" / "search_agent.json").read_bytes()
    handler = lambda r: httpx.Response(200, content=body, headers={"content-type": "application/json"})

    def make(module, **kw):
        return search.make_client(module, transport=httpx.MockTransport(handler), sleep=lambda s: None)

    report = search.search(["freehire"], ["data engineer"], tmp_path, client_factory=make, today=date(2026, 10, 19), recency_days=None, sleep=lambda s: None)
    assert report.outcomes[0].status == "ok" and report.new and all(e["board"] == "freehire" and e["source"] == "board" for e in report.new)
    assert all(jobkey.is_canonical(e["key"]) for e in report.new) and set(state.load(tmp_path)) == {e["key"] for e in report.new}


def test_a_real_adapter_rate_limited_is_an_outcome_not_a_crash(tmp_path):
    handler = lambda r: httpx.Response(429, content=b'{"error": "rate limit exceeded"}')
    report = search.search(["freehire"], ["x"], tmp_path, client_factory=lambda m, **kw: search.make_client(m, transport=httpx.MockTransport(handler), sleep=lambda s: None),
                           sleep=lambda s: None)
    assert report.outcomes[0].status == "rate_limited" and state.load(tmp_path) == {}


# ── health ──
def test_degraded_parser_symptoms_are_read_from_results_already_in_hand_without_a_request(tmp_path):
    rows = [{"title": "Engineer &amp; Co", "company": None, "url": "https://x.io/1", "date": None},
            {"title": "<b>Analyst</b>", "company": None, "url": "https://x.io/2", "date": None}]
    calls = []
    report = run([board("alpha", lambda q, h: calls.append(1) or [dict(r) for r in rows])], tmp_path)
    assert len(calls) == 1 and set(report.outcomes[0].symptoms) == {"no result names a company", "titles hold undecoded entities or HTML"}
    assert search.degraded([]) == () and search.degraded([{"title": "Ok", "company": "A"}] * 3) == ()


def test_zero_results_from_a_board_with_stored_history_is_suspect(tmp_path):
    run([board("alpha", rows_fn([ROWS[6]]))], tmp_path)
    report = run([board("alpha", rows_fn([]))], tmp_path)
    assert report.outcomes[0].symptoms == ("returned nothing although 1 stored postings came from it",)
    assert run([board("beta", rows_fn([]))], tmp_path).outcomes[0].symptoms == ()


def test_a_failing_board_is_not_also_called_degraded(tmp_path):
    run([board("alpha", rows_fn([ROWS[6]]))], tmp_path)
    assert run([board("alpha", fails("blocked"))], tmp_path).outcomes[0].symptoms == ()


def test_the_detail_check_downgrades_a_board_whose_detail_text_is_unreadable():
    mod = lambda detail: board("alpha", lambda q, c: [dict(ROWS[6])], detail)
    good = {"title": "Engineer", "url": "https://x.io/1", "description": "Build things for a living."}
    assert health(mod(lambda ref, c: good), check_detail=True).status == "ok"
    for bad in ({**good, "description": "Build &amp; run &lt;things&gt;"}, {**good, "description": None}, {**good, "description": "<p>Build</p>"}, {}):
        h = health(mod(lambda ref, c, bad=bad: bad), check_detail=True)
        assert h.status == "broken" and "readable" in h.message
    assert health(mod(lambda ref, c: {}), check_detail=False).status == "ok"


def test_the_detail_check_reports_a_refusal_as_one_not_as_unreadable_and_a_gone_posting_as_unjudged():
    assert health(board("alpha", lambda q, c: [dict(ROWS[6])], fails("blocked")), check_detail=True).status == "blocked"
    h = health(board("alpha", lambda q, c: [dict(ROWS[6])], fails("not_found", "closed")), check_detail=True)
    assert h.status == "ok" and "not checked" in h.message


def test_a_result_that_is_an_external_ad_is_skipped_and_the_board_is_not_called_broken():
    h = health(board("alpha", lambda q, c: [dict(ROWS[6])], fails("invalid", "may only use https on the board's host")), check_detail=True)
    assert h.status == "ok" and "external ad" in h.message


def test_the_probe_budget_per_portal_covers_the_detail_check_too():
    def detail(ref, c):
        for _ in range(9):
            c.get(f"https://{HOST}/d")
        return {}

    open_robots = lambda module, **kw: http.Client(module.NAME, module.HOSTS, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
                                                   robots=lambda url: True)
    h = health(board("alpha", lambda q, c: [dict(ROWS[6])], detail), check_detail=True, max_requests=3, client_factory=open_robots)
    assert h.status == "broken" and h.requests == 3


class Clock:
    def __init__(self, step=0.0):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


def health(module, **kw):
    kw.setdefault("client_factory", factory)
    return search.health(module, **kw)


def test_a_board_that_answers_is_ok_after_one_tiny_search():
    seen = []
    h = health(board("alpha", lambda q, c: seen.append(q) or [dict(ROWS[6])]))
    assert (h.status, h.requests) == ("ok", 0) and [(q.text, q.limit) for q in seen] == [("engineer", 3)]


@pytest.mark.parametrize("kind,status", [("rate_limited", "rate_limited"), ("blocked", "blocked"), ("robots", "robots"), ("network", "broken"),
                                          ("parse", "broken"), ("invalid", "broken"), ("not_found", "broken")])
def test_a_rate_limited_or_blocked_portal_is_told_apart_from_a_broken_one(kind, status):
    assert health(board("alpha", fails(kind))).status == status


def test_a_rate_limit_is_never_retried_and_never_called_broken():
    calls = []
    h = health(board("alpha", lambda q, c: calls.append(1) or fails("rate_limited")(q, c)))
    assert h.status == "rate_limited" and len(calls) == 1


def test_broken_only_after_the_probe_and_the_retry_both_come_back_empty():
    words = []
    h = health(board("alpha", lambda q, c: words.append(q.text) or []))
    assert h.status == "broken" and words == ["engineer", "manager"]
    answers = iter([[], [dict(ROWS[6])]])
    assert health(board("alpha", lambda q, c: next(answers))).status == "ok"


def test_the_health_client_has_no_retries_and_a_timeout_within_the_budget():
    seen = {}

    def spy(module, **kw):
        seen.update(kw)
        return factory(module)

    search.health(board("alpha"), client_factory=spy, seconds=5.0)
    assert seen == {"timeout": 5.0, "retries": 0}


def test_the_probe_budget_is_a_hard_request_counter():
    calls = []

    def greedy(q, c):
        for n in range(50):
            calls.append(n)
            c.get(f"https://{HOST}/x")
        return [dict(ROWS[6])]

    h = health(board("alpha", greedy), max_requests=3)
    assert h.status == "broken" and "limit of 3 requests" in h.message and len(calls) == 4 and h.requests == 3


def test_the_probe_budget_is_a_hard_time_limit():
    def slow(q, c):
        c.get(f"https://{HOST}/x")
        c.get(f"https://{HOST}/y")
        return [dict(ROWS[6])]

    h = health(board("alpha", slow), seconds=5.0, clock=Clock(step=3.0))
    assert h.status == "broken" and h.requests == 1


def test_json_and_post_requests_count_against_the_budget_too():
    def fn(q, c):
        c.json(f"https://{HOST}/a")
        c.post_json(f"https://{HOST}/b", {})
        c.json(f"https://{HOST}/c")
        return []

    open_robots = lambda module, **kw: http.Client(module.NAME, module.HOSTS, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
                                                   robots=lambda url: True)
    h = health(board("alpha", fn), max_requests=2, client_factory=open_robots)
    assert h.status == "broken" and h.requests == 2


def test_health_of_a_real_adapter_that_is_rate_limited():
    handler = lambda r: httpx.Response(429)
    h = search.health(search.module_of("freehire"), client_factory=lambda m, **kw: search.make_client(m, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw))
    assert h.status == "rate_limited"


def test_health_of_a_real_adapter_that_answers():
    body = (FIX / "boards" / "freehire" / "search_agent.json").read_bytes()
    handler = lambda r: httpx.Response(200, content=body, headers={"content-type": "application/json"})
    h = search.health(search.module_of("freehire"), client_factory=lambda m, **kw: search.make_client(m, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw))
    assert h.status == "ok" and h.requests == 1


# ── the command line ──
def parse(*argv):
    p = argparse.ArgumentParser()
    search.add_arguments(p)
    return p.parse_args(list(argv))


def test_boards_list_enable_disable_commands(tmp_path, capsys):
    me = ["--me", str(tmp_path)]
    assert search.main(parse(*me, "boards", "list")) == 0
    listing = capsys.readouterr().out
    assert "freehire     on" in listing and "jobnet       off" in listing and "acknowledge" in listing
    assert search.main(parse(*me, "boards", "enable", "jobnet")) == 0
    assert search.main(parse(*me, "boards", "enable", "jobbank")) == 1
    assert "--acknowledge-terms" in capsys.readouterr().out
    assert search.main(parse(*me, "boards", "enable", "jobbank", "--acknowledge-terms")) == 0
    assert search.main(parse(*me, "boards", "disable")) == 1
    assert search.main(parse(*me, "boards", "disable", "jobnet")) == 0
    assert search.enabled_boards(tmp_path) == ["freehire", "jobbank"]


def test_search_command_runs_the_boards_that_are_on_and_prints_new_postings(tmp_path, capsys, monkeypatch):
    captured = {}

    def fake(boards, queries, me, **kw):
        captured.update(boards=boards, queries=queries, kw=kw)
        return search.Report([search.Outcome("freehire", "ok", 1)], [{"key": "k_t", "title": "T", "company": "C", "locations": ["Aarhus"], "tags": []}])

    monkeypatch.setattr(search, "search", fake)
    assert search.main(parse("--me", str(tmp_path), "--query", "a", "-q", "b", "--recency-days", "3")) == 0
    assert captured["boards"] == ["freehire"] and captured["queries"] == ["a", "b"] and captured["kw"]["recency_days"] == 3
    assert "k_t  T at C (Aarhus)" in capsys.readouterr().out
    fake_many = search.Report([search.Outcome("freehire", "ok", 1)], [{"key": "k_t", "title": "T", "company": "C", "locations": ["A", "B", "C"], "tags": ["mass_posted"]}])
    monkeypatch.setattr(search, "search", lambda *a, **k: fake_many)
    search.main(parse("--me", str(tmp_path), "--query", "a"))
    assert "posted in 3 places: A; B; C" in capsys.readouterr().out


def test_a_search_with_a_degraded_board_probes_it_once_and_a_healthy_board_prints_no_health_line(tmp_path, capsys, monkeypatch):
    probed = []
    monkeypatch.setattr(search, "health", lambda module, **kw: probed.append(module.NAME) or search.Health(module.NAME, "broken", "x"))
    monkeypatch.setattr(search, "search", lambda *a, **k: search.Report([search.Outcome("freehire", "ok", 3), search.Outcome("jobnet", "ok", 0, "", ("no result names a company",))]))
    search.main(parse("--me", str(tmp_path), "--query", "a", "--board", "freehire", "--board", "jobnet"))
    out = capsys.readouterr().out
    assert probed == ["jobnet"] and out.count("looks degraded") == 1
    assert [line for line in out.splitlines() if "looks degraded" in line][0].split()[0].endswith("jobnet")


def test_health_command_probes_every_enabled_board_and_the_named_one_with_the_detail_check(tmp_path, capsys, monkeypatch):
    probed = []
    monkeypatch.setattr(search, "health", lambda module, **kw: probed.append((module.NAME, kw)) or search.Health(module.NAME, "ok"))
    search.set_enabled(tmp_path, "jobnet", True)
    assert search.main(parse("--me", str(tmp_path), "health")) == 0
    assert probed == [("freehire", {"check_detail": True}), ("jobnet", {"check_detail": True})]
    probed.clear()
    assert search.main(parse("--me", str(tmp_path), "health", "jobindex")) == 0 and [n for n, _ in probed] == ["jobindex"]
    assert "ok" in capsys.readouterr().out


def test_health_never_changes_board_settings_even_when_every_board_is_broken(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "health", lambda module, **kw: search.Health(module.NAME, "broken", "x"))
    search.set_enabled(tmp_path, "jobnet", True)
    before = (tmp_path / "boards.json").read_bytes()
    assert search.main(parse("--me", str(tmp_path), "health")) == 1
    assert (tmp_path / "boards.json").read_bytes() == before


def test_search_command_needs_a_query_and_exits_1_when_no_board_answered(tmp_path, capsys, monkeypatch):
    assert search.main(parse("--me", str(tmp_path))) == 1 and "--query" in capsys.readouterr().out
    monkeypatch.setattr(search, "search", lambda *a, **k: search.Report([search.Outcome("freehire", "blocked")]))
    assert search.main(parse("--me", str(tmp_path), "--query", "a")) == 1


def test_search_command_reports_a_corrupt_store_and_leaves_it(tmp_path, capsys):
    file = state.path(tmp_path)
    file.parent.mkdir(parents=True)
    file.write_text("[]")
    assert search.main(parse("--me", str(tmp_path), "--query", "a")) == 1
    assert "not a posting store" in capsys.readouterr().out and file.read_text() == "[]"


def test_audit_command_reports_and_exits_1_only_for_a_failure(tmp_path, capsys):
    state.save(tmp_path, {"a/b_c": {"key": "a/b_c", "url": "https://x.io/1", "company": "A", "title": "B"}})
    assert search.main(parse("--me", str(tmp_path), "audit")) == 1
    state.save(tmp_path, {"old_key_1": {"key": "old_key_1", "url": "https://x.io/1", "company": "A", "title": "B"}})
    assert search.main(parse("--me", str(tmp_path), "audit")) == 0
    assert "legacy keys 1" in capsys.readouterr().out


def test_health_command_refuses_a_board_with_unacknowledged_terms(tmp_path, capsys):
    assert search.main(parse("--me", str(tmp_path), "health", "linkedin")) == 1
    assert "terms" in capsys.readouterr().out


@pytest.mark.parametrize("flag", [["--bogus", "1"], ["-z"], ["--keyword", "python"], ["--page", "2"], ["--format", "json"]])
def test_unknown_filter_name_raises_instead_of_being_ignored(flag, capsys):
    called = []
    with pytest.raises(SystemExit) as e:
        contract.run_cli(board("fake", lambda q, h: called.append(1) or []), ["search", "--query", "x", *flag])
    assert e.value.code == 2 and called == [] and capsys.readouterr().out == ""


@pytest.mark.parametrize("argv", [["--limit", "-1"], ["--limit", "0"], ["--limit", "2.5"], ["--limit", "abc"], ["--recency-days", "0"],
                                  ["--recency-days", "-3"], ["--limit"]])
def test_a_bad_or_missing_number_is_refused_before_any_request(argv, capsys):
    called = []
    try:
        code = contract.run_cli(board("fake", lambda q, h: called.append(1) or []), ["search", "--query", "x", *argv])
    except SystemExit as e:
        code = e.code
    assert code in (1, 2) and called == [] and capsys.readouterr().out == ""


def test_a_query_option_with_no_value_is_refused_before_any_request(capsys):
    called = []
    with pytest.raises(SystemExit):
        contract.run_cli(board("fake", lambda q, h: called.append(1) or []), ["search", "--query", "--limit", "5"])
    assert called == [] and capsys.readouterr().out == ""


# ── shared board rows: the contract and the HTTP client every adapter inherits (also tests/test_boards_http.py) ──
def shared_client(handler, **kw):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    sleeps = []
    c = http.Client("example", [HOST], transport=httpx.MockTransport(wrapped), sleep=sleeps.append, robots=kw.pop("robots", lambda url: True), **kw)
    c.seen, c.sleeps = seen, sleeps
    return c


def test_every_adapter_has_the_contract_attributes_and_functions():
    from jobloop import boards
    for name in boards.BUILT_IN:
        m = boards.load(name)
        assert isinstance(m.NAME, str) and m.NAME == name and m.HOSTS and all(isinstance(h, str) for h in m.HOSTS)
        assert isinstance(m.DEFAULT_ENABLED, bool) and isinstance(m.TERMS, str) and callable(m.search) and callable(m.detail)
        assert tuple(inspect.signature(m.search).parameters)[:2] == ("query", "http") and tuple(inspect.signature(m.detail).parameters)[:2] == ("ref", "http")


def test_a_command_other_than_search_or_detail_is_refused_by_the_shared_cli(capsys):
    with pytest.raises(SystemExit) as e:
        contract.run_cli(board("fake"), ["delete", "--query", "x"])
    assert e.value.code == 2 and "invalid choice" in capsys.readouterr().err


def test_an_error_code_is_always_from_the_fixed_vocabulary():
    for kind in contract.KINDS:
        assert contract.BoardError(kind, "x").kind == kind
    with pytest.raises(AssertionError):
        contract.BoardError("something_else", "x")


def test_an_empty_search_is_a_success_with_zero_results_and_exit_0(capsys):
    assert contract.run_cli(board("fake"), ["search", "--query", "data"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["results"] == [] and out["meta"]["count"] == 0


def test_a_challenge_or_wall_page_is_an_error_never_an_empty_success(tmp_path):
    wall = httpx.Response(200, text="<html>Access denied. Please enable JavaScript and cookies</html>", headers={"content-type": "text/html"})
    mod = board("alpha", lambda q, h: h.get(f"https://{HOST}/search", html=False) and [])
    report = search.search(["alpha"], ["x"], tmp_path, modules={"alpha": mod}, today=TODAY,
                           client_factory=lambda m, **kw: search.make_client(m, transport=httpx.MockTransport(lambda r: wall)))
    assert report.outcomes[0].status == "blocked"


def test_the_envelope_has_the_same_meta_for_every_board(capsys):
    for name in ("one", "two"):
        contract.run_cli(board(name), ["search", "--query", "data"])
    metas = [json.loads(line)["meta"] for line in capsys.readouterr().out.splitlines()]
    assert [sorted(m) for m in metas] == [sorted(metas[0])] * 2 and metas[0]["recency_days"] == 14


def test_a_detail_that_is_no_posting_is_a_parse_error_on_stderr(capsys):
    assert contract.run_cli(board("fake", detail=lambda ref, h: {}), ["detail", "123"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "fake: parse" in captured.err


def test_the_backoff_is_exponential_bounded_and_honours_a_short_retry_after():
    c = shared_client(lambda r: httpx.Response(503))
    with pytest.raises(BoardError):
        c.get(f"https://{HOST}/x")
    assert c.sleeps == [1.0, 2.0, 4.0] and len(c.seen) == 4
    slow = shared_client(lambda r: httpx.Response(429, headers={"retry-after": "2"}), retries=2)
    with pytest.raises(BoardError):
        slow.get(f"https://{HOST}/x")
    assert slow.sleeps == [2.0, 2.0]


def test_a_429_then_a_200_succeeds_on_the_second_call():
    answers = iter([httpx.Response(429), httpx.Response(200, json={"ok": 1})])
    c = shared_client(lambda r: next(answers))
    assert c.json(f"https://{HOST}/x") == {"ok": 1} and len(c.seen) == 2


@pytest.mark.parametrize("status", [400, 404, 422])
def test_a_plain_4xx_is_not_retried_and_names_its_status_kind(status):
    c = shared_client(lambda r: httpx.Response(status))
    with pytest.raises(BoardError):
        c.get(f"https://{HOST}/x")
    assert len(c.seen) == 1 and c.sleeps == []


def test_a_connection_failure_fails_fast_without_retry():
    def refuse(request):
        raise httpx.ConnectError("refused")

    c = shared_client(refuse)
    with pytest.raises(BoardError) as e:
        c.get(f"https://{HOST}/x")
    assert e.value.kind == "network" and len(c.seen) == 1 and c.sleeps == []


def test_every_request_has_a_timeout_for_get_post_and_html():
    assert http.Client("x", [HOST]).http.timeout.read == 20.0 and http.Client("x", [HOST], timeout=7.0).http.timeout.connect == 7.0


def test_one_retry_policy_serves_get_post_and_html():
    for call in (lambda c: c.get(f"https://{HOST}/x"), lambda c: c.get(f"https://{HOST}/x", html=True),
                 lambda c: c.post_json(f"https://{HOST}/x", {})):
        c = shared_client(lambda r: httpx.Response(503))
        with pytest.raises(BoardError):
            call(c)
        assert c.sleeps == [1.0, 2.0, 4.0]


def test_no_request_goes_out_without_the_honest_user_agent_on_get_post_and_robots():
    c = shared_client(lambda r: httpx.Response(200, json={}))
    c.get(f"https://{HOST}/x")
    c.post_json(f"https://{HOST}/x", {})
    c.raw(f"https://{HOST}/robots.txt")
    assert {r.headers["user-agent"] for r in c.seen} == {http.USER_AGENT}


def test_the_post_sends_a_json_content_type():
    c = shared_client(lambda r: httpx.Response(200, json={}))
    c.post_json(f"https://{HOST}/x", {"a": 1})
    assert c.seen[0].headers["content-type"] == "application/json"


def test_a_disallowed_url_is_reported_robots_not_empty():
    c = shared_client(lambda r: httpx.Response(200, text="x"), robots=lambda url: False)
    with pytest.raises(BoardError) as e:
        c.get(f"https://{HOST}/jobs", html=True)
    assert e.value.kind == "robots" and c.seen == []


def test_a_robots_file_that_is_a_login_redirect_is_not_taken_as_permission():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"location": f"https://{HOST}/login"})
        return httpx.Response(200, text="page", headers={"content-type": "text/html"})

    c = http.Client("example", [HOST], transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(BoardError) as e:
        c.get(f"https://{HOST}/jobs", html=True)
    assert e.value.kind == "robots"


def test_a_bad_query_makes_no_request_because_the_query_is_refused_first(tmp_path):
    called = []
    with pytest.raises(BoardError):
        run([board("alpha", lambda q, h: called.append(1) or [])], tmp_path, queries=["--help"])
    assert called == []

"""boards/http.py and boards/contract.py: the rules every adapter inherits, and what must never happen."""
from __future__ import annotations

import json
import types
from datetime import date

import httpx
import pytest

from jobloop.boards import contract, http

HOST = "api.example.com"


def client(handler, **kw):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    sleeps = []
    c = http.Client("example", [HOST], transport=httpx.MockTransport(wrapped), sleep=sleeps.append, robots=kw.pop("robots", lambda url: True), **kw)
    c.seen, c.sleeps = seen, sleeps
    return c


def ok(request):
    return httpx.Response(200, json={"results": []})


def test_the_user_agent_is_honest_and_names_the_project():
    c = client(ok)
    c.get(f"https://{HOST}/x")
    ua = c.seen[0].headers["user-agent"]
    assert "job-search-loop" in ua and "github.com/mrcloudchase/job-search-loop" in ua and "Mozilla" not in ua


@pytest.mark.parametrize("url", [f"http://{HOST}/x", "https://evil.example.org/x", f"https://{HOST}.evil.org/x",
                                 f"https://user:pw@{HOST}/x", "https://api.example.com@evil.org/x", "file:///etc/passwd", "ftp://api.example.com/x"])
def test_only_https_on_the_declared_hosts_and_no_userinfo_tricks(url):
    c = client(ok)
    with pytest.raises(contract.BoardError) as e:
        c.get(url)
    assert e.value.kind == "invalid" and c.seen == []


def test_a_redirect_off_the_declared_hosts_is_never_followed():
    c = client(lambda r: httpx.Response(302, headers={"location": "https://evil.example.org/steal"}))
    with pytest.raises(contract.BoardError) as e:
        c.get(f"https://{HOST}/x")
    assert e.value.kind == "invalid" and len(c.seen) == 1


def test_a_redirect_on_the_same_host_is_followed_and_a_loop_is_stopped():
    calls = iter([httpx.Response(301, headers={"location": "/y"}), httpx.Response(200, text="done")])
    assert client(lambda r: next(calls)).get(f"https://{HOST}/x").text == "done"
    loop = client(lambda r: httpx.Response(302, headers={"location": "/x"}))
    with pytest.raises(contract.BoardError, match="redirected too many"):
        loop.get(f"https://{HOST}/x")


def test_429_is_retried_with_a_bounded_retry_after_then_reported_as_rate_limited():
    c = client(lambda r: httpx.Response(429, headers={"retry-after": "9999"}))
    with pytest.raises(contract.BoardError) as e:
        c.get(f"https://{HOST}/x")
    assert e.value.kind == "rate_limited" and len(c.seen) == 4 and c.sleeps == [30.0, 30.0, 30.0]


def test_5xx_backs_off_exponentially_then_is_a_network_failure_and_recovery_is_used():
    c = client(lambda r: httpx.Response(503))
    with pytest.raises(contract.BoardError) as e:
        c.get(f"https://{HOST}/x")
    assert e.value.kind == "network" and c.sleeps == [1.0, 2.0, 4.0]
    answers = iter([httpx.Response(500), httpx.Response(200, text="fine")])
    assert client(lambda r: next(answers)).get(f"https://{HOST}/x").text == "fine"


@pytest.mark.parametrize("status", [401, 403])
def test_a_refusal_is_reported_as_blocked_and_never_retried_or_worked_around(status):
    c = client(lambda r: httpx.Response(status))
    with pytest.raises(contract.BoardError) as e:
        c.get(f"https://{HOST}/x")
    assert e.value.kind == "blocked" and len(c.seen) == 1 and c.sleeps == []


def test_a_challenge_page_served_with_200_is_blocked_not_parsed():
    page = httpx.Response(200, text="<html><title>Just a moment...</title></html>", headers={"content-type": "text/html"})
    with pytest.raises(contract.BoardError) as e:
        client(lambda r: page).get(f"https://{HOST}/x")
    assert e.value.kind == "blocked"


@pytest.mark.parametrize("status,kind", [(404, "not_found"), (410, "not_found"), (400, "invalid"), (422, "invalid")])
def test_other_statuses_have_their_own_kinds(status, kind):
    with pytest.raises(contract.BoardError) as e:
        client(lambda r: httpx.Response(status)).get(f"https://{HOST}/x")
    assert e.value.kind == kind


def test_a_response_over_the_size_limit_is_refused():
    with pytest.raises(contract.BoardError, match="more than 100 bytes"):
        client(lambda r: httpx.Response(200, content=b"x" * 500), max_bytes=100).get(f"https://{HOST}/x")


def test_a_timeout_is_a_network_failure():
    def slow(request):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(contract.BoardError) as e:
        client(slow).get(f"https://{HOST}/x")
    assert e.value.kind == "network"


def test_html_is_scraped_only_if_robots_allows_and_the_page_is_not_fetched_otherwise():
    asked = []
    c = client(ok, robots=lambda url: asked.append(url) or False)
    with pytest.raises(contract.BoardError) as e:
        c.get(f"https://{HOST}/jobs", params={"q": "a"}, html=True)
    assert e.value.kind == "robots" and c.seen == [] and asked == [f"https://{HOST}/jobs?q=a"]
    c.get(f"https://{HOST}/api")           # an API call is not gated
    assert len(c.seen) == 1 and len(asked) == 1


def test_json_that_is_not_json_is_a_parse_failure():
    with pytest.raises(contract.BoardError) as e:
        client(lambda r: httpx.Response(200, text="<html>")).json(f"https://{HOST}/x")
    assert e.value.kind == "parse"


# ── the contract ────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", ["", "   ", "-h", "--help", "x" * 201])
def test_a_query_that_is_empty_huge_or_could_be_a_flag_is_refused(text):
    with pytest.raises(contract.BoardError) as e:
        contract.Query(text)
    assert e.value.kind == "invalid"


@pytest.mark.parametrize("kw", [{"limit": 0}, {"limit": 201}, {"recency_days": 0}, {"recency_days": -3}])
def test_limits_and_recency_are_validated(kw):
    with pytest.raises(contract.BoardError):
        contract.Query("data", **kw)


def test_the_default_window_is_fourteen_days_and_none_means_all():
    assert contract.Query("data").recency_days == 14 and contract.Query("data", recency_days=None).recency_days is None


def test_every_result_has_every_field_and_a_missing_one_is_none_never_omitted():
    r = contract.result({"title": "Engineer", "url": "https://x.io/j/1"})
    assert tuple(r) == contract.FIELDS and r["company"] is None and r["id"] == "https://x.io/j/1" and r["deadline"] is None


@pytest.mark.parametrize("raw", [{}, {"title": "T"}, {"url": "https://x.io"}, {"title": "T", "url": "javascript:alert(1)"},
                                 {"title": "T", "url": "file:///etc/passwd"}, {"title": "  ", "url": "https://x.io"}, {"title": "T", "url": "https://"}])
def test_something_that_is_not_a_posting_with_an_http_url_is_dropped(raw):
    assert contract.result(raw) is None


def test_fields_are_coerced_to_short_single_line_text_and_structures_become_none():
    r = contract.result({"title": "A\n\n" + "x" * 1000, "url": "https://x.io/1", "company": ["not", "text"], "location": {"a": 1}, "description": "line1\nline2 "})
    assert "\n" not in r["title"] and len(r["title"]) == 300 and r["company"] is None and r["location"] is None
    assert r["description"] == "line1\nline2"


def test_the_recency_filter_keeps_recent_and_undated_and_drops_old():
    today = date(2026, 10, 19)
    rows = [{"date": "2026-10-10"}, {"date": "2026-09-01"}, {"date": None}, {"date": "garbage"}, {"date": "2026-10-05T08:00:00Z"}]
    kept = contract.recent(rows, 14, today)
    assert [r["date"] for r in kept] == ["2026-10-10", None, "garbage", "2026-10-05T08:00:00Z"]
    assert contract.recent(rows, None, today) == rows


def fake_board(search=None, detail=None, hosts=(HOST,), terms=""):
    return types.SimpleNamespace(NAME="fake", HOSTS=hosts, TERMS=terms, ACK_WAIVES_ROBOTS=False, __doc__="fake",
                                 search=search or (lambda q, h: []), detail=detail or (lambda r, h: {}))


def test_the_cli_prints_the_envelope_and_applies_recency_and_limit_client_side(capsys, monkeypatch):
    rows = [{"title": f"T{i}", "url": f"https://x.io/{i}", "date": "2026-10-18" if i < 3 else "2020-01-01"} for i in range(6)]
    board = fake_board(search=lambda q, h: rows)
    monkeypatch.setattr(contract, "datetime", types.SimpleNamespace(now=lambda tz: types.SimpleNamespace(date=lambda: date(2026, 10, 19))))
    assert contract.run_cli(board, ["search", "--query", "data", "--limit", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["title"] for r in out["results"]] == ["T0", "T1"] and out["meta"]["count"] == 2 and out["meta"]["board"] == "fake"


def test_the_cli_reports_a_failure_on_stderr_with_exit_1_and_nothing_on_stdout(capsys):
    def boom(q, h):
        raise contract.BoardError("rate_limited", "slow down")
    assert contract.run_cli(fake_board(search=boom), ["search", "--query", "data"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip() == "fake: rate_limited: slow down"


def test_a_bad_query_is_a_stderr_error_with_exit_1_too(capsys):
    assert contract.run_cli(fake_board(), ["search", "--query=--help"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "invalid" in captured.err


def test_a_board_with_terms_refuses_to_run_until_the_person_acknowledges_them(capsys):
    board = fake_board(search=lambda q, h: [{"title": "T", "url": "https://x.io/1"}], terms="Personal use only.")
    assert contract.run_cli(board, ["search", "--query", "data"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "Personal use only." in captured.err and "--acknowledge-terms" in captured.err
    assert contract.run_cli(board, ["search", "--query", "data", "--acknowledge-terms"]) == 0
    captured = capsys.readouterr()
    assert "Personal use only." in captured.err and json.loads(captured.out)["meta"]["count"] == 1


def test_a_json_post_follows_the_same_rules_and_never_follows_a_redirect():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(302, headers={"location": f"https://{HOST}/elsewhere"})

    c = client(handler)
    with pytest.raises(contract.BoardError) as e:
        c.post_json(f"https://{HOST}/search", {"q": "a"})
    assert e.value.kind == "invalid" and len(sent) == 1 and sent[0].method == "POST" and json.loads(sent[0].content) == {"q": "a"}
    with pytest.raises(contract.BoardError) as e:
        c.post_json("https://evil.example.org/search", {})
    assert e.value.kind == "invalid" and len(sent) == 1


def test_a_post_is_gated_by_robots_and_backs_off_like_a_get():
    c = client(ok, robots=lambda url: False)
    with pytest.raises(contract.BoardError) as e:
        c.post_json(f"https://{HOST}/search", {})
    assert e.value.kind == "robots" and c.seen == []
    limited = client(lambda r: httpx.Response(429, headers={"retry-after": "3"}))
    with pytest.raises(contract.BoardError) as e:
        limited.post_json(f"https://{HOST}/search", {})
    assert e.value.kind == "rate_limited" and limited.sleeps == [3.0, 3.0, 3.0]


def test_the_response_remembers_where_the_redirects_ended():
    answers = iter([httpx.Response(301, headers={"location": "/final"}), httpx.Response(200, text="x")])
    assert str(client(lambda r: next(answers)).get(f"https://{HOST}/start").request.url) == f"https://{HOST}/final"


def test_an_adapter_can_ask_for_a_longer_timeout_and_no_retries(capsys):
    seen = {}

    class Spy(http.Client):
        def __init__(self, *a, **kw):
            seen.update(kw)
            super().__init__(*a, **kw)

    board = fake_board()
    board.TIMEOUT, board.RETRIES = 60.0, 0
    real = http.Client
    http.Client = Spy
    try:
        contract.run_cli(board, ["search", "--query", "data"])
    finally:
        http.Client = real
    assert seen["timeout"] == 60.0 and seen["retries"] == 0

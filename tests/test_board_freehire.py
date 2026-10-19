"""The freehire adapter, offline against responses captured from the real API (tests/fixtures/boards/freehire), plus one live smoke."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jobloop.boards import freehire
from jobloop.boards.contract import FIELDS, BoardError, Query, result
from jobloop.boards.http import Client

FIX = Path(__file__).parent / "fixtures" / "boards" / "freehire"
SLUG = "data-engineer-team-eiffel-pashb7sp"


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


def client(handler) -> tuple[Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return Client(freehire.NAME, freehire.HOSTS, transport=httpx.MockTransport(record), sleep=lambda s: None), seen


def answer(name: str, status: int = 200):
    return lambda request: httpx.Response(status, content=fixture(name), headers={"content-type": "application/json"})


def search(handler, **kw):
    http, seen = client(handler)
    with http:
        return freehire.search(Query("data engineer", **kw), http), seen


def error_of(call) -> BoardError:
    with pytest.raises(BoardError) as e:
        call()
    return e.value


def test_default_search_uses_agent_endpoint_with_markdown_and_the_query_filters():
    found, seen = search(answer("search_agent.json"), recency_days=7, limit=3)
    assert len(seen) == 1 and seen[0].url.path == "/api/v1/agent/jobs/search"
    assert dict(seen[0].url.params) == {"q": "data engineer", "limit": "3", "offset": "0", "description_format": "markdown",
                                        "posted_within_days": "7"}
    assert len(found) == 2


def test_no_recency_means_no_posted_within_days_parameter():
    _, seen = search(answer("search_agent.json"), recency_days=None)
    assert "posted_within_days" not in seen[0].url.params


def test_a_location_maps_to_countries_regions_or_remote_and_unknown_words_add_no_filter():
    q = lambda loc: freehire.build_params(Query("go", location=loc))  # noqa: E731
    assert q("Germany, DE, Denmark")["countries"] == ["de", "dk"]
    assert q("Europe")["regions"] == ["eu"]
    assert q("Remote")["work_mode"] == "remote"
    plain = q("Copenhagen")
    assert not {"countries", "regions", "cities", "work_mode"} & set(plain)


def test_repeated_countries_reach_the_api_as_repeated_parameters_never_one_comma_value():
    _, seen = search(answer("search_agent.json"), location="Germany, Denmark")
    assert seen[0].url.params.get_list("countries") == ["de", "dk"]


def test_an_unknown_description_format_is_refused_because_the_server_would_fall_back_silently():
    e = error_of(lambda: freehire.build_params(Query("go"), description_format="weird"))
    assert e.kind == "invalid"


def test_paging_beyond_the_servers_depth_bound_is_refused_before_any_request():
    assert error_of(lambda: freehire.build_params(Query("go", limit=20), offset=9_990)).kind == "invalid"
    assert freehire.build_params(Query("go", limit=20), offset=9_980)["offset"] == 9_980


def test_an_overlong_location_is_refused():
    assert error_of(lambda: freehire.build_params(Query("go", location="x" * 101))).kind == "invalid"


def test_results_have_the_contract_shape_with_every_field_present():
    found, _ = search(answer("search_agent.json"))
    first = found[0]
    assert set(first) == set(FIELDS)
    assert first["id"] == SLUG and first["title"] == "Data Engineer" and first["company"] == "Team EIFFEL"
    assert first["location"] == "Netherlands" and first["date"] == "2026-10-19" and first["deadline"] is None
    assert result(first) is not None


def test_description_is_the_servers_text_verbatim_and_an_empty_one_is_null():
    raw = json.loads(fixture("search_agent.json"))["data"][0]
    assert freehire.to_result(raw)["description"] == raw["description"].strip()
    assert freehire.to_result({**raw, "description": ""})["description"] is None
    assert freehire.to_result({**raw, "description": "# Role\n\n* **Build** it"})["description"] == "# Role\n\n* **Build** it"


def test_url_is_the_postings_own_ats_url_as_given():
    raw = json.loads(fixture("search_agent.json"))["data"][0]
    assert freehire.to_result(raw)["url"] == raw["url"] and "freehire.me" not in raw["url"].split("?")[0]


def test_the_internal_numeric_id_and_other_board_fields_are_not_in_a_result():
    found, _ = search(answer("search_agent.json"))
    assert set(found[0]) == set(FIELDS)


def test_title_entities_are_decoded_once_and_a_blank_title_is_untitled():
    assert freehire.to_result({"title": " R&amp;D &#38; Ops  "})["title"] == "R&D & Ops"
    assert freehire.to_result({"title": "AT&amp;amp;T"})["title"] == "AT&amp;T"
    assert freehire.to_result({"title": "   "})["title"] == "(untitled)"
    assert freehire.to_result({})["title"] == "(untitled)"


def test_missing_company_location_and_date_are_none_not_absent():
    out = freehire.to_result({"title": "T", "company": "", "location": None, "posted_at": "soon", "url": "https://x.example/1"})
    assert out["company"] is None and out["location"] is None and out["date"] is None


def test_the_date_is_the_leading_day_and_a_timezone_offset_is_not_converted():
    assert freehire.day("2026-07-06T15:30:00+02:00") == "2026-07-06"
    assert freehire.day("") is None and freehire.day(None) is None


def test_an_empty_result_page_is_success_with_no_results():
    found, _ = search(answer("search_empty.json"))
    assert found == []


def test_a_closed_posting_in_search_results_is_dropped_never_returned_as_open():
    body = json.loads(fixture("search_agent.json"))
    body["data"][0]["closed_at"] = "2026-10-18T00:00:00Z"
    found, _ = search(lambda r: httpx.Response(200, json=body))
    assert [f["id"] for f in found] == [body["data"][1]["public_slug"]]


def test_a_404_from_search_is_an_error_naming_the_endpoint_never_zero_results():
    e = error_of(lambda: search(answer("error_not_found.json", 404)))
    assert e.kind == "invalid" and "/api/v1/agent/jobs/search" in str(e)


def test_one_429_then_success_is_retried():
    replies = iter([httpx.Response(429, content=fixture("rate_limited.json"), headers={"retry-after": "1"}),
                    httpx.Response(200, content=fixture("search_agent.json"))])
    found, seen = search(lambda r: next(replies))
    assert len(seen) == 2 and len(found) == 2


def test_a_persistent_429_is_rate_limited_and_a_persistent_500_is_a_network_failure():
    assert error_of(lambda: search(answer("rate_limited.json", 429))).kind == "rate_limited"
    assert error_of(lambda: search(answer("error_body.json", 500))).kind == "network"


def test_a_400_error_body_is_an_invalid_request_not_an_empty_result():
    assert error_of(lambda: search(answer("error_pagination.json", 400))).kind == "invalid"


def test_a_2xx_that_is_not_json_is_a_parse_error_not_empty_success():
    assert error_of(lambda: search(lambda r: httpx.Response(200, content=b"<html>oops</html>"))).kind == "parse"


def test_a_2xx_without_the_envelope_is_a_parse_error():
    assert error_of(lambda: search(lambda r: httpx.Response(200, json={"error": "boom"}))).kind == "parse"
    assert error_of(lambda: search(lambda r: httpx.Response(200, json={"data": {"a": 1}}))).kind == "parse"


def test_an_unreachable_board_is_a_network_error_not_a_hang_or_empty():
    def refuse(request):
        raise httpx.ConnectError("refused")
    e = error_of(lambda: search(refuse))
    assert e.kind == "network"


def test_a_401_is_blocked_and_not_retried():
    http, seen = client(lambda r: httpx.Response(401, json={"error": "unauthorized"}))
    with http:
        assert error_of(lambda: freehire.detail(SLUG, http)).kind == "blocked"
    assert len(seen) == 1


@pytest.mark.parametrize("ref,slug", [(SLUG, SLUG), (f"https://freehire.me/jobs/{SLUG}", SLUG),
                                      (f"https://freehire.me/jobs/{SLUG}/?utm=1#x", SLUG), ("A1-b2", "A1-b2")])
def test_a_slug_or_a_freehire_job_url_is_accepted(ref, slug):
    assert freehire.normalize_slug(ref) == slug


@pytest.mark.parametrize("ref", ["", "  ", "has space", "-leading", "a/b", "../etc", "https://evil.example/jobs/abc",
                                 "https://freehire.me.evil.io/jobs/abc", "https://freehire.me@evil.io/jobs/abc",
                                 "https://freehire.me/companies/abc", "http://user@freehire.me/jobs/abc", "javascript:alert(1)"])
def test_other_refs_are_refused_before_any_request(ref):
    http, seen = client(answer("detail_open.json"))
    with http:
        assert error_of(lambda: freehire.detail(ref, http)).kind == "invalid"
    assert seen == []


def test_detail_reads_the_job_endpoint_by_slug():
    http, seen = client(answer("detail_open.json"))
    with http:
        freehire.detail(f"https://freehire.me/jobs/{SLUG}", http)
    assert str(seen[0].url) == f"https://freehire.me/api/v1/jobs/{SLUG}"


def test_detail_description_is_readable_text_with_the_facts_block_before_it():
    http, _ = client(answer("detail_open.json"))
    with http:
        out = freehire.detail(SLUG, http)
    assert set(out) == set(FIELDS) and out["deadline"] is None and out["id"] == SLUG
    assert out["description"].startswith("freehire facts:\n")
    assert "salary: EUR 3500–5500/month" in out["description"] and "category: data_engineering" in out["description"]
    assert "board's staleness read: likely-evergreen" in out["description"]
    assert "<p>" not in out["description"] and "Klaar om als Data Engineer" in out["description"]


def test_detail_of_a_closed_posting_is_not_found_and_says_when_it_closed():
    http, _ = client(answer("detail_closed.json"))
    with http:
        e = error_of(lambda: freehire.detail(SLUG, http))
    assert e.kind == "not_found" and "closed on 2026-10-18" in str(e)


def test_detail_404_is_not_found():
    http, seen = client(answer("error_not_found.json", 404))
    with http:
        assert error_of(lambda: freehire.detail("does-not-exist-zzz", http)).kind == "not_found"
    assert len(seen) == 1


def test_detail_with_an_empty_enrichment_has_no_salary_line():
    body = json.loads(fixture("detail_open.json"))
    body["data"]["enrichment"] = {}
    http, _ = client(lambda r: httpx.Response(200, json=body))
    with http:
        out = freehire.detail(SLUG, http)
    assert "salary" not in out["description"] and "seniority" not in out["description"]


def test_html_becomes_text_with_block_breaks_and_decoded_entities():
    assert freehire.clean_html("<ul><li>Build APIs</li><li>Ship &amp; iterate</li></ul>") == "Build APIs\nShip & iterate"
    assert freehire.clean_html("a<br/>b<p>c&#x41;&#66;</p>\n\n\n\n<div>d</div>") == "a\nbcAB\n\nd"
    assert freehire.clean_html("<p>  </p>") is None and freehire.clean_html(None) is None


def test_salary_keeps_its_period_and_zero_is_a_real_bound():
    assert freehire.format_salary({"salary_currency": "EUR", "salary_min": 60000, "salary_max": 80000, "salary_period": "year"}) == "EUR 60000–80000/year"
    assert freehire.format_salary({"salary_currency": "USD", "salary_min": 0}) == "USD 0"
    assert freehire.format_salary({"salary_max": 90}) == "90"
    assert freehire.format_salary({"salary_currency": "EUR"}) is None


def test_only_the_two_read_endpoints_are_ever_requested():
    http, seen = client(lambda r: httpx.Response(200, content=fixture("detail_open.json" if "/jobs/" in r.url.path and "search" not in r.url.path else "search_agent.json")))
    with http:
        freehire.search(Query("go"), http)
        freehire.detail(SLUG, http)
    assert [r.url.path for r in seen] == ["/api/v1/agent/jobs/search", f"/api/v1/jobs/{SLUG}"]
    assert all(r.method == "GET" and r.url.host == "freehire.me" and "auth" not in r.url.path for r in seen)


def test_the_board_declares_the_contract_names():
    assert freehire.NAME == "freehire" and freehire.HOSTS == ("freehire.me",)
    assert freehire.DEFAULT_ENABLED is True and freehire.TERMS == "" and freehire.ACK_WAIVES_ROBOTS is False


@pytest.mark.live
def test_live_search_and_detail_keep_the_contract_shape():
    with Client(freehire.NAME, freehire.HOSTS) as http:
        found = freehire.search(Query("data engineer", recency_days=7, limit=3), http)
        assert 1 <= len(found) <= 3
        for posting in found:
            assert set(posting) == set(FIELDS) and result(posting) is not None
        one = freehire.detail(found[0]["id"], http)
        assert set(one) == set(FIELDS) and one["id"] == found[0]["id"] and result(one) is not None
        print("LIVE", [(p["id"], p["title"], p["date"]) for p in found], one["description"][:200])

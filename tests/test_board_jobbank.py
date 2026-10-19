"""boards/jobbank.py on synthetic fixtures (see tests/fixtures/boards/jobbank/SYNTHETIC.md): requests, feed, detail, dates."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import httpx
import pytest

from jobloop.boards import contract, jobbank
from jobloop.boards.contract import BoardError, Query

FIX = Path(__file__).parent / "fixtures" / "boards" / "jobbank"
TODAY = date(2026, 10, 19)


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


def client(handler, robots=lambda url: True):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    from jobloop.boards.http import Client
    http = Client(jobbank.NAME, jobbank.HOSTS, transport=httpx.MockTransport(wrapped), sleep=lambda s: None, robots=robots)
    http.seen = seen
    return http


def serving(body: bytes, ctype="application/rss+xml", status=200):
    return lambda request: httpx.Response(status, content=body, headers={"content-type": ctype})


def feed_client():
    return client(serving(fixture("feed.xml")))


def query(**kw):
    return Query(**{"text": "data", **kw})


def sent(http):
    return parse_qsl(urlparse(str(http.seen[0].url)).query)


# --- flags and request construction ------------------------------------------------------------------------------------

def test_empty_query_is_refused_before_any_request():
    http = feed_client()
    with pytest.raises(BoardError) as e:
        jobbank.search(Query("   "), http)
    assert e.value.kind == "invalid" and http.seen == []


def test_detail_without_a_posting_id_is_invalid_before_any_request():
    http = client(serving(b""))
    for ref in ("", "   ", "abc"):
        with pytest.raises(BoardError) as e:
            jobbank.detail(ref, http)
        assert e.value.kind == "invalid"
    assert http.seen == []


@pytest.mark.parametrize("limit", [0, -1, 201])
def test_limit_outside_1_to_200_is_refused(limit):
    with pytest.raises(BoardError) as e:
        Query("data", limit=limit)
    assert e.value.kind == "invalid"


def test_a_valid_limit_is_accepted_and_the_search_goes_on():
    assert len(jobbank.search(query(limit=1), feed_client(), today=TODAY)) == 1


@pytest.mark.parametrize("bad", [0, -4, 1.5, "novo nordisk", "12", True])
def test_company_must_be_the_numeric_id_never_a_name(bad):
    with pytest.raises(BoardError) as e:
        jobbank.Filters(company=bad)
    assert e.value.kind == "invalid" and "numeric company id" in str(e.value)


def test_a_company_id_is_sent_as_virk():
    http = feed_client()
    jobbank.search(query(), http, jobbank.Filters(company=4711), TODAY)
    assert ("virk", "4711") in sent(http)


def test_validation_errors_use_the_contracts_one_stderr_line(capsys):
    code = contract.run_cli(jobbank, ["search", "--query", "data", "--limit", "0", "--acknowledge-terms"])
    err = capsys.readouterr()
    assert code == 1 and err.out == ""
    assert err.err.strip().splitlines()[-1].startswith("jobbank: invalid: limit must be between 1 and 200")
    assert len(err.err.strip().splitlines()) == 2  # the terms notice, then the single error line


def test_all_bad_filter_values_raise_the_same_board_error_shape():
    for kw in ({"company": 0}, {"remote": "ja"}, {"since": "19.10.2026"}, {"types": ("99",)}, {"regions": ("Aarhus",)}):
        with pytest.raises(BoardError) as e:
            jobbank.Filters(**kw)
        assert e.value.kind == "invalid"


def test_remote_and_since_values_validated():
    assert jobbank.Filters(remote="helt", since="2026-10-01").remote == "helt"
    for since in ("2026-13-01", "2026-02-30", "2026-1-1", "yesterday"):
        with pytest.raises(BoardError):
            jobbank.Filters(since=since)
    with pytest.raises(BoardError):
        jobbank.Filters(remote="Helt")


def test_codes_checked_against_known_tables():
    jobbank.Filters(types=("3", "13"), education=("24",), work_areas=("31",), industries=("10331",), suitable_for=("2",), regions=("8",))
    for kw in ({"types": ("7",)}, {"education": ("99",)}, {"work_areas": ("31 ",)}, {"industries": ("10331x",)}, {"suitable_for": ("3",)}):
        with pytest.raises(BoardError):
            jobbank.Filters(**kw)


def test_valued_options_reach_the_request():
    http = feed_client()
    jobbank.search(query(), http, jobbank.Filters(remote="helt", exclude="senior", types=("3", "6"), since="2026-10-05"), TODAY)
    params = sent(http)
    assert ("fjernarbejde", "helt") in params and ("antikey", "senior") in params and ("key", "data") in params
    assert [v for k, v in params if k == "cvtype"] == ["3", "6"]


def test_repeatable_filters_keep_order_as_repeated_parameters():
    http = feed_client()
    filters = jobbank.Filters(types=("13", "3"), education=("24", "21"), regions=("13",), work_areas=("31", "43"),
                              industries=("10331", "10331"), suitable_for=("5", "2"))
    jobbank.search(query(), http, filters, TODAY)
    params = sent(http)
    for key, expected in (("cvtype", ["13", "3"]), ("udd", ["24", "21"]), ("amt", ["13"]), ("erf", ["31", "43"]),
                          ("branche", ["10331", "10331"]), ("andet", ["5", "2"])):
        assert [v for k, v in params if k == key] == expected


def test_single_value_filters_map():
    params = jobbank.build_params(query(), jobbank.Filters(exclude="senior", company=7, remote="delvist", since="2026-10-18"), TODAY)
    assert params["key"] == ["data"] and params["antikey"] == ["senior"] and params["virk"] == ["7"]
    assert params["fjernarbejde"] == ["delvist"] and params["oprettet"] == ["2026-10-18"]


def test_recency_becomes_an_oprettet_date_and_the_later_of_recency_and_since_wins():
    assert jobbank.build_params(query(recency_days=14), jobbank.Filters(), TODAY)["oprettet"] == ["2026-10-05"]
    assert jobbank.build_params(query(recency_days=14), jobbank.Filters(since="2026-10-10"), TODAY)["oprettet"] == ["2026-10-10"]
    assert jobbank.build_params(query(recency_days=14), jobbank.Filters(since="2026-09-01"), TODAY)["oprettet"] == ["2026-10-05"]
    assert "oprettet" not in jobbank.build_params(query(recency_days=None), jobbank.Filters(), TODAY)


def test_city_maps_to_region_code():
    assert jobbank.region_for("Aarhus") == "8" and jobbank.region_for("  Copenhagen ") == "2" and jobbank.region_for("Odense") == "13"
    assert jobbank.region_for("Nordjylland") == "6" and jobbank.region_for("13") == "13"
    assert jobbank.region_for("Springfield") is None
    assert jobbank.build_params(query(location="Aarhus"), jobbank.Filters(), TODAY)["amt"] == ["8"]
    assert "amt" not in jobbank.build_params(query(location="Springfield"), jobbank.Filters(), TODAY)


def test_an_unmapped_location_is_filtered_client_side_on_the_location_text():
    http = feed_client()
    found = jobbank.search(query(location="Odense"), http, today=TODAY)  # mapped: region 13, the board filters
    assert len(found) == 6 and ("amt", "13") in sent(http)
    found = jobbank.search(query(location="Aarhus C"), feed_client(), today=TODAY)  # no region for it: matched on the text
    assert [p["id"] for p in found] == ["900102", "900103", "900106"]  # a posting with no location is kept, not guessed away


def test_denmark_as_a_location_is_not_filtered_at_all():
    assert len(jobbank.search(query(location="Denmark"), feed_client(), today=TODAY)) == 6


def test_search_failure_is_a_board_error_with_a_kind():
    http = client(serving(b"oops", "text/plain", 500))
    with pytest.raises(BoardError) as e:
        jobbank.search(query(), http, today=TODAY)
    assert e.value.kind == "network" and len(http.seen) == 4  # first try plus 3 retries, then it gives up


def test_limit_client_side():
    assert [p["id"] for p in jobbank.search(query(limit=2), feed_client(), today=TODAY)] == ["900101", "900102"]


# --- RSS fetch and parsing ---------------------------------------------------------------------------------------------

def test_one_request_per_search_to_the_rss_path_with_the_honest_user_agent():
    http = feed_client()
    jobbank.search(query(), http, today=TODAY)
    request = http.seen[0]
    assert len(http.seen) == 1 and request.url.host == "jobbank.dk" and request.url.path == "/job/rss" and request.url.scheme == "https"
    assert request.headers["user-agent"].startswith("job-search-loop/") and "Mozilla" not in request.headers["user-agent"]


def test_rss_cap_100_and_no_paging():
    items = "".join(f"<item><title>T{i}</title><description>Fuldtidsjob hos A, B</description>"
                    f"<link>https://jobbank.dk/job/{800000 + i}/a/b/</link></item>" for i in range(130))
    http = client(serving(f"<rss><channel>{items}</channel></rss>".encode()))
    found = jobbank.search(query(limit=200), http, today=TODAY)
    assert len(found) == 100 and found[-1]["id"] == "800099"
    assert "page" not in dict(sent(http))


def test_cloudflare_403_means_board_unavailable():
    http = client(serving(fixture("cloudflare_challenge.html"), "text/html; charset=UTF-8", 403))
    with pytest.raises(BoardError) as e:
        jobbank.search(query(), http, today=TODAY)
    assert e.value.kind == "blocked" and len(http.seen) == 1


def test_a_cloudflare_challenge_is_never_retried_or_parsed_as_a_feed():
    for status, ctype in ((403, "text/html"), (403, "application/rss+xml"), (200, "text/html")):
        http = client(serving(fixture("cloudflare_challenge.html"), ctype, status))
        with pytest.raises(BoardError) as e:
            jobbank.search(query(), http, today=TODAY)
        assert e.value.kind in ("blocked", "parse") and len(http.seen) == 1
    with pytest.raises(BoardError) as e:
        jobbank.detail("900101", client(serving(fixture("cloudflare_challenge.html"), "text/html", 403)))
    assert e.value.kind == "blocked"


def test_a_cloudflare_challenge_on_the_command_line_is_one_stderr_line_and_exit_1(monkeypatch, capsys):
    from jobloop.boards import http as boards_http
    real = boards_http.Client
    monkeypatch.setattr(boards_http, "Client", lambda *a, **kw: real(*a, **{**kw, "robots": lambda url: True, "sleep": lambda s: None,
                        "transport": httpx.MockTransport(serving(fixture("cloudflare_challenge.html"), "text/html", 403))}))
    assert contract.run_cli(jobbank, ["search", "--query", "data", "--acknowledge-terms"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err.strip().splitlines()[-1].startswith("jobbank: blocked: ")


def test_robots_denial_stops_the_search_before_the_feed_is_fetched():
    http = client(serving(fixture("feed.xml")), robots=lambda url: False)
    with pytest.raises(BoardError) as e:
        jobbank.search(query(), http, today=TODAY)
    assert e.value.kind == "robots" and http.seen == []


def test_other_rss_failure_is_a_kinded_error_not_an_empty_result():
    with pytest.raises(BoardError) as e:
        jobbank.search(query(), client(serving(b"gone", "text/plain", 410)), today=TODAY)
    assert e.value.kind == "not_found"
    with pytest.raises(BoardError) as e:
        jobbank.search(query(), client(serving(b"<html><body>Find job</body></html>", "text/html")), today=TODAY)
    assert e.value.kind == "parse"


def test_empty_result_is_success_with_empty_list():
    assert jobbank.search(query(), client(serving(fixture("feed_empty.xml"))), today=TODAY) == []


def test_rss_cdata_and_plain_fields():
    by_id = {p["id"]: p for p in jobbank.search(query(limit=50), feed_client(), today=TODAY)}
    assert by_id["900101"]["title"] == "Dataanalytiker til Nordlys" and by_id["900102"]["title"] == "Energiingeniør"
    assert by_id["900101"]["company"] == "Nordlys Analyse ApS" and by_id["900102"]["company"] == "Fjordvind Energi A/S"


def test_unescaped_ampersands_in_a_feed_are_repaired_without_losing_items():
    by_id = {p["id"]: p for p in jobbank.search(query(limit=50), feed_client(), today=TODAY)}
    assert by_id["900107"]["title"] == "Strategi & udvikling" and by_id["900107"]["company"] == "Bølgeslag & Partnere ApS"
    assert len(by_id) == 6


def test_link_text_node_extracted():
    by_id = {p["id"]: p for p in jobbank.search(query(limit=50), feed_client(), today=TODAY)}
    assert by_id["900102"]["url"] == "https://jobbank.dk/job/900102/fjordvind-energi-as/energiingenioer/"
    assert "atom-should-be-ignored" not in json.dumps(list(by_id.values()))


def test_id_from_link():
    assert jobbank.job_id_from_url("https://jobbank.dk/job/12345/acme/data-scientist/") == "12345"
    assert jobbank.job_id_from_url("https://jobbank.dk/job/12345") == "12345"
    for bad in ("https://jobbank.dk/job/oversigt/x/", "https://jobbank.dk/job/", "https://evil.example/job/12345/", "", None,
                "ftp://jobbank.dk/job/1/", "https://jobbank.dk.evil.org/job/1/", "https://jobbank.dk/other/job/1/"):
        assert jobbank.job_id_from_url(bad) is None


def test_an_item_without_a_numeric_id_or_on_another_host_is_dropped_never_emitted_with_an_empty_id():
    ids = {p["id"] for p in jobbank.search(query(limit=50), feed_client(), today=TODAY)}
    assert "" not in ids and ids == {"900101", "900102", "900103", "900104", "900106", "900107"}
    foreign = b"<rss><channel><item><title>x</title><link>https://evil.example/job/123/</link></item></channel></rss>"
    assert jobbank.search(query(), client(serving(foreign)), today=TODAY) == []


def test_description_split_into_parts():
    p = jobbank.parse_description("Fuldtidsjob, Graduate/trainee hos Nordlys Analyse ApS, København K (Ansøgningsfrist: 15.11.2026)")
    assert p == {"types": ["Fuldtidsjob", "Graduate/trainee"], "company": "Nordlys Analyse ApS", "location": "København K",
                 "deadline": "2026-11-15"}
    assert jobbank.parse_description("Vikariat hos Havnebyen Kommune")["location"] is None
    assert jobbank.parse_description("Fuldtidsjob hos A, B, C")["location"] == "B, C"  # the first comma splits; accepted
    assert jobbank.parse_description("Fuldtidsjob hos A, B (Ansogningsfrist: 01.02.2027)")["deadline"] == "2027-02-01"


def test_deadline_dmy_to_iso():
    assert jobbank.parse_deadline("05.01.2027") == "2027-01-05" and jobbank.parse_deadline("5.1.2027") == "2027-01-05"


def test_rolling_deadline_null():
    assert jobbank.parse_deadline("løbende") is None and jobbank.parse_deadline("Lobende") is None
    by_id = {p["id"]: p for p in jobbank.search(query(limit=50), feed_client(), today=TODAY)}
    assert by_id["900102"]["deadline"] is None


def test_unknown_deadline_passthrough():
    assert jobbank.parse_deadline("snarest muligt") == "snarest muligt"
    assert jobbank.parse_deadline("31.02.2026") == "31.02.2026"  # not a real day: shown as written, never as a date
    by_id = {p["id"]: p for p in jobbank.search(query(limit=50), feed_client(), today=TODAY)}
    assert by_id["900103"]["deadline"] == "snarest muligt"
    assert contract.parse_date("snarest muligt") is None and jobbank.is_expired("snarest muligt", TODAY) is None


def test_unstructured_description_fallback():
    for text in ("Ukendt format", "", "Full-time at Acme, London (Deadline: 1 Dec)", "hos", "(Ansøgningsfrist: )"):
        parts = jobbank.parse_description(text)
        assert parts["types"] == [] and parts["location"] is None and parts["deadline"] is None
        assert parts["company"] == (" ".join(text.split()) or None)
    by_id = {p["id"]: p for p in jobbank.search(query(limit=50), feed_client(), today=TODAY)}
    assert by_id["900106"]["company"] == "Ukendt format" and by_id["900106"]["deadline"] is None


def test_pubdate_to_the_danish_calendar_day():
    # 00:30 in Denmark on the 14th is still the 13th in UTC: the Danish day is kept, and the board's own dates are Danish.
    assert jobbank.pub_date("Wed, 14 Oct 2026 00:30:00 +0200") == "2026-10-14"
    assert jobbank.pub_date("Wed, 14 Oct 2026 22:30:00 GMT") == "2026-10-15"
    assert jobbank.pub_date("Fri, 14 Aug 2026 09:30:00 +0200") == "2026-08-14"
    assert jobbank.pub_date("Tue, 13 Oct 2026 23:30:00 -0500") == "2026-10-14"


def test_bad_pubdate_degrades_to_null():
    for bad in (None, "", "date unavailable", "I går", "Wed, 14 Oct 2026 00:30:00 +0200x", "Wed, 14 Oct 2026 00:30:00", "Wed, 99 Oct 2026 00:30:00 +0200"):
        assert jobbank.pub_date(bad) is None
    by_id = {p["id"]: p for p in jobbank.search(query(limit=50), feed_client(), today=TODAY)}
    assert by_id["900104"]["date"] is None and by_id["900104"]["title"] == "Forskningsassistent"
    assert len(by_id) == 6  # the item with the odd pubDate did not fail the search


def test_danish_time_switches_on_the_last_sundays_of_march_and_october_at_01_utc():
    def hour(y, m, d, h, mi=0):
        return jobbank.copenhagen(datetime(y, m, d, h, mi, tzinfo=timezone.utc)).hour
    assert hour(2026, 3, 29, 0, 59) == 1 and hour(2026, 3, 29, 1) == 3  # +1 then +2
    assert hour(2026, 10, 25, 0, 59) == 2 and hour(2026, 10, 25, 1) == 2  # +2 then +1
    assert hour(2026, 1, 15, 12) == 13 and hour(2026, 7, 15, 12) == 14


def test_native_fields_kept():
    p = {p["id"]: p for p in jobbank.search(query(limit=50), feed_client(), today=TODAY)}["900101"]
    assert p == {"id": "900101", "title": "Dataanalytiker til Nordlys", "company": "Nordlys Analyse ApS", "location": "København K",
                 "date": "2026-10-14", "deadline": "2026-11-15", "url": "https://jobbank.dk/job/900101/nordlys-analyse-aps/dataanalytiker-til-nordlys/",
                 "description": "Fuldtidsjob, Graduate/trainee hos Nordlys Analyse ApS, København K (Ansøgningsfrist: 15.11.2026)"}


def test_a_feed_with_a_doctype_or_entities_is_refused():
    bomb = b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    with pytest.raises(BoardError) as e:
        jobbank.search(query(), client(serving(bomb)), today=TODAY)
    assert e.value.kind == "parse"


def test_the_contract_cli_prints_the_envelope_for_the_fixture(monkeypatch, capsys):
    from jobloop.boards import http as boards_http
    real = boards_http.Client
    monkeypatch.setattr(boards_http, "Client", lambda *a, **kw: real(*a, **{**kw, "robots": lambda url: True, "sleep": lambda s: None,
                        "transport": httpx.MockTransport(serving(fixture("feed.xml")))}))
    assert contract.run_cli(jobbank, ["search", "--query", "data", "--recency-days", "3650", "--acknowledge-terms"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["meta"]["board"] == "jobbank" and out["meta"]["count"] == 6 and out["results"][0]["id"] == "900101"


def test_jobbank_is_off_by_default_and_asks_for_acknowledgement():
    assert jobbank.DEFAULT_ENABLED is False and jobbank.TERMS and jobbank.HOSTS == ("jobbank.dk",)


# --- detail ------------------------------------------------------------------------------------------------------------

def test_normalize_job_id_forms_and_rejections():
    for ref in ("12345", " 12345 ", "https://jobbank.dk/job/12345", "https://jobbank.dk/job/12345/", "https://jobbank.dk/job/12345/acme/title/",
                "https://jobbank.dk/job/12345?x=1", "https://jobbank.dk/job/12345#top", "https://www.jobbank.dk/job/12345/"):
        assert jobbank.normalize_id(ref) == "12345"
    for ref in ("", "abc", "12a", "https://example.com/other/12345", "https://example.com/job/12345/", "https://jobbank.dk/job/abc/",
                "https://jobbank.dk.evil.org/job/12345/", "https://jobbank.dk/other/job/12345/", "12345/6", "-1"):
        with pytest.raises(BoardError) as e:
            jobbank.normalize_id(ref)
        assert e.value.kind == "invalid"


def test_detail_fetches_the_short_url_and_follows_the_slug_redirect():
    def handler(request):
        if request.url.path == "/job/900101/":
            return httpx.Response(301, headers={"location": "/job/900101/nordlys-analyse-aps/dataanalytiker-til-nordlys/"})
        return httpx.Response(200, content=fixture("detail_single.html"), headers={"content-type": "text/html"})
    http = client(handler)
    p = jobbank.detail("https://jobbank.dk/job/900101/anything/", http)
    assert [str(r.url) for r in http.seen][0] == "https://jobbank.dk/job/900101/" and p["title"] == "Dataanalytiker til Nordlys"


def test_detail_404_and_error_codes():
    with pytest.raises(BoardError) as e:
        jobbank.detail("900999", client(serving(b"nope", "text/html", 404)))
    assert e.value.kind == "not_found"
    with pytest.raises(BoardError) as e:
        jobbank.detail("900999", client(serving(b"nope", "text/html", 400)))
    assert e.value.kind == "invalid"


def test_robots_denial_stops_detail_before_any_request():
    http = client(serving(fixture("detail_single.html"), "text/html"), robots=lambda url: False)
    with pytest.raises(BoardError) as e:
        jobbank.detail("900101", http)
    assert e.value.kind == "robots" and http.seen == []


def test_jsonld_graph_array_and_malformed_script():
    graph = jobbank.parse_jsonld(fixture("detail_graph_malformed_first.html").decode())
    assert graph["title"] == "Energiingeniør"  # after a malformed script, inside @graph
    array = jobbank.parse_jsonld(fixture("detail_foreign_no_deadline.html").decode())
    assert array["identifier"]["value"] == "900301"
    plain = jobbank.parse_jsonld(fixture("detail_single.html").decode())
    assert plain["@type"] == "JobPosting"
    typed = '<script type="APPLICATION/LD+JSON">{"@type": ["Thing", "JobPosting"], "title": "T"}</script>'
    assert jobbank.parse_jsonld(typed)["title"] == "T"


def test_detail_field_mapping():
    p = jobbank.parse_detail(fixture("detail_single.html").decode(), "900101")
    assert p == {"id": "900101", "title": "Dataanalytiker til Nordlys", "company": "Nordlys Analyse ApS", "location": "København K, DK",
                 "date": "2026-10-14", "deadline": "2026-11-15",
                 "url": "https://jobbank.dk/job/900101/nordlys-analyse-aps/dataanalytiker-til-nordlys/",
                 "description": "<p>Du bliver en del af et lille team hos Nordlys.</p><ul><li>Python</li><li>SQL</li></ul>"}


def test_detail_falls_back_to_the_requested_id_and_a_jobbank_url_and_handles_foreign_or_missing_location():
    p = jobbank.parse_detail(fixture("detail_graph_malformed_first.html").decode(), "900201")
    assert p["id"] == "900201" and p["url"] == "https://jobbank.dk/job/900201/" and p["location"] == "Aarhus C, DK"  # first of two places
    foreign = jobbank.parse_detail(fixture("detail_foreign_no_deadline.html").decode(), "900301")
    assert foreign["location"] == "Malmö, SE" and foreign["deadline"] is None and foreign["description"] is None
    nowhere = jobbank.parse_detail('<script type="application/ld+json">{"@type":"JobPosting","title":"T"}</script>', "5")
    assert nowhere["location"] is None and nowhere["company"] is None
    hostile = '<script type="application/ld+json">{"@type":"JobPosting","title":"T","url":"https://evil.example/job/5/"}</script>'
    assert jobbank.parse_detail(hostile, "5")["url"] == "https://jobbank.dk/job/5/"


def test_valid_through_to_deadline_and_sentinel():
    def deadline(value):
        tail = f',"validThrough":{json.dumps(value)}' if value is not None else ""
        return jobbank.parse_detail(f'<script type="application/ld+json">{{"@type":"JobPosting","title":"T"{tail}}}</script>', "5")["deadline"]
    assert deadline("2026-12-01") == "2026-12-01" and deadline("2026-12-01T12:00:00") == "2026-12-01"
    assert deadline("0001-01-01") is None and deadline("0001-01-01T00:00:00") is None and deadline("") is None and deadline(None) is None
    assert deadline("whenever") is None
    # an instant is read in Danish time: 23:30 UTC on 30 Oct (winter time) is already 31 Oct in Denmark
    assert deadline("2026-10-30T23:30:00Z") == "2026-10-31" and deadline("2026-07-15T21:30:00Z") == "2026-07-15"
    assert deadline("2026-07-15T22:30:00Z") == "2026-07-16"


def test_missing_jobposting_is_parse_error():
    for page in (fixture("detail_no_jsonld.html").decode(), '<script type="application/ld+json">{"@type": "WebSite"}</script>',
                 '<script type="application/ld+json">not json</script>', ""):
        with pytest.raises(BoardError) as e:
            jobbank.parse_detail(page, "1")
        assert e.value.kind == "parse"
    with pytest.raises(BoardError) as e:
        jobbank.detail("900999", client(serving(fixture("detail_no_jsonld.html"), "text/html")))
    assert e.value.kind == "parse"


def test_expired_when_deadline_in_past():
    assert jobbank.is_expired("2026-10-18", TODAY) is True
    assert jobbank.is_expired("2026-10-19", TODAY) is False and jobbank.is_expired("2026-11-01", TODAY) is False
    assert jobbank.is_expired(None, TODAY) is None and jobbank.is_expired("snarest muligt", TODAY) is None
    assert jobbank.is_expired("2026-02-30", TODAY) is None


def test_the_deadline_day_in_denmark_decides_not_utc_today():
    late_utc = jobbank.copenhagen(datetime(2026, 10, 18, 23, 30, tzinfo=timezone.utc)).date()
    assert late_utc == date(2026, 10, 19) and jobbank.is_expired("2026-10-18", late_utc) is True

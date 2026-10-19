"""boards/jobnet.py: the Jobnet adapter, offline against real captures (tests/fixtures/boards/jobnet), plus one live smoke."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from jobloop.boards import contract, jobnet
from jobloop.boards.http import Client

FIX = Path(__file__).parent / "fixtures" / "boards" / "jobnet"
INTERNAL_ID = "9cfc37d8-7e59-4632-a230-5fdbe95bbc94"
EXTERNAL_ID = "7df94bda-7473-47f3-93c0-638c81ad394d"
NO_PLACE_ID = "0ae68c12-fce8-41c9-a599-f28574a0c22a"


def fixture(name: str):
    return json.loads((FIX / name).read_text())


def page_ads() -> list[dict]:
    return copy.deepcopy(fixture("search_page.json")["jobAds"])


def reply(ads: list[dict], total: int | None = None) -> dict:
    return {"jobAds": ads, "searchFacets": {}, "totalJobAdCount": len(ads) if total is None else total, "searchString": ""}


def api(handler) -> Client:
    """A Client on a mock transport that records every request in `.seen`."""
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    c = Client("jobnet", jobnet.HOSTS, transport=httpx.MockTransport(wrapped), sleep=lambda s: None)
    c.seen = seen
    return c


def serving(**routes):
    """A handler answering by path suffix; the value is a dict (200 JSON) or a ready httpx.Response."""
    def handler(request):
        for suffix, answer in routes.items():
            if request.url.path.endswith(suffix.replace("_", "/")):
                return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)
        return httpx.Response(500)
    return handler


def query(**kw) -> contract.Query:
    return contract.Query(**({"text": "sygeplejerske", "recency_days": None} | kw))


def params_of(request) -> dict:
    return dict(request.url.params)


def test_every_jobnet_request_has_x_csrf_header():
    c = api(lambda r: httpx.Response(200, json=[] if "Typeahead" in r.url.path or "Occupation" in r.url.path else reply([])))
    jobnet.search(query(), c)
    jobnet.occupations("nurse", c)
    jobnet.suggestions("sygepl", c)
    c2 = api(lambda r: httpx.Response(404, json=fixture("detail_404.json")) if "JobAdDetails" in r.url.path else httpx.Response(200, json=reply([])))
    with pytest.raises(contract.BoardError):
        jobnet.detail(INTERNAL_ID, c2)
    assert len(c.seen) == 3 and len(c2.seen) == 2
    assert all(r.headers["x-csrf"] == "1" for r in c.seen + c2.seen)


def test_a_401_is_blocked_and_never_retried_or_worked_around():
    c = api(lambda r: httpx.Response(401))
    with pytest.raises(contract.BoardError) as e:
        jobnet.search(query(), c)
    assert e.value.kind == "blocked" and len(c.seen) == 1


def test_json_calls_never_touch_robots_txt_and_stay_on_the_bff_api():
    c = api(lambda r: httpx.Response(307, headers={"location": "/auth/duendebff/login?returnUrl=%2Frobots.txt"})
            if r.url.path == "/robots.txt" else httpx.Response(200, json=reply(page_ads())))
    jobnet.search(query(), c)
    assert [r.url.path for r in c.seen] == ["/bff/FindJob/Search"]


def test_a_login_redirect_is_never_followed_off_the_declared_host():
    c = api(lambda r: httpx.Response(302, headers={"location": "https://login.example.org/auth"}))
    with pytest.raises(contract.BoardError) as e:
        jobnet.search(query(), c)
    assert e.value.kind == "invalid" and len(c.seen) == 1


def test_jobnet_is_off_by_default_and_declares_only_its_own_host():
    assert jobnet.DEFAULT_ENABLED is False and jobnet.HOSTS == ("jobnet.dk",) and jobnet.NAME == "jobnet"


def test_params_always_include_paging_and_order():
    assert jobnet.build_params() == {"resultsPerPage": 20, "pageNumber": 1, "orderType": "PublicationDate"}


def test_all_filters_map_to_api_param_names():
    p = jobnet.build_params(" python ", region="Nordjylland", work_hours="PartTime", duration="Temporary",
                            announcement="Ordinaert", occupation_areas="10000", occupation_groups="10060",
                            order="BestMatch", page=3, per_page=5)
    assert p == {"resultsPerPage": 5, "pageNumber": 3, "orderType": "BestMatch", "searchString": "python",
                 "regions": "Nordjylland", "workHoursType": "PartTime", "employmentDurationType": "Temporary",
                 "jobAnnouncementType": "Ordinaert", "occupationAreas": "10000", "occupationGroups": "10060"}


def test_radius_omitted_without_postal_code():
    assert "kmRadius" not in jobnet.build_params("x", km_radius=10)
    p = jobnet.build_params("x", postal_code="9000")
    assert p["postalCode"] == "9000" and p["kmRadius"] == 50


@pytest.mark.parametrize("kw", [{"region": "Jylland"}, {"work_hours": "Half"}, {"duration": "Forever"}, {"announcement": "Other"},
                                {"order": "Random"}, {"postal_code": "90"}, {"postal_code": "abcd"}, {"page": 0}, {"per_page": 0}])
def test_enum_filters_reject_unknown_values(kw):
    with pytest.raises(contract.BoardError) as e:
        jobnet.build_params("x", **kw)
    assert e.value.kind == "invalid"


def test_region_and_postal_code_together_rejected():
    with pytest.raises(contract.BoardError) as e:
        jobnet.build_params("x", region="Midtjylland", postal_code="8000")
    assert e.value.kind == "invalid" and "combined" in str(e.value)


@pytest.mark.parametrize("text", ["", "   "])
def test_best_match_without_search_string_rejected(text):
    with pytest.raises(contract.BoardError) as e:
        jobnet.build_params(text, order="BestMatch")
    assert e.value.kind == "invalid"


def test_search_sends_paging_order_and_the_query_text_by_default():
    c = api(lambda r: httpx.Response(200, json=reply([])))
    assert jobnet.search(query(text="python"), c) == []
    assert params_of(c.seen[0]) == {"resultsPerPage": "20", "pageNumber": "1", "orderType": "PublicationDate", "searchString": "python"}


def test_contract_aliases_and_dates_come_from_the_real_search_page():
    c = api(lambda r: httpx.Response(200, json=fixture("search_page.json")))
    by_id = {r["id"]: r for r in jobnet.search(query(), c)}
    nurse = by_id[INTERNAL_ID]
    assert nurse["title"].startswith("Sygeplejerske til Friplejehjemmet")
    assert nurse["company"] == "Friplejehjemmet Hesselvang" and nurse["location"] == "Farsø"
    assert nurse["date"] == "2026-10-19" and nurse["deadline"] == "2026-10-27"
    assert by_id["9cb0cb6c-3638-47fc-9832-a486e21b9fa9"]["location"] == "Frederikshavn"
    assert by_id[NO_PLACE_ID]["location"] is None
    assert set(nurse) == set(contract.FIELDS)


def test_every_result_passes_the_contract_and_keeps_description_as_text():
    c = api(lambda r: httpx.Response(200, json=fixture("search_page.json")))
    found = [contract.result(r) for r in jobnet.search(query(), c)]
    assert all(found) and len(found) == 5
    text = next(r for r in found if r["id"] == INTERNAL_ID)["description"]
    assert "<" not in text and ">" not in text and "\n" in text


def test_html_body_becomes_text_keeping_paragraphs_and_decoding_entities():
    text = jobnet.to_text("<p>Fish &amp; chips</p><p>Line<br>two&nbsp;here</p><ul><li>one</li><li>two</li></ul>")
    assert text.splitlines() == ["Fish & chips", "Line", "two here", "", "- one", "- two"]
    assert jobnet.to_text("<p> </p>") is None and jobnet.to_text(None) is None


def test_null_publication_date_degrades_per_item():
    ads = page_ads()
    ads[0]["publicationDate"] = None
    found = jobnet.search(query(), api(lambda r: httpx.Response(200, json=reply(ads))))
    assert found[0]["date"] is None and found[0]["title"] and found[1]["date"] == "2026-10-15"


@pytest.mark.parametrize("deadline,status", [("1900-01-01T00:00:00+01:00", "NotDisclosed"), ("1900-01-01T00:00:00+01:00", "ExpirationDate"),
                                             (None, "ExpirationDate"), ("2026-12-31T00:00:00+01:00", "NotDisclosed")])
def test_1900_sentinel_deadline_maps_to_null(deadline, status):
    ads = page_ads()
    ads[0].update(applicationDeadline=deadline, applicationDeadlineStatus=status)
    assert jobnet.search(query(), api(lambda r: httpx.Response(200, json=reply(ads))))[0]["deadline"] is None


@pytest.mark.parametrize("value", ["javascript:alert(1)", "file:///etc/passwd", "/relative/path", "", "   ", None, "ftp://x.dk/a", "https:///nohost", "not a url"])
def test_external_url_only_http_https_else_find_job(value):
    ads = page_ads()
    ext = next(a for a in ads if a["jobAdId"] == EXTERNAL_ID)
    ext["jobAdUrl"] = value
    found = {r["id"]: r for r in jobnet.search(query(), api(lambda r: httpx.Response(200, json=reply(ads))))}
    assert found[EXTERNAL_ID]["url"] == f"https://jobnet.dk/find-job/{EXTERNAL_ID}"


def test_internal_and_external_ad_urls():
    ads = page_ads()
    ads[0]["jobAdUrl"] = "https://evil.example.org/phish"
    found = {r["id"]: r for r in jobnet.search(query(), api(lambda r: httpx.Response(200, json=reply(ads))))}
    assert found[INTERNAL_ID]["url"] == f"https://jobnet.dk/find-job/{INTERNAL_ID}"
    assert found[EXTERNAL_ID]["url"].startswith("https://job.maxizoo.dk/")
    assert jobnet.web_url("  https://a.dk/x  ") == "https://a.dk/x"


def test_url_never_uses_legacy_job_route():
    ads = page_ads()
    for ad in ads:
        ad["jobAdUrl"] = "javascript:void(0)"
    found = jobnet.search(query(), api(lambda r: httpx.Response(200, json=reply(ads))))
    assert all(r["url"].startswith("https://jobnet.dk/find-job/") and "/job/" not in r["url"] for r in found)


def test_a_posting_without_an_id_or_title_is_dropped_by_the_contract():
    ads = page_ads()
    del ads[0]["jobAdId"]
    ads[1]["title"] = None
    kept = [r for r in (contract.result(x) for x in jobnet.search(query(), api(lambda r: httpx.Response(200, json=reply(ads))))) if r]
    assert len(kept) == 3


def test_a_reply_that_is_not_a_search_result_is_a_parse_error():
    for body in ({"unexpected": 1}, [], {"jobAds": "x"}):
        with pytest.raises(contract.BoardError) as e:
            jobnet.search(query(), api(lambda r, b=body: httpx.Response(200, json=b)))
        assert e.value.kind == "parse"


def test_location_region_name_goes_to_the_api_as_regions():
    c = api(lambda r: httpx.Response(200, json=reply([])))
    jobnet.search(query(location="Nordjylland"), c)
    p = params_of(c.seen[0])
    assert p["regions"] == "Nordjylland" and "postalCode" not in p


def test_location_postal_code_goes_to_the_api_with_radius_and_never_with_a_region():
    c = api(lambda r: httpx.Response(200, json=reply([])))
    jobnet.search(query(location="9000"), c)
    p = params_of(c.seen[0])
    assert p["postalCode"] == "9000" and p["kmRadius"] == "50" and "regions" not in p


@pytest.mark.parametrize("location,expected", [("", (None, None, "")), ("Denmark", (None, None, "")), ("Sjælland", ("OevrigeSjaelland", None, "")),
                                               ("Hovedstaden", ("HovedstadenOgBornholm", None, "")), ("Aalborg", (None, None, "aalborg"))])
def test_location_interpretation(location, expected):
    assert jobnet.interpret_location(location) == expected


def test_other_location_text_is_matched_client_side_and_sent_nowhere():
    c = api(lambda r: httpx.Response(200, json=fixture("search_page.json")))
    found = jobnet.search(query(location="Farsø"), c)
    assert [r["id"] for r in found] == [INTERNAL_ID]
    p = params_of(c.seen[0])
    assert "regions" not in p and "postalCode" not in p and "Farsø" not in str(p)


def test_recency_is_filtered_client_side_and_paging_stops_when_dates_fall_out_of_the_window():
    today = date.today()
    ads = []
    for age in (0, 3, 40, 41):
        ad = copy.deepcopy(page_ads()[0])
        ad.update(jobAdId=f"id-{age}", publicationDate=f"{today - timedelta(days=age)}T00:00:00+02:00")
        ads.append(ad)
    full = ads + [copy.deepcopy(ads[0]) for _ in range(16)]
    c = api(lambda r: httpx.Response(200, json=reply(full, total=500)))
    found = jobnet.search(query(recency_days=14, limit=50), c)
    assert {r["id"] for r in found if r["id"] != "id-0"} == {"id-3"}
    assert all(contract.parse_date(r["date"]) >= today - timedelta(days=14) for r in found)
    assert len(c.seen) == 1


def test_paging_walks_pages_until_the_limit_and_never_past_the_cap():
    full = [copy.deepcopy(page_ads()[0]) for _ in range(20)]
    c = api(lambda r: httpx.Response(200, json=reply(full, total=5000)))
    jobnet.search(query(limit=200), c)
    assert [params_of(r)["pageNumber"] for r in c.seen] == [str(n) for n in range(1, jobnet.MAX_PAGES + 1)]
    c = api(lambda r: httpx.Response(200, json=reply(full, total=5000)))
    jobnet.search(query(limit=20), c)
    assert len(c.seen) == 1


def test_a_short_page_ends_the_paging():
    c = api(lambda r: httpx.Response(200, json=reply(page_ads(), total=5000)))
    jobnet.search(query(limit=200), c)
    assert len(c.seen) == 1


def test_run_cli_prints_the_contract_envelope_for_a_search(capsys):
    seen = []
    real = Client

    class Mocked(real):
        def __init__(self, board, hosts, **kw):
            super().__init__(board, hosts, transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200, json=fixture("search_page.json"))), **kw)

    import jobloop.boards.http as http_module
    original = http_module.Client
    http_module.Client = Mocked
    try:
        assert contract.run_cli(jobnet, ["search", "--query", "sygeplejerske", "--recency-days", "3650", "--limit", "2"]) == 0
    finally:
        http_module.Client = original
    out = json.loads(capsys.readouterr().out)
    assert out["meta"]["board"] == "jobnet" and out["meta"]["count"] == 2 and len(out["results"]) == 2
    assert seen[0].headers["x-csrf"] == "1"


def test_run_as_a_module_refuses_an_empty_query_before_any_request():
    p = subprocess.run([sys.executable, "-m", "jobloop.boards.jobnet", "search", "--query=-x"], capture_output=True, text=True,
                       cwd=Path(__file__).parent.parent)
    assert p.returncode == 1 and p.stdout == "" and p.stderr.startswith("jobnet: invalid:")


@pytest.mark.parametrize("ref,expected", [
    (INTERNAL_ID, INTERNAL_ID), (f"  {INTERNAL_ID}\n", INTERNAL_ID), ("abc_123-X", "abc_123-X"),
    (f"https://jobnet.dk/find-job/{INTERNAL_ID}", INTERNAL_ID), (f"https://jobnet.dk/find-job/{INTERNAL_ID}/", INTERNAL_ID),
    (f"https://jobnet.dk/find-job/{INTERNAL_ID}?x=1", INTERNAL_ID), (f"https://jobnet.dk/find-job/{INTERNAL_ID}#top", INTERNAL_ID),
    (f"https://jobnet.dk/find-job/detaljer/{INTERNAL_ID}", INTERNAL_ID), (f"https://jobnet.dk/bff/FindJob/JobAdDetails/{INTERNAL_ID}", INTERNAL_ID),
    (f"https://job.jobnet.dk/CV/FindWork/Details/{INTERNAL_ID}", INTERNAL_ID)])
def test_normalize_job_id_accepts_known_shapes(ref, expected):
    assert jobnet.normalize_id(ref) == expected


@pytest.mark.parametrize("ref", ["", "   ", "https://example.com/other/123", f"https://example.com/find-job/{INTERNAL_ID}",
                                 f"https://jobnet.dk.evil.org/find-job/{INTERNAL_ID}", f"https://evil.org/?u=jobnet.dk/find-job/{INTERNAL_ID}",
                                 "https://jobnet.dk/", "https://jobnet.dk/find-job/", f"ftp://jobnet.dk/find-job/{INTERNAL_ID}",
                                 f"https://user@evil.org@jobnet.dk/find-job/{INTERNAL_ID}", "a b", "../etc"])
def test_normalize_job_id_rejects_others(ref):
    with pytest.raises(contract.BoardError) as e:
        jobnet.normalize_id(ref)
    assert e.value.kind == "invalid"


def test_detail_missing_and_bad_id_errors_make_no_request():
    c = api(lambda r: httpx.Response(200, json={}))
    for ref in ("", "https://example.com/x/1"):
        with pytest.raises(contract.BoardError) as e:
            jobnet.detail(ref, c)
        assert e.value.kind == "invalid"
    assert c.seen == []


def test_detail_request_sends_incrementviews_false():
    c = api(serving(**{INTERNAL_ID: fixture("detail_internal.json")}))
    jobnet.detail(INTERNAL_ID, c)
    assert c.seen[0].url.path == f"/bff/FindJob/JobAdDetails/{INTERNAL_ID}" and params_of(c.seen[0]) == {"incrementViews": "false"}


def test_detail_internal_ad_maps_to_the_contract_without_contact_persons():
    d = jobnet.detail(f"https://jobnet.dk/find-job/{INTERNAL_ID}", api(serving(**{INTERNAL_ID: fixture("detail_internal.json")})))
    assert d["id"] == INTERNAL_ID and d["title"].startswith("Sygeplejerske til Friplejehjemmet")
    assert d["company"] == "Friplejehjemmet Hesselvang" and d["location"] == "Farsø"
    assert d["date"] == "2026-10-19" and d["deadline"] == "2026-10-27" and d["url"] == f"https://jobnet.dk/find-job/{INTERNAL_ID}"
    assert "Du arbejder kun i dagvagt" in d["description"] and "<p>" not in d["description"]
    assert contract.result(d) == d
    assert "Fornavn" not in json.dumps(d) and "kontakt@example.invalid" not in json.dumps(d)


def test_detail_1900_sentinel_null_real_and_null_kept():
    cases = [("1900-01-01T00:00:00+01:00", None), ("2026-11-05T00:00:00+01:00", "2026-11-05"), (None, None)]
    for given, expected in cases:
        raw = fixture("detail_internal.json")
        raw["application"]["deadlineDate"] = given
        assert jobnet.detail(INTERNAL_ID, api(serving(**{INTERNAL_ID: raw})))["deadline"] == expected


def test_detail_asap_deadline_is_null_even_with_a_date():
    raw = fixture("detail_internal.json")
    raw["application"]["isApplicationDeadlineASAP"] = True
    assert jobnet.from_detail(raw, INTERNAL_ID)["deadline"] is None


def test_detail_of_an_anonymous_employer_has_no_company():
    raw = fixture("detail_internal.json")
    raw["isAnonymousEmployer"] = True
    assert jobnet.from_detail(raw, INTERNAL_ID)["company"] is None


def external_routes():
    return api(lambda r: httpx.Response(404, json=fixture("detail_404.json")) if "JobAdDetails" in r.url.path
               else httpx.Response(200, json=fixture("search_by_id.json")))


def test_detail_404_falls_back_to_search_by_id(capsys):
    c = external_routes()
    d = jobnet.detail(EXTERNAL_ID, c)
    assert [r.url.path for r in c.seen] == [f"/bff/FindJob/JobAdDetails/{EXTERNAL_ID}", "/bff/FindJob/Search"]
    assert params_of(c.seen[1]) == {"resultsPerPage": "5", "pageNumber": "1", "orderType": "PublicationDate", "searchString": EXTERNAL_ID}
    assert d["id"] == EXTERNAL_ID and d["title"].startswith("Salgsassistent til Frederikshavn")
    assert d["company"] and d["location"] == "Frederikshavn" and d["deadline"] == "2027-01-19"
    assert d["url"].startswith("https://job.maxizoo.dk/") and d["description"]
    assert contract.result(d) == d


def test_fallback_notes_the_source_on_stderr(capsys):
    jobnet.detail(EXTERNAL_ID, external_routes())
    assert EXTERNAL_ID in capsys.readouterr().err


def test_fallback_picks_only_the_exact_id_and_otherwise_reports_not_found():
    other = fixture("search_by_id.json")
    other["jobAds"][0]["jobAdId"] = "someone-else"
    c = api(lambda r: httpx.Response(404, json=fixture("detail_404.json")) if "JobAdDetails" in r.url.path else httpx.Response(200, json=other))
    with pytest.raises(contract.BoardError) as e:
        jobnet.detail(EXTERNAL_ID, c)
    assert e.value.kind == "not_found"
    empty = api(lambda r: httpx.Response(404, json=fixture("detail_404.json")) if "JobAdDetails" in r.url.path else httpx.Response(200, json=reply([])))
    with pytest.raises(contract.BoardError) as e:
        jobnet.detail(EXTERNAL_ID, empty)
    assert e.value.kind == "not_found"


def test_fallback_applies_the_same_url_and_sentinel_rules_as_search():
    raw = fixture("search_by_id.json")
    raw["jobAds"][0].update(jobAdUrl="javascript:alert(1)", applicationDeadlineStatus="NotDisclosed")
    c = api(lambda r: httpx.Response(404, json=fixture("detail_404.json")) if "JobAdDetails" in r.url.path else httpx.Response(200, json=raw))
    d = jobnet.detail(EXTERNAL_ID, c)
    assert d["url"] == f"https://jobnet.dk/find-job/{EXTERNAL_ID}" and d["deadline"] is None


def test_detail_500_is_never_reported_as_not_found_and_never_falls_back():
    c = api(lambda r: httpx.Response(500))
    with pytest.raises(contract.BoardError) as e:
        jobnet.detail(INTERNAL_ID, c)
    assert e.value.kind == "network" and all("Search" not in r.url.path for r in c.seen)


def test_a_detail_reply_that_is_not_a_posting_is_a_parse_error():
    with pytest.raises(contract.BoardError) as e:
        jobnet.detail(INTERNAL_ID, api(lambda r: httpx.Response(200, json={"hello": 1})))
    assert e.value.kind == "parse"


def test_occupations_requires_term_and_slices_client_side():
    items = [{"preferredLabelDa": f"job {i}"} for i in range(30)]
    c = api(lambda r: httpx.Response(200, json=items))
    assert jobnet.occupations("sygepleje", c, limit=5) == items[:5]
    assert params_of(c.seen[0]) == {"searchString": "sygepleje", "pageSize": "5"}
    for bad in ("", "  "):
        with pytest.raises(contract.BoardError) as e:
            jobnet.occupations(bad, c)
        assert e.value.kind == "invalid"
    assert len(c.seen) == 1


def test_occupation_uri_never_truncated():
    uri = "http://data.star.dk/esco/occupation/56f5d45c-ce48-43ac-9720-34831a1c8302"
    got = jobnet.occupations("x", api(lambda r: httpx.Response(200, json=[{"aliases": [{"conceptUriDa": uri}]}])))
    assert got[0]["aliases"][0]["conceptUriDa"] == uri and len(uri) > 70


def test_suggestions_requires_query_and_limit_slices():
    c = api(lambda r: httpx.Response(200, json=["Sygeplejerske", 'Sygeplejerske "Lægeassistent"', "Sygeplejerske, hjemmepleje"]))
    assert jobnet.suggestions("syge", c, limit=2) == ["Sygeplejerske", 'Sygeplejerske "Lægeassistent"']
    assert params_of(c.seen[0]) == {"query": "syge"}
    with pytest.raises(contract.BoardError):
        jobnet.suggestions(" ", c)
    assert len(c.seen) == 1


def test_empty_suggestions_is_empty_list_not_error():
    assert jobnet.suggestions("python", api(lambda r: httpx.Response(200, json=[]))) == []


@pytest.mark.live
def test_live_search_and_detail_smoke():
    with Client("jobnet", jobnet.HOSTS) as http:
        found = jobnet.search(contract.Query("sygeplejerske", recency_days=30, limit=5), http)
        assert found and all(contract.result(r) for r in found)
        print("\nLIVE search:", json.dumps([{k: r[k] for k in ("id", "title", "company", "location", "date", "deadline", "url")} for r in found], ensure_ascii=False))
        internal = next((r for r in found if r["url"].startswith("https://jobnet.dk/find-job/")), found[0])
        got = jobnet.detail(internal["id"], http)
        assert got["id"] == internal["id"] and got["title"] and got["description"]
        print("LIVE detail:", json.dumps({**got, "description": got["description"][:200]}, ensure_ascii=False))

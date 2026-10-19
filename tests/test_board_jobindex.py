"""boards/jobindex.py: what it asks the board for (only what the captured robots.txt allows), what it makes of the
answers (search page, RSS, three shapes of posting page), and how it fails. Fixtures in tests/fixtures/boards/jobindex are
trimmed real captures of 2026-10-19; detail_native.html and detail_passthrough.html are small hand-written pages in the
two shapes the board's notes describe, because no real page of those shapes was captured."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from jobloop.boards import contract, http, jobindex
from jobloop.boards.contract import BoardError, Query

FIX = Path(__file__).parent / "fixtures" / "boards" / "jobindex"
TODAY = date(2026, 10, 19)


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def rules(text: str) -> list[tuple[bool, str]]:
    """The Allow/Disallow lines of the `User-agent: *` group of a robots.txt."""
    out, active = [], False
    for line in text.splitlines():
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            active = value == "*"
        elif active and field in ("allow", "disallow") and value:
            out.append((field == "allow", value))
    return out


def matches(pattern: str, target: str) -> bool:
    regex = "".join(".*" if c == "*" else "$" if c == "$" else re.escape(c) for c in pattern)
    return re.match(regex, target) is not None


def robots_allows(url: str, robots_txt: str | None = None) -> bool:
    """RFC 9309 in miniature: the longest matching rule wins, Allow wins a tie, no match means allowed."""
    parts = urlparse(url)
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    hits = [(len(p), allow) for allow, p in rules(robots_txt or read("robots.txt")) if matches(p, target)]
    return max(hits, key=lambda h: (h[0], h[1]))[1] if hits else True


def gate(url: str) -> bool:
    return robots_allows(url)


class Board:
    """A mock jobindex.dk: serves fixtures by path and records every request."""

    def __init__(self, **pages):
        self.seen: list[httpx.Request] = []
        self.pages = {"/jobsoegning": read("search_python.html"), "/jobsoegning.rss": (FIX / "search_python.rss").read_bytes(),
                      "/jobannonce/h1699736": read("detail_live_beumer.html")}
        self.pages.update(pages)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        body = self.pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="gone")
        if isinstance(body, httpx.Response):
            return body
        ctype = "application/rss+xml" if request.url.path.endswith(".rss") else "text/html; charset=utf-8"
        return httpx.Response(200, content=body if isinstance(body, bytes) else body.encode(), headers={"content-type": ctype})

    def client(self, robots=gate, **kw) -> http.Client:
        return http.Client(jobindex.NAME, jobindex.HOSTS, transport=httpx.MockTransport(self), sleep=lambda s: None, robots=robots, **kw)

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.seen]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(jobindex, "today", lambda: TODAY)


def search(text="python", board=None, **kw):
    board = board or Board()
    q = Query(text, **{"recency_days": None, **kw})
    with board.client() as c:
        return jobindex.search(q, c), board


def stash_page(results, **extra) -> str:
    store = {"searchResponse": {"results": results, **extra}}
    return f"<html><script>var Stash = {json.dumps({'a': {'b': {'storeData': store}}})};</script></html>"


# ---- what is asked of the board ----

def test_jobindex_is_off_by_default_and_only_talks_to_its_own_host():
    assert jobindex.DEFAULT_ENABLED is False
    assert jobindex.HOSTS == ("www.jobindex.dk",)
    assert jobindex.TERMS == "" and jobindex.ACK_WAIVES_ROBOTS is False


def test_search_asks_only_for_q_on_the_allowed_search_page():
    _, board = search("python")
    assert board.urls() == ["https://www.jobindex.dk/jobsoegning?q=python"]


def test_the_urls_the_original_always_sent_are_disallowed_by_the_captured_robots():
    base = "https://www.jobindex.dk/jobsoegning"
    for url in (f"{base}?q=python&page=1&jobage=9999&sort=score", f"{base}?q=python&page=2", f"{base}?q=python&sort=date",
                f"{base}?q=python&jobage=7", f"{base}?q=senior+python+developer", f"{base}?q=a&b=c&d=e",
                "https://www.jobindex.dk/c?t=h1699736&ctx=w", "https://www.jobindex.dk/api/anything"):
        assert not robots_allows(url), url
    for url in (f"{base}?q=python", f"{base}?q=data+engineer", f"{base}.rss?q=python", "https://www.jobindex.dk/jobannonce/h1699736"):
        assert robots_allows(url), url


@pytest.mark.parametrize("text,location", [("python", ""), ("data engineer", ""), ("senior python developer lead", ""),
                                            ("python", "København"), ("data engineer", "Aarhus"), ("Ørsted ingeniør", "")])
def test_every_url_requested_passes_the_captured_robots_rules(text, location):
    _, board = search(text, location=location, limit=200, recency_days=7)
    assert board.seen and all(robots_allows(u) for u in board.urls()), board.urls()
    for u in board.urls():
        assert not {"page", "sort", "jobage"} & set(httpx.URL(u).params), u
        assert httpx.URL(u).params["q"].count(" ") <= 1, u


def test_detail_url_passes_the_captured_robots_rules_and_never_fetches_the_click_through():
    board = Board()
    with board.client() as c:
        jobindex.detail("h1699736", c)
    assert board.urls() == ["https://www.jobindex.dk/jobannonce/h1699736"]
    assert all(robots_allows(u) for u in board.urls())


def test_a_query_of_three_words_sends_two_and_requires_the_third_in_the_posting_text():
    found, board = search("python engineer robotics")
    assert board.urls() == ["https://www.jobindex.dk/jobsoegning?q=python+engineer"]
    assert [p["id"] for p in found] == ["h1699736"]  # the only fixture posting that says "robotics"


def test_a_location_that_fits_in_q_is_sent_in_q_and_one_that_does_not_is_filtered_client_side():
    _, board = search("python", location="Aarhus")
    assert board.urls() == ["https://www.jobindex.dk/jobsoegning?q=python+Aarhus"]
    found, board = search("data engineer", location="Aarhus")
    assert board.urls() == ["https://www.jobindex.dk/jobsoegning?q=data+engineer"]
    assert [p["location"] for p in found] == ["Aarhus N", None, "Aarhus"]  # no area means unknown, kept; Nordhavn and the rest dropped


def test_recency_sort_and_limit_are_applied_client_side():
    found, _ = search("python", recency_days=7, limit=200)
    assert [p["id"] for p in found] == ["h1699736", "h1708174", "h1708262", "r14052181", "h1707011"]
    assert min(p["date"] for p in found) == "2026-10-12"  # the postings of 10-09 and older are out
    assert [p["id"] for p in search("python", recency_days=7, limit=2)[0]] == ["h1699736", "h1708174"]
    every, _ = search("python", limit=200)
    dates = [p["date"] for p in every]
    assert dates == sorted(dates, reverse=True)
    assert len(every) <= jobindex.PAGE_SIZE


def test_search_falls_back_to_the_rss_feed_when_robots_refuses_the_html_page():
    board = Board()
    refuse_html = lambda url: not urlparse(url).path.endswith("/jobsoegning") and gate(url)  # noqa: E731
    with board.client(robots=refuse_html) as c:
        found = jobindex.search(Query("python", recency_days=None), c)
    assert board.urls() == ["https://www.jobindex.dk/jobsoegning.rss?q=python"]
    first = found[0]
    assert first["id"] == "h1707616" and first["url"] == "https://www.jobindex.dk/jobannonce/h1707616"
    assert first["title"].startswith("PhD Scholarship in Machine Learning")
    assert first["company"] == "DTU, Danmarks Tekniske Universitet"
    assert first["location"] == "Kgs. Lyngby" and first["date"] == "2026-10-19" and first["deadline"] is None


def test_robots_refusing_both_the_page_and_the_feed_is_a_robots_error_not_an_empty_list():
    board = Board()
    with board.client(robots=lambda url: False) as c, pytest.raises(BoardError) as e:
        jobindex.search(Query("python"), c)
    assert e.value.kind == "robots" and board.seen == []


def test_an_empty_query_is_refused_before_any_request():
    board = Board()
    with pytest.raises(BoardError) as e:
        Query("   ")
    assert e.value.kind == "invalid" and board.seen == []


def test_a_query_starting_with_a_dash_is_refused_before_any_request():
    with pytest.raises(BoardError) as e:
        Query("-python")
    assert e.value.kind == "invalid"


# ---- what is made of the search page ----

def test_stash_result_maps_contract_fields():
    found, _ = search("python", limit=200)
    nykredit = next(p for p in found if p["id"] == "h1708174")
    assert nykredit == {"id": "h1708174", "title": "Join Nykredit Markets as a Quantitative Developer (Analyst/Associate)",
                        "company": "Nykredit", "location": "Nordhavn", "date": "2026-10-15", "deadline": "2026-11-02",
                        "url": "https://www.jobindex.dk/jobannonce/h1708174", "description": None}


def test_asap_posting_deadline_is_null_even_with_lastdate():
    found, _ = search("python", limit=200)
    beumer = next(p for p in found if p["id"] == "h1699736")
    assert beumer["deadline"] is None  # the capture has lastdate 2026-11-12 and apply_deadline_asap true


def test_deadline_is_the_danish_calendar_date_of_the_utc_timestamp_not_its_first_ten_characters():
    found, _ = search("python", limit=200)
    by_id = {p["id"]: p["deadline"] for p in found}
    assert by_id["h1708174"] == "2026-11-02"      # 2026-11-02T22:59:59Z is 23:59:59 in Denmark: same day
    assert by_id["r14052181"] == "2026-11-19"     # 2026-11-18T23:00:00Z is midnight in Denmark: the next day
    assert jobindex.danish_date("2026-06-30T22:00:00Z") == "2026-07-01"  # summer time is UTC+2
    assert jobindex.danish_date("2026-12-31T23:30:00Z") == "2027-01-01"


def test_deadline_comes_from_apply_deadline_else_lastdate_and_a_posting_without_either_has_none():
    rows = [{"tid": "h1", "headline": "A", "apply_deadline": "2026-12-01T22:59:59Z", "lastdate": "2026-12-05"},
            {"tid": "h2", "headline": "B", "lastdate": "2026-12-05"}, {"tid": "h3", "headline": "C"}]
    board = Board(**{"/jobsoegning": stash_page(rows)})
    found, _ = search("x", board=board)
    assert {p["id"]: p["deadline"] for p in found} == {"h1": "2026-12-01", "h2": "2026-12-05", "h3": None}


def test_archived_and_past_deadline_postings_are_dropped_but_asap_and_future_are_kept():
    rows = [{"tid": "h1", "headline": "archived", "is_archived": True}, {"tid": "h2", "headline": "past", "apply_deadline": "2026-10-17T22:59:59Z"},
            {"tid": "h3", "headline": "today", "apply_deadline": "2026-10-18T22:30:00Z"}, {"tid": "h4", "headline": "asap", "apply_deadline_asap": True, "lastdate": "2026-10-01"},
            {"tid": "h5", "headline": "gone lastdate", "lastdate": "2026-10-10"}]
    found, _ = search("x", board=Board(**{"/jobsoegning": stash_page(rows)}))
    assert [p["id"] for p in found] == ["h3", "h4"]


def test_company_and_location_fall_back_to_companytext_and_the_geojson_title():
    rows = [{"tid": "h1", "headline": "A", "company": {"name": "Named"}, "companytext": "Text"},
            {"tid": "h2", "headline": "B", "companytext": "Text", "geojson": {"features": [{"properties": {"title": "Geo Town"}}]}}]
    found, _ = search("x", board=Board(**{"/jobsoegning": stash_page(rows)}))
    assert [(p["company"], p["location"]) for p in found] == [("Named", None), ("Text", "Geo Town")]


def test_url_is_canonical_jobannonce_with_share_url_and_url_fallbacks():
    rows = [{"tid": "h1", "headline": "A", "share_url": "https://www.jobindex.dk/vis-job/h1"},
            {"headline": "B", "share_url": "https://www.jobindex.dk/vis-job/h2"}, {"headline": "C", "url": "https://www.jobindex.dk/c?t=h3"}]
    found, _ = search("x", board=Board(**{"/jobsoegning": stash_page(rows)}))
    assert [p["url"] for p in found] == ["https://www.jobindex.dk/jobannonce/h1", "https://www.jobindex.dk/vis-job/h2", "https://www.jobindex.dk/c?t=h3"]


def test_search_description_is_null():
    found, _ = search("python", limit=200)
    assert found and all(p["description"] is None for p in found)


def test_page_size_is_20_and_a_larger_limit_does_not_page():
    rows = [{"tid": f"h{i}", "headline": f"T{i}", "firstdate": "2026-10-15"} for i in range(1, 31)]
    board = Board(**{"/jobsoegning": stash_page(rows)})
    found, board = search("x", board=board, limit=200)
    assert len(found) == jobindex.PAGE_SIZE == 20 and len(board.seen) == 1


def test_stash_extraction_handles_braces_inside_strings_and_escapes():
    page = stash_page([{"tid": "h1", "headline": 'a } "quoted" { \\ brace', "company": {"name": "x}"}}])
    found, _ = search("x", board=Board(**{"/jobsoegning": page}))
    assert found[0]["title"] == 'a } "quoted" { \\ brace' and found[0]["company"] == "x}"


def test_a_missing_or_cut_off_stash_is_a_parse_error_naming_layout_or_block_not_zero_results():
    for page in ("<html>consent wall</html>", '<script>var Stash = {"a": {"searchResponse": {"results": ['):
        with pytest.raises(BoardError) as e:
            jobindex.parse_stash(page)
        assert e.value.kind == "parse"
    with pytest.raises(BoardError, match="layout changed or the page is a block page"):
        jobindex.parse_stash("<html></html>")


def test_a_stash_without_search_results_is_a_parse_error():
    with pytest.raises(BoardError) as e:
        jobindex.parse_stash('<script>var Stash = {"common": {"lang": "da"}};</script>')
    assert e.value.kind == "parse"


def test_search_response_is_found_at_any_nesting_depth():
    deep = '<script>var Stash = {"x": [{"y": {"z": {"searchResponse": {"results": [{"tid": "h9"}]}}}}]};</script>'
    assert jobindex.parse_stash(deep)["results"] == [{"tid": "h9"}]


def test_a_search_with_no_hits_is_an_empty_list_not_an_error():
    found, _ = search("zzzz", board=Board(**{"/jobsoegning": stash_page([], hitcount=0)}))
    assert found == []


def test_numeric_entities_decode_including_emoji_and_out_of_range():
    text = jobindex.strip_tags("<b>caf&#233; &#x1F600; &#X41; &#1114112; &oslash;&aelig;&aring;&Oslash;&AElig;&Aring;</b>")
    assert text.startswith("café \U0001F600 A ") and text.endswith("øæåØÆÅ")


# ---- the RSS feed ----

def test_rss_items_read_the_latin1_feed_with_danish_letters():
    items = jobindex.postings_from_rss((FIX / "search_python.rss").read_bytes())
    assert [i["id"] for i in items][:2] == ["h1707616", "h1708450"]
    assert "Senioranalytiker til Tænketanken Mandag Morgen" in items[1]["title"]
    assert items[1]["company"] == "Mandag Morgen ApS"


def test_rss_that_is_not_xml_is_a_parse_error():
    with pytest.raises(BoardError) as e:
        jobindex.postings_from_rss(b"<html>not a feed")
    assert e.value.kind == "parse"


# ---- the board's failure modes ----

def test_a_403_is_blocked_and_not_retried():
    board = Board(**{"/jobsoegning": httpx.Response(403, text="no")})
    with pytest.raises(BoardError) as e:
        search("python", board=board)
    assert e.value.kind == "blocked" and len(board.seen) == 1


def test_a_challenge_page_is_blocked_not_zero_results():
    board = Board(**{"/jobsoegning": httpx.Response(200, text="<title>Just a moment...</title>", headers={"content-type": "text/html"})})
    with pytest.raises(BoardError) as e:
        search("python", board=board)
    assert e.value.kind == "blocked"


def test_a_429_is_retried_with_backoff_then_the_search_succeeds():
    replies = [httpx.Response(429, headers={"retry-after": "3"}), None]
    board = Board()
    page = board.pages["/jobsoegning"]

    def handler(request):
        board.seen.append(request)
        return replies.pop(0) or httpx.Response(200, text=page, headers={"content-type": "text/html"})

    sleeps = []
    with http.Client("jobindex", jobindex.HOSTS, transport=httpx.MockTransport(handler), sleep=sleeps.append, robots=gate) as c:
        assert jobindex.search(Query("python", recency_days=None), c)
    assert sleeps == [3.0] and len(board.seen) == 2


def test_the_real_temporarily_restricted_access_page_is_rate_limited_after_backoff_not_zero_results():
    page = httpx.Response(429, content=(FIX / "rate_limited_429.html").read_bytes(), headers={"content-type": "text/html"})
    board = Board(**{"/jobsoegning": page})
    with pytest.raises(BoardError) as e:
        search("python", board=board)
    assert e.value.kind == "rate_limited" and len(board.seen) == 4


# ---- detail ----

def test_detail_without_an_id_is_invalid_before_any_request():
    board = Board()
    with board.client() as c, pytest.raises(BoardError) as e:
        jobindex.detail("  ", c)
    assert e.value.kind == "invalid" and board.seen == []


def test_url_rebuilt_from_id_for_slug_query_subdomain_variants():
    want = ("h1699736", "https://www.jobindex.dk/jobannonce/h1699736")
    for ref in ("h1699736", "https://www.jobindex.dk/jobannonce/h1699736", "https://www.jobindex.dk/jobannonce/h1699736/some-slug?lang=en",
                "http://jobindex.dk/jobannonce/h1699736/", "https://it.jobindex.dk/jobannonce/h1699736#x", "https://www.jobindex.dk/vis-job/h1699736"):
        assert jobindex.build_url(ref) == want, ref


def test_off_host_lookalike_and_userinfo_urls_rejected():
    for ref in ("https://jobindex.dk.evil.example/jobannonce/h1699736", "https://www.jobindex.dk@evil.example/jobannonce/h1699736",
                "https://evil.example/jobannonce/h1699736", "https://user:pw@www.jobindex.dk/jobannonce/h1699736",
                "https://notjobindex.dk/jobannonce/h1699736"):
        with pytest.raises(BoardError) as e:
            jobindex.build_url(ref)
        assert e.value.kind == "invalid", ref


def test_homepage_url_is_bad_id_not_fake_posting():
    for ref in ("https://www.jobindex.dk/", "https://www.jobindex.dk/jobsoegning?q=python", "https://www.jobindex.dk/jobannonce/"):
        with pytest.raises(BoardError) as e:
            jobindex.build_url(ref)
        assert e.value.kind == "invalid"


def test_bare_id_accepted_garbage_and_ftp_rejected():
    assert jobindex.build_url("r14052181")[0] == "r14052181"
    for ref in ("python", "h1/../x", "h1699736 extra", "ftp://www.jobindex.dk/jobannonce/h1699736", "javascript:alert(1)", "1699736"):
        with pytest.raises(BoardError):
            jobindex.build_url(ref)


def test_current_live_detail_fixture_gives_company_location_and_description():
    board = Board()
    with board.client() as c:
        d = jobindex.detail("h1699736", c)
    assert d["id"] == "h1699736" and d["url"] == "https://www.jobindex.dk/jobannonce/h1699736"
    assert d["title"] == "Software Engineer, Vision and Robotics"
    assert d["company"] == "BEUMER Group" and d["location"] == "Aarhus N"
    assert d["deadline"] is None and d["date"] is None  # this ASAP page states neither
    assert d["description"].startswith("Do you thrive in designing") and "Docker and Kubernetes" in d["description"]
    assert "- Improving new software products" in d["description"]  # list items stay on their own lines


def test_meta_attribute_order_does_not_matter():
    a = '<meta content="Title A" property="og:title">'
    b = '<meta property="og:title" content="Title A">'
    for meta in (a, b):
        assert jobindex.parse_detail(f"<html><head>{meta}</head><body>h1</body></html>", "h1")["title"] == "Title A"


def test_title_fallback_chain_and_parse_error():
    assert jobindex.parse_detail('<meta itemprop="name" content="From itemprop"><h1>H</h1>', "h1")["title"] == "From itemprop"
    assert jobindex.parse_detail("<body><h1> From  h1 </h1></body>", "h1")["title"] == "From h1"
    with pytest.raises(BoardError) as e:
        jobindex.parse_detail("<html><body><p>nothing</p></body></html>", "h1")
    assert e.value.kind == "parse"


def test_native_page_extracts_contract_fields():
    d = jobindex.parse_detail(read("detail_native.html"), "h1500001")
    assert (d["title"], d["company"], d["location"], d["deadline"], d["date"]) == (
        "Data Engineer", "Eksempel Virksomhed A/S", "Aarhus C", "2026-11-13", "2026-10-01")
    assert d["location"] != "Aarhus C second paragraph"  # only the first paragraph of a facts block
    assert d["description"].startswith("Du skal bygge datapipelines")


def test_danish_named_entities_decoded():
    d = jobindex.parse_detail(read("detail_native.html"), "h1500001")
    assert "søger en erfaren udvikler" in d["description"] and "mængder" in d["description"] and "åbne løsninger" in d["description"]


def test_company_requires_title_suffix_match():
    page = '<title>Other Firm - Something Else</title><meta property="og:title" content="Real Job">'
    assert jobindex.parse_detail(page, "h1")["company"] is None
    page = '<title>Real Firm - Real Job</title><meta property="og:title" content="Real Job">'
    assert jobindex.parse_detail(page, "h1")["company"] == "Real Firm"


def test_passthrough_company_is_null_and_url_is_never_the_ats_url():
    d = jobindex.parse_detail(read("detail_passthrough.html"), "h1500002")
    assert d["company"] is None and "ExampleATS" not in json.dumps(d)
    assert d["url"] == "https://www.jobindex.dk/jobannonce/h1500002" and d["id"] == "h1500002"


def test_passthrough_location_from_workplace_widget():
    assert jobindex.parse_detail(read("detail_passthrough.html"), "h1500002")["location"] == "Odense"


def test_deadline_ignores_css_comment_script_and_html_comment_and_is_null_without_a_label():
    d = jobindex.parse_detail(read("detail_passthrough.html"), "h1500002")
    assert d["deadline"] == "2026-12-20"  # not 2020-01-01 (CSS), 2021-02-02 (script) or 2022-03-03 (comment)
    bare = jobindex.parse_detail('<meta property="og:title" content="T"><body><p>No dates here at all.</p></body>', "h1")
    assert bare["deadline"] is None


def test_deadline_label_is_found_in_the_visible_text_of_a_new_shape_page():
    page = '<meta property="og:title" content="T"><body><div class="job-text"><p>Application deadline: 3 November 2026</p></div></body>'
    assert jobindex.parse_detail(page, "h1")["deadline"] == "2026-11-03"


def test_to_iso_date_three_formats_and_garbage():
    assert jobindex.to_iso_date("2026-09-13T10:00:00Z") == "2026-09-13"
    assert jobindex.to_iso_date("13-09-2026") == "2026-09-13" and jobindex.to_iso_date("3/9/2026") == "2026-09-03"
    assert jobindex.to_iso_date("13. september 2026") == "2026-09-13" and jobindex.to_iso_date("13 Maj 2026") == "2026-05-13"
    for bad in (None, "", "ASAP", "snarest muligt", "31-02-2026", "13. blorg 2026", "2026-13-40"):
        assert jobindex.to_iso_date(bad) is None, bad


def test_description_is_visible_text_not_css():
    d = jobindex.parse_detail(read("detail_passthrough.html"), "h1500002")
    assert "Som projektleder hos os" in d["description"]
    assert "color:red" not in d["description"] and "deadline" not in d["description"].split("Ansøgningsfrist")[0]
    assert "var deadline" not in d["description"] and "03-03-2022" not in d["description"]


def test_a_short_body_is_replaced_by_the_meta_description():
    page = '<meta property="og:title" content="T"><meta property="og:description" content="A longer teaser from the meta tags."><body><p>Short.</p></body>'
    assert jobindex.parse_detail(page, "h1")["description"] == "A longer teaser from the meta tags."


def test_detail_date_from_time_element():
    page = '<meta property="og:title" content="T"><time datetime="2026-10-14T22:30:00Z">x</time><body></body>'
    assert jobindex.parse_detail(page, "h1")["date"] == "2026-10-15"


def test_a_removed_ad_answered_by_a_page_that_does_not_mention_it_is_not_found():
    board = Board(**{"/jobannonce/h7": "<html><title>Jobsøgning</title><h1>Find job</h1></html>"})
    with board.client() as c, pytest.raises(BoardError) as e:
        jobindex.detail("h7", c)
    assert e.value.kind == "not_found"


def test_a_404_is_not_found_a_403_is_blocked_and_a_titleless_page_is_a_parse_error():
    board = Board(**{"/jobannonce/h2": httpx.Response(403), "/jobannonce/h3": "<html>h3</html>"})
    for ref, kind in (("h1", "not_found"), ("h2", "blocked"), ("h3", "parse")):
        with board.client() as c, pytest.raises(BoardError) as e:
            jobindex.detail(ref, c)
        assert e.value.kind == kind, ref


# ---- the command line ----

def test_the_cli_prints_the_contract_envelope_and_exits_nonzero_on_a_board_failure(monkeypatch, capsys):
    board = Board(**{"/jobannonce/h2": httpx.Response(403)})
    real = http.Client
    monkeypatch.setattr(http, "Client", lambda name, hosts, **kw: real(name, hosts, transport=httpx.MockTransport(board), robots=gate, sleep=lambda s: None))
    assert contract.run_cli(jobindex, ["search", "--query", "python", "--limit", "2", "--recency-days", "30"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["meta"]["board"] == "jobindex" and out["meta"]["count"] == len(out["results"]) == 2
    assert contract.run_cli(jobindex, ["detail", "h1699736"]) == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["company"] == "BEUMER Group"
    assert contract.run_cli(jobindex, ["detail", "h2"]) == 1
    assert "jobindex: blocked:" in capsys.readouterr().err


# ---- the one live smoke ----

@pytest.mark.live
def test_live_search_and_detail_through_the_client():
    # The real guards.robots may not exist yet; these URLs were checked by hand against the captured rules.
    with http.Client(jobindex.NAME, jobindex.HOSTS, robots=lambda url: robots_allows(url), timeout=90.0) as c:
        found = jobindex.search(Query("python", recency_days=30, limit=5), c)
        assert found and all(p["url"].startswith("https://www.jobindex.dk/jobannonce/") for p in found)
        one = jobindex.detail(found[0]["id"], c)
    assert one["id"] == found[0]["id"] and one["title"] and one["description"]
    print(json.dumps({"search": found, "detail": {k: (v if k != "description" else v[:200]) for k, v in one.items()}}, ensure_ascii=False))

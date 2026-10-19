"""boards/linkedin.py on synthetic fixtures (see tests/fixtures/boards/linkedin/SYNTHETIC.md). Nothing here touches the network."""
from __future__ import annotations

import json
import socket
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from jobloop.boards import contract, http, linkedin
from jobloop.boards.contract import BoardError, Query

FIX = Path(__file__).parent / "fixtures" / "boards" / "linkedin"
HOST = "www.linkedin.com"
REAL_CLIENT = http.Client


def fixture(name: str) -> str:
    return (FIX / name).read_text()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def make_client(handler, robots=lambda url: True, **kw):
    seen, sleeps = [], []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    c = REAL_CLIENT("linkedin", linkedin.HOSTS, transport=httpx.MockTransport(wrapped), sleep=sleeps.append, robots=robots, **kw)
    c.seen, c.sleeps = seen, sleeps
    return c


def html(body: str, status: int = 200):
    return lambda request: httpx.Response(status, text=body, headers={"content-type": "text/html"})


def q(**kw):
    return Query(**{"text": "python", "location": "Berlin, Germany", **kw})


def card(n: int) -> str:
    return (f'<li><div data-entity-urn="urn:li:jobPosting:{3000000000 + n}"><h3 class="base-search-card__title">Role {n}</h3></div></li>')


# --- flag validation and request construction -------------------------------------------------------------------

def test_linkedin_search_without_location_is_error():
    c = make_client(html(fixture("search_cards.html")))
    for location in ("", "   "):
        with pytest.raises(BoardError) as e:
            linkedin.search(q(location=location), c)
        assert e.value.kind == "invalid" and "location" in str(e.value)
    assert c.seen == []


def test_unknown_flag_is_rejected_loudly(monkeypatch, capsys):
    c = make_client(html(""))
    monkeypatch.setattr(http, "Client", lambda *a, **k: c)
    with pytest.raises(SystemExit) as e:
        contract.run_cli(linkedin, ["search", "--query", "x", "--location", "Berlin", "--workplace", "remote", "--acknowledge-terms"])
    assert e.value.code == 2 and "--workplace" in capsys.readouterr().err and c.seen == []


@pytest.mark.parametrize("flag,value", [("--limit", "abc"), ("--limit", "1.5"), ("--recency-days", "0.5"), ("--recency-days", "x")])
def test_positive_int_flags_reject_text_and_fraction(flag, value, capsys):
    with pytest.raises(SystemExit) as e:
        contract.run_cli(linkedin, ["search", "-q", "x", "-l", "Berlin", flag, value, "--acknowledge-terms"])
    assert e.value.code == 2 and flag in capsys.readouterr().err


@pytest.mark.parametrize("flag,value", [("--limit", "0"), ("--recency-days", "0"), ("--recency-days", "-5"), ("--limit", "201")])
def test_zero_and_negative_numbers_fail_before_any_request(flag, value, monkeypatch, capsys):
    c = make_client(html(fixture("search_cards.html")))
    monkeypatch.setattr(http, "Client", lambda *a, **k: c)
    assert contract.run_cli(linkedin, ["search", "-q", "x", "-l", "Berlin", flag, value, "--acknowledge-terms"]) == 1
    assert "invalid" in capsys.readouterr().err and c.seen == []


def test_numeric_flag_without_value_fails(capsys):
    with pytest.raises(SystemExit) as e:
        contract.run_cli(linkedin, ["search", "-q", "x", "-l", "Berlin", "--limit"])
    assert e.value.code == 2 and "--limit" in capsys.readouterr().err


def test_recency_days_maps_to_f_tpr_seconds():
    assert linkedin.build_params(q(recency_days=7))["f_TPR"] == "r604800"
    assert linkedin.build_params(q(recency_days=1))["f_TPR"] == "r86400"
    assert linkedin.build_params(q(recency_days=30))["f_TPR"] == "r2592000"
    assert "f_TPR" not in linkedin.build_params(q(recency_days=None))


def test_page_offset_is_ten_per_page():
    assert [linkedin.build_params(q(), p)["start"] for p in (0, 1, 2)] == ["0", "10", "20"]


def test_location_and_keywords_pass_through():
    c = make_client(html(fixture("search_cards.html")))
    linkedin.search(q(text="data  engineer", location="Mumbai, Maharashtra, India", recency_days=7, limit=5), c)
    req = c.seen[0]
    assert req.url.host == HOST and req.url.path == "/jobs-guest/jobs/api/seeMoreJobPostings/search"
    assert {k: v[0] for k, v in parse_qs(req.url.query.decode()).items()} == {
        "keywords": "data engineer", "location": "Mumbai, Maharashtra, India", "f_TPR": "r604800", "start": "0"}
    assert "linkedin" not in req.headers["user-agent"].lower().replace("job-search-loop", "") and "Mozilla" not in req.headers["user-agent"]
    assert "authorization" not in req.headers and "cookie" not in req.headers


def test_limit_slices_results_client_side():
    c = make_client(html(fixture("search_cards.html")))
    assert [r["id"] for r in linkedin.search(q(limit=2), c)] == ["1000000001", "1000000002"]
    assert len(linkedin.search(q(limit=20), make_client(html(fixture("search_cards.html"))))) == 3


def test_search_pages_are_capped_and_paced():
    pages = {}

    def handler(request):
        start = int(parse_qs(request.url.query.decode())["start"][0])
        pages[start] = pages.get(start, 0) + 1
        return httpx.Response(200, text="".join(card(start + i) for i in range(10)), headers={"content-type": "text/html"})

    c = make_client(handler)
    found = linkedin.search(q(limit=200), c)
    assert sorted(pages) == [0, 10, 20] and len(found) == 30 and len(c.seen) == linkedin.MAX_PAGES
    assert c.sleeps == [linkedin.DELAY_SECONDS] * 2


def test_search_stops_at_an_empty_page():
    def handler(request):
        start = int(parse_qs(request.url.query.decode())["start"][0])
        return httpx.Response(200, text=card(1) if start == 0 else "", headers={"content-type": "text/html"})

    c = make_client(handler)
    assert len(linkedin.search(q(limit=50), c)) == 1 and len(c.seen) == 2


def test_requests_per_run_are_hard_capped():
    c = make_client(html(fixture("detail_open.html")))
    for _ in range(linkedin.MAX_REQUESTS):
        linkedin.detail("1234567", c)
    with pytest.raises(BoardError) as e:
        linkedin.detail("1234567", c)
    assert e.value.kind == "rate_limited" and len(c.seen) == linkedin.MAX_REQUESTS


def test_the_first_request_is_not_delayed_and_later_ones_are():
    c = make_client(html(fixture("detail_open.html")))
    linkedin.detail("1234567", c)
    assert c.sleeps == []
    linkedin.detail("1234567", c)
    assert c.sleeps == [linkedin.DELAY_SECONDS]


def test_search_meta_count_in_the_cli_envelope(monkeypatch, capsys):
    c = make_client(html(fixture("search_cards.html")))
    monkeypatch.setattr(http, "Client", lambda board, hosts, **kw: c)
    assert contract.run_cli(linkedin, ["search", "-q", "python", "-l", "Berlin", "--recency-days", "30", "--acknowledge-terms"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["meta"]["count"] == 3 == len(out["results"]) and out["meta"]["board"] == "linkedin"
    assert set(out["results"][0]) == set(contract.FIELDS)


def test_search_failure_is_a_board_error_with_a_kind():
    def boom(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(BoardError) as e:
        linkedin.search(q(), make_client(boom))
    assert e.value.kind == "network"
    with pytest.raises(BoardError) as e:
        linkedin.search(q(), make_client(html("oops", 400)))
    assert e.value.kind == "invalid"


# --- terms, robots, blocks --------------------------------------------------------------------------------------

def test_linkedin_is_disabled_by_default():
    assert linkedin.DEFAULT_ENABLED is False


def test_linkedin_enable_requires_personal_use_acknowledgement(monkeypatch, capsys):
    c = make_client(html(fixture("search_cards.html")))
    monkeypatch.setattr(http, "Client", lambda *a, **k: c)
    assert contract.run_cli(linkedin, ["search", "-q", "python", "-l", "Berlin"]) == 1
    err = capsys.readouterr().err
    assert "--acknowledge-terms" in err and "personal" in err.lower() and "terms" in err.lower() and c.seen == []
    assert contract.run_cli(linkedin, ["search", "-q", "python", "-l", "Berlin", "--acknowledge-terms"]) == 0
    assert "personal" in capsys.readouterr().err.lower() and 1 <= len(c.seen) <= linkedin.MAX_PAGES


def test_the_acknowledgement_is_the_only_thing_that_waives_robots(monkeypatch):
    assert linkedin.ACK_WAIVES_ROBOTS is True
    c = make_client(html(fixture("search_cards.html")), robots=lambda url: False)
    with pytest.raises(BoardError) as e:
        linkedin.search(q(), c)
    assert e.value.kind == "robots" and c.seen == []
    seen = {}
    monkeypatch.setattr(http, "Client", lambda board, hosts, **kw: seen.update(kw) or make_client(html(fixture("search_cards.html"))))
    contract.run_cli(linkedin, ["search", "-q", "x", "-l", "Berlin", "--acknowledge-terms"])
    assert seen["robots"]("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search") is True


def test_a_429_is_reported_as_rate_limited_and_nothing_more_is_sent():
    c = make_client(html(fixture("challenge_page.html"), 429), retries=1)
    with pytest.raises(BoardError) as e:
        linkedin.search(q(), c)
    assert e.value.kind == "rate_limited"
    sent = len(c.seen)
    with pytest.raises(BoardError) as again:
        linkedin.detail("1234567", c)
    assert again.value.kind == "rate_limited" and len(c.seen) == sent


def test_a_challenge_page_is_blocked_never_parsed_as_results():
    for name in ("challenge_page.html", "authwall.html"):
        with pytest.raises(BoardError) as e:
            linkedin.search(q(), make_client(html(fixture(name))))
        assert e.value.kind == "blocked"
        with pytest.raises(BoardError) as e:
            linkedin.detail("1234567", make_client(html(fixture(name))))
        assert e.value.kind == "blocked"


def test_a_403_is_blocked_and_stops_the_run():
    c = make_client(html("no", 403))
    with pytest.raises(BoardError) as e:
        linkedin.search(q(), c)
    assert e.value.kind == "blocked"
    with pytest.raises(BoardError):
        linkedin.search(q(), c)
    assert len(c.seen) == 1


def test_empty_search_body_is_zero_results_not_an_error():
    c = make_client(html(fixture("search_empty.html")))
    assert linkedin.search(q(), c) == [] and len(c.seen) == 1


def test_no_login_cookie_or_credential_is_ever_sent():
    c = make_client(html(fixture("search_cards.html")))
    linkedin.search(q(), c)
    assert not {"cookie", "authorization"} & set(c.seen[0].headers)


# --- search card parsing ----------------------------------------------------------------------------------------

def test_saved_synthetic_search_response_parses_expected_ids():
    cards = linkedin.parse_cards(fixture("search_cards.html"))
    assert [c["id"] for c in cards] == ["1000000001", "1000000002", "1000000003"]
    assert cards[0]["title"] == "Python Developer" and cards[0]["deadline"] is None and cards[0]["description"] is None


def test_malformed_card_does_not_break_others():
    ids = [c["id"] for c in linkedin.parse_cards(fixture("search_card_variants.html"))]
    assert "2000000007" in ids and "2000000001" in ids


def test_card_without_digits_is_skipped():
    titles = [c["title"] for c in linkedin.parse_cards(fixture("search_card_variants.html"))]
    assert "No Digits Role" not in titles


def test_title_fallback_and_drop_when_missing():
    cards = {c["id"]: c for c in linkedin.parse_cards(fixture("search_card_variants.html"))}
    assert cards["2000000001"]["title"] == "Hidden Title Role"
    assert "2000000002" not in cards


def test_company_and_company_url_extracted():
    c = linkedin.parse_cards(fixture("search_cards.html"))
    assert c[0]["company"] == "Example Works GmbH" and c[0]["company_url"] == "https://www.linkedin.com/company/example-works"
    assert c[2]["company"] is None and c[2]["company_url"] is None


def test_location_extracted_or_null():
    c = linkedin.parse_cards(fixture("search_cards.html"))
    assert [x["location"] for x in c] == ["Berlin, Berlin, Germany", "Italy", None]


def test_date_from_listdate_both_class_variants():
    c = linkedin.parse_cards(fixture("search_cards.html"))
    assert [x["date"] for x in c] == ["2026-10-18", "2026-10-14", None]


def test_url_strips_tracking_and_has_fallback():
    c = {x["id"]: x["url"] for x in linkedin.parse_cards(fixture("search_cards.html") + fixture("search_card_variants.html"))}
    assert c["1000000001"] == "https://de.linkedin.com/jobs/view/python-developer-at-example-works-1000000001"
    assert c["2000000004"] == "https://www.linkedin.com/jobs/view/2000000004"


def test_a_card_link_to_another_host_is_replaced_by_the_canonical_url():
    c = {x["id"]: x["url"] for x in linkedin.parse_cards(fixture("search_card_variants.html"))}
    assert c["2000000005"] == "https://www.linkedin.com/jobs/view/2000000005"


def test_search_results_already_marked_closed_are_dropped():
    assert "2000000006" not in [c["id"] for c in linkedin.parse_cards(fixture("search_card_variants.html"))]


def test_entity_decoding_numeric_hex_and_emoji():
    for raw, want in [("Caf&#xE9;", "Café"), ("Deb&#XFC;t", "Debüt"), ("Caf&#233;", "Café"), ("&#128512;", "\U0001F600"),
                      ("&#x1F600;", "\U0001F600"), ("N&#xF8;rrebro", "Nørrebro"), ("A &amp; B", "A & B"), ("a&nbsp;b", "a\xa0b".replace("\xa0", " "))]:
        assert linkedin.clean_text(raw) == want
    assert linkedin.clean_text("x&#99999999;y") == "xy"
    assert linkedin.clean_text("&amp;lt;") == "&lt;"
    assert linkedin.parse_cards(fixture("search_cards.html"))[1]["title"] == "Data Engineer & Analyst at Café Demo"


def test_strip_tags_and_collapse_whitespace():
    assert linkedin.clean_text("  <b>Senior</b>\n\t  <i>Dev</i>  ") == "Senior Dev"
    assert linkedin.clean_text("<br>") is None and linkedin.clean_text(None) is None


def test_results_pass_the_contract_shape():
    out = [contract.result(c) for c in linkedin.parse_cards(fixture("search_cards.html"))]
    assert all(r and set(r) == set(contract.FIELDS) for r in out)


# --- detail parsing and active detection -------------------------------------------------------------------------

def test_detail_without_id_is_error():
    c = make_client(html(""))
    for ref in ("", "   "):
        with pytest.raises(BoardError) as e:
            linkedin.detail(ref, c)
        assert e.value.kind == "invalid"
    assert c.seen == []


def test_normalize_id_accepts_all_documented_forms():
    for ref in ("1234567890", "urn:li:jobPosting:1234567890", "software-engineer-1234567890",
                "https://www.linkedin.com/jobs/view/1234567890", "https://de.linkedin.com/jobs/view/software-engineer-at-x-1234567890/?trk=a&b=c",
                "linkedin.com/jobs/view/1234567890", "http://uk.linkedin.com/jobs/view/dev-1234567890/", "www.linkedin.com/jobs/view/1234567890"):
        assert linkedin.normalize_id(ref) == "1234567890", ref


def test_normalize_id_rejects_foreign_and_lookalike_hosts():
    for ref in ("https://boards.greenhouse.io/acme/jobs/1234567", "https://jobs.lever.co/acme/1234567", "https://example.com/jobs/view/1234567",
                "https://linkedin.com.evil.io/jobs/view/1234567", "https://notlinkedin.com/jobs/view/1234567",
                "https://www.linkedin.com@evil.io/jobs/view/1234567", "https://user:pw@www.linkedin.com/jobs/view/1234567",
                "https://www.linkedin.com/in/someone-1234567", "https://www.linkedin.com/feed/",
                "https://www.linkedin.com/jobs/search/?currentJobId=1234567", "https://www.linkedin.com:8443/jobs/view/1234567",
                "ftp://www.linkedin.com/jobs/view/1234567", "12345", "software-engineer-123", "urn:li:jobPosting:123", "", "example.com"):
        with pytest.raises(BoardError) as e:
            linkedin.normalize_id(ref)
        assert e.value.kind == "invalid", ref


def test_foreign_url_rejected_before_any_request():
    c = make_client(html(fixture("detail_open.html")))
    with pytest.raises(BoardError):
        linkedin.detail("https://boards.greenhouse.io/acme/jobs/1234567", c)
    assert c.seen == []


def test_detail_request_url_built_from_extracted_id():
    c = make_client(html(fixture("detail_open.html")))
    linkedin.detail("https://de.linkedin.com/jobs/view/platform-engineer-1234567890?trk=x", c)
    assert str(c.seen[0].url) == "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1234567890"


def test_detail_404_is_not_found():
    with pytest.raises(BoardError) as e:
        linkedin.detail("1234567", make_client(html("", 404)))
    assert e.value.kind == "not_found" and "closed" not in str(e.value)


def test_detail_failure_is_a_board_error_with_a_kind():
    def boom(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(BoardError) as e:
        linkedin.detail("1234567", make_client(boom))
    assert e.value.kind == "network"


def test_nested_div_extraction():
    inner = linkedin.extract_div(fixture("detail_open.html"))
    assert "Nested block stays inside." in inner and "Python" in inner and "Mid-Senior" not in inner
    assert linkedin.extract_div('<div class="description__text"></div>') == ""
    assert linkedin.extract_div("<div class='x'>nothing</div>") is None


def test_description_keeps_paragraph_and_list_boundaries():
    text = linkedin.detail("1234567", make_client(html(fixture("detail_open.html"))))["description"]
    assert text.splitlines()[0] == "Build the internal platform."
    assert [l for l in text.splitlines() if l] == ["Build the internal platform.", "Requirements:", "Python", "Docker", "Nested block stays inside."]
    assert "Python\nDocker" in text
    assert "<" not in text and "\n\n\n" not in text and text == text.strip()


def test_criteria_fields_extracted():
    p = linkedin.parse_detail(fixture("detail_open.html"), "1234567")
    assert p["criteria"] == {"seniority level": "Mid-Senior level", "employment type": "Contract",
                             "job function": "Information Technology", "industries": None}


def test_detail_title_company_location():
    p = linkedin.parse_detail(fixture("detail_open.html"), "1234567")
    assert (p["title"], p["company"], p["location"]) == ("Platform Engineer & Tooling Lead", "Example Works GmbH", "Berlin, Berlin, Germany")
    assert p["company_url"] == "https://www.linkedin.com/company/example-works"
    assert linkedin.parse_detail('<div class="description__text"><p>Hi</p></div>', "1234567")["title"] == "(untitled)"


def test_closed_banner_only_in_top_card():
    assert linkedin.page_state(fixture("detail_closed.html")) == "closed"
    assert linkedin.page_state(fixture("detail_phrase_in_description.html")) == "no-banner"
    assert linkedin.page_state(fixture("detail_class_in_description.html")) == "no-banner"
    assert linkedin.page_state(fixture("detail_open.html")) == "no-banner"


def test_active_is_tristate_with_unknown_for_empty_page():
    for body in ("", "   \n", fixture("challenge_page.html"), fixture("authwall.html")):
        assert linkedin.page_state(body) == "unknown"
    with pytest.raises(BoardError) as e:
        linkedin.detail("1234567", make_client(html("")))
    assert e.value.kind == "parse"


def test_a_closed_posting_raises_not_found_closed():
    with pytest.raises(BoardError) as e:
        linkedin.detail("1234567", make_client(html(fixture("detail_closed.html"))))
    assert (e.value.kind, str(e.value)) == ("not_found", "closed")


def test_a_live_posting_quoting_the_closed_phrase_is_still_returned():
    for name in ("detail_phrase_in_description.html", "detail_class_in_description.html"):
        assert linkedin.detail("1234567", make_client(html(fixture(name))))["title"] == "Platform Engineer & Tooling Lead"


def test_closed_cli_detail_exits_1_with_the_reason(monkeypatch, capsys):
    c = make_client(html(fixture("detail_closed.html")))
    monkeypatch.setattr(http, "Client", lambda *a, **k: c)
    assert contract.run_cli(linkedin, ["detail", "1234567", "--acknowledge-terms"]) == 1
    assert "not_found: closed" in capsys.readouterr().err


def test_saved_closed_posting_fixture():
    p = linkedin.parse_detail(fixture("detail_closed.html"), "1234567")
    assert p["state"] == "closed" and p["title"] == "Platform Engineer & Tooling Lead"
    assert p["criteria"]["seniority level"] == "Mid-Senior level" and p["criteria"]["employment type"] == "Contract"


def test_detail_has_no_apply_url_field():
    posting = linkedin.detail("1234567", make_client(html(fixture("detail_open.html"))))
    assert set(posting) == set(contract.FIELDS)
    assert "apply" not in json.dumps(linkedin.parse_detail(fixture("detail_open.html"), "1234567")).lower().replace("applications", "")
    assert not any("apply" in k.lower() for k in linkedin.parse_detail(fixture("detail_open.html"), "1")), "no applyUrl key"


def test_detail_date_null_and_canonical_url():
    posting = linkedin.detail("https://de.linkedin.com/jobs/view/x-1234567890?trk=1", make_client(html(fixture("detail_open.html"))))
    assert posting["date"] is None and posting["deadline"] is None
    assert posting["url"] == "https://www.linkedin.com/jobs/view/1234567890" and posting["id"] == "1234567890"


# --- fixtures ----------------------------------------------------------------------------------------------------

def test_fixture_provenance_recorded_and_no_network():
    note = (FIX / "SYNTHETIC.md").read_text().lower()
    assert "synthetic" in note and "429" in note and "not saved" in note
    with pytest.raises(AssertionError):
        socket.socket().connect(("example.com", 443))

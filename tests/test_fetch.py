"""fetch.py: the ladder (board detail, plain, browser headers after a 403, careers page), each rung under the same rules,
and the line between closed (positive evidence) and unreachable (everything else)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from jobloop import fetch as F
from jobloop.boards.contract import BoardError
from jobloop.boards.http import USER_AGENT

FIX = Path(__file__).parent / "fixtures" / "rank"
POSTING = (FIX / "posting.html").read_text()
URL = "https://jobs.fjordline.example/p/123"
CAREERS = "https://careers.fjordline.example/open"
NO_SLEEP = {"sleep": lambda s: None}


class Site:
    """A scripted web: url -> (status, body[, headers]) or an exception to raise. robots.txt is 404 (no policy) unless set."""

    def __init__(self, routes, robots=None):
        self.routes, self.robots, self.seen = routes, robots, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append((request.url.host, request.url.path, request.headers["user-agent"]))
        if request.url.path == "/robots.txt" and request.url.host not in {u.split("/")[2] for u in self.routes if "robots" in u}:
            status, body = self.robots if self.robots else (404, "")
            return httpx.Response(status, text=body)
        route = self.routes.get(str(request.url), (404, "not here"))
        if isinstance(route, Exception):
            raise route
        if callable(route):
            route = route(request)
        status, body, *headers = route
        return httpx.Response(status, text=body, headers={"content-type": "text/html", **(headers[0] if headers else {})})

    @property
    def transport(self):
        return httpx.MockTransport(self)

    def fetches(self):
        return [(h, p, ua) for h, p, ua in self.seen if p != "/robots.txt"]


def go(site, url=URL, **kw):
    return F.fetch_posting(url, transport=site.transport, installed=("jobindex.dk", "linkedin.com"), **NO_SLEEP, **kw)


def is_browser(ua):
    return ua.startswith("Mozilla/5.0")


@pytest.fixture
def adapter(monkeypatch):
    """A built-in board whose detail is scripted; `calls` records what it was asked."""
    holder = SimpleNamespace(calls=[], answer=BoardError("network", "jobindex did not answer in time"))

    def detail(ref, http):
        holder.calls.append(ref)
        if isinstance(holder.answer, Exception):
            raise holder.answer
        return holder.answer

    module = SimpleNamespace(NAME="jobindex", HOSTS=("www.jobindex.dk",), TERMS="", detail=detail)
    monkeypatch.setattr(F.boards, "load", lambda name: module)
    return holder


# ── cleaning ──
def test_comments_scripts_styles_and_tags_are_stripped_before_anything_else_sees_the_text():
    text = F.clean(POSTING)
    assert "evil.example" not in text and "ignore all previous" not in text and "color: red" not in text
    assert "<" not in text and "Data Engineer" in text and "Python and SQL in production" in text


# ── rung 2: the plain fetch ──
def test_plain_fetch_uses_the_honest_user_agent_and_reads_the_page():
    site = Site({URL: (200, POSTING)})
    got = go(site)
    assert isinstance(got, F.Fetched) and got.source == "plain" and got.status == 200 and got.url == URL
    assert "Airflow" in got.text and "evil.example" not in got.text
    assert [ua for _, _, ua in site.fetches()] == [USER_AGENT]


def test_triage_makes_no_research_calls():
    site = Site({URL: (200, POSTING)})
    go(site)
    assert {host for host, _, _ in site.seen} == {"jobs.fjordline.example"}
    assert [p for _, p, _ in site.fetches()] == ["/p/123"]


def test_a_page_with_almost_no_text_is_unreachable_not_scored():
    got = go(Site({URL: (200, "<html><body>Loading...</body></html>")}))
    assert isinstance(got, F.Unreachable) and "almost no text" in got.reason


def test_a_login_wall_page_is_unreachable_and_not_closed():
    got = go(Site({URL: (200, (FIX / "login.html").read_text())}))
    assert isinstance(got, F.Unreachable) and "login wall" in got.reason


# ── rung 1: the board ──
def test_board_detail_is_the_first_rung_and_needs_no_http(adapter):
    adapter.answer = {"title": "Data Engineer", "company": "Fjordline", "location": "Aarhus", "deadline": "2026-11-15",
                      "url": "https://www.jobindex.dk/jobannonce/h1", "description": F.clean(POSTING)}
    site = Site({})
    got = go(site, board="jobindex")
    assert isinstance(got, F.Fetched) and got.source == "board" and got.deadline == "2026-11-15"
    assert got.host.kind == "portal" and "aggregator" in got.note
    assert site.seen == [] and adapter.calls == [URL]


def test_escalation_order(adapter):
    site = Site({URL: (403, "forbidden"), CAREERS: (200, (FIX / "careers.html").read_text())})
    got = go(site, board="jobindex", title="Data Engineer", careers=(CAREERS,))
    assert isinstance(got, F.Fetched) and got.source == "careers"
    kinds = ["board" if p == "board" else ("browser" if is_browser(ua) else "plain") + ":" + h for h, p, ua in
             [("", "board", "")] + [x for x in site.fetches()]]
    assert adapter.calls == [URL]
    assert kinds == ["board", "plain:jobs.fjordline.example", "browser:jobs.fjordline.example", "plain:careers.fjordline.example"]


def test_a_board_that_marks_a_posting_closed_is_positive_evidence(adapter):
    adapter.answer = BoardError("not_found", "freehire posting x closed on 2026-09-01")
    got = go(Site({}), board="jobindex")
    assert isinstance(got, F.Closed) and "closed" in got.reason


def test_a_board_not_found_without_a_closed_signal_continues_down_the_ladder(adapter):
    adapter.answer = BoardError("not_found", "jobindex has no live posting h1: it was removed or has expired")
    got = go(Site({URL: (200, POSTING)}), board="jobindex")
    assert isinstance(got, F.Fetched) and got.source == "plain"


# ── rung 3: browser headers ──
def test_403_triggers_header_retry_not_closed():
    site = Site({URL: (403, "no")})
    got = go(site)
    assert isinstance(got, F.Unreachable) and not isinstance(got, F.Closed)
    assert [is_browser(ua) for _, _, ua in site.fetches()] == [False, True]


def test_the_header_retry_recovers_a_page_the_plain_fetch_was_refused():
    site = Site({URL: lambda r: (200, POSTING) if is_browser(r.headers["user-agent"]) else (403, "no")})
    got = go(site)
    assert isinstance(got, F.Fetched) and got.source == "browser-headers"


def test_header_retry_blocked_when_robots_unreadable():
    site = Site({URL: (403, "no")}, robots=(500, "oops"))
    got = go(site)
    assert isinstance(got, F.Unreachable) and "robots" in got.reason
    assert site.fetches() == []                                      # nothing fetched, so no retry in disguise


def test_header_retry_not_made_when_robots_stops_allowing_it():
    answers = iter([True, False])
    site = Site({URL: (403, "no")})
    got = F.fetch_posting(URL, transport=site.transport, installed=(), robots=lambda url: next(answers, False), **NO_SLEEP)
    assert isinstance(got, F.Unreachable) and "robots" in got.reason
    assert [is_browser(ua) for _, _, ua in site.fetches()] == [False]


def test_the_header_retry_is_made_once():
    site = Site({URL: (403, "no")})
    go(site)
    assert len(site.fetches()) == 2


@pytest.mark.parametrize("route", [(429, "slow down"), (401, "login"), (200, "<html>Just a moment... enable javascript and cookies</html>")],
                         ids=["429", "401-login", "challenge-page"])
def test_no_header_retry_after_a_429_a_login_wall_or_a_challenge(route):
    site = Site({URL: route})
    got = go(site)
    assert isinstance(got, F.Unreachable)
    assert not any(is_browser(ua) for _, _, ua in site.seen)


# ── closed versus unreachable ──
@pytest.mark.parametrize("route", [(404, "gone?"), (403, "no"), (500, "oops"), (503, "later"), httpx.ReadTimeout("slow")],
                         ids=["404", "403", "500", "503", "timeout"])
def test_404_403_5xx_timeout_are_unreachable_not_expired(route):
    got = go(Site({URL: route}))
    assert isinstance(got, F.Unreachable)


def test_unreachable_result_has_fetch_error():
    got = go(Site({URL: (404, "x")}))
    assert isinstance(got, F.Unreachable) and "404" in got.reason and "says nothing about whether it is open" in got.reason
    assert "\n" not in got.reason


def test_expired_requires_positive_evidence():
    assert isinstance(go(Site({URL: (410, "gone")})), F.Closed)
    said = go(Site({URL: (200, (FIX / "closed.html").read_text())}))
    assert isinstance(said, F.Closed) and "closed" in said.reason
    moved = go(Site({URL: (302, "", {"location": "https://jobs.fjordline.example/jobs"}),
                     "https://jobs.fjordline.example/jobs": (200, (FIX / "listing.html").read_text())}))
    assert isinstance(moved, F.Closed) and "listing" in moved.reason
    assert not isinstance(go(Site({URL: (404, "no")})), F.Closed)
    assert isinstance(go(Site({URL: (200, POSTING)})), F.Fetched)


def test_a_redirect_that_still_lands_on_the_posting_is_not_a_listing():
    moved = go(Site({URL: (301, "", {"location": "https://jobs.fjordline.example/p/123/data-engineer"}),
                     "https://jobs.fjordline.example/p/123/data-engineer": (200, POSTING)}))
    assert isinstance(moved, F.Fetched)


# ── rung 4: the employer's careers page ──
def test_fragment_url_triggers_careers_page_search():
    site = Site({URL: (200, (FIX / "listing.html").read_text()), CAREERS: (200, (FIX / "careers.html").read_text())})
    got = go(site, url=URL + "#data-engineer", title="Data Engineer", careers=(CAREERS,))
    assert isinstance(got, F.Fetched) and got.source == "careers" and got.url == CAREERS
    assert got.host.kind == "unverified" and "not verified" in got.note
    assert ("jobs.fjordline.example", "/p/123", USER_AGENT) not in site.seen          # the listing was never taken as the job


def test_a_fragment_url_with_no_careers_page_is_unreachable_not_scored():
    got = go(Site({URL: (200, POSTING)}), url=URL + "#x", title="Data Engineer")
    assert isinstance(got, F.Unreachable) and "fragment" in got.reason


def test_a_careers_page_must_name_the_job():
    site = Site({URL: (404, "no"), CAREERS: (200, (FIX / "listing.html").read_text())})
    got = go(site, title="Data Engineer", careers=(CAREERS,))
    assert isinstance(got, F.Unreachable) and "does not name this job" in got.reason


def test_careers_pages_follow_the_same_client_rules():
    disallowed = Site({URL: (404, "no"), CAREERS: (200, (FIX / "careers.html").read_text())}, robots=(200, "User-agent: *\nDisallow: /\n"))
    got = go(disallowed, title="Data Engineer", careers=(CAREERS,))
    assert isinstance(got, F.Unreachable) and disallowed.fetches() == []
    plain = Site({URL: (404, "no"), "http://careers.fjordline.example/open": (200, (FIX / "careers.html").read_text())})
    assert isinstance(go(plain, title="Data Engineer", careers=("http://careers.fjordline.example/open",)), F.Unreachable)
    assert [h for h, _, _ in plain.fetches()] == ["jobs.fjordline.example"]                    # the http page was never fetched


def test_unreachable_only_after_full_ladder(adapter):
    site = Site({URL: (403, "no"), CAREERS: (500, "down")})
    got = go(site, board="jobindex", title="Data Engineer", careers=(CAREERS,))
    assert isinstance(got, F.Unreachable)
    for rung in ("jobindex detail", "plain fetch", "browser-headers retry", f"careers page {CAREERS.split('/')[2]}"):
        assert rung in got.reason


# ── the host ──
def test_host_verification_and_aggregator_versus_employer_note():
    ats = go(Site({"https://boards.greenhouse.io/fjordline/jobs/1": (200, POSTING)}), url="https://boards.greenhouse.io/fjordline/jobs/1")
    assert ats.host.kind == "ats" and "applicant-tracking" in ats.note
    own = go(Site({URL: (200, POSTING)}))
    assert own.host.kind == "unverified"


@pytest.mark.parametrize("url", ["http://jobs.fjordline.example/p/1", "https://user@jobs.fjordline.example/p/1", "", "ftp://x.example/a",
                                 "https://jobs.fjordline.example:8443/p/1"])
def test_an_unsafe_url_is_unreachable_before_any_request(url):
    site = Site({})
    got = F.fetch_posting(url, transport=site.transport, installed=(), **NO_SLEEP)
    assert isinstance(got, F.Unreachable) and site.seen == []


def test_a_fetched_value_has_a_known_source():
    with pytest.raises(ValueError):
        F.Fetched("t", URL, "guessed", 200, F.hosts.classify(URL, ()), "n")


def test_url_resolves_to_owning_board_adapter():
    assert F.board_for("https://www.jobindex.dk/jobannonce/h1") == "jobindex"
    assert F.board_for("https://jobindex.dk/jobannonce/h1") == "jobindex"
    assert F.board_for("https://sub.jobindex.dk/x") == "jobindex"
    for not_theirs in ("https://jobindex.dk.evil.example/x", "https://evil.example/jobindex.dk", "https://notjobindex.dk/x",
                       "https://user@jobindex.dk/x", "ftp://jobindex.dk/x", "jobindex.dk", ""):
        assert F.board_for(not_theirs) == "", not_theirs


def test_a_url_on_an_installed_boards_host_is_read_by_that_boards_detail_first(adapter):
    adapter.answer = {"title": "Data Engineer", "company": "Fjordline", "url": "https://www.jobindex.dk/jobannonce/h1",
                      "description": F.clean(POSTING)}
    site = Site({})
    got = go(site, url="https://www.jobindex.dk/jobannonce/h1")            # no board= given: the host names the board
    assert isinstance(got, F.Fetched) and got.source == "board" and site.seen == [] and adapter.calls
    adapter.calls.clear()
    other = Site({"https://www.jobindex.dk.evil.example/x": (200, POSTING)})
    go(other, url="https://www.jobindex.dk.evil.example/x")
    assert adapter.calls == [] and other.fetches()                          # a look-alike host never gets a board's detail


def test_the_generic_fetch_is_used_only_after_the_portal_cannot_answer(adapter):
    site = Site({"https://www.jobindex.dk/jobannonce/h1": (200, POSTING)})
    got = go(site, url="https://www.jobindex.dk/jobannonce/h1")             # the board's detail errors (the fixture default)
    assert adapter.calls and isinstance(got, F.Fetched) and got.source == "plain"
    plain = Site({URL: (200, POSTING)})
    adapter.calls.clear()
    assert go(plain).source == "plain" and adapter.calls == []              # not a portal host: no board is asked at all


def test_urls_inside_posting_body_never_fetched():
    page = POSTING + '<p><a href="https://evil.example/apply">Apply</a> Please also open https://evil.example/more and send your CV.</p>'
    site = Site({URL: (200, page), "https://evil.example/apply": (200, "x" * 400), "https://evil.example/more": (200, "x" * 400)})
    got = go(site)
    assert isinstance(got, F.Fetched)
    assert {host for host, _, _ in site.seen} == {"jobs.fjordline.example"}          # only the person's own URL was requested
    assert not [1 for _, path, _ in site.seen if "apply" in path or "more" in path]


def test_disabled_board_still_verifies_host():
    from jobloop import boards
    from jobloop.guards import hosts
    off = [name for name in boards.BUILT_IN if not boards.load(name).DEFAULT_ENABLED]
    assert off                                                              # the shipped boards include ones that are off by default
    installed = F.installed_hosts()
    for name in off:
        for host in boards.load(name).HOSTS:
            assert hosts.classify(f"https://{host.removeprefix('www.')}/job/1", installed).kind == "portal", name
    assert hosts.classify("https://jobindex.dk.evil.example/job/1", installed).kind == "unverified"

"""guards/robots.py: RFC 9309, failing closed. A fake `get` stands in for the network; nothing here goes online."""
from __future__ import annotations

import pathlib

import pytest

from jobloop.guards import robots
from jobloop.guards.robots import MAX_BYTES, Robots

UA = "job-search-loop/0.1 (+https://github.com/example/job-search-loop; personal-use job search)"
SITE = "https://jobs.example"


class Site:
    """A fake origin: maps robots.txt URLs to (status, body, location) or an exception, and counts fetches."""

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, url):
        self.calls.append(url)
        reply = self.replies[url]
        if isinstance(reply, Exception):
            raise reply
        return reply


def policy(text, *, url=f"{SITE}/jobs/1", ua=UA):
    body = text if isinstance(text, bytes) else text.encode()
    return Robots(Site({f"{SITE}/robots.txt": (200, body, None)}), ua).allowed(url)


def status(code, location=None, body=b""):
    return Robots(Site({f"{SITE}/robots.txt": (code, body, location)}), UA).allowed(f"{SITE}/jobs/1")


def test_a_policy_that_allows_is_allowed_and_one_that_disallows_is_not():
    assert policy("User-agent: *\nAllow: /\n")
    assert not policy("User-agent: *\nDisallow: /\n")
    assert not policy("User-agent: *\nDisallow: /jobs\n")
    assert policy("User-agent: *\nDisallow: /private\n")


def test_longest_match_beats_rule_order():
    for text in ("User-agent: *\nDisallow: /jobs\nAllow: /jobs/open\n", "User-agent: *\nAllow: /jobs/open\nDisallow: /jobs\n"):
        assert policy(text, url=f"{SITE}/jobs/open/1")
        assert not policy(text, url=f"{SITE}/jobs/closed")


def test_a_tie_goes_to_allow_in_both_orders_as_rfc_9309_says():
    for text in ("User-agent: *\nDisallow: /jobs\nAllow: /jobs\n", "User-agent: *\nAllow: /jobs\nDisallow: /jobs\n"):
        assert policy(text)


def test_blank_lines_within_record_keep_rules():
    assert not policy("User-agent: *\n\nDisallow: /jobs\n\n\nDisallow: /x\n")
    assert not policy("User-agent: *\n\nAllow: /x\n\nDisallow: /jobs\n")


def test_no_stdlib_robotparser_import():
    assert "robotparser" not in pathlib.Path(robots.__file__).read_text()


def test_wildcard_end_anchor_and_literal_dollar():
    assert not policy("User-agent: *\nDisallow: /*/apply\n", url=f"{SITE}/jobs/9/apply")
    assert policy("User-agent: *\nDisallow: /*/apply\n", url=f"{SITE}/jobs/9/other")
    assert not policy("User-agent: *\nDisallow: /*.pdf$\n", url=f"{SITE}/cv.pdf")
    assert policy("User-agent: *\nDisallow: /*.pdf$\n", url=f"{SITE}/cv.pdf?x=1")
    assert policy("User-agent: *\nDisallow: /a$b\n", url=f"{SITE}/a")
    assert not policy("User-agent: *\nDisallow: /a$b\n", url=f"{SITE}/a$b")
    assert not policy("User-agent: *\nDisallow: /*\n")
    assert not policy("User-agent: *\nDisallow: /a**b\n", url=f"{SITE}/axxb")


def test_a_wildcard_does_not_make_a_regex_of_the_rule():
    assert policy("User-agent: *\nDisallow: /a.c\n", url=f"{SITE}/abc")
    assert policy("User-agent: *\nDisallow: /(x\n", url=f"{SITE}/x")
    assert not policy("User-agent: *\nDisallow: /(x\n", url=f"{SITE}/(x")


def test_percent_encoded_rules_match_decoded_path():
    text = "User-agent: *\nDisallow: /foo%20bar\n"
    assert not policy(text, url=f"{SITE}/foo%20bar")
    assert not policy(text, url=f"{SITE}/foo bar".replace(" ", "%20"))
    assert policy(text, url=f"{SITE}/foobar")
    assert not policy("User-agent: *\nDisallow: /foo bar\n", url=f"{SITE}/foo%20bar")
    assert not policy("User-agent: *\nDisallow: /%7Euser\n", url=f"{SITE}/~user")
    assert not policy("User-agent: *\nDisallow: /~user\n", url=f"{SITE}/%7euser")
    assert not policy("User-agent: *\nDisallow: /café\n", url=f"{SITE}/caf%C3%A9")
    assert not policy("User-agent: *\nDisallow: /caf%c3%a9\n", url=f"{SITE}/café")


def test_an_encoded_slash_is_not_a_slash():
    assert policy("User-agent: *\nDisallow: /a/b\n", url=f"{SITE}/a%2Fb")
    assert not policy("User-agent: *\nDisallow: /a%2Fb\n", url=f"{SITE}/a%2fb")


def test_query_string_is_part_of_matched_path():
    assert not policy("User-agent: *\nDisallow: /search?q=\n", url=f"{SITE}/search?q=nurse")
    assert not policy("User-agent: *\nDisallow: /*?\n", url=f"{SITE}/jobs?page=2")
    assert policy("User-agent: *\nDisallow: /*?\n", url=f"{SITE}/jobs")
    assert policy("User-agent: *\nDisallow: /search?q=\n", url=f"{SITE}/search")


def test_a_fragment_is_never_sent_or_matched():
    assert not policy("User-agent: *\nDisallow: /jobs\n", url=f"{SITE}/jobs#x")
    assert policy("User-agent: *\nDisallow: /x\n", url=f"{SITE}/jobs#/x")


def test_empty_disallow_and_ruleless_allow():
    assert policy("User-agent: *\nDisallow:\n")
    assert policy("# nothing to say\n# really\n")
    assert policy("User-agent: *\n")
    assert policy("Sitemap: https://jobs.example/sitemap.xml\n")


def test_inline_comments_stripped():
    assert not policy("User-agent: * # everyone\nDisallow: /jobs # no\n")
    assert policy("User-agent: *\nDisallow: /private # /jobs\n")


def test_agent_selection_rules():
    both = "User-agent: *\nDisallow: /\n\nUser-agent: job-search-loop\nAllow: /\n"
    assert policy(both)
    assert not policy("User-agent: *\nDisallow: /\n", ua="other-bot/1")
    assert not policy("User-agent: JOB-SEARCH-LOOP\nDisallow: /\n")
    assert policy("User-agent: somebody-else\nDisallow: /\n")
    assert not policy("User-agent: a\nUser-agent: job-search-loop\nUser-agent: b\nDisallow: /\n")
    assert policy("User-agent: *\nDisallow: /jobs\n\nUser-agent: job-search-loop\nDisallow: /other\n")
    assert not policy("User-agent: job-search-loop/0.1\nDisallow: /\n")


def test_named_agent_optout_honoured():
    assert not policy("User-agent: *\nAllow: /\n\nUser-agent: job-search-loop\nDisallow: /\n")


def test_two_groups_for_the_same_agent_are_combined():
    text = "User-agent: job-search-loop\nDisallow: /a\n\nUser-agent: *\nAllow: /\n\nUser-agent: job-search-loop\nDisallow: /jobs\n"
    assert not policy(text)
    assert not policy(text, url=f"{SITE}/a")


def test_rules_before_any_user_agent_apply_to_nobody():
    assert policy("Disallow: /\n")
    assert policy("Disallow: /\nUser-agent: *\nAllow: /\n")


def test_field_names_are_case_insensitive_and_common_typos_still_disallow():
    assert not policy("USER-AGENT: *\nDISALLOW: /jobs\n")
    assert not policy("User-agent: *\nDissallow: /jobs\n")
    assert not policy("Useragent: *\nDisallow: /jobs\n")


def test_a_rule_without_a_leading_slash_is_read_as_if_it_had_one():
    assert not policy("User-agent: *\nDisallow: jobs\n")


def test_bom_does_not_hide_first_group():
    body = b"\xef\xbb\xbfUser-agent: *\nDisallow: /\n"
    assert not policy(body)
    assert not policy(body, ua="Other-Bot/2")
    assert policy(b"\xef\xbb\xbfUser-agent: *\nAllow: /\n")
    assert not policy(b"\xef\xbb\xbf\xef\xbb\xbfUser-agent: *\nDisallow: /\n")


def test_a_bom_only_or_empty_body_is_unreadable_and_closes_the_site():
    for body in (b"", b"\xef\xbb\xbf", b"\n\n  \n", b"\xef\xbb\xbf\n"):
        assert not policy(body), body


def test_soft_200_is_unreadable_and_closes_the_site():
    pages = (b"<html><body>404 Not Found</body></html>", b"<!doctype html><title>x</title>", b'{"error":"not found"}',
             b"Page not found", b"Error: not found", b"<?xml version='1.0'?><error/>", b"  \n<html>Disallow: /x</html>",
             b"User agent *\nDisallow /")
    for page in pages:
        assert not policy(page), page
        assert not policy(page, url=f"{SITE}/anything/else")


def test_undecodable_rule_line_unconfirmed_comment_harmless():
    assert not policy(b"User-agent: *\nDisallow: /\xff\xfe\n")
    assert not policy(b"User-agent: *\nAllow: /\nDisallow: /x\xc3\n")
    assert not policy(b"User-agent: \xff*\nAllow: /\n")
    assert not policy(b"User-agent: *\xff\nAllow: /\n")
    assert not policy(b"\xffDisallow: /x\nUser-agent: *\nAllow: /\n")
    assert not policy("User-agent: *\nAllow: /\nDisallow: /caf�\n".encode())
    assert policy(b"User-agent: *\nAllow: /\n# caf\xe9 \xff\nSitemap: https://x.example/\xff\n")


def test_utf8_non_ascii_rules_obeyed():
    assert not policy("User-agent: *\nDisallow: /stillinger/københavn\n", url=f"{SITE}/stillinger/k%C3%B8benhavn/1")
    assert policy("User-agent: *\nDisallow: /stillinger/københavn\n", url=f"{SITE}/stillinger/aarhus")
    assert not policy("# ok æøå\nUser-agent: *\nDisallow: /\n")


def test_only_the_first_500_kib_is_read_and_a_half_read_line_is_never_obeyed():
    filler = b"# " + b"x" * 60 + b"\n"
    head = b"User-agent: *\nAllow: /\n" + filler * ((MAX_BYTES - 40) // len(filler))
    assert len(head) < MAX_BYTES
    late = head + b"x" * (MAX_BYTES - len(head) - 6) + b"\nDisallow: /jobs\n"
    assert policy(late)
    assert not policy(b"User-agent: *\nDisallow: /jobs\n" + b"# " + b"y" * MAX_BYTES + b"\n")
    cut_mid_rule = head + b"#" * (MAX_BYTES - len(head) - 12) + b"\nDisallow: /jobs\n"
    assert len(cut_mid_rule) > MAX_BYTES and policy(cut_mid_rule, url=f"{SITE}/j")
    assert policy(cut_mid_rule, url=f"{SITE}/jobs/1")


def test_404_means_allowed_and_so_does_any_plain_4xx_but_not_the_refusals():
    for code in (404, 410, 400, 405, 418, 422):
        assert status(code), code
    assert status(404, body=b"<html>\xff caf\xe9 not found</html>")


def test_a_board_refusing_us_is_disallow_everything():
    for code in (401, 403, 407, 408, 429, 451):
        assert not status(code), code


def test_unreadable_policy_is_unconfirmed_not_allowed():
    for code in (500, 502, 503, 504, 599, 100, 304, 0, 700, 600):
        assert not status(code), code


def test_network_errors_are_disallow_everything():
    for error in (OSError("down"), TimeoutError(), RuntimeError("anything"), ValueError("x")):
        assert not Robots(Site({f"{SITE}/robots.txt": error}), UA).allowed(f"{SITE}/jobs/1")


def test_redirects_are_followed_up_to_five_hops_then_unavailable():
    def chain(hops):
        replies = {f"{SITE}/robots.txt": (301, b"", "/hop1")}
        for n in range(1, hops):
            replies[f"{SITE}/hop{n}"] = (302, b"", f"/hop{n + 1}")
        replies[f"{SITE}/hop{hops}"] = (200, b"User-agent: *\nAllow: /\n", None)
        return Robots(Site(replies), UA)

    assert chain(5).allowed(f"{SITE}/jobs/1")
    assert not chain(6).allowed(f"{SITE}/jobs/1")


def test_a_redirect_loop_ends_closed():
    site = Site({f"{SITE}/robots.txt": (302, b"", "/robots.txt")})
    assert not Robots(site, UA).allowed(f"{SITE}/jobs/1")
    assert len(site.calls) == 6


def test_cross_host_redirects_are_followed_and_the_target_policy_applies_to_the_origin():
    site = Site({f"{SITE}/robots.txt": (301, b"", "https://www.other.example/robots.txt"),
                 "https://www.other.example/robots.txt": (200, b"User-agent: *\nDisallow: /jobs\n", None)})
    r = Robots(site, UA)
    assert not r.allowed(f"{SITE}/jobs/1")
    assert r.allowed(f"{SITE}/open")
    assert site.calls == [f"{SITE}/robots.txt", "https://www.other.example/robots.txt"]


def test_redirect_to_a_dead_end_is_closed():
    for location in (None, "", "ftp://x.example/robots.txt", "javascript:alert(1)", "/a\nb"):
        assert not status(302, location), location
    site = Site({f"{SITE}/robots.txt": (302, b"", "https://gone.example/robots.txt"),
                 "https://gone.example/robots.txt": (503, b"", None)})
    assert not Robots(site, UA).allowed(f"{SITE}/jobs/1")


def test_redirect_to_404_means_no_policy_and_allowed():
    site = Site({f"{SITE}/robots.txt": (302, b"", "/missing"), f"{SITE}/missing": (404, b"", None)})
    assert Robots(site, UA).allowed(f"{SITE}/jobs/1")


def test_a_redirect_error_from_the_client_fails_closed():
    site = Site({f"{SITE}/robots.txt": (302, b"", "https://not-allowed.example/robots.txt"),
                 "https://not-allowed.example/robots.txt": PermissionError("host not allowed")})
    assert not Robots(site, UA).allowed(f"{SITE}/jobs/1")


def test_robots_target_rebuilt_from_scheme_host():
    site = Site({"https://jobs.example:8443/robots.txt": (200, b"User-agent: *\nAllow: /\n", None)})
    assert Robots(site, UA).allowed("https://JOBS.Example:8443/a/b?c=d#e")
    assert site.calls == ["https://jobs.example:8443/robots.txt"]
    site = Site({"http://jobs.example/robots.txt": (200, b"User-agent: *\nAllow: /\n", None)})
    assert Robots(site, UA).allowed("HTTP://jobs.example/x/robots.txt?x=//evil.example")
    assert site.calls == ["http://jobs.example/robots.txt"]


def test_urls_that_are_not_plain_http_pages_are_never_allowed_and_never_fetched():
    for url in ("ftp://jobs.example/x", "file:///etc/passwd", "javascript:alert(1)", "//jobs.example/x", "jobs.example/x", "https://",
                "https://evil.example@jobs.example/x", "https://jobs.example\\@evil.example/", " https://jobs.example/x",
                "https://jobs.example/a b", "https://jobs.example:99999/x", "https://jobs.example:abc/x", "", "https://xn--\ud800.example/"):
        site = Site({})
        assert not Robots(site, UA).allowed(url), url
        assert site.calls == []
    assert not Robots(Site({}), UA).allowed(None)


def test_each_origin_is_fetched_once_and_a_refusal_is_remembered_too():
    site = Site({f"{SITE}/robots.txt": (200, b"User-agent: *\nDisallow: /x\n", None),
                 "https://down.example/robots.txt": (503, b"", None),
                 "http://jobs.example/robots.txt": (404, b"", None)})
    r = Robots(site, UA)
    for _ in range(3):
        assert r.allowed(f"{SITE}/a") and not r.allowed(f"{SITE}/x")
        assert not r.allowed("https://down.example/a")
    assert r.allowed("http://jobs.example/a")
    assert sorted(site.calls) == sorted([f"{SITE}/robots.txt", "https://down.example/robots.txt", "http://jobs.example/robots.txt"])


def test_a_network_error_is_not_retried_within_the_run():
    site = Site({f"{SITE}/robots.txt": OSError("down")})
    r = Robots(site, UA)
    assert not r.allowed(f"{SITE}/a") and not r.allowed(f"{SITE}/b")
    assert len(site.calls) == 1


def test_the_cache_lifetime_is_bounded():
    now = [0.0]
    site = Site({f"{SITE}/robots.txt": (200, b"User-agent: *\nAllow: /\n", None)})
    r = Robots(site, UA, ttl=100, clock=lambda: now[0])
    r.allowed(f"{SITE}/a")
    now[0] = 100
    r.allowed(f"{SITE}/a")
    assert len(site.calls) == 1
    now[0] = 101
    site.replies[f"{SITE}/robots.txt"] = (200, b"User-agent: *\nDisallow: /\n", None)
    assert not r.allowed(f"{SITE}/a")
    assert len(site.calls) == 2


def test_the_default_lifetime_is_at_most_a_day():
    assert robots.DEFAULT_TTL <= 24 * 3600


def test_a_user_agent_without_a_product_token_is_refused():
    for ua in ("", "   ", "(+https://x.example)", "/1.0"):
        with pytest.raises(ValueError, match="product token"):
            Robots(Site({}), ua)


def test_the_product_token_is_the_name_before_the_version():
    assert not policy("User-agent: job-search-loop\nDisallow: /\n", ua="Job-Search-Loop/9.9 (x)")
    assert policy("User-agent: job-search\nDisallow: /\n")
    assert policy("User-agent: job-search-loop-extra\nDisallow: /\n")


def test_a_real_world_policy_end_to_end():
    text = """﻿# jobs.example
User-agent: *
Disallow: /api/
Disallow: /*/apply$
Allow: /api/public/

Sitemap: https://jobs.example/sitemap.xml

User-agent: badbot
Disallow: /
"""
    assert policy(text, url=f"{SITE}/en/jobs/x")
    assert not policy(text, url=f"{SITE}/api/x")
    assert policy(text, url=f"{SITE}/api/public/x")
    assert not policy(text, url=f"{SITE}/en/9/apply")
    assert policy(text, url=f"{SITE}/en/9/apply?x=1")

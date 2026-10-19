"""guards/hosts.py: which tier a posting's host is in, and that every trick fails closed to unverified."""
from __future__ import annotations

import pytest

from jobloop.guards import hosts
from jobloop.guards.hosts import ATS_APEXES, HostVerdict, classify

PORTALS = ("jobindex.dk", "www.jobbank.dk")


def kind(url, installed=PORTALS):
    return classify(url, installed).kind


def test_host_classified_into_exactly_one_tier():
    assert kind("https://www.jobindex.dk/jobannonce/1") == "portal"
    assert kind("https://boards.greenhouse.io/acme/jobs/1") == "ats"
    assert kind("https://acme.example/careers/1") == "unverified"
    for url in ("https://jobindex.dk/", "https://greenhouse.io/", "https://x.example/"):
        assert classify(url, PORTALS).kind in hosts.KINDS


def test_portal_host_set_comes_from_the_installed_hosts_passed_in():
    assert kind("https://newboard.example/job/1", ("newboard.example",)) == "portal"
    assert kind("https://newboard.example/job/1", ()) == "unverified"
    assert kind("https://newboard.example/job/1", ("otherboard.example",)) == "unverified"


def test_portal_host_matches_apex_www_and_subdomains():
    for url in ("https://jobindex.dk/x", "https://www.jobindex.dk/x", "https://de.jobindex.dk/x", "https://JobIndex.DK/x"):
        assert classify(url, PORTALS) == HostVerdict("portal", url.split("/")[2].lower(), "jobindex.dk", classify(url, PORTALS).reason)
    assert classify("https://www.jobbank.dk/x", PORTALS).apex_or_none == "jobbank.dk"


def test_a_portal_given_with_www_or_capitals_is_normalised_and_a_bad_one_refused():
    assert kind("https://jobbank.dk/x", ("WWW.JobBank.dk",)) == "portal"
    for bad in ("", "localhost", "https://jobindex.dk", "job index.dk", "xn--jobindex.dk", "1.2.3.4"):
        with pytest.raises(ValueError, match="installed portal host"):
            classify("https://jobindex.dk/", (bad,))


def test_the_six_vendors_are_seven_literals_and_both_workday_apexes_count():
    assert ATS_APEXES == ("greenhouse.io", "lever.co", "myworkdayjobs.com", "workday.com", "ashbyhq.com",
                          "smartrecruiters.com", "workable.com")
    assert len(ATS_APEXES) == 7
    vendors = {"workday" if "workday" in apex else apex.split(".")[0] for apex in ATS_APEXES}
    assert len(vendors) == 6
    for apex in ("myworkdayjobs.com", "workday.com"):
        assert kind(f"https://acme.{apex}/job") == "ats"


@pytest.mark.parametrize("apex", ATS_APEXES)
def test_each_ats_apex_and_its_subdomains_classify_official(apex):
    for host in (apex, f"jobs.{apex}", f"a.b.{apex}"):
        verdict = classify(f"https://{host}/x", ())
        assert (verdict.kind, verdict.host, verdict.apex_or_none) == ("ats", host, apex)


def test_ats_match_is_exact_or_dot_suffix():
    assert kind("https://notgreenhouse.io/x") == "unverified"
    assert kind("https://greenhouse.io/x") == "ats"
    assert kind("https://x.greenhouse.io/x") == "ats"


def test_prefix_lookalike_unverified():
    for url in ("https://evil-greenhouse.io/j", "https://greenhouse.io.evil/j", "https://greenhouseio.com/j",
                "https://lever.com/j", "https://xlever.co/j"):
        assert kind(url) == "unverified", url


def test_suffix_spoof_unverified():
    for url in ("https://job-boards.greenhouse.io.evil.com/j", "https://myworkdayjobs.com.phishing.net/login",
                "https://lever.co.attacker.org/apply", "https://jobindex.dk.evil.com/x"):
        assert kind(url) == "unverified", url


def test_userinfo_trick_classifies_real_host_and_fails_closed():
    verdict = classify("https://greenhouse.io@evil.com/", ())
    assert (verdict.kind, verdict.host) == ("unverified", "evil.com")
    assert kind("https://boards.greenhouse.io@evil-domain.com/job/1") == "unverified"
    assert kind("https://jobindex.dk:pw@evil.com/") == "unverified"
    assert kind("https://evil.com@boards.greenhouse.io/") == "unverified"


def test_unknown_domain_unverified():
    verdict = classify("https://unknown-board.example.com/posting/123", PORTALS)
    assert (verdict.kind, verdict.host, verdict.apex_or_none) == ("unverified", "unknown-board.example.com", None)
    assert "not an installed portal" in verdict.reason


def test_trailing_dot_uppercase_and_ports():
    assert kind("https://boards.greenhouse.io./x") == "unverified"
    assert kind("https://BOARDS.GreenHouse.IO/x") == "ats"
    assert classify("HTTPS://Boards.Greenhouse.io/x", ()).host == "boards.greenhouse.io"
    assert kind("https://boards.greenhouse.io:443/x") == "ats"
    assert kind("http://boards.greenhouse.io:80/x") == "ats"
    for url in ("https://boards.greenhouse.io:8443/x", "https://boards.greenhouse.io:80/x", "https://boards.greenhouse.io:/x",
                "https://boards.greenhouse.io:abc/x"):
        assert kind(url) == "unverified", url


def test_idn_punycode_and_non_ascii_lookalikes_unverified():
    for url in ("https://xn--greenhouse-8ya.io/x", "https://boards.xn--reenhouse-c3a.io/x",
                "https://grеenhouse.io/x", "https://greenhouse。io/x", "https://ｇreenhouse.io/x",
                "https://xn--jobindex.dk/x"):
        assert kind(url) == "unverified", url


def test_non_http_schemes_and_missing_host_unverified():
    for url in ("ftp://boards.greenhouse.io/x", "javascript://boards.greenhouse.io/%0aalert(1)", "file:///etc/passwd",
                "//boards.greenhouse.io/x", "boards.greenhouse.io/x", "https:boards.greenhouse.io", "https:///x",
                "https://", "", "mailto:a@greenhouse.io", "data:text/html,greenhouse.io"):
        verdict = classify(url, PORTALS)
        assert verdict.kind == "unverified" and verdict.apex_or_none is None, url


def test_malformed_url_unverified_no_exception():
    for url in ("not a url", "http://[::1", "https://[::1]/x", "https://127.0.0.1/x", "https://1.2.3.4.5/x", None, 5):
        assert classify(url, PORTALS).kind == "unverified"


def test_whitespace_control_characters_and_backslashes_unverified():
    for url in (" https://boards.greenhouse.io/x", "https://boards.greenhouse.io/x ", "https://boards.green house.io/x",
                "https://boards.greenhouse.io\t/x", "https://boards.greenhouse.io\n.evil.com/", "https://boards.greenhouse.io\x00.evil.com/",
                "https://evil.com\\@boards.greenhouse.io/", "https://evil.com\\.greenhouse.io/", "https://boards.greenhouse.io/a\\b",
                "https://boards.greenhouse.io .evil.com/", "https://boards.greenhouse.io /x"):
        assert kind(url) == "unverified", repr(url)


def test_path_query_and_fragment_cannot_change_the_host():
    assert kind("https://evil.com/boards.greenhouse.io") == "unverified"
    assert kind("https://evil.com?x=@boards.greenhouse.io/") == "unverified"
    assert kind("https://evil.com#@boards.greenhouse.io") == "unverified"
    assert kind("https://boards.greenhouse.io/café/ø") == "ats"


def test_bare_ats_apex_has_no_portal_tier():
    verdict = classify("https://greenhouse.io/corp/123", PORTALS)
    assert (verdict.kind, verdict.apex_or_none) == ("ats", "greenhouse.io")


def test_a_host_that_is_both_prefers_the_installed_portal():
    assert classify("https://boards.greenhouse.io/x", ("greenhouse.io",)).kind == "portal"


def test_a_verdict_cannot_be_built_inconsistent():
    with pytest.raises(ValueError):
        HostVerdict("ats", "x.example", None, "r")
    with pytest.raises(ValueError):
        HostVerdict("unverified", "x.example", "x.example", "r")
    with pytest.raises(ValueError):
        HostVerdict("trusted", "x.example", "x.example", "r")


# ── trust: the employer's own site, trusted_hosts, an approval; never a look-alike ──
def trust(url, company="DPG Media", domains=None, trusted=(), approved=False):
    return hosts.trust(url, company, domains or {}, trusted, approved)


@pytest.mark.parametrize("url", ["https://vacatures.dpgmedia.nl/v/1", "https://www.dpgmedia.nl/", "https://careers.dpgmedia.nl/1",
                                 "https://jobs.dpgmedia.nl/1", "https://karriere.dpgmedia.nl/1", "https://a.b.dpgmedia.nl/1"])
def test_the_employers_own_site_is_the_employer(url):
    assert trust(url)[0] == "employer"


@pytest.mark.parametrize("url", ["https://dpgmedia-jobs.evil.io/1", "https://dpgmedia.evil.io/1", "https://evil-dpgmedia.nl/1",
                                 "https://evil.io/dpgmedia", "https://it.whatjobs.com/1", "https://startup.jobs/1",
                                 "https://u@dpgmedia.nl/1", "http://dpgmedia.nl:8080/1", "https://127.0.0.1/dpgmedia", "ftp://dpgmedia.nl/1", ""])
def test_look_alikes_and_aggregators_are_not_trusted(url):
    assert trust(url)[0] == ""


def test_a_company_name_that_is_too_short_never_matches():
    assert trust("https://ab.example/1", "AB Corp")[0] == "" and trust("https://x.example/1", "")[0] == ""


def test_a_configured_company_domain_is_the_employers_on_a_label_boundary_and_only_for_that_company():
    domains = {"Northwind Analytics": "nw-group.example"}
    assert trust("https://apply.nw-group.example/1", "northwind analytics", domains)[0] == "employer"
    assert trust("https://nw-group.example/1", "Northwind Analytics", domains)[0] == "employer"
    for url in ("https://evil-nw-group.example/1", "https://nw-group.example.evil.io/1"):
        assert trust(url, "Northwind Analytics", domains)[0] == ""
    assert trust("https://apply.nw-group.example/1", "Other Corp", domains)[0] == ""


def test_trusted_hosts_match_the_host_or_a_subdomain_on_a_label_boundary():
    t = ("whatjobs.com",)
    assert trust("https://whatjobs.com/1", trusted=t)[0] == "trusted" and trust("https://NL.WhatJobs.com/1", trusted=t)[0] == "trusted"
    for url in ("https://evil-whatjobs.com/1", "https://whatjobs.com.evil.io/1", "https://notwhatjobs.com/1", "https://jobs.com/1"):
        assert trust(url, trusted=t)[0] == ""


def test_an_approval_is_its_own_kind_and_a_missing_one_is_not_trust():
    assert trust("https://it.whatjobs.com/1", approved=True)[0] == "approved" and trust("https://it.whatjobs.com/1", approved=False)[0] == ""
    kind_, why = trust("https://it.whatjobs.com/1")
    assert kind_ == "" and why


@pytest.mark.parametrize("entry", ["http://whatjobs.com", "whatjobs.com/jobs", "whatjobs.com:443", "10.0.0.1", "[::1]", "com", "co.uk", "*.whatjobs.com",
                                   "", "  ", 5, None, "what jobs.com", "user@whatjobs.com"])
def test_a_trusted_hosts_entry_that_is_not_a_plain_host_is_refused(entry):
    assert hosts.trusted_entry_problem(entry)


def test_plain_trusted_hosts_entries_are_accepted():
    assert not any(hosts.trusted_entry_problem(e) for e in ("whatjobs.com", "Startup.Jobs", "jobs.example.co.uk"))

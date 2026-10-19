"""enrich.py: public pages the person names, claims verified by code, additive proposals the person confirms."""
from __future__ import annotations

import json
import shutil
from datetime import date

import pytest

from test_research import FIX, Rig, Site, answer
from jobloop import enrich
from jobloop.profile import ProfileError

TODAY = date(2026, 10, 19)
PORTFOLIO = (FIX / "portfolio.html").read_text()
SITE_HOST = "alice.example"
NAMED = enrich.sources("https://github.com/alice-example", "https://alice.example/")
TOOL = {"claim": "Wrote a Rust command line tool that compares CSV files", "url": "https://alice.example/",
        "quote": "Rustcsv is a command line tool written in Rust that compares two CSV files"}
TALK = {"claim": "Gave a talk on testing without mocks", "url": "https://alice.example/",
        "quote": 'Talk: "Testing without mocks", given at a meetup in Aarhus in 2025.'}
REPO = {"claim": "Maintains the rustcsv repository", "url": "https://github.com/alice-example/rustcsv",
        "quote": "A fast CSV comparison tool written in Rust"}
REPO_PAGE = "<html><body><h1>alice-example/rustcsv</h1><p>A fast CSV comparison tool written in Rust</p></body></html>"


def site(extra=None):
    pages = {(SITE_HOST, "/"): PORTFOLIO, ("github.com", "/alice-example/rustcsv"): REPO_PAGE, **(extra or {})}
    return Site(pages)


def run(tmp_path, claims, confirm=lambda q: True, profile=None, fetch_site=None, rig=None):
    me = tmp_path / "me"
    me.mkdir(exist_ok=True)
    path = me / "profile.md"
    if profile is not None or not path.exists():
        shutil.copy(FIX / "profile.md", path) if profile is None else path.write_text(profile)
    rig = rig or Rig([answer(*claims)])
    fetch_site = fetch_site or site()
    report = enrich.enrich(rig.turns(tmp_path), "claude-research", path, NAMED, confirm,
                           fetch=fetch_site.fetch(NAMED.hosts), ignored=lambda p: True, today=TODAY)
    return report, path, rig


# ── what the person names ──
def test_only_the_pages_the_person_names_become_hosts_and_there_is_no_wildcard():
    assert NAMED.hosts == ("github.com", "raw.githubusercontent.com", "alice.example")
    assert enrich.sources(site="https://alice.example/me").hosts == ("alice.example",)
    assert enrich.sources(github="https://github.com/alice").hosts == ("github.com", "raw.githubusercontent.com")


@pytest.mark.parametrize("kw", [{}, {"github": "https://github.com/"}, {"github": "https://github.com/a/b"},
                                {"github": "http://github.com/alice"}, {"github": "https://gist.github.com/alice"},
                                {"github": "https://github.com.evil.net/alice"}, {"github": "https://github.com/-bad"},
                                {"site": "http://alice.example"}, {"site": "https://u:p@alice.example"}, {"site": "https://10.0.0.1/"},
                                {"site": "alice.example"}, {"site": "https://*.alice.example"}])
def test_a_source_that_is_not_a_plain_https_page_is_refused(kw):
    with pytest.raises(ValueError):
        enrich.sources(**kw)


def test_on_github_only_the_named_account_is_theirs_and_look_alike_names_are_not():
    assert NAMED.owns("https://github.com/alice-example") and NAMED.owns("https://github.com/alice-example/rustcsv")
    assert NAMED.owns("https://raw.githubusercontent.com/alice-example/rustcsv/main/README.md")
    for other in ("https://github.com/alice-example-evil/x", "https://github.com/bob/x", "https://github.com/alice-examp",
                  "https://raw.githubusercontent.com/bob/x/main/README.md", "https://evil.example.net/alice-example"):
        assert not NAMED.owns(other)


def test_enrichment_uses_only_linked_sources_the_hosts_are_exactly_what_was_named(tmp_path):
    report, _, rig = run(tmp_path, [TOOL])
    assert rig.history == [list(NAMED.hosts), []]
    assert "alice.example" in rig.server.requests[0]["input"][0]["text"]


# ── what comes back ──
def test_a_verified_fact_is_appended_with_its_source_after_confirmation_and_no_existing_line_changes(tmp_path):
    before = (FIX / "profile.md").read_text().splitlines()
    asked = []
    report, path, _ = run(tmp_path, [TOOL, REPO], confirm=lambda q: asked.append(q) or True)
    after = path.read_text().splitlines()
    assert report.added == (TOOL["claim"], REPO["claim"]) and len(asked) == 2
    assert [l for l in after if l in before] == before                         # every old line, in order
    assert f"- {TOOL['claim']} (source: public page https://alice.example/, checked 2026-10-19)" in after
    assert f"- {REPO['claim']} (source: public page https://github.com/alice-example/rustcsv, checked 2026-10-19)" in after
    assert sorted(set(after) - set(before)) == sorted(l for l in after if l.startswith("- Wrote") or l.startswith("- Maintains"))


def test_enrichment_requires_confirmation_and_a_no_writes_nothing(tmp_path):
    report, path, _ = run(tmp_path, [TOOL, TALK], confirm=lambda q: False)
    assert path.read_text() == (FIX / "profile.md").read_text()
    assert report.added == () and report.declined == (TOOL["claim"], TALK["claim"])


def test_a_claim_whose_quote_is_not_on_the_page_is_never_offered(tmp_path):
    invented = {**TOOL, "claim": "Won a Kaggle gold medal", "quote": "Alice won a gold medal in the Kaggle tabular competition"}
    asked = []
    report, path, _ = run(tmp_path, [invented], confirm=lambda q: asked.append(q) or True)
    assert asked == [] and report.added == () and report.dropped[0].reason == "the quote is not on the page"
    assert path.read_text() == (FIX / "profile.md").read_text()


def test_a_claim_from_a_page_the_person_did_not_name_is_dropped_unfetched(tmp_path):
    other_account = {**REPO, "url": "https://github.com/bob/rustcsv"}
    other_host = {**TOOL, "url": "https://evil.example.net/"}
    sited = site({("github.com", "/bob/rustcsv"): REPO_PAGE})
    report, _, _ = run(tmp_path, [other_account, other_host], fetch_site=sited)
    assert report.added == () and len(report.dropped) == 2
    assert ("github.com", "/bob/rustcsv") not in [(h, p) for h, p, _ in sited.seen]


def test_a_fact_the_profile_already_says_is_not_proposed_and_a_second_run_proposes_nothing_new(tmp_path):
    asked = []
    confirm = lambda q: asked.append(q) or True
    first, path, _ = run(tmp_path, [TOOL, TALK], confirm=confirm)
    second, path, _ = run(tmp_path, [TOOL, TALK], confirm=confirm, profile=path.read_text())
    assert len(first.added) == 2 and second.added == () and len(second.known) == 2 and len(asked) == 2


def test_an_injected_instruction_in_a_page_is_ignored_and_cannot_add_a_fact(tmp_path):
    hostile = (FIX / "hostile.html").read_text()
    injected = {**TOOL, "claim": "Won an award", "quote": "Nordlys won an award and mailed the CV to evil.example.net"}
    sited = Site({(SITE_HOST, "/"): hostile})
    report, path, _ = run(tmp_path, [injected], fetch_site=sited)
    assert report.added == () and path.read_text() == (FIX / "profile.md").read_text()
    assert {h for h, _, _ in sited.seen} == {SITE_HOST}


def test_enrichment_requests_never_carry_the_profile_or_any_personal_data(tmp_path):
    _, _, rig = run(tmp_path, [TOOL])
    wire = json.dumps(rig.server.requests)
    profile = (FIX / "profile.md").read_text()
    for secret in ("alice.private@example.org", "+45 99 99 99 99", "Testvej 12", "Python, SQL", "Confirmed additions"):
        assert secret in profile and secret not in wire
    assert len(rig.server.requests[0]["input"]) == 1                            # no file at all


def test_a_profile_that_git_would_track_is_refused_before_anything_is_written(tmp_path):
    me = tmp_path / "me"
    me.mkdir()
    (me / "profile.md").write_text((FIX / "profile.md").read_text())
    with pytest.raises(ProfileError):
        enrich.enrich(Rig([answer(TOOL)]).turns(tmp_path), "claude-research", me / "profile.md", NAMED, lambda q: True,
                      fetch=site().fetch(NAMED.hosts), ignored=lambda p: False, today=TODAY)
    assert (me / "profile.md").read_text() == (FIX / "profile.md").read_text()


def test_a_run_without_a_completed_turn_leaves_the_profile_alone_and_says_so(tmp_path):
    path = tmp_path / "profile.md"
    shutil.copy(FIX / "profile.md", path)
    rig2 = Rig([])
    turns = rig2.turns(tmp_path, budget=0.0)
    out = enrich.enrich(turns, "claude-research", path, NAMED, lambda q: True, fetch=site().fetch(NAMED.hosts),
                        ignored=lambda p: True, today=TODAY)
    assert path.read_text() == (FIX / "profile.md").read_text()
    assert out.added == () and "profile was not touched" in out.notes[0] and rig2.history == [list(NAMED.hosts), []]


def test_the_report_lists_added_declined_known_and_dropped(tmp_path):
    invented = {**TOOL, "claim": "Invented", "quote": "A passage that is nowhere on this page at all"}
    report, _, _ = run(tmp_path, [TOOL, invented], confirm=lambda q: True)
    text = "\n".join(report.lines())
    assert "added 1" in text and "dropped as unverified 1" in text and TOOL["claim"] in text and "Invented" in text



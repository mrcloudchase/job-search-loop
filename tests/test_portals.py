"""portals.py: portal output is untrusted data, and portal commands are plain code with no shell."""
from __future__ import annotations

import sys

import pytest

from jobloop import portals

JOB = {"id": "h1", "title": "Data Engineer", "company": "Acme", "location": "Aarhus", "date": "2026-10-16",
       "deadline": None, "url": "https://example.com/j/1", "description": None}


def fake_portal(tmp_path, output, extra=""):
    script = tmp_path / "portal.py"
    script.write_text(f"import json,sys\n{extra}\nprint(json.dumps({output!r}) if not isinstance({output!r}, str) else {output!r})\n")
    return {"name": "fake", "enabled": True, "search": [sys.executable, str(script), "{query}", "{limit}"], "detail": [sys.executable, str(script)]}


def test_results_become_jobs_with_a_stable_key():
    job = portals.normalize(JOB, "jobindex")
    assert (job.title, job.company, job.deadline, job.description) == ("Data Engineer", "Acme", "", "")
    assert job.key == "acme_data-engineer" and job == portals.normalize(dict(JOB), "jobindex")


@pytest.mark.parametrize("bad", ["a string", None, 5, [], {"title": "", "url": "https://x.io"}, {"title": "T"},
                                 {"title": "T", "url": "javascript:alert(1)"}, {"title": "T", "url": "file:///etc/passwd"},
                                 {"title": "T", "url": "ftp://x.io/j"}])
def test_anything_that_is_not_a_posting_with_an_http_url_is_dropped(bad):
    assert portals.normalize(bad, "p") is None


def test_fields_are_coerced_to_short_single_line_text():
    job = portals.normalize({**JOB, "title": "A\n\n" + "x" * 1000, "company": ["not", "text"]}, "p")
    assert "\n" not in job.title and len(job.title) <= 200 and job.company.startswith("['not'")


def test_a_search_runs_the_command_with_the_query_as_one_argument(tmp_path):
    portal = fake_portal(tmp_path, {"results": [JOB, {"title": "no url"}]})
    jobs = portals.search(portal, "data engineer; rm -rf / $(whoami)", 5)
    assert [j.title for j in jobs] == ["Data Engineer"]


def test_placeholders_are_filled_per_argument_and_never_parsed(tmp_path):
    argv = portals.command(["tool", "--query", "{query}", "--limit", "{limit}", "--keep", "{unknown}"], query="a b; $(x) {limit}", limit="5")
    assert argv == ["tool", "--query", "a b; $(x) {limit}", "--limit", "5", "--keep", "{unknown}"]


@pytest.mark.parametrize("query", ["", "   ", "-h", "--help", "x" * 201])
def test_a_query_that_could_be_a_flag_or_is_empty_or_huge_is_refused(query):
    with pytest.raises(SystemExit):
        portals.check_query(query)


def test_a_failing_portal_raises_with_its_own_message(tmp_path):
    portal = fake_portal(tmp_path, "x", extra="sys.stderr.write('rate limited'); sys.exit(3)")
    with pytest.raises(RuntimeError, match="rate limited"):
        portals.search(portal, "q", 5)


def test_portal_commands_never_see_your_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("MY_TOKEN", "tok")
    monkeypatch.setenv("JOBLOOP_VISIBLE", "yes")
    out = portals.run([sys.executable, "-c", "import os; print(sorted(k for k in os.environ if k in ('OPENAI_API_KEY','MY_TOKEN','JOBLOOP_VISIBLE')))"])
    assert "JOBLOOP_VISIBLE" in out and "OPENAI" not in out and "TOKEN" not in out


def test_output_that_is_not_a_list_of_results_gives_no_jobs(tmp_path):
    assert portals.search(fake_portal(tmp_path, {"results": "oops"}), "q", 5) == []
    assert portals.search(fake_portal(tmp_path, [JOB]), "q", 5)[0].title == "Data Engineer"


def test_a_portal_that_needs_a_location_says_so(tmp_path):
    portal = {**fake_portal(tmp_path, []), "search": ["x", "{query}", "{location}"]}
    with pytest.raises(RuntimeError, match="--location"):
        portals.search(portal, "q", 5)


def test_import_finds_portal_clis_and_leaves_every_one_disabled(tmp_path):
    for name, extra in (("jobindex-search", ""), ("linkedin-search", "NO_LOCATION")):
        src = tmp_path / name / "cli" / "src"
        src.mkdir(parents=True)
        (src / "cli.ts").write_text(extra)
    found = portals.discover(tmp_path)
    assert [p["name"] for p in found] == ["jobindex", "linkedin"] and not any(p["enabled"] for p in found)
    assert "{location}" in found[1]["search"] and "{location}" not in found[0]["search"]

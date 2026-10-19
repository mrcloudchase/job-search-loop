"""addboard.py: a harness builds a board's adapter, the loop verifies it offline, and only a verified board is registered, OFF."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from fake_server import FakeServer
from jobloop import addboard as ab, search
from jobloop.boards import external
from jobloop.turns import Api, Turns

GOOD = Path(__file__).parent / "fixtures" / "addboard" / "good"
HOSTS = ("jobs.nordlys.example",)
URL = "https://jobs.nordlys.example/"
ADAPTER = (GOOD / "adapter.py").read_text()
SAMPLES = {p.name: p.read_text() for p in (GOOD / "samples").iterdir()}
LIVE = (GOOD / "live_test.json").read_text()
BOARD = (GOOD / "board.json").read_text()
REQ = ab.Request("nordlys-jobs", URL, HOSTS, "engineer")
FAIL = {"rc": 1, "stdout": "", "timed_out": False}


def draft(adapter=ADAPTER, board=BOARD, live=LIVE, samples=None, extra=None) -> ab.Draft:
    files = {"adapter.py": adapter, "board.json": board, "live_test.json": live,
             **{f"samples/{n}": t for n, t in (SAMPLES if samples is None else samples).items()}, **(extra or {})}
    return ab.Draft({k: v for k, v in files.items() if v is not None})


def errors(**kinds) -> dict:
    """The container's outputs for every case that must fail, as a correct adapter gives them."""
    named = {"bad flag": "invalid", "limit 0": "invalid", "limit text": "invalid", "recency 0": "invalid", "unknown command": "invalid",
             "parse missing file": "not_found", "search without network": "network", "detail without network": "network", **kinds}
    return {case: {**FAIL, "stderr": f"nordlys-jobs: {kind}: it failed\n"} for case, kind in named.items()}


def parsed(samples=None) -> dict:
    """What `parse` prints for each sample: the real fixture adapter, offline (parse never touches the network)."""
    out = {}
    for name in samples or SAMPLES:
        done = subprocess.run([sys.executable, "-I", str(GOOD / "adapter.py"), "parse", str(GOOD / "samples" / name)], capture_output=True, text=True)
        out[f"parse {name}"] = {"rc": done.returncode, "stdout": done.stdout, "stderr": done.stderr, "timed_out": False}
    return out


def good_runner(d, query):
    return {**parsed(d.samples), **errors()}


# ── what the person gives ──
@pytest.mark.parametrize("name", ["Nordlys", "a", "nordlys_jobs", "-x", "x-", "x" * 41, "../evil", "jobindex", "freehire", "a b", ""])
def test_board_name_format_and_no_collision_with_built_in_boards(name):
    with pytest.raises(ValueError):
        ab.Request(name, URL, HOSTS, "q")


def test_a_name_already_used_by_a_folder_or_a_registered_board_is_refused_before_anything_runs(tmp_path):
    (tmp_path / "boards" / "taken-one").mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        ab.request_for(tmp_path, "taken-one", URL, HOSTS, "q")
    external.register(tmp_path, "registered-one", "0" * 64, "2026-10-19")
    with pytest.raises(ValueError, match="already exists"):
        ab.request_for(tmp_path, "registered-one", URL, HOSTS, "q")
    assert ab.request_for(tmp_path, "fresh-one", URL, ["jobs.nordlys.example"], " engineer ").query == "engineer"


@pytest.mark.parametrize("host", ["*.nordlys.example", "https://jobs.nordlys.example", "jobs.nordlys.example:8443", "jobs.nordlys.example/x",
                                  "10.0.0.1", "u@jobs.nordlys.example", "localhost", "Jobs.Nordlys.Example", "xn--bcher-kva.example", "a b.example", ""])
def test_a_host_that_is_not_an_exact_plain_name_is_refused(host):
    with pytest.raises(ValueError):
        ab.Request("nordlys-jobs", URL, (host,), "q")


@pytest.mark.parametrize("url", ["http://jobs.nordlys.example/", "https://evil.example/", "https://u:p@jobs.nordlys.example/", "https://jobs.nordlys.example:8443/",
                                 "https://jobs.nordlys.example.evil.net/", "ftp://jobs.nordlys.example/", "jobs.nordlys.example", "https://jobs.nordlys.example/a b"])
def test_the_url_must_be_https_on_one_of_the_declared_hosts(url):
    with pytest.raises(ValueError):
        ab.Request("nordlys-jobs", url, HOSTS, "q")


def test_hosts_are_limited_distinct_and_at_least_one():
    for hosts in ((), HOSTS * 2, tuple(f"h{i}.nordlys.example" for i in range(6))):
        with pytest.raises(ValueError):
            ab.Request("nordlys-jobs", URL, hosts, "q")


@pytest.mark.parametrize("query", ["", "  ", "-rf", "x" * 101])
def test_the_test_query_must_be_a_plain_search(query):
    with pytest.raises(ValueError):
        ab.Request("nordlys-jobs", URL, HOSTS, query)


def test_a_url_argument_is_carried_into_the_task_the_harness_gets(tmp_path):
    server = FakeServer([{"files": {"REFUSED.txt": "no"}}])
    rig = Rig(server)
    add(tmp_path, rig, runner=ab.run_in_container)
    task = json.loads(server.requests[0]["input"][1]["file_data"])
    assert task == {"name": "nordlys-jobs", "url": URL, "hosts": list(HOSTS), "test_query": "engineer"}
    assert server.requests[0]["model"] == "claude-portal" and URL in server.requests[0]["input"][0]["text"]


# ── the source check ──
def test_the_good_adapter_passes_the_source_check():
    assert ab.check_source(ADAPTER, HOSTS) == []


def replace(old, new):
    assert old in ADAPTER
    return ADAPTER.replace(old, new)


@pytest.mark.parametrize("line", ["import subprocess", "import os", "import socket", "import urllib.request", "from os import path", "import importlib",
                                  "from urllib import request", "import ctypes", "import pickle", "import tempfile", "import shutil",
                                  "from .x import y", "from re import *", "import requests"])
def test_an_adapter_that_imports_subprocess_os_sockets_or_anything_outside_the_list_is_rejected(line):
    problems = ab.check_source(line + "\n" + ADAPTER, HOSTS)
    assert problems and any("not allowed" in p for p in problems)


@pytest.mark.parametrize("code", ["eval('1')", "exec('x=1')", "compile('1', 'x', 'eval')", "__import__('os')", "getattr(sys, 'modules')",
                                  "sys.modules['os'].system('id')", "().__class__.__bases__", "open.__self__", "vars()", "globals()"])
def test_dynamic_code_and_reflection_are_rejected(code):
    assert ab.check_source(ADAPTER + f"\nx = {code}\n", HOSTS)


def test_re_compile_is_fine_but_a_bare_compile_is_not():
    assert ab.check_source(ADAPTER + "\nPATTERN = re.compile('a')\n", HOSTS) == []
    assert ab.check_source(ADAPTER + "\ncompile('1', 'x', 'eval')\n", HOSTS)


def test_pathlib_is_not_an_allowed_import_so_writes_have_no_second_route():
    assert any("pathlib" in p for p in ab.check_source("from pathlib import Path\n" + ADAPTER, HOSTS))


@pytest.mark.parametrize("code", ["open('/etc/x', 'w')", "open('out.txt', 'a')", "open('x', 'r+')", "open('x', mode='wb')", "open('x', MODE)",
                                  "handle.write('x')", "sys.stdin.write('x')"])
def test_a_file_opened_for_writing_or_written_to_is_rejected_but_stdout_and_stderr_are_not(code):
    assert ab.check_source(ADAPTER + f"\n{code}\n", HOSTS)
    assert ab.check_source(ADAPTER + "\nsys.stdout.write('x')\nsys.stderr.write('y')\nopen('x')\nopen('x', 'rb')\n", HOSTS) == []


def test_an_adapter_naming_another_host_is_rejected():
    problems = ab.check_source(replace('BASE = "https://jobs.nordlys.example"', 'BASE = "https://evil.example.net"'), HOSTS)
    assert any("evil.example.net" in p and "declared host" in p for p in problems)
    assert any("not https" in p or "declared host" in p for p in ab.check_source(ADAPTER + '\nOTHER = "http://jobs.nordlys.example/x"\n', HOSTS))
    assert ab.check_source(ADAPTER + '\nOTHER = "https://jobs.nordlys.example.evil.net/x"\n', HOSTS)
    assert ab.check_source(ADAPTER + '\nOTHER = "https://u:p@jobs.nordlys.example/x"\n', HOSTS)


def test_a_paid_fetch_service_or_any_third_party_host_cannot_be_reached_by_an_adapter():
    assert ab.check_source(ADAPTER + '\nPROXY = "https://api.scrapingbee.example/?url="\n', HOSTS)
    assert ab.check_source(ADAPTER + '\nclient = httpx.Client(proxy="http://x")\n', HOSTS)


@pytest.mark.parametrize("code", ['URL = "https://" + host + "/x"', 'URL = f"https://{host}/x"', 'SCHEME = "https"', 'S = "https://"', 'u = urljoin(BASE, link)',
                                  'client = httpx.Client(follow_redirects=True)', 'client = httpx.Client(verify=False)',
                                  'client = httpx.Client(cookies={"a": "b"})', 'client = httpx.Client(auth=("a", "b"))'])
def test_urls_are_written_whole_and_redirects_proxies_cookies_and_auth_are_refused(code):
    assert ab.check_source(ADAPTER + f"\nhost = 'x'\n{code}\n", HOSTS)


def test_a_url_with_the_host_spelled_out_and_a_formatted_path_is_fine():
    assert ab.check_source(ADAPTER + '\nx = f"https://jobs.nordlys.example/jobs/{1}"\n', HOSTS) == []


def test_credentials_and_secrets_in_an_adapter_are_rejected():
    for code in ('H = {"Authorization": "x"}', 'H = {"Cookie": "a=b"}', 'H = {"X-Api-Key": "k"}', 'API_KEY = "abcdefghijk"',
                 'T = "Bearer abcdefghijklmnop"', 'K = "sk-abcdefghijklmnopqrstuvwx"'):
        assert ab.check_source(ADAPTER + f"\n{code}\n", HOSTS), code


@pytest.mark.parametrize("agent", ["Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36", "Googlebot/2.1"])
def test_a_browser_or_unnamed_user_agent_is_rejected(agent):
    problems = ab.check_source(replace('"job-search-loop/0.1 (personal-use job search)"', json.dumps(agent)), HOSTS)
    assert any("User-Agent" in p for p in problems)


def test_an_adapter_without_a_user_agent_header_is_rejected():
    assert any("User-Agent" in p for p in ab.check_source(replace('headers={"User-Agent": USER_AGENT}, ', ""), HOSTS))


def test_an_adapter_without_backoff_or_a_robots_check_or_the_standard_flags_is_rejected():
    assert any("backoff" in p for p in ab.check_source(replace("time.sleep(2 ** attempt)", "pass"), HOSTS))
    assert any("backoff" in p for p in ab.check_source(ADAPTER.replace("429", "430"), HOSTS))
    assert any("robots" in p for p in ab.check_source(replace('"/robots.txt"', '"/index"'), HOSTS))
    for flag in ("--query", "--location", "--recency-days", "--limit", "parse", "detail"):
        assert any(flag in p for p in ab.check_source(ADAPTER.replace(f'"{flag}"', '"zzz"'), HOSTS)), flag


def test_source_that_is_not_python_or_is_huge_is_rejected():
    assert "not valid Python" in ab.check_source("def (:", HOSTS)[0]
    assert "over" in ab.check_source("x = 1\n" * 100_000, HOSTS)[0]


# ── the output check ──
def printed(rows, **meta) -> str:
    return json.dumps({"meta": {"board": "x", "count": len(rows), **meta}, "results": rows})


def row(**over) -> dict:
    return {"id": "1", "title": "Embedded Software Engineer", "company": None, "location": None, "date": None, "deadline": None,
            "url": "https://jobs.nordlys.example/jobs/1", "description": None, **over}


SAMPLE = SAMPLES["search-engineer.json"]


def test_a_posting_that_is_not_in_the_sample_is_rejected_and_named():
    problems, good = ab.check_postings("parse a", printed([row(title="Chief Wizard of Made Up Things")]), SAMPLE, HOSTS)
    assert good == 0 and "not in the saved sample" in problems[0] and "Chief Wizard" in problems[0]


def test_titles_are_found_literally_in_json_html_entities_and_split_markup():
    assert ab.check_postings("p", printed([row(title="R&D Mechanical Engineer")]), SAMPLE, HOSTS) == ([], 1)
    page = "<li><b>Senior</b>\n  Rust   Developer &amp; Lead</li>"
    assert ab.check_postings("p", printed([row(title="Senior Rust Developer & Lead")]), page, HOSTS) == ([], 1)
    assert ab.check_postings("p", printed([row(title="senior rust developer")]), page, HOSTS)[1] == 0     # case matters


@pytest.mark.parametrize("url", ["https://evil.example/jobs/1", "http://jobs.nordlys.example/jobs/1", "javascript:alert(1)", "https://jobs.nordlys.example.evil.net/1",
                                 "https://u:p@jobs.nordlys.example/1", "/jobs/1", "https://jobs.nordlys.example:8443/1", None])
def test_a_hostile_url_is_never_accepted_in_adapter_output(url):
    problems, good = ab.check_postings("p", printed([row(url=url)]), SAMPLE, HOSTS)
    assert good == 0 and problems


@pytest.mark.parametrize("bad", [{"extra": 1}, {"id": None}, {"title": None}, {"title": 5}, {"description": ["x"]}, {"title": "Embedded &amp; Software Engineer"},
                                 {"description": "<p>tags</p>"}])
def test_generated_board_output_shape(bad):
    broken = {**row(), **bad}
    problems, good = ab.check_postings("p", printed([broken]), SAMPLE, HOSTS)
    assert good == 0 and problems


def test_a_result_with_a_missing_key_is_rejected():
    broken = row()
    del broken["deadline"]
    assert ab.check_postings("p", printed([broken]), SAMPLE, HOSTS)[1] == 0


@pytest.mark.parametrize("text", ["not json", "[]", '{"meta": {}}', '{"meta": 1, "results": []}', '{"meta": {}, "results": {}}'])
def test_output_that_is_not_the_contract_envelope_is_rejected(text):
    assert ab.check_postings("p", text, SAMPLE, HOSTS)[0]


def test_meta_count_must_match():
    assert ab.check_postings("p", printed([row()], count=9), SAMPLE, HOSTS)[0]


def test_a_description_with_tags_or_entities_is_rejected_and_plain_text_is_not():
    assert ab.check_postings("p", printed([row(description="Write firmware.\n\nBring tools.")]), SAMPLE, HOSTS) == ([], 1)
    for dirty in ("<p>Write</p>", "Write &nbsp; firmware", "Write \ufffd"):
        assert ab.check_postings("p", printed([row(description=dirty)]), SAMPLE, HOSTS)[1] == 0


# ── the container's runs, judged ──
def test_the_good_adapters_runs_pass_every_check_and_count_the_postings():
    assert ab.check_cases({**parsed(), **errors()}, draft(), HOSTS) == []
    assert sum(ab.check_postings("p", o["stdout"], SAMPLES[n[6:]], HOSTS)[1] for n, o in parsed().items()) == 5


def test_a_sample_that_parses_to_nothing_or_too_few_postings_is_rejected():
    outputs = {**parsed(), **errors()}
    outputs["parse search-data.json"] = {**outputs["parse search-data.json"], "stdout": printed([])}
    assert any("no postings" in p for p in ab.check_cases(outputs, draft(), HOSTS))
    one = {"search-data.json": SAMPLES["search-data.json"]}
    problems = ab.check_cases({**parsed(one), **errors()}, draft(samples=one), HOSTS)
    assert any("only 1 valid postings" in p for p in problems)


def test_made_up_postings_in_a_parse_run_are_sent_back():
    outputs = {**parsed(), **errors()}
    made_up = printed([row(title="Invented Job", url="https://jobs.nordlys.example/jobs/9")])
    outputs["parse search-data.json"] = {**outputs["parse search-data.json"], "stdout": made_up}
    problems = ab.check_cases(outputs, draft(), HOSTS)
    assert any("Invented Job" in p and "not in the saved sample" in p for p in problems)


def test_a_parse_that_crashes_or_times_out_is_reported_with_its_stderr():
    outputs = {**parsed(), **errors()}
    outputs["parse search-data.json"] = {"rc": 1, "stdout": "", "stderr": "Traceback ... KeyError: 'jobs'", "timed_out": False}
    outputs["parse search-engineer.json"] = {"rc": None, "stdout": "", "stderr": "", "timed_out": True}
    problems = ab.check_cases(outputs, draft(), HOSTS)
    assert any("KeyError" in p for p in problems) and len([p for p in problems if p.startswith("parse samples/")]) == 2


@pytest.mark.parametrize("case,out,expect", [
    ("bad flag", {"rc": 2, "stdout": "", "stderr": "usage: x\nx: error: unrecognized\n", "timed_out": False}, "exit 2"),
    ("bad flag", {"rc": 0, "stdout": printed([]), "stderr": "", "timed_out": False}, "exit 0"),
    ("bad flag", {"rc": 1, "stdout": "", "stderr": "Traceback (most recent call last):\n  ...\nValueError: x\n", "timed_out": False}, "no traceback"),
    ("bad flag", {"rc": 1, "stdout": "", "stderr": "something went wrong\n", "timed_out": False}, "NAME: KIND"),
    ("bad flag", {"rc": 1, "stdout": "", "stderr": "x: network: no\n", "timed_out": False}, "kind invalid"),
    ("limit 0", {"rc": 1, "stdout": "", "stderr": "x: parse: no\n", "timed_out": False}, "kind invalid"),
    ("search without network", {"rc": 1, "stdout": "", "stderr": "x: invalid: no\n", "timed_out": False}, "network or robots"),
    ("search without network", {"rc": 0, "stdout": printed([row()]), "stderr": "", "timed_out": False}, "exit 0"),
    ("detail without network", {"rc": 1, "stdout": printed([row()]), "stderr": "x: network: no\n", "timed_out": False}, "nothing on stdout"),
    ("detail without network", {"rc": None, "stdout": "", "stderr": "", "timed_out": True}, "did not finish"),
])
def test_generated_board_error_shape(case, out, expect):
    problems = ab.check_cases({**parsed(), **errors(), case: out}, draft(), HOSTS)
    assert any(case in p and expect in p for p in problems), problems


def test_the_offline_cases_cover_every_sample_bad_flags_and_a_search_with_no_network():
    cases = {c["name"]: c["argv"] for c in ab.cases_for(["a.json", "b.html"], "engineer")}
    assert cases["parse a.json"] == ["parse", "/in/samples/a.json"] and "parse b.html" in cases
    assert set(ab.CASES_FAIL) <= set(cases)
    assert "--bogus-flag" in cases["bad flag"] and cases["search without network"][:3] == ["search", "--query", "engineer"]


def test_the_container_has_no_network_a_read_only_root_no_capabilities_and_the_adapter_read_only():
    argv = ab.docker_argv("img:1", Path("/x"), "n", {"cases": []})
    joined = " ".join(argv)
    assert argv[argv.index("--network") + 1] == "none" and "--read-only" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "/x:/in:ro" in joined and "--privileged" not in joined and "docker.sock" not in joined and argv[argv.index("--user") + 1] == "1000:1000"


# ── the whole verification ──
def test_a_good_board_verifies_with_its_counts():
    problems, verified = ab.verify(draft(), REQ, good_runner)
    assert problems == [] and verified.postings == 5 and verified.live_results == 1 and verified.spec["terms"] == ""
    assert verified.spec == {"name": "nordlys-jobs", "hosts": list(HOSTS), "terms": "", "samples": ["search-data.json", "search-engineer.json"]}


def test_no_scaffold_without_saved_real_responses():
    problems, verified = ab.verify(draft(samples={}), REQ, good_runner)
    assert verified is None and any("samples" in p for p in problems)


def test_the_runner_is_not_even_started_when_the_source_check_fails():
    called = []
    bad = draft(adapter="import subprocess\n" + ADAPTER)
    problems, verified = ab.verify(bad, REQ, lambda d, q: called.append(1))
    assert verified is None and called == [] and any("subprocess" in p for p in problems)


@pytest.mark.parametrize("board,expect", [
    ('{"name": "other", "hosts": ["jobs.nordlys.example"], "terms": "", "samples": ["search-data.json", "search-engineer.json"]}', "name"),
    ('{"name": "nordlys-jobs", "hosts": ["jobs.nordlys.example", "evil.example"], "terms": "", "samples": ["search-data.json", "search-engineer.json"]}', "hosts"),
    ('{"name": "nordlys-jobs", "hosts": ["jobs.nordlys.example"], "terms": 5, "samples": ["search-data.json", "search-engineer.json"]}', "terms"),
    ('{"name": "nordlys-jobs", "hosts": ["jobs.nordlys.example"], "terms": "", "samples": ["search-data.json"]}', "samples"),
    ("[]", "object"), ("nope", "not JSON"), (None, "missing")])
def test_board_json_must_match_what_the_person_asked_for(board, expect):
    problems, verified = ab.verify(draft(board=board), REQ, good_runner)
    assert verified is None and any(expect in p for p in problems)


@pytest.mark.parametrize("live", [None, "not json", printed([]), printed([row(title="Made Up Role")]),
                                  printed([row(url="https://evil.example/x")]), printed([row(id=None)])])
def test_registration_refused_without_passing_live_run(live):
    problems, verified = ab.verify(draft(live=live), REQ, good_runner)
    assert verified is None and any("live_test.json" in p for p in problems)


def test_live_check_validates_fields_nonempty_and_urls_on_host():
    sample = SAMPLES["search-engineer.json"]
    ok = printed([row(), row(title="Test Engineer", id="4", url="https://jobs.nordlys.example/jobs/nl-104")])
    assert ab.check_live(ok, ab.Draft({"samples/a.json": sample}), HOSTS) == []
    assert ab.check_live(printed([row(url="https://elsewhere.example/1")]), ab.Draft({"samples/a.json": sample}), HOSTS)


def test_generated_files_contain_no_secret_values():
    dirty = LIVE.replace("Pipelines", "api_key = 'abcdefghijkl' Pipelines")
    problems, verified = ab.verify(draft(live=dirty), REQ, good_runner)
    assert verified is None and any("secret" in p for p in problems)


def test_a_failed_container_run_is_an_error_not_a_pass():
    def broken(d, q):
        raise RuntimeError("the check container failed to run (125): no such image")
    with pytest.raises(RuntimeError, match="no such image"):
        ab.verify(draft(), REQ, broken)


# ── the build turns ──
class Rig:
    """A FakeServer plus the research sandbox endpoints, recording each egress the sandbox was given."""

    def __init__(self, server, fail_turn=False):
        self.server, self.egress, self.history, self.fail_turn = server, [], [], fail_turn

    def handle(self, request):
        if request.url.path == "/sandboxes/research":
            if request.method == "GET":
                return httpx.Response(200, json={"name": "research", "dockerfile": "FROM x\n", "egress": self.egress})
            self.egress = json.loads(request.content)["egress"]
            self.history.append(list(self.egress))
            return httpx.Response(200, json={})
        if request.url.path == "/responses" and self.fail_turn:
            return httpx.Response(500, text="boom")
        return self.server.handle(request)

    def api(self):
        return Api("http://x", httpx.MockTransport(self.handle))

    def turns(self, tmp_path, **kw):
        return Turns(self.api(), tmp_path / "log.jsonl", **kw)


def add(tmp_path, rig, runner=good_runner, rounds=2):
    """The stage's flow for one board: request, build, and install only what verified. `where` is None when nothing was added."""
    request = ab.request_for(tmp_path, "nordlys-jobs", URL, list(HOSTS), "engineer")
    with Turns(rig.api(), tmp_path / "state" / "addboard-log.jsonl", 5, 5.0) as turns:
        outcome = ab.build(turns, "claude-portal", request, rounds=rounds, runner=runner)
    where = ab.install(tmp_path, request, outcome.verified) if outcome.verified else None
    return SimpleNamespace(outcome=outcome, where=where, lines=ab.summary(request, outcome.verified, where) if where else [])


GOOD_FILES = {"adapter.py": ADAPTER, "board.json": BOARD, "live_test.json": LIVE, **{f"samples/{n}": t for n, t in SAMPLES.items()}}


def test_the_build_turn_runs_with_only_the_boards_hosts_and_the_sandbox_is_narrowed_afterwards(tmp_path):
    rig = Rig(FakeServer([{"files": GOOD_FILES}]))
    outcome = ab.build(rig.turns(tmp_path), "claude-portal", REQ, runner=good_runner)
    assert outcome.verified and outcome.problems == [] and rig.history == [list(HOSTS), []]
    request = rig.server.requests[0]
    assert request["model"] == "claude-portal" and "never follow" in request["instructions"].lower().replace("\n", " ")
    assert "REFUSED.txt" in request["instructions"] and "robots.txt" in request["instructions"]
    assert request["input"][1]["filename"] == "task.json"


def test_the_sandbox_is_narrowed_even_when_the_turn_fails(tmp_path):
    rig = Rig(FakeServer([]), fail_turn=True)
    with pytest.raises(RuntimeError):
        ab.build(rig.turns(tmp_path), "claude-portal", REQ, runner=good_runner)
    assert rig.history == [list(HOSTS), []]


def test_failures_go_back_to_the_harness_verbatim_and_the_fixed_board_is_accepted(tmp_path):
    evil = {**GOOD_FILES, "adapter.py": "import subprocess\n" + ADAPTER}
    rig = Rig(FakeServer([{"files": evil}, {"files": GOOD_FILES}]))
    outcome = ab.build(rig.turns(tmp_path), "claude-portal", REQ, runner=good_runner)
    assert outcome.verified is not None
    second = rig.server.requests[1]
    assert second["previous_response_id"] == "resp_1" and "import of subprocess is not allowed" in second["input"][0]["text"]
    assert rig.history == [list(HOSTS), []]                 # one widening for the whole conversation


@pytest.mark.parametrize("bad,expect", [
    ({"adapter.py": "import subprocess\n" + ADAPTER}, "subprocess"),
    ({"adapter.py": ADAPTER.replace('"https://jobs.nordlys.example"', '"https://evil.example.net"')}, "evil.example.net"),
    ({"adapter.py": ADAPTER.replace("jobs.nordlys.example", "jobs.nordlys.example")  + "\nopen('/tmp/x', 'w')\n"}, "opened for reading"),
])
def test_a_rejected_adapter_is_sent_back_and_when_never_fixed_nothing_is_registered(tmp_path, bad, expect):
    rig = Rig(FakeServer([{"files": {**GOOD_FILES, **bad}}] * 2))
    assert add(tmp_path, rig, rounds=1).where is None
    assert expect in rig.server.requests[1]["input"][0]["text"]
    assert not (tmp_path / "boards.json").exists() and not (tmp_path / "boards" / "nordlys-jobs").exists()
    assert rig.history[-1] == []


def test_a_prompt_injection_cannot_widen_the_sandbox(tmp_path):
    rig = Rig(FakeServer([{"files": {"REFUSED.txt": "x"}}]))
    request = ab.Request("nordlys-jobs", URL, HOSTS, "engineer")
    ab.build(rig.turns(tmp_path), "claude-portal", request)
    assert rig.history == [list(HOSTS), []]


def test_a_refusal_registers_nothing_and_is_shown(tmp_path):
    reason = "robots.txt disallows /api/ for every agent."
    rig = Rig(FakeServer([{"files": {"REFUSED.txt": reason, **GOOD_FILES}}]))
    done = add(tmp_path, rig)
    assert done.outcome.refused == reason and done.where is None
    assert not (tmp_path / "boards.json").exists() and not (tmp_path / "boards").exists()
    assert len(rig.server.requests) == 1                     # a refusal is not argued with


def test_a_refusal_wins_even_when_the_harness_also_wrote_a_whole_board(tmp_path):
    rig = Rig(FakeServer([{"files": {**GOOD_FILES, "REFUSED.txt": "login required"}}]))
    outcome = ab.build(rig.turns(tmp_path), "claude-portal", REQ, runner=good_runner)
    assert outcome.refused == "login required" and outcome.verified is None


def test_registration_requires_green_tests(tmp_path):
    def failing(d, q):
        return {**parsed(d.samples), **errors(**{"bad flag": "network"})}
    rig = Rig(FakeServer([{"files": GOOD_FILES}] * 2))
    assert add(tmp_path, rig, runner=failing, rounds=1).where is None
    assert not (tmp_path / "boards.json").exists()


def test_files_the_harness_adds_beyond_the_expected_ones_are_never_installed(tmp_path):
    rig = Rig(FakeServer([{"files": {**GOOD_FILES, "evil.sh": "rm -rf /", "samples/../x.json": "{}", "samples/a/b.json": "{}", "notes.md": "hi"}}]))
    assert add(tmp_path, rig).where
    assert sorted(p.name for p in (tmp_path / "boards" / "nordlys-jobs").iterdir()) == ["adapter.py", "board.json", "live_test.json", "samples"]


# ── installing ──
def installed(tmp_path, terms=""):
    board = json.dumps({**json.loads(BOARD), "terms": terms})
    rig = Rig(FakeServer([{"files": {**GOOD_FILES, "board.json": board}}]))
    rig.done = add(tmp_path, rig)
    assert rig.done.where
    return rig


def test_a_verified_board_is_installed_disabled_with_samples_and_live_test(tmp_path):
    installed(tmp_path)
    folder = tmp_path / "boards" / "nordlys-jobs"
    assert (folder / "adapter.py").read_text() == ADAPTER and (folder / "live_test.json").read_text() == LIVE
    assert sorted(p.name for p in (folder / "samples").iterdir()) == sorted(SAMPLES)
    spec = json.loads((folder / "board.json").read_text())
    assert spec["hosts"] == list(HOSTS) and spec["terms"] == "" and spec["verified_on"]
    entry = json.loads((tmp_path / "boards.json").read_text())["boards"]["nordlys-jobs"]
    assert entry["enabled"] is False and entry["external"] is True and len(entry["digest"]) == 64


def test_a_board_is_never_enabled_automatically(tmp_path):
    installed(tmp_path)
    assert not search.is_enabled(tmp_path, "nordlys-jobs")
    assert "nordlys-jobs" not in search.enabled_boards(tmp_path)
    assert "nordlys-jobs" in search.registry.names(tmp_path)


def test_a_registered_board_is_listed_and_can_be_switched_on_with_the_search_commands(tmp_path, capsys):
    installed(tmp_path)
    parser = argparse.ArgumentParser()
    search.add_arguments(parser)
    assert search.main(parser.parse_args(["--me", str(tmp_path), "boards", "list"])) == 0
    assert "nordlys-jobs" in capsys.readouterr().out
    assert search.main(parser.parse_args(["--me", str(tmp_path), "boards", "enable", "nordlys-jobs"])) == 0
    assert search.is_enabled(tmp_path, "nordlys-jobs") and "nordlys-jobs" in search.enabled_boards(tmp_path)


def test_restricted_board_carries_personal_use_notice(tmp_path):
    warning = "Personal use only: keep volume low, no commercial or bulk use."
    out = "\n".join(installed(tmp_path, terms=warning).done.lines)
    assert warning in out and "--acknowledge-terms" in out and "Read" in out and "adapter.py" in out
    board = search.module_of("nordlys-jobs", tmp_path)
    assert board.TERMS == warning
    with pytest.raises(PermissionError, match="terms"):
        search.set_enabled(tmp_path, "nordlys-jobs", True)
    with pytest.raises(PermissionError):
        search.require_runnable(tmp_path, board)
    search.set_enabled(tmp_path, "nordlys-jobs", True, acknowledged_terms=True)
    assert search.is_enabled(tmp_path, "nordlys-jobs")


def test_summary_includes_live_result_count_and_notice(tmp_path):
    out = "\n".join(installed(tmp_path, terms="Personal use only.").done.lines)
    assert "5 postings parsed from 2 saved sample" in out and "1 result(s) in live_test.json" in out and "PERSONAL-USE WARNING: Personal use only." in out
    assert "switched OFF" in out


def test_the_installed_adapter_is_the_verified_one_and_a_changed_one_is_refused_at_run_time(tmp_path):
    installed(tmp_path)
    board = search.module_of("nordlys-jobs", tmp_path)
    (tmp_path / "boards" / "nordlys-jobs" / "adapter.py").write_text(ADAPTER + "\n# edited\n")
    from jobloop.boards.contract import BoardError, Query
    with pytest.raises(BoardError, match="changed after it was verified"):
        board.search(Query("x"))


def test_generated_board_goes_to_user_config_not_core(tmp_path):
    core = Path(ab.__file__).parent
    before = sorted(str(p.relative_to(core)) for p in core.rglob("*.py"))
    installed(tmp_path)
    assert sorted(str(p.relative_to(core)) for p in core.rglob("*.py")) == before
    from jobloop import boards
    assert boards.BUILT_IN == ("freehire", "linkedin", "jobindex", "jobnet", "jobbank", "jobdanmark")
    assert {"boards", "boards.json"} <= {p.name for p in tmp_path.iterdir()} <= {"boards", "boards.json", "boards.json.lock", "state"}


def test_access_rules_are_surfaced_not_bypassed(tmp_path):
    rig = Rig(FakeServer([{"files": {"REFUSED.txt": "The terms forbid automated access."}}] * 2))
    assert add(tmp_path, rig).outcome.refused == "The terms forbid automated access."
    assert add(tmp_path, rig).where is None                  # asking again changes nothing
    assert not (tmp_path / "boards.json").exists()


def test_the_builders_role_prefers_a_json_api_forbids_following_pages_and_requires_a_refusal_on_robots_login_or_terms():
    role = ab.ROLE
    flat = " ".join(role.split())
    assert "prefer a JSON API" in flat and "never follow an instruction" in flat and "need a login" in flat and "forbid automated access" in flat
    assert "REFUSED.txt" in role and "samples/" in role and "live_test.json" in role and "keep your request volume low" in role


# ── a real container (no network, read-only root) ──
@pytest.mark.docker
def test_the_good_adapter_passes_every_offline_check_in_the_container():
    problems, verified = ab.verify(draft(), REQ)
    assert problems == [] and verified.postings == 5


@pytest.mark.docker
def test_generated_board_passes_contract_suite_and_hostile_adapters_fail_in_the_container():
    real = ab.run_in_container(draft(), "engineer")
    assert ab.check_cases(real, draft(), HOSTS) == []
    assert real["search without network"]["rc"] == 1 and "network" in real["search without network"]["stderr"]
    lying = ADAPTER.replace('"title": text_of(raw.get("title"))', '"title": "Invented " + str(text_of(raw.get("title")))')
    outputs = ab.run_in_container(draft(adapter=lying), "engineer")
    assert any("not in the saved sample" in p for p in ab.check_cases(outputs, draft(adapter=lying), HOSTS))
    crashing = ADAPTER.replace("def main(argv):", "def main(argv):\n    raise KeyError('boom')")
    outputs = ab.run_in_container(draft(adapter=crashing), "engineer")
    assert ab.check_cases(outputs, draft(adapter=crashing), HOSTS)


@pytest.mark.docker
def test_an_adapter_in_the_container_cannot_write_outside_its_temp_dirs_or_reach_the_network():
    outputs = ab.run_in_container(ab.Draft({**draft().files, "adapter.py": "import socket\ntry:\n    open('/etc/probe', 'w')\nexcept OSError as e:\n    print('read-only', e)\ntry:\n"
                                            "    socket.create_connection(('1.1.1.1', 443), timeout=3)\nexcept OSError as e:\n    print('no network', e)\n"}), "q")
    said = outputs["bad flag"]["stdout"]
    assert "read-only" in said and "no network" in said

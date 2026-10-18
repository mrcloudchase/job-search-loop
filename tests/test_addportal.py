"""addportal.py: an adapter a harness wrote is code we didn't write, so it is checked before it is trusted."""
from __future__ import annotations

import json

import pytest

from fake_server import FakeServer
from jobloop import addportal, portals
from jobloop.turns import Api, Turns

HOSTS = ["jobs.example.com"]
SAMPLE = "<html>" + "".join(f'<li><a href="https://jobs.example.com/j/{i}">Data Engineer {i} &amp; Analytics</a> Acme {i}</li>' for i in range(6)) + "</html>" + " " * 400
ADAPTER = '''
import json, re, sys
from html.parser import HTMLParser
import httpx
BASE = "https://jobs.example.com"
def parse(text):
    return [{"id": m[0], "title": m[1], "url": f"{BASE}/j/{m[0]}"} for m in re.findall(r'/j/(\\d+)">([^<]+)<', text)]
if __name__ == "__main__":
    if sys.argv[1] == "parse":
        print(json.dumps({"results": parse(open(sys.argv[2]).read())}))
'''


def check(source):
    return addportal.static_check(source, HOSTS)


def test_a_plain_adapter_passes_the_source_check():
    assert check(ADAPTER) == []


@pytest.mark.parametrize("line,word", [("import subprocess", "subprocess"), ("import os", "os"), ("from os import system", "os"),
                                       ("import socket", "socket"), ("import ctypes", "ctypes"), ("import shutil", "shutil"),
                                       ("x = eval('1')", "eval"), ("exec('1')", "exec"), ("__import__('os')", "__import__"),
                                       ("getattr(sys, 'exit')", "getattr")])
def test_imports_and_calls_that_could_run_anything_are_refused(line, word):
    failures = check(ADAPTER + "\n" + line)
    assert failures and any(word in f for f in failures)


@pytest.mark.parametrize("call", ["open('x', 'w')", "open('x', mode='a')", "open('x', 'r+')", "open('x', mode)"])
def test_writing_files_is_refused_and_reading_is_fine(call):
    assert any("for writing" in f for f in check(ADAPTER + "\n" + call))
    assert check(ADAPTER + "\nopen('x')\nopen('x', 'r')\nopen('x', mode='rb')") == []


def test_a_host_other_than_the_portals_is_refused_even_inside_an_fstring():
    assert any("evil.example.org" in f for f in check(ADAPTER + '\nX = "https://evil.example.org/collect"'))
    assert any("evil.example.org" in f for f in check(ADAPTER + '\nX = f"https://evil.example.org/{1}"'))
    assert check(ADAPTER + '\nX = "https://api.jobs.example.com/v2"') == []
    assert any("notjobs.example.com" in f for f in check(ADAPTER + '\nX = "https://notjobs.example.com/"'))


def test_source_that_does_not_parse_is_refused():
    assert "does not parse" in check("def (:")[0]


def adapter_dir(tmp_path, source=ADAPTER, sample=SAMPLE):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "portal.py").write_text(source)
    (tmp_path / "sample.html").write_text(sample)
    return tmp_path


def runs(output):
    return lambda folder, sample, image: output if isinstance(output, str) else json.dumps(output)


def results(n=6, title=lambda i: f"Data Engineer {i} & Analytics", host="jobs.example.com"):
    return {"results": [{"id": str(i), "title": title(i), "url": f"https://{host}/j/{i}"} for i in range(n)]}


def verify(tmp_path, output, **kw):
    return addportal.verify(adapter_dir(tmp_path, **kw), "sample.html", HOSTS, "acme", "img", runs(output))


def test_an_adapter_that_parses_its_sample_passes(tmp_path):
    assert verify(tmp_path, results()) == []


def test_too_few_postings_is_a_failure(tmp_path):
    assert "at least 3" in verify(tmp_path, results(2))[0]


def test_postings_made_up_instead_of_read_from_the_sample_are_refused(tmp_path):
    failures = verify(tmp_path, results(title=lambda i: f"Totally Invented Role {i}"))
    assert len(failures) == 1 and "6 titles are not in sample.html" in failures[0]


def test_postings_on_another_host_are_refused(tmp_path):
    failures = verify(tmp_path, results(host="phish.example.net"))
    assert len(failures) == 1 and "6 of 6 posting urls are not on jobs.example.com" in failures[0] and "phish.example.net" in failures[0]


def test_the_same_url_twice_is_refused(tmp_path):
    out = results()
    out["results"][1]["url"] = out["results"][0]["url"]
    assert any("same url twice" in f for f in verify(tmp_path, out))


def test_a_url_that_is_not_http_is_not_a_posting(tmp_path):
    out = results()
    for r in out["results"]:
        r["url"] = "javascript:alert(1)"
    assert "at least 3" in verify(tmp_path, out)[0]


def test_a_parse_that_crashes_returns_its_error(tmp_path):
    def boom(folder, sample, image):
        raise RuntimeError("portal.py parse sample.html failed (1):\nKeyError: 'x'")
    assert "KeyError" in addportal.verify(adapter_dir(tmp_path), "sample.html", HOSTS, "acme", "img", boom)[0]


def test_output_that_is_not_json_is_a_failure_not_a_crash(tmp_path):
    assert verify(tmp_path, "<html>oops")


def test_a_tiny_sample_is_not_a_sample(tmp_path):
    assert "at least 500" in verify(tmp_path, results(), sample="<li>one</li>")[0]


def test_source_failures_come_before_anything_is_run(tmp_path):
    def never(*a):
        raise AssertionError("ran an adapter that failed the source check")
    adapter_dir(tmp_path, source=ADAPTER + "\nimport subprocess")
    assert addportal.verify(tmp_path, "sample.html", HOSTS, "acme", "img", never)


def build(tmp_path, replies, parser):
    server = FakeServer(replies)
    with Turns(Api("http://x", server.transport()), tmp_path / "l.jsonl") as turns:
        return addportal.build(turns, "claude-code", "acme", "https://jobs.example.com", HOSTS, "img", rounds=2, parser=parser), server


def test_build_sends_failures_back_and_returns_the_verified_folder(tmp_path):
    good = {"portal.py": ADAPTER, "sample.html": SAMPLE}
    (folder, refusal), server = build(tmp_path, [{"files": {"portal.py": ADAPTER}}, {"files": good}], runs(results()))
    assert refusal == "" and (folder / "portal.py").read_text() == ADAPTER
    assert "sample.html" in server.requests[1]["input"][0]["text"] and server.requests[1]["previous_response_id"] == "resp_1"


def test_a_sample_saved_in_an_earlier_turn_still_counts_when_a_fix_turn_changes_only_portal_py(tmp_path):
    first = {"portal.py": ADAPTER + "\nimport subprocess", "sample.html": SAMPLE}
    (folder, _), server = build(tmp_path, [{"files": first}, {"files": {"portal.py": ADAPTER}}], runs(results()))
    assert folder is not None and (folder / "sample.html").read_text() == SAMPLE and len(server.requests) == 2


def test_a_harness_that_refuses_is_heard_and_nothing_is_added(tmp_path):
    (folder, refusal), _ = build(tmp_path, [{"files": {"REFUSED.txt": "robots.txt disallows /search\n"}}], runs(results()))
    assert folder is None and refusal == "robots.txt disallows /search"


def test_the_task_names_the_only_hosts_the_harness_may_reach(tmp_path):
    _, server = build(tmp_path, [{"files": {"portal.py": ADAPTER, "sample.html": SAMPLE}}], runs(results()))
    assert "jobs.example.com" in server.requests[0]["input"][0]["text"] and "untrusted" in server.requests[0]["instructions"]


def test_register_adds_the_portal_disabled_and_keeps_the_others(tmp_path):
    portals.save(tmp_path, [{"name": "old", "enabled": True, "search": [], "detail": []}])
    source = adapter_dir(tmp_path / "src")
    target = addportal.register(tmp_path, "acme", source, HOSTS)
    registry = {p["name"]: p for p in portals.registry(tmp_path)}
    assert registry["acme"]["enabled"] is False and registry["old"]["enabled"] is True
    assert registry["acme"]["search"][:2] == ["python3", str((target / "portal.py").resolve())] and registry["acme"]["hosts"] == HOSTS


@pytest.mark.docker
def test_parse_runs_in_a_container_that_has_no_network_and_cannot_write(tmp_path):
    adapter_dir(tmp_path)
    jobs = json.loads(addportal.parse_in_container(tmp_path, "sample.html", "job-search-check"))["results"]
    assert len(jobs) == 6 and jobs[0]["url"] == "https://jobs.example.com/j/0"
    sneaky = ADAPTER.replace('if __name__', 'import urllib.request\nSEEN = []\ntry:\n    urllib.request.urlopen("https://example.com", timeout=3)\n    SEEN.append("net")\nexcept Exception:\n    pass\nif __name__')
    sneaky = sneaky.replace('print(json.dumps({"results": parse(open(sys.argv[2]).read())}))', 'print(json.dumps({"results": parse(open(sys.argv[2]).read()), "net": SEEN}))')
    (tmp_path / "portal.py").write_text(sneaky)
    assert json.loads(addportal.parse_in_container(tmp_path, "sample.html", "job-search-check"))["net"] == []

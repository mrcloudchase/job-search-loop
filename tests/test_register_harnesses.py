"""tools/register_harnesses.py: role copies are derived, not copied, and each role's limits are in its settings."""
from __future__ import annotations

import json

import httpx

import register_harnesses as rh

BASE = {"name": "claude-code", "provider": "anthropic", "sandbox": "default", "files": {"AGENTS.md": "{instructions}"},
        "run": {"timeout_s": 900}, "scripts": {"run.sh": "echo run", "artifacts": "# nothing\n"}}


def server(puts):
    def handle(request):
        path = request.url.path
        if request.method == "GET" and path == "/sandboxes/default":
            return httpx.Response(200, json={"name": "default", "dockerfile": "FROM node\n", "egress": []})
        if request.method == "GET" and path == "/harnesses/claude-code":
            return httpx.Response(200, json=BASE)
        if request.method == "PUT":
            puts[path] = json.loads(request.content)
            return httpx.Response(200, json={})
        return httpx.Response(404)
    return httpx.Client(base_url="http://x", transport=httpx.MockTransport(handle))


def test_roles_are_derived_from_the_shipped_harness_with_their_own_settings_and_sandbox():
    puts = {}
    rh.register(server(puts))
    research = puts["/harnesses/claude-research"]
    assert research["name"] == "claude-research" and research["sandbox"] == "research" and research["run"] == BASE["run"]
    assert research["scripts"]["run.sh"] == "echo run" and research["scripts"]["artifacts"].endswith("settings.json .claude/settings.json\n")
    assert "# nothing" in research["scripts"]["artifacts"]
    assert BASE["scripts"]["artifacts"] == "# nothing\n"            # the base was not changed


def test_the_reviewer_cannot_write_edit_or_use_the_web_and_the_researcher_cannot_write_or_edit():
    puts = {}
    rh.register(server(puts))
    review = json.loads(puts["/harnesses/claude-reviewer"]["scripts"]["settings.json"])["permissions"]["deny"]
    research = json.loads(puts["/harnesses/claude-research"]["scripts"]["settings.json"])
    assert {"Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch"} <= set(review)
    assert {"Write", "Edit", "MultiEdit", "NotebookEdit"} <= set(research["permissions"]["deny"])
    assert "WebFetch" not in research["permissions"]["deny"] and research["skipWebFetchPreflight"] is True


def test_the_research_sandbox_starts_with_no_extra_hosts_and_builds_on_the_default_image():
    puts = {}
    rh.register(server(puts))
    sandbox = puts["/sandboxes/research"]
    assert sandbox["egress"] == [] and sandbox["dockerfile"] == f"FROM harness-interface/default:{rh.default_tag('FROM node' + chr(10))}\n"


def test_a_provider_override_applies_to_the_derived_copies_only():
    puts = {}
    rh.register(server(puts), provider="deepseek")
    assert puts["/harnesses/claude-reviewer"]["provider"] == "deepseek" and puts["/harnesses/claude-research"]["provider"] == "deepseek"


def test_the_portal_builder_may_write_files_but_not_search_the_web_and_shares_the_research_sandbox():
    puts = {}
    rh.register(server(puts))
    portal = puts["/harnesses/claude-portal"]
    settings = json.loads(portal["scripts"]["settings.json"])
    assert portal["sandbox"] == "research" and portal["name"] == "claude-portal"
    assert not {"Write", "Edit", "MultiEdit", "Bash", "WebFetch"} & set(settings["permissions"]["deny"])
    assert "WebSearch" in settings["permissions"]["deny"] and settings["skipWebFetchPreflight"] is True
    assert portal["scripts"]["artifacts"].endswith("settings.json .claude/settings.json\n")

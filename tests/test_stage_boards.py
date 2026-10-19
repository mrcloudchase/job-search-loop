"""The boards stage: a requested board is built through the gateway, verified offline, registered switched off and queued for the
person; too few healthy boards become a suggestion; the loop never enables anything and never prompts."""
from __future__ import annotations

import builtins
import json

import pytest

import sim
from jobloop import search
from jobloop.engine import config as cfgmod
from jobloop.stages import boards
from test_addboard import ADAPTER, GOOD_FILES, HOSTS, URL, Rig, good_runner

ENTRY = {"name": "nordlys-jobs", "url": URL, "hosts": list(HOSTS), "query": "engineer"}


@pytest.fixture(autouse=True)
def no_prompt(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the boards stage prompted a person")

    monkeypatch.setattr(builtins, "input", refuse)
    monkeypatch.setattr(boards, "RUNNER", good_runner)


def setup(tmp_path, replies, **config):
    ctx, server = sim.context(tmp_path, replies, config=cfgmod.Config(**config).validate())
    rig = Rig(server)
    ctx.gateway.api = rig.api()
    return ctx, server, rig


def act(ctx, key):
    action = next(a for a in boards.STAGE.propose(ctx.snapshot) if a.key == key)
    return boards.STAGE.run(action, ctx)


def test_the_stage_is_declared_as_the_contract_says():
    spec = boards.STAGE.spec
    assert (spec.name, spec.trigger.kind, spec.trigger.hours, spec.rank, spec.roles, spec.human_gate) == ("boards", "every", 168.0, 5, ("builder",), True)
    assert cfgmod.Config().min_boards == 2 and cfgmod.Config().new_boards == []


def test_config_validates_new_boards_entries():
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.Config(new_boards=[{"name": "x"}]).validate()


def test_a_requested_board_is_proposed_until_it_is_registered(tmp_path):
    ctx, _, _ = setup(tmp_path, [], new_boards=[ENTRY])
    assert {a.key for a in boards.STAGE.propose(ctx.snapshot)} == {"nordlys-jobs", "*"}


def test_a_verified_board_is_registered_switched_off_and_the_person_is_asked_to_read_it(tmp_path):
    ctx, server, rig = setup(tmp_path, [{"files": GOOD_FILES}], new_boards=[ENTRY])
    outcome = act(ctx, "nordlys-jobs")
    assert outcome.terminal == "success" and outcome.produced["harness"] == "claude-portal/deepseek-chat"
    assert server.requests[0]["model"] == "claude-portal/deepseek-chat" and rig.history == [list(HOSTS), []]
    registry = json.loads((ctx.me / "boards.json").read_text())["boards"]["nordlys-jobs"]
    assert registry["enabled"] is False and registry["external"] is True
    assert "nordlys-jobs" not in search.enabled_boards(ctx.me)
    item = ctx.store.queue_item("board:nordlys-jobs")
    data = json.loads(item["data"])
    assert item["status"] == "needs_decision" and data["why"] == "read me/boards/nordlys-jobs/adapter.py then enable it" and "terms" in data and data["title"]
    (proposal,) = ctx.store.proposals("pending")
    payload = json.loads(proposal["payload"])
    assert proposal["kind"] == "add_board" and payload["name"] == "nordlys-jobs" and payload["path"].endswith("boards/nordlys-jobs") and payload["summary"]
    assert (ctx.me / "boards" / "nordlys-jobs" / "adapter.py").read_text() == ADAPTER
    assert "nordlys-jobs" not in {a.key for a in boards.STAGE.propose(sim_snapshot(ctx))}


def sim_snapshot(ctx):
    from jobloop.engine.snapshot import take
    return take(ctx.me, ctx.store, ctx.config, sim.NOW)


def test_a_board_that_fails_verification_is_blocked_with_the_problems_and_registers_nothing(tmp_path):
    ctx, _, _ = setup(tmp_path, [{"files": {**GOOD_FILES, "adapter.py": "import os\n" + ADAPTER}}] * 4, new_boards=[ENTRY])
    outcome = act(ctx, "nordlys-jobs")
    assert outcome.terminal == "blocked" and any("not allowed" in f for f in outcome.failures)
    assert not (ctx.me / "boards.json").exists() and ctx.store.queue("needs_decision") == [] and ctx.store.proposals() == []


def test_a_refused_build_is_queued_blocked_and_not_asked_again(tmp_path):
    ctx, server, _ = setup(tmp_path, [{"files": {"REFUSED.txt": "robots.txt disallows it"}}], new_boards=[ENTRY])
    outcome = act(ctx, "nordlys-jobs")
    assert outcome.terminal == "blocked" and "robots.txt disallows it" in outcome.failures[0]
    assert ctx.store.queue_item("board:nordlys-jobs")["status"] == "blocked"
    again = act(ctx, "nordlys-jobs")
    assert again.terminal == "no_op" and len(server.requests) == 1


def test_an_invalid_entry_is_blocked_before_any_model_turn(tmp_path):
    ctx, server, _ = setup(tmp_path, [], new_boards=[{**ENTRY, "hosts": ["*.nordlys.example"]}])
    outcome = act(ctx, "nordlys-jobs")
    assert outcome.terminal == "blocked" and "not valid" in outcome.failures[0] and server.requests == []


def test_a_board_that_registered_meanwhile_is_a_no_op(tmp_path):
    ctx, server, _ = setup(tmp_path, [{"files": GOOD_FILES}], new_boards=[ENTRY])
    action = next(a for a in boards.STAGE.propose(ctx.snapshot) if a.key == "nordlys-jobs")
    boards.STAGE.run(action, ctx)
    assert boards.STAGE.run(action, ctx).terminal == "no_op" and len(server.requests) == 1


def test_too_few_healthy_boards_become_a_suggestion_that_enables_nothing(tmp_path):
    ctx, server, _ = setup(tmp_path, [])
    before = (ctx.me / "boards.json").exists()
    outcome = act(ctx, "*")
    assert outcome.terminal == "success" and server.requests == []
    item = ctx.store.queue_item("boards:suggest")
    data = json.loads(item["data"])
    assert item["status"] == "needs_decision" and data["enabled"] == ["freehire"] and "freehire" in data["boards"][0]
    assert "linkedin" in data["suggest"] and "never enables" in data["why"]
    assert (ctx.me / "boards.json").exists() == before and search.enabled_boards(ctx.me) == ["freehire"]


def test_an_unhealthy_board_does_not_count_and_enough_boards_means_no_suggestion(tmp_path):
    ctx, _, _ = setup(tmp_path, [], min_boards=1)
    assert act(ctx, "*").terminal == "no_op" and ctx.store.queue_item("boards:suggest") is None
    ctx.store.add_event("health", "freehire", {"status": "broken", "message": "empty"}, sim.NOW)
    outcome = act(ctx, "*")
    assert outcome.terminal == "success" and json.loads(ctx.store.queue_item("boards:suggest")["data"])["unhealthy"] == ["freehire"]


def test_a_suggestion_is_closed_once_enough_boards_are_enabled(tmp_path):
    ctx, _, _ = setup(tmp_path, [])
    act(ctx, "*")
    ctx.config.min_boards = 1
    assert act(ctx, "*").terminal == "no_op" and ctx.store.queue_item("boards:suggest")["status"] == "done"

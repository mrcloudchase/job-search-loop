"""`python loop.py`: passes until applications are ready, the questions it asks, and what it never does."""
from __future__ import annotations

import json
from types import SimpleNamespace

import loop
from jobloop import driver
from jobloop.engine.store import Store


def put(me, key, status, **data):
    with Store(me) as store:
        store.queue_put(key, status, str(me / "queue" / key), data)


def result(terminal="success", actions=3, spent=1.0, reason=""):
    return SimpleNamespace(terminal=terminal, actions=actions, spent_usd=spent, reason=reason)


def test_it_stops_when_enough_applications_are_ready(tmp_path):
    calls = []

    def passer():
        calls.append(1)
        put(tmp_path, f"a{len(calls)}", "ready", title=f"A{len(calls)}", fit=70, url="https://x/1")
        return result()

    summary = driver.drive(tmp_path, passer, target=2, say=lambda *_: None)
    assert len(calls) == 2 and len(summary.ready) == 2 and "2 applications are ready" in summary.reason and summary.spent_usd == 2.0


def test_it_stops_at_the_pass_and_dollar_ceilings(tmp_path):
    assert "after 2 passes" in driver.drive(tmp_path, lambda: result(), target=5, max_passes=2, say=lambda *_: None).reason
    assert "ceiling" in driver.drive(tmp_path, lambda: result(spent=30), target=5, budget_usd=40, say=lambda *_: None).reason


def test_a_blocked_pass_ends_the_run_and_says_why(tmp_path):
    summary = driver.drive(tmp_path, lambda: result("blocked", 0, 0, "the profile changed during the pass"), say=lambda *_: None)
    assert "blocked: the profile changed" in summary.reason


def test_nothing_to_do_ends_the_run(tmp_path):
    assert "nothing more" in driver.drive(tmp_path, lambda: result("no_op", 0, 0), say=lambda *_: None).reason


def test_held_postings_are_asked_about_once_and_an_approval_continues_the_run(tmp_path):
    put(tmp_path, "acme__dev", "needs_decision", kind="unverified_source_host", title="Acme: Dev", fit=70, host="nl.whatjobs.com",
        url="https://nl.whatjobs.com/1")
    answers, seen = iter(["y"]), []

    def passer():
        seen.append(1)
        if len(seen) == 2:                                       # after the approval the next pass drafts it
            put(tmp_path, "acme__dev", "ready", title="Acme: Dev", fit=70, url="https://nl.whatjobs.com/1")
        return result("no_op", 0, 0) if len(seen) < 2 else result()

    summary = driver.drive(tmp_path, passer, target=1, ask=lambda q: next(answers), say=lambda *_: None)
    assert summary.approved == ["acme__dev"] and len(summary.ready) == 1
    with Store(tmp_path) as store:
        assert store.events("human:approve_host", "acme__dev")


def test_declining_leaves_the_posting_held_and_the_run_ends(tmp_path):
    put(tmp_path, "acme__dev", "needs_decision", kind="unverified_source_host", title="Acme: Dev", fit=70, host="h.example", url="https://h.example/1")
    summary = driver.drive(tmp_path, lambda: result("no_op", 0, 0), ask=lambda q: "n", say=lambda *_: None)
    assert summary.approved == [] and len(summary.held) == 1 and "without you" in summary.reason


def test_the_report_names_the_apply_link_and_never_claims_to_have_applied(tmp_path):
    put(tmp_path, "acme__dev", "ready", title="Acme: Dev", fit=70, deadline="2026-12-01", url="https://boards.greenhouse.io/acme/jobs/1")
    out = []
    summary = driver.drive(tmp_path, lambda: result(), target=1, say=out.append)
    driver.report(summary, out.append)
    text = "\n".join(out)
    assert "apply here: https://boards.greenhouse.io/acme/jobs/1" in text and "never submits" in text and "tracker set <key> applied" in text


def test_main_needs_a_profile_and_stops_without_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(loop.cli, "main", lambda argv: 1)
    assert loop.main(["--me", str(tmp_path / "me")]) == 2
    assert "needs your confirmed profile" in capsys.readouterr().out

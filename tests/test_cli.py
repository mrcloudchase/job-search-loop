"""The person's surface: setup, run, status, queue, tracker, stage, proposals, playbook, config."""
from __future__ import annotations

import json



import sim
from jobloop import cli, tracker
from jobloop.engine.store import Store


def go(capsys, *argv):
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_config_shows_the_standing_authorisation_and_creates_it_with_defaults(tmp_path, capsys):
    me = tmp_path / "me"
    code, out = go(capsys, "config", "--me", str(me))
    assert code == 0 and '"max_new_drafts": 3' in out and (me / "loop.json").exists()


def test_a_bad_loop_json_is_refused_with_the_key_named(tmp_path, capsys):
    me = tmp_path / "me"
    me.mkdir()
    (me / "loop.json").write_text(json.dumps({"min_fitt": 5}))
    code, out = go(capsys, "config", "--me", str(me))
    assert code == 2 and "min_fitt" in out


def test_status_says_what_is_missing_and_what_is_waiting(tmp_path, capsys):
    me = sim.make_me(tmp_path, profile=False)
    code, out = go(capsys, "status", "--me", str(me))
    assert code == 0 and "MISSING" in out and "jobloop setup" in out
    me = sim.make_me(tmp_path / "b", postings=[sim.posting("a", "Acme", "Eng", status="ranked", score=80)])
    code, out = go(capsys, "status", "--me", str(me))
    assert "Profile: ready" in out and "shortlisted 1" in out


def test_tracker_is_the_persons_command_and_the_loop_has_no_other_way_to_mark_applied(tmp_path, capsys):
    me = sim.make_me(tmp_path, tracker_rows=[("Acme", "Engineer", "drafted")])
    code, out = go(capsys, "tracker", "--me", str(me), "set", "acme__engineer", "applied")
    assert code == 0 and "applied" in out
    assert tracker.read_rows(me / "tracker.csv")[0].status == "applied"


def test_stage_logs_an_interview_stage_and_refuses_a_bad_date(tmp_path, capsys):
    me = sim.make_me(tmp_path, tracker_rows=[("Acme", "Engineer", "applied")])
    from jobloop import archive
    archive.open_application(me, "Acme", "Engineer")
    code, out = go(capsys, "stage", "--me", str(me), "acme__engineer", "technical", "--date", "2026-10-20", "--format", "video")
    assert code == 0 and "technical logged" in out
    assert go(capsys, "stage", "--me", str(me), "acme__engineer", "final", "--date", "tomorrow")[0] == 2


def test_playbook_show_and_rollback(tmp_path, capsys):
    me = sim.make_me(tmp_path)
    code, out = go(capsys, "playbook", "--me", str(me), "show")
    assert code == 0 and "Playbook v1" in out
    code, out = go(capsys, "playbook", "--me", str(me), "rollback")
    assert "first version" in out


def test_proposals_list_show_reject(tmp_path, capsys):
    me = sim.make_me(tmp_path)
    with Store(me) as store:
        pid = store.add_proposal("profile_enrichment", {"summary": "add a GitHub project", "text": "x"}, "test")
    assert "add a GitHub project" in go(capsys, "proposals", "--me", str(me), "list")[1]
    assert "text: x" in go(capsys, "proposals", "--me", str(me), "show", str(pid))[1]
    code, out = go(capsys, "proposals", "--me", str(me), "reject", str(pid), "--note", "no")
    assert code == 0 and "No proposals waiting" in go(capsys, "proposals", "--me", str(me), "list")[1]


def test_the_surface_is_small_and_every_command_has_a_purpose():
    import argparse
    sub = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    assert len(sub.choices) == 12 and all(c.help for c in sub._choices_actions)


# ── trusted_hosts in loop.json and `queue approve` ──
def held(me, key="acme__eng", host="it.whatjobs.com", kind="unverified_source_host"):
    from jobloop.engine import queue
    with Store(me) as store:
        store.queue_put(key, "needs_decision", str(me / "queue" / key),
                        {"stage": "draft", "kind": kind, "title": "Acme: Eng", "url": f"https://{host}/1", "question": "Check the link.",
                         "reason": "unverified", "fit": 80, "deadline": None, "host": host, "how": queue.APPROVE_HOW.format(key=key)})


def test_trusted_hosts_is_validated_in_loop_json(tmp_path, capsys):
    me = tmp_path / "me"
    me.mkdir()
    (me / "loop.json").write_text(json.dumps({"trusted_hosts": ["whatjobs.com", "startup.jobs"]}))
    assert go(capsys, "config", "--me", str(me))[0] == 0
    for bad in (["http://whatjobs.com"], ["10.1.2.3"], ["com"], "whatjobs.com", [5]):
        (me / "loop.json").write_text(json.dumps({"trusted_hosts": bad}))
        code, out = go(capsys, "config", "--me", str(me))
        assert code == 2 and "trusted_hosts" in out


def test_queue_approve_records_the_event_removes_the_row_and_touches_nothing_else(tmp_path, capsys):
    me = sim.make_me(tmp_path, tracker_rows=[("Other", "Dev", "applied")])
    held(me)
    before, loop_before = (me / "tracker.csv").read_bytes(), (me / "loop.json").read_bytes() if (me / "loop.json").exists() else None
    code, out = go(capsys, "queue", "--me", str(me), "approve", "acme__eng")
    assert code == 0 and "the next pass drafts it" in out and "Nothing was drafted yet" in out
    with Store(me) as store:
        assert store.queue_item("acme__eng") is None
        events = store.events("human:approve_host", "acme__eng")
        assert len(events) == 1 and json.loads(events[0]["data"]) == {"host": "it.whatjobs.com"}
    assert (me / "tracker.csv").read_bytes() == before
    assert (me / "loop.json").read_bytes() == loop_before if loop_before is not None else not (me / "loop.json").exists()   # no --host: loop.json untouched
    assert not (me / "applications").exists() or not list((me / "applications").iterdir())


def test_queue_approve_host_appends_to_trusted_hosts_atomically_and_keeps_other_keys(tmp_path, capsys):
    me = sim.make_me(tmp_path)
    (me / "loop.json").write_text(json.dumps({"min_fit": 70, "trusted_hosts": ["startup.jobs"], "ladders": {"drafter": ["codex"]}}))
    held(me)
    code, out = go(capsys, "queue", "--me", str(me), "approve", "acme__eng", "--host")
    assert code == 0 and "trusted_hosts" in out
    data = json.loads((me / "loop.json").read_text())
    assert data["trusted_hosts"] == ["startup.jobs", "it.whatjobs.com"] and data["min_fit"] == 70 and data["ladders"] == {"drafter": ["codex"]}
    assert not list(me.glob("loop.json.*")) and not list(me.glob(".loop*"))                 # the temp file was replaced into place
    held(me, "beta__eng", "it.whatjobs.com")
    go(capsys, "queue", "--me", str(me), "approve", "beta__eng", "--host")
    assert json.loads((me / "loop.json").read_text())["trusted_hosts"] == ["startup.jobs", "it.whatjobs.com"]     # no duplicate
    assert go(capsys, "config", "--me", str(me))[0] == 0


def test_queue_approve_refuses_anything_that_is_not_a_held_back_host_and_an_unknown_key(tmp_path, capsys):
    me = sim.make_me(tmp_path)
    held(me, "ready__eng", kind="needs_decision")
    with Store(me) as store:
        store.queue_put("plain__eng", "ready", "x", {"title": "Plain"})
    for key in ("ready__eng", "plain__eng"):
        code, out = go(capsys, "queue", "--me", str(me), "approve", key)
        assert code == 2 and "nothing to approve" in out and key in out
    code, out = go(capsys, "queue", "--me", str(me), "approve", "nope__nope")
    assert code == 2 and "no queue entry 'nope__nope'" in out
    with Store(me) as store:
        assert store.events("human:approve_host") == [] and store.queue_item("plain__eng") is not None and store.queue_item("ready__eng") is not None


def test_queue_approve_host_with_a_bad_host_changes_nothing(tmp_path, capsys):
    me = sim.make_me(tmp_path)
    held(me, host="10.0.0.1")
    code, out = go(capsys, "queue", "--me", str(me), "approve", "acme__eng", "--host")
    assert code == 2 and "trusted_hosts" in out
    with Store(me) as store:
        assert store.queue_item("acme__eng") is not None and store.events("human:approve_host") == []


def test_queue_list_show_and_status_tell_the_exact_command(tmp_path, capsys):
    me = sim.make_me(tmp_path)
    held(me)
    for argv in (("queue", "--me", str(me), "list"), ("queue", "--me", str(me), "show", "acme__eng"), ("status", "--me", str(me))):
        code, out = go(capsys, *argv)
        assert code == 0 and "jobloop queue approve acme__eng" in out

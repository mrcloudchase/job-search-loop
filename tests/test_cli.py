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
    assert len(sub.choices) == 10 and all(c.help for c in sub._choices_actions)

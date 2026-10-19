"""Whole-loop simulation: the real engine (`engine.runner.run_pass`) and all thirteen real stages, composed.

Nobody tests the stages together anywhere else. Here a candidate's folder holds only a profile and a few postings; the network and
the container are stand-ins behind the stages' documented hooks, and the harness-interface server (tests/sim_loop.py `LoopServer`)
answers each request as the role it plays. Each test is a scenario; between passes the simulated person acts through the same
APIs the real person's commands use, and the clock moves.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import timedelta

import pytest

import sim_loop as S
from jobloop import archive, cli, tracker
from jobloop.engine import snapshot
from jobloop.stages import all_stages

K1, K2, K3, K4 = (S.app_key(k) for k in ("sim1", "sim2", "sim3", "sim4"))


@pytest.fixture
def loop(tmp_path, monkeypatch):
    return S.Loop(tmp_path, monkeypatch)


def profile_unchanged(loop) -> bool:
    return (loop.me / "profile.md").read_bytes() == loop.profile_bytes


def statuses(loop) -> dict[str, str]:
    return {r.key: r.status for r in loop.rows()}


def state_of(loop, posting: str) -> str:
    with loop.store() as store:
        snap = snapshot.take(loop.me, store, loop.cfg, loop.clock())
        return snap.state_of(posting)


def requests_text(server, role: str | None = None) -> list[str]:
    return [json.dumps(b) for b, r in zip(server.requests, server.roles) if role in (None, r)]


def cli_out(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


# ───────────────────────────── 1. the first pass ─────────────────────────────
def test_a_first_pass_from_a_profile_and_postings_to_a_ready_queue(loop, capsys):
    result = loop.run_pass()
    assert result.terminal == "success" and result.violations == [] and result.digest_path.exists()
    # triage ranked all four, one is vetoed by its gate
    assert [state_of(loop, k) for k in ("sim1", "sim2", "sim3", "sim4")] == ["drafted", "drafted", "vetoed", "shortlisted"]
    fit3 = json.loads(next((loop.me / "fit").glob("sim3-*.json")).read_text())
    assert fit3["shortlisted"] is False and any("citizenship" in v for v in fit3["vetoes"]) and len(fit3["panel"]) == 2
    # research wrote a record for every shortlisted posting (never for the vetoed one), claims verified by the loop's own fetch
    research = {p.stem: json.loads(p.read_text()) for p in (loop.me / "research").glob("*.json")}
    assert set(research) == {K1, K2, K4} and research[K2]["claims"] and research[K2]["skipped"] == ""
    # draft: at most max_new_drafts (2), the best fit first, never the vetoed one; the tracker holds drafted rows only
    assert statuses(loop) == {K2: "drafted", K1: "drafted"}
    assert not (loop.me / "applications" / K3).exists() and not (loop.me / "applications" / K4).exists()
    info = json.loads((loop.me / "applications" / K2 / "draft.json").read_text())
    assert info["first_pass"] and info["harnesses"]["drafter"] != info["harnesses"]["reviewer"] and info["judge"]["mean"] == 8.0
    assert info["gaps"] == [] and json.loads((loop.me / "applications" / K1 / "draft.json").read_text())["gaps"] == ["Scala"]
    # package: ready entries with the PDFs, the editable copies, the checklist and the form answers
    queue = loop.queue()
    assert queue[K1]["status"] == queue[K2]["status"] == "ready" and K3 not in queue and K4 not in queue
    folder = loop.me / "queue" / K1
    assert {p.name for p in folder.iterdir()} == {"cv.pdf", "cover.pdf", "queue.json", "README.md", "edit-here", "original", "form-answers.txt"}
    answers = (folder / "form-answers.txt").read_text()
    assert "FOR YOU: What is your gender?" in answers and "orchestrate them with Apache Airflow" in answers
    assert "jobloop tracker set" in (folder / "README.md").read_text() and "Scala" in (folder / "README.md").read_text()
    # the digest lists the queue, and the invariants held
    digest = loop.digest()
    assert f"`{K1}`" in digest and f"`{K2}`" in digest and "## Waiting for you" in digest and "never marks applied" in digest
    assert profile_unchanged(loop)
    code, out = cli_out(capsys, "queue", "--me", str(loop.me), "list")
    assert code == 0 and K1 in out and K2 in out


def test_a_second_pass_drafts_the_next_posting_and_nothing_twice(loop):
    loop.run_pass()
    first = json.loads((loop.me / "applications" / K2 / "draft.json").read_text())
    loop.clock.advance(hours=1)
    result = loop.run_pass()
    assert result.violations == [] and statuses(loop) == {K2: "drafted", K1: "drafted", K4: "drafted"}
    drafts = [a for a in loop.attempts("draft") if a["terminal"] == "success"]
    assert sorted(a["item"] for a in drafts) == sorted([K1, K2, K4])
    assert json.loads((loop.me / "applications" / K2 / "draft.json").read_text()) == first          # nothing redrafted
    assert [a["item"] for a in loop.attempts("package") if a["terminal"] == "success"].count(K1) == 1
    loop.clock.advance(hours=1)
    assert loop.run_pass().terminal == "no_op"                       # nothing left to do: a pass that finds nothing says so
    assert len([a for a in loop.attempts("draft") if a["terminal"] == "success"]) == 3


# ───────────────────────────── 2. the person acts ─────────────────────────────
def act_applied_and_interview(loop):
    """The person submits both applications; one went quiet, the other has an interview ahead."""
    loop.clock.advance(days=1)
    loop.person(K1, "applied", days_ago=12)
    loop.person(K2, "applied", days_ago=5)
    archive.record_stage(loop.me, K2, on=loop.clock().date() + timedelta(days=3), stage="technical", format="video", outcome="pending")
    loop.person(K2, "interview")


def test_the_loop_follows_what_the_person_does(loop, capsys):
    loop.run_pass()
    act_applied_and_interview(loop)
    result = loop.run_pass()                     # (the third posting is drafted in this pass too: the person's rows are exactly what they set)
    assert result.violations == [] and statuses(loop) == {K2: "interview", K1: "applied", K4: "drafted"}
    # a follow-up draft for the quiet applied row, never sent; the interview pack for the stage the person logged
    follow = [p for p in (loop.me / "applications" / K1).glob("followup_*.md")]
    assert len(follow) == 1 and follow[0].read_text().startswith("Dear Brightwell Analytics team")
    assert [q for q in loop.queue("followup:") if loop.queue()[q]["status"] == "ready"]
    pack = loop.queue(f"interview:{K2}:")
    assert list(pack) == [f"interview:{K2}:technical"] and pack[f"interview:{K2}:technical"]["status"] == "ready"
    text = (loop.me / "applications" / K2 / "interview_prep_technical.md").read_text()
    assert "## 2. STAR answers" in text and "hybrid policy" in text and "jobloop mock" in text
    # the queue entries for the two applications the person moved on are closed; nothing the person owns was written by the loop
    assert loop.queue()[K1]["status"] == loop.queue()[K2]["status"] == "done"
    # the offer: terms pasted by the person -> analysis and negotiation prep, an offer queue entry
    loop.clock.advance(days=2)
    loop.person(K2, "offer")
    (loop.me / "offers").mkdir()
    (loop.me / "offers" / f"{K2}.md").write_text("Base pay is 70000 EUR per year.\nStart date: 2027-01-04.\nHybrid with 2 days on site.\n")
    loop.run_pass()
    folder = loop.me / "applications" / K2
    assert (folder / "offer_analysis.md").read_text().rstrip().endswith("Your decision.") and (folder / "negotiation_prep.md").exists()
    assert loop.queue(f"offer:{K2}")[f"offer:{K2}"]["status"] == "ready"
    assert statuses(loop)[K2] == "offer" and profile_unchanged(loop)
    # and nothing is analysed or drafted twice
    loop.clock.advance(hours=1)
    count = len(loop.attempts())
    loop.run_pass()
    new = loop.attempts()[count:]
    assert not [a for a in new if a["stage"] in ("offer", "interview", "followup", "draft", "package") and a["terminal"] == "success"], new
    code, out = cli_out(capsys, "status", "--me", str(loop.me))
    assert code == 0 and "applied 2" in out and "interviews 1" in out and "offers 1" in out


# ───────────────────────────── 3. the loop learns ─────────────────────────────
def test_the_loop_learns_from_an_edit_and_rolls_back_a_playbook_that_makes_things_worse(tmp_path, monkeypatch, capsys):
    loop = S.Loop(tmp_path, monkeypatch, config={"rollback_after": 1})
    loop.run_pass()
    assert loop.server.style_seen == []
    # the person edits a delivered CV: a line the profile does not back
    edit = loop.me / "queue" / K2 / "edit-here" / "cv.tex"
    delivered = edit.read_text()
    edit.write_text(delivered.replace("\\end{itemize}", "\\item Led a team of 12 engineers at Contoso Labs.\n\\end{itemize}"))
    assert loop.queue(K2)[K2]["status"] == "ready"
    loop.clock.advance(hours=25)
    loop.server.judge_overall = 3                      # whatever is drafted under the next playbook is judged worse
    loop.run_pass()
    with loop.store() as store:
        versions = {v["version"]: v["status"] for v in store.playbook_versions()}
        pending = [p for p in store.proposals("pending") if p["kind"] == "profile_fact"]
        learned = [json.loads(e["data"]) for e in store.events("learn")]
    assert versions == {1: "superseded", 2: "active"} and learned[0]["version"] == 2
    rule = "Lead with the result and keep every paragraph short."
    assert (loop.me / "playbook" / "v0002" / "style.md").read_text().strip() == f"- {rule}"
    # the next draft under the new playbook carries the rule in the drafter's brief; the first drafts never had it
    assert loop.server.style_seen and all(rule in s for s in loop.server.style_seen) and statuses(loop)[K4] == "drafted"
    drafter_calls = [b for b in loop.server.seen("drafter") if "WRITING STYLE RULES" in b["instructions"]]
    assert drafter_calls and all(rule in b["instructions"] for b in drafter_calls)
    # the edit's new fact is a proposal for the person: the profile is untouched until they accept it
    assert len(pending) == 1 and "Contoso Labs" in pending[0]["payload"] and profile_unchanged(loop)
    # ... and the person's acceptance works (the only path that writes the profile)
    code, out = cli_out(capsys, "proposals", "--me", str(loop.me), "accept", str(pending[0]["id"]))
    assert code == 0, out
    assert "Contoso Labs" in (loop.me / "profile.md").read_text() and not profile_unchanged(loop)
    # a day later the new version's drafts are measurably worse than its parent's: rolled back, rule gone from the next brief
    loop.profile_bytes = (loop.me / "profile.md").read_bytes()
    loop.add_postings("sim7")
    loop.clock.advance(hours=25)
    seen = len(loop.server.style_seen)
    result = loop.run_pass()
    with loop.store() as store:
        versions = {v["version"]: v["status"] for v in store.playbook_versions()}
        rolled = [json.loads(e["data"]) for e in store.events("playbook_rollback")]
    assert versions == {1: "active", 2: "rolled_back"} and rolled and "worse than v1" in rolled[0]["line"]
    assert statuses(loop)[S.app_key("sim7")] == "drafted" and len(loop.server.style_seen) == seen       # drafted under v1: no style rule
    assert result.violations == [] and "rolled back" in loop.digest()


# ───────────────────────────── 4. failures, injected across the whole loop ─────────────────────────────
def drafter_models(loop) -> list[str]:
    return [b["model"] for b in loop.server.seen("drafter")]


def test_a_harness_that_returns_500_once_costs_one_stage_item_one_rest_and_nothing_else(tmp_path, monkeypatch):
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=("sim1", "sim2"))
    loop.server.inject(lambda body, role: role == "fit", "500")
    first = loop.run_pass()
    assert first.violations == [] and first.digest_path.exists()
    triage = loop.attempts("triage")
    assert [a["terminal"] for a in triage] == ["blocked"] and "HTTP 500" in triage[0]["failures"]
    assert state_of(loop, "sim1") == state_of(loop, "sim2") == "new" and not list((loop.me / "fit").glob("*.json"))      # nothing half-written
    others = {a["stage"] for a in loop.attempts() if a["terminal"] in ("success", "no_op")}
    assert others == {"learn", "upskill", "discover", "boards", "maintain"}                # every other stage still did its work
    assert "`triage`" in loop.digest() and "HTTP 500" in loop.digest()                     # the digest says what failed
    loop.clock.advance(hours=1)
    loop.run_pass()
    assert len(loop.attempts("triage")) == 1                                              # resting: not retried inside the backoff
    loop.clock.advance(hours=7)
    loop.run_pass()
    assert statuses(loop) == {K2: "drafted", K1: "drafted"} and loop.queue()[K2]["status"] == "ready"
    assert [a["terminal"] for a in loop.attempts("triage")] == ["blocked", "success"]


def test_a_drafter_that_never_validates_stalls_and_the_next_attempt_runs_on_the_next_harness(tmp_path, monkeypatch):
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=("sim2",), config={"best_of": 1})
    loop.server.inject(lambda body, role: role == "drafter" and body["model"] == S.DEEPSEEK, lambda body: {"answer": "done, nothing written"}, times=99)
    result = loop.run_pass()
    drafts = loop.attempts("draft")
    assert [a["terminal"] for a in drafts] == ["stalled", "success"] and "cv.tex and cover.tex must both exist" in drafts[0]["failures"]
    models = drafter_models(loop)
    assert models[:4] == [S.DEEPSEEK] * 4 and set(models[4:]) == {S.CODEX}          # the first ladder harness got its rounds, then the next harness
    assert drafts[0]["harness"] == S.DEEPSEEK and drafts[1]["harness"] == S.CODEX
    assert statuses(loop) == {K2: "drafted"} and result.violations == []
    assert [a["item"] for a in drafts].count(K2) == 2 and len([a for a in drafts if a["terminal"] == "success"]) == 1
    reviews = loop.server.seen("reviewer")
    assert reviews and all(b["model"] != S.CODEX for b in reviews)                     # the checker is never the maker's harness


@pytest.mark.parametrize("budget", [0.12, 0.45, 0.9, 1.3, 1.9, 2.5])
def test_a_pass_that_runs_out_of_budget_leaves_consistent_stores_and_the_next_pass_finishes_the_work(tmp_path, monkeypatch, budget):
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=("sim1", "sim2", "sim3"))
    loop.server.cost = 0.1
    starved = loop.run_pass(config=S.cfgmod.Config(**{**S.json.loads(loop.cfg.as_text()), "pass_budget_usd": budget}).validate())
    assert starved.digest_path.exists() and starved.violations == [] and starved.spent_usd <= budget + 0.1 + 1e-9
    assert starved.terminal in ("exhausted", "success", "no_op")
    check_consistent(loop)
    loop.clock.advance(days=1)
    final = loop.run_pass()
    assert final.violations == [] and statuses(loop) == {K2: "drafted", K1: "drafted"} and state_of(loop, "sim3") == "vetoed"
    check_consistent(loop)
    assert {k: v["status"] for k, v in loop.queue().items() if archive.is_key(k)} == {K1: "ready", K2: "ready"}


def check_consistent(loop):
    """Whatever a pass was cut short in: every tracker row is `drafted` with its archive complete, every attempt has a terminal state."""
    for row in loop.rows():
        folder = loop.me / "applications" / row.key
        assert row.status == "drafted" and (folder / "draft.json").exists() and (folder / "cv.pdf").exists() and (folder / "cover.tex").exists()
    assert all(a["terminal"] in ("success", "no_op", "blocked", "stalled", "exhausted") for a in loop.attempts())
    for key, item in loop.queue().items():
        if archive.is_key(key):
            assert any(r.key == key for r in loop.rows()) and (loop.me / "queue" / key / "queue.json").exists()
    assert profile_unchanged(loop)


def test_an_unverified_source_host_is_a_decision_for_the_person_and_nothing_is_drafted(tmp_path, monkeypatch, capsys):
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=("sim6", "sim2"))
    key = S.app_key("sim6")
    loop.run_pass()
    loop.clock.advance(hours=1)
    loop.run_pass()
    assert statuses(loop) == {K2: "drafted"} and not (loop.me / "applications" / key).exists()
    entry = loop.queue(key)[key]
    assert entry["status"] == "needs_decision" and entry["kind"] == "unverified_source_host" and "sketchworks-careers.example" in entry["reason"]
    assert [a["terminal"] for a in loop.attempts("draft") if a["item"] == key] == ["blocked"]               # asked once, not again every pass
    assert not [b for b in loop.server.seen("drafter") if "Sketch Works" in json.dumps(b)]
    assert "unverified source host" in loop.digest()
    code, out = cli_out(capsys, "queue", "--me", str(loop.me), "done", key)          # the person has dealt with it (applied elsewhere, or no)
    assert code == 0
    before = len(loop.attempts("draft"))
    for _ in range(3):
        loop.clock.advance(days=2)
        loop.run_pass()
    assert len(loop.attempts("draft")) == before and loop.queue(key)[key]["status"] == "done"      # and the loop does not ask again


def test_the_research_harness_never_receives_any_profile_text_in_any_request(loop):
    loop.run_pass()
    act_applied_and_interview(loop)
    loop.run_pass()
    seen = [json.dumps(b) for b, r in zip(loop.server.requests, loop.server.roles) if r in ("research", "resources") or b["model"] == S.RESEARCHER]
    assert len(seen) >= 7 and any("Northwind Analytics" in s for s in seen)                # company research, the interview's, and upskill's
    for text in seen:
        assert not [m for m in S.PROFILE_MARKERS if m in text], text[:300]
    # and the sandbox is widened to exactly one employer's hosts for a turn, then narrowed again
    widened = [hosts for _, hosts in loop.server.sandbox_log if hosts]
    assert widened and all(h.split(".", 1)[1] in S.DOMAINS.values() for hosts in widened for h in hosts if h.count(".") > 1) and loop.server.sandbox["research"] == []


def test_a_hostile_posting_is_data_and_changes_nothing(tmp_path, monkeypatch):
    clean = S.Loop(tmp_path / "clean", monkeypatch, posting_keys=("sim2",))
    clean.run_pass()
    hostile = S.Loop(tmp_path / "hostile", monkeypatch, posting_keys=("sim5",))
    hostile.run_pass()
    key = S.app_key("sim5")
    assert statuses(hostile) == {key: "drafted"} and hostile.queue(key)[key]["status"] == "ready"
    shape = lambda loop: [(a["stage"], a["terminal"]) for a in loop.attempts()]
    assert shape(hostile) == shape(clean)                                               # exactly the same steps, to the same ends
    with hostile.store() as store:
        assert store.proposals() == [] and [e for e in store.events("invariant")] == []
    for body in hostile.server.requests:
        everything = json.dumps(body)
        assert S.HOSTILE_COMMENT_MARK not in everything and "Haskell" not in everything          # comments never leave the host
        sent = body["instructions"] + "\n".join(f["text"] for f in body["input"] if f["type"] == "input_text")
        assert S.HOSTILE_MARK not in sent, sent[:400]                                          # the title is data in a file, never in a prompt
    drafted = (hostile.me / "applications" / key / "cover.tex").read_text()
    assert "PhD" not in drafted and "Haskell" not in drafted
    assert (hostile.me / "applications" / key / "job_posting.md").read_bytes().decode().count(S.HOSTILE_COMMENT_MARK) == 1     # archived verbatim


def test_a_kill_in_the_middle_of_a_draft_leaves_consistent_stores_and_the_next_pass_resumes_without_drafting_twice(tmp_path, monkeypatch):
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=("sim1", "sim2"))
    kill = loop.server.inject(lambda body, role: role == "drafter" and len(loop.server.seen("drafter")) >= 2, "raise")
    first = loop.run_pass()
    assert kill.hits == 1 and first.digest_path.exists() and first.violations == []
    check_consistent(loop)
    assert [a["terminal"] for a in loop.attempts("draft")][-1] in ("exhausted", "blocked") and len(statuses(loop)) < 2
    loop.clock.advance(hours=1)
    second = loop.run_pass()
    assert second.violations == [] and statuses(loop) == {K2: "drafted", K1: "drafted"}
    check_consistent(loop)
    drafted = [a["item"] for a in loop.attempts("draft") if a["terminal"] == "success"]
    assert sorted(drafted) == sorted([K1, K2])                                              # each application drafted once
    assert len(list(loop.rows())) == 2 and [r.key for r in loop.rows()].count(K1) == 1


@pytest.mark.parametrize("where", ["tracker", "draft_json"])
def test_a_kill_between_the_archive_and_the_tracker_row_is_repaired_by_the_next_pass(tmp_path, monkeypatch, where):
    """The commit of a draft is several writes (documents, tracker row, draft.json). A process killed between any two of them must
    not leave an application nobody will ever finish."""
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=("sim2",))
    real = {"tracker": tracker.record_drafted, "draft_json": archive.write_atomically}
    armed = {"on": True}

    def kill_tracker(*a, **k):
        if armed["on"]:
            armed["on"] = False
            raise KeyboardInterrupt
        return real["tracker"](*a, **k)

    def kill_draft_json(path, data):
        if armed["on"] and Path(path).name == "draft.json":
            armed["on"] = False
            raise KeyboardInterrupt
        return real["draft_json"](path, data)

    monkeypatch.setattr(tracker, "record_drafted", kill_tracker) if where == "tracker" else monkeypatch.setattr(archive, "write_atomically", kill_draft_json)
    first = loop.run_pass()
    assert first.digest_path.exists() and not armed["on"] and profile_unchanged(loop)
    loop.clock.advance(hours=1)
    second = loop.run_pass()
    loop.clock.advance(hours=1)
    loop.run_pass()
    assert second.violations == [] and statuses(loop) == {K2: "drafted"}, "the half-committed application was never finished"
    check_consistent(loop)
    assert loop.queue()[K2]["status"] == "ready"
    assert len([a for a in loop.attempts("draft") if a["terminal"] == "success"]) == 1
    assert len(loop.server.seen("drafter")) <= 6                         # repaired from the archive, not drafted a second time


# ───────────────────────────── 5. the funnel, the CLI and the policy order ─────────────────────────────
def populated(loop):
    """Three passes of a search in motion: two applications moved on by the person, an offer with its terms, a new posting."""
    loop.run_pass()
    act_applied_and_interview(loop)
    loop.run_pass()
    loop.clock.advance(days=1)
    loop.person(K2, "offer")
    (loop.me / "offers").mkdir()
    (loop.me / "offers" / f"{K2}.md").write_text("Base pay is 70000 EUR per year.\nStart date: 2027-01-04.\nHybrid with 2 days on site.\n")
    loop.add_postings("sim7")
    loop.clock.advance(hours=2)


def funnel_numbers(out: str) -> dict:
    import re
    return {"postings": int(re.search(r"postings (\d+)", out)[1]), "rows": int(re.search(r"tracker rows (\d+)", out)[1]),
            "states": {k: int(v) for k, v in re.findall(r"(\w+) (\d+)", re.search(r"Postings by state: (.*)", out)[1])},
            "accepted": int(re.search(r"accepted \(queue-ready\) (\d+)", out)[1]), "applied": int(re.search(r"· applied (\d+)", out)[1]),
            "interviews": int(re.search(r"interviews (\d+)", out)[1]), "offers": int(re.search(r"offers (\d+)", out)[1])}


def test_the_funnel_numbers_in_status_and_the_digest_agree_with_the_stores(loop, capsys):
    populated(loop)
    loop.run_pass()
    code, out = cli_out(capsys, "status", "--me", str(loop.me))
    assert code == 0
    n = funnel_numbers(out)
    rows = loop.rows()
    with loop.store() as store:
        snap = snapshot.take(loop.me, store, loop.cfg, loop.clock())
        counted = {}
        for key in snap.postings:
            counted[snap.state_of(key)] = counted.get(snap.state_of(key), 0) + 1
        queued = {r["item"]: r["status"] for r in store.queue() if archive.is_key(r["item"])}
        waiting = [r["item"] for r in store.queue() if r["status"] != "done"]
    assert n["postings"] == len(snap.postings) == 5 and n["rows"] == len(rows) == 4 and n["states"] == counted
    assert counted == {"applied": 1, "offer": 1, "drafted": 2, "vetoed": 1}                    # the person's states are the tracker's own words
    assert n["applied"] == sum(1 for r in rows if r.status != "drafted") == 2
    assert n["interviews"] == sum(1 for r in rows if r.status in ("interview", "offer", "hired", "offer_declined")) == 1
    assert n["offers"] == sum(1 for r in rows if r.status in ("offer", "hired", "offer_declined")) == 1
    assert n["accepted"] == len(queued) == len(rows) and {r.key for r in rows} == set(queued)    # every drafted row was queued, nothing else is
    # the digest and `queue list` show exactly what is not done
    digest = loop.digest()
    for item in waiting:
        assert f"`{item}`" in digest
    assert not [k for k, v in queued.items() if v != "done" and f"`{k}`" not in digest]
    code, listing = cli_out(capsys, "queue", "--me", str(loop.me), "list")
    assert f"{len(waiting)} in the queue" in listing
    # the funnel only narrows
    ranked = [k for k, e in snap.postings.items() if e["status"] == "ranked"]
    researched = list((loop.me / "research").glob("*.json"))
    assert len(snap.postings) >= len(ranked) >= len(researched) >= len(rows)


def test_queue_commands_work_for_every_kind_of_entry_the_loop_makes(loop, capsys):
    populated(loop)
    loop.run_pass()
    items = loop.queue()
    kinds = {k.split(":")[0] if ":" in k else "application" for k in items}
    assert {"application", "followup", "interview", "offer", "upskill", "boards"} <= kinds
    for key in items:
        code, out = cli_out(capsys, "queue", "--me", str(loop.me), "show", key)
        assert code == 0 and out.strip(), key
        if key.startswith(("followup:", "interview:", "offer:")):
            assert "file:" in out and len(out) > 300                           # the draft, the pack or the analysis itself
    code, out = cli_out(capsys, "queue", "--me", str(loop.me), "show", "no:such")
    assert code == 2 and "no queue entry" in out                                 # a refusal in words, not a traceback
    code, out = cli_out(capsys, "queue", "--me", str(loop.me), "done", f"offer:{K2}")
    assert code == 0 and "applied" not in out and loop.queue(f"offer:{K2}")[f"offer:{K2}"]["status"] == "done"
    assert statuses(loop)[K2] == "offer"                                          # `queue done` never touches the tracker


def test_stage_proposals_and_tracker_commands_compose_with_the_loop(loop, capsys):
    loop.run_pass()
    loop.clock.advance(days=1)
    # the person's commands, as typed
    me = str(loop.me)
    assert cli_out(capsys, "tracker", "--me", me, "set", K1, "applied")[0] == 0
    code, out = cli_out(capsys, "stage", "--me", me, K1, "phone_screen", "--date", (loop.clock().date() + timedelta(days=2)).isoformat(), "--format", "phone")
    assert code == 0, out
    assert cli_out(capsys, "tracker", "--me", me, "set", K1, "interview")[0] == 0
    result = loop.run_pass()
    assert result.violations == [] and (loop.me / "applications" / K1 / "interview_prep_phone-screen.md").exists()
    assert loop.queue(f"interview:{K1}:phone-screen")                              # the stage the person logged is the stage prepared
    code, out = cli_out(capsys, "tracker", "--me", me, "list")
    assert code == 0 and "interview" in out


def test_dry_run_lists_what_a_pass_would_do_in_the_policy_order_and_a_run_does_it(loop, capsys):
    populated(loop)
    loop.clock.advance(hours=24)                                  # the daily stages are due again
    code, out = cli_out(capsys, "run", "--me", str(loop.me), "--dry-run")
    assert code == 0
    listed = [line.split()[0] for line in out.splitlines()[1:] if line.strip() and not line.startswith("  problem")]
    assert listed == ["learn", "offer", "triage", "discover", "maintain"], out          # furthest down the funnel first, daily chores last
    assert f"{K2}" in out and "terms are in" in out
    ranks = {s.spec.name: s.spec.rank for s in all_stages()}
    assert [ranks[s] for s in listed] == sorted((ranks[s] for s in listed), reverse=True)
    before = len(loop.attempts())
    code, out = cli_out(capsys, "run", "--me", str(loop.me), "--stage", "triage", "--stage", "offer")
    assert code == 0
    ran = [(a["stage"], a["terminal"]) for a in loop.attempts()[before:]]
    assert ran[0] == ("offer", "success") and ("triage", "success") in ran and {s for s, _ in ran} <= {"offer", "triage"}
    assert (loop.me / "applications" / K2 / "offer_analysis.md").exists()
    code, out = cli_out(capsys, "run", "--me", str(loop.me), "--stage", "nonesuch")
    assert code == 2 and "unknown stage" in out
    # a real run, as the person's cron job would make it, does what the dry run said
    code, out = cli_out(capsys, "run", "--me", str(loop.me))
    assert code == 0 and "Pass" in loop.digest()


# ───────────────────────────── 6. more of the loop composed ─────────────────────────────
def test_a_search_finds_the_posting_and_the_same_pass_carries_it_to_a_ready_queue_entry(tmp_path, monkeypatch):
    """From nothing but a profile: discover finds it on a board, triage scores it, research, draft and package follow, in one pass."""
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=(), feed=("sim8",))
    result = loop.run_pass()
    key = S.app_key("sim8")
    order = [a["stage"] for a in loop.attempts() if a["terminal"] == "success"]
    assert order.index("discover") < order.index("triage") < order.index("research") < order.index("draft") < order.index("package")
    assert statuses(loop) == {key: "drafted"} and loop.queue(key)[key]["status"] == "ready" and result.violations == []
    with loop.store() as store:
        yields = [json.loads(e["data"]) for e in store.events("yield")]
        health = [json.loads(e["data"])["status"] for e in store.events("health")]
    assert yields and sum(y["new"] for y in yields) == 1 and set(health) == {"ok"}               # yield for `learn`, health for the boards stage
    assert profile_unchanged(loop)


def test_a_posting_with_a_close_deadline_is_researched_and_drafted_before_a_better_fit(tmp_path, monkeypatch):
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=("sim1", "sim2", "sim4"), config={"max_new_drafts": 1})
    from jobloop import state
    with state.transaction(loop.me) as store:
        store["sim4"]["deadline"] = (loop.clock().date() + timedelta(days=2)).isoformat()          # fit 67 against sim2's 77
    loop.run_pass()
    assert statuses(loop) == {K4: "drafted"} and (loop.me / "research" / f"{K4}.json").exists()
    assert [a["item"] for a in loop.attempts("research")][0] == K4
    assert loop.queue(K4)[K4]["deadline"] == (loop.clock().date() + timedelta(days=2)).isoformat()
    assert "deadline 2026-10-21" in loop.digest()


def test_a_board_the_person_asked_for_is_built_switched_off_and_the_person_can_accept_the_proposal(tmp_path, monkeypatch, capsys):
    from test_addboard import HOSTS, URL as ENTRY_URL, good_runner
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=(), config={"new_boards": [{"name": "nordlys-jobs", "url": ENTRY_URL, "hosts": list(HOSTS), "query": "engineer"}]})
    monkeypatch.setattr(S.boards, "RUNNER", good_runner)
    assert loop.run_pass().violations == []
    from jobloop import search
    assert "nordlys-jobs" not in search.enabled_boards(loop.me)                         # the loop never switches a board on
    entry = loop.queue("board:nordlys-jobs")["board:nordlys-jobs"]
    assert entry["status"] == "needs_decision"
    code, out = cli_out(capsys, "proposals", "--me", str(loop.me), "list")
    assert code == 0 and "nordlys-jobs" in out
    with loop.store() as store:
        (pid,) = [p["id"] for p in store.proposals("pending") if p["kind"] == "add_board"]
    code, out = cli_out(capsys, "proposals", "--me", str(loop.me), "accept", str(pid))
    assert code == 0, out                                                                  # accepting means "I read it": the board stays off
    assert "nordlys-jobs" not in search.enabled_boards(loop.me)


def test_a_stale_application_is_put_to_the_person_and_only_the_person_resolves_it(loop, capsys):
    loop.run_pass()
    loop.clock.advance(days=1)
    loop.person(K1, "applied", days_ago=70)
    loop.run_pass()
    with loop.store() as store:
        (stale,) = [p for p in store.proposals("pending") if p["kind"] == "stale_resolution"]
    assert statuses(loop)[K1] == "applied" and loop.queue(f"stale:{K1}")[f"stale:{K1}"]["status"] == "needs_decision"      # asked, not decided
    loop.clock.advance(days=1)
    loop.run_pass()
    with loop.store() as store:
        assert len([p for p in store.proposals() if p["kind"] == "stale_resolution"]) == 1                  # not asked again every pass
    assert statuses(loop)[K1] == "applied"
    code, out = cli_out(capsys, "proposals", "--me", str(loop.me), "accept", str(stale["id"]), "--choice", "no_response")
    assert code == 0, out
    assert statuses(loop)[K1] == "no_response" and loop.queue(f"stale:{K1}")[f"stale:{K1}"]["status"] == "done"
    loop.clock.advance(days=1)
    assert loop.run_pass().violations == []                                                # and the loop carries on with the rest


def test_two_passes_never_overlap(loop):
    with archive.locked(loop.me / "engine" / "pass", timeout=0.0):
        second = loop.run_pass()
    assert second.terminal == "blocked" and "another pass is running" in second.reason and loop.server.requests == []
    assert loop.run_pass().terminal == "success"


def test_a_long_search_settles_every_posting_and_an_idle_loop_asks_the_harness_for_nothing(tmp_path, monkeypatch):
    """Thirty-odd passes, three days apart: nothing is proposed forever, follow-ups stop at two, an unreachable posting is retried and
    one that stays down is given up on, a closed one expires, and a finished search costs nothing."""
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=("sim1", "sim2", "sim9", "sim10", "sim11"))
    loop.run_pass()
    assert [state_of(loop, k) for k in ("sim9", "sim10", "sim11")] == ["unreachable", "unreachable", "expired"]
    loop.person(K1, "applied", days_ago=0)
    asked = []
    for day in range(1, 34):
        loop.clock.advance(days=3)
        if day == 2:
            loop.down.discard("sim9")                                   # the site comes back up
        before = len(loop.server.requests)
        result = loop.run_pass()
        assert result.violations == [] and result.terminal in ("success", "no_op"), (day, result.terminal, result.reason)
        asked.append(len(loop.server.requests) - before)
    assert state_of(loop, "sim9") in ("drafted", "shortlisted") and statuses(loop).get(S.app_key("sim9")) == "drafted"
    assert state_of(loop, "sim10") == "gave_up" and state_of(loop, "sim11") == "expired"
    assert len(list((loop.me / "applications" / K1).glob("followup_*.md"))) == 2                      # two follow-ups, never a third
    drafted = [a["item"] for a in loop.attempts("draft") if a["terminal"] == "success"]
    assert sorted(drafted) == sorted({K1, K2, S.app_key("sim9")}) and len(drafted) == 3              # each application drafted once
    with loop.store() as store:
        assert len([p for p in store.proposals() if p["kind"] == "stale_resolution"]) == 1            # K1 went stale, was put to the person once
        assert store.db.execute("SELECT COUNT(*) FROM queue WHERE item LIKE 'followup:%'").fetchone()[0] == 2
        assert store.db.execute("SELECT COUNT(*) FROM queue WHERE item LIKE 'upskill:%'").fetchone()[0] >= 3          # one report a week ...
        assert store.db.execute("SELECT COUNT(*) FROM queue WHERE item LIKE 'upskill:%' AND status='ready'").fetchone()[0] == 1   # ... and only the latest waits
    assert statuses(loop)[K1] == "applied" and profile_unchanged(loop)
    # a search with nothing left to do costs no harness turn at all, apart from the weekly skills report
    paid = {a["stage"] for a in loop.attempts()[-18:] if a["cost_usd"]}
    assert paid <= {"upskill"}, paid                                    # the weekly report is the only thing a finished search still buys
    assert loop.attempts()[-1]["terminal"] in ("success", "no_op")


def test_without_a_profile_the_loop_asks_for_one_and_runs_nothing_else_until_it_exists(tmp_path, monkeypatch, capsys):
    loop = S.Loop(tmp_path, monkeypatch, profile=False)
    result = loop.run_pass()
    assert [(a["stage"], a["terminal"]) for a in loop.attempts()] == [("onboard", "blocked")] and loop.server.requests == []
    assert result.violations == [] and loop.queue("__setup__")["__setup__"]["status"] == "needs_decision"
    code, out = cli_out(capsys, "status", "--me", str(loop.me))
    assert "MISSING" in out and "jobloop setup" in out
    code, out = cli_out(capsys, "queue", "--me", str(loop.me), "show", "__setup__")
    assert code == 0 and "jobloop setup" in out
    loop.clock.advance(hours=7)
    (loop.me / "profile.md").write_text(S.PROFILE)                  # the person's `jobloop setup`
    result = loop.run_pass()
    assert loop.queue("__setup__")["__setup__"]["status"] == "done" and statuses(loop) == {K2: "drafted", K1: "drafted"}
    assert result.violations == []


def test_the_persons_public_pages_become_proposals_and_only_their_accept_writes_the_profile(tmp_path, monkeypatch, capsys):
    from test_enrich import site
    loop = S.Loop(tmp_path, monkeypatch, config={"enrich_github": "https://github.com/alice-example", "enrich_site": "https://alice.example/"})
    monkeypatch.setattr(S.onboard, "FETCH", lambda named: site().fetch(named.hosts))
    result = loop.run_pass()
    assert result.violations == [] and profile_unchanged(loop)
    with loop.store() as store:
        found = [p for p in store.proposals("pending") if p["kind"] == "profile_enrichment"]
    assert len(found) == 2 and all(json.loads(p["payload"])["source_url"].startswith("https://") for p in found)
    code, out = cli_out(capsys, "proposals", "--me", str(loop.me), "list")
    assert code == 0 and "Rust command line tool" in out
    code, out = cli_out(capsys, "proposals", "--me", str(loop.me), "accept", str(found[0]["id"]))
    assert code == 0, out
    text = (loop.me / "profile.md").read_text()
    assert "Rust command line tool" in text and text.startswith(S.PROFILE[:200])                    # one additive, source-tagged bullet
    loop.profile_bytes = (loop.me / "profile.md").read_bytes()
    loop.clock.advance(days=8)
    assert loop.run_pass().violations == []
    with loop.store() as store:
        assert len([p for p in store.proposals() if p["kind"] == "profile_enrichment"]) == 2         # nothing proposed again, accepted or not
    assert profile_unchanged(loop)


def test_every_command_the_loop_tells_the_person_to_run_exists(loop, capsys, monkeypatch):
    """The queue, the digest and the packs name commands (`search boards enable`, `mock`, `stage`, `tracker set`, `queue done`...). Each one parses."""
    populated(loop)
    loop.run_pass()
    texts = [loop.digest()] + [p.read_text() for p in (loop.me / "queue").glob("*/README.md")] \
        + [p.read_text() for p in (loop.me / "applications").glob("*/interview_prep_*.md")] + [json.dumps(v) for v in loop.queue().values()]
    import re
    from jobloop import cli as cli_module
    commands = {m for t in texts for m in re.findall(r"jobloop ([a-z]+)", t.replace("`", " "))}
    known = {c for a in cli_module.build_parser()._actions if hasattr(a, "choices") and a.choices for c in a.choices}
    assert {"tracker", "queue", "mock", "search"} <= commands and commands <= known, commands - known
    code, out = cli_out(capsys, "search", "--me", str(loop.me), "boards", "list")
    assert code == 0 and "freehire" in out and "linkedin" in out
    code, out = cli_out(capsys, "search", "--me", str(loop.me), "boards", "enable", "linkedin")
    assert code == 1 and "terms" in out                                                  # a board with terms: the person acknowledges them, in words
    code, out = cli_out(capsys, "search", "--me", str(loop.me), "boards", "enable", "linkedin", "--acknowledge-terms")
    assert code == 0 and "linkedin is now on" in out
    # the mock interview: the pack for the stage is built; without a person at the terminal no mock is run
    import builtins

    def eof(*a, **k):
        raise EOFError
    monkeypatch.setattr(builtins, "input", eof)
    before = len(list((loop.me / "applications" / K2).glob("interview_prep_*.md")))
    code, out = cli_out(capsys, "mock", "--me", str(loop.me), K2)
    packs = sorted((loop.me / "applications" / K2).glob("interview_prep_*.md"))
    assert code == 0 and len(packs) == before + 1
    assert "using Airflow in production" in packs[-1].read_text()                  # built on the application's stored fit, found by its posting's name
    assert cli_out(capsys, "mock", "--me", str(loop.me), "no_such__key")[0] == 2


def test_a_reviewers_edits_are_applied_only_when_grounded_and_the_archive_holds_exactly_what_was_checked(tmp_path, monkeypatch):
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=("sim2",))
    good = {"file": "cv.tex", "old_string": "mentors two junior engineers", "new_string": "mentors two junior engineers through code review", "reason": "reframing"}
    bad = {"file": "cv.tex", "old_string": "Data engineer, 7 years building pipelines.", "new_string": "Data engineer, 12 years building pipelines.", "reason": "keyword match"}
    reply = lambda body: {"answer": json.dumps([good, bad]) + "\n\nMissed keywords and requirements: none.\nCompany and department angles: none.\n"
                                                 "Action-oriented reframing: one edit.\nTone and style: no issues."}
    loop.server.inject(lambda body, role: role == "reviewer", reply, times=99)
    loop.run_pass()
    folder = loop.me / "applications" / K2
    notes = json.loads((folder / "draft.json").read_text())["notes"]
    assert any("sent 2 edits; the loop applied 1 and rejected 1" in n for n in notes), notes
    cv = (folder / "cv.tex").read_text()
    assert "through code review" in cv and "7 years" in cv and "12 years" not in cv
    assert (folder / "cv.pdf").read_bytes().count(b"through code review") == 1              # the PDF is of the text that was checked after the edit
    assert statuses(loop) == {K2: "drafted"} and (folder / "review.md").exists()


def test_without_a_known_employer_domain_nothing_is_researched_and_the_letter_makes_no_company_claims(tmp_path, monkeypatch):
    loop = S.Loop(tmp_path, monkeypatch, posting_keys=("sim2",), config={"company_domains": {}})
    loop.run_pass()
    record = json.loads((loop.me / "research" / f"{K2}.json").read_text())
    assert record["claims"] == [] and record["domain"] == "" and "no employer domain" in record["skipped"]
    assert not loop.server.seen("research") and statuses(loop) == {K2: "drafted"}              # no researcher turn was ever made
    assert "no verified company research" in " ".join(json.loads((loop.me / "applications" / K2 / "draft.json").read_text())["notes"])
    assert loop.queue(K2)[K2]["status"] == "ready"


def test_a_person_can_switch_stages_off_and_the_rest_still_composes(tmp_path, monkeypatch):
    loop = S.Loop(tmp_path, monkeypatch, config={"disabled_stages": ["research", "discover", "upskill", "boards", "maintain", "learn"]})
    result = loop.run_pass()
    ran = {a["stage"] for a in loop.attempts()}
    assert ran == {"triage", "draft", "package"} and result.violations == []
    assert statuses(loop) == {K2: "drafted", K1: "drafted"} and not (loop.me / "research").exists()
    assert not loop.server.seen("research") and not loop.server.seen("resources")


def test_a_pass_past_its_deadline_starts_no_model_turn_and_still_writes_its_digest(tmp_path, monkeypatch):
    loop = S.Loop(tmp_path, monkeypatch, config={"pass_minutes": 1e-7})
    result = loop.run_pass()
    assert result.terminal == "exhausted" and result.reason.startswith("pass time") and result.digest_path.exists()
    assert loop.server.requests == [] and "ran out of time or money" in loop.digest()
    assert state_of(loop, "sim1") == "new" and statuses(loop) == {}


def test_what_the_loop_stored_joins_up_across_the_libraries_that_read_it_by_key(loop):
    """Postings, fits and applications are named three ways (store key, fit_key(posting), company__role). Each reader must find the others."""
    from jobloop import calibrate, upskill
    from jobloop.engine import learn as analysis
    loop.run_pass()
    loop.clock.advance(days=1)
    loop.person(K1, "applied", days_ago=5)
    loop.person(K1, "rejected", days_ago=1)
    loop.person(K2, "applied", days_ago=5)
    loop.person(K2, "interview", days_ago=3)
    loop.person(K2, "hired", days_ago=1)
    found, notes = calibrate.samples(loop.me, loop.rows())
    assert {s.key for s in found} == {K1, K2} and not [n for n in notes if "no saved fit" in n]          # calibration sees both outcomes
    jobs = upskill.load_jobs(loop.me)
    assert not jobs.needs_inference and jobs.tracked == 2 and {j.key for j in jobs.jobs if j.source == "tracked"} == {K1, K2}   # no inference, no double count
    with loop.store() as store:
        ev = analysis.evidence(loop.me, store, loop.rows())
    assert {(o.key, o.status) for o in ev.outcomes} >= {(K1, "rejected"), (K2, "hired")} and all(o.score is not None and o.band for o in ev.outcomes)
    assert ev.settled and ev.enough()


# ───────────────────────────── 7. chaos: a seeded random walk over everything above ─────────────────────────────
def audit(loop, expected: dict, *, settled: bool) -> None:
    """The invariants that must hold after every step; `settled` (after clean passes) also demands every application be whole and queued."""
    assert profile_unchanged(loop)
    rows = loop.rows()
    assert len({r.key for r in rows}) == len(rows)                                           # never two rows for one application
    for row in rows:
        assert row.status == expected.get(row.key, "drafted"), (row.key, row.status)         # the loop wrote `drafted` and nothing else
        assert (loop.me / "applications" / row.key / "outcome.md").exists() or not (loop.me / "applications" / row.key / "draft.json").exists()
    drafted = [a["item"] for a in loop.attempts("draft") if a["terminal"] == "success"]
    assert len(drafted) == len(set(drafted)), drafted                                        # never drafted twice
    for key in loop.queue():
        if archive.is_key(key):
            assert key in {r.key for r in rows}                                             # a queue entry for an application that does not exist
    for item in loop.attempts():
        assert item["terminal"] in ("success", "no_op", "blocked", "stalled", "exhausted")
    if settled:
        for row in rows:
            folder = loop.me / "applications" / row.key
            assert (folder / "draft.json").exists() and (folder / "cv.pdf").exists(), row.key
            assert row.key in loop.queue() and (loop.me / "queue" / row.key / "queue.json").exists()
        assert sorted(drafted) == sorted(r.key for r in rows)


def walk(root, monkeypatch, seed: int, steps: int, tally) -> None:
    import random
    rnd = random.Random(seed)
    loop = S.Loop(root, monkeypatch, posting_keys=("sim1", "sim2", "sim3", "sim4"), config={"max_new_drafts": 1})
    fresh = ["sim7", "sim8", "sim5"]
    expected: dict[str, str] = {}
    for step in range(steps):
        move = rnd.choice(["pass", "pass", "pass", "pass_500", "pass_kill", "tick", "tick", "apply", "apply", "interview", "offer", "reject", "addpost"])
        rows = {r.key: r.status for r in loop.rows()}
        if move == "tick":
            loop.clock.advance(hours=rnd.choice([1, 7, 25, 60]))
        elif move == "apply" and "drafted" in rows.values():
            key = rnd.choice([k for k, v in rows.items() if v == "drafted"])
            loop.person(key, "applied", days_ago=rnd.choice([0, 5, 12]))
            expected[key] = "applied"
        elif move == "interview" and "applied" in rows.values():
            key = rnd.choice([k for k, v in rows.items() if v == "applied"])
            archive.record_stage(loop.me, key, on=loop.clock().date() + timedelta(days=rnd.choice([-3, 2])), stage="technical", format="video",
                                 outcome=rnd.choice(["pending", "passed"]))
            loop.person(key, "interview")
            expected[key] = "interview"
        elif move == "offer" and "interview" in rows.values():
            key = rnd.choice([k for k, v in rows.items() if v == "interview"])
            loop.person(key, "offer")
            (loop.me / "offers").mkdir(exist_ok=True)
            (loop.me / "offers" / f"{key}.md").write_text("Base pay is 70000 EUR per year.\nHybrid with 2 days on site.\n")
            expected[key] = "offer"
        elif move == "reject" and any(v in ("applied", "interview") for v in rows.values()):
            key = rnd.choice([k for k, v in rows.items() if v in ("applied", "interview")])
            loop.person(key, "rejected")
            expected[key] = "rejected"
        elif move == "addpost" and fresh:
            loop.add_postings(fresh.pop(0))
        elif move.startswith("pass"):
            if move == "pass_500":
                loop.server.inject(lambda body, role: True, "500", times=1)
            elif move == "pass_kill":
                at = len(loop.server.requests) + rnd.randint(1, 8)
                loop.server.inject(lambda body, role, at=at: len(loop.server.requests) >= at, "raise", times=1)
            result = loop.run_pass()
            assert result.digest_path.exists() and result.violations == [], (seed, step, move, result)
            tally["500"] += sum(i.hits for i in loop.server.injections if i.what == "500")
            tally["kill"] += sum(i.hits for i in loop.server.injections if i.what == "raise")
            loop.server.injections.clear()
        tally[move] += 1
        audit(loop, expected, settled=False)
    # whatever happened, clean passes finish the work: everything shortlisted is drafted, whole and queued
    for _ in range(5):
        loop.clock.advance(days=1)
        result = loop.run_pass()
        assert result.violations == [] and result.digest_path.exists()
    audit(loop, expected, settled=True)
    for row in loop.rows():
        tally["reached " + row.status] += 1
    assert cli.main(["status", "--me", str(loop.me)]) == 0


def test_a_random_walk_of_passes_failures_kills_and_the_persons_acts_keeps_every_invariant(tmp_path, monkeypatch):
    from collections import Counter
    tally: Counter = Counter()
    for seed in range(8):
        walk(tmp_path / f"seed{seed}", monkeypatch, seed, 20, tally)
    assert tally["kill"] >= 2 and tally["500"] >= 2 and tally["reached applied"] and tally["reached rejected"] and tally["reached offer"] + tally["reached interview"], tally


def test_an_offer_without_terms_asks_once_and_waits_for_the_file(loop):
    loop.run_pass()
    loop.clock.advance(days=1)
    loop.person(K2, "applied", days_ago=3)
    loop.person(K2, "interview", days_ago=2)
    loop.person(K2, "offer")
    loop.run_pass()
    entry = loop.queue(f"offer:{K2}")[f"offer:{K2}"]
    assert entry["status"] == "needs_decision" and f"me/offers/{K2}.md" in entry["why"]
    for _ in range(2):                                           # asked once, not again every pass
        loop.clock.advance(days=1)
        loop.run_pass()
    assert [a["terminal"] for a in loop.attempts("offer")] == ["blocked"]
    (loop.me / "offers").mkdir()
    (loop.me / "offers" / f"{K2}.md").write_text("Base pay is 70000 EUR per year.\nHybrid with 2 days on site.\n")
    loop.clock.advance(days=1)
    loop.run_pass()
    assert loop.queue(f"offer:{K2}")[f"offer:{K2}"]["status"] == "ready" and (loop.me / "applications" / K2 / "offer_analysis.md").exists()
    # the person edits the terms (a counter-offer): the analysis is redone, the old one kept beside it
    (loop.me / "offers" / f"{K2}.md").write_text("Base pay is 75000 EUR per year.\nHybrid with 2 days on site.\n")
    loop.clock.advance(days=1)
    loop.run_pass()
    assert (loop.me / "applications" / K2 / "offer_analysis.previous.md").exists() and "75000" in (loop.me / "applications" / K2 / "offer_analysis.md").read_text()


def test_attempt_limits_count_failures_since_the_last_success(tmp_path):
    from jobloop.engine import policy
    from jobloop.engine.spec import Action, Spec
    from jobloop.engine.store import Store
    spec, action = Spec("probe", "g"), Action("probe", "*", "r")
    with Store(tmp_path) as store:
        stamp = lambda hours: (S.START + timedelta(hours=hours)).isoformat()
        for n, terminal in enumerate(["blocked", "blocked", "blocked", "success", "blocked"]):
            store.record_attempt(1, "probe", "*", "", "", stamp(n), stamp(n), terminal, 0, 0, ["it failed"], [])
        assert policy.allowed(action, spec, store, S.START + timedelta(hours=4, minutes=30))[0] is False       # one failure: rests the 6 hour backoff
        assert policy.allowed(action, spec, store, S.START + timedelta(hours=10, minutes=30))[0] is True        # ... and not the 24 hours three old failures would earn

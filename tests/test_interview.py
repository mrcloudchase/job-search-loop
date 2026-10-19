"""interview.py: the prep pack says nothing that is not on the paper, and the mock interview stays inside its rules."""
from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path

import pytest

from fake_server import FakeServer
from jobloop import archive, interview as I, tracker
from jobloop.turns import Api, Turns

FIX = Path(__file__).parent / "fixtures" / "interview"
PROFILE = (FIX / "profile.md").read_text()
POSTING = (FIX / "posting.md").read_bytes()
FIT = json.loads((FIX / "fit.json").read_text())
D = date(2026, 3, 2)
COMPANY, ROLE = "Brightwell Analytics", "Senior Data Engineer"

STAR_TEXT = {
    "situation": "At Marlow Retail Group the company ran 45 cron jobs with no monitoring and nobody owned them end to end.",
    "task": "I had to move them onto a single scheduler that the whole data team could watch and trust every morning.",
    "action": "I built the first Airflow deployment at the company and migrated the cron jobs onto it one by one, "
              "checking each run against the old output before I switched it off.",
    "result": "All of the jobs ran on Airflow with alerts, so a failure was seen by the team at once instead of by an analyst later.",
    "lesson": "Migrate in small verified steps and make failures visible to everyone."}
GOOD = {
    "star": [{"qid": "q1", **STAR_TEXT, "stub": "",
              "evidence": ["Built the first Airflow deployment at the company and migrated 45 cron jobs onto it."]}],
    "bridges": [
        {"gap": "Scala", "acknowledge": "I have not worked with Scala yet.",
         "adjacent": "I wrote data transformations in Python and SQL at the Delft Energy Cooperative.",
         "adjacent_quote": "Automated monthly consumption reports with Python and SQL",
         "learning_path": "I will learn it by porting one existing job and studying the language documentation."},
        {"gap": "Kubernetes", "acknowledge": "I have not run Kubernetes in production.",
         "adjacent": "I ship services in Docker containers.", "adjacent_quote": "Strong: Python, SQL, PostgreSQL, Apache Airflow, dbt, Kafka, BigQuery, Docker",
         "learning_path": "I would start with the official tutorials and deploy one small pipeline job to a test cluster."}],
    "consistency": [
        {"quote": "Cut the nightly rebuild of the delivery-performance mart from 6 hours to 55 minutes", "source": "cv",
         "probe": "How?", "prepare": "Explain the incremental dbt models and why the rebuild fell to 55 minutes."},
        {"quote": "migrated 45 cron jobs onto it", "source": "cover_letter", "probe": "Which jobs first?",
         "prepare": "Walk through the order in which the 45 jobs moved."}],
    "tough": [{"id": "why_leave", "answer": "I want to work on a data platform team with more scope."},
              {"id": "missing_skill", "answer": "I would learn it the way I learned Airflow, by migrating one real job."},
              {"id": "five_year", "answer": "I would like to be a tech lead."},
              {"id": "weakness", "answer": "I take on too much ownership, so I now delegate reviews."},
              {"id": "why_company", "answer": "Their engineering blog describes the platform I want to work on.", "hook": "c1"}],
    "ask": [{"question": "How is the data platform team organised?", "category": "team", "about": "team structure"},
            {"question": "What does the first quarter in the role look like?", "category": "role", "about": "first quarter"},
            {"question": "Which parts of the stack are you planning to change?", "category": "tech and growth", "about": "stack changes"},
            {"question": "How does the team handle on-call?", "category": "culture", "about": "on-call"},
            {"question": "Is the role hybrid and how many office days?", "category": "role", "about": "hybrid policy"},
            {"question": "How do engineers grow into tech lead?", "category": "tech and growth", "about": "career growth"}]}
CLAIM = I.VerifiedClaim("c1", "Brightwell runs a hybrid policy with two office days a week.", "https://example.com/careers", True, "hybrid policy")
UNVERIFIED = I.VerifiedClaim("c2", "Brightwell is about to be acquired.", "https://example.com/rumour", False, "acquisition")
PERSON = I.VerifiedClaim("p1", "Jo Vale leads the platform team.", "https://example.com/team", True, "role", professional=True)
PRIVATE = I.VerifiedClaim("p2", "Jo Vale's family lives in Utrecht.", "https://example.com/post", True, "family", professional=False)


def research(subject, name, company):
    return [CLAIM, UNVERIFIED] if subject == "company" else [PERSON, PRIVATE]


@pytest.fixture
def me(tmp_path):
    root = tmp_path / "me"
    root.mkdir()
    (root / "profile.md").write_text(PROFILE)
    return root


def archived(me, stages=()):
    name = archive.open_application(me, COMPANY, ROLE)
    archive.store_posting(me, name, POSTING)
    archive.store_document(me, name, "cv", FIX / "cv.txt")
    archive.store_document(me, name, "cover_letter", FIX / "cover_letter.txt")
    for stage in stages:
        archive.record_stage(me, name, **stage)
    return name


def context(me, **kw):
    return I.load_context(me, archived(me, **kw))


def checked(data, ctx, claims=(CLAIM,), fit=FIT, stage="technical"):
    qs = I.likely_questions(ctx, fit, PROFILE, stage)
    return I.check_pack(data, ctx, PROFILE, qs, [t for t, _ in I.gap_terms(fit, PROFILE, ctx.posting)], list(claims))


def failures_of(change, **kw):
    data = copy.deepcopy(GOOD)
    change(data)
    return "\n".join(checked(data, kw.pop("ctx"), **kw)[0])


# ── choosing the application ──
def rows(*specs):
    return [tracker.Row(company=c, role=r, status=s) for c, r, s in specs]


def test_no_target_practice_is_redirected():
    chosen, message = I.select_application(rows(("Acme", "Dev", "drafted"), ("Old", "Dev", "rejected")))
    assert chosen == [] and "generic practice is out of scope" in message


def test_company_match_cases_one_many_none():
    table = rows(("Acme BV", "Data Engineer", "applied"), ("Acme BV", "Analyst", "interview"), ("Other", "Dev", "applied"))
    one, _ = I.select_application(table, "other")
    assert [r.company for r in one] == ["Other"]
    many, message = I.select_application(table, "ACME bv")
    assert len(many) == 2 and "Data Engineer" in message and "Analyst" in message
    none, message = I.select_application(table, "Nowhere")
    assert none == [] and "track it first" in message
    assert len(I.select_application(table, "Acme BV", "analyst")[0]) == 1


def test_drafted_and_final_rows_never_offered():
    table = rows(("A", "r", "drafted"), ("B", "r", "applied"), ("C", "r", "interview"), ("D", "r", "offer"),
                 ("E", "r", "rejected"), ("F", "r", "hired"), ("G", "r", "withdrawn"))
    assert [r.company for r in I.live_rows(table)] == ["B", "C", "D"]
    assert I.LIVE == tracker.OPEN - {"drafted"}


def test_empty_tracker_asks_for_details():
    chosen, message = I.select_application([])
    assert chosen == [] and "--company" in message and "--posting" in message


# ── context ──
def test_context_loads_the_posting_the_submitted_documents_and_outcome(me):
    ctx = context(me, stages=[dict(on=D, stage="phone", feedback="Asked about Scala twice.", outcome="passed")])
    assert "Production pipelines" in ctx.posting and "PhD" not in ctx.posting          # the hidden comment is not the posting
    assert set(ctx.documents) == {"cv", "cover_letter"} and ctx.missing == ()
    assert ctx.stages[0].feedback == "Asked about Scala twice." and ctx.title == f"{COMPANY} — {ROLE}"


def test_missing_context_is_stated_plainly_and_a_posting_file_stands_in(me):
    key = archive.open_application(me, COMPANY, ROLE)
    assert I.load_context(me, key).missing == ("cv", "cover_letter", "posting")
    ctx = I.load_context(me, key, FIX / "posting.md")
    assert ctx.posting.startswith("# Senior Data Engineer") and ctx.missing == ("cv", "cover_letter")


def test_known_stage_info_not_requested_again(me):
    ctx = context(me, stages=[dict(on=D, stage="technical", format="video", people=("Jo Vale",))])
    asked = []
    got = I.collect_stage_details(ctx.stages, "technical", lambda q: asked.append(q) or "")
    assert got == {"date": "2026-03-02", "format": "video", "people": "Jo Vale"} and asked == []
    got = I.collect_stage_details((), "final", lambda q: asked.append(q) or "x", {"format": "phone"})
    assert len(asked) == 2 and got["format"] == "phone"


# ── likely questions ──
def test_prior_stage_feedback_ranked_first(me):
    ctx = context(me, stages=[dict(on=D, stage="phone", feedback="Too vague on streaming.")])
    qs = I.likely_questions(ctx, FIT, PROFILE, "technical")
    assert qs[0].source == "feedback" and "Too vague on streaming." in qs[0].text


def test_gap_requirements_outrank_strong_matches(me):
    qs = I.likely_questions(context(me), FIT, PROFILE, "technical")
    order = [q.text for q in qs]
    scala, kube = next(i for i, t in enumerate(order) if "Scala" in t), next(i for i, t in enumerate(order) if "Kubernetes" in t)
    python = next(i for i, t in enumerate(order) if "using Python" in t)
    assert scala < kube < python
    assert [q.source for q in qs[:2]] == ["gap", "gap"] and [q.id for q in qs] == [f"q{i}" for i in range(1, len(qs) + 1)]


def test_question_sources_priority_and_stage_templates(me):
    ctx = context(me, stages=[dict(on=D, stage="phone", feedback="Be concise.")])
    sources = [q.source for q in I.likely_questions(ctx, FIT, PROFILE, "final")]
    assert sources == sorted(sources, key=["feedback", "gap", "requirement", "stage"].index)
    assert any("salary" in q.text for q in I.likely_questions(ctx, FIT, PROFILE, "final round"))
    assert any("notice period" in q.text for q in I.likely_questions(ctx, FIT, PROFILE, "phone screen"))
    assert I.stage_kind("Technical interview") == "technical" and I.stage_kind("phone-screen") == "screen"


def test_without_a_fit_the_gaps_are_the_profiles_not_yet_skills_the_posting_names(me):
    assert I.gap_terms(None, PROFILE, context(me).posting) == [("Scala", "required"), ("Kubernetes", "required")]


# ── STAR answers ──
def test_good_pack_passes_every_check(me):
    failures, cut = checked(GOOD | {"ask": copy.deepcopy(GOOD["ask"])}, context(me))
    assert failures == [] and [item["question"] for item, _ in cut] == ["Is the role hybrid and how many office days?"]


def test_new_star_only_from_profile_facts(me):
    ctx = context(me)
    assert "none of the sources" in failures_of(lambda d: d["star"][0].update(result="Saved 90 percent of the cost."), ctx=ctx)
    assert "evidence" in failures_of(lambda d: d["star"][0].update(evidence=["Led a team of 30 engineers."]), ctx=ctx)
    assert "evidence" in failures_of(lambda d: d["star"][0].update(evidence=[]), ctx=ctx)
    assert "'Snowflake' is a name that is not in the profile" in failures_of(
        lambda d: d["star"][0].update(action=STAR_TEXT["action"] + " I tuned Snowflake costs too."), ctx=ctx)


def test_evidence_from_a_not_yet_line_backs_nothing(me):
    assert "evidence" in failures_of(lambda d: d["star"][0].update(evidence=["Not yet: Scala, Spark, Kubernetes"]), ctx=context(me))


def test_placeholder_star_stubs_are_surfaced_not_written_up(me):
    ready, stubs = I.star_examples(PROFILE)
    assert [e.title for e in ready] == ["Airflow migration at Marlow"] and stubs == ["Nightly rebuild cut from 6 hours to 55 minutes"]
    assert I.surface_stubs(stubs, "we cut a nightly rebuild") == stubs
    ctx = context(me)
    assert "not filled in" in failures_of(lambda d: d["star"][0].update(stub=stubs[0]), ctx=ctx)
    assert "not a filled STAR stub" in failures_of(lambda d: d["star"][0].update(stub="Invented story"), ctx=ctx)


def test_use_for_tags_drive_star_mapping():
    ready, _ = I.star_examples(PROFILE)
    assert I.map_star("Tell me about a migration you led", ready).title == "Airflow migration at Marlow"
    assert I.map_star("Tell me about a conflict", ready) is None


def test_star_answer_length_bounds(me):
    ctx = context(me)
    short = failures_of(lambda d: d["star"][0].update(situation="Short.", task="Short.", action="Short.", result="Short.", lesson="Short."), ctx=ctx)
    assert "a STAR answer is 60 to 330 words" in short
    assert "lesson" in failures_of(lambda d: d["star"][0].update(lesson=""), ctx=ctx)
    assert "qid must be one of" in failures_of(lambda d: d["star"][0].update(qid="q999"), ctx=ctx)


# ── consistency ──
def test_brief_quotes_only_claims_present_in_submitted_documents(me):
    ctx = context(me)
    paraphrase = failures_of(lambda d: d["consistency"][0].update(quote="Reduced the nightly rebuild to under an hour"), ctx=ctx)
    assert "not literally in the submitted cv" in paraphrase
    wrong_doc = failures_of(lambda d: d["consistency"][0].update(source="cover_letter"), ctx=ctx)
    assert "not literally in the submitted cover_letter" in wrong_doc
    assert "source must be one of" in failures_of(lambda d: d["consistency"][0].update(source="linkedin"), ctx=ctx)
    assert "not literally" in failures_of(lambda d: d["consistency"][0].update(quote="Led"), ctx=ctx)
    assert "say how to defend" not in failures_of(lambda d: None, ctx=ctx)


def test_the_quote_is_found_across_typography_and_spacing(me):
    quote = "Cut  the nightly rebuild of the delivery-performance mart\nfrom 6 hours to 55 minutes"
    assert failures_of(lambda d: d["consistency"][0].update(quote=quote), ctx=context(me)) == ""


def test_prep_contradicting_submitted_cv_is_rejected(me):
    ctx = context(me)
    bad = failures_of(lambda d: d["consistency"][0].update(prepare="Say the rebuild fell to 20 minutes."), ctx=ctx)
    assert "20" in bad and "is a number that is in none of the sources" in bad


def test_a_pack_without_submitted_documents_has_no_brief_to_check(me):
    key = archive.open_application(me, COMPANY, ROLE)
    archive.store_posting(me, key, POSTING)
    ctx = I.load_context(me, key)
    data = copy.deepcopy(GOOD)
    data["consistency"] = []
    assert checked(data, ctx)[0] == []


# ── bridges ──
def test_bridge_has_three_parts_and_cites_profile_fact(me):
    ctx = context(me)
    assert "must all be non-empty" in failures_of(lambda d: d["bridges"][0].update(learning_path=""), ctx=ctx)
    assert "adjacent_quote must be the profile's own words" in failures_of(
        lambda d: d["bridges"][0].update(adjacent_quote="Wrote Scala services for ten years"), ctx=ctx)


def test_a_bridge_that_claims_the_skill_is_rejected(me):
    ctx = context(me)
    claim = failures_of(lambda d: d["bridges"][0].update(acknowledge="I am proficient in Scala."), ctx=ctx)
    assert "reads as a claim of Scala" in claim
    assert "reads as a claim of Scala" in failures_of(lambda d: d["bridges"][0].update(acknowledge="I have extensive Scala experience."), ctx=ctx)
    assert "reads as a claim of Scala" in failures_of(lambda d: d["bridges"][0].update(acknowledge="I know Scala."), ctx=ctx)
    assert "must not mention Scala" in failures_of(lambda d: d["bridges"][0].update(adjacent="I used Scala in a side project."), ctx=ctx)


def test_every_gap_has_a_bridge_and_no_other_gap_is_invented(me):
    ctx = context(me)
    assert "the gap 'Kubernetes' has no bridge" in failures_of(lambda d: d["bridges"].pop(), ctx=ctx)
    extra = failures_of(lambda d: d["bridges"].append(dict(d["bridges"][0], gap="Haskell")), ctx=ctx)
    assert "'Haskell' is not a gap of this application" in extra


# ── tough questions, questions to ask, research ──
def test_tough_question_catalogue_complete(me):
    assert set(I.TOUGH_QUESTIONS) == {"why_leave", "missing_skill", "five_year", "weakness", "why_company"}
    assert "'weakness' has no answer" in failures_of(lambda d: d["tough"].pop(3), ctx=context(me))


def test_why_this_company_cites_verified_hook(me):
    ctx = context(me)
    assert "hook must be the id of a verified claim" in failures_of(lambda d: d["tough"][4].update(hook="c2"), ctx=ctx)
    assert "hook must be the id of a verified claim" in failures_of(lambda d: d["tough"][4].update(hook=""), ctx=ctx)


def test_without_any_verified_claim_why_this_company_is_left_unanswered(me):
    ctx = context(me)
    assert "no verified hook" in failures_of(lambda d: None, ctx=ctx, claims=())
    data = copy.deepcopy(GOOD)
    data["tough"].pop()
    assert checked(data, ctx, claims=())[0] == []


def test_publicly_answered_questions_removed(me):
    ctx = context(me)
    assert I.answered_publicly("Is the role hybrid?", "hybrid policy", [CLAIM]) is CLAIM
    assert I.answered_publicly("How many office days a week do you have, with a hybrid policy?", "", [CLAIM]) is CLAIM
    assert I.answered_publicly("How is the team organised?", "team structure", [CLAIM]) is None
    data = copy.deepcopy(GOOD)
    failures, cut = checked(data, ctx)
    assert failures == [] and len(data["ask"]) == 5 and all(i["question"] != GOOD["ask"][4]["question"] for i in data["ask"])
    assert cut[0][1] is CLAIM


def test_too_few_questions_left_after_the_cut_go_back_to_the_harness(me):
    data = copy.deepcopy(GOOD)
    data["ask"] = data["ask"][3:5]
    failures, _ = checked(data, context(me))
    assert any("public information already answers" in f and "propose at least 4 more" in f for f in failures)


def test_four_to_six_questions_stage_appropriate():
    items = [{"question": f"q{i}", "category": c} for i, c in enumerate(["culture", "role", "tech and growth", "team", "culture", "tech and growth", "role"])]
    final = I.ask_questions(items, "final round")
    assert len(final) == 6 and {i["category"] for i in final[:3]} == {"culture", "team"}
    tech = I.ask_questions(items, "technical")
    assert {i["category"] for i in tech[:3]} == {"tech and growth", "role"}
    assert I.ask_questions(items[:4], "phone")[0]["category"] == "role"


def test_unverified_claim_excluded_from_pack():
    got, dropped = I.usable_claims(research, "company", "Brightwell", "Brightwell")
    assert got == [CLAIM] and dropped == 1
    people, _ = I.usable_claims(research, "interviewer", "Jo Vale", "Brightwell")
    assert people == [PERSON]
    assert I.usable_claims(None, "company", "x", "x") == ([], 0)
    assert not I.VerifiedClaim("c3", "text", "no-link", True).usable("company")
    assert not I.VerifiedClaim("", "text", "https://example.com", True).usable("company")


# ── the harness step and the saved pack ──
def run_pack(me, replies, stage="technical", details=None, fit=FIT, rounds=3, claims=research):
    ctx = I.load_context(me, archive.key(COMPANY, ROLE))
    server = FakeServer(replies)
    with Turns(Api("http://fake", server.transport()), me / "state" / "log.jsonl") as turns:
        pack = I.build_pack(turns, "claude-code", ctx, PROFILE, fit, stage,
                            details or {"date": "2026-04-01", "format": "video", "people": "Jo Vale"}, claims, rounds)
    return pack, server, ctx


def reply(data):
    return {"files": {"pack.json": json.dumps(data)}, "cost": 0.1}


def test_pack_contains_the_sections_and_only_verified_research(me):
    archived(me, stages=[dict(on=D, stage="phone", feedback="Be concise.")])
    pack, server, _ = run_pack(me, [reply(GOOD)])
    for heading in ("## 1. Likely questions", "## 2. STAR answers", "## 3. Consistency brief", "## 4. Honest bridges",
                    "## 5. Tough questions", "## 6. Questions to ask", "## 7. Research", "## 8. Logistics"):
        assert heading in pack
    assert "Brightwell runs a hybrid policy" in pack and "about to be acquired" not in pack and "family lives" not in pack
    assert "Jo Vale leads the platform team" in pack and "1 unverified claim(s) were not used" in pack
    assert "Cut, public information already answers: Is the role hybrid" in pack and "Round 'phone'" in pack
    assert "Nightly rebuild cut from 6 hours to 55 minutes" in pack.split("Stubs for you to fill in")[1]
    assert "Check camera, sound and light" in pack and "Date: 2026-04-01" in pack


def test_posting_and_documents_travel_as_files_and_instructions_hold_only_the_role(me):
    archived(me)
    _, server, _ = run_pack(me, [reply(GOOD)])
    body = server.requests[0]
    files = {i["filename"]: i["file_data"] for i in body["input"] if i["type"] == "input_file"}
    assert {"profile.md", "posting.md", "cv.txt", "cover_letter.txt", "questions.json", "gaps.json", "research.json", "stubs.json"} <= set(files)
    assert "PhD" not in files["posting.md"] and "Production pipelines" in files["posting.md"]
    assert "Production pipelines" not in body["instructions"] and "Production pipelines" not in body["input"][0]["text"]
    research_sent = json.loads(files["research.json"])
    assert [c["id"] for c in research_sent] == ["c1"]


def test_failures_go_back_verbatim_on_the_same_conversation_and_a_bad_pack_is_never_returned(me):
    archived(me)
    bad = copy.deepcopy(GOOD)
    bad["star"][0]["result"] = "Saved 90 percent."
    pack, server, _ = run_pack(me, [reply(bad), reply(GOOD)])
    assert pack and server.requests[1]["previous_response_id"] == "resp_1"
    sent = server.requests[1]["input"][0]["text"]
    assert "90" in sent and "is a number that is in none of the sources" in sent
    none, _, _ = run_pack(me, [reply(bad)] * 4, rounds=3)
    assert none is None


def test_unparseable_or_missing_pack_json_is_a_failure(me):
    archived(me)
    pack, server, _ = run_pack(me, [{"files": {"pack.json": "{nope"}}, reply(GOOD)])
    assert pack and "not valid JSON" in server.requests[1]["input"][0]["text"]
    pack, server, _ = run_pack(me, [{"answer": "done"}, reply(GOOD)])
    assert pack and "pack.json must exist" in server.requests[1]["input"][0]["text"]


def test_pack_saved_per_stage_without_overwriting_earlier(me):
    key = archived(me)
    first = I.save_pack(me, key, "Phone screen", "one")
    again = I.save_pack(me, key, "Phone screen", "two")
    other = I.save_pack(me, key, "final", "three")
    folder = me / "applications" / key
    assert first.name == "interview_prep_phone-screen.md" and again.name == "interview_prep_phone-screen-2.md"
    assert first.read_text() == "one" and other.name == "interview_prep_final.md"
    assert sorted(p.name for p in folder.glob("interview_prep_*")) == sorted([first.name, again.name, other.name])


def test_packs_differ_by_stage(me):
    archived(me)
    phone, _, _ = run_pack(me, [reply(GOOD)], stage="phone screen")
    final, _, _ = run_pack(me, [reply(GOOD)], stage="final round")
    assert phone != final and "notice period" in phone and "salary expectations" in final


def test_interview_writes_confined_to_archive_and_the_profile_is_never_edited(me):
    key = archived(me)
    before = {p: p.read_bytes() for p in me.rglob("*") if p.is_file()}
    pack, _, _ = run_pack(me, [reply(GOOD)])
    path = I.save_pack(me, key, "technical", pack)
    after = {p: p.read_bytes() for p in me.rglob("*") if p.is_file()}
    new = set(after) - set(before)
    assert path in new and all(p == path or p.parent == me / "state" for p in new)
    assert all(after[p] == before[p] for p in before)                                 # nothing existing changed, profile included
    with pytest.raises(archive.ArchiveRefused):
        I.save_pack(me, "../../outside", "x", "no")


def test_tips_for_phone_and_video_only():
    assert any("glass of water" in t for t in I.TIPS["phone"]) and any("camera" in t for t in I.TIPS["video"])
    assert any("anything else" in t for t in I.TIPS["phone"]) and set(I.TIPS) == {"phone", "video"}


# ── the mock interview ──
def mock_turn(kind="", question="", feedback="", better_star=""):
    return {"files": {"turn.json": json.dumps({"kind": kind, "question": question, "feedback": feedback, "better_star": better_star})}}


def plan_replies(budget, feedback="Good structure; lead with the result."):
    plan = I.mock_plan(budget)
    replies = [mock_turn(plan[0], "Tell me what draws you to data platforms?")]
    for i in range(1, budget):
        replies.append(mock_turn(plan[i], f"Question {i + 1}?", feedback))
    return replies + [mock_turn(feedback=feedback)]


def run(me, replies, answers, budget=6, minutes=20, cost_budget=5.0):
    key = archived(me)
    ctx = I.load_context(me, key)
    server = FakeServer(replies)
    queue, asked = list(answers), []

    def ask(question):
        asked.append(question)
        return queue.pop(0) if queue else None

    with Turns(Api("http://fake", server.transport()), me / "state" / "log.jsonl", minutes, cost_budget) as turns:
        mock = I.run_mock(turns, "claude-code", ctx, PROFILE, "## 2. STAR answers\n### Tell me about a migration\n", ask, budget)
    return mock, server, asked


def test_mock_question_order_and_behavioural_count():
    assert I.mock_plan(4) == ["warm-up", "technical", "behavioural", "tough"]
    assert I.mock_plan(6) == ["warm-up", "technical", "technical", "behavioural", "behavioural", "tough"]
    with pytest.raises(ValueError):
        I.mock_plan(3)


def test_feedback_follows_every_answer_on_one_conversation(me):
    mock, server, asked = run(me, plan_replies(4), ["a1", "a2", "a3", "a4"], budget=4)
    assert mock.ended == "finished" and [x.answer for x in mock.exchanges] == ["a1", "a2", "a3", "a4"]
    assert all(x.feedback for x in mock.exchanges) and len(asked) == 4 and len(server.requests) == 5
    assert [r.get("previous_response_id") for r in server.requests] == [None, "resp_1", "resp_2", "resp_3", "resp_4"]
    assert server.requests[1]["input"][0]["text"] == "ANSWER:\na1"
    assert [x.kind for x in mock.exchanges] == I.mock_plan(4)


def test_the_harness_gets_the_profile_with_the_behavioural_register_and_the_documents(me):
    _, server, _ = run(me, plan_replies(4), ["a"] * 4, budget=4)
    files = {i["filename"]: i["file_data"] for i in server.requests[0]["input"] if i["type"] == "input_file"}
    assert "Likes owning a pipeline end to end (stated)" in files["profile.md"] and {"cv.txt", "cover_letter.txt", "plan.json"} <= set(files)
    assert "natural register" in server.requests[0]["instructions"]
    assert not any(i["type"] == "input_file" for i in server.requests[1]["input"])


def test_feedback_before_any_answer_is_refused(me):
    early = mock_turn("warm-up", "First?", feedback="You did great already.")
    mock, server, _ = run(me, [early, *plan_replies(4)], ["a"] * 4, budget=4)
    assert "leave feedback empty on the first turn" in server.requests[1]["input"][0]["text"]
    assert mock.ended == "finished"


def test_a_question_of_the_wrong_kind_or_after_the_last_is_sent_back(me):
    replies = plan_replies(4)
    wrong = mock_turn("tough", "Out of order?")
    mock, server, _ = run(me, [wrong, *replies], ["a"] * 4, budget=4)
    assert "must be of kind 'warm-up'" in server.requests[1]["input"][0]["text"]
    over = plan_replies(4)
    over[-1] = mock_turn(question="One more?", feedback="Fine.")
    _, server, _ = run(me, [*over, mock_turn(feedback="Fine.")], ["a"] * 4, budget=4)
    assert "last question" in server.requests[-1]["input"][0]["text"]


def test_the_person_can_stop_and_no_more_turns_are_spent(me):
    mock, server, asked = run(me, plan_replies(6), ["a1", "stop"], budget=6)
    assert mock.ended == "stopped" and len(mock.exchanges) == 1 and len(server.requests) == 2
    assert len(asked) == 2
    silent, server, _ = run(me, plan_replies(6), [], budget=6)
    assert silent.ended == "stopped" and silent.exchanges == () and len(server.requests) == 1


def test_the_question_budget_is_never_exceeded(me):
    replies = plan_replies(4)
    mock, server, asked = run(me, replies + [mock_turn("technical", "Extra?")], ["a"] * 10, budget=4)
    assert len(asked) == 4 and len(mock.exchanges) == 4 and len(server.requests) == 5


def test_a_spent_money_budget_ends_the_mock_without_a_new_turn(me):
    replies = [dict(r, cost=1.0) for r in plan_replies(4)]
    mock, server, _ = run(me, replies, ["a"] * 4, budget=4, cost_budget=1.0)
    assert mock.ended == "budget" and len(server.requests) == 1
    assert [x.feedback for x in mock.exchanges] == [""]                     # the answer was taken, its feedback never bought


def test_mock_feedback_never_invents_facts_about_the_person(me):
    bad = plan_replies(4)
    bad[1] = mock_turn("technical", "Q2?", "Good, and your 15 years at Snowflake clearly show.")
    mock, server, _ = run(me, [*bad[:2], bad[1], *plan_replies(4)[1:]], ["a"] * 4, budget=4)
    sent_back = server.requests[2]["input"][0]["text"]
    assert "none of the sources" in sent_back and "'Snowflake' in the feedback is a name that is not in the profile" in sent_back


def test_better_star_must_be_a_title_in_the_pack(me):
    replies = plan_replies(4)
    replies[1] = mock_turn("technical", "Q2?", "Fine.", better_star="Invented story")
    mock, server, _ = run(me, [*replies[:2], plan_replies(4)[1], *plan_replies(4)[2:]], ["a"] * 4, budget=4)
    assert "better_star 'Invented story' is not the title" in server.requests[2]["input"][0]["text"]
    good = plan_replies(4)
    good[1] = mock_turn("technical", "Q2?", "Fine.", better_star="Tell me about a migration")
    ok, _, _ = run(me, good, ["a"] * 4, budget=4)
    assert ok.ended == "finished"


def test_answers_are_never_stored_outside_me_or_in_the_turn_log(me):
    secret = "My secret answer about Tidewater"
    mock, server, _ = run(me, plan_replies(4), [secret] * 4, budget=4)
    ctx = I.load_context(me, archive.key(COMPANY, ROLE))
    saved = I.save_pack(me, ctx.key, "technical", I.render_mock(ctx, "technical", mock), "mock_interview")
    assert secret in saved.read_text() and me in saved.parents
    assert secret not in (me / "state" / "log.jsonl").read_text()
    holders = [p for p in me.rglob("*") if p.is_file() and secret in p.read_text(errors="ignore")]
    assert holders == [saved]


def test_mock_runs_only_on_acceptance(me, tmp_path):
    archived(me)
    args = argparse.Namespace(me=str(me), company=COMPANY, role=ROLE, posting=None, stage="technical", date="2026-04-01",
                              format="video", people="Jo Vale", fit=None, mock=True, questions=4, model="claude-code",
                              server="http://fake", rounds=1, minutes=5, budget=5.0)
    tracker.ensure_tracker(me / "tracker.csv")
    tracker.add_external(me / "tracker.csv", company=COMPANY, role=ROLE, applied_on=D, by_person=True)
    (me / "fit").mkdir()
    (me / "fit" / f"{archive.key(COMPANY, ROLE)}.json").write_text(json.dumps(FIT))
    declined = FakeServer([reply(GOOD)])
    code = I.main(args, research=research, ask=lambda q: "", confirm=lambda q: False, api=Api("http://fake", declined.transport()))
    assert code == 0 and len(declined.requests) == 1
    assert (me / "applications" / archive.key(COMPANY, ROLE) / "interview_prep_technical.md").is_file()
    accepted = FakeServer([reply(GOOD), *plan_replies(4)])
    answers = iter(["a1", "a2", "a3", "a4"])
    code = I.main(args, research=research, ask=lambda q: next(answers, ""), confirm=lambda q: True,
                  api=Api("http://fake", accepted.transport()))
    assert code == 0 and len(accepted.requests) == 6
    assert (me / "applications" / archive.key(COMPANY, ROLE) / "mock_interview_technical.md").is_file()


def test_closing_message_points_to_logging_the_stage_and_the_profile():
    assert "log the stage" in I.CLOSING and "profile first" in I.CLOSING


def test_main_refuses_without_a_target_before_any_turn(me, capsys):
    tracker.ensure_tracker(me / "tracker.csv")
    args = argparse.Namespace(me=str(me), company="", role="", posting=None, stage="technical", date="", format="", people="",
                              fit=None, mock=False, questions=6, model="claude-code", server="http://fake", rounds=1,
                              minutes=5, budget=5.0)
    server = FakeServer([])
    assert I.main(args, api=Api("http://fake", server.transport())) == 2 and server.requests == []
    assert "tracker is empty" in capsys.readouterr().out

"""Tests that close the profile/security parity rows (docs/parity/A-profile-security.md): UTF-8 output, reframing levels,
CV and letter consistency, and the third-party-code guard."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from jobloop import consistency, letter, reframe, text, verify

ROOT = Path(__file__).resolve().parent.parent
PROFILE = (ROOT / "fixtures" / "profile.md").read_text()


# ── UTF-8 output whatever the host's default encoding ─────────────────────────────────────────────────
def legacy(*argv: str, code: str | None = None) -> subprocess.CompletedProcess:
    """Run python the way a Windows-1252 host would: ASCII locale, no UTF-8 mode, a legacy stream encoding."""
    env = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252", "PYTHONPATH": str(ROOT)}
    args = [sys.executable, "-"] if code else [sys.executable, "-m", "jobloop", *argv]           # code on stdin: argv is not UTF-8 here
    return subprocess.run(args, capture_output=True, env=env, cwd=ROOT, input=code.encode("utf-8") if code else None)


def test_non_latin_output_roundtrips_under_legacy_locale():
    shown = "Яндекс Łódź 東京 履歴書.pdf"
    without = legacy(code=f"print({shown!r})")
    assert without.returncode != 0                                       # the host default really cannot print it
    done = legacy(code=f"from jobloop.text import utf8_io; utf8_io(); print({shown!r}); import sys; print({shown!r}, file=sys.stderr)")
    assert done.returncode == 0
    assert done.stdout.decode("utf-8").strip() == shown and done.stderr.decode("utf-8").strip() == shown


def test_utf8_io_replaces_what_it_cannot_write_and_leaves_ordinary_streams_alone(capsys):
    text.utf8_io()                                                       # a captured stream has no reconfigure: nothing breaks
    print("plain")
    assert capsys.readouterr().out == "plain\n"


def test_cyrillic_cjk_polish_in_each_cli_output(tmp_path):
    from jobloop import tracker
    me = tmp_path / "me"
    me.mkdir()
    tracker.record_drafted(me / "tracker.csv", company="Яндекс Bank Łódź", role="エンジニア Engineer", today=date(2026, 10, 1),
                           cv_file="cv.pdf", cover_letter_file="cover.pdf", fit_rating=70, source="", deadline=None, channel="")
    listed = legacy("tracker", "--me", str(me), "list")
    assert listed.returncode == 0 and "Яндекс" in listed.stdout.decode("utf-8") and "エンジニア" in listed.stdout.decode("utf-8")
    data = ROOT / "tests" / "fixtures" / "salary" / "benchmark.json"
    looked_up = legacy("salary", "--data", str(data), "--company", "Nestl")                   # Nestlé: a Latin-1 name
    assert looked_up.returncode == 0 and "Nestlé Suisse SA" in looked_up.stdout.decode("utf-8")
    cjk = legacy("salary", "--data", str(data), "--company", "Nestl", "--json")
    assert cjk.returncode == 0 and b"Nestl" in cjk.stdout
    verdict = legacy(code="from jobloop import pdfcheck, text, layout; text.utf8_io()\n"
                          "print('履歴書.pdf: ' + pdfcheck.unverified('pdftotext', 'missing for 履歴書.pdf').feedback())\n"
                          "print('Zażółć gęślą jaźń')")
    assert verdict.returncode == 0 and "履歴書.pdf" in verdict.stdout.decode("utf-8") and "Zażółć gęślą jaźń" in verdict.stdout.decode("utf-8")


# ── reframing: emphasis, never substance ──────────────────────────────────────────────────────────────
def test_reframe_levels_ok_flag_never():
    ok = "- Ran the shipment-events pipeline on Apache Airflow, 40 million events per day from Kafka"          # emphasis on one aspect
    flag = "- Built dbt models and migrated 45 cron jobs for 12 analysts"                                        # merges two roles
    never = "- Operated Spark clusters for the shipment-events pipeline"                                         # a skill not held
    assert reframe.level(ok, PROFILE).name == "ok"
    assert reframe.level(flag, PROFILE).name == "flag"
    assert reframe.level(never, PROFILE).name == "never"
    assert reframe.level("- Mentors two junior engineers and leads the weekly data-modelling review", PROFILE).name == "ok"


def test_adjacent_work_described_in_terms_from_another_role_is_flagged_not_waved_through():
    adjacent = "- Wrote Python ingestion services for partner price feeds and Kafka streams"
    assert reframe.level(adjacent, PROFILE).name == "flag"


def test_backtrack_test_flags_overreach():
    cv = "\n".join(["- Ran the shipment-events pipeline on Apache Airflow, 40 million events per day from Kafka",
                    "- Built dbt models and migrated 45 cron jobs for 12 analysts",
                    "- Operated Spark clusters for the shipment-events pipeline"])
    found = reframe.backtrack(cv, PROFILE)
    assert len(found) == 2 and all("without backtracking" in f for f in found)
    assert any("Spark" in f and "never" in f for f in found) and any("dbt" in f or "cron" in f for f in found)
    assert reframe.backtrack("- Mentors two junior engineers; leads the weekly data-modelling review", PROFILE) == []


def test_a_bullet_that_is_not_in_the_profile_in_any_form_is_never_but_a_not_yet_line_backs_nothing():
    assert reframe.level("- Deployed Kubernetes clusters", PROFILE).name == "never"       # the profile says "Not yet: Kubernetes"


def test_stretch_bullets_reach_the_result_and_the_failure_list_only_for_never():
    cv = "\\begin{itemize}\n\\item Built dbt models and migrated 45 cron jobs for 12 analysts\n\\item Operated Spark clusters\n\\end{itemize}"
    stretch, never = reframe.split(cv, PROFILE)
    assert len(stretch) == 1 and len(never) == 1 and "Spark" in never[0]


# ── the CV and the letter agree ───────────────────────────────────────────────────────────────────────
CV = "Alex Fernsby\nSenior Data Engineer, Tidewater Logistics (Rotterdam), March 2022 - present\n- Owns the shipment-events pipeline."


def test_cv_letter_no_contradiction():
    good = "I am a Senior Data Engineer at Tidewater Logistics. Before that I was a Data Engineer at Marlow Retail Group."
    assert consistency.check(CV, good, PROFILE) == []
    wrong_title = "I work as a Junior Analyst at Tidewater Logistics, where I own the pipeline."
    found = consistency.check(CV, wrong_title, PROFILE)
    assert found and "Tidewater Logistics" in found[0] and "Senior Data Engineer" in found[0]
    assert consistency.check(CV, "I was a Junior Analyst at Delft Energy Cooperative.", PROFILE) == []       # their own title: fine


def test_a_number_or_date_the_letter_states_must_be_in_the_cv_or_the_profile():
    found = consistency.check(CV, "I have run pipelines for 15 years and since March 2020.", PROFILE)
    assert any("15" in f for f in found) and any("March 2020" in f for f in found)
    assert consistency.check(CV, "I have 7 years of experience and have been at Tidewater Logistics since March 2022.", PROFILE) == []


def test_the_letter_check_is_part_of_the_whole_gate():
    assert "consistency" in verify.judge.__code__.co_names or "consistency" in verify.__dict__


# ── the letter forward-looks instead of repeating the CV ──────────────────────────────────────────────
def test_letter_forward_looking_sections():
    cv = ("Owns the shipment-events pipeline: 40 million events per day from Kafka into a PostgreSQL and BigQuery warehouse "
          "orchestrated with Apache Airflow. Cut the nightly rebuild of the delivery-performance mart from 6 hours to 55 minutes.")
    copied = ("Dear Hiring Manager\nOwns the shipment-events pipeline: 40 million events per day from Kafka into a PostgreSQL and "
              "BigQuery warehouse orchestrated with Apache Airflow.\nSincerely")
    fresh = ("Dear Hiring Manager\nFor your platform I would start by mapping which feeds break most often, then add tests where "
             "failures cost the most, and report the drop after one quarter.\nSincerely")
    assert letter.check_repeats_cv(copied, cv) and "repeats the CV" in letter.check_repeats_cv(copied, cv)[0]
    assert letter.check_repeats_cv(fresh, cv) == []
    assert letter.check_repeats_cv(fresh, None) == []


# ── a company claim the loop did not verify cannot be in the letter ──────────────────────────────────
def test_unverified_company_claim_blocked():
    from jobloop import grounding
    claim = "Northwind opened its Rotterdam data hub in 2023 with 120 engineers"
    stated = "I read that Northwind opened its Rotterdam data hub in 2023 with 120 engineers."
    assert grounding.check(stated, [PROFILE], date(2026, 10, 19))                              # not verified: blocked
    assert grounding.check(stated, [PROFILE, claim], date(2026, 10, 19)) == []                 # verified by research: allowed
    assert grounding.check("Northwind opened a hub with 300 engineers.", [PROFILE, claim], date(2026, 10, 19))


# ── nothing here fetches or installs third-party code ─────────────────────────────────────────────────
def test_addboard_never_fetches_third_party_code():
    installers = re.compile(r"\b(?:git (?:clone|pull|submodule)|pip3? install|uv (?:pip|add|tool install)|npm install|curl |wget )")
    hits = [f"{p.relative_to(ROOT)}: {m.group()}" for p in (ROOT / "jobloop").rglob("*.py") for m in installers.finditer(p.read_text())]
    assert hits == []
    ran = [m for p in (ROOT / "jobloop").rglob("*.py") for m in re.findall(r'subprocess\.run\(\[\s*"([\w-]+)"(?:,\s*"([\w-]+)")?', p.read_text())]
    assert {tool for tool, _ in ran}.isdisjoint({"pip", "pip3", "uv", "npm", "curl", "wget", "python"})
    assert {sub for tool, sub in ran if tool == "git"} <= {"check-ignore", "ls-files"}                    # reads, never fetches


# ── flagged bullets are shown after drafting; a poor experience match warns before ────────────────────
def test_stretch_bullets_surface_and_low_match_warns(tmp_path, capsys):
    from dataclasses import replace
    from test_apply import DRAFT, REVIEW, Env, make_fit
    env = Env(tmp_path, [DRAFT, {"answer": REVIEW}, {"files": DRAFT["files"]}])
    inner = env.app.deps.verify
    env.app.deps.verify = lambda *a, **k: replace(inner(*a, **k), stretch=('flag: "Built dbt models": "dbt" comes from a different role',))
    env.app.deps.evaluate = lambda *a, **k: replace(make_fit(), experience=40)
    assert env.app.execute() == 0
    out = capsys.readouterr().out
    assert "Experience match is 40" in out and out.index("Experience match is 40") < out.index("RESULT")
    assert "keep it" in out and "soften" in out and "drop it" in out and "Built dbt models" in out


def test_a_good_match_and_clean_bullets_show_neither_warning(tmp_path, capsys):
    from test_apply import DRAFT, REVIEW, Env
    env = Env(tmp_path, [DRAFT, {"answer": REVIEW}, {"files": DRAFT["files"]}])
    assert env.app.execute() == 0
    out = capsys.readouterr().out
    assert "Experience match is" not in out and "stretch the profile" not in out


def test_the_gate_fails_a_never_bullet_and_a_contradicting_letter_and_passes_a_stretch_with_a_note():
    from test_verify import CV_TEX, LETTER, docs, judge
    bullet = lambda text: CV_TEX.replace("\\section{Education}", "\\begin{itemize}\n\\item " + text + "\n\\end{itemize}\n\\section{Education}")
    ok = judge(cv_tex=bullet("Owns the shipment-events pipeline on Apache Airflow"))
    assert ok.passed and ok.stretch == ()
    never = judge(cv_tex=bullet("Operated Spark clusters for the shipment-events pipeline"))
    assert not never.passed and any("never" in f and "Spark" in f for f in never.failures)
    stretch = judge(cv_tex=bullet("Built dbt models and migrated 45 cron jobs for 12 analysts"))
    assert stretch.passed and len(stretch.stretch) == 1 and "dbt" in stretch.stretch[0]
    wrong = LETTER.replace("Senior Data Engineer", "Senior Data Engineer", 1) + "\nI work as a Junior Analyst at Tidewater Logistics."
    found = judge(docs(letter=wrong))
    assert any("Junior Analyst" in f and "contradict" in f for f in found.failures)


# ── the letter's lists ────────────────────────────────────────────────────────────────────────────────
def test_list_wrapper_pattern():
    from jobloop import latex
    good = "\\lettercontent{Opening.}\n\\begin{letterbullets}\n\\item \\textbf{A}: x\n\\end{letterbullets}\n\\lettercontent{Closing.}"
    wrapped = "\\lettercontent{Opening.\n\\begin{itemize}\n\\item x\n\\end{itemize}\n}"
    bare = "\\lettercontent{Opening.}\n\\begin{itemize}\n\\item x\n\\end{itemize}"
    assert latex.letter_rules(good) == []
    found = latex.letter_rules(wrapped)
    assert len(found) == 1 and "inside \\lettercontent" in found[0]
    assert "bare list" in latex.letter_rules(bare)[0] and "letterbullets" in latex.letter_rules(bare)[0]
    assert latex.letter_rules("%\\lettercontent{\\begin{itemize}}") == []                      # a comment is not the letter
    assert any("lettercontent" in f for f in verify.source_findings("\\section{Profile}", wrapped))


# ── the command table lists the modules that exist, each with a purpose ──────────────────────────────
def test_reference_table_lists_existing_files():
    from jobloop.__main__ import COMMANDS
    modules = {p.stem for p in (ROOT / "jobloop").glob("*.py") if "def add_arguments" in p.read_text()}
    assert modules == set(COMMANDS)                                      # none missing from the table, none listed that is not there
    assert all(purpose.strip() and len(purpose.split()) >= 2 for purpose in COMMANDS.values())
    assert all((ROOT / "jobloop" / f"{name}.py").is_file() for name in COMMANDS)

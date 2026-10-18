"""interview.py: no claim in the room that isn't on the paper."""
from __future__ import annotations

from pathlib import Path

from fake_server import FakeServer
from jobloop import interview
from jobloop.turns import Api, Turns

FIXTURES = Path(__file__).parent.parent / "fixtures"
PROFILE = (FIXTURES / "profile.md").read_text()
POSTING = (FIXTURES / "posting.md").read_text()
CV = r"\section*{Experience} \item Cut the nightly rebuild from 6 hours to 55 minutes by moving it to incremental dbt models."
COVER = r"Dear Hiring Manager, I mentor two junior engineers and lead the weekly data-modelling review. I have not yet worked with Scala."

PACK = """# Interview prep

## Likely questions
1. Tell me about the rebuild.
2. How would you learn Scala?

## STAR answers
1. Situation: the nightly rebuild took 6 hours. Action: incremental dbt models. Result: 55 minutes.

## Consistency brief
> "Cut the nightly rebuild from 6 hours to 55 minutes"
Be ready to say which tables were incremental.
> I mentor two junior engineers and lead the weekly data-modelling review.
Name them by role, not by name.

## Honest bridges
Scala: I have not worked with it; dbt and SQL modelling is the closest, and I would learn it with the team.
Spark: same.

## Questions to ask
- How does the platform team decide what to build next?
"""
GAPS = ["Scala", "Spark"]


def check(pack=PACK, gaps=GAPS):
    return interview.check(pack, PROFILE, POSTING, CV + "\n" + COVER, gaps)


def test_a_pack_built_from_the_paper_and_the_profile_passes():
    assert check() == []


def test_every_section_is_required():
    assert "## Honest bridges" in check(PACK.replace("## Honest bridges", "## Gaps"))[0]


def test_a_quote_that_is_not_on_the_paper_is_refused():
    failures = check(PACK.replace("Cut the nightly rebuild from 6 hours to 55 minutes", "Led a team of fifty engineers"))
    assert "not in cv.tex or cover.tex" in failures[0] and "fifty" in failures[0]


def test_the_consistency_brief_must_quote_something():
    pack = PACK.split("## Consistency brief")[0] + "## Consistency brief\nJust be confident.\n\n## Honest bridges" + PACK.split("## Honest bridges")[1]
    assert 'start with ">"' in check(pack)[0]


def test_a_star_answer_with_a_number_the_profile_lacks_is_refused():
    assert "contain numbers" in check(PACK.replace("Result: 55 minutes.", "Result: 5 minutes and 300 thousand euros saved."))[0]


def test_list_numbering_is_not_a_number_claim():
    assert check(PACK.replace("Situation:", "3. Situation:")) == []


def test_every_gap_needs_an_honest_bridge():
    failures = check(PACK.replace("Spark: same.", ""))
    assert "missing: Spark" in failures[0]


def test_links_are_refused_because_nothing_here_is_researched():
    assert "Remove the links" in check(PACK + "\nSee https://brightwell.example/blog for their forecasting stack.")[0]


def test_latex_markup_is_not_in_the_way_of_a_quote():
    assert " ".join(interview.plain(r"saved \textbf{3 days} in \emph{two} steps").split()) == "saved 3 days in two steps"
    assert interview.sections(PACK)["Honest bridges"].startswith("\nScala")


def test_the_pack_is_built_from_files_and_failures_go_back(tmp_path):
    server = FakeServer([{"files": {"interview_prep.md": "no sections"}}, {"files": {"interview_prep.md": PACK}}])
    with Turns(Api("http://x", server.transport()), tmp_path / "l.jsonl") as turns:
        pack = interview.build(turns, "codex", PROFILE, POSTING, CV, COVER, GAPS, "technical")
    assert pack == PACK and "missing these sections" in server.requests[1]["input"][0]["text"]
    assert {f["filename"] for f in server.requests[0]["input"][1:]} == {"profile.md", "posting.md", "cv.tex", "cover.tex"}
    assert "technical" in server.requests[0]["input"][0]["text"] and "untrusted" in server.requests[0]["instructions"]

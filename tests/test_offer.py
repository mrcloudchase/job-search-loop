"""offer.py: figures must be in a source, the analysis names the gaps and never advises, the prep invents no market data."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from fake_server import FakeServer
from jobloop import archive, offer as O
from jobloop.turns import Api, Turns

TODAY = date(2026, 10, 19)
PROFILE = (Path(__file__).resolve().parent.parent / "fixtures" / "profile-cph.md").read_text()
TERMS = """# Offer from Acme, Senior Data Engineer

- Base pay: DKK 700,000 per year
- Bonus: 10 percent of base pay
- Equity: none
- Pension: 8 percent employer contribution
- Holiday: 25 days
- Start date: 2027-03-01
- Notice period: three months
- Location: Copenhagen, 3 days a week on site
- On-call: paid rota, DKK 5,000 per month
"""
POSTING = "Senior Data Engineer at Acme. You join a data platform team of 14 people in Copenhagen.\n<!-- hidden: 999 -->\n"
BENCH = {"metadata": {"index_baseline": 100, "index_label": "Index"}, "companies": [
    {"name": "Acme", "city": "Copenhagen", "categories": {"engineers": {"count": 12, "index": 118.5}}}]}
DB1, DB2 = "more than 4 days a week on site", "on-call without compensation"
WANTS = "a senior individual-contributor or tech-lead role on a data platform team"

ANALYSIS = f"""# Offer analysis: Acme, Senior Data Engineer

## Offer at a glance
- Base pay: DKK 700,000 per year, with a bonus of 10 percent of base pay and no equity.
- Pension of 8 percent and 25 days of holiday; start date 2027-03-01 after a notice period of three months.
- Copenhagen, with 3 days a week on site, and a paid on-call rota at DKK 5,000 per month.

## Against your targets
- Deal-breaker "{DB1}": met, the offer asks for 3 days a week on site.
- Deal-breaker "{DB2}": met, the on-call rota is paid at DKK 5,000 per month.
- Want "{WANTS}": the offer names a Senior Data Engineer role on a data platform team, but says nothing about tech-lead scope.
- Start: your earliest start is 2027-02-01 and the offer says 2027-03-01, so the offer starts later than you could.
- Career goals: the offer does not mention a path to tech lead.

## Gaps
- No equity is offered. The offer is silent on a tech-lead path and on what happens to the bonus in a partial year.

## Questions to ask
- How is the 10 percent bonus decided, and has it been paid in full in recent years?
- Can the role grow toward tech lead, and what would that need?

## Trade-offs
Accepting gives you a paid on-call rota and a hybrid week inside your limits, but no equity and no stated path to tech lead.
Declining keeps your current role and its notice period but gives up this platform scope.

Your decision.
"""
PREP = """# Negotiation prep: Acme, Senior Data Engineer

## Market data
No benchmark data was supplied, so nothing here states what the market pays.

## To look up
- What similar senior data engineers are paid in Copenhagen, from sources you trust.
- How the employer values equity, if any is offered later.

## Negotiation points
1. Tech-lead path: ask for a written route to tech lead, because it is your stated career goal.
2. Base pay: ask whether DKK 700,000 per year has room, once you have looked up market pay.
3. Start date: ask whether 2027-02-01 is possible, since it is your earliest start.
"""


def inputs(tmp_path, terms=TERMS, bench=False):
    me = tmp_path / "me"
    me.mkdir(parents=True, exist_ok=True)
    (me / "offers").mkdir(exist_ok=True)
    (me / "offers" / "acme__senior_data_engineer.md").write_text(terms)
    key = archive.open_application(me, "Acme", "Senior Data Engineer")
    archive.store_posting(me, key, POSTING.encode())
    if bench:
        (me / "salary").mkdir()
        (me / "salary" / "benchmark.json").write_text(json.dumps(BENCH))
    return me, O.gather(me, key, "Acme", "Senior Data Engineer", PROFILE, TODAY)


def fails(tmp_path, analysis=ANALYSIS, prep=PREP, bench=False):
    return O.check(inputs(tmp_path, bench=bench)[1], analysis, prep)


def test_a_good_pair_passes(tmp_path):
    assert fails(tmp_path) == []


def test_gather_uses_the_targets_the_visible_posting_and_the_benchmark(tmp_path):
    _, i = inputs(tmp_path, bench=True)
    assert i.deal_breakers == (DB1, DB2) and i.wants == WANTS and "earliest start 2027-02-01" in i.targets
    assert "Quartz" not in i.targets and "Marlow" not in i.targets            # the targets, not the whole profile
    assert "team of 14" in i.posting and "999" not in i.posting and "118.5" in i.benchmark


def test_missing_terms_say_where_to_paste_them(tmp_path):
    me, _ = inputs(tmp_path)
    (me / "offers" / "acme__senior_data_engineer.md").write_text("  \n")
    with pytest.raises(O.OfferError, match="me/offers/acme__senior_data_engineer.md"):
        O.gather(me, "acme__senior_data_engineer", "Acme", "Senior Data Engineer", PROFILE, TODAY)


def test_a_profile_without_preferences_is_refused(tmp_path):
    me, _ = inputs(tmp_path)
    with pytest.raises(O.OfferError, match="Preferences"):
        O.gather(me, "acme__senior_data_engineer", "Acme", "Senior Data Engineer", "# A\n\n## Summary\nx\n", TODAY)


@pytest.mark.parametrize("old,new,needle", [
    ("DKK 700,000 per year, with", "DKK 750,000 per year, with", "750,000"),                       # an invented amount
    ("start date 2027-03-01", "start date 2027-04-01", "2027-04"),                              # an invented date
    ("and no equity", "and equity of 2 percent", "2"),                                          # an invented number
    ("Pension of 8 percent", "Pension of 9 percent", "9"),
])
def test_a_figure_in_no_source_is_rejected_verbatim(tmp_path, old, new, needle):
    found = fails(tmp_path, ANALYSIS.replace(old, new))
    assert found and any(needle in f and "none of the sources" in f and f.startswith("offer_analysis.md") for f in found)


def test_figures_from_the_posting_the_targets_and_the_benchmark_are_sources(tmp_path):
    extra = ANALYSIS.replace("## Gaps", "## Gaps\n- The posting names a team of 14 people; your limit is 4 days.")
    assert fails(tmp_path, extra) == []
    prep = PREP.replace("No benchmark data was supplied, so nothing here states what the market pays.",
                        "The benchmark you supplied shows an index of 118.5 for engineers at Acme.")
    assert fails(tmp_path, prep=prep, bench=True) == []
    assert any("118.5" in f for f in fails(tmp_path / "plain", prep=prep, bench=False))


def test_derived_arithmetic_is_not_a_source(tmp_path):
    found = fails(tmp_path, ANALYSIS.replace("## Gaps\n", "## Gaps\n- Base and bonus come to DKK 770,000 a year.\n"))
    assert any("770,000" in f for f in found)


def test_the_analysis_must_quote_each_deal_breaker_and_the_wants(tmp_path):
    assert any(DB2 in f for f in fails(tmp_path, ANALYSIS.replace(DB2, "unpaid on call")))
    assert any(WANTS in f for f in fails(tmp_path, ANALYSIS.replace(WANTS, "a good job")))


def test_sections_questions_and_the_closing_line(tmp_path):
    assert any("'## Gaps'" in f for f in fails(tmp_path, ANALYSIS.replace("## Gaps", "## Misc")))
    assert any("Questions to ask" in f and "bullet" in f for f in fails(tmp_path, ANALYSIS.replace("- How is", "How is").replace("- Can the", "Can the")))
    assert any("Your decision." in f for f in fails(tmp_path, ANALYSIS.replace("Your decision.", "Good luck.")))
    assert any("Your decision." in f for f in fails(tmp_path, ANALYSIS + "\nP.S. sign soon\n"))


@pytest.mark.parametrize("advice", ["You should accept this offer.", "I recommend you decline.", "It is wise to accept.", "My advice is to walk away."])
def test_no_advice_to_accept_or_decline(tmp_path, advice):
    found = fails(tmp_path, ANALYSIS.replace("## Trade-offs\n", f"## Trade-offs\n{advice}\n"))
    assert any("advice to accept or decline" in f for f in found)
    assert any("advice to accept or decline" in f for f in fails(tmp_path, prep=PREP + f"\n{advice}\n"))


def test_negotiation_points_are_ranked_and_without_a_benchmark_no_market_data(tmp_path):
    assert any("most important first" in f for f in fails(tmp_path, prep=PREP.replace("2. Base", "- Base")))
    assert any("most important first" in f for f in fails(tmp_path, prep=PREP.replace("2. Base", "3. Base")))
    assert any("No benchmark data was supplied" in f for f in fails(tmp_path, prep=PREP.replace("No benchmark data was supplied", "Nothing")))
    assert any("To look up" in f for f in fails(tmp_path, prep=PREP.replace("- What similar", "What similar").replace("- How the", "How the")))
    claim = PREP.replace("## Negotiation points", "## Negotiation points\nThe market rate is higher for this role.")
    assert any("states market pay" in f for f in fails(tmp_path, prep=claim))


def test_a_missing_document_is_a_failure(tmp_path):
    assert any("negotiation_prep.md must exist" in f for f in fails(tmp_path, prep=None))
    assert any("offer_analysis.md must exist" in f for f in fails(tmp_path, analysis=" "))


def run(tmp_path, replies, **kw):
    me, i = inputs(tmp_path)
    server = FakeServer(replies)
    with Turns(Api("http://x", server.transport()), tmp_path / "log.jsonl", 5, 5.0) as turns:
        return O.analyse(turns, "codex", i, **kw), server, me, i


def test_analyse_sends_failures_back_word_for_word_and_returns_only_a_passing_pair(tmp_path):
    bad = ANALYSIS.replace("DKK 700,000", "DKK 750,000")
    result, server, *_ = run(tmp_path, [{"files": {O.ANALYSIS: bad, O.PREP: PREP}}, {"files": {O.ANALYSIS: ANALYSIS}}])
    assert result.analysis == ANALYSIS and result.prep == PREP
    assert "750,000" in server.requests[1]["input"][0]["text"] and server.requests[1]["previous_response_id"] == "resp_1"
    first = json.dumps(server.requests[0])
    assert "offer.md" in first and "targets.md" in first and "Quartz" not in first


def test_analyse_never_returns_documents_that_keep_failing(tmp_path):
    bad = {"files": {O.ANALYSIS: ANALYSIS.replace("DKK 700,000", "DKK 750,000"), O.PREP: PREP}}
    result, server, *_ = run(tmp_path, [bad] * 4)
    assert result is None and len(server.requests) == 4


def test_write_keeps_the_terms_hash_and_the_previous_pair(tmp_path):
    result, _, me, i = run(tmp_path, [{"files": {O.ANALYSIS: ANALYSIS, O.PREP: PREP}}])
    assert O.analysed_hash(me, i.key) is None
    first, second = O.write(me, i.key, result, i.terms)
    assert first.name == O.ANALYSIS and second.name == O.PREP and O.analysed_hash(me, i.key) == i.terms_hash
    O.write(me, i.key, result, i.terms + "x")
    assert (first.parent / "offer_analysis.previous.md").is_file() and O.analysed_hash(me, i.key) == O.terms_hash(i.terms + "x")

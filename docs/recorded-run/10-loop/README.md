# Recorded run 10: the loop, end to end

`python -m jobloop run` on a **fictional candidate** (`profile-fictional.md`, Copenhagen, English C1) against the **real boards**
(freehire, linkedin, jobindex, jobnet, jobdanmark; jobbank refuses on robots.txt) and the **real harness-interface server**.
Standing authorisation: `loop.json` (2 new drafts per pass, fit >= 55, best of 2, LinkedIn terms acknowledged by me).

**Providers, plainly.** No Anthropic key on this machine. Drafter ladder: Claude Code on **DeepSeek**, then **Codex (OpenAI)**.
Reviewer and judges: Codex and the read-only Claude Code copy on DeepSeek, never the drafter's family. Researcher: Claude Code
on DeepSeek on the `research` sandbox. Planner (fit, facts, learn, follow-up, interview, offer): DeepSeek then Codex. Codex
reports tokens, not dollars; the loop counts unpriced tokens against the budget at $1 per million.

## What happened (digests in `digests/`, raw logs in `logs/`)

| Pass | Stages run | Result |
|---|---|---|
| 1 | everything the loop wanted to do | discover (12 searches, 128 found, 69 new) -> triage (panel of two harnesses per posting) -> research -> **draft** -> **package**: two applications **ready in the queue** (Maersk 81, Flatpay 78). Two Adesso postings were parked as `needs_decision`: their source host (`jobs.adesso-group.com`) is neither an installed board nor an official ATS, so the loop did not draft them. The pass stopped at its token ceiling (6.1M unpriced tokens), after 25 actions and $16.44, and still wrote its digest. |
| (me) | | **I played the person**, with the person's own commands: marked both applications `applied`, logged a technical-interview stage and set `interview`, edited a delivered letter, set `offer` and wrote offer terms (fictional) into `me/offers/`. I also backdated the Flatpay tracker row so a follow-up was due. Nothing here was done by the loop. |
| 2 | interview, maintain | **interview pack** for the technical stage: the first harness stalled on the pack's checks, the loop moved to the next harness on the ladder and it passed. |
| 3 | offer, learn, upskill, followup | **offer analysis and negotiation prep** (every figure checked against the terms file), **upskill** report with fetched resources (62 gaps over 9 applications), and **learn** wrote **playbook v2** (style rules from the judge's repeated failures and my edit). |
| 4, 5 | followup, learn | **follow-up draft** for Flatpay (grounded in the submitted documents; a draft only). Learn turned my ungrounded edit ("a team of 47 engineers", a number the profile does not have) into a `profile_fact` **proposal** for the person; I rejected it, since it is not true of the fictional candidate. |
| 6-9 | discover, triage | see "What the run exposed". |

`status.txt` is the final picture; `queue/` has the entries the person sees (README checklist, queue.json); `applications/` has
the CV and letter PDFs (CV 2 pages, letter 1, every code check passed), the judge/review notes, the interview pack, offer files,
follow-up draft; `playbook/` the two versions; `report.html` the static report; `tracker.csv` the person's file (the loop wrote
`drafted` and nothing else).

## What the run exposed (and what changed because of it)

- **25 of 69 postings were "unreachable".** Freehire returns links to aggregators whose pages the ladder cannot read, so triage
  could never score them, although search had already returned their full descriptions. The posting store now keeps the board's
  description and the fetch ladder's last rung uses it (noted as such); with it, triage ranked 13 more postings in three batches. Tests: `tests/test_fetch.py`, `tests/test_state.py`,
  `tests/test_search.py`, `tests/test_stage_triage.py`.
- **A learned style rule contradicted a check.** Playbook v2 contains "name each acknowledged gap in both the CV and the
  letter", which the verifiers forbid for the CV (a gap is never put on a CV). The verifiers would have rejected such drafts,
  but the rule would have caused stalls. Rules that contradict a verification are now refused by `playbook.validate`
  (`tests/test_engine.py`). The v2 files in `playbook/` are kept as the loop wrote them, bug included.
- **Learn's fact detector reasons about numbers, not claims.** My first test edit ("a team of three engineers", then "3") was
  not flagged, because those numbers appear elsewhere in the profile; "47" was. A claim-level check would need a judge.

## Honest limits of this run

- One candidate, one day, a few real passes; "interviews and offers" are my simulation of the person, so the outcome side of
  self-improvement (weights from outcomes) had nothing real to learn from. Learn acted on judge failures and my edit only.
- First-pass verification rate was 0% (both drafts needed fix rounds); judge mean 6.7/10; cost per accepted application about
  $3.40 in the first pass. The Maersk company research verified 0 of 30 claims (the loop dropped all of them and made no
  company claims); Falck-style verified research is in `../04b-apply-verified-research/`.
- Several postings could not be drafted for lack of a known employer domain (`company_domains` in loop.json) or a verified source host:
  the loop asks the person instead of guessing.

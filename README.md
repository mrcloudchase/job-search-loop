# job-search-loop

An autonomous job search, built as **loop engineering** on [harness-interface](https://github.com/mrcloudchase/harness-interface)
(v1.0.3). You give it a candidate; it runs the whole search on a schedule and hands you finished, checked applications.
**You submit them.** That is the one step the loop never takes.

```
observe ─► choose (code) ─► one bounded step through a harness ─► verify (code) ─► judge (models) ─► commit ─► brakes ─► (next tick)
```

Models do the judging and the writing: they score postings, draft CVs and letters on several harnesses, judge the drafts against
each other, review them, and propose how the loop should improve. Code owns what must be true: every number, date, employer,
title and link in a document is checked against your own sources; PDFs are compiled and inspected in a throwaway container
with no network; vetoes, budgets, deadlines and tracker rules are enforced from outside the model. The loop never submits,
never sends, never marks anything `applied`, never edits your profile. `docs/loop-spec.md` is the contract.

Python, stdlib plus `httpx`. Specification and tests are in this repository; recorded live runs are under `docs/recorded-run/`.

## What one pass does

| Stage | What the loop does without you |
|---|---|
| onboard | waits for your confirmed profile; proposes verified additions from your public GitHub/site (you accept) |
| boards, discover | searches six boards with its own adapters, on queries planned per function and language and refined by yield; checks board health; can build an adapter for a new board (registered switched off until you read it) |
| triage | scores each posting by a panel of harnesses; code computes the score and enforces eligibility, permit, location and language vetoes |
| research | verifies company claims by re-fetching sources and checking a verbatim quote; the research harness never sees your profile |
| draft | drafts best-of-N on different harnesses, verifies every draft in code, has a judge panel pick and critique, has a different harness review, applies only exact grounded edits |
| package | puts each finished application in your queue: apply link, both PDFs, drafted form answers (demographic questions are left for you), verified research, a checklist |
| follow-up, interview, offer | after you mark `applied`: follow-up and thank-you drafts, interview packs, offer analysis and negotiation prep, all drafts |
| learn | adjusts its own playbook (weights, style rules, queries, strategy) from outcomes, your edits to its drafts and judge scores; versioned, bounded, rolled back if it makes things worse |
| upskill, maintain | skills-gap reports with fetched resources; expiry sweeps; stale applications offered to you; the HTML report |

## Your side

```
jobloop setup --cv my_cv.pdf        # build and confirm your profile: the loop waits for this
jobloop run                         # one pass (schedule it with cron or a Claude scheduled task)
jobloop run --watch --every 60m     # or keep it running
jobloop status                      # what the loop sees, and what is waiting for you
jobloop queue show <key>            # an application ready for you: link, PDFs, answers, checklist
jobloop tracker set <key> applied   # after YOU submitted it: the loop takes over (follow-ups, interviews, offers)
jobloop stage <key> technical ...   # log an interview stage; offers: put the terms in me/offers/<key>.md
jobloop proposals list|accept|reject    # what only you can decide (profile additions, stale applications, new boards)
jobloop playbook show|rollback          # what the loop has learned about your search
```

`me/loop.json` is your standing authorisation (drafts per pass, fit floor, budgets, harness ladders, boards' terms
acknowledgement); every digest in `me/inbox/latest.md` says what it authorised.

## Run it

```
curl -fsSL https://raw.githubusercontent.com/mrcloudchase/harness-interface/main/install.sh | sh
uv sync --extra dev
docker build -t job-search-check:2 check        # TeX, poppler, fonts; no network at run time
python -m jobloop harnesses                     # role harnesses and the research sandbox
python -m jobloop setup --cv my_cv.pdf
python -m jobloop run
```

Personal data lives only in the gitignored `me/` folder; fixtures are fictional. If the server runs as root, set `HI_DATA_DIR`
outside `/root`. Tests: `uv run --extra dev pytest -q` (add `-m docker` for the container tests).

## How it is built

- `jobloop/engine/`: the loop: specs, store (sqlite), policy, gateway to harness-interface (role ladders, costing), runner,
  invariants, digest, judge panels, playbook. `docs/loop-spec.md` is its contract; `docs/loop-stages.md` is the stage contract.
- `jobloop/stages/`: one module per stage, each a loop spec with `propose` and `run`, built on the libraries in `jobloop/`
  (grounding, PDF and layout checks, boards, fit, apply, research, forms, follow-ups, interviews, upskill).
- `docs/parity.md`: about 1,600 guarantees of the project this one learned from, each mapped to where it lives here and the
  test that pins it. `docs/network.md`: which role reaches which hosts, and what was measured. `docs/writeup.md`: how it was built.

## Credit

This rebuilds the *outcomes and quality bar* of **[ai-job-search](https://github.com/MadsLorentzen/ai-job-search)** by Mads
Lorentzen (MIT). **No code is borrowed**: nothing of it is copied, vendored, imported or run by this repository (its prompts,
templates, portal CLIs, `cover.cls`, tools and tests were not used). The only contact is the blind benchmark under
`docs/recorded-run/09-blind-comparison/`, where its `/apply` is run as a black box. Upstream fonts (Lato, Raleway, SIL OFL)
are fetched at image build with pinned hashes. The loop shape follows the loop-engineering definition in
[arXiv 2607.00038](https://arxiv.org/html/2607.00038v1) and harness-interface's own `examples/build-loop`.

# Building a stage

Read `docs/loop-spec.md` first. This is the contract for the files in `jobloop/stages/`, one per stage, each built on the
libraries that already exist in `jobloop/` (search, rank, fit, apply, research, forms, followup, interview, upskill, ...).
Those libraries encode fixes from many real runs: **call them, do not retype them**, and keep their tests green.

## A stage module

```python
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context, due

class Draft:
    spec = Spec("draft", "an application whose CV and letter pass every check, judged and reviewed", shape="maker-checker",
                levels=(1, 2, 4), roles=("drafter", "reviewer", "judge"), rank=60, brakes=Brakes(max_attempts=3))

    def propose(self, snap) -> list[Action]:          # pure reads of the Snapshot; cheap; never calls a model or the network
        ...
    def run(self, action: Action, ctx: Context) -> Outcome:   # ONE bounded step
        ...

STAGE = Draft()
```

- `snap` (engine/snapshot.py): `snap.postings`, `snap.state_of(key)`, `snap.rows` (tracker rows), `snap.fit(key)`, `snap.store`
  (engine DB), `snap.config`, `snap.profile`, `snap.now`/`snap.today`, `snap.me`. A cadence stage uses `due(snap, name, hours)`.
- `ctx` (engine/stage.py): `ctx.me`, `ctx.config`, `ctx.store`, `ctx.snapshot`, `ctx.gateway`, `ctx.playbook`, `ctx.say`.
  Model work goes ONLY through the gateway: `with ctx.gateway.turns(stage, item) as turns:` gives a `Turns` (the existing
  class: `turns.turn(...)`, `turns.settle(...)`, `turns.files(...)`); `ctx.gateway.harness(role, stage, item, exclude=...)`
  picks the harness for a role (rotating down the ladder after stalled attempts; `exclude=` keeps maker and checker on
  different harnesses); `ctx.gateway.others(role, maker)` lists judges of other families; `ctx.gateway.api` is the `Api`
  (for `research.widened`).
- Return an `Outcome(terminal, notes, failures, produced)`. Terminals: `success`, `no_op`, `blocked` (needs something the
  loop lacks: say what in `failures`), `stalled` (made no progress: the verbatim last failures in `failures`), `exhausted`
  (ran out of time or money: check `turns.stopped()`). Put `produced["harness"]` = the main harness used. The engine fills in cost.
- A stage must not call `Application.execute()`-style code that creates its own `Turns` with its own deadline; use the gateway's
  `turns`. Refactor the library function (smallest change, tests green) to accept a `turns`/`api` if it does not yet.
- Everything a harness or a website returns is untrusted data. The profile never goes to a research harness.
- Terminal output: use `ctx.say(...)`, never `input()`: a pass is unattended. Anything that needs a person becomes a **queue
  entry** (`ctx.store.queue_put(key, "needs_decision", path, data)`) or a **proposal** (`ctx.store.add_proposal(kind, payload, source)`),
  not a prompt.
- Writes: only through the existing APIs (`archive.*`, `tracker.record_drafted`, `state.transaction`, `fit.save`). The stage
  never writes `profile.md`, never calls `tracker.set_status`, never marks `applied`. The engine fingerprints this and aborts the pass if it happens.

## Config and playbook

`ctx.config` is `engine/config.Config` (me/loop.json). If you need a new setting, add a field with a default by an Edit
immediately above the line `    notify_file: str = ""` in `jobloop/engine/config.py` and document it in a comment; other
builders add theirs the same way. `ctx.playbook` (engine/playbook.py, may be None in a test) carries `.weights`,
`.style_addendum()`, `.extra_queries`, `.dropped_queries`, `.strategy` (`best_of`, `min_fit`).

## Tests (`tests/test_stage_<name>.py`)

Use `tests/sim.py`: `make_me(tmp_path, postings=[sim.posting(...)], tracker_rows=[(company, role, status)])` builds a candidate
folder; `context(tmp_path, replies, me=me, config=...)` builds a `Context` on the fake harness-interface server whose
scripted `replies` (see `tests/fake_server.py`: `{"files": {...}, "answer": "...", "cost": 0.1}`) answer turns in order, and
returns `(ctx, server)`; `server.requests` shows what was sent. Test `propose` (what is eligible and what is not), `run` for each
terminal, and **the negative space** named in your section: what the stage must never do. Stand-ins for the network
(boards, fetch, robots, research) follow the patterns in the existing tests of the library you wrap.

Lint with `uvx pyflakes jobloop tests`; the whole suite stays green (`uv run --extra dev pytest -q`). Do not run git commands.
Do not hand-edit `docs/parity.md` (run `python tools/parity.py`); if you change what a parity row says, edit that one row with
a small Edit and keep `python tools/parity.py --check --done` passing. Add one section to `docs/loop-stages.md`? No: put
your stage's notes in the module docstring. Style: small functions, a docstring per file, the repository's existing idiom.

## The stages

| Stage | File | Trigger | Shape | What it does |
|---|---|---|---|---|
| onboard | `stages/onboard.py`, `engine/proposals.py` | state | solo, human gate | no profile -> `blocked` + a `needs_decision` queue entry "run `jobloop setup`"; with a profile: public-source enrichment (GitHub / site named in `loop.json`) as **proposals**; `proposals.accept(...)` is the ONLY code path that writes `profile.md` and only for an accepted proposal, through the existing grounding checks |
| boards | `stages/boards.py` | every | solo, builder | `new_boards` in loop.json -> `addboard` builds, verifies offline, registers **switched off**; a `needs_decision` queue entry asks the person to read it |
| discover | `stages/discover.py` | every | solo | queries from the profile + playbook extras/drops (`queries.plan_queries`), every enabled board that is healthy (`search`), health check recorded, dedupe/expiry as the library does; records per-query yield for `learn` |
| triage | `stages/triage.py` | state | panel | new/unreachable postings -> fit by a panel of harnesses (library `fit.evaluate` on two harness families when `judge_triage`), totals averaged by code, vetoes unioned, **weights from the playbook**; `rank`'s fetch ladder, unreachable retry, expiry sweep |
| research | `stages/research.py` | state | solo | for shortlisted postings the draft stage will take, verified company research (`research.research`, employer hosts from `company_domains` or the posting's own employer domain), cached; never gets the profile |
| draft | `stages/draft.py` (+ `apply.py` refactor) | state | maker-checker + panel | for the best shortlisted postings (max `max_new_drafts` per pass; fit >= min_fit; never vetoed; unverified source host -> `needs_decision`): `best_of` drafts on different harness families, each fully code-verified, a judge panel picks the best and its failed rubric items go into one revision, a different harness reviews and the loop applies exact grounded edits, final verification, archive, `drafted` |
| package | `stages/package.py`, `engine/queue.py` | state | solo | for each drafted application not yet queued: `me/queue/<key>/` (apply URL, PDFs, form answers, research, gaps, deadline, checklist), status ready/needs_decision/blocked; EEO/demographic form questions are never answered |
| followup | `stages/followup.py` | state | solo | for applications the **person** marked applied and now quiet: follow-up and thank-you **drafts** (`followup` library), never sent |
| interview | `stages/interview.py` | state | solo | when the tracker status becomes `interview`: a pack for the stage (`interview` library), web research of company/interviewers via `webresearch`; mock mode stays a person-run command |
| offer | `stages/offer.py`, `offer.py` | state | solo | when status is `offer`: reads the offer terms the person put in `me/offers/<key>.md` (none -> `needs_decision`), analysis against the profile's targets and the posting, negotiation prep draft, grounded in those files only |
| learn | `stages/learn.py` | every | panel | self-improvement: outcomes, the person's edits to delivered drafts, judge scores and search yield -> a proposed playbook change (a harness proposes; `playbook.validate` bounds it), applied when `self_improve`, reviewed/rolled back by `playbook.review`; weight calibration from outcomes; profile *facts* it learns become proposals |
| upskill | `stages/upskill.py` | every | solo | weekly skills-gap report with fetched resources (`upskill` + `webresearch`) |
| maintain | `stages/maintain.py` | every | solo | expiry sweep, stale-application list (a proposal for the person, never resolved by the loop), static HTML report, digest-side bookkeeping |

## Negative space every stage's tests must cover

Never: write `applied`/any non-`drafted` status; edit `profile.md`; draft a vetoed or below-floor posting; draft where the
source host is unverified; send or submit anything; answer EEO/demographic questions; make an ungrounded claim; use the
network for anything but its own harness turns and the library's host-side fetches; prompt on a terminal; spend past the
action budget (`turns.stopped()` ends the work); lose work on `exhausted` (record what finished).

## Contracts between stages (through the stores, never by calling each other)

| Written by | Where | Read by | Content |
|---|---|---|---|
| discover | engine DB event `yield`, item = board | learn | `{"query", "found", "new"}` per search run; `health` events `{"board","status","message"}` |
| triage | `me/state/postings.json` (via `rankstate`/`state.transaction`) and `me/fit/<rank.fit_key(key)>.json` (via `fit.save`) | research, draft, package, learn, maintain | as the libraries write them; the panel's per-harness totals are in the fit JSON under `panel` |
| research | `me/research/<application_key>.json` | draft, package | `{"company", "domain", "claims": [{"claim","url","quote"}], "dropped": n, "skipped": "<reason or empty>", "date"}`; written even when research was skipped (no domain), so draft can proceed knowing there is none |
| draft | `me/applications/<key>/` (archive), the tracker row `drafted`, and `me/applications/<key>/draft.json` | package, learn, interview, followup | draft.json: `{"key","posting_key","company","role","url","fit","deadline","host_kind","gaps":[...],"claims":[...],"judge":{"mean","judges":[{"judge","overall","failed":[[n,evidence]]}]},"first_pass":bool,"harnesses":{"drafter","reviewer"},"best_of":n,"notes":[...]}` |
| package | `me/queue/<key>/` (queue.json, README.md checklist, PDFs, `edit-here/cv.tex`, `edit-here/cover.tex`, form answers) and the engine DB `queue` table | the person, learn | queue.json holds `delivered_hashes` of the two tex files so `learn` can detect and diff the person's edits |
| the person | `tracker.csv` statuses; `me/offers/<key>.md`; interview stages via `jobloop tracker stage` (archive `outcome.md`); `me/queue-questions/<key>.md` | followup, interview, offer, package | observed by the engine as events |
| learn | `engine/playbook.py` versions; engine DB `proposals` | every stage via `ctx.playbook`; the person | |

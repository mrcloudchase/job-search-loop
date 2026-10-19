# The loop: specification

`jobloop run` is one autonomous outer loop that takes a candidate through the whole job search and stops short of one thing:
**submitting an application is done by a person**. Everything else (finding, judging, researching, drafting, reviewing,
packaging, following up, preparing interviews and offers, learning from outcomes) is the loop's work. This document is the
contract the engine (`jobloop/engine/`) and every stage (`jobloop/stages/`) are built and tested against.

## Shape

```
        ┌───────────────────────────── schedule (cron, scheduled task, or run --watch) ──────────────────────┐
        ▼                                                                                                     │
  observe ─► policy.next ─► stage.run (one bounded step) ─► verify (code) ─► judge (models) ─► commit ─► brakes ─┘
 (stores,     (plain code:      harness turns through the        facts, formats,        panel scores,      one DB        no-progress,
  human         which item,     harness-interface gateway,       grounding, pages,      best-of-N pick     transaction   budget, deadline
  events)       which action)   escalating harness ladder        ATS, tracker rules)
```

A **tick pass** repeats that cycle until the policy finds nothing eligible, the deadline or budget is reached, or a brake
fires. A pass always ends with a written digest. Everything is resumable: a process killed at any point leaves a consistent
store and the next pass continues from what the stores say, not from memory.

## Who decides what

| Decision | Decided by |
|---|---|
| Which item and action next, retries, parking, escalation | **code** (`engine/policy.py`) |
| Facts in documents: every number, date, employer, title, email, phone and link | **code** (grounding against the candidate's sources) |
| Formats: compile, page counts, ATS text layer, layout, tracker vocabulary, robots, hosts | **code** |
| Whether a posting fits, whether a draft is good, which of several drafts is best, whether a query is productive | **models** (judge panels of different harnesses), with code computing scores and enforcing vetoes |
| How the loop itself improves (scoring weights, style rules, query sets, drafting strategy) | **models propose, code bounds, outcomes decide** (`engine/playbook.py`) |
| Submitting, sending, marking `applied`, writing the profile, resolving stale applications | **the person** |

## Items and states

The loop does not keep a second copy of the world. Its snapshot is assembled each tick from the stores that already own the
facts: the posting store (`me/state/postings.json`), the tracker (`me/tracker.csv`, the person's own file), the archive
(`me/applications/<key>/`), the queue (`me/queue/<key>/`) and the engine database (`me/engine/engine.db`: runs, attempts,
events, proposals, judgments, playbook versions).

```
posting:      new ─► ranked ─► (vetoed | below_floor | shortlisted) ─► researched ─► drafted ─► QUEUED(ready) ─► [person: applied]
                 └─► unreachable ─► (retry ... gave_up)      └─► expired / closed
application:  drafted ─► [person] applied ─► followup_due ─► interview ─► offer ─► [person] hired | offer_declined | rejected | no_response | withdrawn
```

Words in brackets are **human gates**: states the loop cannot leave on its own. It only observes that the person did.

## A stage is a loop spec

Every stage declares a `Spec` (`engine/spec.py`) and implements `propose` and `run`:

| Field | Meaning |
|---|---|
| `name`, `goal` | what finishing means, stated so that a check can decide it |
| `trigger` | `state` (runs when items are in a state) or `every` (runs when a cadence is due) |
| `shape` | `solo`, `maker-checker` (a different harness reviews), or `panel` (several judges) |
| `levels` | the verification levels the stage's checks really are: 1 deterministic, 2 rule or constraint, 3 delayed field truth (outcomes), 4 model judge, 5 human |
| `roles` | harness roles it uses (drafter, reviewer, researcher, judge, builder) |
| `brakes` | max attempts per item, repeated-failure detection, share of the pass budget |
| `human_gate` | whether it ends in a state only a person can leave |

`propose(snapshot, config, now)` returns zero or more `Action`s (stage, item key, reason, priority). `run(action, ctx)`
performs **one bounded step** and returns an `Outcome` with a **terminal state**:

| Terminal | Meaning | What the engine does |
|---|---|---|
| `success` | the step's goal was met and verified | commit, move on |
| `no_op` | nothing to do (state already satisfied) | record, no retry |
| `blocked` | cannot proceed without something the loop does not have (a human decision, a missing file, a refused gate) | park with the reason; surface in the digest and queue |
| `stalled` | made no progress: the same failure repeated, or the retry ladder is exhausted | park; the next attempt uses the next harness on the ladder; after the ladder, `blocked` |
| `exhausted` | time, tokens or dollars ran out mid-step | stop starting model turns; finish non-model steps; write the digest |

An error or a spent budget is **never** a success.

## Engine guarantees

1. **Single flight.** A lock file in `me/engine/` allows one pass at a time; a stale lock (older than the pass deadline) is broken.
2. **Atomic commit.** One database transaction per action; a crash leaves no half-recorded attempt.
3. **Brakes.** A per-pass budget ceiling in dollars (unpriced token use is converted at a configured rate and also capped
   in tokens) and a deadline; a no-progress detector (the same stage, item and failure digest twice marks the attempt
   `stalled`); per-item attempt limits with backoff.
4. **Escalation ladder.** Each role has an ordered list of harnesses in `loop.json`; a stalled item's next attempt uses the next harness.
5. **Tripwires.** Before and after every pass the engine fingerprints what must not change (`engine/invariants.py`) and
   aborts the pass if it did.

## Invariants (tested by simulation and checked at run time)

- The loop never writes `applied` (or `interview`, `offer`, `hired`, `rejected`, `no_response`, `offer_declined`, `withdrawn`) to the tracker. It writes `drafted` only, through the archive/tracker API.
- The loop never submits, sends or posts anything outside the harness-interface server and the boards' own read endpoints.
- The loop never changes `profile.md` without a person's accepted proposal.
- It never drafts a vetoed posting, a posting below the configured floor, or one whose source host is unverified, unless the host is the employer's own site, one the person trusts (`trusted_hosts`), or the person approved that application (`jobloop queue approve <key>`); otherwise it becomes `needs_decision` and says the command.
- It never makes a claim in a document that the candidate's sources do not back.
- It never drafts the same application twice, and two passes never overlap.
- A pass never starts a model turn after its deadline or budget; it still writes its digest.
- Everything a harness or a website returns is untrusted data and never an instruction.
- Personal data stays in `me/` (gitignored).

## Self-improvement and judging

Models judge and the loop learns, inside bounds code enforces:

- **Judge panel** (`engine/judge.py`): documents and postings are scored against the rubric in
  `docs/recorded-run/09-blind-comparison/rubric.md` by judges on harnesses other than the maker's. Scores are stored; best-of-N drafting picks the best passing draft.
- **Playbook** (`engine/playbook.py`): versioned, per candidate: scoring weights, a writing-style addendum, extra search queries, drafting strategy. A change is proposed from outcomes, the person's edits to delivered drafts, judge scores and search yield; code validates it (weights within bounds, style rules contain no facts, queries pass the planner's checks); it is applied as a new version; its effect on the tracked metrics is measured; a version that makes them worse is **rolled back automatically**. Every change is in the digest.
- **Never self-modified:** the profile's facts. A fact the loop learns (from an interview, from the person's edits) becomes a proposal the person accepts.

Tracked metrics: first-pass verification rate, mean judge score, cost per accepted (queue-ready) application, applications per week, and, with enough outcomes, interview and offer rates per posting band.

## The person's surface

`jobloop setup` (build the profile, confirm it) · `jobloop run [--watch]` (a pass, or passes on a cadence) ·
`jobloop status` · `jobloop queue list|show|done` · `jobloop tracker list|set` · `jobloop stage` (log an interview stage) ·
`jobloop search boards list|enable|disable` · `jobloop mock` (rehearse an interview) · `jobloop proposals list|accept|reject` ·
`jobloop playbook show|rollback` · `jobloop harnesses` (register the role harnesses). The queue entry for an application
holds the apply link, both PDFs, drafted form answers (EEO-type and demographic questions are left for the person), the
verified company research, the gaps the letter acknowledges, the deadline, and a checklist. The person submits, then runs
`jobloop tracker set <key> applied`; the loop takes over from there (follow-ups, interview packs, offer analysis).

## Scheduling

One pass per invocation by default (survives crashes and ephemeral machines): schedule `jobloop run` with cron or a Claude
scheduled task. `jobloop run --watch --every 60m` loops in-process for a machine that stays up. State is on disk, so either
way the next pass resumes.

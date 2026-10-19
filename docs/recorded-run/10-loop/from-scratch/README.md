# From scratch: a new candidate, real boards, a simulated person

`jobloop setup --cv` → `jobloop run` (search, triage, research) → five shortlisted postings held for an unverified source host →
`jobloop queue approve` for one, and the employer's-own-site rule for another → `jobloop run --stage draft --stage package`.

- Passes 3 and 4 found nothing eligible: after a person's answer the old block's backoff still applied. Fixed (the draft stage
  marks such an action `retry_now`; `tests/test_stage_draft.py`, `tests/test_engine.py`, `tests/test_queue.py`).
- Pass 5: both postings were drafted; the code checks (layout, competency-list length) rejected the drafts, so attempts were `stalled`
  and the ladder moved to the next harness. The pass stopped at its token cap.
- Pass 6: after one more stalled attempt, the DPG Media draft passed every check and was queued ready
  (`queue-entry-README.md`, `queue-entry.json`). Cmotions stays parked and retries on the next pass. Three postings from
  aggregator hosts stay held until the person approves them. Nothing was submitted or marked applied.

Digests are in this folder. The candidate is fictional; the postings are live and public.

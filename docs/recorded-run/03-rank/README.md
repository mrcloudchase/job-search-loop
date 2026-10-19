# Recorded run 3: ranking, with an unreachable and an expired posting

`python -m jobloop rank --model claude-deepseek/deepseek-chat --limit 5 --top 3` (run1), then
`rank --focus "Dead Link" --limit 2` (run2). **Provider: DeepSeek**, through the shipped `claude-code` harness copy
`claude-deepseek` (no Anthropic key here). Scores are computed by code from the facts the harness reports; the fictional
candidate's profile lists no English and a deal-breaker of more than 3 on-site days, so the five real Copenhagen postings
are all vetoed, each with its quoted reason, and none is shortlisted: that is the gates working, not a ranking failure.
Run1 also shows the loop sending a verifier failure back to the harness verbatim (the `gaps` list was too long) and the
harness fixing it on the same conversation.

Two cases were **constructed** because live boards did not hand me one on demand:
- *Unreachable:* a posting row I added to the store with a `.invalid` URL (`Dead Link Labs`). Run2: it was not scored, is
  listed "could not fetch, will retry" with each rung's reason, and is never called closed.
- *Expired:* I edited the stored deadline of an already-ranked real posting (Aalborg Universitet) to 2026-10-10. Run2's
  sweep (no fetch) retired it.

# Recorded run 4b: a full apply with verified company research (Falck)

`python -m jobloop apply --url <linkedin posting> --company-domain falck.com --drafter codex --reviewer claude-reviewer/deepseek-chat --researcher claude-research/deepseek-chat --fit-model codex --facts-model codex`

**Providers, plainly:** drafter, fit scorer and facts reader = **Codex on OpenAI**; reviewer = the shipped Claude Code harness
copied with **DeepSeek** as provider, restricted by its CLI settings to read-only and no web (`claude-reviewer`); researcher =
the same copy on the `research` sandbox (`claude-research`, DeepSeek). Two different harnesses drafted and reviewed. Codex
reports tokens, not dollars (2.9M tokens here); the dollar figure in the transcript is only the DeepSeek turns.

Result: **both PDFs pass every check** (CV 2 pages, letter 1; the checklist is at the end of `transcript.txt`): compile, ATS
text layer, layout, style, grounding, keyword coverage (`coverage.md`). The tracker got one row, `drafted`; nothing else is
written and nothing is sent.

Company research: `research.md` holds 30 claims, each a source URL on falck.com plus a verbatim quote. This run's research
came **from the 30-day cache, re-verified**: the loop fetched each page again host-side (robots first) and checked each quote
was literally on it: "30 verified, 0 dropped". The first (live) research turn that filled the cache ran in an earlier attempt of
this command; the harness gave claims, the loop dropped any it could not verify. The reviewer's notes are in `review.md`; the loop applied or rejected
its structured edits itself.

Earlier attempts of this same scenario (and of the Too Good To Go one) are kept in `../04-apply-attempts/`: each failed round is a check
doing its job, or a bug in a check that was then fixed with a test (a PDF wrapping a role heading or a link, a role title
with a dash, a model-claimed veto with a quote that said the opposite).

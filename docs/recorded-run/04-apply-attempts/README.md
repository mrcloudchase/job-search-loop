# The apply loop's failed attempts (kept on purpose)

Every file is one real run, ANSI stripped. They are not failures to hide: each shows a check refusing something, and
several exposed a bug in a check that was then fixed with a test before the passing runs in `../04a-…` and `../04b-…`.

| Run | What it shows |
|---|---|
| run0 | the fit gate vetoed a 4-day on-site posting against a "more than 3 days" deal-breaker: nothing was drafted |
| run1, 3-6 | drafts failing compile/layout/grounding rounds (page counts, empty address, competency list size, thin profile); failures went back verbatim |
| run2 | a model-claimed deal-breaker veto where the day count was within the limit: now the code judges day limits |
| run7 | a model "quoted" the location line as a permit requirement: now a permit requirement must quote words about a permit |
| run8 | an award line at an employer was read as a role heading: fixed |
| run9 | a model-claimed on-call deal-breaker whose quote said there is *no* on-call duty: now a negated quote is a flag, not a veto |
| run10-13 | Falck: the fit never produced valid quotes with one model, a term wrapped at a hyphen in the PDF text layer, a role title containing a dash, layout edge cases |

Providers: Claude Code harness copies on DeepSeek, Codex on OpenAI, as stated in each run's banner lines.

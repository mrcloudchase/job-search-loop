# How this was built: a procedural write-up for the author of harness-interface

What I did, in order; what worked; what cost time; what harness-interface lacked. Everything here is backed by a file in
this repository (commits, `docs/recorded-run/`, `docs/network.md`). Run on harness-interface v1.0.3, one machine, **no
Anthropic API key**: the shipped `claude-code` harness ran on DeepSeek (a copy of the harness with another provider), and
`codex` ran on OpenAI. Every recorded run says which.

## What was built
A Python loop (`jobloop/`, about 15,000 lines, stdlib plus `httpx`) with 2,587 offline tests, plus a contract of about 1,600
guarantees of the original project (`docs/parity.md`, no row `planned`). Harnesses draft, score and critique; code decides.

## In order

1. **Environment.** Installed with the official `install.sh`. Hit, in turn: the sandbox archive/Docker Hub rate limit (429) and
   a TLS-intercepting proxy that made the default sandbox image fail to build (npm `SELF_SIGNED_CERT_IN_CHAIN`); I worked around
   it with a stand-in image tagged as the default tag. The server ran as root, so `HI_DATA_DIR` had to leave `/root` (0700
   home). `uv tool install` lacked the server extra.
2. **Read both projects completely**, then wrote the contract (`docs/parity.md`) before any code: seven readers produced rows
   (original guarantee with file and line, where it lives here, which test pins it, status).
3. **Proved the loop in one file** (`apply.py`): a drafter on one harness, a reviewer on another in a fresh conversation,
   verification outside the harness in a no-network container, failures sent back verbatim with `previous_response_id`, a
   deadline (`POST /responses/{id}/cancel`) and a budget. One recorded run with a hidden prompt injection in the posting
   (`docs/recorded-run/01-proof/`).
4. **Grew it phase by phase** (profile, search, rank, apply, lifecycle, interview, upskill, add-a-board), mostly with parallel
   builder agents under a shared brief: ownership by file, no git operations, small single-row edits to the shared parity files.
   Each phase has offline tests (including what must never happen) and a recorded live run.
5. **Closed the matrix** with checkers (`tools/parity.py --check --done` fails on a `planned` row or a test name that does not exist).
6. **Live runs** exposed what offline tests did not; about 13 apply attempts are kept in `docs/recorded-run/04-apply-attempts/`.
7. **Blind comparison** against the original's `/apply` (`docs/recorded-run/09-blind-comparison/`).

## What worked well in harness-interface
- **`previous_response_id` as the repair channel.** `Turns.settle` sends check failures back word for word on the same
  conversation; models fix real layout and grounding failures in two or three rounds. It is the core of the design.
- **Different harnesses as different conversations.** Drafter and reviewer on different harnesses, the reviewer in a fresh
  workspace, is one `model` string. Harness agnosticism really is "a name in `model`".
- **`artifacts` for per-role CLI settings.** `.claude/settings.json` with `deny` on Write/Edit/WebFetch/WebSearch makes a
  reviewer that cannot write. I probed it: the deny rules hold under bypass-permissions mode.
- **`PUT /sandboxes/research` to widen egress for one turn** (serialized): a research harness reaches one company's hosts only
  while its turn runs, and never receives the profile.
- **`POST /responses/{id}/cancel`** at a deadline, and the streamed `harness.cost_usd`, made `--minutes` and `--budget` real.
- **Provider swap per harness.** Running the same harness on DeepSeek by copying it made the whole project testable without a key.

## What cost time
- **Checks that were wrong, not models.** Most failed apply rounds were bugs in my checks meeting real PDFs: a PDF puts a
  role's employer on the next line; a link wraps at its hyphen; a term wraps at a hyphen; an award line at an employer looks
  like a role heading; a role title with a dash trips the no-dash rule; a model "quoted" the location line as a permit
  requirement; a model reported an on-call deal-breaker from a line saying there is none. Each is now a test.
- **Model variability.** DeepSeek's layout tuning oscillates around the page-fill limits; the same scenario passed with
  Codex drafting. Both are recorded.
- **Sites.** One employer's site sat behind a bot challenge; the researcher tried for eight minutes and returned `[]`, and the loop
  rightly made no claims. A second employer's site worked and gave 30 verified claims.
- **Shared files between parallel builders** (one rewrote a whole parity file): fixed by a rule of single-row edits.
- **Rebuilding from the contract.** The matrix was worth it, but 1,600 rows is a lot to keep honest; a third are `stricter here`.

## What harness-interface lacked (work-arounds in brackets)
- **Network per turn.** A sandbox is fixed per harness, so a role needs a harness copy and a sandbox. [harness copies derived at
  registration time by `tools/register_harnesses.py`; research widened with `PUT /sandboxes/research`.]
- **Per-request tool limits.** There is no way to say "this turn may not write". [`artifacts` settings per harness copy.]
- **Structured output.** Every JSON result goes through a file the model must write, then is validated and sent back.
  A response schema would remove a whole class of repair rounds.
- **Cost for Codex.** It reports tokens, not dollars, so budgets are partial for mixed runs. [the loop prints unpriced tokens.]
- **Default sandbox behind a re-signing proxy** cannot be built; a way to add CA certificates to the default image would help.
- **`/files` versus `harness.files`.** The first lists the whole workspace, the second only this turn's changes; I confused
  them once and the loop now has one `listing` helper.
- **Search tools run at the provider**, not in the sandbox (probed), so a sandbox with no egress can still search; the
  security model should say so. `WebFetch` needs `skipWebFetchPreflight` in the settings to work through the door.
- **Install paths.** `uv tool install` without the server extra, and root's 0700 home, are small papercuts on a fresh machine.

## Things I decided that a person should confirm
- LinkedIn and Jobbank terms: the board CLI requires `--acknowledge-terms`; for the recorded runs I acknowledged LinkedIn's
  (personal, low volume) because the brief asked for all six boards. Jobbank's `robots.txt` disallows the feed and the adapter
  refuses; I did not work around it.
- A negated deal-breaker quote is a flag, not a veto; an on-site day limit is judged by code, not by the model's claim.
- I marked a fictional application `applied` through the tracker CLI, as the person, only to demo the follow-up draft.
- Add-a-board: the adapter runs on the host after a source check and an offline container check; the person reads it before enabling.

## Known limits
The blind comparison is one application per system, with two scorers that are not a true third harness (only two harness
families exist here). The rebuild's letter can call a keyword a gap where adjacent evidence exists (a synonym table would fix
it). The documents-folder and interview profile paths and the `--section` path have offline tests but no recorded live run.

# job-search-loop

A job search that runs as a loop around any coding harness, instead of inside one Claude Code session. It
profiles you, searches job boards, scores fit, drafts a tailored CV and cover letter in LaTeX, has a *different*
harness critique them, checks the PDFs, tracks applications and prepares interviews. It drives the harnesses
through [harness-interface](https://github.com/mrcloudchase/harness-interface), so the drafter and the reviewer
are two names: Claude Code, Codex, or any of the 23 it ships.

**Credit.** This is a re-imagining of [MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search)
(MIT), a Claude Code project of slash-command prompts and skills that got its author hired. The workflow, the fit
framework's weights and bands, the drafter-and-fresh-context-reviewer idea, the grounding audit, and the job-portal
CLIs (which `portals import` finds in a checkout of that repo) are theirs. Nothing was copied from it: the code and
every prompt here are written fresh, and no file of theirs is in this repository. Go and read it.

## The idea

> Stop prompting your harness; write a loop around it.

A harness is good at *producing* (a CV, a score, a critique). It is not good at *knowing it is done*. So the loop
decides everything checkable, outside the harness, and sends failures back word for word:

| The harness | The loop (plain code) |
|---|---|
| drafts `cv.tex` and `cover.tex` | compiles them in a throwaway container with no network; exactly 2 and 1 pages; text extracts; every key requirement the profile backs is on the CV; **no number the profile lacks**; a requirement the profile *doesn't* back is **not** on the CV and **is** named in the letter |
| scores fit and quotes the profile as evidence | checks each quote is really in the profile, computes the weighted total itself, applies the location and language vetoes, says go or no-go |
| builds your profile from a CV and notes | checks every number, email, phone and link is in what you gave, and no year was dropped |
| writes interview prep | checks every quoted claim is really in the CV or letter you submitted, STAR answers add no numbers, every gap has a bridge, no links |
| writes a portal adapter | checks its source (few imports, no `eval`, no file writes, no foreign hosts) and runs it on its own saved sample in a container with no network |

## Quick start

You need Python 3.11+, [uv](https://docs.astral.sh/uv/), Docker, and harness-interface running with a provider and
the harnesses you want. Pin it, as the library docs suggest:

```bash
# 1. harness-interface (the server needs its `server` extra)
uv tool install "harness-interface[server] @ git+https://github.com/mrcloudchase/harness-interface@v1.0.2"
hi server start
hi provider add anthropic --url https://api.anthropic.com --key-env ANTHROPIC_API_KEY
hi provider add openai    --url https://api.openai.com/v1 --key-env OPENAI_API_KEY
hi harness add --builtin claude-code codex

# 2. this repo, and the container the loop checks documents in (LaTeX + poppler; no network when it runs)
git clone https://github.com/mrcloudchase/job-search-loop && cd job-search-loop
docker build -t job-search-check -f check/Dockerfile check

# 3. the loop, on the fictional candidate and posting that ship in fixtures/
uv run python -m jobloop apply --profile fixtures/profile.md --posting fixtures/posting.md --out out/demo \
    --drafter claude-code --reviewer codex --minutes 20 --budget 5
```

You get `out/demo/cv.pdf` and `out/demo/cover.pdf`, the reviewer's `critique.md`, and `log.jsonl`. Every turn prints
its response id, cost and diff; `hi show <id>`, `hi diff <id>` and `hi output <id>` replay any of them.
[docs/recorded-run.md](docs/recorded-run.md) is a real run, and what it caught.

**Your own data** goes in `me/` (gitignored, never committed): your profile, tracker, seen jobs and fits.

```bash
python -m jobloop profile --cv me/cv.txt --notes me/notes.md          # -> me/profile.md. Read it.
python -m jobloop portals import path/to/ai-job-search/.agents/skills  # find their portal CLIs (needs bun)
python -m jobloop portals enable freehire                              # opt in, one at a time
python -m jobloop search --query "data engineer" --rank 10             # dedupe, then rank by fit
python -m jobloop apply --posting me/postings/KEY.md --keywords me/fit/KEY.keywords --profile me/profile.md \
    --out me/applications/KEY --tracker me/tracker.csv --key KEY
python -m jobloop interview --posting me/postings/KEY.md --cv me/applications/KEY/cv.tex --cover me/applications/KEY/cover.tex
python -m jobloop gaps --plan codex                                    # what to learn next
python -m jobloop tracker list
```

## What maps to what

| ai-job-search | here | |
|---|---|---|
| `/setup` | `profile` | CV + notes → `profile.md`; nothing may be new |
| `/scrape` | `portals`, `search` | portal CLIs run as plain code, not in a harness; dedupe by a stable key; closed postings dropped |
| `/rank`, Step 1 of `/apply` | `fit`, `search` | weights 30/25/15/30 and the bands are theirs; the total and the go/no-go are computed here |
| `/apply` | `apply` | draft → verify → review (a different harness) → revise once → verify |
| `/interview` | `interview` | prep pack from the posting and the *submitted* documents |
| `/outcome` (tracker) | `tracker` | CSV; the loop can record `found` and `drafted`, never `applied` |
| `/upskill` | `gaps` | the table is arithmetic over saved fits; `--plan` adds a study order |
| `/add-portal` | `add-portal` | a harness on a one-host sandbox writes an adapter; the loop verifies it offline |

**Not ported:** the Gmail and Notion sync, the HTML report, `/reset`, `/add-template` (pass `--templates DIR`
instead), the salary lookup, the company-research cache, and the upstream-update tools. Company research needs web
access, which a sandbox only has if you give it exactly the hosts; nothing here does that except `add-portal`.

## Rules the code keeps

- **The loop decides, not the model.** Compiles, page counts, text extraction, keyword coverage, number grounding,
  quotes, scores, vetoes, spend and time are all checked or computed by code. A harness's "done" is never trusted.
- **Harness-agnostic.** The harness is a name in `model` (`claude-code`, `codex`, `claude-deepseek/deepseek-chat`);
  the role goes in the instructions. Nothing here knows how any harness works, and the drafter and reviewer must be
  different harnesses.
- **Postings are untrusted.** They travel to a harness as a file, never in its instructions; both roles are told to
  read them as data; HTML comments are stripped before anything is derived from them; and a number a posting
  tries to plant ("15 years of Spark") fails the grounding check. The harness sandbox reaches only its model
  provider. The one step that needs the web, `add-portal`, tells you to give its harness a sandbox whose egress is
  exactly the portal's host and nothing else.
- **A person decides.** Nothing submits an application. `apply` presents the PDFs and stops; the tracker will not
  let the loop write `applied` (only `tracker set` can, which is you).
- **Private.** `me/` and `out/` are gitignored. Fixtures are fictional.
- **Stops cleanly.** `--minutes` cancels the turn in flight (`POST /responses/{id}/cancel`); `--budget` starts no new
  turn. Turns that report no cost (Codex reports tokens) are counted, not hidden.

## Layout

```
jobloop/          one module per step, each with a docstring saying its one job
  turns.py        the API client, the deadline, the budget, and the verify-and-send-back loop
  term.py text.py the terminal; text helpers (comparison, numbers, stable keys)
  profile.py portals.py search.py fit.py apply.py interview.py tracker.py gaps.py addportal.py
check/Dockerfile  the throwaway container documents and adapters are checked in
fixtures/         a fictional candidate, a fictional posting (with a planted injection), LaTeX templates
tests/            uv run --extra dev pytest        (-m docker also runs the container tests)
```

Style follows harness-interface's [conventions](https://github.com/mrcloudchase/harness-interface/blob/main/docs/contributors/conventions.md):
the standard library plus `httpx`, the plainest code that works, negative-space tests.

## License

MIT. See [LICENSE](LICENSE). It builds on harness-interface (Apache 2.0) and credits ai-job-search (MIT).

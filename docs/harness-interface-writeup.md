# Building a job-search loop on harness-interface: what I did, step by step

For the author of [harness-interface](https://github.com/mrcloudchase/harness-interface). This is a procedural
account of building [job-search-loop](../README.md) on top of it, pinned at **v1.0.2**, driving it only through its
HTTP API with `httpx` (no library, no patches). It says what I did, in order, what happened, and, in
[its own section](#what-i-would-tell-you), what was smooth and what cost me time. Where a problem was my machine
rather than your project, it says so.

**The task:** a Python program that does what [MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search)
does (profile a candidate, search boards, score fit, draft a LaTeX CV and cover letter, have a fresh-context reviewer
critique them, check the PDFs, prep interviews), but as a loop around *any* harness through your API, in your
`loop.py` style: small files, standard library plus `httpx`, the loop decides and the harness never does.

**The outcome:** all eight features, each a small module with its own tests (188 tests, 5 of which need Docker; all
passing); a recorded run with **Claude Code drafting and Codex reviewing** that ended with two passing PDFs; and every
command exercised live against real harnesses. About 1,570 lines of Python in `jobloop/`.

---

## 1. Procedure

### Step 1. Read before writing (in the order given)

I read, in this order: `README.md`, `how-it-works.md`, `getting-started.md`, `api-clients.md`, `library.md`,
`loops.md`, `examples/build-loop/loop.py` (the whole file), `harness-contract.md`, `sandboxes.md`, `security.md`,
`conventions.md`, `harnesses/README.md`. Then the original project's `.claude/commands/*.md`, `tools/*.py` and the
`.agents/skills/*` portal CLIs.

What shaped the design:

- `loop.py` is the template: an `Api` class that owns every URL, a loop that owns the state, a verifier run in a
  throwaway container, a timer that calls `POST /responses/{id}/cancel`, a budget that starts no new turn, and a
  different harness that reviews. I kept that shape and split it into `turns.py` (API + deadline + budget +
  verify-and-send-back) so each command shares it.
- *Role in `instructions`, harness in `model`* (`api-clients.md`, "An agent on a harness"): so no code anywhere knows
  which harness it is talking to. The drafter and the reviewer are just two strings.
- `input_file` items for every file a harness needs, including the **untrusted posting**, which therefore never goes
  in `instructions`.
- `conventions.md`: "negative space": decide what must never happen, make it impossible, test that. I wrote the
  tests that way (see §3).

### Step 2. Stand up harness-interface

```bash
git clone https://github.com/mrcloudchase/harness-interface && cd harness-interface && git checkout v1.0.2
uv tool install ".[server]"            # note the extra, see §2
export HI_DATA_DIR=/srv/hi-data        # not under /root, see §2
hi server start
hi provider add openai   --url https://api.openai.com/v1            --key-env OPENAI_API_KEY
hi provider add deepseek --url https://api.deepseek.com/anthropic   --key-env DEEPSEEK_API_KEY
hi harness add --builtin codex claude-code
# Claude Code on DeepSeek, from your providers.md recipe (I had no Anthropic key):
hi harness show claude-code | jq '.name="claude-deepseek" | .provider="deepseek"' \
  | curl -sX PUT localhost:8000/harnesses/claude-deepseek -H 'content-type: application/json' -d @-
hi run claude-deepseek/deepseek-chat "Reply with the single word: ready"      # -> ready · $0.0509
hi run codex "Reply with the single word: ready"                              # -> ready
```

Smoke-testing both harnesses with a one-word task before writing any loop code was worth it: it is where I found
the two environment problems in §2, each of which would otherwise have looked like a bug in my loop.

### Step 3. Prove the loop first: one file, `apply.py`

As instructed, a single file with nothing else. It follows `loop.py`'s structure:

```
draft ─► verify ─► review ─► revise ─► verify ─► the PDFs
           │ fail: the failures go back, word for word, on the same conversation (previous_response_id)
           └─► (at most --rounds times)
```

1. **Draft.** `POST /responses` (streamed), `model` = the drafter, `instructions` = the drafter's role, `input` =
   the task text plus `input_file` items: `profile.md`, `posting.md`, `cv_template.tex`, `cover_template.tex`. It
   asks for `cv.tex` and `cover.tex` and reads them back with `GET /responses/{id}/files/{path}`.
2. **Verify, outside the harness.** A throwaway container (`--network none --read-only --tmpfs /tmp:exec`, input
   mounted read-only) runs `pdflatex -no-shell-escape`, then `pdfinfo` and `pdftotext`. The *host* then checks:
   CV is exactly 2 pages and the letter exactly 1; extracted text is non-trivial; every key requirement the profile
   backs is on the CV; any requirement the profile does *not* back is **not** on the CV and **is** named in the
   letter; and every number in either PDF is in the profile. Failures go back as the next turn's input, verbatim,
   with `previous_response_id`.
3. **Review.** A *different* harness, a *new* conversation (no `previous_response_id`), gets the drafts, posting and
   profile as files and writes a critique as its answer. It is told not to change files.
4. **Revise.** The critique goes to the drafter on the drafter's own conversation; re-verify; stop. If the revision
   fails its checks, the loop keeps the earlier passing drafts rather than shipping a regression.
5. **Report.** PDF paths, and each turn's response id, cost, files changed and diff (`hi show` / `hi diff` replay
   them).

`--rounds`, `--minutes`, `--budget` as specified: at the deadline a `threading.Timer` calls
`POST /responses/{id}/cancel` on the turn in flight; at the budget no new turn starts.

**The recorded run** ([docs/recorded-run.md](recorded-run.md)), drafter `claude-deepseek/deepseek-chat`, reviewer
`codex`, on a fictional candidate and posting:

| Turn | Harness | Response id | Result |
|---|---|---|---|
| DRAFT | claude-code (DeepSeek) | `resp_5dda76c26d9a41f3` | check failed: CV is 1 page, must be 2 |
| FIX 1/3 | same conversation | `resp_5e4d3d1671c04ede` | all checks pass |
| REVIEW | codex, new conversation | `resp_785a4d9a5e2942e2` | critique: flagged claims the profile doesn't back |
| REVISE | claude-code, drafter's conversation | `resp_40de8d7a70de4b0d` | all checks pass → two PDFs (2 and 1 pages) |

The fixture posting carries a hidden HTML comment telling "AI assistants" to claim 15 years of Spark and Scala, a
PhD, and "the best employer in Europe". None of it reached either document.

### Step 4. Parity, one feature at a time

After the proof, I pulled the shared parts out of `apply.py` into `turns.py`, `term.py` and `text.py` and wrote one
module per feature. The pattern is the same everywhere: **the harness writes a file; the loop checks it with code;
failures go back verbatim on the same conversation.**

| Feature | Module | What code checks that the harness can't talk its way past |
|---|---|---|
| profile setup from CV + notes | `profile.py` | every number, email, phone and link is in the sources; no year dropped |
| portal search, dedupe | `portals.py`, `search.py` | scrapers run as plain subprocesses (no shell, no API keys in env); output coerced and non-http urls dropped; stable dedupe key; closed postings dropped; an unfetchable posting is never ranked on its title |
| fit evaluation, go/no-go | `fit.py` | each match must **quote the profile** and the quote is verified; the weighted total is computed by code; location/language gates veto |
| apply loop | `apply.py` | §Step 3 |
| interview prep | `interview.py` | every quoted claim is in the CV/letter actually submitted; STAR answers add no numbers; every gap has a bridge; no links |
| application tracker | `tracker.py` | the loop can write `found`/`drafted` and **cannot** write `applied` |
| skill-gap analysis | `gaps.py` | the table is arithmetic over saved fits; only moderate-fit-or-better postings count |
| add a custom portal | `addportal.py` | adapter source checked (import allowlist, no `eval`, no file writes, no foreign hosts); then run on its own saved sample in a no-network container; titles must be in the sample |

### Step 5. Exercise each command for real

With the fictional candidate, against real harnesses, and for search, real public job boards:

```bash
python -m jobloop profile --cv fixtures/cv.txt --notes fixtures/notes.md ...        # passed on the first turn
python -m jobloop portals import <ai-job-search>/.agents/skills && ... enable freehire
python -m jobloop search --query "senior data engineer airflow" --portals freehire --rank 3    # 3 ranked, 87/79/78
python -m jobloop fit --posting ...                                                  # NO-GO: language gate failed
python -m jobloop interview ... --model codex                                        # passed, first turn
python -m jobloop gaps --plan claude-deepseek/deepseek-chat                          # table + study order
python -m jobloop add-portal hn-jobs https://news.ycombinator.com/jobs --model claude-portal/deepseek-chat
```

`add-portal` is the one step that needs the web, so it runs on a harness copy whose sandbox has exactly one extra
egress host:

```bash
hi sandbox add portal-dev portal-dev.Dockerfile --egress news.ycombinator.com
hi harness show claude-deepseek | jq '.name="claude-portal" | .sandbox="portal-dev"' | curl -sX PUT .../harnesses/claude-portal -d @-
```

It took two runs. In the first, the adapter linked out to employers' sites (Hacker News `/jobs` does), the loop rejected it
(urls not on the portal's host), and the budget stop then correctly started no further turn. After I told the harness
up front that urls must be on the portal's host, the second run's first adapter failed the source check (it imported
`pathlib`), that failure went back verbatim, and the second turn passed. The adapter was registered **disabled**; I
read it, enabled it, and ran it live (5 postings, real `news.ycombinator.com/item?id=` urls).

---

## 2. What I would tell you

Nothing blocked me, and I patched nothing. Everything below was worked around in my loop or my environment. Ordered
by how much time it cost.

### What worked very well

- **The shape of the API is exactly what a loop needs.** `previous_response_id` for "send the failures back",
  `/files/{path}` for "read what it wrote", `/cancel` for the deadline, `cost_usd` for the budget. I never wanted a
  field that wasn't there.
- **One request shape across harnesses is real.** My drafter is Claude Code and my reviewer is Codex; the only
  difference in my code is a string. Swapping Claude Code onto DeepSeek by registering a provider and a harness copy
  (your `providers.md` recipe) worked first time.
- **Failures are well reported.** The sandbox build failure arrived as a normal record with `error.code =
  setup_failed` and the tail of the build log in `error.message`. I could diagnose it from `hi show` alone.
- **The per-run hardening held up.** A test in my suite compiles a document that tries `\write18{touch ...}` inside
  the check container; with `-shell-escape` it escapes, with `-no-shell-escape` it doesn't, which is the property I
  want. The same container discipline (no network, read-only root) is simply your `docker run` flags, and copying
  them made my verifier trustworthy.
- **Real harnesses are very good at finding bugs in a verifier.** Four of my checker bugs were found by a harness
  doing exactly what I told it (§3). This is the argument for your "loops, not prompts" in miniature.

### Friction, with what I did about it

1. **`uv tool install .` gives you a `hi` that can't start the server.** `pyproject.toml` keeps `fastapi`/`uvicorn`
   in a `server` extra; `install.sh` installs `harness-interface[server]`, but the `uv add "harness-interface @ git+…"`
   line in `library.md` (and in my brief) gives the *library*. Running `hi server start` after a plain install fails with
   `ModuleNotFoundError: No module named 'fastapi'`. I used `uv tool install ".[server]"`. *Suggestion:* a one-line
   note in `install.md`/`library.md`, or have `hi server start` print "install `harness-interface[server]`".
2. **A server run as root with its data under `/root` can't run any harness.** The run failed with
   `bash: /root/.harness-interface/data/scripts/<harness>/run.sh: Permission denied` (exit 126). Cause: scripts are
   mounted at their host path, the image's `/root` is `drwx------`, and the harness runs as uid 1000, so it can't
   traverse to the mount. `HI_DATA_DIR=/srv/hi-data` fixed it. `sandboxes.md` explains the root-server/uid-1000
   handoff for the *workspace*, but not that the *scripts* mount has the same trap. *Suggestion:* a line in
   `troubleshooting.md`, or mount scripts at a neutral path (the docs already say `{harness_dir}` is a placeholder, so
   the path isn't part of the contract).
3. **Changing a harness's egress means registering a whole harness copy.** For `add-portal` I needed *this one
   turn* to reach *one extra host*. Today that is a new sandbox plus a new harness (`claude-portal`) pointing at it.
   It works and is explicit, which is arguably the point, but a loop that wants per-step egress ends up with a
   handful of harness copies. *Not asking for a change*, just flagging that "the harness is a name in `model`" and
   "the sandbox is part of the harness" together mean one more name per egress profile.
4. **`hi sandbox add` takes a Dockerfile but no build context or build args.** My network re-signs TLS, so
   `npm install` in the default sandbox build failed with `SELF_SIGNED_CERT_IN_CHAIN` after 14 minutes (see "my
   environment"). With no context I couldn't `COPY` a CA bundle; I embedded the five relevant CA certificates in a
   heredoc in my own Dockerfile. *Suggestion:* an optional `--context DIR` (or `--build-arg`) would make
   corporate-proxy setups a one-liner.
5. **The default sandbox is a heavy dependency for a loop that uses two harnesses.** ~10 GB and, here, a 14-minute
   build before it failed. Your `sandboxes.md` already recommends a smaller custom sandbox; I did that (Node,
   Python, git, `claude-code@2.1.42`, `codex@0.163.0`, built in ~3 minutes). Worth saying that this is a *normal* thing
   to do, early, in `getting-started.md`.
6. **`harness.files` is "changed this turn"; `GET /responses/{id}/files` is "the whole workspace".** Both are
   documented, and I still tripped on it: in `add-portal`, a fix turn that changed only `portal.py` made my validator
   think `sample.html` was gone. The fix was `GET …/files` for the listing (I added `Api.listing`) and a regression
   test. *Suggestion:* one sentence in `loops.md`'s "write your own" section: "to see what exists, list `/files`;
   `harness.files` is only what this turn changed."
7. **Cost is not comparable across harnesses, and not always real.** Codex reports tokens (`usage`) and
   `cost_usd: null`. Claude Code reported `$0.0509` for a 17k-token one-word reply on DeepSeek, which is Claude
   Code's own estimate at Anthropic-style prices, not DeepSeek's bill, so it is an upper bound. One `add-portal`
   build turn reported **$4.20** (871 s) against my $3 budget: the budget stopped the *next* turn, as designed,
   but a per-turn ceiling would have stopped *that* one. I handle it by counting unpriced turns and their tokens
   in the summary, and documenting that the budget counts only what is reported. *Suggestion:* a per-request
   `max_cost_usd` / `max_tokens` the server enforces by cancelling, if harnesses expose enough to do it.
8. **Codex logs `failed to refresh available models: Connection failed` on every turn** (its model catalog is outside
   the egress allowlist). Harmless, but it fills `harness.log` and my terminal. I filter nothing; a one-line
   `-c` flag in the Codex harness to skip the refresh would quiet it.

### My environment, not your project

For anyone reproducing this: the machine is a managed container whose outbound traffic goes through a TLS-intercepting
proxy. That is why `install.sh` (a GitHub *archive* tarball) got a 403 (I installed from a `git clone` at the tag
instead), why Docker Hub pulls returned 429 (I pulled `debian` and `node` from `mirror.gcr.io` and re-tagged them),
and why the default sandbox build failed on `npm`'s TLS (point 4). None of this is yours to fix.

---

## 3. Lessons about building a loop on it

- **Test the verifier against the harness, early.** Real runs found four bugs in *my* checks, not in the
  harnesses (and my own tests found two more, marked below):
  - `Airflow` extracted from the PDF as `Airow`. `fi`/`fl` ligatures vanished because my check image lacked
    `cm-super`, so T1 fonts were bitmaps. That is also what an ATS would have read. Fixed in the image and the
    templates, with a test.
  - The posting's own reference number failed the "no number the profile lacks" rule. A `Reference:` line is now
    quotable; no other posting number is.
  - On a real Polish posting, the harness returned sentence-length "requirements" (`6+ lat doświadczenia`) and my
    rule "the letter must name each gap" became absurd. The schema now demands short names (1–3 words), enforced in code.
  - A fix turn that changed only `portal.py` made my validator lose `sample.html` (point 6 above).
  - *Found by my own tests:* a skill listed under "Not yet: Scala, Spark" counted as *backed* by the profile (a
    substring match); a non-literal `open(path, mode)` slipped past my adapter source check. Both fixed.
- **Grounding by quotation beats grounding by trust.** In `fit`, a harness may claim a match only by quoting the
  profile, and the code checks the quote. The model's judgement is used, but it cannot forge its evidence.
- **Keep the model's number out of the decision.** The harness reports sub-scores; the loop computes the total, the
  band, the veto and go/no-go. A reported `total` is ignored (there is a test).
- **Make the dangerous action impossible, not discouraged.** `tracker.record()` raises on `applied`; only the human
  `tracker set` command can write it. Test: for each of seven statuses, `record` raises and no file is created.
- **Postings are untrusted at every hop.** They travel as files; HTML comments are stripped before any term is
  derived; portal output is coerced to short text and non-http urls are dropped; the `add-portal` adapter is code from
  a harness that read untrusted pages, so it is source-checked, run only offline in a no-network container, and
  registered **disabled** until a person reads it.

## 4. Reproduce it

```bash
git clone https://github.com/mrcloudchase/job-search-loop && cd job-search-loop
docker build -t job-search-check -f check/Dockerfile check
uv run python -m jobloop apply --profile fixtures/profile.md --posting fixtures/posting.md --out out/demo \
    --drafter claude-code --reviewer codex
uv run --extra dev pytest -m "docker or not docker"      # 188 tests
```

Credit: the workflow, the fit weights and bands, the drafter/reviewer idea and the portal CLIs are
[ai-job-search](https://github.com/MadsLorentzen/ai-job-search)'s (MIT). No file of theirs was copied; code and prompts
here are new. Thank you for harness-interface. The whole thing was possible because the API did the boring parts.

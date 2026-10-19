# job-search-loop

An AI job search that runs as a **loop around coding harnesses**, driven through
[harness-interface](https://github.com/mrcloudchase/harness-interface) (v1.0.3). A harness drafts, scores and critiques;
**code decides**: it verifies every document in a throwaway no-network container, sends failures back to the harness
word for word on the same conversation, applies a reviewer's edits only when they match exactly once and add no
ungrounded claim, and enforces every guarantee that matters (grounding, eligibility vetoes, page counts, ATS text layer,
tracker vocabulary) in plain Python. The model never submits, sends, or marks anything applied: **a person decides**.

Python, stdlib plus `httpx`. About 15,000 lines of code and 17,600 lines of tests; every behaviour has a test, including
what must never happen.

## Credit

This rebuilds the *outcomes and quality bar* of **[ai-job-search](https://github.com/MadsLorentzen/ai-job-search)** by Mads
Lorentzen (MIT). **No code is borrowed**: nothing of it is copied, vendored, imported or run by this repository (its
prompts, templates, portal CLIs, `cover.cls`, tools and tests were not used). The only contact is the blind benchmark under
`docs/recorded-run/09-blind-comparison/`, where its `/apply` is run as a black box to compare outputs. The original was read for
its behaviour and its `CLAUDE.md` verification checklist; this is not a port. Upstream fonts (Lato, Raleway, SIL OFL) are
fetched at image build with pinned hashes.

## What it covers

Run `python -m jobloop --help`. One command per phase:

| Phase | Commands | In one line |
|---|---|---|
| Profile | `profile`, `enrich`, `queries` | Build `profile.md` from a documents folder, one CV, or an interview; every number, date, email, phone and link is grounded in your sources by code; inferred traits are labelled; you confirm before anything is written. Public GitHub/portfolio enrichment is additive and source-tagged. Search queries per function in every declared language. |
| Search | `search`, `search boards`, `search health` | Own adapters for linkedin, freehire, jobindex, jobnet, jobbank, jobdanmark behind one contract; robots checked first; no shell, no keys; Danish boards off by default; dedupe, expiry, health check. |
| Rank / fit | `rank`, `fit`, `calibrate` | Weighted score in code (30/25/15/30), eligibility/permit/location/language gates with a FAIL veto; quoted evidence checked; unreachable postings retried then given up, 403/404 never "closed". |
| Apply | `apply`, `forms` | Gates, source-host verification, verified company research, a 2-page moderncv CV and a 1-page letter compiled in a container, drafter and reviewer on **different harnesses**, structured edits applied by the loop, `drafted` recorded and nothing else. |
| Lifecycle | `tracker`, `followup`, `stale`, `report` | Status vocabulary with no going backwards; archive per application; follow-up *drafts* only; a static HTML report with escaped inline SVG; CSV-safe. |
| Interview | `interview` | A pack per stage from the profile and the submitted papers, a consistency brief, honest bridges, mock mode. |
| Upskill | `upskill` | Gaps weighted by fit with provenance; only resources the loop fetched itself. |
| Add a board | `addboard` | A harness builds an adapter to the contract; the loop verifies it offline in a no-network container and registers it **switched off** until you read it. |

Coverage is not command-for-command parity with the original. The contract is **[docs/parity.md](docs/parity.md)**: about
1,600 rows, one per guarantee, each naming where it lives here and the test that pins it, with a status of `done`,
`stricter here` (enforced in code where the original only said it in prose) or `not ported, because ...`. There are no
`planned` rows (`python tools/parity.py --check --done`). Out of scope by instruction: Gmail sync, Notion sync, fork and
upstream-sync tooling, `/reset`.

Networks are written down in [docs/network.md](docs/network.md): which role reaches which hosts and why, and what was
measured.

## Recorded runs

Real runs, committed under [docs/recorded-run/](docs/recorded-run/), each stating which provider it used. This machine has
**no Anthropic key**: "Claude Code" runs here on **DeepSeek** (the shipped `claude-code` harness copied with another
provider), and Codex runs on **OpenAI**. Costs are each harness's own estimate.

| Run | What it shows |
|---|---|
| `01-proof` | the one-file `apply.py` loop, two harnesses, a prompt injection in a hidden HTML comment |
| `02-search` | search over all six boards with own adapters (jobbank refuses on robots.txt, and says so) |
| `03-rank` | ranking with vetoes, an unreachable and an expired posting |
| `04-apply-attempts`, `04a-…`, `04b-…` | the apply loop's failed rounds (kept: they are the checks working), a passing run, and a run with verified company research |
| `05-upskill`, `06-addboard`, `07-interview`, `08-followup` | the rest |
| `09-blind-comparison` | the original's `/apply` versus this one, scored blind against the original's own checklist |

## Run it

```
curl -fsSL https://raw.githubusercontent.com/mrcloudchase/harness-interface/main/install.sh | sh
uv sync --extra dev
docker build -t job-search-check:2 check        # TeX, poppler, fonts; no network at run time
python tools/register_harnesses.py              # role harnesses and the research sandbox
python -m jobloop apply --me me --url <posting> --drafter claude-code --reviewer codex
```

Personal data lives only in the gitignored `me/` folder; fixtures are fictional. If the server runs as root, set
`HI_DATA_DIR` outside `/root`. Tests: `uv run --extra dev pytest -q` (add `-m docker` for the container tests).

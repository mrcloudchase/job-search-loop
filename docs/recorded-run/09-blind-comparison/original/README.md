# Original project run (MadsLorentzen/ai-job-search, `/apply`)

Benchmark output of the ORIGINAL project for the fixed candidate (`fixtures/profile-cph.md`, Alex Fernsby) and the fixed posting (Too Good To Go, Senior Data Engineer, Copenhagen), for blind comparison with the rebuild.

## Result
- `cv/main_too_good_to_go_senior_data_engineer.pdf` (here: `main_too_good_to_go_senior_data_engineer.pdf`): 2 pages, plus `.tex`
- `cover_too_good_to_go_senior_data_engineer.pdf`: 1 page, plus `.tex`
- Both PDFs exist and were compiled by the original's own flow (lualatex for the CV, xelatex for the letter).
- Also written by the run: `job_search_tracker.csv` (row, fit 82), `too-good-to-go.json` (company research cache), `converted-inputs/archived_job_posting.md`.

## Command
Checkout copy at `/tmp/run/ai-job-search` (copy of the clean MIT clone; `/tmp/orig` untouched), run in a docker container from `job-search-check:2` (has lualatex, xelatex, poppler, python3; no bun/git, not needed for pasted text). `run.sh` is included. Essentially:

```
claude -p "$(cat prompt.txt)" --model deepseek-chat --output-format stream-json --verbose --dangerously-skip-permissions
```
inside `docker run --user root --network host` (node22 and claude-code mounted read-only, checkout mounted at /w, cwd /w, `IS_SANDBOX=1`, `python` symlinked to python3).
`prompt.txt` = `/apply ` + the verbatim posting text + a short note ("pasted text, answer yes / proceed to draft without asking, non-interactive, finish through Step 6").

## Provider
DeepSeek via its Anthropic-compatible endpoint, same as the harness `claude-deepseek` (provider url `https://api.deepseek.com/anthropic`): `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`, `ANTHROPIC_AUTH_TOKEN` taken from `DEEPSEEK_API_KEY` (passed by variable name, never written), model `deepseek-chat` for all slots (`ANTHROPIC_MODEL`, SMALL_FAST, DEFAULT_OPUS/SONNET/HAIKU, `CLAUDE_CODE_SUBAGENT_MODEL`). The CLI printed a harmless `unrecognized_model` notice. The reviewer subagent ran on the same model.

## What was converted / adapted by hand (see `converted-inputs/`)
Emulated the original's `/setup` (Path B style) by hand from the profile, adding nothing and dropping nothing the profile states:
- `CLAUDE.md` Candidate Profile (identity, languages, CV language English, status, eligibility, education, all 3 roles with every bullet, skills strong/working/not-yet, projects, certifications, publications, awards, behavioural profile with the stated/inferred tags, wants/goals, writing style, deal-breakers)
- skill files 01, 02, 04 (skills areas, goals, flexibility; eligibility/start-date note), 05 (contact block, one profile statement taken from the profile Summary), 06 (contact line, signature), 07 (the single STAR stub left empty as the profile has it), 03 (profile writing style appended), `.claude/skills/job-scraper/search-queries.md` (settings and queries prepended)
- `cv/main_example.tex` master CV with the full content; `cover_letters/cover_example.tex` name/contact
- Profile has no LinkedIn, so LinkedIn links were removed from the templates; GitHub kept. Dates in the master CV use years (`2022-Present`); months are kept in 01 and CLAUDE.md. Settings (Malmö/Aarhus/Groningen tiers etc.) only landed in search-queries.md (not used by `/apply`).
- Not done: salary data (none in profile), /scrape portals, bun installs.

## Did its own verification pass?
Yes. Step 5 mechanical checks: `tools/verify_pdf.py --pages 2` / `--pages 1`, `verify_layout.py`, and ATS extraction with `--ascii-dates --dump-text` (extractor pypdf, installed into the container from a wheel via PYTHONPATH). The cover letter initially spilled to 2 pages (verify_pdf failed, layout flagged an 89 percent empty page 2); after several trimming iterations it ended at 1 page, "layout: clean", and all final checks exit 0. The run's own checklist report (all PASS, ATS keyword table with honest gaps: Redshift, dlt, Metaflow, AWS services, inventory, Agile) is at the end of `transcript.txt`. Tool output is in `verification-output.txt`. Note these are the model's own self-reported passes plus the scripts' exit codes. The run listed a few "before you send" caveats (e.g. "I have not used dlt or Metaflow in production" and "I push back on requirements" are claims not stated in the profile).

## Cost / time
Reported `total_cost_usd` 9.22 with 104 turns, 982 s wall time, about 5.6M cache-read tokens, 97k input, 75k output. This USD figure comes from Claude Code's own estimate for an unrecognised model and is not DeepSeek's real billing; the true DeepSeek cost is far lower. Under the 45 min / $10 limits either way.

## Problems / caveats
- Container ran as root with `IS_SANDBOX=1`; the workspace was reported as "not trusted", so `.claude/settings.json` allow-list entries were ignored (irrelevant with bypassed permissions).
- With pasted text the run still tried guessed URLs (a Greenhouse job URL, company pages) via WebFetch/curl and did web research (9 WebSearch/WebFetch calls plus curl) for company claims; it cited a CEVA Logistics parcels figure it verified via trade press.
- A stray `texput.log` was created and removed by the run. The `.txt` ATS dump was deleted by the run per its own step 5d.
- Files: `transcript.txt` (readable, ANSI stripped, tool results truncated to 4000 chars each), `transcript-raw.jsonl` (full stream-json, ANSI-free), `prompt.txt`, `run.sh`.

---
Note added when committing: the hand-converted inputs (the candidate's profile re-typed into the original's file layout, plus the
original's own template text) and the 12 MB raw stream-json transcript were not committed. This repository contains no file of the
original; only the original's *output* for the benchmark (its PDFs, `.tex`, verification output and a readable transcript).

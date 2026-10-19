# Blind comparison: the original's `/apply` against this one

**Same inputs.** The fictional candidate (`fixtures/profile-cph.md`) and one public posting (Too Good To Go, Senior Data Engineer,
Copenhagen, a LinkedIn page read verbatim). **Same provider for both makers:** DeepSeek (`deepseek-chat`) through Claude Code.

- `original/`: the original project's `/apply` (MadsLorentzen/ai-job-search, MIT) run as a black box, headless, with the
  candidate hand-converted into its profile layout. See `original/README.md` for the command, the conversions and its own
  verification (its page counts, ASCII dates, layout and ATS checks passed at the end; its letter first spilled to a 2nd page
  and was trimmed). Its run did its own web research and cost an estimated $9.22 (Claude Code's estimate for an unknown model).
- `rebuild/`: this repository's apply loop, recorded run `../04a-apply-tgtg/` (drafter DeepSeek, reviewer Codex/OpenAI).
  Its company research found nothing it could verify (the employer's site was behind a bot challenge), so the letter makes no
  company claims.

**Scoring.** `rubric.md` is the original's own `CLAUDE.md` "Verification Checklist", 24 items (reworded only where it names the
original's files), plus an overall 1 to 10. `tools/blind_score.py` shows a scorer the two applications as **X and Y in random
order**, as text extracted from the PDFs plus page counts and nothing else, with the candidate's profile and the posting; the
key (`key.json`) is written only after the scorer has answered. The scorer must answer every item for both, with evidence;
the loop checks the shape. Two scorers, each shuffled independently:

| Scorer run (folder `scores-…`) | Order | Rebuild: pass / fail / na, overall | Original: pass / fail / na, overall |
|---|---|---|---|
| `claude-reviewer-deepseek-chat` | rebuild shown first (X) | 23 / 0 / 1, **8/10** | 19 / 5 / 0, **5/10** |
| `claude-reviewer-deepseek-chat-swapped` | original shown first (X) | 18 / 6 / 0, **6/10** | 19 / 5 / 0, **5/10** |
| `codex` | rebuild shown first (X) | 20 / 4 / 0, **7/10** | 19 / 4 / 1, **6/10** |
| `codex-swapped` | original shown first (X) | 20 / 3 / 1, **7/10** | 18 / 4 / 2, **6/10** |

**Read this honestly.** The rebuild's overall score is at or above the original's in all four runs, but the margin is small and noisy: the same scorer gave the rebuild 8 and then 6 when the order flipped. One application per system: it is a small comparison. Only two harness
families exist on this machine, so there was no truly third harness: the DeepSeek scorer is the same family as the maker of
both documents, and the OpenAI scorer reviewed (but did not draft) the rebuild's letter during its own run. The scorers have no
web access, so rubric item 4 (verified company claims) is judged on plausibility only. Where the scorers disagreed with each
other on an item, nothing was adjudicated.

**Where the rebuild scored lower (the `codex` scorer, items 7, 12, 14) and why.** The letter calls CI/CD and stakeholder
management *gaps* while the CV shows GitHub Actions deployments and a five-team data contract. The cause is by design:
the loop decides a requirement is backed only if the profile literally says the term, so "CI/CD" is a gap and the letter must
acknowledge it, which then reads as a contradiction next to adjacent evidence. It is the strict side of "never stuff a keyword",
not a fabrication, but it is a real weakness: a synonym table from the fit step (the keyword checker already supports
synonyms) would let the letter bridge instead of deny. Not fixed in this round. Item 14 is one awkward sentence in the letter.

**Where the original scored lower**, per the scorers: an invented company fact in its letter ("300,000 parcels with CEVA
Logistics since 2024", in no input), dates cut to years, icon-name artefacts in the CV header text layer ("MOBILE-ALT",
"Envelope"), a generic salutation variant, an unsupported "GitOps-style" claim on the CV, and a long letter likely to spill.

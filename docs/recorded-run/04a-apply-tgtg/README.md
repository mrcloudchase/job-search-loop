# Recorded run 4a: a full apply where the employer's site gave nothing verifiable (Too Good To Go)

`python -m jobloop apply --url <linkedin posting> --company-domain toogoodtogo.com --drafter claude-deepseek/deepseek-chat --reviewer codex ...`
**Providers:** drafter, fit, facts and researcher = Claude Code harness copies on **DeepSeek**; reviewer = **Codex on OpenAI**.

Both PDFs pass every check. The research turn found the employer's site behind a bot challenge (it tried for 8 minutes, in
its own transcript); it returned `[]`, so `research.md` says "None. Make no claims about the company." and the letter makes
none: the loop does not guess. The reviewer's 7 structured edits were all applied by the loop. Fit 74 (drafted, fictional
candidate); the veto gate stopped two earlier attempts (see `../04-apply-attempts/`).

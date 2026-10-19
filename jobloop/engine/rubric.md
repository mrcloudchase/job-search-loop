# Rubric: the original's own verification checklist

These items are the "Verification Checklist" of the original project's `CLAUDE.md` (MadsLorentzen/ai-job-search, MIT),
reworded only where they name the original's own files. The scorer sees two anonymous applications, X and Y, for the same
fictional candidate and posting, and answers each item per application: `pass`, `fail`, or `na` (cannot be judged from the
inputs, or does not apply), with one sentence of evidence. A scorer has no web access: for company claims it judges whether
each is attributed to something the inputs support, and marks `na` when it cannot tell.

## Factual accuracy
1. All claims match the candidate's profile: no fabricated skills, experience or achievements.
2. Job titles, dates, company names and locations are correct.
3. Contact details are correct.
4. Company-specific claims (partnerships, products, technology, expansions) are verifiable and stated modestly; none is invented.

## Targeting
5. The profile statement / opening paragraph is tailored to this role (not generic).
6. Skills and experience bullets are reframed to match the job's requirements.
7. Key requirements are addressed, with gaps acknowledged where relevant.
8. Nice-to-have requirements are highlighted where there is a match.

## Consistency
9. The CV follows a standard 2-page banking-style format.
10. The cover letter follows an established structure (header, salutation, body, sign-off).
11. Tone is consistent across the CV and the letter.
12. No contradictions between the CV and the letter.

## Quality
13. No LaTeX artefacts (stray commands, broken characters) in the extracted text.
14. No spelling or grammar errors.
15. The letter is addressed correctly ("Dear Hiring Manager" if no person is named).
16. The letter fits one page.
17. CV section headings match the CV's language.

## Compiled PDF verification
18. The CV is exactly 2 pages.
19. No orphaned entry titles at the bottom of a page.
20. The letter is exactly 1 page, signature included.

## ATS and keywords (CV)
21. The text layer extracts cleanly: no `(cid:` markers, no replacement characters.
22. Email and phone appear as literal text in the extraction.
23. Reading order of the extracted text matches a sensible visual order.
24. Posting keywords are covered or honestly absent; genuine gaps are visible and never stuffed.

Beyond the checklist, the scorer gives each application an overall score from 1 to 10 for how ready it is to send after one
read-through by the candidate, with two sentences of reasons.

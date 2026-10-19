# Recorded run 2: search across the six boards

Own adapters (`jobloop/boards/*.py`), plain `httpx`, no shell, no keys. Query "data engineer", last 14 days, limit 5.
Danish boards (jobindex, jobnet, jobbank, jobdanmark) were switched on for this run with `search boards enable`; linkedin
and jobbank need `--acknowledge-terms` (their terms restrict automated access; personal, low-volume use only), which I
passed because the brief asked for all six. **No model provider was used**: search is plain code. Raw output: `raw.txt`,
`health.txt`.

| Board | Result |
|---|---|
| freehire | 5 postings |
| linkedin | 5 postings (guest endpoint, terms acknowledged) |
| jobindex | 2 postings (Copenhagen) |
| jobnet | 4 postings with place "København" (0 with "Copenhagen", which the board does not know) |
| jobdanmark | 5 postings with "København"; "Copenhagen" is refused with a message naming the fix |
| jobbank | **0: refused.** `robots.txt` does not allow `jobbank.dk/job/rss`; the adapter fails closed and says so. Not worked around. |

Found while running: the health check called jobindex "broken" because the first result was an external ad (a Workday URL)
whose detail page the adapter rightly refuses to fetch off the board's host. The check now skips external ads
(`test_a_result_that_is_an_external_ad_is_skipped_and_the_board_is_not_called_broken`); the second health run is in `health.txt`.

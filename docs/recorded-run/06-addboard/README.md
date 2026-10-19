# Recorded run 6: a new job board added (arbeitnow)

`python -m jobloop addboard arbeitnow https://www.arbeitnow.com --host www.arbeitnow.com --query "data engineer" --model claude-portal/deepseek-chat`

**Provider: DeepSeek**, through `claude-portal`, a copy of the shipped `claude-code` harness that may write files and fetch
pages, on the `research` sandbox widened to exactly `www.arbeitnow.com` for the build turn (see docs/network.md).
`raw.txt` is the whole run; `adapter/` is what the harness wrote (adapter, board.json, the live-test result).

What happened, in order:
1. The harness read robots.txt (allows the paths), the terms (personal, non-commercial use; the API asks not to be abused)
   and found the JSON feed ignores `search`, so it used the site's own search endpoint. Its own words in the transcript:
   an instruction aimed at LLMs on the About page was noticed and ignored as data.
2. The loop verified the adapter **without trusting the harness**: a source check on the host, then in the no-network
   read-only container `parse` on every saved sample, bad flags, bad numbers and a missing file, then search and detail
   with no network. The first build failed ten checks; the failures went back verbatim, round two failed seven, round three
   passed all of them.
3. Registered **switched off** with the personal-use warning; the person must read `adapter.py` and then run
   `python -m jobloop search boards enable arbeitnow --acknowledge-terms`. I did not enable it.

Limits, stated plainly: the live test ran inside the harness sandbox and the loop did not rerun it (it only checks that the
saved results' titles appear in the saved samples); an adapter running on the host has no network confinement, so the
source check and the person reading it are the controls.

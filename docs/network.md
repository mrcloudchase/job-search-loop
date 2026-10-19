# Network profiles

Every network profile this repository uses, why it has that shape, and what was measured to decide it. A sandbox in
harness-interface is a Dockerfile plus an exact `egress` list (no wildcards), and it is fixed per harness, so each role
below is a harness copy (registered by `tools/register_harnesses.py`, derived from the installed `claude-code` at
registration time; nothing of harness-interface is copied here). The harness's own provider host is always reachable
through the door; the lists below are what is added on top.

| Role (harness name) | Sandbox | Egress | Why |
|---|---|---|---|
| drafter (`claude-code`) | `default` | provider only | Writes `cv.tex`/`cover.tex` from files the loop placed. The profile is in its workspace, so it gets no web: nothing it reads can carry the profile out. |
| reviewer (`codex`, or `claude-reviewer`) | `default` | provider only | Audits grounding against `profile.md`. `claude-reviewer` additionally denies Write/Edit/WebFetch/WebSearch in `.claude/settings.json` (`artifacts`), so it can only answer. |
| fit scorer, posting-facts reader, interview, upskill, follow-up, profile builder | `default` | provider only | They read untrusted text and the profile. They need no web, so they have none. |
| researcher (`claude-research`) | `research` | none until widened, then the employer's exact hosts for one turn | Never gets the profile. `jobloop/research.py` widens the sandbox with `PUT /sandboxes/research` before one research turn and resets it to `[]` after, only while research turns are serialized (one sandbox, one policy at a time). Findings come back as claims with a source URL and a verbatim quote; the loop re-fetches each URL itself (robots check first, plain code, host-side) and drops any claim whose quote is not on the page. |
| web researcher for `upskill` and `interview` (`claude-research`, the same role as the researcher) | `research` | none: WebSearch runs at the provider (probe below); for `interview --company-domain` the company's exact hosts for one turn, as above | Gets a gap's name, a company name or an interviewer's name and company, as text: no file, so no profile or posting. `jobloop/webresearch.py` treats everything it returns as a lead and fetches each page itself, host-side with `httpx`: https only, honest User-Agent, robots check failing closed, size/time/page-count limits, redirects only to other https hosts, and any host that is an IP literal or resolves to a private, loopback or link-local address is refused. A resource is listed, and a claim kept, only if the page fetched now holds it (the quote, verbatim). The open web is reachable by the loop's fetch only because every page is untrusted data that is stripped and never followed; the harness itself can reach nothing. |
| board builder (`claude-portal`) | `research` | exactly one board's hosts for the build turn | `jobloop/addboard.py` widens it, the harness writes an adapter and saved samples, and the loop then verifies it offline in the no-network check container. It may write files and fetch; it never gets the profile. |
| check container (`job-search-check:2`) | not a harness sandbox | none (`docker run --network none`, read-only root, uid 1000) | Compiles the model's LaTeX and inspects the PDFs. Compiling untrusted LaTeX is code execution, so it runs with no network, no secrets and no profile outside the two `.tex` files. |
| board adapters, robots checks, posting fetches | host-side plain code | the board's hosts, from `httpx` | Never run by a harness. No shell, no API keys in the environment, output treated as untrusted. |

## Probes (against harness-interface v1.0.3, Claude Code harness)

Run before deciding the profiles above; the response ids are in the server's history.

- **Web search runs at the provider, not in the sandbox.** `WebSearch` returned results from a sandbox with `egress: []`
  (resp_3deec3ce4ade41c4), so allowing it needs no host and a harness that must not search needs the CLI's own `deny` rule,
  not the network. The research role therefore relies on source verification by the loop, not on the sandbox, to keep
  search results honest.
- **`WebFetch` needs `skipWebFetchPreflight: true`** in the settings file (its preflight calls a host the sandbox does not
  allow); with it, fetches work on allowed hosts only. The door answers 403 for every other host (resp_3722c7602d5b4945).
- **`deny` rules for Write/Edit hold under bypass-permissions mode**, so a per-role `artifacts` settings file is a real
  restriction and not advice.
- The sandbox image for a Dockerfile is tagged `sha256(Dockerfile)[:12]`; the `research` sandbox is
  `FROM harness-interface/default:<that tag>` and builds with no context.

## Wrongly-reachable would be a bug

Tests that pin this: `tests/test_register_harnesses.py` (roles, settings, empty egress), `tests/test_webresearch.py` (private hosts and IP literals never requested, robots-blocked pages never fetched, profile never sent), `tests/test_research.py`
(widened only for the turn, reset on error, hosts exact, profile never sent), `tests/test_latexrun.py` (the container is
run with `--network none`).

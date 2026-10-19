# Board knowledge note: freehire.me (public JSON API aggregator)

Audience: the person writing `boards/freehire` (Python, httpx) without seeing the original.
Sources: `.agents/skills/freehire-search` (SKILL.md, url-reference.md, cli/src/*.ts, cli/tests/*) and live requests on 2026-10-19 with User-Agent `job-search-loop-research/0.1`.

## 0. Position on robots, terms, personal use

- This is the friendliest board of the three: a documented, public, unauthenticated JSON API. No login, no key for reads. Only per-user tracking (apply/save/me) needs a bearer key and the original deliberately never touches it; the rebuild must stay search plus detail only. `GET /api/v1/auth/me` answers 401.
- robots.txt (live, 200): catch-all group allows `/` and disallows `/my/`, `/signin`, `/jobs/*/discussion`, `/companies/*/discussion`; the Googlebot and Bingbot groups additionally disallow `/api/`. The comment block addressed to bots says the catalogue is a public JSON API, that scraping HTML is unnecessary, lists llms.txt, an OpenAPI file, API docs, an MCP endpoint and a CLI page, and asks (not requires) for a User-Agent that names the project with a contact URL, so operators can warn before limits change. So an RFC 9309 check as a generic crawler (catch-all group) PERMITS `/api/v1/...`. The rebuild's robots gate will pass; run it anyway.
- No personal-use restriction is stated by the original or in robots. The service is a personal project run best-effort with no SLA (SKILL.md lines 41-50). The code is open source (backend repo named `strelov1/freehire`, MIT, Go, PostgreSQL, Meilisearch), so a base URL override for self-hosting exists (`FREEHIRE_API_URL`, default `https://freehire.me`, trailing slashes stripped, blank means default). Keep that override as a config setting.
- Original UA was `MadsLorentzen/ai-job-search/1.0 (+repo URL; freehire-search)` plus `Accept: application/json`. Use the same shape for the new project: `<project>/<version> (+<contact URL>; boards/freehire)`.

## 1. Endpoints

| Purpose | Method and path | Notes |
|---|---|---|
| Search with FULL descriptions | GET `/api/v1/agent/jobs/search` | Same query surface, ranking and pagination as the web search, but each hit's description is replaced with the complete text read from the database, rendered per `description_format`. Hydration cannot be turned off. This is the default for `search`. |
| Search, lean | GET `/api/v1/jobs/search` | Same surface; description is the index's truncated preview. Used when the caller wants no bodies (the original's `--no-description`) because the agent endpoint would do wasted database work. The original then nulls every description client-side so output never carries a partial text. |
| Detail | GET `/api/v1/jobs/{slug}` | One job by `public_slug`. Serves closed postings too (non-null `closed_at`). Description arrives as HTML. |
| Facet vocabulary | GET `/api/v1/jobs/facets` (optional `?q=<role>` and the same filters) | Not called by the original at runtime; it is the source for valid facet values. The adapter or a refresh script should use it to validate/auto-complete facet values rather than guessing. |
| Auth probe | GET `/api/v1/auth/me` | 401; never used. |

Envelope on every response: `{ "data": ..., "meta": {...}, "error": "..." }`. Lists: array in `data`, pagination in `meta` as `{ total, limit, offset }`. Single item: object in `data`. Errors: `{ "error": "<message>" }` with a 4xx/5xx status; 404 is `{ "error": "not found" }` (confirmed live for a non-existent slug).

## 2. Search parameters

Pagination is offset/limit: `limit` (CLI default 25) and `offset = (page - 1) * limit`. Server rule: `offset + limit` must not exceed 10,000. The original sends both always.

| Query parameter | Source flag in original | Values and behaviour |
|---|---|---|
| `q` | query | Full-text; optional (an unfiltered search is valid). Sent only when non-empty. |
| `limit`, `offset` | limit, page | see above |
| `description_format` | description-format | `markdown` (the original's default), `text`, `html`. Agent endpoint only; NOT sent to the lean endpoint. An unrecognised value is NOT an error server side (it silently falls back to html), so the client must validate (original does). |
| `posted_within_days` | jobage | Whole days. Sent only when greater than 0 and below the 9999 sentinel. |
| `work_mode` | remote | `remote`, `hybrid`, `onsite` (live facet counts confirm exactly these three). The original lets a bare `--remote` mean `remote`. |
| `company_slug` | company | single slug from a result's `company_slug` |
| `regions` | region | repeatable; OR within the facet. Doc values: global, eu, us, apac, latam, cis; plus the special value `none` meaning "region could not be resolved". LIVE DISCREPANCY (2026-10-19): the live facet list for regions includes values such as africa, americas, apac, asia, au, br, and in result rows I saw `eu`, `north_america`, `uk`. So the documented list is stale; read values from the facets endpoint. |
| `countries` | country | repeatable; ISO 3166 alpha-2. The reference file says lowercased, but live the server treats `DE` and `de` identically (both returned the same total). Lower-case anyway. |
| `cities` | city | repeatable; display name such as `Berlin` |
| `seniority` | seniority | repeatable; live values include c_level, intern, junior, middle, senior, staff, lead, principal |
| `category` | category | repeatable; live values include administration, ai_engineering, architecture, backend, blockchain, business_analysis, frontend, fullstack, devops, ml_ai, qa (examples only; use the facets endpoint) |
| `skills` | skill | repeatable; canonical lower-case names such as go, kubernetes |
| any other facet | `--facet key=value` | Escape hatch. Live facet keys seen: ai_archetype, ai_interview, auto_apply_available, category, cities, collections, company_size, company_type, countries, domains, education_level, employment_type, english_level, is_tech, posting_language, reality, regions, relocation, requires_clearance, role_type, salary_currency, salary_period, seniority, skills, source, visa_sponsorship, work_mode. Numeric range style keys such as `salary_min` are mentioned in the original docs. |

Combination semantics: repeated values of one parameter are ORed; different parameters are ANDed; geography parameters OR into one location group. Comma lists on the CLI are split and sent as repeated parameters (never as one comma-joined value). Whitespace around items is trimmed and empty items dropped.

Mapping to the shared contract: `query` to `q`; `location` has no free-text equivalent, so the adapter maps the contract's location to `countries` (ISO codes) and/or `regions`/`cities`, and the loop must post-filter on the returned `location` text because facets are incomplete; `recency` to `posted_within_days`; `limit` to `limit` (server-side page size) with client-side slice only if the caller wants fewer than a page.

Total count: `meta.total` is an "estimated match count". Live example: `q=go` unfiltered on the agent endpoint reported 511,555; with `posted_within_days=7` and `regions=eu` on the lean endpoint it reported 6,738. Do not treat total as exact.

## 3. Result fields: JSON paths and normalisation

Observed job object (live, lean search, 2026-10-19), keys: `public_slug`, `source` (the upstream ATS, for example comeet or greenhouse), `manually_added`, `external_id`, `url`, `title`, `company`, `company_slug`, `location`, `description`, `countries`, `regions`, `work_mode`, `skills`, `cities`, `collections`, `is_tech` (live value "non_tech" seen; the original ignores this field), `posted_at`, `created_at`, `updated_at`, `last_seen_at`, `closed_at`, `enrichment` (object, `{}` when not enriched), `enriched_at`, `enrichment_version`, `view_count`, `applied_count`, `upvote_count`, `downvote_count`, `my_vote`, and `reality` (object with class such as "stale", age in days, repost count, mass-posting count and a fake-freshness flag). The `reality` object is new relative to the original and is valuable for the loop's staleness and ghost-posting checks; consider surfacing it. The internal numeric id is never exposed.

| Contract field | JSON path | Rule |
|---|---|---|
| id | `public_slug` | Stable identifier; also what detail takes. |
| title | `title` | Decode HTML entities (once), trim; if the result is empty use "(untitled)". Real titles contain `&amp;`, `&#38;`, trailing spaces. |
| company | `company` | Empty string becomes null. Also keep `company_slug`. |
| location | `location` | Free-text ATS location ("Barcelona, CT", "India"); empty becomes null. Structured geography is in `regions`, `countries`, `cities`; an empty array means "unknown", not "none". |
| date | `posted_at` | ISO timestamp; keep only the leading `YYYY-MM-DD` (regex on the first ten characters). Offsets are not converted: `2026-07-06T15:30:00+02:00` becomes `2026-07-06`. Null if absent or not date-like ("soon", empty). |
| deadline | none | The API has no deadline field in what the original reads; set null. |
| url | `url` | The real posting URL on the employer's ATS host, frequently with tracking parameters appended by freehire (`utm_source=freehire.me`, `_gl`, `_ga`). Strip tracking parameters in the loop's URL normaliser. |
| description | `description` | Search (agent): full text already rendered in the requested format; pass through VERBATIM (do not strip tags again, that would destroy Markdown). Empty string becomes null. Lean mode: set null. Detail: HTML, so strip it (below). |
| extras | `work_mode` (null if absent), `regions`, `countries`, `skills`, `company_slug` | Permitted superset in the original. |

Detail adds: `cities`, `seniority`, `category`, `employment_type` (from `enrichment`, each null when unset), and `salary` (a string built from `enrichment.salary_currency`, `salary_min`, `salary_max`, `salary_period`). Salary format: currency, a space, min, an en dash, max, then "/" and the period when one is recorded; a single bound prints just that number; null when both bounds are missing (zero is a legitimate value because the test is "is null", not "is falsy"). The period matters: a yearly figure must never be mistaken for monthly, so keep it and compare pay only within one period.

HTML-to-text for detail descriptions: convert `br` to newline and the close of `p`, `li`, `ul`, `ol`, `div`, `hN` to newline; remove all other tags; decode entities (named, decimal and hex numeric, code-point safe); collapse runs of spaces/tabs; trim spaces around newlines; collapse three or more newlines to two; return null when empty. Example pinned by a test: a list of two items with an `&amp;` becomes two lines "Build APIs" and "Ship & iterate".

## 4. Detail by slug

Accepted inputs: a bare slug (letters, digits, hyphens, starting with a letter or digit) or any URL containing `/jobs/<slug>` (query and fragment ignored). Anything else (spaces, punctuation, empty) is rejected with BAD_ID before any network call. Note the original's URL branch does not check the host; a stricter rebuild should require host `freehire.me` (or the configured base host).

404 means the slug does not exist and is reported as NOT_FOUND. A closed posting is still served (200) with `closed_at` set, which is the active/expired signal (section 5).

## 5. Active versus expired

- Search (both endpoints) lists open jobs only. A slug that disappears from search but resolves in detail with a non-null `closed_at` is closed. The original's detail output does not surface `closed_at`; the rebuild should: `closed_at` non-null means expired, null means open as of `last_seen_at`.
- Detail 404 means unknown/removed (not necessarily closed).
- `reality.class` ("stale" seen live with age 18 days, two reposts) and `fake_freshness` flag are additional freshness signals; undocumented in the original, worth recording in saved fixtures before relying on them.

## 6. Rate limits and failure modes

- Live response headers (2026-10-19): `x-ratelimit-limit: 600`, `x-ratelimit-remaining`, `x-ratelimit-reset` (per window; the unit appeared to be per minute but this was not confirmed). The original ignores these headers; a rebuild should read them and slow down when remaining is low.
- 429 and every 5xx: retry with exponential backoff and jitter: up to 6 retries (7 attempts), first delay 500 ms, doubling, capped at 8 s, plus 0-500 ms jitter. After the last attempt, fail with "freehire API request failed: <status> <text>". A pinned test checks 7 attempts total on a persistent 500, and that one 429 followed by a 200 succeeds on the second attempt.
- 404: not retried; the helper returns "none". Callers decide: detail says NOT_FOUND; search treats it as a configuration error naming the endpoint path (a self-hosted instance older than the agent endpoint answers 404 there) and must NEVER present it as zero results.
- Other 4xx/5xx-less errors: the error message comes from the body's `error` field if the body parses, else "request failed: status text". A 2xx whose body does not parse as JSON is an error ("returned an unparseable response body"), never empty success.
- Connection failure, DNS failure, refused, or timeout: fail FAST with "could not reach the freehire API at <base> (<cause>)", no retry (graceful degradation: an outage must not hang the caller). A pinned test checks exactly one attempt.
- Timeout: 15 s per request via abort signal (a test checks a signal is passed).
- No login wall or bot wall is expected on the API. An outage is the realistic failure; the loop should mark the board "unavailable this run" and carry on.
- Hosted-service caveat: best-effort personal project, no SLA; coverage includes non-tech postings, though facet dictionaries are tech-first.

## 7. Quirks

- Facets are derived per posting and can be incomplete, geography especially: a remote job may have a work mode but no region. Filtering by `regions=eu` silently drops unresolved jobs. `regions=none` selects the unresolved bucket and ORs with real values (`eu,none`). Because of this the loop must not treat facet filtering as complete and should run a widened pass or client-side filter on `location`.
- Agent endpoint hydration is best effort: a hit whose database row vanished keeps the truncated preview, so a description is "full in practice, never guaranteed".
- Descriptions are large; the original measured bodies at roughly 73% of a default search payload and advised pre-filtering on title and company before reading bodies, with a lean mode for discovery.
- The agent endpoint ignores some parameters (the original test asserts `include_description` and `semantic_ratio` are NOT sent because the API lists them as ignored).
- Table output sizes the slug column to the longest slug so it is never truncated (a truncated slug cannot be looked up). Keep full slugs in any human display.
- Facet values are controlled vocabularies; never invent values. The countries facet is case-insensitive in practice.

## 8. Saved real responses a test suite should capture

1. Agent search, markdown description, one or two hits (record the date; includes the `meta` block).
2. Lean search response for the same query (shows truncated preview description).
3. A search with zero hits (`data: []`, `meta.total: 0`).
4. A detail response for an open job with enrichment populated (seniority, category, employment type, salary with a period), and one with an empty enrichment object.
5. A detail response for a CLOSED job (non-null `closed_at`), plus the 404 body `{"error":"not found"}`.
6. A 429 response (headers included) and a 500 response, for the retry tests.
7. The facets response for a role query, to validate the facet-name list and detect drift.
8. A job whose title carries entities (`&amp;`, `&#38;`) and trailing whitespace; a job with no company, no location and null `posted_at`.

## 9. Live check log

- 2026-10-19: robots.txt 200 as described. `jobs/search?q=go&limit=2&posted_within_days=7&regions=eu` returned 200 with envelope and `meta {limit 2, offset 0, total 6738}`. `agent/jobs/search?q=go&limit=1&description_format=markdown` returned 200; description came back as plain Markdown-style text (no tags). `jobs/{slug}` 200; `jobs/does-not-exist-zzz` 404 `{"error":"not found"}`. `jobs/search?limit=1&description_format=weird` returned 200 (the lean endpoint ignores/accepts it). `countries=DE` and `countries=de` returned identical totals (69,923). Facets endpoint 200 with 27 facet families.

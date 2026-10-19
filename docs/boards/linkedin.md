# Board knowledge note: LinkedIn (public "jobs-guest" endpoints)

Audience: the person writing `boards/linkedin` (Python, httpx) without seeing the original.
Sources: original skill at `.agents/skills/linkedin-search` (SKILL.md, url-reference.md, cli/src/*.ts, cli/tests/*), plus a few live requests on 2026-10-19 with User-Agent `job-search-loop-research/0.1`.

## 0. Read this first: robots.txt and terms position (blocks a naive "robots check first" rule)

- LinkedIn's robots.txt (fetched live 2026-10-19, HTTP 200, about 4,900 lines) has, for the catch-all group, a bare `Disallow: /` plus a note that crawling requires written whitelisting (an address at linkedin.com is named for applications). Named search-engine groups carry long allow/deny lists, and those lists explicitly deny `/jobs-guest/`, `/jobs/view/externalApply/`, `/jobs?runSearch*` and `/api/jobPostings/jobs*`.
- The header comment states that automated access without express permission is "strictly prohibited" and points at the user agreement. The original skill says the same in prose: automated access is against LinkedIn's Terms of Service; personal use only; keep volume low; no commercial or bulk use; own responsibility (SKILL.md lines 29-33, README lines 10-12, url-reference line 6).
- Consequence for the rebuild: under RFC 9309 an honest crawler with a custom UA falls into the catch-all group and is told `Disallow: /`. The rule "robots.txt check before scraping HTML" therefore denies every LinkedIn URL. The original never checked robots at all. The owner must decide one of: (a) ship the adapter disabled by default, enabled only by an explicit per-user opt-in that records the personal-use acknowledgement and an explicit "robots disallows this" override logged in the run record; (b) drop the adapter. Do not silently bypass the gate. Whatever is chosen, the adapter must refuse to run when the opt-in is absent, and the personal-use text must be shown at opt-in time.
- The original SKILL.md ships `enabled: true` for this board; the Danish jobbank ships `enabled: false`. Treat LinkedIn as opt-in in the rebuild.

## 1. Endpoints

| Purpose | Method and URL | Auth | Notes |
|---|---|---|---|
| Search | GET `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search` | none | Returns an HTML fragment, not a full page: a flat run of `<li>` cards, no `<html>` wrapper beyond a doctype. Page size is fixed at 10. |
| Detail | GET `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<jobId>` | none | Returns an HTML fragment for one posting (top card, description, criteria list). |
| Canonical job page (not fetched, used as the result URL) | `https://www.linkedin.com/jobs/view/<jobId>` | n/a | Result URLs from search are regional hosts with slugs (for example `de.linkedin.com/jobs/view/<slug>-<id>`), query string removed. |

No API key, no cookies. The original sent: a UA of the form "Mozilla/5.0 (compatible; linkedin-search-cli/1.0)" (cli/src/helpers.ts line 15), `Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8`, `Accept-Language: en-US,en;q=0.9`, `X-Requested-With: XMLHttpRequest`, follow redirects, 15 second timeout. The rebuild must use the honest UA convention instead (tool name plus contact URL); the `X-Requested-With` header is cosmetic and can be dropped. Live check: a plain curl with the research UA and only `Accept-Language` got HTTP 200 on search, so no browser impersonation is needed for search.

## 2. Search query parameters

| Parameter | Meaning | Accepted values | Notes |
|---|---|---|---|
| `keywords` | free-text query | any string | Omit when no query. Original recommended always giving one. |
| `location` | place string | free text such as `Berlin, Germany`, `Mumbai, Maharashtra, India`, `Remote` | Required by the original CLI (error NO_LOCATION when missing). Passed verbatim, no geocoding, no codes. The string `Remote` is accepted as a location. |
| `f_TPR` | posted-within window | the letter `r` followed by a number of SECONDS, for example `r86400` (24h), `r604800` (7d), `r2592000` (30d), `r1800` (30 min) | Days are converted as days times 86400, minutes as minutes times 60. Any positive whole number works, not only 1/7/14/30 (SKILL.md lists 1/7/14/30 but the code accepts any whole number of at least 1). A value of 0, negative, or the sentinel "9999 days" means: do not send the parameter. |
| `f_WT` | workplace type | `1` on-site, `2` remote, `3` hybrid | CLI flag values `remote`, `hybrid`, `onsite` (also `on-site`), case-insensitive. Any other value is silently ignored by the original (no filter sent). The rebuild should reject unknown values. |
| `start` | offset | multiples of 10 | Computed as (page minus 1) times 10. Always sent, including `0`. |

Mapping to the rebuild contract: `query` to `keywords`; `location` to `location`; `recency` (days) to `f_TPR` seconds; `limit` is client-side only (slice after parsing; the board returns up to 10 per request). Workplace type has no slot in the shared contract; keep it as an optional board-specific field or let the loop filter client-side.

Pagination: offset-based, 10 per page. A page past the end returns an empty body (or a fragment with no cards), which parses to zero results with HTTP 200. The original issues exactly one request per `search` call; paging is the caller's job. The original documents no depth ceiling and this research did not probe one (UNVERIFIED: guest search may stop serving results after some depth), so a test should not assume deep paging works.

## 3. Search response shape and how to extract each field

The response is HTML. The original deliberately used regular expressions, not a DOM parser (it noted that one popular Node parser mis-nests these cards). In Python, selectolax or lxml will do; either way, split per card and parse each card independently so one malformed card cannot hide the others.

Per card (verified against live output on 2026-10-19; 10 cards in one response):

| Contract field | Where it lives | Fallbacks and cleaning |
|---|---|---|
| id | The card container element carries a `data-entity-urn` attribute of the form `urn:li:jobPosting:<digits>`. Split the whole response on that prefix and take the leading digits of each chunk. | A chunk with no leading digits is skipped. |
| title | `h3` with class `base-search-card__title` | If missing or empty, use the visually-hidden span with class `sr-only` inside the full-link anchor. If both are empty the card is DROPPED (the only reason a card is dropped). Strip tags, collapse whitespace, decode entities. |
| company | `h4` with class `base-search-card__subtitle`; its text (usually inside a nested anchor) | Null when absent or empty. |
| company URL (extra, not in the shared contract) | `href` of the anchor inside that `h4` | Query string stripped. Null when absent. |
| location | `span` with class `job-search-card__location` | Null when absent or empty. Typically a city/region string or just a country (live example: "Italy"). |
| date | `time` element whose class starts with `job-search-card__listdate` (live variants: `job-search-card__listdate` and `job-search-card__listdate--new`); value from its `datetime` attribute | The attribute is already `YYYY-MM-DD`. The visible text ("3 days ago") is not used. Null when absent. |
| url | anchor with class starting `base-card__full-link`; its `href` | Decode `&amp;`, then cut at the first `?` to drop tracking parameters. If no link exists, synthesise `https://www.linkedin.com/jobs/view/<id>`. |
| description | not present in search results | Always null from search. |
| deadline | not present | Always null. LinkedIn guest pages carry no application deadline. |

Other attributes on cards (impression ids, tracking ids, row/column numbers, logo image URLs, "benefits" blocks) are noise; ignore them. Do not store tracking identifiers.

Empty result: HTTP 200 with an empty or whitespace-only body (or no `urn:li:jobPosting:` substring). Output is an empty `results` list, exit 0. The original test suite proves this by serving an empty body and expecting success.

Entity decoding: the original decodes `&amp; &lt; &gt; &quot; &#39; &apos;`, decimal and hex numeric references (both `x` and `X`), and `&nbsp;`, in that order; numeric references use full code points (so emoji above U+FFFF work) and out-of-range values are dropped rather than raising. Note the original decodes `&amp;` first, which double-decodes sequences like `&amp;lt;`; in Python use `html.unescape` once on the stripped text instead. Test cases to keep: `Caf&#xE9;`, `Deb&#XFC;t`, `Caf&#233;`, `&#128512;`, `&#x1F600;`, `N&#xF8;rrebro`.

## 4. Detail page

URL: `.../jobPosting/<jobId>` with a numeric id.

Accepted detail inputs (all must normalise to the digits, anything else is rejected BEFORE any network call with code BAD_ID):
- a bare number of at least 6 digits;
- a URN containing `urn:li:jobPosting:<digits>`;
- an http(s) URL, or a scheme-less string that starts with a host, whose parsed hostname is `linkedin.com` or any subdomain of it, with a path of `/jobs/view/` followed by either the digits or a slug ending in `-<digits>`, optional trailing slash; tracking query ignored;
- a bare slug such as `software-engineer-1234567890`.
Rejected: any other host (Greenhouse, Lever, example.com), look-alike hosts (`linkedin.com.evil.io`, `notlinkedin.com`), userinfo tricks (`www.linkedin.com@evil.io`), LinkedIn URLs that are not job views (`/in/...`, `/feed/`, `/jobs/search/?currentJobId=...`), fewer than 6 digits, empty string. Use real URL parsing for host and path, never a regex over the whole string. Rationale recorded in the original: an apply link on an ATS host used to be reduced to its 7-digit path segment and fetched as an unrelated LinkedIn posting.

Fields:

| Field | Where | Notes |
|---|---|---|
| title | `h1` or `h2` with a class starting `top-card-layout__title` or `topcard__title` | Fallback text "(untitled)" if absent. In the saved real fixture the title is an `h2` carrying both class names. |
| company and company URL | anchor with class starting `topcard__org-name-link` | Name from anchor text, URL from `href` with query dropped. |
| location | `span` with classes `topcard__flavor topcard__flavor--bullet` | The first such span is the location. |
| description | `div` whose class contains `show-more-less-html__markup` (preferred) or `description__text` | Needs a depth-aware div extractor because descriptions contain nested divs; a regex that stops at the first closing div truncates. Convert `br` to newline, close of `p`, `li`, `ul`, `ol`, `div`, `hN` to newline, strip other tags, clean each line separately, collapse runs of three or more newlines to two, trim, null if empty. Tests pin that paragraph and list boundaries survive (a list item and the next item are on separate lines; no `<p>` remains). Empty `<p><br></p>` paragraphs are common and collapse. |
| criteria | `li` items with a subheader `h3` (class `description__job-criteria-subheader`) and a value `span` (class starting `description__job-criteria-text`) | Label lower-cased becomes the key: "seniority level", "employment type", "job function", "industries". Missing key gives null. Example values from the real fixture: Mid-Senior level; Contract; Information Technology; "Technology, Information and Media". |
| date | not available on this endpoint | Original sets null. A rebuild that wants the posting date must carry it forward from the search card (join on id) or read a relative "posted ... ago" text if present; the original did not. |
| url | synthesised `https://www.linkedin.com/jobs/view/<id>` | |
| apply URL | deliberately NOT emitted | The original dropped the field because the real markup never matched and a corrected version would duplicate the job-view URL. A test pins its absence. |
| isActive | see section 5 | |

## 5. Active versus expired

- A closed posting renders, inside the top card, a `figure` with classes `closed-job closed-job__flavor topcard__flavor-row` containing a figcaption with the text "No longer accepting applications". These two markers (class and visible text) are the only closed markers observed on real pages (verified by the original author on 2026-08-09; saved fixture for posting 4345841651 shows exactly this).
- Detection searches only the part of the page BEFORE the description block begins (the first occurrence of the description markup class). Recruiter boilerplate in a live posting's description may quote the phrase, or mention the CSS class name, and must not mark it closed. Tests pin both false-positive cases.
- Semantics: `isActive = true` means only "no closed banner found". It is not proof of being open: markup drift, a consent wall or an interstitial also render no banner. The adapter should expose a tri-state (closed / no-banner / unknown) and treat an empty or challenge-page body as unknown, not active.
- A posting removed entirely returns HTTP 404 from the detail endpoint (see failure modes), reported as NOT_FOUND.
- Search results never indicate expiry.

## 6. Rate limits and failure modes (observed and documented)

| Situation | What it looks like | Original behaviour | Rebuild guidance |
|---|---|---|---|
| Normal | HTTP 200, HTML fragment | parse | |
| Throttled / challenged | Live 2026-10-19: the FIRST detail request, right after a successful search, got HTTP 429 with a roughly 24 KB body that is a small HTML document with a `meta refresh` redirect to the LinkedIn home page and an obfuscated JavaScript proof-of-work challenge. | Retry on 429 and every 5xx: up to 6 retries (7 attempts), delay starts 500 ms, doubles, capped at 8 s, plus random jitter 0-500 ms; after the last attempt raise "Request failed: 429 ...". Total patience is about 25 s. | Same schedule is fine, but honour a `Retry-After` header if present, and do not retry more than that. Do NOT attempt to solve or evade the challenge. After the retry budget is spent, report the board as blocked/unavailable for the run and let the loop move on. A per-run circuit breaker (stop calling the board after N consecutive 429s) is stricter than the original. |
| Not found | HTTP 404 | helper returns empty string; detail reports NOT_FOUND exit 1; search treats it as zero results | Keep; but distinguish "search returned nothing" from "endpoint 404". |
| Other 4xx (403, 400 etc.) | | thrown as "Request failed: <status> <text>" immediately, no retry | Keep. |
| Login wall / auth wall | Not observed on these endpoints. A consent or login interstitial would return 200 with HTML lacking the expected markers. | Not detected; would yield empty results or a detail with title "(untitled)" and no description. | Detect: detail with no title and no description, or search body non-empty but with zero `urn:li:jobPosting:` hits and containing sign-in markers, should be an error "unexpected page shape", not an empty success. |
| Timeout | stalled socket | 15 second per-request timeout via abort signal (a unit test pins that a timeout signal is passed) | Keep 15 s connect+read budget. |
| Network error | DNS or connect failure | exception becomes SEARCH_FAILED / DETAIL_FAILED | Retry-less is acceptable, or one retry. |

Politeness: the original added no delay between calls; the skill said keep volume low. The rebuild should add a fixed minimum gap (at least 1 s) between any two requests to this host and cap requests per run.

## 7. Quirks worth knowing

- Cards come back newest-relevance mixed; there is no sort parameter in the original.
- Search results link to regional subdomains (`de.`, `it.`, `dk.`); detail accepts any `*.linkedin.com`.
- The regional host in the result URL does not matter for the API; always call the `www` host.
- `date` on search cards is the date part only; there is no time-of-day.
- Two class variants for the date element (`--new` suffix for very recent postings) must both match.
- A location string that LinkedIn cannot resolve yields results for a nearby or default region rather than an error. Do not assume location matching is strict; the loop should post-filter on the returned `location` text.
- The `f_TPR` sub-day windows (minutes) are supported by the board; the original CLI exposed them as a separate flag that conflicts with the days flag. In the rebuild contract, recency should be one value; convert to seconds and use a single parameter.
- Fractional or zero values for age/page/limit used to be truncated to zero and the filter silently dropped from the request; the original now rejects them (see parity rows). The Python port should validate before building the request.

## 8. What a test suite should capture (saved real responses, no network in tests)

1. A real search response with at least two full cards (the original kept two verbatim cards; one regional host, a logo image, a company link, a location, a `--new` date variant ideally). Record capture date and a content hash. Add a third synthetic variant: a card with no location and no date.
2. A real detail response excerpt for a CLOSED posting (title heading, the closed-job figure, the description div with adjacent paragraphs, empty paragraphs and list items, and the four-item criteria list). The original fixture is posting 4345841651 captured 2026-07-14.
3. A real detail response for an OPEN posting (not in the original; capture one) to pin the absence of a banner.
4. An empty search body (HTTP 200, zero bytes) and a 404 body.
5. The 429 challenge page captured on 2026-10-19 (HTML with meta refresh and script), to pin "challenge page is an error, never parsed as results".
6. A response with a recruiter-boilerplate description that quotes the closed phrase, and one that names the closed CSS class, both on live postings.
7. Contract test with the shared fields: id, title, company, location, date, deadline (null), url, description (null from search, text from detail), all present, null when missing.

## 9. Live check log

- 2026-10-19: robots.txt GET returned 200 (catch-all `Disallow: /`; `/jobs-guest/` denied in named groups).
- 2026-10-19: search GET (keywords python, location "Berlin, Germany", f_TPR r604800, start 0) returned 200, about 29 KB, 10 cards; date elements carried `datetime="2026-10-14"` and `"2026-10-18"` with the two listdate class variants; result links were on `de.linkedin.com` with `position`, `pageNum`, `refId`, `trackingId` tracking parameters.
- 2026-10-19: detail GET for a job id from that search returned 429 with the challenge body described above. Not retried (to stay polite and not to evade).

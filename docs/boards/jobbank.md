# Board knowledge note: Akademikernes Jobbank (jobbank.dk, Denmark)

Audience: the person writing `boards/jobbank` (Python, httpx) without seeing the original.
Sources: `.agents/skills/jobbank-search` (SKILL.md, url-reference.md, cli/README.md, cli/src/*.ts, cli/tests/*) and live requests on 2026-10-19 with User-Agent `job-search-loop-research/0.1`.

## 0. Availability, robots, terms (read first)

- LIVE RESULT, 2026-10-19: both `https://jobbank.dk/robots.txt` and `https://jobbank.dk/job/rss?key=python&amt=2` answered HTTP 403 with a Cloudflare interaction page (title "Just a moment...", an interstitial that needs a browser to solve a challenge, about 5.5 KB). So from this environment the board was not reachable at all, including robots.txt. I did not try to get around it (no browser headers, no retries, no solving). Everything below about the response bodies therefore comes from the original's code, docs and tests, not from a fresh capture.
- The original treats this as a normal, documented condition: SKILL.md says that if Cloudflare blocks, report the portal as unavailable and fall back to web search instead of retrying (lines 28 and 176); the RSS fetch turns a 403 whose body matches "just a moment", "cloudflare" or "cf-chl" (case-insensitive) into an error whose text says Jobbank is blocking automated requests and to skip this portal. A unit test pins exactly that for a 403 with a "Just a moment..." body.
- robots.txt position: unknown (could not be fetched). Under RFC 9309 a 4xx on robots.txt normally means "no rules, access allowed", but a Cloudflare challenge is an active block, so the rebuild should classify "robots.txt answered with a challenge page" as BLOCKED/unavailable, not as allowed. The original never fetched robots.txt for this board. The original shipped it disabled (`enabled: false`, a Danish demo portal that the setup step turns on when the market is Denmark).
- Terms: the original states no ToS prohibition for jobbank and no personal-use banner (unlike LinkedIn). The rebuild should still show a short notice and keep volume low (the original README: at most 2 requests per search; a 300-500 ms pause between sequential calls).
- The README says a browser-style User-Agent is sent and even quotes a Chrome string, but the code actually sends `Mozilla/5.0 (compatible; jobbank-cli/1.0)` (helpers line 5), and a unit test compares the request header to that constant. The README is wrong; the code is honest. The rebuild must use an honest tool-naming UA and must not impersonate a browser.

## 1. Endpoints

| Purpose | URL | Format | Notes |
|---|---|---|---|
| Search (used) | `https://jobbank.dk/job/rss?<params>` | RSS 2.0 XML | Up to 100 items per request. Same filters as the HTML search. The `page` parameter has NO effect on RSS; there is no pagination. |
| Total count (secondary request) | `https://jobbank.dk/job/?<same params>` | HTML | Only the `<title>` is read: a pattern of the form "<N> relevante job ...". Thousands separators `.` or `,` are removed. If anything fails the total is null; this request must never fail the search. The original waits 300 ms before it. |
| Detail | `https://jobbank.dk/job/<id>/` | HTML with JSON-LD | The short URL redirects to the slug form `/job/<id>/<company-slug>/<title-slug>/` and returns 200. 404 means unknown. |
| HTML results page (documented, not used by the CLI) | `https://jobbank.dk/job/?<params>&page=N` | HTML | About 20 results per page; selectors in section 3b. Pagination only exists here. |

## 2. Search parameters (RSS and HTML take the same ones)

Multiple values: repeat the parameter (`cvtype=3&cvtype=13`), which means OR within a facet. The CLI also accepts comma lists in one flag value and merges them with repeats (`--type=3,6` plus `--type=13` gives three values in order). Filter ids are case-sensitive; invalid combinations may just return zero results.

| Parameter | Flag in original | Meaning and values |
|---|---|---|
| `key` | --key | keywords (title, company, keyword) |
| `antikey` | --exclude | excluded keywords |
| `cvtype` | --type | job type: 3 Fuldtidsjob (full-time), 6 Graduate/trainee, 13 Deltidsjob (part-time), 8 Vikariat (temporary), 12 Ph.d. and Postdoc, 11 Freelance, 15 Iværksætterprojekt, 14 Event, 9 Praktikplads (internship), 4 Studiejob, 5 Studieprojekt/speciale |
| `udd` | --education | education area: 20 Administration, 43 Construction, 29 Architecture/Art/Design, 47 Electronics/Telecom, 32 Food/Veterinary, 38 HR, 28 Humanities, 24 IT, 23 Law, 44 Chemistry/Biotech/Materials, 45 Climate/Environment/Energy, 37 Agriculture/Nature, 22 Marketing/Business, 48 Mechanical/Design, 46 Math/Physics/Nano, 31 Medicine/Health, 30 Natural sciences, 41 Organisation/Management, 35 Production/Logistics/Transport, 34 Social sciences, 25 Language/Media/Communication, 33 Engineering/Technology, 26 Education/Pedagogy, 21 Economics/Accounting |
| `amt` | --location | REGION code (not a city name): 2 Storkøbenhavn, 3 Nordsjælland, 14 Østsjælland, 4 Vestsjælland, 5 Sydsjælland and islands, 13 Fyn, 12 Sønderjylland, 11 Sydvestjylland, 9 Vestjylland, 10 Sydøstjylland, 7 Midtjylland, 8 Østjylland (Aarhus), 6 Nordjylland, 20 Bornholm, 21 Øresundsregionen, 22 Greenland and Faroe Islands, 23 Abroad-Sweden, 24 Abroad-Norway, 19 Abroad-other |
| `erf` | --work-area | function: 20 Administration, 38 Architecture/Design, 22 Bank/Insurance, 43 Data/Analysis, 47 Export, 41 Research/Development, 28 HR, 49 Procurement, 34 Internet/Multimedia, 32 IT-Hardware, 33 IT-Network/Telecom, 31 IT-Software, 24 Law, 35 Communication/Media/SoMe, 46 Construction/Calculation, 37 Art/Culture, 26 Management/Planning, 29 Marketing/Advertising, 40 Medicine/Health, 45 Natural sciences, 23 Organisation/Association, 52 Politics/Society, 44 Production, 27 Project management, 50 Consulting/Support, 30 Sales, 39 Social services, 42 Engineering, 25 Executive management, 48 Transport/Logistics, 36 Education, 21 Finance/Administration |
| `branche` | --industry | sector: 10359 Law/Accounting, 11669 Construction, 11634 Electronics/Machinery, 16791 Unions/Funds, 10358 Finance/Insurance/Pension, 10442 Research/Education, 15407 Food/Groceries, 10364 Trade/Service, 10331 IT/Tele, 17209 Climate/Energy/Utilities, 16826 Municipalities, 10341 Culture/Media/Entertainment, 10333 Pharma/Biotech/Chemistry, 10363 Paper/Furniture/Materials, 11626 Regions/Health/Social, 15586 Consulting, 10362 Government/Politics, 10440 Transport, 12450 Temp/Recruitment |
| `andet` | --suitable-for | 2 Nyuddannede (new graduates), 4 international background, 5 Erfarne (experienced) |
| `virk` | --company | company. CONTRADICTION in the original: the URL reference says "company name, URL-encoded string" (example `virk=novo+nordisk`), while the CLI declares it a positive whole number "Company ID" and rejects anything else. The adapter should accept the numeric id (what the CLI proved) and treat name search as unverified. |
| `fjernarbejde` | --remote | exactly `helt` (fully remote) or `delvist` (partly). The CLI does not validate the value. Validate in the rebuild. |
| `oprettet` | --since | posted on or after, `YYYY-MM-DD`. The CLI does not validate the format. Validate in the rebuild. |
| `page` | none | HTML only |

Mapping to the shared contract: `query` to `key`; `location` to `amt` region codes (the loop needs a city-to-region lookup, for example Copenhagen to 2, Aarhus to 8, Odense to 13) and the loop post-filters on the returned location text; `recency` to `oprettet` computed as today's date minus N days (the board takes a date, not an age); `limit` is client-side only (RSS returns up to 100). At least one filter or keyword is required by the original (error MISSING_REQUIRED, "--key or at least one filter is required"), so an empty search is refused rather than dumping the full database.

## 3a. RSS response and how each field is found

Format: RSS 2.0, each posting an `item` with `title`, `description`, `link`, `pubDate`. Values are usually CDATA wrapped but plain text also occurs; the original tries CDATA first then plain. Items are split on `item` boundaries and each is parsed on its own. `link` can clash with an Atom link element, so only the text node inside `link` is read. In Python use an XML parser with a tolerant fallback (items with unescaped ampersands appear in the wild).

| Contract field | Source | Rule |
|---|---|---|
| id | `link` | The first numeric path segment after `/job/` (`/job/12345/acme/data-scientist` gives 12345). A non-numeric segment yields empty string; do not invent an id; skip or flag such items. |
| title | `title` | trimmed |
| company, location, job type, deadline | `description` | The description is a one-line summary in Danish: `<job types> hos <Company>, <Location> (Ansøgningsfrist: <deadline>)`. Parse: split at the FIRST " hos "; left side is job type(s) (may be several, comma separated, for example "Fuldtidsjob, Graduate/trainee"); on the right, take a trailing parenthetical "Ansøgningsfrist: ..." (the "ø" may also appear as "o"); the remainder splits at the FIRST ", " into company and location (no comma means all company, location empty). Companies with commas in their name would mis-split; accept that. If " hos " is absent, the entire description becomes the company and the other fields stay empty/null. |
| deadline | the parenthetical | `DD.MM.YYYY` becomes `YYYY-MM-DD`. The word "løbende" (rolling; also "lobende") becomes null. Any other text passes through UNCHANGED (for example "snarest muligt") so a downstream parser can see it; the loop must treat a non-date deadline defensively (not as a real date). |
| date and posted | `pubDate` | RFC 2822 date; convert to a full ISO timestamp in UTC (an input of `Fri, 14 Aug 2026 09:30:00 +0200` becomes `2026-08-14T07:30:00.000Z`), `date` is the first ten characters of that UTC string. Note the UTC conversion can shift the calendar day relative to Danish local time. Unparseable or missing pubDate: posted empty, date null, and the item must STILL be returned (one bad item must never fail the whole search; the original had that bug once). Test inputs: "date unavailable", a valid date with a stray trailing character, "I går". |
| url | `link` | as given |
| description | the RSS `description` text | This is only a summary line, not the job text. The shared contract's `description` for search is therefore this summary; the full text needs `detail`. |

Extra fields the original emitted: `jobType`, `posted`. Search meta in the original is `{ total }` only (no count/page), which differs from the shared contract; the rebuild adds count and page.

## 3b. HTML results page (documented but unused; fallback only)

Selectors from the reference file: container `div.job-item`; id from the container's `name` attribute; title `.job-header`; type/company/location text `.job-teaser`; excerpt `.job-description`; updated date `.job-date-updated`; deadline `.job-date-application`; detail link anchors whose href starts `/job/` (prefix with the host). About 20 results per page. These selectors were last written down by the original author and not re-verified; capture a fresh page before relying on them.

## 4. Detail page

Fetch `https://jobbank.dk/job/<id>/`. Accepted inputs: a bare digit string, or a URL whose path contains `/job/<digits>` followed by a slash, end, query or fragment. Anything else (letters, other hosts, empty) is rejected as BAD_ID before any request. NOTE the original does not check the host of a URL input (an unrelated site with `/job/123/` passes), whereas a test only pins that `https://example.com/other/12345` is rejected because the path lacks `/job/`. The rebuild should require host `jobbank.dk`.

Extraction: collect all `script` elements of type `application/ld+json`; parse each, ignoring invalid JSON and continuing to the next; accept a root that is an object, an array, or an object with `@graph` array, and search recursively for the first node whose `@type` equals `JobPosting`. If none is found: error PARSE_ERROR "No JSON-LD found on job page" (this is also what an invalid id redirected to a generic page looks like).

Field map (JSON-LD to output): `identifier.value` to id (fall back to the requested id); `url`; `title`; `description` (HTML string, keep; the plain format strips tags and collapses whitespace); `datePosted` as given (README says `YYYY-MM-DD`); `validThrough` to deadline = first 10 characters, with the sentinel `0001-01-01` mapped to null, absent or empty mapped to null; `employmentType` string or list normalised to a list of strings (Schema.org values such as FULL_TIME); `hiringOrganization.name`, `hiringOrganization.logo` (null if absent); `jobLocation.address.streetAddress`, `addressLocality` (city), `postalCode`, `addressCountry` (often "DK"). Location parts are empty strings, not null, when absent; international postings may have none. A single `jobLocation` object is assumed; the original does not handle an array of locations (flag as a risk; test with a captured multi-location posting if one exists).

Shape mismatch with the shared contract: the original detail output nests `company` and `location` as objects. The rebuild's adapter maps to flat `company` (name), `location` (city, country joined), `date` (datePosted), `deadline`, `url`, `description`.

## 5. Active versus expired

- The original has NO explicit expiry detection for jobbank. Signals available: (a) `validThrough` in JSON-LD is the application deadline; a deadline in the past on today's date means closed for applications; null (or the "løbende" rolling value in the feed) means open-ended; (b) HTTP 404 on the detail URL means removed; (c) a page with no JobPosting JSON-LD is unusable. Closed postings may still be served with a past `validThrough`.
- The rebuild should compute `expired = deadline < today` itself, and keep "unknown" when the deadline is non-date text.

## 6. Rate limits and failure modes

| Situation | Look | Original behaviour |
|---|---|---|
| Cloudflare challenge | HTTP 403, HTML page titled "Just a moment..." (confirmed live, both robots.txt and RSS) | RSS: error text about bot protection, code API_ERROR, exit 1, instruction to skip the board and fall back to web search. No retry. The generic fetch helper deliberately RETURNS non-retry statuses (such as 403) to the caller instead of raising, and a test pins that it makes just one call for a 403. |
| 429 or 5xx | | retry up to 6 times (7 attempts): delay 500 ms, doubling, capped at 5 s (not 8 s as in the other boards), plus 0-500 ms jitter; then "Request failed: <status> <text>". |
| Timeout | | 15 s abort signal per request (test checks the signal exists) |
| Detail 404 | | NOT_FOUND "Job not found" |
| Detail other non-2xx | | API_ERROR "Failed to fetch job page: <status> <text>" |
| Search non-2xx other than the Cloudflare 403 | | API_ERROR "Failed to fetch RSS feed: <status> <text>" |
| Empty RSS (valid XML, zero items) | | exit 0 with an empty results list; total from the HTML title (live fixture shows a zero total from a title like "0 relevante job") |
| Count request fails | | total is null and the search still succeeds |

Politeness: no explicit published limits; Cloudflare is present. Keep at most two requests per search (RSS plus count) with a 300-500 ms pause, and a minimum gap between searches.

## 7. Quirks and contradictions to resolve

- Error format inconsistency: schema-validation failures (bad `--limit`, bad `--company`) come from the framework (bunli) as `{ "ok": false, "error": { "kind": "validation", "option": ..., "message": ... } }`, NOT the standard `{error, code}` shape, while the board code's own errors use `{error, code}`. The rebuild should use one shape everywhere.
- The CLI treats a value beginning with a dash as a missing value (negative numbers give UNKNOWN_FLAG); `--flag=value` is the supported way to pass such a value.
- Every declared option takes a value: a bare `--remote`, `--type`, `--company`, or `--since` followed by another flag or end of input is an error BAD_ARG "option ... requires a value" raised BEFORE any request (the framework would otherwise drop the option and run a broader search).
- A comma inside a repeatable value is split into several values; `--type=3,6` equals two repeats.
- README type column says "number" for repeatable flags, but the values are carried as strings and validated only by the portal.
- SKILL.md says `meta.total` is the true count; it comes from the HTML title regex and is null when that fails. RSS returns at most 100 items regardless.
- Danish text in descriptions ("hos", "Ansøgningsfrist", "løbende") means the parser is language-specific; an English-locale feed would break it. Add a test that an unparseable description does not raise.
- Location strings are Danish place names, not region labels; `amt` filter uses region ids.

## 8. Saved real responses a test suite should capture (none could be captured today)

Because the board is behind a challenge from the research environment, capture these from a normal browser session or a permitted network and store with date and hash:
1. An RSS response with several items: CDATA and plain variants, multiple job types, a rolling deadline, a dated deadline, a free-text deadline if one exists, and one item with an odd pubDate if found.
2. An RSS response with zero items.
3. The HTML results page `<title>` for a query with thousands (a number with a thousands separator) and one with zero results.
4. A detail page with JSON-LD: single object form, an `@graph` form if it occurs, and a posting with no `validThrough`, one with the `0001-01-01` sentinel, and one with a foreign (non-DK) location or no location.
5. A removed/unknown id (404) and an id that redirects to a generic page without JSON-LD.
6. The Cloudflare challenge response saved today's way (403, "Just a moment..." body) for the block-detection test.
7. A page with a malformed JSON-LD script followed by a valid one.

## 9. Live check log

- 2026-10-19: GET robots.txt gave 403 (Cloudflare challenge). GET RSS with `key=python&amt=2` gave 403 (same challenge). No further attempts; no evasion.

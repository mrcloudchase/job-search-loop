# Board knowledge note: Jobdanmark (jobdanmark.dk)

Audience: the person writing `boards/jobdanmark.py` (httpx) without the original TypeScript. Danish board, off by default.
Sources: original `.agents/skills/jobdanmark-search/` (SKILL.md, cli/README.md, cli/src/**, cli/tests/**) plus a few polite live requests on 2026-10-19 with User-Agent `job-search-loop-research/0.1`. Live observations are marked **[live 2026-10-19]**.

## 1. Position on robots.txt and terms

* `https://jobdanmark.dk/robots.txt` (200) **[live]**: `User-agent: *`, `Disallow: /bin/`, `Disallow: /umbraco/`, `Sitemap: https://jobdanmark.dk/sitemap`. So `/job/<slug>` pages are allowed for HTML scraping. `/api/...` paths are **not** disallowed, so the JSON endpoints below are permitted by robots.txt, but they are the site's own undocumented XHR API (no published terms for it were read). Run the robots check anyway before any HTML fetch (the detail page) and for the API paths.
* The sitemap (`/sitemap`, ~4.3 MB XML **[live]**, lists `/job/...` URLs with `lastmod`) is a polite discovery and freshness source; do not download it in a loop (one fetch per day at most).
* Honest User-Agent required. The original used `Mozilla/5.0 (compatible; jobdanmark-cli/1.0)`.

## 2. Endpoints

| Purpose | Method and URL | Notes |
|---|---|---|
| Search | `POST https://jobdanmark.dk/api/jobsearch/search/{page}` | JSON body, `Content-Type: application/json`; page is 1-indexed in the path; 30 results per page, server-enforced |
| Categories with live counts | `GET /api/categorycount/getcounts` | JSON array |
| Autocomplete (job titles + categories) | `GET /api/search/autocomplete?q=<text>` | JSON array of groups |
| Location suggestions | `GET /api/search/locations?q=<text>` | JSON array of groups |
| Posting (HTML) | `GET https://jobdanmark.dk/job/{slug}` | the only detail source; no JSON detail API |
| Company page | `/virksomheder/{slug}` | linked from postings |
| Sitemap | `/sitemap` | see section 1 |

Images in the raw API (`companyLogo.url`, `coverImage.url`) are relative paths; prefix the site base if you ever need them. The original drops them from search output.

## 3. Search request body

```
{ "jobTypes": [..strings..], "filters": [ {type, value, displayText}, ... ], "locationMode": "Text", "distance": 50 }
```

* `jobTypes`: the comma-separated user value split on commas, trimmed, empties dropped. Accepted values: `fuldtid`, `deltid`, `fleksjob`, `elev`, `studiejob`, `praktik` (the original does not validate; an unknown value was not probed). Several types combine with OR as far as the original's examples suggest (`fuldtid,deltid`).
* `filters`: order as built by the original: freetext, category, jobtitle, municipality, zip, region. Each has `type`, `value`, `displayText` (**`displayText` is mandatory**, the server rejects a filter without it; for IDs it is just the number as a string).

| Filter `type` | `value` | Source of valid values |
|---|---|---|
| `freetext` | string | any |
| `category` | **integer** | `/api/categorycount/getcounts` or autocomplete group "Kategorier" |
| `jobtitle` | **integer** | autocomplete group "Jobtitler" (`value` field) |
| `municipality` | string, the municipality display name (e.g. `Odense`, `København`) | `/api/search/locations` group "Kommune" |
| `zip` | string of the 4-digit postal code | locations group "Postnummer" |
| `region` | string region name (e.g. `Midtjylland`, `Sjælland`) | locations may return a region group; exact region vocabulary was not probed |

* `locationMode` is the constant `"Text"` and `distance` the constant 50 (no radius search is exposed).
* Filters combine (AND). With **no** filters and no job types the server returns the whole database (13,862 results at the original's capture date). That is why the original rejects unknown flags: a misnamed filter silently became "no filter". In the rebuild, never send an empty filter set by accident; assert at least one filter when the caller asked for any.
* There is no sort parameter and no recency filter: results come in the site's own order (boosted jobs, `boostJob: true`, may be placed first). Recency must be filtered client-side from `publishedDate`.

Category IDs (stable ids, verified live in titles): 227972 Pædagogik, Uddannelse og Forskning; 227973 Håndværk, Industri, Transport og Landbrug; 227974 Salg, Kommunikation, Marketing, og Design; 227975 Pleje, Social og Sundhed; 227976 Hotel, Service, Restauration og Sikkerhed; 227977 Kontor, Finans og Økonomi; 227978 IT, Ingeniør og Energi; 227979 Ledelse, HR og projektstyring; 543415 Kirke, Kultur og Underholdning; 227980 Øvrige job. **[live]** counts: 227972 had 1,983, 227973 had 2,847, 227975 had 2,834 (the README's example counts are stale, so do not hard-code counts).

## 4. Search response

```
{ items: [...], currentPage, totalItems, itemsPrPage (30), totalPages }
```
**[live]** `freetext python` + `jobTypes fuldtid` gave totalItems 74, totalPages 3, 30 items on page 1.

Item keys: `title`, `companyName`, `companyAddress`, `jobTypes` (list of strings), `boostJob`, `publishedDate` (`DD-MM-YYYY`), `applicationDeadline` (`DD-MM-YYYY` or null; sometimes only date), `url` (relative `/job/<slug>`), plus presentation-only `companyLogo`, `companyLogoSvgMarkup`, `overlayColor`, `coverImage`, `silhouetteLogo` (about 40% of the payload; drop them).

Contract mapping:

| Contract field | Rule |
|---|---|
| id / slug | `url` minus the leading `/job/` prefix (the original strips only that prefix, so a full-URL `url` would keep its host; handle both) |
| url | prefix `https://jobdanmark.dk` when the value does not start with `http` |
| title | `title` |
| company | `companyName` |
| location | city parsed from `companyAddress` (this is the employer's address, not necessarily the workplace): first try a 4-digit postcode followed by whitespace and the rest ("Lautruphøj 2, 2750 Ballerup" gives "Ballerup"); else a 4-digit code followed by a comma and a non-digit start ("2670, Greve" gives "Greve"); trim; null when no postcode or when the address is null. A 4-digit *street number* must never be mistaken for the postcode ("Vejlevej 1234, 7100 Vejle" gives "Vejle"). |
| date | `publishedDate` converted from `DD-MM-YYYY` to `YYYY-MM-DD`; an already different shape is passed through unchanged (the rebuild should instead validate and null it) |
| deadline | same conversion of `applicationDeadline`; null when null |
| description | not in search (no teaser field) |

The original also kept the native fields `companyName`, `companyAddress`, `jobTypes`, `boostJob`, `publishedDate`, `applicationDeadline` unchanged next to the contract aliases.

## 5. Lookup endpoints

* **Autocomplete** `GET /api/search/autocomplete?q=<text>` returns `[ {title: "Jobtitler", items:[...]}, {title: "Kategorier", items:[...]} ]` with items `{id: "title__454" | "category__227978", text, value (integer), category: "jobtitle" | "category", slug}`. **[live]** The server does **not** filter by the query: a nonsense query returned `Jobtitler` with an empty `items` and the full list of ten categories under `Kategorier`. The original filters client-side: keep only items whose `text` (when it is a string) contains the query case-insensitively, then drop groups left empty; an item with null/missing text is skipped, a group with missing `items` is tolerated. `--limit` is applied across groups in order (fill the first group, then the next) and empty groups are not emitted.
* **Locations** `GET /api/search/locations?q=<text>` returns groups "Kommune" (item `value` is the municipality name, `id` like `municipality__0461` where 0461 is the official municipality code) and "Postnummer" (`value` is the 4-digit string, `text` like "5000 Odense C"), probably also a region group. **[live]** `q=Odense` gave one municipality and many postal districts. Server-side filtering is real here (no client filter in the original); groups with no items are dropped; limit applies across groups like autocomplete.
* **Categories** `GET /api/categorycount/getcounts` returns `[ {id (int), title, helpText, count} ]`; `--limit` slices the array.

## 6. Detail page (HTML)

`GET https://jobdanmark.dk/job/{slug}`. The slug is the path segment; a bare slug is `[A-Za-z0-9_-]+`; a full URL or a relative `/job/<slug>` path is also accepted and the slug is the segment after `/job/` up to `/`, `?` or `#`. Anything else is a bad-id error. (The original checks the `/job/` path but not the host; the rebuild should require host `jobdanmark.dk`.)

Parsing order:

1. **JSON-LD first**: every `<script type="application/ld+json">`; a malformed script is skipped silently; accept a script whose root object has `@type: JobPosting`, or an array containing one. If found, map: title, `datePosted`, `validThrough` (null if absent), `employmentType` (string becomes a one-element list; list kept; absent gives []), `hiringOrganization.name` (empty string when absent) and `.logo` (null), `jobLocation.address.{streetAddress, addressLocality, addressRegion, postalCode, addressCountry}` (each null if absent), and `description`. JSON-LD descriptions are HTML: render to plain text, one line per block element (paragraphs, list items, `<br>`), entities decoded, whitespace collapsed, empty lines removed; plain text stays unchanged and an absent description is the empty string. `applyUrl` is null on this branch.
2. **Rendered HTML fallback** when no JobPosting JSON-LD exists. **[live]** a current posting (`postdoc-position-...`, published 15-10-2026) had **no JSON-LD at all** and used this layout, so this is now the primary path. Selectors:
   * title: `.job-list-details .title`, else first `h1`, else first `h3` with a trailing ` | jobdanmark` removed; if empty: parse error.
   * company name: the link `a[href^="/virksomheder/"]` inside `.job-details-head`; logo from `.company-logo img[src]` or `.company-logo source[srcset]` (first token), made absolute.
   * overview list `ul.job-overview li`, each `<strong>Label:</strong> value`: labels `Udgivet` (published, `DD-MM-YYYY`), `Jobtype` (e.g. `Fuldtid`), `Arbejdssted` (workplace, free text address), `Ansøgningsfrist` (deadline, `DD-MM-YYYY`, sometimes followed by a time such as `30-11-2026 22.59`, or the free text `Løbende` meaning rolling, which maps to null), `Kontaktperson` (a person's name, personal data).
   * location: `streetAddress` = the `Arbejdssted` text, `addressLocality` = city parsed with the same postcode rule as search, `postalCode` null, `addressRegion` null, `addressCountry` "DK".
   * description: text of every `p` and `li` inside `.job-list-details`, one per line.
   * apply link: first `a.action.primary` href (HTML-decoded, made absolute if it starts with `/`).
   * dates are converted to `YYYY-MM-DD`; unparseable or free text gives null for the deadline and an empty string for datePosted.
3. **Text cleanup**: the live pages contain stray malformed closing tags such as `Fuldtid </ li >` (a space after `<` and after the slash) which an HTML parser leaves as text. Remove only closing-tag shapes (`<` optional spaces `/` optional spaces tag name optional spaces `>`); do **not** strip opening tags or angle brackets that were entity-encoded in author text (`&lt;div&gt;`, `&lt;T&gt;`, `x < y`), which must survive as literal characters. The cleanup applies to title, company, workplace, overview values and description lines. **[live]** the `Jobtype` line was exactly `Fuldtid </ li >`.
4. Output contract fields of the original's detail: `slug, url, title, datePosted, validThrough, employmentType[], hiringOrganization{name, logo}, jobLocation{streetAddress, addressLocality, addressRegion, postalCode, addressCountry}, description, applyUrl`. (For the rebuild's unified contract map to `{id: slug, title, company, location, date, deadline, url, description}` with null for missing.)

## 7. Active / expired detection

* HTTP 404 on `/job/<slug>` was treated as "Job not found" (no retry). A rendered not-found page (title starts with `404`, contains "page not found" or "siden blev ikke fundet", or body text contains "siden blev ikke fundet") is also "not found"; a title that merely contains "404" mid-phrase ("HTTP 404 Page Designer") is a valid posting.
* **[live 2026-10-19] The real behaviour differs**: a nonexistent slug answers **HTTP 301** and redirects to `https://jobdanmark.dk/jobsoeger/find-job` (a generic HTTP 200 search page titled "Find job i Danmark – Se ledige stillinger her", with none of the not-found phrases). A client that follows redirects, as the original did, would not get a 404 and the original's phrase-based check would not fire; the page has no job title selectors so it would end as a parse error or, worse, as a wrong posting. The adapter must disable or inspect redirects: treat any final URL that is not `/job/<slug>` as "expired/removed". Expired postings presumably take the same path (not separately verified; no live expired slug was available).
* A past `validThrough`/deadline (local date) means expired; `Løbende` means no deadline; a missing deadline is unknown.

## 8. Rate limits and failure modes

* Timeout 15 s; 429 and 5xx retried up to 6 times (7 attempts) with exponential backoff 500 ms doubling to a 5 s cap plus 0-500 ms jitter; other 4xx fail at once; the same loop for GET, POST and HTML GET. A 404 on the HTML page returns "none" without retry.
* Bad POST bodies give 400 (the README shows `API request failed: 400 Bad Request` as the example), notably a filter without `displayText`.
* Empty result: `items: []`, `totalItems: 0`. Lookup endpoints return `[]` or groups with empty `items`.
* No rate-limit headers or block page were observed in 9 live requests (all 200 except the 301). If a bot wall appears it will probably be an HTML page on the API paths: always check that API responses are JSON (content-type) before parsing.

## 9. Quirks

* Slug equals the id; it is also the URL tail, so ids are not numeric and can be long.
* The company address is the employer's postal address; the job's workplace appears only on the detail page (`Arbejdssted`). Do not present the search `location` as the work location without saying so.
* The `Kategorier` group in autocomplete is always returned in full: filter it.
* Deadlines carry `22.59`-style times with a dot as the separator.
* `boostJob` ads are paid placements; do not treat them as ranked by relevance.

## 10. Fixtures a test suite should capture

1. A POST search response (30 items) containing: an address with a trailing-comma postcode ("2670, Greve"), one with a 4-digit street number, one with null address, one with null deadline, and a relative `url`.
2. A POST response with zero items; a 400 response for a filter lacking `displayText`.
3. Autocomplete response for a real term and for a nonsense term (all categories returned), including an item with null text.
4. Locations response for a municipality name and for a postal code.
5. Categories response.
6. Detail HTML: (a) a JSON-LD posting with an HTML description with entities, (b) a page with several scripts where the first is malformed and the JobPosting sits inside an array, (c) the rendered layout shown above with `Fuldtid </ li >` and `Løbende` variants, (d) a rendered not-found page, (e) the redirect response (301 plus the generic find-job page) for a missing slug.
7. 429 then 200, persistent 503, and 404 behaviours for the three request kinds (GET JSON, POST JSON, GET HTML).
8. robots.txt as saved on the capture date.

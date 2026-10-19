# Board knowledge note: Jobindex (jobindex.dk)

Audience: the person writing `boards/jobindex.py` (httpx) without access to the original TypeScript. Danish board, off by default.
Sources: original `.agents/skills/jobindex-search/` (SKILL.md, url-reference.md, cli/README.md, cli/src/**, cli/tests/**) plus a handful of polite live requests on 2026-10-19 with User-Agent `job-search-loop-research/0.1`. Live observations are marked **[live 2026-10-19]**; everything else is from the original and was true on its own test fixtures of 2026-08-19.

## 1. Position on robots.txt and terms (read this first)

* `https://www.jobindex.dk/robots.txt` is reachable (200) and is a long `User-agent: *` block. **[live]**
* The original CLI never reads it and sends `q`, `page`, `jobage` and `sort` on every search. The live robots.txt **disallows** (for all agents) these patterns: `/jobsoegning*page=`, `/jobsoegning*sort=`, `/jobsoegning*jobage=`, `/jobsoegning*radius=`, `/jobsoegning*geoareaid=`, `/jobsoegning*archive=`, `/jobsoegning*companyid=`, `/jobsoegning?*&*&` (two or more ampersands), `/jobsoegning*?*+*+` (a query with two or more plus signs, i.e. three or more words), `/jobsoegning/*/*/*?` (category plus area plus any query), `/jobsoegning/*?*&`, `/jobsoegning/kort`, `/jobsoegning/analyse`. Also disallowed: `/api/`, `/c/` and `/c?` (the click-through redirect), `/cgi/`, `/job/*?`, `/jobannonce/korrektur/`, `/jobannonce/embed/`, `/layout/`, `/logo/`, `/img/`, a list of specific `/vis-job/<id>` ids, `/bruger/...`.
* Allowed in practice: `/jobsoegning?q=<one or two words>` (single ampersand-free query), `/jobsoegning.rss?q=...`, `/jobannonce/<id>` and `/vis-job/<id>` (except the few listed ids), the category/area path forms without a query string.
* Consequence for the rebuild: the robots check (RFC 9309, longest-match, `*` and `$` wildcards) is mandatory for this adapter and it will **block** the paging, sorting and age filters the original used. Plan for it: do not request page 2, `sort=` or `jobage=` unless the live robots.txt permits them at run time; instead fetch only `?q=` (and the RSS feed, which is a published syndication interface), and apply recency, sorting and the limit client-side from `firstdate`. Because three-word queries are disallowed by the plus-sign rule, either keep queries to one or two words or send the extra words as a client-side post-filter. The adapter must return an explicit "blocked by robots.txt" outcome (not an empty list) when a URL is disallowed.
* Terms of use were not read for this note (the site's terms page is not an API contract). Jobindex publishes an RSS feed and a robots file that disallows `/api/`; there is no documented public JSON API. Treat HTML/Stash scraping as tolerated-but-fragile, keep volumes small, identify honestly.

## 2. Endpoints and URL patterns

| Purpose | URL | Notes |
|---|---|---|
| Search (HTML with embedded JSON) | `GET https://www.jobindex.dk/jobsoegning?q=<text>&page=<n>&jobage=<days>&sort=<score|date>` | the original's only search path. The old `/jobsoegning.json` returns 204 No Content and is dead. |
| Search (RSS 2.0) | `GET https://www.jobindex.dk/jobsoegning.rss?q=<text>` | advertised in the page as `link_rss` **[live]**. XML declares ISO-8859-1. 20 items **[live]**. |
| Area/category browse (UI paths) | `/jobsoegning/{area}` or `/jobsoegning/{group}/{slug}/{area}` | documented in url-reference.md for a browser; the original CLI does not use them. Area slugs: `storkoebenhavn`, `nordsjaelland`, `sjaelland`, `fyn`, `nordjylland`, `midtjylland`, `sydjylland`, `bornholm`. Category example: `it/itdrift`. Category must precede area. Other slugs must be discovered through the site's own filter UI. |
| Posting (canonical) | `GET https://www.jobindex.dk/jobannonce/{id}` (also `.../{id}/{title-slug}`) | the slug is optional. |
| Posting (short, in RSS and `share_url`) | `https://www.jobindex.dk/vis-job/{id}` | redirects to the jobannonce form. |
| Click-through to employer | `/c?t={id}&ctx=w...` | robots-disallowed; never fetch it. It appears as `url` in search results and as the apply link on detail pages; store it as a string, do not follow it. |

Search query parameters (as the original sent them):

| Param | Values | Effect |
|---|---|---|
| `q` | free text; single quotes around it (`'data engineer'`) give exact match; the city name can be put in the query as the only location mechanism | required by the original (empty query is an error before any request) |
| `page` | 1-indexed integer | 20 results per page, fixed; the page says `max_page` 50 and `total_pages` **[live]** |
| `jobage` | positive integer days; documented values 1, 7, 14, 30; 9999 means all (the original default). Any integer >= 1 was accepted by the original, only the documented values are known to be meaningful | recency filter |
| `sort` | `score` (relevance, default) or `date` (newest first) | unvalidated string in the original |
| `area`, `geoareaid`, `radius` | none work as query params | silently ignored per the original's README (and robots disallows them anyway) |

Not URL-constructable (UI only): employment type, working hours, remote work.

## 3. Where results are found in the search response

The page embeds a script assigning `var Stash = {...};`. The original extracts the object by scanning from the first `{` and counting braces while respecting string quotes and backslash escapes (a regex is not safe: the blob is ~390 KB and contains braces in strings), then parses it as JSON. If the marker is missing the failure is "Could not locate Stash blob"; if the braces never balance, "Unterminated Stash blob".

Inside the parsed object the original searches recursively for the first object holding `searchResponse.results` as an array. **[live]** the container is `jobsearch/result_app/list/storeData` (the original's docs and fixtures say `jobsearch/result_app/storeData`; the recursive search is what makes both work, so keep it recursive). Sibling Stash keys seen: `common`, `cookie_consent`, `jobagent/popup`, `login`, `sentry`, `widgets/jix_email_field`.

In the store object **[live]**: `hitcount` (integer total, 180 for `q=python`), `page_size` (20), `max_page`, `total_pages`, `results` (list), `link_canonical`, `link_rss`, `title` ("Ledige job - Python"), `facets` (empty dict here), `suggestedChanges`, `archive_toggle`, `link_subpages`.

Per result (live keys): `tid`, `headline`, `company` (dict, may contain `name`, `homeurl`, `bottom_logo_url`), `companytext` (plain string), `area`, `geojson` (feature collection with `properties.title`), `firstdate`, `lastdate`, `apply_deadline`, `apply_deadline_asap`, `share_url`, `url`, `html` (the rendered card), `is_archived`, `home_workplace`, `salary`, `source`, `addresses`, `has_spo`, `workplace_company`, and others. The original maps:

| Contract field | Source | Rules |
|---|---|---|
| id | `tid` | ids are a letter plus digits: `h...` (jobindex-hosted) or `r...` (robot-collected / external). Empty string if missing. |
| title | `headline` | empty string if missing |
| company | `company.name`, else `companytext`, else null | |
| companyUrl | `company.homeurl` or null | not in the shared contract; optional |
| location | `area`, else `geojson.features[0].properties.title`, else null | |
| date | `firstdate` (already `YYYY-MM-DD`) | null if missing |
| deadline | if `apply_deadline_asap` is true: null (it wins over any date field). Else if `apply_deadline` is a string: its first 10 characters. Else if `lastdate` is a string: that value. Else null | **[live]** `apply_deadline` looks like `2026-11-02T22:59:59Z` (UTC). `h` ads end at 22:59:59Z (= 23:59:59 Danish time, same date) but some `r` ads end at 23:00:00Z, which is midnight Danish time of the *next* day, so slicing the first ten characters can be one day early. Convert UTC to Europe/Copenhagen before taking the date (the original did not). About half of live results are ASAP (null deadline). `lastdate` is "last date the ad is shown", not necessarily an application deadline. |
| url | `https://www.jobindex.dk/jobannonce/{tid}` when tid exists, else `share_url`, else `url`, else empty | |
| description | always null on search | the card's `html` field holds a teaser; the original ignored it |
| total | `hitcount` if numeric, else number of results on the page | |

Legacy (unused) card-HTML parsing: `div#jobad-wrapper-<id>` with classes `PaidJob` / `jix_robotjob`, title in `h4 a`, company in `.jix-toolbar-top__company a`, location in `span.jix_robotjob--area`, date in `time[datetime]`, teaser in the first `<p>` (300 chars). Do not implement; the Stash path replaced it. A hit-count regex `af <strong>1.234</strong>` (Danish thousands dots) also exists but is unused.

RSS items **[live]**: each `<item>` has `title` (headline plus company), `link` (`/vis-job/{id}`), `description` (HTML-escaped job card, including logo, `h4` headline, `jobad-element-area`), several `category` elements, `guid` (`https://www.jobindex.dk/{numeric id}`, no `h` prefix), `pubDate` (RFC 822, `+0200`). No deadline field. The RSS is the robots-friendly fallback when Stash scraping is blocked; fields are thinner (no area/deadline outside the escaped HTML).

## 4. Detail page

Fetch `https://www.jobindex.dk/jobannonce/{id}` (follow redirects). The original rebuilds that URL from a bare id or from a URL; see section 6 for the input gate.

Two page shapes were documented from fixtures of 2026-08-19:

1. **Native** (recognised by any `class="jd-` substring): title from `og:title` (fallback `meta itemprop=name`, fallback first `h1`); company from the `<title>` tag formatted `COMPANY - Job title` (only accepted when the part after ` - ` equals the title, so an unrelated title never becomes a company); location, deadline, job type and hours from `jd-location`, `jd-deadline`, `jd-type`, `jd-workhours` blocks (value is the first `<p>` inside the block); description from `jd-description`.
2. **External ATS passthrough** (hr-manager / Talentech style: the employer's hosted ad served through jobindex): `og:url` and `og:site_name` point at the ATS, so `company` is null (never the ATS brand); location from `class="workplace..."` followed by `<span class="empty">`; deadline from the first date within 300 characters after the label `Ansøgningsfrist`, `Application due` or `Frist`, searched only in visible markup (the label also occurs in a CSS comment in `<style>`, which an earlier version captured as the deadline); description is the visible body text (head, script, style, comments stripped; block-closing tags become newlines), replaced by `og:description`/`description` meta when the body text is under 100 characters.

Date formats accepted for deadlines: ISO `YYYY-MM-DD...`, `DD-MM-YYYY`, and Danish long form (`13. september 2026`, month names januar..december, optional dot). Output is `YYYY-MM-DD` or null.

Other detail fields: `date` from the first `<time datetime>` (normalised); `applyUrl` from the first `href="/c?t=..."` (prefixed with the site base, `&amp;` decoded); `companyUrl` is always null; `id` and `url` are always the jobindex id and its `jobannonce` URL, never `og:url`/canonical (those point at the external ATS or carry a slug). A page without any title gives a parse error.

**[live 2026-10-19, drift warning]** A fresh `h` posting (BEUMER, `h1699736`) was neither shape: no `jd-*` blocks, no `workplace` widget, no deadline label anywhere; `og:title`, `og:url` (with title slug), `og:description` (teaser) and `link rel=canonical` were present, with meta tags written content-first (`<meta content="..." property="og:title">`); `<h1>` present; the apply link `/c?t=<id>&ctx=w` present; the visible body was ~16 KB. A parser built only from the August fixtures would return null company/location/deadline for such pages. The adapter should therefore (a) match meta attributes in either order, (b) fall back to the search-result record (company, area, deadline) already fetched for the same id, and (c) save this page as a fixture.

HTML entity handling: decode named `&amp; &lt; &gt; &quot; &#39; &apos; &nbsp;`, the Danish named entities `&oslash; &Oslash; &aelig; &AElig; &aring; &Aring;`, and decimal/hex numeric references up to U+10FFFF including emoji; out-of-range values are dropped, never an exception. (In Python use `html.unescape`, which covers all of these.)

## 5. Active / expired detection

* The search page lists current ads plus, if `archive` toggling is on, archived ones; results have `is_archived`. Treat `is_archived: true` as expired.
* A deadline in the past (after timezone conversion) means expired; an ASAP ad (`apply_deadline_asap`) has deadline null = no stated deadline, not expired. `lastdate` in the past also means the ad is no longer displayed.
* Detail: HTTP 404 is "Job not found". Behaviour for a removed ad that redirects to a search page was not tested; detect it by checking that the final URL still contains `/jobannonce/<id>`.

## 6. Input gate for `detail`

A detail argument may be a bare id or a URL. A bare id is any non-empty token of letters, digits, underscore, hyphen (no scheme, no slash); unknown ids simply 404. A URL must be `http(s)`, its host must be exactly `jobindex.dk` or end in `.jobindex.dk` (parse the URL and compare the hostname; reject `jobindex.dk.evil.example` and `www.jobindex.dk@evil.example`), and its path must contain `/jobannonce/` followed by a letter and digits. The fetch URL is then rebuilt as `https://www.jobindex.dk/jobannonce/{id}`; the raw input is never fetched. Anything else is a bad-id error. Trailing slash, query string and title slug are ignored; subdomains like `it.jobindex.dk` are accepted.

## 7. Rate limits, failures, blocks

* Original policy: timeout 15 s per request; retry on 429 and any 5xx up to 6 retries (7 attempts) with exponential backoff starting 500 ms doubling to a 5 s cap, plus 0-500 ms random jitter; no retry on other 4xx; 404 on a posting is "not found".
* No `Retry-After` handling in the original; the rebuild should honour it.
* The original User-Agent was `Mozilla/5.0 (compatible; jobindex-cli/1.0)` with `Accept-Language: da,en;q=0.9`. The rebuild uses an honest UA naming the loop.
* No block page was encountered in the live probes (2 search/RSS GETs, 1 posting GET, 1 robots GET, all 200). A block would most plausibly be 403/429 or a consent/captcha HTML page lacking `var Stash`; the missing-Stash error is therefore the signal, and it must be reported as a distinct "layout changed or blocked" failure rather than as zero results. A legitimately empty result is a Stash with `hitcount: 0` and `results: []` (the original's own test fixture).
* Danish locale quirk: a no-results search can still render suggestions (`suggestedChanges`); do not mistake them for results.

## 8. Quirks

* Ads come from two populations: `h` ids (jobindex-hosted) and `r` ids (robot-collected from employer sites); both are valid detail ids. The `rN` ads may show different deadline timezone behaviour (see above).
* The search page is ~390 KB; fetch once per query.
* Location filtering: none server-side by param; put the city in `q`, or use the path-based area slugs (UI form) if robots permits, or filter client-side on `area`.
* `meta.perPage` was hard-coded 20 and `limit` applied client-side with a minimum of 1.
* Ids in RSS `guid` lack the letter prefix; take the id from `link` instead.

## 9. Fixtures a test suite should capture

1. Search page for a typical query (full HTML, redacted of cookies): normal results with a mix of `h` and `r` ids, ASAP and dated deadlines, UTC `Z` deadlines at 22:59:59 and 23:00:00.
2. Search page with zero hits (`hitcount: 0`, `results: []`).
3. Search page where the Stash marker is absent (a consent wall or block page) to pin the "layout changed or blocked" outcome.
4. A Stash whose store lacks `hitcount` (total falls back to result count), and one with a result lacking `area` but with `geojson`.
5. Detail pages: one native `jd-*` page, one external ATS passthrough (with the CSS-comment trap), and one of the 2026 live shape described in section 4.
6. The RSS feed for the same query (ISO-8859-1).
7. A 404 for a removed posting, and a 429 then 200 sequence for backoff tests.
8. The robots.txt file as saved on the capture date, to test the longest-match and wildcard rules against `/jobsoegning?q=python`, `/jobsoegning?q=python&page=2`, `/jobsoegning?q=a+b+c`.

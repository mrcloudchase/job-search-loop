# Board knowledge note: Jobnet (jobnet.dk, STAR)

Audience: the person writing `boards/jobnet.py` (httpx) without the original TypeScript. Danish board, off by default.
Sources: original `.agents/skills/jobnet-search/` (SKILL.md, cli/README.md, cli/src/**, cli/tests/**) plus a few polite live requests on 2026-10-19 with User-Agent `job-search-loop-research/0.1`. Live observations are marked **[live 2026-10-19]**.

## 1. Position on robots.txt and terms

* `https://jobnet.dk/robots.txt` answers **HTTP 307 to a login route** (`/auth/duendebff/login?returnUrl=%2Frobots.txt`) for an anonymous client. **[live]** There is no readable robots.txt. By RFC 9309 an unavailable robots file (4xx) means "no restrictions", but a redirect to an authentication page is not a clean 404; the adapter should record "robots.txt unavailable (auth redirect)" and treat it as a decision point for the owner rather than silently assuming permission. Never log in.
* The data comes from the site's own backend-for-frontend JSON API under `https://jobnet.dk/bff`, which the public single-page app calls. It is undocumented, not a published API, and the original relies on a custom request header `x-csrf: 1` that the web app itself sends. Using it is the same traffic the site's own pages generate, but it is not an invited integration; keep volume low and make it off by default (as planned). No terms were read for this note.
* Operator: STAR (Styrelsen for Arbejdsmarked og Rekruttering), the national public job portal; it also lists external ads.

## 2. Endpoints

All under `https://jobnet.dk/bff`, all GET, all JSON, all need header `x-csrf: 1`.

| Purpose | Path | Notes |
|---|---|---|
| Search | `/FindJob/Search` | parameters below |
| Detail | `/FindJob/JobAdDetails/{jobAdId}?incrementViews=false` | `incrementViews=false` avoids bumping the public view counter; keep it |
| Occupation lookup | `/OccupationSearch?searchString=<text>&pageSize=<n>` | returns a bare JSON array; the server does not reliably honour `pageSize`, slice client-side |
| Typeahead | `/FindJob/GetTypeaheadSuggestions?query=<text>` | returns a bare JSON array of strings (Danish titles; English words give few or none) |
| Company logo | `https://jobnet.dk{logoUrl}` | `logoUrl` is a relative `/bff/SharedComponents/JobAdCard/CompanyLogo/ByJobAdId/{id}` path |
| Human posting page | `https://jobnet.dk/find-job/{jobAdId}` | HTTP 200 for internal ads **[live]** (it is a single-page shell). The legacy `/job/{id}` route redirects anonymous visitors into a MitID login: never emit it. |

Missing header **[live]**: the same search without `x-csrf: 1` returns **HTTP 401** with an empty body. So a 401 means "header missing/blocked", not "bad credentials".

## 3. Search parameters

| Param | Values | Notes |
|---|---|---|
| `resultsPerPage` | integer >= 1 (original default 10) | **required** together with `pageNumber`; omitting either while sending `searchString` returns HTTP 400, error code 1014 "Fejl i formatering af inputs" **[live]** |
| `pageNumber` | 1-indexed | required |
| `orderType` | `PublicationDate` (default, newest first), `BestMatch` (relevance; only meaningful with a search string), `ApplicationDate` (earliest deadline first) | passed through unvalidated in the original; validate in the rebuild |
| `searchString` | free text | optional; the job id works as a search string and finds that ad **[live]** (used by the detail fallback) |
| `regions` | one of `HovedstadenOgBornholm`, `Midtjylland`, `Syddanmark`, `OevrigeSjaelland`, `Nordjylland` | single value in the original; the facet list confirms these five **[live]** |
| `postalCode` + `kmRadius` | 4-digit postal code; radius integer >= 1 km (original default 50, sent only when a postal code is given) | do not combine with `regions` (advice from the skill; not enforced by the original) |
| `workHoursType` | `FullTime` or `PartTime` | |
| `employmentDurationType` | `Permanent` or `Temporary` | |
| `jobAnnouncementType` | `Ordinaert`, `Efterloenner`, `Foertidspension` | the jobs' announcement kind ("Almindelige vilkår" is the display name of `Ordinaert`) |
| `occupationAreas` | area identifier such as `10000` | the facet list returns area identifiers with counts; a human-readable mapping is not given by the API |
| `occupationGroups` | group identifier such as `10060` | |

There is no recency/"posted within N days" parameter. Recency is therefore client-side from `publicationDate` (or use `orderType=PublicationDate` and stop paging when dates fall outside the window). Filter *names for lookup*: occupation concepts come from `/OccupationSearch` (ESCO concept URIs `http://data.star.dk/esco/occupation/<uuid>`; the original's docs say the UUID tail can seed the occupation filters, but the exact accepted form of `occupationAreas`/`occupationGroups` values is not verified; the facets `byAlias`, `occupationAreas`, `occupationGroups`, `occupations` expose the identifiers that can be filtered on).

Facet keys returned **[live]**: `byAlias`, `occupationAreas`, `occupationGroups`, `occupations`, `regions`, `countries`, `jobAnnouncements`, `employmentDurations`, `workHours`, `disabilityFriendly`, `workHoursPartTime`. The original passes through only `regions`, `workHours`, `employmentDurations`, `occupationAreas`, `countries`.

## 4. Search response

Top level: `jobAds` (list), `searchFacets` (object), `totalJobAdCount` (int), `searchString` (string echo). **[live]** `totalJobAdCount` for an empty query was 22,687; `python` 313; `sygeplejerske` 870; region Nordjylland 1,809.

Per ad (live keys): `jobAdId` (UUID), `title`, `hiringOrgName`, `occupation`, `conceptUriDa`, `jobAnnouncementTypeName`, `municipality`, `postalCode` (int), `postalDistrictName`, `country`, `workPlaceAddress` (can have trailing spaces), `cvr`, `publicationDate` (ISO with offset, e.g. `2026-10-19T00:00:00+02:00`), `applicationDeadline`, `applicationDeadlineSort`, `applicationDeadlineStatus`, `workHourPartTime` (bool), `isExternal` (bool), `jobAdUrl`, `hasLogo`, `logoUrl`, `isSeen`, `isFavorite`, `isJoblogged`, and `description` (full HTML of the ad body, ~6.5 KB for one live ad). Because `description` is present in the search payload, a Python adapter can fill the contract `description` from search for free; the original deliberately dropped it to save tokens (it has to be converted to text).

Normalisation the original applies (and the contract mapping):

| Contract field | Source | Rules |
|---|---|---|
| id | `jobAdId` | |
| title | `title` | |
| company | `hiringOrgName` | |
| location | `postalDistrictName`, else `municipality`, else null | |
| date | first 10 chars of `publicationDate`; null if the field is null (a null must not crash the whole page; the original once did) | the date is in local (+01/+02) time, so slicing the first ten characters is the local date |
| deadline | first 10 chars of `applicationDeadline`; null if null **or if it starts with `1900-01-01`** | `1900-01-01` is the "deadline not disclosed" sentinel, paired with `applicationDeadlineStatus: NotDisclosed`. Live statuses seen: `ExpirationDate`. |
| url | for external ads (`isExternal` true) the employer's `jobAdUrl` **only if** it is an absolute http or https URL (value trimmed; `javascript:`, `file:`, relative or blank are rejected); otherwise `https://jobnet.dk/find-job/{jobAdId}` | internal ads always use the find-job URL |
| jobAdUrl | the vetted destination or null | |
| description | null in the original's search output | |

Native fields are kept in the original's output (null-coalescing `occupation`, `municipality`, `postalCode`, `postalDistrictName`, `applicationDeadline`, `applicationDeadlineStatus`, `logoUrl`, `cvr`, `jobAdUrl`; `workPlaceAddress` defaults to empty string).

Pagination: `pageNumber` + `resultsPerPage`; the total page count is `ceil(totalJobAdCount / resultsPerPage)`. 20 per page worked live. No cursor. Deep paging limits were not probed.

## 5. Detail response

`GET /FindJob/JobAdDetails/{id}?incrementViews=false` returns (live keys for an internal ad): `id`, `title`, `body` (HTML), `publicationDateTime` (7-digit fractional seconds with offset), `unpublicationDateTime` (the posting's end of publication, equal to the deadline for the live example), `approvalStatus` (`Godkendt`), `views`, `createdDateTime`, `updatedDateTime`, `isAnonymousEmployer`, `hasLogo`, `isJoblogged`, `organisationTypeId`, `user`, `employer {cvrNumber, pNumber, name, hasCompanyLogo}`, `job {type, address{streetName, city, postalCode, municipality, countryCode, countryName}, noFixedWorkplace, isLimitedPeriod, isDisabilityFriendly, isPartTime, employmentDate, conceptUriDa, preferredLabelDa, driversLicenses, classifications, shifts, isFavorite}`, `application {deadlineDate, availablePositions, contactPersons[{firstNames, lastName, title, emailAddress, phoneNumber}], emailAddress, url, urlText, isApplicationDeadlineASAP}`. Contact persons carry personal data: do not log or persist them beyond what the loop needs.

* `application.url` may be missing/empty (the employer takes applications through Jobnet's own system; the live internal ad had no `url`).
* `application.deadlineDate` starting with `1900-01-01` must be replaced with null before anything else sees it (otherwise the ad reads as 126 years expired). `isApplicationDeadlineASAP` or `applicationDeadlineStatus == NotDisclosed` marks "no stated deadline".
* **External ads return HTTP 404 from the detail endpoint** (error body `{"errorInformation":{"errorCode":121100,...}}`) **[live]**, as does any unknown id **[live]**. The original's fallback on a 404: run a search with `searchString=<the id>`, `resultsPerPage=5`, `pageNumber=1`, `orderType=PublicationDate`, pick the ad whose `jobAdId` equals the id (**[live]** this works, returns exactly one ad with its HTML description), and synthesise a detail record from the search fields (street from `workPlaceAddress` trimmed or null, city from `postalDistrictName` then `municipality`, postal code as string, country code "DK" when country is "Danmark", `job.type` from `jobAnnouncementTypeName` else FullTime/PartTime from `workHourPartTime`, `isPartTime`, `application.url` from `jobAdUrl` trimmed or null, deadline from `applicationDeadline`, ASAP flag true only when status is `NotDisclosed`, body from `description` or empty, many fields null, `isExternal` true). It prints a note on stderr when the fallback is used. If the search finds nothing, the result is "not found". Any non-404 failure is an API error.

## 6. Active / expired detection

* A posting is live while it appears in search and its detail endpoint answers 200. The deadline (date part, local time) in the past means expired; `unpublicationDateTime` is the publication end. External ads vanish from the search when the feed drops them; a 404 with no search match = gone.
* "Not disclosed" deadline means unknown, not expired.

## 7. Input gate for `detail`

The id argument is trimmed; a token of letters, digits, underscore or hyphen is accepted as is; otherwise the id is extracted from a URL containing `/find-job/`, `/find-job/detaljer/`, `/JobAdDetails/` or `/Details/` (legacy `job.jobnet.dk/CV/FindWork/Details/<id>` works), followed by `/`, end, `?` or `#`. Anything else (including `https://example.com/other/123`) is a bad-id error. The original does not check the host of a URL beyond that path pattern; the rebuild should require host `jobnet.dk` or `*.jobnet.dk` for URL inputs.

## 8. Rate limits and failure modes

* Timeout 15 s; retry on 429 and 5xx, up to 6 retries (7 attempts), backoff 500 ms doubling to 5 s cap plus 0-500 ms jitter; other 4xx fail immediately.
* UA in the original: `Mozilla/5.0 (compatible; jobnet-cli/1.0)` plus `x-csrf: 1`.
* Failures seen **[live]**: 401 empty body (missing header), 400 + errorCode 1014 (bad/missing paging params), 404 + errorCode 121100 (unknown or external ad detail), 307 to a login route for `/robots.txt`. Error bodies are JSON with `errorInformation.errorCode`, `errorInformation.errorMessage` (Danish) and a `correlationId`.
* An empty result is `totalJobAdCount: 0`, `jobAds: []`; `searchFacets` is still present.

## 9. Quirks

* `description` HTML must be stripped to text (tags to spaces, decode `&amp; &lt; &gt; &quot; &#39; &nbsp;`, collapse whitespace). Python `html.unescape` plus a tag-stripper that preserves paragraph breaks is better than the original's flat single-line output.
* Typeahead strings may contain embedded quotes (`Sygeplejerske "Lægeassistent"`); treat them as plain text.
* `postalCode` is an int in search and a string in detail.
* `occupations` response items nest `aliases[]` with `aliasIdentifier`, `conceptUriDa`, `alternativeLabelDa`; the concept URI is long (over 70 characters): never truncate it in any table output.
* Facet counts are computed for the current search, not globally.

## 10. Fixtures a test suite should capture

1. Search JSON: an ordinary page mixing internal and external ads; one ad with `publicationDate` null; one with `applicationDeadline` `1900-01-01T00:00:00+01:00` and status `NotDisclosed`; one external ad with a good `jobAdUrl`, one with null, one with a `javascript:` value.
2. Search JSON with zero hits.
3. Detail JSON for an internal ad (with contact persons, empty `application.url`), and one with the 1900 sentinel in `deadlineDate`.
4. The 404 error body for an external ad plus the matching search-by-id response (fallback path).
5. The 400/1014, 401 and 404/121100 bodies.
6. OccupationSearch response (long concept URIs) and a typeahead array.
7. Sequence tests: 429 then 200; persistent 500 (seven calls then failure).
8. The 307 response for robots.txt to pin the "robots unavailable" handling.

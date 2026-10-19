# freehire fixtures

Captured 2026-10-19 from https://freehire.me/api/v1 with User-Agent `job-search-loop-research/0.1`, a handful of requests,
trimmed to two postings with descriptions cut short (public job-ad text; no personal data).

- search_agent.json: GET /agent/jobs/search?q=data engineer&limit=3&description_format=markdown&posted_within_days=7&regions=eu
- search_lean.json: GET /jobs/search with the same query (lean preview descriptions)
- search_empty.json: GET /agent/jobs/search?q=zzqxjwvkpl (zero hits)
- detail_open.json: GET /jobs/data-engineer-team-eiffel-pashb7sp (enrichment with salary and period)
- error_not_found.json, error_pagination.json: real error bodies (404, and 400 for offset 10000 plus limit)
- detail_closed.json: DERIVED from detail_open.json by setting `closed_at` and emptying `enrichment`; no closed posting was
  found live (search lists open jobs only), so this one is not a verbatim capture.
- error_body.json, rate_limited.json: SYNTHETIC bodies in the API's `{"error": ...}` envelope; a 429 was not provoked on a
  polite public service. The 429 status itself is supplied by the test transport.

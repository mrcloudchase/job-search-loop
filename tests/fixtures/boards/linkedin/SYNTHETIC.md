# These fixtures are synthetic

Every file here was written by hand from the page structure described in docs/boards/linkedin.md. They are NOT saved
LinkedIn responses. Companies, postings, ids and people are fictional (ids are made up; Example Works, Northwind Demo
and the like do not exist).

Why: LinkedIn answered this machine's first request with an HTTP 429 JavaScript challenge, and the adapter must not
evade that. So no real response could be captured, and no live smoke test was run. When a person with working access
saves real responses, they can replace these files; keep the capture date and a hash beside them.

Tests never contact LinkedIn.

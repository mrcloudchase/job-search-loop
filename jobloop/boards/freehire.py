"""freehire.me: a public, unauthenticated JSON API that aggregates postings from many employers' applicant systems.

Search is `GET /api/v1/agent/jobs/search`, which returns each posting with its full description (Markdown); detail is
`GET /api/v1/jobs/<slug>`. Only these two read endpoints are ever called: never the per-user ones (apply, save, me).
Run as `python -m jobloop.boards.freehire search --query ...`.

What the board filters natively: the query text (`q`), recency (`posted_within_days`), the page size (`limit`), and
geography: a location is mapped to `countries` (ISO codes or country names), `regions=eu` (Europe) or `work_mode=remote`.
What it cannot filter: a city or any other free text (the board's geography is derived per posting and often missing), so a
location word that is not recognised sends no geography filter and the loop filters on each result's `location`. Nothing
here is trusted to be complete: a filter only narrows what the board returns.

Promises: a closed posting (`closed_at` set) is never returned as open; detail of one is `not_found` and says when it
closed. A 404 from search is a configuration error, never "no results". An empty page is success; a 2xx that is not the
documented envelope is a parse error. The API publishes no application deadline, so `deadline` is always None.
`detail` puts a labelled "freehire facts" block (seniority, category, employment type, salary with its period, and the
board's own staleness read) before the description, because the shared result shape has no field for them.
"""
from __future__ import annotations

import html
import re
import sys
from urllib.parse import quote, urlparse

from .contract import BoardError, Query, run_cli

NAME = "freehire"
HOSTS = ("freehire.me",)
DEFAULT_ENABLED = True
TERMS = ""
ACK_WAIVES_ROBOTS = False

API = "https://freehire.me/api/v1"
MAX_DEPTH = 10_000  # the server refuses offset + limit beyond this
DESCRIPTION_FORMATS = ("markdown", "text", "html")
COUNTRY_CODES = frozenset(
    "ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bl bm bn bo bq br bs bt bv bw by bz ca cc cd "
    "cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg eh er es et fi fj fk fm fo fr ga gb gd ge gf "
    "gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu id ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn "
    "kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mf mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na "
    "nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si "
    "sj sk sl sm sn so sr ss st sv sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug um us uy uz va vc ve vg vi "
    "vn vu wf ws ye yt za zm zw".split())
COUNTRY_NAMES = {
    "united states": "us", "usa": "us", "united kingdom": "gb", "uk": "gb", "england": "gb", "germany": "de", "deutschland": "de",
    "france": "fr", "spain": "es", "italy": "it", "netherlands": "nl", "holland": "nl", "belgium": "be", "denmark": "dk",
    "danmark": "dk", "sweden": "se", "norway": "no", "finland": "fi", "ireland": "ie", "poland": "pl", "portugal": "pt",
    "austria": "at", "switzerland": "ch", "czechia": "cz", "czech republic": "cz", "canada": "ca", "australia": "au",
    "new zealand": "nz", "india": "in", "japan": "jp", "singapore": "sg", "brazil": "br", "mexico": "mx", "israel": "il",
    "united arab emirates": "ae", "south africa": "za", "romania": "ro", "hungary": "hu", "greece": "gr", "estonia": "ee",
    "latvia": "lv", "lithuania": "lt", "ukraine": "ua", "turkey": "tr", "china": "cn", "south korea": "kr",
}
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


def geography(location: str) -> dict[str, list[str] | str]:
    """The API parameters a free-text location can be trusted to set; words it cannot place add nothing."""
    if len(location) > 100:
        raise BoardError("invalid", f"a location is at most 100 characters, not {len(location)}")
    out: dict[str, list[str] | str] = {}
    countries: list[str] = []
    for token in (t.strip().lower() for t in location.split(",")):
        if token in COUNTRY_NAMES:
            countries.append(COUNTRY_NAMES[token])
        elif len(token) == 2 and token in COUNTRY_CODES:
            countries.append(token)
        elif token in ("europe", "eu"):
            out["regions"] = ["eu"]
        elif token == "remote":
            out["work_mode"] = "remote"
    if countries:
        out["countries"] = list(dict.fromkeys(countries))
    return out


def build_params(query: Query, *, description_format: str = "markdown", offset: int = 0) -> dict[str, object]:
    """The search parameters for a query. Refuses what the server would silently misread."""
    if description_format not in DESCRIPTION_FORMATS:
        raise BoardError("invalid", f"description_format is one of {', '.join(DESCRIPTION_FORMATS)}, not {description_format!r}")
    if offset < 0 or offset + query.limit > MAX_DEPTH:
        raise BoardError("invalid", f"offset plus limit must be between 0 and {MAX_DEPTH}, not {offset + query.limit}")
    params: dict[str, object] = {"q": query.text, "limit": query.limit, "offset": offset, "description_format": description_format}
    if query.recency_days is not None:
        params["posted_within_days"] = query.recency_days
    geo = geography(query.location)
    params.update(geo)
    return params


def normalize_slug(ref: str) -> str:
    """A posting's public slug from a bare slug or a freehire.me `/jobs/<slug>` url; anything else is refused before a request."""
    ref = (ref or "").strip()
    if SLUG.fullmatch(ref):
        return ref
    parts = urlparse(ref)
    if parts.scheme in ("http", "https") and (parts.hostname or "").lower() in HOSTS and not parts.username:
        m = re.fullmatch(r"/jobs/([A-Za-z0-9][A-Za-z0-9-]*)/?", parts.path)
        if m:
            return m.group(1)
    raise BoardError("invalid", f"not a freehire posting slug or freehire.me/jobs/<slug> url: {ref[:80]!r}")


def clean_html(text: object) -> str | None:
    """A detail description as readable text: block ends become line breaks, tags go, entities are decoded."""
    if not isinstance(text, str):
        return None
    text = re.sub(r"(?i)<br\s*/?>|</(p|li|ul|ol|div|h[1-6])\s*>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]*>", "", text))
    text = re.sub(r"[ \t\r\f\v\xa0]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None


def format_salary(enrichment: dict) -> str | None:
    """`EUR 3500-5500/month`: the period stays, so a yearly figure is never read as monthly. None when no bound is known."""
    low, high = enrichment.get("salary_min"), enrichment.get("salary_max")
    if low is None and high is None:
        return None
    amount = f"{low}–{high}" if low is not None and high is not None else str(low if low is not None else high)
    period = f"/{enrichment['salary_period']}" if enrichment.get("salary_period") else ""
    return f"{enrichment.get('salary_currency') or ''} {amount}{period}".strip()


def short(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def day(value: object) -> str | None:
    """The leading YYYY-MM-DD of a timestamp (offsets are not converted), or None if it does not look like a date."""
    m = re.match(r"\d{4}-\d{2}-\d{2}", value) if isinstance(value, str) else None
    return m.group(0) if m else None


def to_result(raw: dict) -> dict:
    """One posting in the contract's shape. The description is the server's own Markdown, passed through untouched."""
    title = html.unescape(raw["title"]).strip() if isinstance(raw.get("title"), str) else ""
    return {"id": short(raw.get("public_slug")), "title": title or "(untitled)", "company": short(raw.get("company")),
            "location": short(raw.get("location")), "date": day(raw.get("posted_at")), "deadline": None,
            "url": short(raw.get("url")), "description": short(raw.get("description"))}


def data_of(response, expected: type) -> object:
    """The envelope's `data`, or a parse error: a 2xx that is not the documented shape is never an empty success."""
    try:
        body = response.json()
    except ValueError:
        raise BoardError("parse", f"{NAME} returned an unparseable response body") from None
    if not isinstance(body, dict) or not isinstance(body.get("data"), expected):
        raise BoardError("parse", f"{NAME} did not send the expected envelope: {str(body)[:120]}")
    return body["data"]


def search(query: Query, http) -> list[dict]:
    """Open postings matching the query, newest first as the board ranks them. Closed ones are dropped."""
    path = f"{API}/agent/jobs/search"
    try:
        response = http.get(path, params=build_params(query), headers={"Accept": "application/json"})
    except BoardError as e:
        if e.kind == "not_found":
            raise BoardError("invalid", f"{NAME} has no {path}: check the board is current (a 404 is not an empty result)") from None
        raise
    return [to_result(j) for j in data_of(response, list) if isinstance(j, dict) and not j.get("closed_at")]


def facts(job: dict) -> str:
    """The detail facts the shared result shape has no field for, as one labelled block."""
    enrichment = job.get("enrichment") if isinstance(job.get("enrichment"), dict) else {}
    reality = job.get("reality") if isinstance(job.get("reality"), dict) else {}
    cities = job.get("cities") if isinstance(job.get("cities"), list) else []
    pairs = [("seniority", enrichment.get("seniority")), ("category", enrichment.get("category")),
             ("employment type", enrichment.get("employment_type")), ("salary", format_salary(enrichment)),
             ("cities", ", ".join(map(str, cities)) or None), ("work mode", job.get("work_mode")),
             ("board's staleness read", reality.get("class")), ("days since first posted", reality.get("age_days")),
             ("reposts", reality.get("repost_count")), ("fake freshness flagged", reality.get("fake_freshness"))]
    lines = [f"{k}: {v}" for k, v in pairs if v is not None]
    return "freehire facts:\n" + "\n".join(lines) if lines else ""


def detail(ref: str, http) -> dict:
    """One posting by slug or freehire.me url. A closed posting is `not_found` and says when it closed; a missing one is too."""
    slug = normalize_slug(ref)
    job = data_of(http.get(f"{API}/jobs/{quote(slug, safe='')}", headers={"Accept": "application/json"}), dict)
    if job.get("closed_at"):
        raise BoardError("not_found", f"{NAME} posting {slug} closed on {day(job['closed_at']) or job['closed_at']}")
    out = to_result(job)
    block, body = facts(job), clean_html(job.get("description"))
    out["description"] = "\n\n".join(part for part in (block, body) if part) or None
    return out


if __name__ == "__main__":
    raise SystemExit(run_cli(sys.modules[__name__]))

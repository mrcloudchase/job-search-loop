"""Jobnet (jobnet.dk, run by STAR, the Danish public job portal): the board adapter. Off by default; Danish market only.

It reads the site's own backend-for-frontend JSON API under https://jobnet.dk/bff, the traffic the public web app
generates. That API is undocumented and not an invited integration, so it is opt-in (`DEFAULT_ENABLED = False`) and
the volume stays low (a few pages per search). Every request carries `x-csrf: 1`, without which the API answers 401.

robots.txt, stated honestly: https://jobnet.dk/robots.txt answers a 307 to a login page, so there is no readable file.
The Client's robots gate would read that as a disallow, which is right for scraped HTML but wrong here, because
nothing is scraped: every request is a JSON API call, so each one passes `html=False` and the gate is not used. This is
a decision the owner can revisit, not a finding that Jobnet permits it. The adapter never logs in, never follows a
login redirect off the host, and never emits the legacy `/job/{id}` link, which forces a MitID login.

The API has no recency parameter, so recency (and any location it cannot map) is filtered client-side. A detail for an
external ad is a 404 on the detail endpoint, so the posting is found again through search by its id.
Contact persons in a detail are personal data and are never copied out.
"""
from __future__ import annotations

import html
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .contract import BoardError, Query, parse_date, recent
from .http import Client

NAME = "jobnet"
HOSTS = ("jobnet.dk",)
DEFAULT_ENABLED = False
TERMS = ""
ACK_WAIVES_ROBOTS = False

BASE = "https://jobnet.dk/bff"
HEADERS = {"x-csrf": "1", "Accept": "application/json"}
PAGE_SIZE = 20
MAX_PAGES = 5
DEFAULT_RADIUS_KM = 50
NOT_DISCLOSED = "1900-01-01"

REGIONS = ("HovedstadenOgBornholm", "Midtjylland", "Syddanmark", "OevrigeSjaelland", "Nordjylland")
WORK_HOURS = ("FullTime", "PartTime")
DURATIONS = ("Permanent", "Temporary")
ANNOUNCEMENTS = ("Ordinaert", "Efterloenner", "Foertidspension")
ORDERS = ("PublicationDate", "BestMatch", "ApplicationDate")
REGION_NAMES = {
    "hovedstaden": REGIONS[0], "hovedstadenogbornholm": REGIONS[0], "bornholm": REGIONS[0], "capitalregion": REGIONS[0],
    "midtjylland": REGIONS[1], "centraljutland": REGIONS[1],
    "syddanmark": REGIONS[2], "southerndenmark": REGIONS[2],
    "sjaelland": REGIONS[3], "sjælland": REGIONS[3], "oevrigesjaelland": REGIONS[3], "zealand": REGIONS[3],
    "nordjylland": REGIONS[4], "northjutland": REGIONS[4],
}
WHOLE_COUNTRY = ("danmark", "denmark", "dk")
ID = re.compile(r"[A-Za-z0-9_-]+")
ID_IN_PATH = re.compile(r"/(?:find-job/detaljer|find-job|FindJob/JobAdDetails|Details)/([A-Za-z0-9_-]+)(?:[/?#]|$)", re.I)


def choice(name: str, value: str | None, allowed: tuple[str, ...]) -> str | None:
    if value is not None and value not in allowed:
        raise BoardError("invalid", f"{name} must be one of {', '.join(allowed)}, not {value!r}")
    return value


def build_params(search_string: str = "", *, region: str | None = None, postal_code: str | None = None,
                 km_radius: int = DEFAULT_RADIUS_KM, work_hours: str | None = None, duration: str | None = None,
                 announcement: str | None = None, occupation_areas: str | None = None,
                 occupation_groups: str | None = None, order: str = "PublicationDate", page: int = 1,
                 per_page: int = PAGE_SIZE) -> dict:
    """The search request's parameters, refused early when they are wrong: paging and order are always sent (the API
    answers 400 without paging), a region and a postal code are never combined, BestMatch needs a search string,
    and each enumerated value must be one the API knows."""
    choice("region", region, REGIONS), choice("work hours", work_hours, WORK_HOURS), choice("duration", duration, DURATIONS)
    choice("announcement", announcement, ANNOUNCEMENTS), choice("order", order, ORDERS)
    if region and postal_code:
        raise BoardError("invalid", "a region and a postal code cannot be combined; use one")
    if postal_code is not None and not re.fullmatch(r"\d{4}", postal_code):
        raise BoardError("invalid", f"a postal code is 4 digits, not {postal_code!r}")
    if order == "BestMatch" and not search_string.strip():
        raise BoardError("invalid", "order BestMatch needs a search string")
    if page < 1 or per_page < 1 or km_radius < 1:
        raise BoardError("invalid", "page, per_page and km_radius must each be at least 1")
    params: dict = {"resultsPerPage": per_page, "pageNumber": page, "orderType": order}
    if postal_code:
        params.update(postalCode=postal_code, kmRadius=km_radius)
    for key, value in (("searchString", search_string.strip()), ("regions", region), ("workHoursType", work_hours),
                       ("employmentDurationType", duration), ("jobAnnouncementType", announcement),
                       ("occupationAreas", occupation_areas), ("occupationGroups", occupation_groups)):
        if value:
            params[key] = value
    return params


def interpret_location(location: str) -> tuple[str | None, str | None, str]:
    """A free-text location as (region, postal code, text to filter on client-side): a region name or a 4-digit postal
    code the API can filter on; the whole country filters nothing; anything else is matched against the ad's place."""
    text = " ".join(location.split())
    key = re.sub(r"[\s\-]", "", text).casefold()
    if not text or key in WHOLE_COUNTRY:
        return None, None, ""
    if key in REGION_NAMES:
        return REGION_NAMES[key], None, ""
    if re.fullmatch(r"\d{4}", text):
        return None, text, ""
    return None, None, text.casefold()


def day(value: object) -> str | None:
    """The local calendar day of an ISO timestamp, or None; the not-disclosed sentinel is no date."""
    text = value if isinstance(value, str) else ""
    return text[:10] if parse_date(text) and not text.startswith(NOT_DISCLOSED) else None


def web_url(value: object) -> str | None:
    """An employer's link only if it is an absolute http(s) URL; javascript:, file:, relative or blank are refused."""
    text = value.strip() if isinstance(value, str) else ""
    parts = urlparse(text)
    return text if parts.scheme in ("http", "https") and parts.hostname else None


def posting_url(ad_id: str, external: bool, employer_url: object) -> str:
    """An external ad links to the employer if that link is safe; every other ad to its Jobnet page."""
    return (web_url(employer_url) if external else None) or f"https://jobnet.dk/find-job/{ad_id}"


def to_text(body: object) -> str | None:
    """An ad's HTML body as plain text, keeping paragraph and list breaks."""
    if not isinstance(body, str):
        return None
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", body)
    text = re.sub(r"(?i)<\s*br\s*/?>|</\s*(p|div|h[1-6]|ul|ol|tr)\s*>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]*>", " ", text)).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() or None


def from_search_ad(ad: dict) -> dict:
    """One search hit in the contract's shape. A null date or deadline degrades that field only."""
    ad_id = str(ad.get("jobAdId") or "")
    disclosed = ad.get("applicationDeadlineStatus") != "NotDisclosed"
    return {"id": ad_id or None, "title": ad.get("title"), "company": ad.get("hiringOrgName"),
            "location": ad.get("postalDistrictName") or ad.get("municipality") or None, "date": day(ad.get("publicationDate")),
            "deadline": day(ad.get("applicationDeadline")) if disclosed else None,
            "url": posting_url(ad_id, bool(ad.get("isExternal")), ad.get("jobAdUrl")) if ad_id else None,
            "description": to_text(ad.get("description"))}


def search_ads(http: Client, params: dict) -> tuple[list[dict], int]:
    """One page of raw ads and the total hit count; a reply that is not a search result is a parse error."""
    data = http.json(f"{BASE}/FindJob/Search", params=params, headers=HEADERS)
    ads = data.get("jobAds") if isinstance(data, dict) else None
    if not isinstance(ads, list):
        raise BoardError("parse", "jobnet's search reply has no list of job ads; the API may have changed")
    total = data.get("totalJobAdCount")
    return [a for a in ads if isinstance(a, dict)], total if isinstance(total, int) else 0


def in_place(ad: dict, wanted: str) -> bool:
    return any(wanted in str(ad.get(k) or "").casefold() for k in ("postalDistrictName", "municipality", "workPlaceAddress"))


def search(query: Query, http: Client) -> list[dict]:
    """Newest first, a few pages at most, stopping once enough recent postings are found or the dates fall out of the
    recency window. A region or postal code goes to the API; any other location text is matched client-side."""
    region, postal, place = interpret_location(query.location)
    floor = None if query.recency_days is None else datetime.now(timezone.utc).date() - timedelta(days=query.recency_days)
    found: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        ads, total = search_ads(http, build_params(query.text, region=region, postal_code=postal, page=page))
        fresh = [from_search_ad(a) for a in ads if in_place(a, place)]
        found += recent(fresh, query.recency_days)
        dates = [parse_date(day(a.get("publicationDate"))) for a in ads]
        if len(found) >= query.limit or page * PAGE_SIZE >= total or len(ads) < PAGE_SIZE \
                or (floor and any(d and d < floor for d in dates)):
            break
    return found


def normalize_id(ref: str) -> str:
    """A posting id from a bare id or from a jobnet.dk (or *.jobnet.dk) posting URL; anything else is refused."""
    text = ref.strip()
    if ID.fullmatch(text):
        return text
    parts = urlparse(text)
    host = (parts.hostname or "").lower()
    found = ID_IN_PATH.search(parts.path) if parts.scheme in ("http", "https") else None
    if not found or parts.username or parts.password or not (host == "jobnet.dk" or host.endswith(".jobnet.dk")):
        raise BoardError("invalid", f"not a Jobnet posting id or jobnet.dk posting URL: {ref[:80]!r}")
    return found.group(1)


def from_detail(raw: dict, ad_id: str) -> dict:
    """A detail reply in the contract's shape. Contact persons are not copied; a not-disclosed or ASAP deadline is None."""
    if not isinstance(raw, dict) or not isinstance(raw.get("title"), str):
        raise BoardError("parse", "jobnet's detail reply is not a posting; the API may have changed")
    employer, job = raw.get("employer") or {}, raw.get("job") or {}
    application, address = raw.get("application") or {}, job.get("address") or {}
    deadline = None if application.get("isApplicationDeadlineASAP") else day(application.get("deadlineDate"))
    return {"id": str(raw.get("id") or ad_id), "title": raw["title"],
            "company": None if raw.get("isAnonymousEmployer") else employer.get("name"),
            "location": address.get("city") or address.get("municipality") or None,
            "date": day(raw.get("publicationDateTime")), "deadline": deadline,
            "url": f"https://jobnet.dk/find-job/{ad_id}", "description": to_text(raw.get("body"))}


def detail(ref: str, http: Client) -> dict:
    """One posting by id or URL. `incrementViews=false` keeps the public view counter honest. A 404 is how Jobnet
    answers for an external ad, so the posting is looked for in search by its id; no match there means it is gone."""
    ad_id = normalize_id(ref)
    try:
        return from_detail(http.json(f"{BASE}/FindJob/JobAdDetails/{ad_id}", params={"incrementViews": "false"},
                                     headers=HEADERS), ad_id)
    except BoardError as e:
        if e.kind != "not_found":
            raise
    print(f"jobnet: no detail page for {ad_id}; taking it from search (an external ad)", file=sys.stderr)
    params = build_params(ad_id, per_page=5)
    for ad in search_ads(http, params)[0]:
        if ad.get("jobAdId") == ad_id:
            return from_search_ad(ad)
    raise BoardError("not_found", f"jobnet has no posting {ad_id}; it may have been taken down")


def occupations(term: str, http: Client, limit: int = 10) -> list[dict]:
    """Occupation concepts for a term, as the API gives them (concept URIs whole, never truncated). The API ignores
    its page size, so the list is cut here."""
    if not term.strip():
        raise BoardError("invalid", "an occupation lookup needs a search term")
    data = http.json(f"{BASE}/OccupationSearch", params={"searchString": term.strip(), "pageSize": limit}, headers=HEADERS)
    if not isinstance(data, list):
        raise BoardError("parse", "jobnet's occupation reply is not a list")
    return data[:limit]


def suggestions(term: str, http: Client, limit: int = 10) -> list[str]:
    """Typeahead titles for a term. The list is tuned to Danish and may be empty, which is not an error."""
    if not term.strip():
        raise BoardError("invalid", "a typeahead lookup needs a search term")
    data = http.json(f"{BASE}/FindJob/GetTypeaheadSuggestions", params={"query": term.strip()}, headers=HEADERS)
    if not isinstance(data, list):
        raise BoardError("parse", "jobnet's typeahead reply is not a list")
    return [s for s in data if isinstance(s, str)][:limit]


if __name__ == "__main__":
    from .contract import run_cli
    raise SystemExit(run_cli(sys.modules[__name__]))

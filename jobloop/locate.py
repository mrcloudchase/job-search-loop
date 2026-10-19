"""Find the employer's own posting for a role seen on an aggregator, on an official applicant-tracking system.

Aggregator pages are scripts with no apply link, so the loop cannot follow them. The five big ATS vendors publish each
employer's open roles as public JSON, so for a company name the loop tries the likely board names, reads the listing, and
accepts a role only when **code** is satisfied: the posting's host is an official ATS (`guards.hosts.classify`), its title
matches the aggregator's, and the employer is the right one (the company's name is in the posting, or the two descriptions
share most of their distinctive words). A model is never asked and nothing it says is trusted. Reads only; no key.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from .guards import hosts
from .text import flat

TIMEOUT = 15.0
MAX_BYTES = 25_000_000
DESCRIPTION_CAP = 12_000
TITLE_MATCH = 0.8
OVERLAP_MATCH = 0.4
SUFFIXES = {"inc", "ltd", "limited", "llc", "llp", "plc", "gmbh", "ag", "bv", "nv", "sa", "srl", "spa", "pte", "pty", "co", "corp",
            "corporation", "company", "group", "holding", "holdings", "international", "the", "of", "and", "kg", "ab", "as", "oy", "se"}
NOISE = re.compile(r"\((?:[mwfdx]\s*/\s*){1,3}[mwfdx]\)|\b[mwfd]\s*/\s*[mwfd](?:\s*/\s*[mwfd])?\b", re.I)


@dataclass(frozen=True)
class Located:
    url: str
    ats: str
    title: str
    slug: str
    description: str = ""
    why: str = ""


@dataclass
class Result:
    found: Located | None = None
    tried: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", flat(text))


def slugs(company: str) -> list[str]:
    """Board names an employer is likely to use, most likely first: `Cmotions B.V.` -> cmotions, `Philip Morris International` ->
    philipmorris, philip-morris, philipmorrisinternational, philip."""
    words = _words(company.replace("&", " and ").replace(".", ""))
    core = [w for w in words if w not in SUFFIXES] or words
    found = []
    for candidate in ("".join(core), "-".join(core), "".join(words), "-".join(words), core[0] if core else ""):
        if len(candidate) >= 3 and candidate not in found:
            found.append(candidate)
    return found[:4]


def _title_words(title: str) -> set[str]:
    stripped = NOISE.sub(" ", re.sub(r"\s+[-–—|]\s+.*$", "", title))
    return {w for w in _words(stripped) if w not in {"and", "the", "of", "for", "a", "in", "at"}}


def title_score(left: str, right: str) -> float:
    a, b = _title_words(left), _title_words(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap(left: str, right: str) -> float:
    """Share of the shorter text's distinctive words (five letters or more) that the other has too."""
    a = {w for w in _words(left) if len(w) >= 5}
    b = {w for w in _words(right) if len(w) >= 5}
    if len(a) < 20 or len(b) < 20:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text or "")
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)</?(p|br|li|ul|div|h\d)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def _get(client: httpx.Client, url: str):
    try:
        response = client.get(url, headers={"accept": "application/json"})
    except httpx.HTTPError:
        return None
    if response.status_code != 200 or len(response.content) > MAX_BYTES:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _greenhouse(client, slug):
    data = _get(client, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    for job in (data or {}).get("jobs") or []:
        if isinstance(job, dict):
            url = job.get("absolute_url")                    # many employers point this at their own site; the board's own page is canonical
            if job.get("id") and hosts.classify(str(url or ""), ()).kind != "ats":
                url = f"https://boards.greenhouse.io/{slug}/jobs/{job['id']}"
            yield job.get("title"), url, strip_html(_unescape(job.get("content") or ""))


def _lever(client, slug):
    data = _get(client, f"https://api.lever.co/v0/postings/{slug}?mode=json")
    for job in data if isinstance(data, list) else []:
        if isinstance(job, dict):
            yield job.get("text"), job.get("hostedUrl"), str(job.get("descriptionPlain") or "")


def _ashby(client, slug):
    data = _get(client, f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    for job in (data or {}).get("jobs") or []:
        if isinstance(job, dict):
            yield job.get("title"), job.get("jobUrl"), str(job.get("descriptionPlain") or "")


def _workable(client, slug):
    data = _get(client, f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    for job in (data or {}).get("jobs") or []:
        if isinstance(job, dict):
            yield job.get("title"), job.get("url") or job.get("shortlink"), strip_html(str(job.get("description") or ""))


def _smartrecruiters(client, slug):
    data = _get(client, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings")
    for job in (data or {}).get("content") or []:
        if isinstance(job, dict) and job.get("id"):
            yield job.get("name"), f"https://jobs.smartrecruiters.com/{slug}/{job['id']}", ""


def _unescape(text: str) -> str:
    """Greenhouse sends its HTML entity-escaped once."""
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")


ADAPTERS = (("greenhouse", _greenhouse), ("lever", _lever), ("ashby", _ashby), ("workable", _workable),
            ("smartrecruiters", _smartrecruiters))


def find(company: str, title: str, description: str = "", *, transport: httpx.BaseTransport | None = None) -> Result:
    """The employer's own posting for this role, or a Result with `found` None and what was tried."""
    result = Result()
    words = _words(company.replace("&", " and ").replace(".", ""))
    core = [w for w in words if w not in SUFFIXES] or words
    name = " ".join(core)                                     # "Stripe, Inc." is looked for in a posting as "stripe"
    if not name or not title.strip():
        result.notes.append("no company or title to look for")
        return result
    best: tuple[float, Located] | None = None
    with httpx.Client(timeout=TIMEOUT, transport=transport, follow_redirects=False) as client:
        for slug in slugs(company):
            for vendor, adapter in ADAPTERS:
                result.tried.append(f"{vendor}/{slug}")
                for job_title, url, text in adapter(client, slug):
                    if not isinstance(job_title, str) or not isinstance(url, str):
                        continue
                    score = title_score(title, job_title)
                    if score < TITLE_MATCH or hosts.classify(url, ()).kind != "ats":
                        continue
                    shared = overlap(description, text)
                    mentions = name in " ".join(_words(text)) or "".join(core) in flat(url).replace("-", "")
                    if not (mentions or shared >= OVERLAP_MATCH):
                        result.notes.append(f"{vendor}/{slug}: '{job_title}' matches the title but not the employer")
                        continue
                    why = f"{vendor} board '{slug}': title match {score:.2f}, " + \
                          (f"the posting names {company}" if mentions else f"{shared:.0%} of its distinctive words are shared")
                    ranked = score + (0.5 if shared >= OVERLAP_MATCH else 0.0) + (0.25 if mentions else 0.0)
                    if best is None or ranked > best[0]:
                        best = (ranked, Located(url, vendor, job_title, slug, text[:DESCRIPTION_CAP], why))
            if best is not None:
                break                                        # the first board name that holds the role is the employer's
    result.found = best[1] if best else None
    return result

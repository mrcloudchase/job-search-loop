"""jobkey.py: the canonical key of a posting, and the canonical form of its URL. Both are pure functions.

What it promises:
- a key is `<company-slug>_<title-slug>`: lowercase ASCII letters, digits and hyphens around exactly one underscore, so it is
  safe as a file or folder name (no `/`, `,`, brackets) and never empty;
- the same posting gets the same key on every run: nothing in it is random, dated or machine-dependent;
- two different long titles never share a key (a capped slug ends in a hash of the full slug);
- a title or company with no Latin letters is never reduced to nothing: its identity comes from the URL's job id, or from a
  hash, so non-Latin postings from one company never collapse into one key;
- a URL is canonicalised by dropping what only tracks a visit (utm_*, click ids, fragments) and keeping what names the posting.
Nothing here reads or writes a file.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

COMPANY_WIDTH = 40
TITLE_WIDTH = 60
HASH_LENGTH = 6
UNKNOWN_COMPANY = "unknown-company"
TRACKING_PARAMS = frozenset(("gclid", "fbclid", "msclkid", "dclid", "yclid", "igshid", "mc_cid", "mc_eid", "ref", "refid",
                             "referrer", "trk", "trackingid", "tracking", "_hsenc", "_hsmi"))
_JOB_ID = re.compile(r"\d{6,}")
_CANONICAL = re.compile(r"[a-z0-9][a-z0-9-]*_[a-z0-9][a-z0-9-]*")
_LEGACY = re.compile(r"[a-z0-9][a-z0-9-]*_[a-z0-9][a-z0-9-]*_[a-z0-9][a-z0-9-]*")


def _digest(text: str, length: int = HASH_LENGTH) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:length]


def _identity(text: object) -> str:
    """Text as compared for identity: NFC, case-folded, whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFC", str(text or "")).casefold().split())


def slugify(text: object) -> str:
    """Lowercase ASCII with every run of other characters turned into one hyphen; empty for text with no Latin letters."""
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def lost_letters(text: object) -> bool:
    """True if the ASCII fold drops a letter that is not Latin (so the slug is only a fragment of the name).
    Latin letters with no ASCII form (ø, æ, ß, ł) do not count: keys that already lack them must not change."""
    for char in unicodedata.normalize("NFKD", str(text or "")):
        if char.isalpha() and not char.isascii() and "LATIN" not in unicodedata.name(char, ""):
            return True
    return False


def cap(slug: str, width: int) -> str:
    """The slug itself if it fits, else its first `width` characters plus a hash of the whole slug."""
    if len(slug) <= width:
        return slug
    return f"{slug[:width].strip('-')}-{_digest(slug)}"


def canonical_url(url: object) -> str:
    """The URL as compared for dedupe: lowercase scheme and host without `www.` or a default port, no fragment, no tracking
    parameters, remaining parameters sorted, no trailing slash. Text that is not an http(s) URL comes back stripped."""
    text = str(url or "").strip()
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return text
    host = parts.hostname.lower().removeprefix("www.")
    port = f":{parts.port}" if parts.port not in (None, 80, 443) else ""
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS)
    return urlunsplit(("https", host + port, parts.path.rstrip("/"), urlencode(query), ""))


def job_id(url: object) -> str | None:
    """The portal's numeric job id: the first run of six or more digits in the canonical URL."""
    found = _JOB_ID.search(canonical_url(url))
    return found.group() if found else None


def _company_half(company: object) -> str:
    if not _identity(company):
        return UNKNOWN_COMPANY
    slug = slugify(company)
    if slug and not lost_letters(company):
        return cap(slug, COMPANY_WIDTH)
    return f"{cap(slug, COMPANY_WIDTH)}-{_digest(_identity(company), 8)}" if slug else _digest(_identity(company), 8)


def _title_half(title: object, url: object) -> str:
    slug = slugify(title)
    if slug and not lost_letters(title):
        return cap(slug, TITLE_WIDTH)
    anchor = job_id(url) or (_digest(canonical_url(url), 8) if str(url or "").strip() else
                             _digest(_identity(title), 8) if _identity(title) else None)
    if anchor is None:
        return f"untitled-{_digest(str(url or ''))}"
    return f"{cap(slug, TITLE_WIDTH)}-{anchor}" if slug else anchor


def make_key(company: object, title: object, url: object = "") -> str:
    """The key of a posting. The URL is used only when the title cannot name it (no Latin letters, or some lost)."""
    return f"{_company_half(company)}_{_title_half(title, url)}"


def is_canonical(key: object) -> bool:
    """True for a key `make_key` could have produced in shape: two halves around one underscore, safe as a folder name."""
    return isinstance(key, str) and bool(_CANONICAL.fullmatch(key))


def is_legacy_shape(key: object) -> bool:
    """True for an older three-part key: not canonical, but harmless, so kept apart from a malformed one."""
    return isinstance(key, str) and bool(_LEGACY.fullmatch(key))


@dataclass(frozen=True)
class Audit:
    """What `audit` found. Only malformed keys and duplicate URLs are failures; legacy keys and drift are information."""
    entries: int
    malformed: tuple[str, ...]
    legacy: tuple[str, ...]
    duplicate_urls: tuple[str, ...]
    drifted: int

    @property
    def failed(self) -> bool:
        return bool(self.malformed or self.duplicate_urls)


def audit(postings: object) -> Audit:
    """Report on a store (key -> entry). It only reports: a rewritten key would orphan its archive folder and tracker row."""
    if not isinstance(postings, dict):
        raise ValueError("a store is an object of key -> entry")
    malformed, legacy, urls, duplicates, drifted = [], [], {}, [], 0
    for key, entry in postings.items():
        if not isinstance(entry, dict):
            raise ValueError(f"the entry {key!r} is not an object")
        if is_legacy_shape(key):
            legacy.append(key)
        elif not is_canonical(key):
            malformed.append(key)
        elif key != make_key(entry.get("company"), entry.get("title"), entry.get("url")):
            drifted += 1
        url = canonical_url(entry.get("url"))
        if url and url in urls:
            duplicates.append(url)
        urls[url] = key
    return Audit(len(postings), tuple(malformed), tuple(legacy), tuple(duplicates), drifted)

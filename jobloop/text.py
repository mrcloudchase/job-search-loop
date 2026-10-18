"""Text as the loop compares it: folded and flattened, and mined for the numbers and terms its checks
need. Pure functions, no I/O."""
from __future__ import annotations

import hashlib
import re
import unicodedata


def flat(text: str) -> str:
    """Ligatures and typographic marks folded, case and spacing ignored."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().replace("­", "").split())


def visible(posting: str) -> str:
    """A posting without its HTML comments: hidden text is never the posting's text."""
    return re.sub(r"<!--.*?-->", "", posting, flags=re.S)


def claimed(profile: str) -> str:
    """The profile's lines that back a claim: a line saying "not yet" backs nothing."""
    return "\n".join(l for l in profile.splitlines() if not re.search(r"\bnot yet\b", l, re.I))


def numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)*", text))


def slug(text: str, width: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()).strip("-")[:width].strip("-")


def jobkey(company: str, title: str, url: str = "") -> str:
    """One stable, filename-safe key per posting, the same on every run: a long title keeps a hash of its full
    text so two different long titles never collide, and a title with no Latin letters falls back to the url."""
    name, full = slug(company, 30), slug(title, 999)
    if not (name and full):
        return "job-" + hashlib.sha1(f"{company}|{title}|{url}".encode()).hexdigest()[:10]
    return f"{name}_{full}" if len(full) <= 50 else f"{name}_{full[:44].strip('-')}-{hashlib.sha1(full.encode()).hexdigest()[:5]}"

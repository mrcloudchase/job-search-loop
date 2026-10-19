"""Text as the loop compares it: folded and flattened, and mined for the numbers its checks need. Pure functions, no I/O."""
from __future__ import annotations

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

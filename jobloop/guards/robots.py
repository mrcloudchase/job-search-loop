"""robots.txt, RFC 9309, failing closed. `Robots(get, user_agent).allowed(url)` says whether this crawler may fetch a URL.

`get(url) -> (status, body, location)` fetches ONE URL without following redirects (boards/http.py's `Client.raw`);
this module follows up to 5 redirects itself, across hosts, as the RFC says. It promises: anything it cannot read as a
clear answer means NO. That covers a 401/403/407/408/429/451, any 5xx, a network error, too many redirects, an empty
body, a soft-200 (HTML or JSON served as robots.txt), and a rule line damaged by undecodable bytes. Only a plain
"not found"-style 4xx (404, 410 ...) is "no policy", which RFC 9309 treats as allowed. Rules: the most specific
user-agent group wins and `*` is the fallback; the longest matching rule wins and Allow wins a tie; `*` and a trailing
`$` are wildcards; percent-encoding is normalised on both sides; the query string is part of what is matched; only the
first 500 KiB is read. A policy (or a refusal) is fetched once per origin and kept for a bounded time."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit

Get = Callable[[str], tuple[int, bytes, str | None]]

MAX_REDIRECTS = 5
MAX_BYTES = 500 * 1024
DEFAULT_TTL = 24 * 3600.0
REDIRECTS = frozenset({301, 302, 303, 307, 308})
REFUSING = frozenset({401, 403, 407, 408, 429, 451})
BOM = b"\xef\xbb\xbf"
USER_AGENT_FIELDS = frozenset({"user-agent", "useragent", "user agent"})
ALLOW_FIELDS = frozenset({"allow"})
DISALLOW_FIELDS = frozenset({"disallow", "dissallow", "dissalow", "disalow", "diasllow", "disallaw"})
OTHER_FIELDS = frozenset({"sitemap", "crawl-delay", "host", "clean-param", "request-rate", "visit-time"})
RULE_FIELDS = USER_AGENT_FIELDS | ALLOW_FIELDS | DISALLOW_FIELDS
UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
KEPT = frozenset(":/?[]@!$&'()*+,;=")
CONTROL = re.compile(r"[\x00-\x20\x7f\\]")


@dataclass(frozen=True)
class Policy:
    """What one origin's robots.txt says to this crawler: ordered (allow, length, regex) rules, or closed for good."""
    rules: tuple[tuple[bool, int, re.Pattern], ...] = ()
    closed: bool = False

    def allows(self, target: str) -> bool:
        if self.closed:
            return False
        best_length, verdict = -1, True
        for allow, length, pattern in self.rules:
            if pattern.match(target) and (length > best_length or (length == best_length and allow)):
                best_length, verdict = length, allow
        return verdict


CLOSED = Policy(closed=True)


def normalise(text: str) -> str:
    """Percent-encoding in one canonical form: unreserved bytes literal, everything else %XX in upper case."""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "%" and re.fullmatch(r"[0-9A-Fa-f]{2}", text[i + 1:i + 3]):
            byte = int(text[i + 1:i + 3], 16)
            out.append(chr(byte) if chr(byte) in UNRESERVED else f"%{byte:02X}")
            i += 3
            continue
        out.append(c if c in UNRESERVED or c in KEPT else "".join(f"%{b:02X}" for b in c.encode("utf-8")))
        i += 1
    return "".join(out)


def compile_rule(pattern: str) -> tuple[int, re.Pattern]:
    """A rule path as (specificity, regex): `*` matches any run, a trailing `$` anchors, any other `$` is literal."""
    if not pattern.startswith(("/", "*")):
        pattern = "/" + pattern
    pattern = normalise(pattern)
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*")) + ("$" if anchored else "")
    return len(pattern), re.compile(regex, re.DOTALL)


def fields(text: str):
    """Yield (field, value) for each `field: value` line, comments stripped, the field lower-cased."""
    for line in re.split(r"\r\n|\r|\n", text):
        name, colon, value = line.split("#", 1)[0].partition(":")
        if colon:
            yield name.strip().lower(), value.strip()


def is_robots(text: str) -> bool:
    """Does this body look like a robots.txt? HTML, JSON and prose do not; a file of only comments does."""
    lines = [line.strip() for line in re.split(r"\r\n|\r|\n", text) if line.strip()]
    if not lines or lines[0].startswith("<"):
        return False
    if all(line.startswith("#") for line in lines):
        return True
    return any(name in RULE_FIELDS | OTHER_FIELDS for name, _ in fields(text))


def damaged(text: str) -> bool:
    """Is a User-agent, Allow or Disallow line spoiled by undecodable bytes (U+FFFD)? Elsewhere they are harmless."""
    for name, value in fields(text):
        if name.replace("\ufffd", "") in RULE_FIELDS and ("\ufffd" in name or "\ufffd" in value):
            return True
    return False


def cut(body: bytes) -> bytes:
    """The first 500 KiB, ending at a whole line: a half-read rule is never obeyed."""
    if len(body) <= MAX_BYTES:
        return body
    if body[MAX_BYTES:MAX_BYTES + 1] in (b"\n", b"\r"):
        return body[:MAX_BYTES]
    head = body[:MAX_BYTES]
    return head[:max(head.rfind(b"\n"), head.rfind(b"\r"), 0)]


def groups(text: str) -> list[tuple[list[str], list[tuple[bool, str]]]]:
    """(agent names, rules) per group in file order: consecutive User-agent lines share one group, blank lines end none."""
    found: list[tuple[list[str], list[tuple[bool, str]]]] = []
    in_rules = True
    for name, value in fields(text):
        if name in USER_AGENT_FIELDS:
            if in_rules:
                found.append(([], []))
                in_rules = False
            found[-1][0].append(re.split(r"[/\s]", value.lower(), maxsplit=1)[0])
        elif name in ALLOW_FIELDS | DISALLOW_FIELDS:
            in_rules = True
            if found and value:
                found[-1][1].append((name in ALLOW_FIELDS, value))
    return found


def parse(body: bytes, product: str) -> Policy:
    """The policy for one product token. Unreadable bodies close the origin; the rest follows RFC 9309 section 2.2."""
    while body.startswith(BOM):
        body = body[len(BOM):]
    text = cut(body).decode("utf-8", errors="replace")
    if not text.strip() or not is_robots(text) or damaged(text):
        return CLOSED
    found = groups(text)
    mine = [rules for agents, rules in found if product in agents]
    chosen = mine or [rules for agents, rules in found if "*" in agents]
    return Policy(rules=tuple((allow, *compile_rule(path)) for rules in chosen for allow, path in rules))


def origin_of(url: str) -> tuple[str, str] | None:
    """(origin, target) for an http(s) URL, else None: origin is `scheme://host[:port]`, target is path plus query."""
    if not isinstance(url, str) or CONTROL.search(url):
        return None
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
        host = host.encode("idna").decode("ascii") if host else None
    except (ValueError, UnicodeError):
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or "@" in parts.netloc:
        return None
    origin = f"{parts.scheme.lower()}://{host}" + (f":{port}" if port else "")
    return origin, (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


class Robots:
    """Answers `allowed(url)` for one crawler, fetching each origin's robots.txt once and failing closed."""

    def __init__(self, get: Get, user_agent: str, *, ttl: float = DEFAULT_TTL, clock: Callable[[], float] = time.monotonic):
        token = re.match(r"[A-Za-z0-9_-]+", user_agent.strip())
        if token is None:
            raise ValueError(f"user agent {user_agent!r} has no product token to match robots.txt groups against")
        self.get, self.product, self.ttl, self.clock = get, token.group().lower(), ttl, clock
        self._cache: dict[str, tuple[float, Policy]] = {}

    def allowed(self, url: str) -> bool:
        found = origin_of(url)
        if found is None:
            return False
        origin, target = found
        cached = self._cache.get(origin)
        if cached is None or self.clock() - cached[0] > self.ttl:
            cached = self._cache[origin] = (self.clock(), self._fetch(origin))
        return cached[1].allows(normalise(target))

    def _fetch(self, origin: str) -> Policy:
        url = f"{origin}/robots.txt"
        for _ in range(MAX_REDIRECTS + 1):
            try:
                status, body, location = self.get(url)
            except Exception:
                return CLOSED
            if status in REDIRECTS:
                if not location or CONTROL.search(location):
                    return CLOSED
                url = urljoin(url, location)
                if urlsplit(url).scheme not in ("http", "https"):
                    return CLOSED
                continue
            if 200 <= status < 300:
                return parse(body, self.product)
            if 400 <= status < 500 and status not in REFUSING:
                return Policy()
            return CLOSED
        return CLOSED

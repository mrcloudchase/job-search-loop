"""Where did this posting come from? `classify(url, installed_hosts)` puts a URL's host in exactly one tier: `portal` (an
installed job board's host or a true subdomain of it), `ats` (an official applicant-tracking system's apex or a true
subdomain of it), or `unverified`. It fails closed: a look-alike prefix, a suffix trick, userinfo, a trailing dot, a port
other than the scheme's own, an IDN or punycode name, a scheme other than http(s), whitespace, control characters, a
backslash or a missing host are all `unverified`. Pure: no lookups, no network."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Seven literals for six vendors: the spec says "six" and lists Workday twice, as myworkdayjobs.com "(or workday.com)".
ATS_APEXES = ("greenhouse.io", "lever.co", "myworkdayjobs.com", "workday.com", "ashbyhq.com", "smartrecruiters.com",
              "workable.com")
KINDS = ("portal", "ats", "unverified")
FORBIDDEN = re.compile(r"[\x00-\x20\x7f-\x9f\\]")
LABEL = re.compile(r"(?!xn--)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
DEFAULT_PORTS = {"http": "80", "https": "443"}


@dataclass(frozen=True)
class HostVerdict:
    kind: str
    host: str
    apex_or_none: str | None
    reason: str

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, not {self.kind!r}")
        if (self.kind == "unverified") != (self.apex_or_none is None):
            raise ValueError("a verified host names the apex it matched; an unverified one names none")


def registrable(name: str) -> str | None:
    """A plain lower-case ASCII DNS name (letters, digits, hyphens, at least two labels), else None."""
    labels = name.split(".")
    if len(labels) < 2 or len(name) > 253 or not all(LABEL.fullmatch(label) for label in labels):
        return None
    return None if labels[-1].isdigit() else name


def installed_set(installed_hosts: Iterable[str]) -> tuple[str, ...]:
    """The installed boards' hosts, lower-cased and without a leading `www.`. Refuses a name that is not a plain host."""
    names = []
    for raw in installed_hosts:
        name = raw.strip().lower().removeprefix("www.")
        if registrable(name) is None:
            raise ValueError(f"installed portal host {raw!r} is not a plain DNS name such as 'jobindex.dk'")
        names.append(name)
    return tuple(names)


def split_url(url: str) -> tuple[str, str]:
    """(host, why-not) for an http(s) URL: the host when safe, else "" and the reason it is not."""
    if not isinstance(url, str) or not url:
        return "", "no URL"
    if FORBIDDEN.search(url) or any(c.isspace() for c in url):
        return "", "the URL has whitespace, a control character or a backslash"
    scheme, sep, rest = url.partition("://")
    scheme = scheme.lower()
    if not sep or scheme not in DEFAULT_PORTS:
        return "", "the URL is not http or https"
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
    host, _, port = authority.rpartition("@")[2].partition(":")
    host = host.lower()
    if "@" in authority:
        return host, f"the URL carries userinfo; its real host is {host or 'missing'}"
    if not host:
        return "", "the URL has no host"
    if host.endswith("."):
        return host, "the host ends with a dot"
    if registrable(host) is None:
        return host, "the host is not a plain DNS name (an IP address, IDN or punycode name, or malformed)"
    if ":" in authority and port != DEFAULT_PORTS[scheme]:
        return host, f"the port {port or '(empty)'} is not the default for {scheme}"
    return host, ""


def classify(url: str, installed_hosts: Iterable[str]) -> HostVerdict:
    """Which tier is this URL's host in? Installed portal first, then the official ATS apexes, else unverified."""
    portals = installed_set(installed_hosts)
    host, why_not = split_url(url)
    if why_not:
        return HostVerdict("unverified", host, None, why_not)
    for kind, names in (("portal", portals), ("ats", ATS_APEXES)):
        for name in names:
            if host == name or host.endswith("." + name):
                return HostVerdict(kind, host, name, f"{host} is {name}" if host == name else f"{host} is under {name}")
    return HostVerdict("unverified", host, None, f"{host} is not an installed portal or an official ATS")

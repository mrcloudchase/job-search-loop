"""The HTTP client every board adapter shares. It is the only way an adapter touches the network, so the rules live here
once: an honest User-Agent; only https, only the adapter's declared hosts (every redirect hop too); a time limit and a
size limit; backoff on 429 and 5xx that honours a bounded Retry-After; and, before any HTML page is scraped, a robots.txt
check that fails closed. A failure is a `BoardError` whose kind tells a blocked or rate-limited board from a broken one."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx

from .contract import BoardError

USER_AGENT = "job-search-loop/0.1 (+https://github.com/mrcloudchase/job-search-loop; personal-use job search)"
RETRY_AFTER_MAX = 30.0
CHALLENGE = re.compile(r"just a moment|attention required|enable javascript and cookies|captcha|cf-chl|access denied", re.I)


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes
    headers: httpx.Headers


class Client:
    """One adapter's view of the network. Use it as a context manager."""

    def __init__(self, board: str, hosts: tuple[str, ...] | list[str], *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, robots: Callable[[str], bool] | None = None,
                 timeout: float = 20.0, max_bytes: int = 3_000_000, retries: int = 3, user_agent: str = USER_AGENT):
        self.board, self.hosts, self.sleep, self.retries = board, tuple(h.lower() for h in hosts), sleep, retries
        self.max_bytes, self.user_agent, self._robots, self._gate = max_bytes, user_agent, robots, None
        self.http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False,
                                 headers={"User-Agent": user_agent, "Accept-Language": "en;q=0.8,da;q=0.5"})

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc) -> None:
        self.http.close()

    def _check(self, url: str) -> None:
        parts = urlparse(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in self.hosts or parts.username or parts.password:
            raise BoardError("invalid", f"{self.board} may only use https on {', '.join(self.hosts)}, not {url[:80]}")

    def _once(self, url: str, params: dict | None, headers: dict | None) -> Reply:
        """One request, redirects not followed, the body size-limited."""
        self._check(url)
        try:
            with self.http.stream("GET", url, params=params, headers=headers) as r:
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > self.max_bytes:
                        raise BoardError("parse", f"{self.board} sent more than {self.max_bytes} bytes")
                return Reply(r.status_code, bytes(body), r.headers)
        except httpx.TimeoutException:
            raise BoardError("network", f"{self.board} did not answer in time") from None
        except httpx.HTTPError as e:
            raise BoardError("network", f"{self.board}: {type(e).__name__}: {e}") from None

    def raw(self, url: str) -> tuple[int, bytes, str | None]:
        """A fetch with no robots gate and no redirect-following, for reading robots.txt itself."""
        reply = self._once(url, None, None)
        return reply.status, reply.body, reply.headers.get("location")

    def allowed(self, url: str) -> bool:
        """May this page be scraped? Fails closed: if robots.txt can't be read as a clear answer, the answer is no."""
        if self._robots is not None:
            return self._robots(url)
        if self._gate is None:
            from ..guards.robots import Robots
            self._gate = Robots(self.raw, self.user_agent)
        return self._gate.allowed(url)

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None, html: bool = False) -> httpx.Response:
        """A GET with backoff. `html=True` marks a page that is scraped rather than an API, so robots.txt is checked first."""
        if html and not self.allowed(str(httpx.URL(url, params=params)) if params else url):
            raise BoardError("robots", f"robots.txt does not allow {self.board} to fetch {url[:100]}")
        for attempt in range(self.retries + 1):
            reply = self._follow(url, params, headers)
            if reply.status != 429 and reply.status < 500:
                break
            if attempt == self.retries:
                raise BoardError("rate_limited" if reply.status == 429 else "network",
                                 f"{self.board} answered {reply.status} after {attempt + 1} tries")
            self.sleep(self._delay(attempt, reply.headers.get("retry-after")))
        ctype = reply.headers.get("content-type", "")
        if reply.status in (401, 403) or (reply.status == 200 and "html" in ctype and CHALLENGE.search(reply.body[:3000].decode("utf-8", "replace"))):
            raise BoardError("blocked", f"{self.board} refused the request ({reply.status}); it is not retried or worked around")
        if reply.status in (404, 410):
            raise BoardError("not_found", f"{self.board} has no such page ({reply.status})")
        if reply.status >= 400:
            raise BoardError("invalid", f"{self.board} answered {reply.status}")
        return httpx.Response(reply.status, content=reply.body, headers={"content-type": ctype}, request=httpx.Request("GET", url))

    def _follow(self, url: str, params: dict | None, headers: dict | None) -> Reply:
        """One attempt, following up to 5 redirects, each of which must stay on the declared hosts."""
        for _ in range(6):
            reply = self._once(url, params, headers)
            location = reply.headers.get("location")
            if reply.status in (301, 302, 303, 307, 308) and location:
                url, params = urljoin(url, location), None
                continue
            return reply
        raise BoardError("network", f"{self.board} redirected too many times")

    @staticmethod
    def _delay(attempt: int, retry_after: str | None) -> float:
        try:
            return min(float(retry_after), RETRY_AFTER_MAX) if retry_after else 2.0 ** attempt
        except ValueError:
            return 2.0 ** attempt

    def json(self, url: str, **kw) -> object:
        try:
            return json.loads(self.get(url, **kw).content)
        except ValueError:
            raise BoardError("parse", f"{self.board} did not send JSON") from None

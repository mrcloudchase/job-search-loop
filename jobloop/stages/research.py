"""The research stage: verified company research for the shortlisted postings the draft stage will take next.

`propose` offers shortlisted postings with no `me/research/<application_key>.json`, best fit first, at most
`2 * max_new_drafts` of them, so the draft stage always has researched candidates waiting.

`run` decides the employer's own domain by code (`company_domains` in loop.json, else the posting's URL host when
`hosts.classify` finds it neither a board nor an ATS and it carries the company's name), then calls `research.research`
through the gateway's turns on the researcher-role harness. The hosts come from `research.employer_hosts`; the sandbox is
widened for exactly that turn by the library and reset after it, error or not; every claim is re-fetched host-side and kept
only if its quote is on the page (robots checked, exact hosts). The 30-day cache is a lead and is re-verified.
The harness is given the company name, the hosts and a short posting summary (hidden HTML comments stripped); the profile is
never read here, so it cannot be sent. With no known domain no model is called: the record says why.

The record, written atomically (also when nothing verified or research was skipped):
`{"company", "domain", "claims": [{"claim","url","quote"}], "dropped": n, "skipped": "<reason or empty>", "date"}`.
Terminals: success (record written), blocked (the posting vanished), stalled (no valid answer), exhausted (stop).
The engine is serial, so two researches never widen the shared sandbox at once.
"""
from __future__ import annotations

import json

from .. import archive, research as lib
from ..engine import policy
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context
from ..fetch import installed_hosts
from ..guards import hosts as host_check

NO_DOMAIN = "no employer domain known (set company_domains in loop.json)"
NOTHING = "nothing verifiable"
TRANSPORT = None                    # tests inject an httpx transport and a robots callable for the host-side fetches
ROBOTS = None


def record_path(me, key: str):
    return me / "research" / f"{key}.json"


def employer_domain(entry: dict, company_domains: dict, installed: tuple[str, ...] | None = None) -> str:
    """The employer's own domain, or "": the configured one (case-insensitive), else the posting URL's host when it is
    neither a job board nor an ATS and carries the company's name."""
    company = str(entry.get("company") or "")
    for name, domain in (company_domains or {}).items():
        if str(name).strip().casefold() == company.strip().casefold() and str(domain).strip():
            return str(domain).strip().lower()
    url = str(entry.get("url") or "")
    installed = installed_hosts() if installed is None else installed
    verdict = host_check.classify(url, installed)
    return host_check.employer_host(url, company) if verdict.kind == "unverified" else ""


def posting_text(entry: dict) -> str:
    """A short summary of the posting for the harness: the posting's own fields, never the profile."""
    lines = [f"Title: {entry.get('title') or ''}", f"Company: {entry.get('company') or ''}",
             f"Locations: {', '.join(map(str, entry.get('locations') or []))}"]
    for field in ("description", "summary"):
        if entry.get(field):
            lines.append(str(entry[field]))
    return "\n".join(lines)


def make_fetcher(hosts):
    return lib.Fetcher(hosts, transport=TRANSPORT, robots=ROBOTS)


def _write(ctx: Context, key: str, company: str, domain: str, claims, dropped: int, skipped: str) -> None:
    data = {"company": company, "domain": domain, "claims": [c.as_dict() for c in claims], "dropped": dropped,
            "skipped": skipped, "date": ctx.now.date().isoformat()}
    path = record_path(ctx.me, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    archive.write_atomically(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())


class Research:
    spec = Spec("research", "verified company research for the shortlisted postings the draft stage will take",
                trigger=Trigger("state"), shape="solo", levels=(1,), roles=("researcher",), rank=30, brakes=Brakes(max_attempts=3))

    def propose(self, snap) -> list[Action]:
        found = []
        for key in snap.keys_in("shortlisted"):
            app = snap.application_key(snap.postings[key])
            if not app or record_path(snap.me, app).exists():
                continue
            fit = snap.fit(key) or {}
            score = fit.get("total", snap.postings[key].get("rank_score", 0))
            score = score if isinstance(score, (int, float)) else 0
            found.append((score, app, key))
        actions = [Action("research", app, f"shortlisted, fit {score}, not yet researched", priority=float(score), payload={"posting_key": key},
                          deadline=str(snap.postings[key].get("deadline") or "") or None) for score, app, key in found]
        actions.sort(key=lambda a: (policy.urgency(a, snap.today), -a.priority, a.key))        # the postings the draft stage will take first (urgent, then best)
        return actions[:2 * snap.config.max_new_drafts]

    def _entry(self, action: Action, ctx: Context) -> dict | None:
        snap = ctx.snapshot
        entry = snap.postings.get(action.payload.get("posting_key", ""))
        if entry is not None and snap.application_key(entry) == action.key:
            return entry
        return next((e for e in snap.postings.values() if snap.application_key(e) == action.key), None)

    def run(self, action: Action, ctx: Context) -> Outcome:
        entry = self._entry(action, ctx)
        if entry is None:
            return Outcome("blocked", failures=[f"the posting for {action.key} is no longer in the store"])
        company = str(entry.get("company") or "")
        domain = employer_domain(entry, ctx.config.company_domains)
        if not domain:
            _write(ctx, action.key, company, "", (), 0, NO_DOMAIN)
            return Outcome("success", notes=[f"{company}: {NO_DOMAIN}"])
        try:
            hosts = lib.employer_hosts(domain, str(entry.get("url") or ""))
        except ValueError as error:
            _write(ctx, action.key, company, "", (), 0, f"{domain}: {error}")
            return Outcome("success", notes=[f"{company}: domain refused ({error})"])
        model = ctx.gateway.harness("researcher", "research", action.key)
        with ctx.gateway.turns("research", action.key) as turns, make_fetcher(hosts) as fetcher:
            result = lib.research(turns, model, company, hosts, "company", posting_text(entry), fetch=fetcher,
                                  cache_dir=ctx.me / "cache" / "research", today=ctx.now.date())
            stopped = turns.stopped()
        produced = {"harness": model}
        if not result.claims and not result.dropped and any("no research turn completed" in n for n in result.notes):
            if stopped:
                return Outcome("exhausted", notes=[f"research stopped: {stopped}"], produced=produced)
            return Outcome("stalled", failures=list(result.notes), produced=produced)
        skipped = "" if result.claims else NOTHING
        _write(ctx, action.key, company, domain, result.claims, len(result.dropped), skipped)
        return Outcome("success", notes=result.lines()[:1] + list(result.notes), produced=produced)


STAGE = Research()

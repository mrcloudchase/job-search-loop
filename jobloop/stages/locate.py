"""The locate stage: find the employer's own posting for a shortlisted role that was seen on a host the loop does not trust.

A posting from an aggregator has no link a person can apply at, and the draft stage will not work from an unverified host.
`propose` offers shortlisted postings whose host is unverified (and not already trusted by the employer's-own-site rule,
`trusted_hosts` or the person's approval) with no record, best fit first, at most `2 * max_new_drafts`. `run` calls
`locate.find` (public ATS listings, no model) and writes `me/locate/<application_key>.json`:
`{"found": bool, "url", "ats", "title", "description", "why", "tried": [...], "date"}`. The draft stage then drafts from the
located posting and the queue's apply link is that employer-hosted page. A role with no ATS listing gets `found: false` and
stays a `needs_decision` for the person (`jobloop queue approve`). Nothing here is trusted from a model or from the aggregator.
Terminals: success (record written, found or not).
"""
from __future__ import annotations

import json

from .. import archive, locate as lib
from ..engine import policy
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context
from ..fetch import installed_hosts
from ..guards import hosts as host_check

TRANSPORT = None                    # tests inject an httpx transport


def record_path(me, key: str):
    return me / "locate" / f"{key}.json"


def located(me, key: str) -> dict | None:
    """The stored record when it found a role on an ATS host (re-checked here: the host must still classify as an ATS)."""
    try:
        data = json.loads(record_path(me, key).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("found") or host_check.classify(str(data.get("url") or ""), ()).kind != "ats":
        return None
    return data


def needs_locating(snap, entry: dict, app: str) -> bool:
    url = str(entry.get("url") or "")
    if host_check.classify(url, installed_hosts()).kind != "unverified":
        return False
    kind, _ = host_check.trust(url, str(entry.get("company") or ""), snap.config.company_domains, snap.config.trusted_hosts, False)
    return not kind and not snap.store.events("human:approve_host", app, limit=1)


class Locate:
    spec = Spec("locate", "an employer-hosted posting for each shortlisted role seen only on an aggregator",
                trigger=Trigger("state"), shape="solo", levels=(1,), roles=(), rank=25, brakes=Brakes(max_attempts=2))

    def propose(self, snap) -> list[Action]:
        found = []
        for key in snap.keys_in("shortlisted"):
            entry = snap.postings[key]
            app = snap.application_key(entry)
            if not app or record_path(snap.me, app).exists() or not needs_locating(snap, entry, app):
                continue
            fit = snap.fit(key) or {}
            score = fit.get("total", entry.get("rank_score", 0))
            score = score if isinstance(score, (int, float)) else 0
            found.append(Action("locate", app, f"shortlisted, fit {score}, only on {entry.get('url')}", priority=float(score),
                                payload={"posting_key": key}, deadline=str(entry.get("deadline") or "") or None))
        found.sort(key=lambda a: (policy.urgency(a, snap.today), -a.priority, a.key))
        return found[:2 * snap.config.max_new_drafts]

    def run(self, action: Action, ctx: Context) -> Outcome:
        snap = ctx.snapshot
        entry = snap.postings.get(action.payload.get("posting_key", ""))
        if entry is None or snap.application_key(entry) != action.key:
            entry = next((e for e in snap.postings.values() if snap.application_key(e) == action.key), None)
        if entry is None:
            return Outcome("blocked", failures=[f"the posting for {action.key} is no longer in the store"])
        result = lib.find(str(entry.get("company") or ""), str(entry.get("title") or ""), str(entry.get("description") or ""),
                          transport=TRANSPORT)
        data = {"found": result.found is not None, "tried": result.tried, "notes": result.notes, "date": ctx.now.date().isoformat()}
        if result.found:
            data.update(url=result.found.url, ats=result.found.ats, title=result.found.title, description=result.found.description,
                        why=result.found.why)
        path = record_path(ctx.me, action.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        archive.write_atomically(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())
        if result.found:
            return Outcome("success", notes=[f"{entry.get('company')}: found on {result.found.ats}: {result.found.url} ({result.found.why})"])
        return Outcome("success", notes=[f"{entry.get('company')}: no listing on a known ATS (tried {len(result.tried)} boards); stays with the person"])


STAGE = Locate()

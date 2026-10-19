"""Stage `onboard`: no profile -> ask for one; with a profile -> public-source enrichment as proposals.

Without `me/profile.md` the stage's one action is blocked ("no profile: run `jobloop setup`") and writes a `needs_decision`
queue entry; the policy lets no other stage run until a profile exists. With a profile, every `enrich_every_hours` it reads the
pages the person named (`enrich_github` / `enrich_site` in loop.json, else the GitHub and site links in the profile's Contact)
through a researcher harness that never sees the profile, and turns every code-verified fact (its quote is on the fetched page,
the page is the person's own) into a `profile_enrichment` proposal: {summary, section, text, source_url, quote}.

Never: writes profile.md (only `engine.proposals.accept` does, for a person's yes), prompts, follows links off the named pages,
or proposes a fact the profile already says or the person already decided on. Nothing here calls `input()`.
"""
from __future__ import annotations

import json
import re

from .. import enrich, profile
from ..engine.proposals import ADDITIONS
from ..engine.snapshot import Snapshot
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context, due
from ..text import flat

SETUP = "__setup__"
ENRICH = "enrich"
NO_PROFILE = "no profile: run `jobloop setup`"
KIND = "profile_enrichment"
GITHUB_LINK = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]+)(?![\w/-])", re.I)
SITE_LINK = re.compile(r"(?:https://)?((?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?)", re.I)
NOT_A_SITE = ("github.com", "linkedin.com", "gitlab.com", "twitter.com", "x.com", "example.com")


def links_of(md: str) -> str:
    """The Contact section's `- Links:` value, or ''."""
    try:
        return profile.fields(profile.sections(md).get("Contact", "")).get("links", "")
    except ValueError:
        return ""


def named_sources(config, md: str) -> enrich.Sources | None:
    """What the person named: loop.json first, else the GitHub and site links in the profile. None if there is nothing readable."""
    github, site = config.enrich_github.strip(), config.enrich_site.strip()
    if not github and not site:
        links = links_of(md)
        user = GITHUB_LINK.search(links)
        github = f"https://github.com/{user.group(1)}" if user else ""
        rest = GITHUB_LINK.sub(" ", links)
        for m in SITE_LINK.finditer(rest):
            if not m.group(1).lower().startswith(NOT_A_SITE):
                site = "https://" + m.group(1)
                break
    for pair in ((github, site), (github, ""), ("", site)):      # a bad half must not stop the good half
        try:
            return enrich.sources(pair[0] or None, pair[1] or None)
        except ValueError:
            continue
    return None


class Onboard:
    spec = Spec("onboard", "a profile exists, and what the person's public pages add to it waits as proposals they decide",
                trigger=Trigger("state"), shape="solo", levels=(1, 5), roles=("researcher",), rank=5,
                brakes=Brakes(max_attempts=3), human_gate=True)

    def propose(self, snap: Snapshot) -> list[Action]:
        if not snap.profile_ready:
            return [Action("onboard", SETUP, NO_PROFILE, priority=10)]
        out = []
        waiting = snap.store.queue_item(SETUP)
        if waiting and waiting["status"] != "done":
            out.append(Action("onboard", SETUP, "the profile exists now: close the setup request", priority=10))
        if named_sources(snap.config, snap.profile) and due(snap, "onboard", snap.config.enrich_every_hours, ENRICH):
            out.append(Action("onboard", ENRICH, "read the pages you named for profile additions", priority=1))
        return out

    def run(self, action: Action, ctx: Context) -> Outcome:
        if action.key == SETUP:
            return self.setup(ctx)
        return self.enrich(ctx)

    def setup(self, ctx: Context) -> Outcome:
        if ctx.snapshot.profile_ready:
            ctx.store.queue_put(SETUP, "done", "me/profile.md", {"title": "Build your profile"}, ctx.now)
            return Outcome("success", notes=["profile found; setup request closed"])
        ctx.store.queue_put(SETUP, "needs_decision", "me/profile.md", {
            "title": "Build your profile",
            "why": "The loop cannot search, rank or draft without your profile. Run `jobloop setup`; it asks you, and you confirm what is written."},
            ctx.now)
        return Outcome("blocked", failures=[NO_PROFILE])

    def enrich(self, ctx: Context) -> Outcome:
        snap = ctx.snapshot
        named = named_sources(ctx.config, snap.profile)
        if named is None:
            return Outcome("no_op", notes=["no GitHub or site named (enrich_github / enrich_site, or Links in the profile)"])
        model = ctx.gateway.harness("researcher", "onboard", ENRICH)
        with ctx.gateway.turns("onboard", ENRICH) as turns:
            found = enrich.collect(turns, model, named, fetch=_fetcher(named))
            stopped = turns.stopped()
        produced = {"harness": model}
        if found is None:
            if stopped:
                return Outcome("exhausted", notes=[f"stopped: {stopped}"], produced=produced)
            return Outcome("stalled", failures=["no research turn completed (time, budget or format)"], produced=produced)
        kept, dropped = found
        added = self.propose_new(ctx, kept, snap.profile)
        notes = [f"{len(added)} proposed, {len(kept) - len(added)} already known or decided, {len(dropped)} dropped as unverified"]
        return Outcome("success", notes=notes + [f"proposed: {t}" for t in added], produced=produced)

    def propose_new(self, ctx: Context, kept, profile_text: str) -> list[str]:
        seen = {flat(_text(r)) for r in ctx.store.proposals() if r["kind"] == KIND}
        known = flat(profile_text)
        added = []
        for claim in kept:
            if flat(claim.claim) in known or flat(claim.claim) in seen:
                continue
            ctx.store.add_proposal(KIND, {"summary": f"Add to your profile: {claim.claim}", "section": ADDITIONS, "text": claim.claim,
                                          "source_url": claim.url, "quote": claim.quote},
                                   f"public page {claim.url}", ctx.now)
            seen.add(flat(claim.claim))
            added.append(claim.claim)
        return added


def _text(row) -> str:
    try:
        return str(json.loads(row["payload"]).get("text", ""))
    except (TypeError, ValueError, AttributeError):
        return ""


def _fetcher(named: enrich.Sources):
    """The host-side page fetch that verifies quotes (tests replace this); None lets `collect` open the real one."""
    return FETCH(named) if FETCH else None


FETCH = None

STAGE = Onboard()

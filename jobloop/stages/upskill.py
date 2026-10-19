"""The upskill stage: a weekly skills-gap report with study resources the loop itself fetched.

`propose` offers one action (`*`) when the cadence is due (`upskill_every_hours`, default 168) and a profile exists.
`run` calls `upskill.run` (the library: gaps weighted by fit, heatmap, diff against the last report, a plan the code checks)
on the gateway's turns, with `webresearch.WebResearch` as the researcher: that harness is sent one gap NAME (and the year) and
no file, never the profile, and every lead is fetched host-side; a resource is listed only if its page was fetched now.
The infer/plan turns use the planner-role harness, the web leads the researcher-role harness.

Terminals: `no_op` when no tracked or ranked application gives a gap to learn from (nothing is written); `success` after the
dated report `me/upskill/report-YYYY-MM-DD.md` is saved and a `ready` queue entry `upskill:<date>` (title "Skills-gap
report") points at it; `exhausted` when the budget or deadline stopped the model turns (the report as far as it got is saved,
no queue entry, the next pass tries again); `blocked` with no profile. Never prompts, never writes the tracker or profile.
"""
from __future__ import annotations

from .. import upskill as lib, webresearch
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context, due

WEB: dict = {}                      # tests inject transport=, resolve= and robots= for the host-side fetches


class Upskill:
    spec = Spec("upskill", "a skills-gap report from tracked and ranked applications, with study resources that were fetched",
                trigger=Trigger("every", 168.0), shape="solo", levels=(1, 2), roles=("researcher", "planner"), rank=15,
                brakes=Brakes(max_attempts=2))

    def propose(self, snap) -> list[Action]:
        if not snap.profile_ready or not due(snap, "upskill", snap.config.upskill_every_hours):
            return []
        return [Action("upskill", "*", f"no skills-gap report in {snap.config.upskill_every_hours:g} hours")]

    def run(self, action: Action, ctx: Context) -> Outcome:
        if not ctx.snapshot.profile_ready:
            return Outcome("blocked", failures=["no profile.md to compare the gaps with (run `jobloop setup`)"])
        jobs = lib.load_jobs(ctx.me)
        if not jobs.jobs and not jobs.needs_inference:
            return Outcome("no_op", notes=["no tracked or ranked applications to learn gaps from"])
        today = ctx.now.date()
        planner = ctx.gateway.harness("planner", "upskill", "*")
        researcher = ctx.gateway.harness("researcher", "upskill", "*")
        with ctx.gateway.turns("upskill", "*") as turns, webresearch.WebResearch(turns, researcher, today=today, **WEB) as web:
            if turns.stopped():
                return Outcome("exhausted", notes=[f"upskill not started: {turns.stopped()}"])
            text, gaps = lib.run(ctx.me, planner, turns, today, web.resources, web.page)
            stopped = turns.stopped()
        path = lib.save(ctx.me, today, text)
        produced = {"harness": planner, "researcher": researcher, "report": str(path)}
        if stopped:
            return Outcome("exhausted", notes=[f"report saved as far as it got ({stopped}): {path}"], produced=produced)
        ctx.store.queue_put(f"upskill:{today.isoformat()}", "ready", str(path), {
            "title": "Skills-gap report", "why": f"{len(gaps)} gaps from {len(jobs.jobs)} tracked or ranked applications; read the plan in {path.name}",
            "deadline": ""}, ctx.now)
        ctx.say(f"upskill: saved {path} ({len(gaps)} gaps)")
        return Outcome("success", notes=[f"{len(gaps)} gaps, report {path}"], produced=produced)


STAGE = Upskill()

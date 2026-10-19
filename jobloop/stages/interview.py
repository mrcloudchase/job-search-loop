"""The interview stage: a prep pack for the stage an application is at, when the PERSON has set its status to `interview`.

`propose` offers a tracker row whose status is `interview` (nothing else: not `drafted`, `applied`, `offer`, `withdrawn`...)
that has no pack yet for its current stage. The current stage is the latest stage the person logged in the archive's
`outcome.md` whose outcome is still `pending`; with none logged (or all settled) it is a generic `next interview` pack.

`run` builds the pack with `interview.build_pack` on a planner harness, through the gateway's turns. Stage details (date,
format, interviewers) come from the logged stage; a generic pack says so in a note. Company research goes through
`webresearch.WebResearch` on a researcher harness: it is sent a company name (and a domain from `company_domains` in
loop.json, if one is set) and NEVER the profile, a posting or a file; claims count only when the loop's own fetch of the
page finds the quote. Interviewer research happens only for names the person logged, and only public professional
claims are kept (the library refuses the rest). The pack is saved in the application's folder as the library does
(`interview_prep_<stage>.md`, never over an earlier one) and a `ready` queue entry `interview:<key>:<stage>` points at it.
Mock interviews stay a person-run, interactive command: the pack's notes say to run `jobloop mock <key>` yourself.
The stage prompts for nothing, writes no tracker status and never sends anything.

Terminals: success, no_op (the status moved on or a pack exists), blocked (no profile), stalled (no pack passed),
exhausted (the stop signal).
"""
from __future__ import annotations


from .. import archive, interview as lib, tracker, webresearch
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context
from ..text import slug

GENERIC = "next interview"
TRANSPORT = None                    # tests inject an httpx transport, a resolver and a robots callable for the host-side fetches
RESOLVE = None
ROBOTS = None


def current_stage(me, key: str) -> archive.Stage | None:
    """The latest logged stage that is still pending, or None (then the pack is the generic next interview)."""
    outcome = archive.read_outcome(me, key)
    pending = [s for s in (outcome.stages if outcome else ()) if s.outcome == "pending"]
    return pending[-1] if pending else None


def stage_name(stage: archive.Stage | None) -> str:
    return stage.stage if stage else GENERIC


def pack_exists(me, key: str, name: str) -> bool:
    folder = archive.folder_for(me, key)
    stem = f"interview_prep_{slug(name, 40) or 'stage'}"
    return folder.is_dir() and any(p.name == f"{stem}.md" or p.name.startswith(f"{stem}-") for p in folder.glob(f"{stem}*.md"))


def item(key: str, name: str) -> str:
    return f"interview:{key}:{slug(name, 40) or 'stage'}"


def domain_for(company: str, domains: dict) -> str:
    for name, domain in (domains or {}).items():
        if str(name).strip().casefold() == company.strip().casefold() and str(domain).strip():
            return str(domain).strip().lower()
    return ""


class Interview:
    spec = Spec("interview", "an interview prep pack for the stage the person is at, every claim on the paper",
                trigger=Trigger("state"), shape="solo", levels=(1, 2), roles=("researcher", "planner"), rank=85,
                brakes=Brakes(max_attempts=3))

    def propose(self, snap) -> list[Action]:
        found = []
        for row in snap.rows:
            if not row.key or tracker.normalize_status(row.status) != "interview":
                continue
            name = stage_name(current_stage(snap.me, row.key))
            if not pack_exists(snap.me, row.key, name):
                found.append(Action("interview", row.key, f"status is interview; no pack for {name}", priority=1.0, payload={"stage": name}))
        return found

    def _fit(self, ctx: Context, key: str) -> dict | None:
        snap = ctx.snapshot
        entry = next((e for e in snap.postings.values() if snap.application_key(e) == key), None)
        return snap.fit(entry["key"]) if entry and entry.get("key") else None

    def run(self, action: Action, ctx: Context) -> Outcome:
        row = ctx.snapshot.row_by_key.get(action.key)
        if row is None or tracker.normalize_status(row.status) != "interview":
            return Outcome("no_op", notes=[f"{action.key} is not at interview in the tracker (a person sets that)"])
        logged = current_stage(ctx.me, action.key)
        name = stage_name(logged)
        if pack_exists(ctx.me, action.key, name):
            return Outcome("no_op", notes=[f"a pack for {name} already exists"])
        if not ctx.snapshot.profile_ready:
            ctx.store.queue_put(f"interview:{action.key}:profile", "needs_decision", "", {
                "title": f"Interview prep: {row.company}, {row.role}", "deadline": "",
                "why": "there is no profile to prepare answers from: run `jobloop setup`"}, ctx.now)
            return Outcome("blocked", failures=["no profile.md: an interview pack states only what the profile states"])
        details = {"date": logged.on.isoformat() if logged else "", "format": logged.format if logged else "",
                   "people": ", ".join(logged.people) if logged else ""}
        context = lib.load_context(ctx.me, action.key)
        planner = ctx.gateway.harness("planner", "interview", action.key)
        researcher = ctx.gateway.harness("researcher", "interview", action.key)
        domain = domain_for(row.company, ctx.config.company_domains)
        notes = []
        with ctx.gateway.turns("interview", action.key) as turns:
            try:
                web = webresearch.WebResearch(turns, researcher, today=ctx.now.date(), cache_dir=ctx.me / "company_research",
                                              company_domain=domain, transport=TRANSPORT, resolve=RESOLVE, robots=ROBOTS)
            except ValueError as error:
                notes.append(f"company domain {domain!r} refused ({error}); researched by name only")
                web = webresearch.WebResearch(turns, researcher, today=ctx.now.date(), cache_dir=ctx.me / "company_research",
                                              transport=TRANSPORT, resolve=RESOLVE, robots=ROBOTS)
            with web:
                pack = lib.build_pack(turns, planner, context, ctx.snapshot.profile, self._fit(ctx, action.key), name, details, web)
            stopped = turns.stopped()
        produced = {"harness": planner, "researcher": researcher}
        if pack is None:
            if stopped:
                return Outcome("exhausted", notes=[f"interview pack stopped: {stopped}"], produced=produced)
            return Outcome("stalled", failures=[f"no interview pack for {name} passed the library's checks"], produced=produced)
        pack += self._notes(action.key, logged, notes)
        path = lib.save_pack(ctx.me, action.key, name, pack)
        title = f"Interview prep: {row.company}, {row.role} ({name})"
        why = f"a prep pack for {name}" + ("" if logged else "; no stage details were logged, so this is generic") + f"; rehearse with `jobloop mock {action.key}`"
        ctx.store.queue_put(item(action.key, name), "ready", str(path), {"title": title, "why": why, "deadline": details["date"]}, ctx.now)
        ctx.say(f"interview: saved {path.name}")
        return Outcome("success", notes=[f"saved {path}"], produced={**produced, "path": str(path)})

    def _notes(self, key: str, logged: archive.Stage | None, extra: list[str]) -> str:
        lines = ["", "## 9. Notes"]
        if logged is None:
            lines.append("- No interview stage is logged for this application, so this is a generic pack for the next interview. "
                         "Log the stage (date, format, interviewers) with `jobloop stage KEY STAGE --date YYYY-MM-DD --format video` and the loop prepares a specific one.")
        lines.append(f"- Mock interview: run `jobloop mock {key}` yourself. It is interactive and not part of the unattended loop.")
        return "\n".join(lines + [f"- {n}" for n in extra]) + "\n"


STAGE = Interview()

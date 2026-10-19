"""The draft stage: for the best shortlisted postings, `best_of` drafts of a CV and cover letter, each fully verified by code, a
judge panel picks the best, a different harness reviews it, and exactly one draft is archived and recorded `drafted`.

`propose` (cheap, no model, no network) offers shortlisted postings (the snapshot's state: ranked, no veto, at or above the
floor) that have a stored fit (`me/fit/`) with no veto, a research record (`me/research/<key>.json`, or `research` is in
`disabled_stages`), no tracker row and no archive folder, whose posting is not parked in the queue as `needs_decision`. The
floor is the active playbook's `strategy.min_fit` (else `config.min_fit`). Best fit first, and at most
`config.max_new_drafts` minus the successful drafts already made in the current pass (read from the engine database). An
action the policy would not run now (resting after failures) is not proposed, so it does not hold a slot.

`run`, in order. Guards again (tracker row: `no_op`; veto or floor or no fit: `blocked`). The source host: an unverified host
(`guards.hosts.classify`, of the posting's URL and of the URL its text was read from) is then put to `guards.hosts.trust`: the
employer's own site, a host in `trusted_hosts`, or an application the person approved (`jobloop queue approve`, an engine event
`human:approve_host` for this application only) continues the pipeline, with `host_trust` in draft.json and the note `source
host accepted: <reason>` (`host_kind` stays the truthful classification; the verifiers are unchanged). Without trust it writes a
`needs_decision` queue entry (data `host`, and `how`: the exact approve command) and returns `blocked` with the failure
`unverified source host: ...`; nothing is drafted. The
research record is read (a missing one with `research` enabled is `blocked`); its claims travel to the drafter as
research.md and nowhere else, and no researcher harness is used here. The posting text is `me/postings/<fit key>.txt` (what
triage fetched) or fetched through the library's ladder. Facts (company, role) are the posting entry's own words when they
are literally in the text, else one cheap harness turn (`posting.extract`). Then `best_of` drafts (playbook strategy, else
config): draft 0 on `gateway.harness("drafter")`, later ones on a harness of another family when the ladder has one; each
draft is verified by `verify.verify` (in the container) and failures go back verbatim on its conversation for up to ROUNDS
rounds; a draft that never passes is dropped. With `judge_drafts` every passing draft is scored by `engine.judge` on judges
of other families than its drafter (text from the compiled PDFs), the highest mean wins (ties: the first), ONE revision turn
carries the winner's failed rubric items, and it is kept only if it passes every check again and a re-judge does not score it
lower. The maker-checker review (reviewer on a harness of another family) and the loop's exact, grounded edits come from
`apply.Application.review/revise`; the final texts are re-verified when they are not the pair last verified; `finish`
(archive, tracker `drafted`, draft.json) runs once.

draft.json (see docs/loop-stages.md) holds `first_pass` (the chosen draft's first verification passed; the literal note
"first pass" is then in `notes` and in the attempt's notes, which the metrics read), the judge scores, both harnesses,
`best_of`, gaps, claims, host kind, fit and deadline. Judgments are stored per candidate (subject `draft-candidate-N`,
`draft-candidate-revised`) and the delivered draft's under subject `draft`, which `learn` and the metrics read.

Terminals: `success`; `stalled` with the verbatim last failures when no draft passed (the engine then rotates the drafter
down the ladder); `blocked` (vetoed, below the floor, no stored fit, no research record, unreadable posting, unverified host);
`no_op` (already tracked); `exhausted` on a stop (a draft that had passed every check is still finished and recorded, with a
note that it was not reviewed; nothing is written when none had passed).

The commit is crash-safe: its key goes into the engine kv `draft.committing` before `finish` and out after the tracker row; every
archive file is written atomically and draft.json lands before the row. A process killed in between is picked up again by
`propose` (the archive folder exists, but the key is still committing) and `_recover` either records the row from the whole archive
(`apply.recover_commit`, no model turn) or clears the partial files and drafts again. `propose` also puts a posting whose deadline is
within days (`policy.urgency`) ahead of a better fit when there are fewer slots than candidates, and skips a posting whose queue entry
the person has closed (`needs_decision` or `done`).

Never: writes any status but `drafted`, overrides a veto, sets `waive_unverified`, calls `input()`, claims what the verifiers
refuse, sends the profile to a researcher, or drafts the same application twice.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

from .. import apply as applying, fetch, letter, posting as posting_facts, rank, rankstate
from ..engine import judge, playbook as playbooks, policy, queue as queue_helpers
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context
from ..guards import hosts as host_check
from ..research import Claim
from ..text import flat, visible

APPROVED = queue_helpers.APPROVED   # engine event: the person approved this application's source host (item = application key)
APPROVE_HOW = queue_helpers.APPROVE_HOW
ROUNDS = 3                          # turns a drafter gets to fix failed checks, per verification (the apply loop's default)
COMMITTING = "draft.committing"     # engine kv: application key -> when its archive commit began; cleared once the tracker row is written
DEPS: applying.Deps | None = None   # tests supply stand-ins for the container verifier
FETCH = None                        # tests supply a stand-in for fetch.fetch_posting


def family(harness: str) -> str:
    return harness.split("/")[0]


def active_playbook(snap):
    """The active playbook version read from its files (no write, unlike `playbook.load`), or None."""
    for row in snap.store.playbook_versions():
        if row["status"] == "active":
            try:
                return playbooks.read(snap.me, row["version"])
            except (OSError, ValueError, KeyError):
                return None
    return None


def committing(store) -> dict:
    found = store.get(COMMITTING, {})
    return dict(found) if isinstance(found, dict) else {}


def end_commit(store, app: str) -> None:
    found = committing(store)
    if found.pop(app, None) is not None:
        store.put(COMMITTING, found)


def current_run(store) -> int | None:
    row = store.db.execute("SELECT id FROM runs WHERE finished IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def drafted_this_run(store) -> int:
    run = current_run(store)
    return sum(1 for a in store.attempts_in_run(run) if a["stage"] == "draft" and a["terminal"] == "success") if run else 0


def research_record(me: Path, key: str) -> dict | None:
    try:
        data = json.loads((me / "research" / f"{key}.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def claims_of(record: dict) -> tuple[Claim, ...]:
    found = []
    for item in record.get("claims") or []:
        try:
            found.append(Claim(str(item["claim"]), str(item["url"]), str(item["quote"])))
        except (KeyError, TypeError, ValueError):
            continue                                    # a claim that is not well formed is not used
    return tuple(found)


def fit_total(stored: dict, entry: dict) -> float:
    total = stored.get("total", entry.get("rank_score", 0))
    return total if isinstance(total, (int, float)) and not isinstance(total, bool) else 0


def pdf_text(rendered, fallback: str) -> str:
    """The text of a compiled PDF: what the verifier's container extracted, else `pdftotext` on the bytes, else the source."""
    text = getattr(rendered, "text", "")
    if isinstance(text, str) and text.strip():
        return text
    pdf = getattr(rendered, "pdf", None)
    if pdf and shutil.which("pdftotext"):
        try:
            done = subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, timeout=60)
            if done.returncode == 0 and done.stdout.decode("utf-8", "replace").strip():
                return done.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            pass
    return fallback


def judge_files(app: applying.Application, attempt: applying.Attempt) -> dict[str, str]:
    docs = attempt.result.documents
    pages = [f"{name}: {getattr(getattr(docs, name), 'pages', None) or 'unknown'} pages" for name in ("cv", "cover")]
    return {"profile.md": app.profile, "posting.md": app.run.posting, "cv.txt": pdf_text(docs.cv, attempt.cv),
            "cover.txt": pdf_text(docs.cover, attempt.cover), "pages.txt": "\n".join(pages) + "\n"}


def mean_of(judgments: list) -> float:
    return judge.mean(judgments) if judgments else -1.0


class Draft:
    spec = Spec("draft", "an application whose CV and letter pass every check, judged and reviewed", trigger=Trigger("state"),
                shape="maker-checker", levels=(1, 2, 4), roles=("drafter", "reviewer", "judge"), rank=60, brakes=Brakes(max_attempts=3))

    # ── what is eligible ───────────────────────────────────────────────────────────────────────
    def propose(self, snap) -> list[Action]:
        pb = active_playbook(snap)
        floor = (pb.strategy.get("min_fit", snap.config.min_fit) if pb else snap.config.min_fit)
        room = snap.config.max_new_drafts - drafted_this_run(snap.store)
        if room <= 0:
            return []
        research_off = "research" in snap.config_disabled()
        unfinished = committing(snap.store)
        found = []
        for pkey in snap.keys_in("shortlisted"):
            entry = snap.postings[pkey]
            app = snap.application_key(entry)
            if not app or app in snap.row_by_key or ((snap.me / "applications" / app).exists() and app not in unfinished):
                continue                                    # (a commit cut short by a kill is picked up again: `_recover`)
            if rankstate.vetoes(entry) or not (research_off or research_record(snap.me, app) is not None):
                continue
            stored = snap.fit(pkey)
            total = fit_total(stored or {}, entry)
            if stored is None or stored.get("vetoes") or total < floor:
                continue
            parked = snap.store.queue_item(app)
            if parked is not None and parked["status"] == "done":                              # the person has said they dealt with it
                continue
            revisit = parked is not None and parked["status"] == "needs_decision" and self._host_now_trusted(snap, parked, entry)
            if parked is not None and parked["status"] == "needs_decision" and not revisit:
                continue                                                                       # asked, and nothing has changed the answer
            tries = snap.store.attempts("draft", app)
            approved_since = bool(tries) and any(e["at"] > tries[-1]["finished"] for e in snap.store.events(queue_helpers.APPROVED, app))
            deadline = str(entry.get("deadline") or "")
            action = Action("draft", app, f"shortlisted, fit {total}, researched, not yet drafted", priority=float(total),
                            payload={"posting_key": pkey, "fit": total, **({"retry_now": True} if revisit or approved_since else {})}, deadline=deadline if deadline[:1].isdigit() else None)
            if policy.allowed(action, self.spec, snap.store, snap.now)[0]:
                found.append((total, app, action))
        found.sort(key=lambda t: (policy.urgency(t[2], snap.today), -t[0], t[1]))      # a deadline within days outranks a better fit for the few slots
        return [a for _, _, a in found[:room]]

    @staticmethod
    def _host_now_trusted(snap, parked, entry) -> bool:
        """A posting parked for its source host is looked at again when the employer's-own-site rule or `trusted_hosts` now covers it."""
        try:
            data = json.loads(parked["data"]) if isinstance(parked["data"], str) else dict(parked["data"] or {})
        except (TypeError, ValueError):
            return False
        if data.get("kind") != "unverified_source_host":
            return False
        kind, _ = host_check.trust(str(entry.get("url") or ""), str(entry.get("company") or ""), snap.config.company_domains,
                                   snap.config.trusted_hosts, False)
        return bool(kind)

    # ── one action ─────────────────────────────────────────────────────────────────────────────
    def _entry(self, action: Action, ctx: Context) -> tuple[str, dict | None]:
        snap = ctx.snapshot
        pkey = action.payload.get("posting_key", "")
        entry = snap.postings.get(pkey)
        if entry is not None and snap.application_key(entry) == action.key:
            return pkey, entry
        return next(((k, e) for k, e in snap.postings.items() if snap.application_key(e) == action.key), ("", None))

    def _needs_decision(self, ctx: Context, action: Action, entry: dict, kind: str, reason: str, question: str, total, extra: dict | None = None) -> Outcome:
        data = {"stage": "draft", "kind": kind, "title": f"{entry.get('company')}: {entry.get('title')}", "url": entry.get("url"),
                "question": question, "reason": reason, "fit": total, "deadline": entry.get("deadline"), **(extra or {})}
        ctx.store.queue_put(action.key, "needs_decision", str(ctx.me / "queue" / action.key), data, ctx.now)
        return Outcome("blocked", failures=[reason], notes=[question])

    def _posting(self, ctx: Context, pkey: str, entry: dict, allow=False) -> tuple[str, str] | Outcome | None:
        """(text, url the text came from), an Outcome that ends the action, or None when the text came from an unverified host.
        `allow` is True when the host was already accepted, or a function (url -> (kind, reason)) that decides for the host the
        text was fetched from; the verifiers are not involved."""
        cached = ctx.me / "postings" / f"{rank.fit_key(pkey)}.txt"
        try:
            text = cached.read_text()
        except OSError:
            text = ""
        if visible(text).strip():
            return text, str(entry.get("url") or "")
        got = (FETCH or fetch.fetch_posting)(str(entry.get("url") or ""), board=str(entry.get("board") or ""), title=str(entry.get("title") or ""),
                                              acknowledged=ctx.config.acknowledge_terms)
        if isinstance(got, fetch.Closed):
            return Outcome("blocked", failures=[f"the posting is closed: {got.reason}"])
        if not isinstance(got, fetch.Fetched) or not visible(got.text).strip():
            return Outcome("blocked", failures=[f"the posting could not be read: {getattr(got, 'reason', 'it is empty')}"])
        if got.host.kind == "unverified" and allow is not True:
            if not (callable(allow) and allow(got.url)):
                return None
        return got.text, got.url

    def _facts(self, entry: dict, text: str, turns, harness: str) -> posting_facts.Facts | None:
        company, title = str(entry.get("company") or ""), str(entry.get("title") or "")
        seen = flat(visible(text))
        if flat(company) and flat(title) and flat(company) in seen and flat(title) in seen:
            return posting_facts.Facts(company, posting_facts.short_role(title), deadline=str(entry.get("deadline") or "") or None,
                                       reference=posting_facts.reference_of(text), language=letter.detect(visible(text)))
        found = posting_facts.extract(turns, harness, text)
        if found is not None and entry.get("deadline") and not found.deadline:
            found = replace(found, deadline=str(entry["deadline"]))
        return found

    def _approved(self, store, app: str) -> bool:
        """Did the person approve THIS application's source host (`jobloop queue approve`)? Read from the event, so it persists."""
        return bool(store.events(APPROVED, app, limit=1))

    def run(self, action: Action, ctx: Context) -> Outcome:
        snap = ctx.snapshot
        pkey, entry = self._entry(action, ctx)
        if entry is None:
            return Outcome("blocked", failures=[f"the posting for {action.key} is no longer in the store"])
        app = action.key
        if app in snap.row_by_key:
            return Outcome("no_op", notes=[f"{app} is already in the tracker"])
        if rankstate.vetoes(entry):
            return Outcome("blocked", failures=["vetoed: " + "; ".join(rankstate.vetoes(entry))])
        stored = snap.fit(pkey)
        if stored is None:
            return Outcome("blocked", failures=["no stored fit evaluation for this posting (triage writes it)"])
        total = fit_total(stored, entry)
        pb = ctx.playbook
        floor = pb.strategy.get("min_fit", ctx.config.min_fit) if pb is not None else ctx.config.min_fit
        try:
            fit = applying.fit_from_dict(stored, pkey)
        except (KeyError, TypeError, ValueError) as error:
            return Outcome("blocked", failures=[f"the stored fit cannot be read: {error}"])
        if fit.vetoes or stored.get("vetoes"):
            return Outcome("blocked", failures=["vetoed by the fit's gates: " + "; ".join(fit.vetoes or stored["vetoes"])])
        if total < floor:
            return Outcome("blocked", failures=[f"fit {total} is below the floor {floor}"])
        recovered = self._recover(ctx, app)
        if recovered is not None:
            return recovered
        url = str(entry.get("url") or "")
        verdict = host_check.classify(url, fetch.installed_hosts())
        approved, company = self._approved(ctx.store, app), str(entry.get("company") or "")
        accepted: list[tuple[str, str]] = []                    # (kind, reason) of each unverified host the person's rules accepted
        if verdict.kind == "unverified":
            kind, reason = host_check.trust(url, company, ctx.config.company_domains, ctx.config.trusted_hosts, approved)
            if not kind:
                return self._unverified(ctx, action, entry, verdict, total)
            accepted.append((kind, reason))
        record = research_record(ctx.me, app)
        if record is None and "research" not in ctx.config.disabled_stages:
            return Outcome("blocked", failures=[f"no research record for {app} (me/research/{app}.json); the research stage writes one"])
        claims = claims_of(record or {})
        def text_host(text_url: str) -> bool:
            kind, reason = host_check.trust(text_url, company, ctx.config.company_domains, ctx.config.trusted_hosts, approved)
            if kind:
                accepted.append((kind, reason))
            return bool(kind)

        got = self._posting(ctx, pkey, entry, allow=True if accepted else text_host)
        if isinstance(got, Outcome):
            return got
        if got is None:
            return self._unverified(ctx, action, entry, host_check.HostVerdict("unverified", "", None, "the posting text came from an unverified host"), total)
        text, text_url = got
        try:
            return self._draft(ctx, action, pkey, entry, fit, stored, total, text, text_url, claims, record or {}, accepted)
        except applying.NeedsDecision as question:
            return self._needs_decision(ctx, action, entry, "needs_decision", f"needs a person: {question}", str(question), total)

    def _recover(self, ctx: Context, app: str) -> Outcome | None:
        """A commit that was cut short (the process was killed between the archive writes and the tracker row): finish it from the
        archive when it is whole (`apply.recover_commit`), else it is cleared away and the draft is made again."""
        if app not in committing(ctx.store):
            return None
        whole = applying.recover_commit(ctx.me, app, ctx.now.date())
        end_commit(ctx.store, app)
        return Outcome("success", notes=[f"{app}: an interrupted commit was finished from its archive; nothing was drafted again"]) if whole else None

    def _unverified(self, ctx, action, entry, verdict, total) -> Outcome:
        host = verdict.host or host_check.split_url(str(entry.get("url") or ""))[0]
        why = f"unverified source host: {host or entry.get('url')}: {verdict.reason}"
        how = APPROVE_HOW.format(key=action.key)
        return self._needs_decision(ctx, action, entry, "unverified_source_host", why,
                                    f"{entry.get('company')}: {entry.get('title')} was found on a host that is not an installed job board "
                                    f"or a known ATS ({host or entry.get('url')}). Check the employer and the link; nothing is drafted. {how}",
                                    total, {"host": host, "how": how})

    # ── the drafting ───────────────────────────────────────────────────────────────────────────
    def _drafter_for(self, ctx: Context, app: str, first: str, i: int) -> str:
        """Draft 0 on the ladder's current harness; later drafts on another family when the ladder has one."""
        if i == 0:
            return first
        pick = ctx.gateway.harness("drafter", "draft", app, exclude=first)
        others = [h for h in ctx.config.ladder("drafter") if family(h) != family(first)]
        if family(pick) == family(first) or i == 1 or len(others) < 2 or pick not in others:
            return pick
        return others[(others.index(pick) + i - 1) % len(others)]

    def _judge(self, ctx: Context, app: str, application, attempt, subject: str) -> list:
        return judge.score(ctx, app, judge_files(application, attempt), attempt.harness, subject=subject)

    def _draft(self, ctx, action, pkey, entry, fit, stored, total, text, text_url, claims, record, accepted=()) -> Outcome:
        app = action.key
        pb = ctx.playbook
        best_of = int((pb.strategy.get("best_of") if pb is not None else None) or ctx.config.best_of)
        best_of = max(1, min(4, best_of))
        first = ctx.gateway.harness("drafter", "draft", app)
        notes: list[str] = [f"source host accepted: {reason}" for _, reason in accepted]
        deps = replace(DEPS or applying.Deps(), today=lambda: ctx.now.date())
        with ctx.gateway.turns("draft", app) as turns:
            facts = self._facts(entry, text, turns, first)
            if facts is None:
                reason = "stopped" if turns.stopped() else "no company and role could be read from the posting"
                return Outcome("exhausted" if turns.stopped() else "stalled", failures=[reason], produced={"harness": first})
            prepared = applying.Prepared(
                posting=text, url=text_url, facts=facts, fit=fit, profile=ctx.snapshot.profile, claims=claims,
                style=pb.style_addendum() if pb is not None else "", key=app,
                record_as=(str(entry.get("company") or ""), str(entry.get("title") or "")), fit_total=int(total) if float(total).is_integer() else None,
                company_domain=str(record.get("domain") or ""),
                notes=(["no verified company research: the letter makes no company claims"] if not claims else []))
            application = applying.Application.for_engine(ctx.me, turns, prepared, drafter=first, reviewer="", rounds=ROUNDS, deps=deps)
            passed, failures = [], []
            for i in range(best_of):
                if turns.stopped():
                    break
                got = application.attempt(self._drafter_for(ctx, app, first, i))
                if got is not None:
                    passed.append(got)
                else:
                    failures = list(application.last_failures) or failures
            if not passed:
                if turns.stopped():
                    return Outcome("exhausted", notes=[f"stopped ({turns.stopped()}) before any draft passed every check"], produced={"harness": first})
                return Outcome("stalled", failures=failures or ["no draft passed every check"], notes=[f"{best_of} draft(s) dropped"],
                               produced={"harness": first})
            notes.append(f"{len(passed)} of {best_of} drafts passed every check")
            chosen, panel = self._pick(ctx, app, application, passed, turns, notes)
            first_pass = chosen.first_pass
            chosen, panel = self._improve(ctx, app, application, chosen, panel, turns, notes)
            version = getattr(pb, "version", 0)
            for j in panel:                                    # the delivered draft's judgments: what learn and the metrics read
                ctx.store.add_judgment(app, "draft", j.judge, j.overall, {"failed": j.failed, "reasons": j.reasons}, version, ctx.now)
            reviewer = self._review(ctx, app, application, chosen, turns, notes)
            cv, cover, result = application.run.best
            if application.verified != (cv, cover):            # final verification of exactly what is archived
                result = application.check(cv, cover)
                if not result.passed:
                    return Outcome("stalled", failures=list(result.failures), produced={"harness": chosen.harness})
                application.run.best = (cv, cover, result)
            stopped = turns.stopped()
        if first_pass:
            notes.insert(0, "first pass: the chosen draft passed every check on its first verification")
        info = {"posting_key": pkey, "judge": {"mean": judge.mean(panel), "judges": [
                    {"judge": j.judge, "overall": j.overall, "failed": [[n, why] for n, why in j.failed]} for j in panel]},
                "first_pass": first_pass, "harnesses": {"drafter": chosen.harness, "reviewer": reviewer}, "best_of": best_of, "notes": notes,
                "stretch": list(getattr(result, "stretch", ()) or ())}
        if accepted:
            info["host_trust"] = accepted[0][0]                 # the person's rule, recorded; host_kind stays the truthful classification          # bullets that reframe your experience: you keep, soften or drop them
        found = committing(ctx.store)
        found[app] = ctx.now.isoformat()
        ctx.store.put(COMMITTING, found)
        application.finish(info)
        end_commit(ctx.store, app)
        produced = {"harness": chosen.harness, "reviewer": reviewer, "judge_mean": info["judge"]["mean"], "first_pass": first_pass}
        if stopped and not reviewer:
            return Outcome("exhausted", notes=[*notes, f"stopped ({stopped}): the draft was finished without a review"], produced=produced)
        return Outcome("success", notes=notes + application.run.notes[-1:], produced=produced)

    def _pick(self, ctx, app, application, passed, turns, notes):
        """The best passing draft by mean judge score (ties: the first); with judging off, the first."""
        if not ctx.config.judge_drafts:
            return passed[0], []
        scored = []
        for n, attempt in enumerate(passed):
            if turns.stopped():
                break
            scored.append((attempt, self._judge(ctx, app, application, attempt, f"draft-candidate-{n}")))
        usable = [(a, j) for a, j in scored if j]
        if not usable:
            notes.append("no judge scored a draft: the first passing draft is used")
            return passed[0], []
        chosen, panel = max(usable, key=lambda t: mean_of(t[1]))
        notes.append("judge means: " + ", ".join(f"{a.harness} {judge.mean(j):.1f}" for a, j in usable) + f"; picked {chosen.harness}")
        return chosen, panel

    def _improve(self, ctx, app, application, chosen, panel, turns, notes):
        """One revision on the winner's failed rubric items, kept only if it passes every check and does not score lower."""
        items = judge.failed_items(panel)
        if not items or turns.stopped():
            return chosen, panel
        revised = application.refine(chosen, items)
        if revised is None:
            notes.append("judge revision discarded: it did not pass every check")
            return chosen, panel
        again = self._judge(ctx, app, application, revised, "draft-candidate-revised") if not turns.stopped() else []
        if again and mean_of(again) >= mean_of(panel):
            notes.append(f"judge revision kept: mean {mean_of(panel):.1f} to {mean_of(again):.1f}")
            return revised, again
        notes.append("judge revision discarded: " + ("its score dropped" if again else "it could not be re-judged"))
        return chosen, panel

    def _review(self, ctx, app, application, chosen, turns, notes) -> str:
        """The maker-checker step; returns the reviewer's harness, or "" when no review ran."""
        application.run.best = (chosen.cv, chosen.cover, chosen.result)
        reviewer = ctx.gateway.harness("reviewer", "draft", app, exclude=chosen.harness)
        if family(reviewer) == family(chosen.harness):
            notes.append("not reviewed: the reviewer ladder has no harness of another family than the drafter")
            return ""
        if turns.stopped():
            notes.append(f"not reviewed: stopped ({turns.stopped()})")
            return ""
        application.args.reviewer = reviewer
        review = application.review(chosen.cv, chosen.cover)
        if review is None:
            notes.append("not reviewed: the review turn did not run")
            return ""
        application.revise(chosen.record, review)
        return reviewer


STAGE = Draft()

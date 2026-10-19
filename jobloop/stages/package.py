"""The package stage: for each drafted application, one folder the person works from, `me/queue/<key>/`.

`propose` offers applications whose tracker row is `drafted`, whose archive folder holds the CV and cover letter PDFs and
`draft.json`, and that have no queue row yet, or whose row is stale (a source file changed, or the form answers were not
finished). A row the person marked done is never offered again.

`run` writes, all by code, with one harness turn only for form answers:
  cv.pdf, cover.pdf       copies of the delivered documents
  queue.json              apply URL, title, fit, deadline, host kind, why it fits (from the fit's strengths), the gaps the
                          letter acknowledges, verified company claims, judge mean, `delivered_hashes` (sha256 of the
                          delivered cv.tex and cover.tex) and `sources` (what this package was built from)
  README.md               the checklist: read both, open the link, submit it yourself, then `jobloop tracker set <key> applied`
  edit-here/cv.tex, edit-here/cover.tex
                          copies the person may edit; `learn` diffs them against `delivered_hashes`. A re-run refreshes an
                          edit-here file only while it still equals what was delivered: the person's edits are never overwritten.
  form-answers.txt        when there are questions (`form_questions` in loop.json, or me/queue-questions/<key>.md, which
                          wins): answers drafted by the `planner` harness through `forms` (counted by code, grounded in the
                          profile, style-checked, gap terms never claimed). A question about race, ethnicity, gender, sexual
                          orientation, disability, veteran status, age, religion, marital status, citizenship or any voluntary
                          self-identification (`forms.personal_question`, several languages) is NEVER sent to a model: it is
                          written as `FOR YOU: <original text>`.
Status: `ready`; `needs_decision` when the source host is unverified (and the draft has no `host_trust`: the draft stage accepted
it as the employer's site, a trusted host or an approved application, and the README then says so) or a check was waived in draft.json's notes; `blocked`
when a file is missing. The stage never writes a tracker status and never submits anything.
me/queue-questions/<key>.md: one question per line (a leading -, * or number is ignored, # lines are skipped); an optional
`(limit 300 words)` / `[limit 1200 characters]` at the end sets that question's limit.
Terminals: success (entry written; needs_decision counts, it waits for a person), blocked (a file is missing), stalled (no
form answer passed the checks; the entry is written with those questions left for the person), exhausted (budget or time).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .. import archive, forms
from ..engine import queue as queue_helpers
from ..engine.spec import Action, Brakes, Outcome, Spec, Trigger
from ..engine.stage import Context
from ..fetch import installed_hosts
from ..guards import hosts as host_check
from ..rank import fit_key
from ..text import visible

PDFS = {"cv.pdf": ("cv.pdf",), "cover.pdf": ("cover.pdf", "cover_letter.pdf")}
TEX = {"cv.tex": ("cv.tex",), "cover.tex": ("cover.tex", "cover_letter.tex")}
VERIFIED = ("portal", "ats")
ROUNDS = 3
LIMIT = re.compile(r"\s*[\[(]\s*limit\s+(\d+)\s*(characters without spaces|characters|chars|words)?\s*[\])]\s*$", re.I)
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
TRANSPORT_NOTE = "Drafted for you to read and paste. Nothing has been submitted."


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _first(folder: Path, names: tuple[str, ...]) -> Path | None:
    return next((folder / n for n in names if (folder / n).is_file()), None)


def _one_line(text: object, width: int = 300) -> str:
    return " ".join(str(text).split())[:width]


# ── questions ──
def parse_markdown_questions(text: str) -> tuple[forms.Question, ...]:
    found = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = BULLET.sub("", line)
        limit, unit = None, "characters"
        if m := LIMIT.search(line):
            line, limit = line[:m.start()].strip(), int(m[1])
            unit = {"chars": "characters", None: "characters"}.get((m[2] or "").lower(), (m[2] or "characters").lower())
        if line:
            found.append(forms.Question(f"q{len(found) + 1}", line, limit if limit and limit > 0 else None, unit))
    return tuple(found)


def questions_source(me: Path, key: str, config) -> tuple[str, Path | None]:
    """("markdown" | "json" | "", path): the person's per-application file wins over the standing `form_questions` file."""
    own = me / "queue-questions" / f"{key}.md"
    if own.is_file():
        return "markdown", own
    if config.form_questions:
        path = Path(config.form_questions)
        for candidate in (path, me / path):
            if candidate.is_file():
                return "json", candidate
    return "", None


def load_questions(me: Path, key: str, config, company: str) -> tuple[str, tuple[forms.Question, ...], str]:
    """(employer, questions, problem). A file that cannot be read is a problem for the person, never a prompt or a crash."""
    kind, path = questions_source(me, key, config)
    if not path:
        return company, (), ""
    try:
        if kind == "markdown":
            questions = parse_markdown_questions(path.read_text(encoding="utf-8"))
            if len({q.id for q in questions}) != len(questions):
                raise ValueError("duplicate question ids")
            return company, questions, ""
        employer, questions = forms.load_questions(path)
        return employer or company, questions, ""
    except (OSError, ValueError, SystemExit) as error:
        return company, (), f"the questions file {path.name} could not be read ({error})"


def _questions_hash(me: Path, key: str, config) -> str:
    _, path = questions_source(me, key, config)
    try:
        return _sha(path.read_bytes()) if path else ""
    except OSError:
        return ""


# ── what an application is built from ──
def sources_of(me: Path, key: str, config) -> dict:
    """sha256 of what the package is built from; a package is stale when this differs from what queue.json recorded."""
    folder = archive.folder_for(me, key)
    out = {}
    for name, alternatives in PDFS.items():
        found = _first(folder, alternatives)
        out[name] = _sha(found.read_bytes()) if found else ""
    draft = folder / "draft.json"
    out["draft.json"] = _sha(draft.read_bytes()) if draft.is_file() else ""
    out["questions"] = _questions_hash(me, key, config)
    return out


def read_queue_json(me: Path, key: str) -> dict:
    try:
        data = json.loads((queue_helpers.entry_dir(me, key) / "queue.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_draft(folder: Path) -> tuple[dict, str]:
    try:
        data = json.loads((folder / "draft.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {}, f"draft.json cannot be read ({type(error).__name__})"
    return (data, "") if isinstance(data, dict) else ({}, "draft.json is not an object")


def _lines(value: object) -> list[str]:
    items = [v if not isinstance(v, dict) else v.get("claim") or v.get("text") or "" for v in value] if isinstance(value, list) else []
    return [line for line in (_one_line(v) for v in items) if line]


def _claims(me: Path, key: str, draft: dict) -> list[dict]:
    """The company claims that were verified: the research record's, else the ones draft.json names."""
    try:
        record = json.loads((me / "research" / f"{key}.json").read_text(encoding="utf-8"))
        claims = record.get("claims") if isinstance(record, dict) else None
    except (OSError, ValueError):
        claims = None
    claims = claims if isinstance(claims, list) and claims else draft.get("claims")
    found = []
    for c in claims if isinstance(claims, list) else []:
        if isinstance(c, dict) and c.get("claim"):
            found.append({"claim": _one_line(c["claim"]), "url": _one_line(c.get("url") or "", 500)})
        elif isinstance(c, str) and c.strip():
            found.append({"claim": _one_line(c), "url": ""})
    return found


def _why(strengths: list[str]) -> str:
    kept = [_one_line(s, 120).rstrip(".") for s in strengths if str(s).strip()][:3]
    return ("Fits because: " + "; ".join(kept) + ".") if kept else "No fit strengths on file."


def _host_kind(draft: dict, url: str) -> str:
    kind = draft.get("host_kind")
    if kind in host_check.KINDS:
        return kind
    try:
        return host_check.classify(url, installed_hosts()).kind
    except ValueError:
        return "unverified"


def _accepted(draft: dict) -> tuple[str, str]:
    """(host_trust kind, why) when the draft stage accepted an unverified host (employer / trusted / approved), else ("", "")."""
    kind = draft.get("host_trust")
    if kind not in ("employer", "trusted", "approved"):
        return "", ""
    notes = draft.get("notes") if isinstance(draft.get("notes"), list) else []
    prefix = "source host accepted: "
    why = next((_one_line(n)[len(prefix):] for n in notes if isinstance(n, str) and n.startswith(prefix)), "")
    return kind, why or f"{kind}"


def _waived(draft: dict) -> list[str]:
    notes = draft.get("notes")
    return [_one_line(n) for n in notes if isinstance(n, str) and "waive" in n.casefold()] if isinstance(notes, list) else []


# ── edit-here ──
def _deliver_tex(folder: Path, edit: Path, name: str, previous: dict, notes: list[str]) -> str | None:
    """Copy the delivered tex into edit-here unless the person changed the copy. Returns the hash recorded as delivered."""
    source = _first(folder, TEX[name])
    if source is None:
        notes.append(f"{name} is not in the archive, so there is no editable copy")
        return previous.get(name)
    data = source.read_bytes()
    new, target, kept = _sha(data), edit / name, previous.get(name)
    if target.exists():
        current = _sha(target.read_bytes())
        if current not in (kept, new):
            notes.append(f"edit-here/{name} holds your edits and was left as it is")
            return kept or new
    edit.mkdir(parents=True, exist_ok=True)
    if not target.exists() or _sha(target.read_bytes()) != new:
        archive.write_atomically(target, data)
    (edit.parent / "original").mkdir(exist_ok=True)
    archive.write_atomically(edit.parent / "original" / name, data)      # what was delivered, so `learn` can diff the person's edits
    return new


# ── form answers ──
def _posting_text(me: Path, key: str, draft: dict, entry: dict) -> str:
    try:
        return visible((archive.folder_for(me, key) / archive.POSTING).read_bytes().decode("utf-8", "replace"))      # the archive keeps comments; no prompt gets them
    except OSError:
        return "\n".join([f"Title: {draft.get('role') or entry.get('title') or ''}", f"Company: {draft.get('company') or entry.get('company') or ''}"])


def _render_answers(employer: str, questions: tuple[forms.Question, ...], answers: dict[str, str], skipped: dict[str, str], profile: str) -> str:
    """forms.render for the answered questions, then every question left for the person, in its original text."""
    answered = tuple(q for q in questions if q.id in answers)
    if answered:
        text = forms.render(employer, answered, answers, profile).rstrip("\n")
        head, marker, tail = text.partition("\n## Dates quick reference")
        body = head
        tail = marker + tail
    else:
        body = f"APPLICATION FORM ANSWERS: {employer}\n{TRANSPORT_NOTE}\n"
        tail = ""
    left = [forms.for_you(q, skipped.get(q.id, "")) for q in questions if q.id not in answers]
    middle = ("\n\n" + "\n".join(left)) if left else ""
    return body + middle + ("\n" + tail if tail else "") + "\n"


def draft_answers(ctx: Context, key: str, employer: str, questions: tuple[forms.Question, ...], posting: str, gaps: list[str]) -> tuple[dict, list[str], str, str]:
    """(answers, failures, stopped, harness). One conversation on the planner harness; only answers that pass every check are kept."""
    profile = ctx.snapshot.profile
    kept: dict[str, str] = {}
    last: list[str] = []
    model = ctx.gateway.harness("planner", "package", key)

    def validate(record: dict) -> list[str]:
        raw = turns.files(record, "answers.json")["answers.json"]
        if raw is None:
            return ["answers.json must exist in the working directory."]
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        failures = forms.check_answers(questions, data, [profile], gaps, ctx.now.date())
        if not failures:
            kept.update(data)
        last[:] = failures
        return failures

    with ctx.gateway.turns("package", key) as turns:
        record = turns.turn("FORMS", model, "Draft answers.json for the questions in questions.json.", forms.ROLE,
                            files={"profile.md": profile, "posting.md": posting,
                                   "questions.json": json.dumps([q.__dict__ for q in questions], indent=2)})
        passed = turns.settle(record, validate, ROUNDS, forms.ROLE, "FIX") if record else None
        stopped = turns.stopped()
    return (kept if passed else {}), ([] if passed else list(last)), ("" if passed else stopped), model


# ── the README ──
def readme_text(key: str, data: dict, status: str, reasons: list[str], form_file: bool, left: list[str]) -> str:
    lines = [f"# {data['title']}", "", f"Status: {status}"]
    if reasons:
        lines += ["", "Decide first:"] + [f"- {r}" for r in reasons]
    lines += ["", "## Checklist", "",
              "1. Read both documents: `cv.pdf` and `cover.pdf`.",
              f"2. Open the link: {data['url'] or '(no link was recorded; find the posting yourself)'}",
              "3. Submit the application yourself. The loop never submits anything.",
              f"4. Then run `jobloop tracker set {key} applied` (and `jobloop queue done {key}`)."]
    if data["deadline"]:
        lines.append(f"5. The deadline is {data['deadline']}.")
    lines += ["", "## Notes", "",
              "- Any EEO, demographic or voluntary self-identification question (race, ethnicity, gender, sexual orientation, "
              "disability, veteran status, age, religion, marital status, citizenship) is left for you. The loop never answers them.",
              "- If you want to change a document, edit `edit-here/cv.tex` or `edit-here/cover.tex`; the loop learns from the difference. "
              "Your edits are never overwritten."]
    if data.get("host_trust"):
        lines += ["", f"Source host accepted: {data['host_accepted']}. Check the employer and the link yourself before you apply."]
    if data.get("stretch"):
        lines += ["", "## Bullets that stretch your experience (keep, soften or drop?)", "",
                  "These CV bullets reframe what the profile says more than a plain restatement does. Read each; you decide.", ""]
        lines += [f"- {b}" for b in data["stretch"]]
        lines.append("")
    if form_file:
        lines.append("- `form-answers.txt` holds drafted answers to the form's questions, each with its counts. Check every date and number before you paste." +
                     (f" {len(left)} question(s) in it are marked FOR YOU." if left else ""))
    if data["why"]:
        lines += ["", f"Why it fits: {data['why']}"]
    if data["gaps"]:
        lines += ["", "Gaps the letter acknowledges:"] + [f"- {g}" for g in data["gaps"]]
    if data["claims"]:
        lines += ["", "Verified company claims:"] + [f"- {c['claim']}" + (f" ({c['url']})" if c["url"] else "") for c in data["claims"]]
    return "\n".join(lines) + "\n"


def _write_json(path: Path, data: dict) -> None:
    archive.write_atomically(path, (json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode())


class Package:
    spec = Spec("package", "a queue entry the person can submit from: apply link, both PDFs, form answers, checklist",
                trigger=Trigger("state"), shape="solo", levels=(1, 2), roles=("planner",), rank=70, human_gate=True,
                brakes=Brakes(max_attempts=3))

    def _stale(self, snap, key: str) -> bool:
        row = snap.store.queue_item(key)
        if row is None:
            return True
        if row["status"] == "done":
            return False
        recorded = read_queue_json(snap.me, key)
        if recorded.get("sources") != sources_of(snap.me, key, snap.config):
            return True
        if recorded.get("forms_status") == "pending":
            return True
        if recorded.get("forms_status") == "failed":
            stalled = sum(1 for a in snap.store.attempts("package", key) if a["terminal"] == "stalled")
            return stalled < self.spec.brakes.max_attempts
        return False

    def propose(self, snap) -> list[Action]:
        found = []
        for row in snap.rows:
            key = row.key
            if not key or (row.status or "").strip().casefold() != "drafted":
                continue
            folder = archive.folder_for(snap.me, key)
            if not (folder / "draft.json").is_file() or any(_first(folder, names) is None for names in PDFS.values()):
                continue
            if not self._stale(snap, key):
                continue
            found.append(Action("package", key, "drafted, not yet queued" if snap.store.queue_item(key) is None else "queue entry is stale",
                                priority=10.0, deadline=row.deadline or None))
        return found

    def run(self, action: Action, ctx: Context) -> Outcome:
        key, me = action.key, ctx.me
        folder = archive.folder_for(me, key)
        out = queue_helpers.entry_dir(me, key)
        out.mkdir(parents=True, exist_ok=True)
        previous = read_queue_json(me, key)
        draft, draft_problem = _read_draft(folder)
        entry = ctx.snapshot.postings.get(draft.get("posting_key") or "") or {}
        missing = [name for name, alternatives in PDFS.items() if _first(folder, alternatives) is None]
        if draft_problem:
            missing.append("draft.json")
        company, role = _one_line(draft.get("company") or entry.get("company") or "", 100), _one_line(draft.get("role") or entry.get("title") or "", 100)
        row = next((r for r in ctx.snapshot.rows if r.key == key), None)
        company, role = company or (row.company if row else key), role or (row.role if row else "")
        url = _one_line(draft.get("url") or entry.get("url") or (row.source if row else ""), 500)
        deadline = str(draft.get("deadline") or entry.get("deadline") or (row.deadline if row else "") or "")
        fit_data = ctx.snapshot.fit(draft.get("posting_key") or "") or {}
        strengths = [s for s in (fit_data.get("strengths") or entry.get("strengths") or []) if isinstance(s, str)]
        judge = draft.get("judge") if isinstance(draft.get("judge"), dict) else {}
        kind = _host_kind(draft, url)
        trust_kind, trust_why = _accepted(draft)
        reasons = [f"the source host ({kind}) is not a verified job board or ATS: check the link is the employer's real posting"] \
            if kind not in VERIFIED and not trust_kind else []
        reasons += [f"a check was waived when this was drafted: {w}" for w in _waived(draft)]
        notes: list[str] = []
        info = {"key": key, "url": url, "title": f"{company} — {role}".strip(" —"), "company": company, "role": role,
                "fit": draft.get("fit", fit_data.get("total", entry.get("rank_score"))), "deadline": deadline, "host_kind": kind,
                "why": _why(strengths), "gaps": _lines(draft.get("gaps")), "stretch": _lines(draft.get("stretch")), "claims": _claims(me, key, draft),
                "judge_mean": judge.get("mean"), "host_trust": trust_kind, "host_accepted": trust_why}
        for name, alternatives in PDFS.items():
            if (src := _first(folder, alternatives)) is not None:
                archive.write_atomically(out / name, src.read_bytes())
        delivered = dict(previous.get("delivered_hashes") or {})
        for name in TEX:
            if not draft_problem or _first(folder, TEX[name]):
                if (h := _deliver_tex(folder, out / "edit-here", name, delivered, notes)):
                    delivered[name] = h
        status = "blocked" if missing else "needs_decision" if reasons else "ready"
        if missing:
            reasons = [f"missing from the application folder: {', '.join(missing)}"] + reasons

        outcome, stopped, harness, left, form_file, failures = "success", "", "", [], False, []
        forms_status = ""
        sources = sources_of(me, key, ctx.config)
        if not missing:
            employer, questions, problem = load_questions(me, key, ctx.config, company)
            if problem:
                notes.append(problem)
                reasons.append(problem)
                status = "needs_decision"
            if questions:
                form_file = True
                forms_status, outcome, stopped, harness, left, failures = self._forms(ctx, key, folder, out, draft, entry, employer, questions,
                                                                            previous, sources, fit_data, notes)
            elif (out / "form-answers.txt").exists():
                (out / "form-answers.txt").unlink()
        data = {**info, "status": status, "reasons": reasons, "notes": notes, "delivered_hashes": delivered, "sources": sources,
                "forms_status": forms_status, "left_for_you": left, "built": ctx.now.date().isoformat()}
        archive.write_atomically(out / "README.md", readme_text(key, info, status, reasons, form_file, left).encode())
        _write_json(out / "queue.json", data)
        ctx.store.queue_put(key, status, str(out), {k: info[k] for k in ("title", "fit", "deadline", "why", "url")}, ctx.now)
        produced = {"harness": harness} if harness else {}
        if missing:
            return Outcome("blocked", notes=notes, failures=[f"{key}: {reasons[0]}"], produced=produced)
        if outcome != "success":
            return Outcome(outcome, notes=notes + [f"{key}: queued as {status}, form answers unfinished"],
                           failures=[f"{key}: {stopped}"] if outcome == "exhausted" else failures, produced=produced)
        return Outcome("success", notes=[f"{key}: queued as {status}"] + notes, produced=produced)

    def _forms(self, ctx, key, folder, out, draft, entry, employer, questions, previous, sources, fit_data, notes):
        """(forms_status, outcome, stopped, harness, left_for_you, failures). Personal questions never reach a model."""
        personal = {q.id: forms.personal_question(q.text) for q in questions}
        left_ids = [q.id for q in questions if personal[q.id]]
        answerable = tuple(q for q in questions if not personal[q.id])
        target = out / "form-answers.txt"
        if previous.get("forms_status") == "ok" and previous.get("sources", {}).get("questions") == sources["questions"] and target.exists():
            return "ok", "success", "", "", previous.get("left_for_you", []), []
        skipped = {i: "" for i in left_ids}
        answers, harness, stopped, status, outcome, failures = {}, "", "", "ok", "success", []
        if answerable:
            gaps = forms.gap_terms(ctx.me / "fit" / f"{fit_key(draft.get('posting_key') or '')}.json") if fit_data else []
            answers, failures, stopped, harness = draft_answers(ctx, key, employer, answerable, _posting_text(ctx.me, key, draft, entry), gaps)
            if not answers:
                status, outcome = ("pending", "exhausted") if stopped else ("failed", "stalled")
                reason = "drafting was cut short" if stopped else "no answer passed the checks"
                skipped.update({q.id: reason for q in answerable})
                notes.append(f"form answers: {reason}")
        archive.write_atomically(target, _render_answers(employer, questions, answers, skipped, ctx.snapshot.profile).encode())
        return status, outcome, stopped, harness, [q.text for q in questions if q.id not in answers], failures


STAGE = Package()

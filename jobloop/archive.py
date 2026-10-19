"""archive.py: one folder per application under me/applications/<key>/, and the file primitives the tracker shares.

The folder holds the posting exactly as it was fetched (bytes, never a summary), the CV and cover letter that were submitted,
and outcome.md: the status and the interview stages, which are only ever appended. The key is derived from company and role
so that no character in either can split the name across directories or leave the folder; every path is checked to sit
directly inside me/applications/ before anything is written. Files already archived are never overwritten (what was
submitted wins) and recording twice is the same as recording once. Nothing here sends, submits or fetches anything.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import re
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

POSTING = "job_posting.md"
OUTCOME = "outcome.md"
DOCUMENT_KINDS = ("cv", "cover_letter")
ARCHIVE_STATUSES = ("in_progress", "hired", "offer_declined", "rejected", "no_response", "interview_only", "withdrawn")
STAGE_OUTCOMES = ("pending", "passed", "failed", "withdrawn")
KEY_BYTES = 120
_FEEDBACK_LIMIT = 10_000
_NAMED_DOCUMENT = re.compile(r"(?:cv|cover_letter)\.[a-z0-9]{1,6}")
_SUFFIX = re.compile(r"\.[a-z0-9]{1,6}")
_DATE = r"\d{4}-\d{2}-\d{2}"


class ArchiveRefused(ValueError):
    """The archive would have to break a promise (leave its folder, change a settled status) to do this."""


class OutcomeFormatError(ValueError):
    """outcome.md is not in the format this module writes."""


# ── file primitives (shared with tracker.py) ──
@contextmanager
def locked(target: Path, timeout: float = 15.0):
    """An exclusive lock for read-modify-write on `target`, so two writers never interleave."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target.with_name(target.name + ".lock"), "a") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{target.name} is locked by another writer; try again in a moment") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _temp(path: Path, data: bytes) -> str:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        os.unlink(name)
        raise
    return name


def write_atomically(path: Path, data: bytes) -> None:
    """Replace `path` with `data` in one step: a reader sees the old file or the new one, never half of it."""
    name = _temp(path, data)
    try:
        if path.exists():
            os.chmod(name, path.stat().st_mode & 0o777)
        os.replace(name, path)
    except BaseException:
        os.unlink(name)
        raise


def create_new(path: Path, data: bytes) -> bool:
    """Write `data` to `path` only if nothing is there; False when something already is. Never overwrites."""
    name = _temp(path, data)
    try:
        os.link(name, path)
        return True
    except FileExistsError:
        return False
    finally:
        os.unlink(name)


# ── the key ──
def slug_part(text: object) -> str:
    """Lower-cased letters and digits of any script; every other character (/ \\ . : space, controls) becomes one _."""
    folded = unicodedata.normalize("NFKC", str(text)).casefold()
    kept = "".join(c if c.isalnum() or unicodedata.category(c)[0] == "M" else "_" for c in folded)
    return re.sub(r"_+", "_", kept).strip("_")


def key(company: object, role: object) -> str:
    """The folder name of an application (also its tracker row key): <company>__<role>, lengths capped with a hash."""
    parts = [slug_part(company), slug_part(role)]
    if not all(parts):
        raise ValueError("company and role each need at least one letter or digit to name an application folder")
    name = "__".join(parts)
    if len(name.encode()) <= KEY_BYTES:
        return name
    digest = hashlib.sha256(name.encode()).hexdigest()[:10]
    while len(name.encode()) > KEY_BYTES - 11:
        name = name[:-1]
    return f"{name.rstrip('_')}_{digest}"


def is_key(name: object) -> bool:
    """True for a name this module could have derived: letters, digits, marks and underscores, one path component."""
    return (isinstance(name, str) and 0 < len(name.encode()) <= KEY_BYTES and name[0] != "_"
            and all(c == "_" or c.isalnum() or unicodedata.category(c)[0] == "M" for c in name))


def folder_for(me: Path, name: str, *, create: bool = False) -> Path:
    """me/applications/<name>, checked to be a real directory directly inside me/applications."""
    if not is_key(name):
        raise ArchiveRefused(f"{name!r} is not an application key; derive it with archive.key(company, role)")
    base = Path(me) / "applications"
    folder = base / name
    if folder.resolve().parent != base.resolve():
        raise ArchiveRefused(f"{folder} would leave {base}; it is a link out of the archive, so nothing is written there")
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


# ── posting and documents ──
def store_posting(me: Path, name: str, posting: bytes) -> str:
    """Archive the posting byte for byte as fetched: "written", or "kept" when one is already there (it is not replaced)."""
    if not isinstance(posting, bytes):
        raise TypeError("the posting is archived as the bytes that were fetched, not as a rewritten string")
    if not posting.strip():
        raise ArchiveRefused("no posting text to archive; nothing is written and nothing is reconstructed from memory")
    return "written" if create_new(folder_for(me, name, create=True) / POSTING, posting) else "kept"


def store_document(me: Path, name: str, kind: str, source: Path) -> str:
    """Copy (never move) a submitted CV or letter in as cv.<ext> or cover_letter.<ext>: "written", "kept" or "skipped"."""
    if kind not in DOCUMENT_KINDS:
        raise ValueError(f"kind must be one of {DOCUMENT_KINDS}, got {kind!r}")
    source = Path(source)
    suffix = source.suffix.lower()
    if not _SUFFIX.fullmatch(suffix):
        raise ArchiveRefused(f"{source.name} has no plain file extension to archive it under")
    if not source.is_file():
        return "skipped"
    return "written" if create_new(folder_for(me, name, create=True) / f"{kind}{suffix}", source.read_bytes()) else "kept"


# ── outcome.md ──
@dataclass(frozen=True)
class Stage:
    on: date
    stage: str
    format: str = ""
    people: tuple[str, ...] = ()
    feedback: str = ""
    outcome: str = "pending"


@dataclass(frozen=True)
class Note:
    on: date
    text: str


@dataclass(frozen=True)
class Outcome:
    title: str
    status: str = "in_progress"
    resolved: date | None = None
    stages: tuple[Stage, ...] = ()
    notes: tuple[Note, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in ARCHIVE_STATUSES:
            raise ValueError(f"archive status must be one of {ARCHIVE_STATUSES}, got {self.status!r}")
        if (self.status == "in_progress") != (self.resolved is None):
            raise ValueError("a date resolved is written exactly when the application is no longer in_progress")


def _line(value: object, width: int = 200) -> str:
    text = " ".join("".join(c for c in str(value) if c.isprintable() or c.isspace()).split())
    return text[:width]


def _verbatim(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c)[0] != "C").strip()
    if len(text) > _FEEDBACK_LIMIT:
        raise ArchiveRefused(f"text longer than {_FEEDBACK_LIMIT} characters; store it as a file in the folder instead")
    return text


def _block(label: str, value: str) -> str:
    first, *rest = value.split("\n")
    return "\n".join([f"{label} {first}".rstrip(), *("    " + r for r in rest)])


def render_outcome(outcome: Outcome) -> str:
    """outcome.md in the one format parse_outcome reads back."""
    lines = [f"# Outcome: {outcome.title}", "", f"**Status:** {outcome.status}"]
    if outcome.resolved:
        lines.append(f"**Date resolved:** {outcome.resolved.isoformat()}")
    lines += ["", "## Interview stages", ""]
    for s in outcome.stages:
        lines += [f"- Date: {s.on.isoformat()}", _block("- Stage:", s.stage), _block("- Format:", s.format),
                  _block("- People:", "; ".join(s.people)), _block("- Feedback:", s.feedback), f"- Outcome: {s.outcome}", ""]
    if not outcome.stages:
        lines += ["_None yet._", ""]
    lines += ["## Notes", ""]
    lines += [_block(f"- {n.on.isoformat()}:", n.text) for n in outcome.notes] or ["_None yet._"]
    return "\n".join(lines) + "\n"


def parse_outcome(text: str) -> Outcome:
    """Read outcome.md back; anything not in the written format raises OutcomeFormatError naming the line."""
    lines = text.split("\n")
    if not lines[0].startswith("# Outcome: "):
        raise OutcomeFormatError("outcome.md must start with '# Outcome: <Company> — <Role>'")
    title, status, resolved, section = lines[0][len("# Outcome: "):], None, None, None
    stages: list[dict[str, str]] = []
    notes: list[list[str]] = []
    target: list | None = None
    label = ""
    for number, line in enumerate(lines[1:], 2):
        if line.startswith("    ") and target is not None:
            if label:
                stages[-1][label] += "\n" + line[4:]
            else:
                notes[-1][1] += "\n" + line[4:]
        elif not line or line == "_None yet._":
            continue
        elif m := re.fullmatch(r"\*\*Status:\*\* (\w+)", line):
            status = m[1]
        elif m := re.fullmatch(rf"\*\*Date resolved:\*\* ({_DATE})", line):
            resolved = date.fromisoformat(m[1])
        elif line in ("## Interview stages", "## Notes"):
            section, target, label = line[3:], None, ""
        elif section == "Interview stages" and (m := re.fullmatch(r"- (Date|Stage|Format|People|Feedback|Outcome):(?: (.*))?", line)):
            label = m[1].lower()
            if label == "date":
                stages.append({})
            if not stages or label in stages[-1]:
                raise OutcomeFormatError(f"line {number}: a stage entry must start with '- Date:' and name each field once")
            stages[-1][label] = m[2] or ""
            target = stages
        elif section == "Notes" and (m := re.fullmatch(rf"- ({_DATE}):(?: (.*))?", line)):
            notes.append([m[1], m[2] or ""])
            target, label = notes, ""
        else:
            raise OutcomeFormatError(f"line {number}: {line!r} is not part of the outcome.md format")
    try:
        return Outcome(title, status or "", resolved, tuple(_stage(s) for s in stages),
                       tuple(Note(date.fromisoformat(d), t) for d, t in notes))
    except (ValueError, KeyError) as error:
        raise OutcomeFormatError(f"outcome.md is inconsistent: {error}") from error


def _stage(fields: dict[str, str]) -> Stage:
    if fields["outcome"] not in STAGE_OUTCOMES:
        raise ValueError(f"stage outcome {fields['outcome']!r}")
    return Stage(date.fromisoformat(fields["date"]), fields["stage"], fields["format"],
                 tuple(p for p in fields["people"].split("; ") if p), fields["feedback"], fields["outcome"])


def _outcome_path(me: Path, name: str) -> Path:
    return folder_for(me, name) / OUTCOME


def read_outcome(me: Path, name: str) -> Outcome | None:
    """The application's outcome.md, or None when it has none."""
    path = _outcome_path(me, name)
    return parse_outcome(path.read_text(encoding="utf-8")) if path.is_file() else None


def _update(me: Path, name: str, change) -> bool:
    """Read-modify-write outcome.md under a lock; `change` returns the new Outcome, or None for no change."""
    path = _outcome_path(me, name)
    if not path.is_file():
        raise ArchiveRefused(f"no outcome.md for {name}; call archive.open_application first")
    with locked(path):
        current = parse_outcome(path.read_text(encoding="utf-8"))
        new = change(current)
        if new is None or new == current:
            return False
        write_atomically(path, render_outcome(new).encode())
        return True


def open_application(me: Path, company: object, role: object) -> str:
    """Create the folder and an in_progress outcome.md if they are not there yet; return the key. Idempotent."""
    name = key(company, role)
    folder = folder_for(me, name, create=True)
    title = f"{_line(company, 100)} — {_line(role, 100)}"
    create_new(folder / OUTCOME, render_outcome(Outcome(title)).encode())
    return name


def record_stage(me: Path, name: str, *, on: date, stage: str, format: str = "", people: tuple[str, ...] = (),
                 feedback: str = "", outcome: str = "pending") -> bool:
    """Append one interview stage entry. Earlier entries are never touched; an identical entry is not added twice."""
    if outcome not in STAGE_OUTCOMES:
        raise ValueError(f"stage outcome must be one of {STAGE_OUTCOMES}, got {outcome!r}")
    entry = Stage(on, _line(stage, 80), _line(format), tuple(_line(p, 80).replace(";", ",") for p in people if _line(p)),
                  _verbatim(feedback), outcome)
    if not entry.stage:
        raise ValueError("a stage needs a name, e.g. phone_screen, technical, case, final_round")
    return _update(me, name, lambda o: o if entry in o.stages else Outcome(
        o.title, o.status, o.resolved, o.stages + (entry,), o.notes))


def record_outcome(me: Path, name: str, status: str, *, on: date, note: str = "") -> bool:
    """Settle the application: in_progress becomes `status` and the date resolved is set. A settled status never changes
    (only notes are added); recording the same thing again changes nothing."""
    if status not in ARCHIVE_STATUSES or status == "in_progress":
        raise ValueError(f"an outcome is one of {ARCHIVE_STATUSES[1:]}, got {status!r}")
    text = _verbatim(note)

    def change(o: Outcome) -> Outcome:
        if o.status not in ("in_progress", status):
            raise ArchiveRefused(f"{name} is already {o.status}; add a note instead of changing a settled outcome")
        added = Note(on, text)
        notes = o.notes + (added,) if text and added not in o.notes else o.notes
        return Outcome(o.title, status, o.resolved or on, o.stages, notes)

    return _update(me, name, change)


# ── reading it back ──
@dataclass(frozen=True)
class Archive:
    key: str
    posting: bytes | None
    documents: tuple[str, ...]
    outcome: Outcome | None


def read(me: Path, name: str) -> Archive:
    """What is archived under `name`: only the named files are read, any other file in the folder is ignored."""
    folder = folder_for(me, name)
    if not folder.is_dir():
        return Archive(name, None, (), None)
    posting = folder / POSTING
    documents = sorted(p.name for p in folder.iterdir() if p.is_file() and _NAMED_DOCUMENT.fullmatch(p.name))
    return Archive(name, posting.read_bytes() if posting.is_file() else None, tuple(documents), read_outcome(me, name))

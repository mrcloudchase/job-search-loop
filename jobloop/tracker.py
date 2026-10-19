"""tracker.py: me/tracker.csv, the one record of where every application stands, and the rules for changing it.

What it promises, enforced here and not left to a prompt:
- the status is one of nine words; a row never moves backwards, and a Final row is never rewritten (a new application to
  the same company and role gets a fresh row);
- the loop can only ever write `drafted` (`record_drafted` has no status parameter); every other status is a person's
  command (`set_status`, `add_external`), refused unless `by_person=True`;
- every value written is made CSV-safe first; the header is stable, rows we do not touch stay byte for byte as they were,
  unknown columns survive, and each write is atomic and locked against another writer;
- a row's key is its archive folder name (archive.key), so the tracker and me/applications/ always agree.
It also lists the open pipeline with days-quiet maths (dates in, days out) and offers the CLI `list`, `set`, `add`.
Nothing here sends or submits anything.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jobloop import archive, csvsafe
from jobloop.term import say

HEADER = ("date", "company", "sector", "role", "role_type", "channel", "status", "contact_person", "fit_rating", "notes",
          "cv_file", "cover_letter_file", "source", "deadline")
STATUSES = ("drafted", "applied", "interview", "offer", "hired", "rejected", "no_response", "offer_declined", "withdrawn")
OPEN = frozenset(("drafted", "applied", "interview", "offer"))
FINAL = frozenset(STATUSES) - OPEN
RANK = {"drafted": 0, "applied": 1, "interview": 2, "offer": 3, **{s: 4 for s in FINAL}}
CHANNELS = ("", "portal", "online")
URGENT_DAYS = 7
FOLLOWUP_AFTER_DAYS = 10
FOLLOWUPS_BEFORE_REST = 2
STALE_AFTER_DAYS = 60
REDRAFTED = "redrafted"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class StatusRefused(ValueError):
    """A status change that would break a rule (backwards, out of a Final status, not by a person)."""


class UnknownKey(LookupError):
    """No application with that key is in the tracker."""


# ── the status vocabulary ──
def normalize_status(raw: object) -> str | None:
    """The canonical status a stored value means (case ignored; the legacy `no response`/`offer declined` accepted), or None."""
    text = " ".join(str(raw).strip().lower().split())
    text = text.replace(" ", "_") if text in ("no response", "offer declined") else text
    return text if text in RANK else None


def is_open(raw: object) -> bool:
    """Open is whatever is not Final, so an unknown value stays open until someone declares it final."""
    return normalize_status(raw) not in FINAL


def is_chaseable(raw: object) -> bool:
    """Open and actually sent: a `drafted` row was never submitted, so nobody is waiting to hear back."""
    return is_open(raw) and normalize_status(raw) != "drafted"


def validate_status(raw: object) -> str:
    """The canonical status for a value about to be written; anything else (including the archive's own words) is refused."""
    status = normalize_status(raw)
    if status is None:
        raise StatusRefused(f"{raw!r} is not a tracker status; use one of: {', '.join(STATUSES)}")
    return status


def advance(current: object, new: object) -> str:
    """`new` if a row holding `current` may move there. Same status is fine (a no-op); backwards is refused, and so is any
    change out of a Final status (it is never reopened; add a new application instead)."""
    status = validate_status(new)
    held = normalize_status(current)
    if held in FINAL and status != held:
        raise StatusRefused(f"the row is already {held}, which is final; it is never reopened or rewritten. "
                            "A new application to the same company and role is a new row")
    if held in FINAL:
        return status
    if held is not None and RANK[status] < RANK[held]:
        raise StatusRefused(f"a row never moves backwards: it is {held}, so it cannot become {status}")
    return status


# ── values ──
@dataclass(frozen=True)
class Row:
    date: str = ""
    company: str = ""
    sector: str = ""
    role: str = ""
    role_type: str = ""
    channel: str = ""
    status: str = ""
    contact_person: str = ""
    fit_rating: str = ""
    notes: str = ""
    cv_file: str = ""
    cover_letter_file: str = ""
    source: str = ""
    deadline: str = ""

    @property
    def key(self) -> str:
        """The archive folder name of this application; empty for a hand-edited row too bare to name one."""
        try:
            return archive.key(self.company, self.role)
        except ValueError:
            return ""


def parse_date(text: object) -> date | None:
    """A strict YYYY-MM-DD date, or None. Never guesses."""
    if not isinstance(text, str) or not _DATE.fullmatch(text.strip()):
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def validate_fit_rating(value: object) -> str:
    """A bare number from 0 to 100 as text ("72", "72.5"), never `72/100` or a verdict; None and "" mean unrated."""
    if value is None or value == "":
        return ""
    text = str(value).strip()
    if isinstance(value, bool) or not re.fullmatch(r"\d{1,3}(\.\d+)?", text) or float(text) > 100:
        raise ValueError(f"fit_rating must be a bare number from 0 to 100, got {value!r}")
    return text


def _deadline(value: object) -> str:
    if value is None or value == "":
        return ""
    if parse_date(value) is None:
        raise ValueError(f"deadline must be YYYY-MM-DD or empty, got {value!r}")
    return str(value).strip()


def _exact(value: object, name: str) -> str:
    """A file name kept exactly as given; refused (not silently altered) if it could not be written exactly."""
    text = "" if value is None else str(value)
    if not csvsafe.is_safe(text, 500):
        raise ValueError(f"{name} {text!r} has a comma, quote, line break or other character a tracker field cannot hold")
    return text


def append_note(existing: str, fragment: object) -> str:
    """The one way a note is added: the fragment is made CSV-safe, then joined to what is there with `; `."""
    safe = csvsafe.note(fragment)
    return f"{existing}; {safe}" if existing and safe else existing or safe


def _dated_note(on: date, text: object) -> str:
    safe = csvsafe.note(text)
    return f"{on.isoformat()} {safe}" if safe else ""


# ── the file ──
class _Table:
    """The tracker's text as records, each holding its own raw text so that rows nobody touches are written back as read."""

    def __init__(self, text: str):
        self.records: list[list] = []
        if not text:
            self.columns, self.header_raw, self.eol = list(HEADER), ",".join(HEADER) + "\n", "\n"
            return
        lines = list(io.StringIO(text, newline=""))
        reader, done = csv.reader(iter(lines)), 0
        try:
            for fields in reader:
                self.records.append(["".join(lines[done:reader.line_num]), fields])
                done = reader.line_num
        except csv.Error as error:
            raise ValueError(f"tracker.csv cannot be read: {error}") from error
        self.header_raw, header = self.records.pop(0)
        self.columns = header
        self.eol = "\r\n" if self.header_raw.endswith("\r\n") else "\n"
        missing = [c for c in HEADER if c not in header and c != "deadline"]
        if missing or len(set(header)) != len(header):
            raise ValueError(f"tracker.csv header is not the tracker header (missing {missing or 'none'}, or a repeated column)")

    def migrate(self) -> None:
        """A header without `deadline` gets `,deadline` on the header line only; no data row is touched."""
        if "deadline" not in self.columns:
            self.columns.append("deadline")
            self.header_raw = self.header_raw.rstrip("\r\n") + ",deadline" + self.eol

    def rows(self) -> list[tuple[int, Row]]:
        return [(i, self.row(i)) for i, (_, fields) in enumerate(self.records) if fields]

    def row(self, index: int) -> Row:
        values = dict(zip(self.columns, self.records[index][1]))
        return Row(**{c: values.get(c, "") for c in HEADER})

    def _line(self, fields: list[str]) -> str:
        out = io.StringIO()
        csv.writer(out, lineterminator=self.eol).writerow(fields)
        return out.getvalue()

    def update(self, index: int, changes: dict[str, str]) -> Row:
        fields = list(self.records[index][1])
        if len(fields) > len(self.columns):
            raise ValueError("that row has more fields than the header has columns (a stray comma?); fix tracker.csv by hand")
        fields += [""] * (len(self.columns) - len(fields))
        for name, value in changes.items():
            fields[self.columns.index(name)] = value
        self.records[index] = [self._line(fields), fields]
        return self.row(index)

    def append(self, values: dict[str, str]) -> Row:
        fields = [values.get(c, "") for c in self.columns]
        self.records.append([self._line(fields), fields])
        return self.row(len(self.records) - 1)

    def text(self) -> str:
        raws = [self.header_raw] + [r for r, _ in self.records]
        return "".join(r if r.endswith(("\n", "\r")) or i == len(raws) - 1 else r + self.eol for i, r in enumerate(raws))


def _load(path: Path) -> _Table:
    return _Table(path.read_bytes().decode("utf-8-sig") if path.exists() else "")


def _edit(path: Path, change):
    """Run `change(table)` under the lock on the current file and write the result atomically."""
    path = Path(path)
    with archive.locked(path):
        table = _load(path)
        table.migrate()
        result = change(table)
        archive.write_atomically(path, table.text().encode())
        return result


def read_rows(path: Path) -> tuple[Row, ...]:
    """Every row, in file order; a missing tracker is no rows. Rows lacking `deadline` read with an empty one."""
    return tuple(row for _, row in _load(Path(path)).rows())


def ensure_tracker(path: Path) -> None:
    """Create the tracker with the standard header if it is not there."""
    _edit(path, lambda table: None)


def match_rows(rows, company: str, role: str | None = None) -> list[Row]:
    """Rows for a company (and role if given), compared the way keys are: case, spacing and punctuation ignored."""
    wanted = archive.slug_part(company)
    wanted_role = archive.slug_part(role) if role else None
    return [r for r in rows if archive.slug_part(r.company) == wanted
            and (wanted_role is None or archive.slug_part(r.role) == wanted_role)]


# ── what the loop may write ──
@dataclass(frozen=True)
class Recorded:
    action: str
    key: str
    row: Row


def record_drafted(path: Path, *, company: str, role: str, today: date, cv_file: str, cover_letter_file: str,
                   fit_rating: object = None, source: str = "", deadline: object = None, sector: str = "",
                   role_type: str = "", channel: str = "", contact_person: str = "") -> Recorded:
    """The only tracker write the loop makes: a `drafted` row for a new application, or a refresh of the still-open row for
    this company and role. It has no status parameter, so it cannot write `applied` or anything else. A refresh keeps
    the status, keeps the date unless the row is still `drafted` (then today), keeps an existing deadline, fit and source
    when the new run found none, and adds an undated `redrafted` note (undated so it never counts as contact)."""
    if not isinstance(today, date):
        raise TypeError("today must be a date")
    company, role = csvsafe.field(company), csvsafe.field(role)
    name = archive.key(company, role)
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    fresh = {"cv_file": _exact(cv_file, "cv_file"), "cover_letter_file": _exact(cover_letter_file, "cover_letter_file"),
             "fit_rating": validate_fit_rating(fit_rating), "source": csvsafe.url(source), "deadline": _deadline(deadline)}

    def change(table: _Table) -> Recorded:
        same = [(i, r) for i, r in table.rows() if r.key == name]
        open_rows = [(i, r) for i, r in same if is_open(r.status)]
        if open_rows:
            index, row = open_rows[-1]
            changes = {k: v for k, v in fresh.items() if v or k in ("cv_file", "cover_letter_file")}
            if normalize_status(row.status) == "drafted":
                changes["date"] = today.isoformat()
            if REDRAFTED not in [n.strip() for n in row.notes.split(";")]:
                changes["notes"] = append_note(row.notes, REDRAFTED)
            return Recorded("updated", name, table.update(index, changes))
        values = {"date": today.isoformat(), "company": company, "role": role, "status": "drafted", "channel": channel,
                  "sector": csvsafe.field(sector), "role_type": csvsafe.field(role_type),
                  "contact_person": csvsafe.field(contact_person), **fresh}
        return Recorded("added_after_final" if same else "added", name, table.append(values))

    return _edit(path, change)


# ── what only a person may write ──
def _require_person(by_person: object) -> None:
    if by_person is not True:
        raise StatusRefused("only a person changes a status beyond `drafted`; this call must be a person's command (by_person=True)")


def _open_row(table: _Table, name: str) -> tuple[int, Row]:
    same = [(i, r) for i, r in table.rows() if r.key == name]
    if not same:
        raise UnknownKey(f"no application {name!r} in the tracker; `list` shows the keys")
    open_rows = [(i, r) for i, r in same if is_open(r.status)]
    return open_rows[-1] if open_rows else same[-1]


def set_status(path: Path, name: str, status: str, *, by_person: bool, on: date, note: str = "", me: Path | None = None) -> Row:
    """Move an application to `status` (a person's command). Refused if it would go backwards or leave a Final status.
    Only `status` and `notes` change, plus `date` when the row leaves `drafted` (it becomes the day it was submitted).
    Setting the Final status a row already holds changes nothing in the tracker. With `me`, a Final status is also
    settled in the application's archive so the two stay in step."""
    _require_person(by_person)

    def change(table: _Table) -> Row:
        index, row = _open_row(table, name)
        new = advance(row.status, status)
        if normalize_status(row.status) in FINAL:
            return row
        changes = {"status": new}
        if normalize_status(row.status) == "drafted" and new != "drafted":
            changes["date"] = on.isoformat()
        fragment = _dated_note(on, note)
        if fragment:
            changes["notes"] = append_note(row.notes, fragment)
        return table.update(index, changes)

    row = _edit(path, change)
    if me is not None and row.status in FINAL:
        archive.open_application(me, row.company, row.role)
        archive.record_outcome(me, row.key, row.status, on=on, note=note)
    return row


def add_external(path: Path, *, company: str, role: str, applied_on: date, channel: str = "", source: str = "",
                 by_person: bool) -> Row:
    """Record an application made outside the loop, as `applied` on the day the person says (a person's command)."""
    _require_person(by_person)
    company, role = csvsafe.field(company), csvsafe.field(role)
    name = archive.key(company, role)
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")

    def change(table: _Table) -> Row:
        if any(r.key == name and is_open(r.status) for _, r in table.rows()):
            raise StatusRefused(f"{name} is already open in the tracker; change it with `set` instead of adding it again")
        return table.append({"date": applied_on.isoformat(), "company": company, "role": role, "status": "applied",
                             "channel": channel, "source": csvsafe.url(source)})

    return _edit(path, change)


# ── the open pipeline ──
def days_quiet(row: Row, today: date) -> int | None:
    """Days since the later of the row's date and the latest dated entry in its notes (a date after `today` is not contact
    yet); None when no date parses, never a guess. The undated `redrafted` marker counts for nothing."""
    dates = [d for d in [parse_date(row.date), *(parse_date(m) for m in _DATE.findall(row.notes))] if d and d <= today]
    return (today - max(dates)).days if dates else None


def followups_sent(notes: str) -> int:
    """How many `followed up YYYY-MM-DD` markers the notes hold."""
    return len(re.findall(r"followed up \d{4}-\d{2}-\d{2}", notes))


def deadline_flag(deadline: str, today: date) -> str:
    """"missed" once passed, "urgent" within seven days, "" otherwise (and for a deadline that does not parse)."""
    due = parse_date(deadline)
    if due is None:
        return ""
    left = (due - today).days
    return "missed" if left < 0 else "urgent" if left <= URGENT_DAYS else ""


@dataclass(frozen=True)
class Entry:
    row: Row
    days_quiet: int | None
    followups: int | None
    flag: str


@dataclass(frozen=True)
class Pipeline:
    submitted: tuple[Entry, ...]
    drafted: tuple[Entry, ...]
    skipped: tuple[str, ...]
    followup_due: bool
    stale_due: bool


def open_pipeline(rows, today: date) -> Pipeline:
    """Every open row: sent ones with days quiet and follow-ups sent, drafted ones apart with neither (nothing was sent, so
    no clock runs). A sent row whose dates do not parse is skipped and named, never guessed."""
    submitted, drafted, skipped = [], [], []
    for row in rows:
        if not is_open(row.status):
            continue
        flag = deadline_flag(row.deadline, today)
        if not is_chaseable(row.status):
            drafted.append(Entry(row, None, None, flag))
        elif (quiet := days_quiet(row, today)) is None:
            skipped.append(f"{row.key or row.company}: no date that parses, so no days-quiet figure")
        else:
            submitted.append(Entry(row, quiet, followups_sent(row.notes), flag))
    return Pipeline(tuple(submitted), tuple(drafted), tuple(skipped),
                    any(e.days_quiet >= FOLLOWUP_AFTER_DAYS and e.followups < FOLLOWUPS_BEFORE_REST for e in submitted),
                    any(e.days_quiet >= STALE_AFTER_DAYS for e in submitted))


def render_pipeline(pipeline: Pipeline) -> str:
    """The pipeline as text. The deadline shows only on rows that have one; a passed deadline on a drafted row is called out."""
    if not (pipeline.submitted or pipeline.drafted):
        return "\n".join(["Every application is resolved.", *pipeline.skipped])
    out, n = [], 0
    if pipeline.submitted:
        out.append("  #  company / role                          applied     status      deadline          quiet  follow-ups")
        for e in pipeline.submitted:
            n += 1
            due = f"{e.row.deadline} {e.flag}".strip()
            out.append(f"{n:>3}  {e.row.company[:18]:<18} {e.row.role[:20]:<20} {e.row.date:<11} {e.row.status:<11} "
                       f"{due:<17} {e.days_quiet:>4}d  {e.followups}")
    if pipeline.drafted:
        out += ["", "Drafted, not yet submitted"]
        for e in pipeline.drafted:
            n += 1
            due = f"  deadline {e.row.deadline}" if e.row.deadline else ""
            out.append(f"{n:>3}  {e.row.company} — {e.row.role}  (drafted {e.row.date}){due}")
        out += [f"     deadline passed, never sent: {e.row.company} — {e.row.role} ({e.row.deadline})"
                for e in pipeline.drafted if e.flag == "missed"]
    if pipeline.followup_due:
        out += ["", f"Some applications are {FOLLOWUP_AFTER_DAYS}+ days quiet with under {FOLLOWUPS_BEFORE_REST} follow-ups: a nudge is due."]
    if pipeline.stale_due:
        out.append(f"Some applications are {STALE_AFTER_DAYS}+ days quiet: consider a stale sweep.")
    return "\n".join([*out, *pipeline.skipped])


# ── the human command ──
def add_arguments(p) -> None:
    """`list`, `set KEY STATUS --note` and `add` (an application made outside the loop): the person's side of the tracker."""
    p.add_argument("--me", default="me", help="your personal folder (default: me)")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="the open pipeline with days quiet")
    s = sub.add_parser("set", help="change an application's status (you decide, the loop never does)")
    s.add_argument("key", help="the application key shown by `list`")
    s.add_argument("status", help=" | ".join(STATUSES))
    s.add_argument("--note", default="", help="a short note, dated and added to the row")
    s.add_argument("--date", help="the day it happened, YYYY-MM-DD (default: today)")
    a = sub.add_parser("add", help="add an application made outside the loop")
    a.add_argument("--company", required=True)
    a.add_argument("--role", required=True)
    a.add_argument("--date", help="the day you applied, YYYY-MM-DD (default: today)")
    a.add_argument("--channel", default="", choices=CHANNELS)
    a.add_argument("--source", default="", help="the posting URL")


def main(args, today: date | None = None) -> int:
    """Run the human command; refusals are printed with what to do and return 2."""
    me = Path(args.me)
    path, today = me / "tracker.csv", today or date.today()
    on = today
    if args.action != "list" and args.date:
        on = parse_date(args.date)
        if on is None:
            say(f"--date must be YYYY-MM-DD, got {args.date!r}")
            return 2
    try:
        if args.action == "list":
            say(render_pipeline(open_pipeline(read_rows(path), today)))
        elif args.action == "set":
            row = set_status(path, args.key, args.status, by_person=True, on=on, note=args.note, me=me)
            say(f"{row.key}: {row.status}")
        else:
            row = add_external(path, company=args.company, role=args.role, applied_on=on, channel=args.channel,
                               source=args.source, by_person=True)
            say(f"added {row.key}: applied {row.date}")
    except (ValueError, LookupError, TimeoutError) as error:
        say(str(error))
        return 2
    return 0

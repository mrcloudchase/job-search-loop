"""report.py: the whole job search on one static page, me/report.html.

One HTML file with its CSS inline and its charts as inline SVG: no script, no external resource of any kind (no font, no
stylesheet, no image, no CDN), so it opens offline and nothing in it can run. Light and dark come from CSS variables.

What it promises:
- every value that came from the tracker or an outcome.md is HTML-escaped where it is written, in cells, attributes and
  SVG text alike; a link is made only from an http or https URL, anything else is shown as text;
- it only reads: the tracker and the archive are never written, and running it again replaces the same file;
- the funnel is built from STAGE HISTORY (the interview stages in outcome.md, and the statuses that imply a stage), never
  from the current status alone: an application that reached an interview and was then rejected counts at Interview;
- `drafted` rows were never sent, so they appear only as a count and in their own list, and in no statistic;
- every number is counted from the rows; a figure with nothing to count from is shown as "n/a", never estimated.
"""
from __future__ import annotations

import argparse
import html
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from jobloop import archive, stale, tracker
from jobloop.term import say

FUNNEL = ("Applied", "Interview", "Offer", "Hired")
OUTCOMES = tuple(s for s in tracker.STATUSES if s in tracker.FINAL)           # the tracker owns which statuses are final
REJECTIONS = ("rejected", "no_response")
BUCKETS = {"drafted": "Drafted", "applied": "Active", "interview": "Interview", "offer": "Offer", "hired": "Hired"}
UNRECOGNISED = "Unrecognised"
DEADLINE_HORIZON_DAYS = 30
NOTE_WIDTH = 80
DASH = "—"
OUTPUT = "report.html"


def esc(value: object) -> str:
    """Text for HTML, attribute values included: & < > \" and ' all escaped."""
    return html.escape(str(value), quote=True)


def bucket_of(status: object) -> str:
    """The report bucket of a stored status. Final ones other than hired are Closed; a word that is no status at all
    (`interview_only` is the archive's word, not the tracker's) is Unrecognised and is named in the report."""
    canonical = tracker.normalize_status(status)
    return UNRECOGNISED if canonical is None else BUCKETS.get(canonical, "Closed")


@dataclass(frozen=True)
class Application:
    row: tracker.Row
    outcome: archive.Outcome | None
    quiet: int | None

    @property
    def status(self) -> str | None:
        return tracker.normalize_status(self.row.status)

    @property
    def sent(self) -> bool:
        return self.status != "drafted"


def applications(me: Path, today: date) -> tuple[tuple[Application, ...], tuple[str, ...]]:
    """Every tracker row with its outcome.md and days quiet, and a line for each outcome.md that could not be read."""
    out, problems = [], []
    for row in tracker.read_rows(Path(me) / "tracker.csv"):
        try:
            outcome = stale.load_outcome(Path(me), row)
        except ValueError as error:
            outcome = None
            problems.append(f"{row.key or row.company}: outcome.md not used ({error}); its stages are missing from the funnel")
        out.append(Application(row, outcome, stale.quiet(row, today, outcome)))
    return tuple(out), tuple(problems)


# ── numbers ──
def reached(app: Application) -> tuple[bool, bool, bool, bool]:
    """(applied, interview, offer, hired) for a sent application: a stage counts when the status implies it or the
    outcome.md records an interview stage; a later stage implies the earlier ones."""
    status = app.status
    hired = status == "hired"
    offer = hired or status in ("offer", "offer_declined")
    interview = offer or status == "interview" or bool(app.outcome and app.outcome.stages)
    return app.sent, interview, offer, hired


def funnel(apps) -> dict[str, int]:
    counts = [0, 0, 0, 0]
    for app in apps:
        if app.sent:
            counts = [c + r for c, r in zip(counts, reached(app))]
    return dict(zip(FUNNEL, counts))


def rejection_rate(apps) -> float | None:
    """Rejected and no_response over every sent application with a final outcome; None when there is none."""
    final = [a.status for a in apps if a.sent and a.status in tracker.FINAL]
    return sum(s in REJECTIONS for s in final) / len(final) if final else None


@dataclass(frozen=True)
class Stats:
    sent: int
    drafted: int
    open: int
    funnel: dict[str, int]
    past_screen: float | None
    rejection_rate: float | None
    outcomes: dict[str, int]
    sectors: dict[str, int]
    channels: dict[str, int]
    unrecognised: tuple[str, ...]


def stats(apps) -> Stats:
    sent = [a for a in apps if a.sent]
    f = funnel(apps)
    final = Counter(a.status for a in sent)
    return Stats(len(sent), len(apps) - len(sent), sum(a.status in ("applied", "interview", "offer") for a in sent), f,
                 f["Interview"] / f["Applied"] if f["Applied"] else None, rejection_rate(apps),
                 {s: final[s] for s in OUTCOMES},
                 dict(Counter(a.row.sector or "(none)" for a in sent).most_common()),
                 dict(Counter(a.row.channel or "(none)" for a in sent).most_common()),
                 tuple(sorted({a.row.status.strip() or "(empty)" for a in sent if a.status is None})))


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


# ── drawing ──
def bar_chart(title: str, items: dict[str, int], summary: str) -> str:
    """A horizontal bar chart as inline SVG in a chart card; it renders for one bar, and for none."""
    top = max(items.values(), default=0) or 1
    rows = []
    for i, (label, value) in enumerate(items.items()):
        y = 8 + i * 30
        width = round(300 * value / top)
        shown = label if len(label) <= 24 else label[:23] + "…"
        rows.append(f'<text x="0" y="{y + 15}" class="lbl">{esc(shown)}<title>{esc(label)}</title></text>'
                    f'<rect x="170" y="{y}" width="{width}" height="20" rx="3" class="bar c{i % 4}"/>'
                    f'<text x="{176 + width}" y="{y + 15}" class="val">{value}</text>')
    height = 16 + 30 * max(len(items), 1)
    body = "".join(rows) or f'<text x="0" y="24" class="lbl">{DASH} nothing to show yet</text>'
    return (f'<div class="card"><h3>{esc(title)}</h3><svg role="img" aria-label="{esc(summary)}" viewBox="0 0 520 {height}" '
            f'width="100%" preserveAspectRatio="xMinYMin meet">{body}</svg></div>')


def _cell(value: object) -> str:
    text = str(value).strip()
    return esc(text) if text else DASH


def _source(value: str) -> str:
    """A link only for a plain http or https URL; everything else is text."""
    text = value.strip()
    parts = urlsplit(text)
    if parts.scheme in ("http", "https") and parts.netloc and not any(c.isspace() or ord(c) < 32 for c in text):
        return f'<a href="{esc(text)}" rel="noopener noreferrer">{esc(parts.netloc)}</a>'
    return _cell(text)


def _notes(value: str) -> str:
    text = value.strip()
    if not text:
        return DASH
    shown = text if len(text) <= NOTE_WIDTH else text[:NOTE_WIDTH - 1] + "…"
    return f'<span title="{esc(text)}">{esc(shown)}</span>'


def _table(columns: tuple[str, ...], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f"<p class=\"muted\">{esc(empty)}</p>"
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


PIPELINE_COLUMNS = ("Company", "Role", "Status", "Applied", "Deadline", "Days quiet", "Follow-ups", "Notes", "Source")


def pipeline_table(apps, today: date) -> str:
    """Sent applications still open, the most quiet first (that is where attention is due)."""
    open_apps = [a for a in apps if a.status in ("applied", "interview", "offer") or (a.sent and a.status is None)]
    open_apps.sort(key=lambda a: (a.quiet is None, -(a.quiet or 0), a.row.company.casefold()))
    rows = []
    for a in open_apps:
        flag = tracker.deadline_flag(a.row.deadline, today)
        rows.append([_cell(a.row.company), _cell(a.row.role), _cell(a.row.status), _cell(a.row.date),
                     _cell(f"{a.row.deadline} {flag}" if a.row.deadline else ""),
                     DASH if a.quiet is None else str(a.quiet), str(tracker.followups_sent(a.row.notes)),
                     _notes(a.row.notes), _source(a.row.source)])
    return _table(PIPELINE_COLUMNS, rows, "No application is waiting on an answer.")


def drafted_table(apps) -> str:
    rows = [[_cell(a.row.company), _cell(a.row.role), _cell(a.row.date), _cell(a.row.deadline)]
            for a in sorted((a for a in apps if not a.sent), key=lambda a: (a.row.date, a.row.company.casefold()), reverse=True)]
    return _table(("Company", "Role", "Drafted", "Deadline"), rows, "Nothing is drafted and waiting to be sent.")


def deadlines_table(apps, today: date) -> str:
    """Deadlines still ahead (within 30 days) of open applications, soonest first, and passed deadlines of drafts never sent."""
    items = []
    for a in apps:
        due = tracker.parse_date(a.row.deadline)
        if due is None or not tracker.is_open(a.row.status):
            continue
        left = (due - today).days
        if 0 <= left <= DEADLINE_HORIZON_DAYS:
            items.append((due, a, f"in {left} days" if left else "today"))
        elif left < 0 and not a.sent:
            items.append((due, a, "passed, never sent"))
    rows = [[_cell(a.row.company), _cell(a.row.role), _cell(a.row.deadline), esc(note), _cell(a.row.status)]
            for due, a, note in sorted(items, key=lambda t: (t[0], t[1].row.company.casefold()))]
    return _table(("Company", "Role", "Deadline", "When", "Status"), rows, f"No deadline in the next {DEADLINE_HORIZON_DAYS} days.")


CSS = """
:root{--bg:#fbfaf7;--fg:#1d2330;--muted:#5d6676;--card:#ffffff;--line:#dcd8cf;--c0:#2f6fb0;--c1:#b4691f;--c2:#3b8a5f;--c3:#7a5aa6;--warn:#b3361f}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--fg:#e8e6e1;--muted:#a3a9b5;--card:#1c2028;--line:#323846;--c0:#6aa6e0;--c1:#e0a05c;--c2:#6cc293;--c3:#b79be0;--warn:#ff8a73}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:1000px;margin:0 auto}h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:32px 0 8px}h3{font-size:.95rem;margin:0 0 8px}
.muted{color:var(--muted)}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin:16px 0}
.stat,.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}.stat b{display:block;font-size:1.6rem}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}.lbl,.val{fill:var(--fg);font-size:13px}.bar.c0{fill:var(--c0)}.bar.c1{fill:var(--c1)}.bar.c2{fill:var(--c2)}.bar.c3{fill:var(--c3)}
.warn{color:var(--warn)}a{color:var(--c0)}footer{margin-top:32px;color:var(--muted);font-size:.85rem}
"""


def render(apps, problems, today: date) -> str:
    """The page. Every dynamic value goes through esc() on its way in."""
    s = stats(apps)
    cards = [("Sent", s.sent), ("Drafted", s.drafted), ("Open", s.open), ("Reached interview", s.funnel["Interview"]),
             ("Offers", s.funnel["Offer"]), ("Hired", s.funnel["Hired"])]
    charts = [
        bar_chart("Funnel, by stages reached", s.funnel,
                  "Applications that reached each stage: " + ", ".join(f"{k} {v}" for k, v in s.funnel.items())),
        bar_chart("Outcome mix", s.outcomes, "Final outcomes: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in s.outcomes.items())),
        bar_chart("By sector", s.sectors, "Sent applications by sector: " + (", ".join(f"{k} {v}" for k, v in s.sectors.items()) or "none")),
        bar_chart("By channel", s.channels, "Sent applications by channel: " + (", ".join(f"{k} {v}" for k, v in s.channels.items()) or "none")),
    ]
    notes = [*problems, *([f"Unrecognised status, counted as sent but in no bucket: {', '.join(s.unrecognised)}"] if s.unrecognised else [])]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job search report</title><style>{CSS}</style></head><body><main>
<h1>Job search report</h1><p class="muted">Generated {today.isoformat()}. Progress past the first screen: {percent(s.past_screen)}. Rejection rate: {percent(s.rejection_rate)}.</p>
<div class="stats">{''.join(f'<div class="stat"><b>{n}</b>{esc(label)}</div>' for label, n in cards)}</div>
{''.join(f'<p class="warn">{esc(n)}</p>' for n in notes)}
<h2>Open pipeline</h2>{pipeline_table(apps, today)}
<h2>Upcoming deadlines</h2>{deadlines_table(apps, today)}
<h2>Drafted, not yet sent</h2>{drafted_table(apps)}
<h2>Charts</h2><div class="charts">{''.join(charts)}</div>
<footer>Generated {today.isoformat()} from tracker.csv and the archive. Drafted rows are in no statistic. A funnel stage counts when
the status implies it or an interview stage is recorded.</footer>
</main></body></html>
"""


def summary_text(apps, path: Path) -> str:
    s = stats(apps)
    return "\n".join([f"report: {path}", f"applications sent: {s.sent}; drafted, not yet sent: {s.drafted}; open: {s.open}",
                      f"reached interview: {s.funnel['Interview']}; offers: {s.funnel['Offer']}; hired: {s.funnel['Hired']}",
                      f"progress past the first screen: {percent(s.past_screen)}; rejection rate: {percent(s.rejection_rate)}"])


def generate(me: Path, today: date) -> tuple[Path, str]:
    """Write me/report.html (replacing the last one) and return its path with the summary. Reads the tracker and archive only."""
    me = Path(me)
    apps, problems = applications(me, today)
    path = me / OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    archive.write_atomically(path, render(apps, problems, today).encode("utf-8"))
    return path, summary_text(apps, path)


# ── the command ──
def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--me", default="me", help="your personal folder (default: me); the report is written to <me>/report.html")


def main(args: argparse.Namespace, today: date | None = None) -> int:
    try:
        _, summary = generate(Path(args.me), today or date.today())
    except ValueError as error:
        say(str(error))
        return 2
    say(summary)
    return 0

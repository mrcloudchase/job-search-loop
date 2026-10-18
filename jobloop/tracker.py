"""The application tracker: one CSV row per application, in your own gitignored folder. The loop may record
an application as found or drafted. That it was applied for, or anything after, only a person says, with
`tracker set`: nothing here submits an application, and nothing the loop runs can claim one was sent."""
from __future__ import annotations

import argparse
import csv
import os
from datetime import date
from pathlib import Path

FIELDS = ["key", "date", "company", "role", "status", "fit", "source", "cv_file", "cover_file", "deadline", "notes"]
LOOP_STATUSES = ("found", "drafted")                                        # all the loop may write
STATUSES = LOOP_STATUSES + ("applied", "interview", "offer", "rejected", "withdrawn")


def rows(path: Path | str) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write(path: Path | str, table: list[dict]) -> None:
    """Whole file at once, through a temporary file, so a crash never leaves half a tracker."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with open(temp, "w", newline="") as f:
        out = csv.DictWriter(f, FIELDS, extrasaction="ignore")
        out.writeheader()
        out.writerows(table)
    os.replace(temp, path)


def record(path: Path | str, key: str, status: str, **fields: str) -> None:
    """What the loop does: add an application, or move it between the loop's own statuses."""
    if status not in LOOP_STATUSES:
        raise ValueError(f"the loop may record {' or '.join(LOOP_STATUSES)}, not {status!r}; a person sets the rest with `tracker set`")
    put(path, key, status, **fields)


def put(path: Path | str, key: str, status: str, **fields: str) -> None:
    table = rows(path)
    row = next((r for r in table if r["key"] == key), None)
    if row is None:
        row = {"key": key, "date": date.today().isoformat()}
        table.append(row)
    row.update({"status": status, **{k: str(v) for k, v in fields.items() if v not in (None, "")}})
    write(path, table)


def set_status(path: Path | str, key: str, status: str, note: str = "") -> None:
    """What a person does: say where an application stands."""
    if status not in STATUSES:
        raise SystemExit(f"status must be one of {', '.join(STATUSES)}")
    if not any(r["key"] == key for r in rows(path)):
        raise SystemExit(f"no application {key!r} in {path}")
    put(path, key, status, notes=note)


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--file", default="me/tracker.csv")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="every application and where it stands")
    s = sub.add_parser("set", help="say where an application stands (your decision, never the loop's)")
    s.add_argument("key")
    s.add_argument("status", choices=STATUSES)
    s.add_argument("--note", default="")


def main(args: argparse.Namespace) -> int:
    if args.action == "set":
        set_status(args.file, args.key, args.status, args.note)
    for r in rows(args.file):
        print(f"{r['status']:<10} {r.get('fit') or '-':>3}  {r['key']:<40} {r.get('company', '')} · {r.get('role', '')}")
    return 0

"""The engine's own memory: me/engine/engine.db (stdlib sqlite3). Runs, attempts, events, queue, proposals, judgments, playbook versions.

It holds what the other stores do not: what the loop tried, how it ended, what is waiting for a person, what the judges
said, and which playbook version was active. The facts of the search stay in the stores that own them (postings,
tracker, archive). Every change is one transaction, so a process killed anywhere leaves a consistent file.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT, finished TEXT, mode TEXT, config TEXT,
  terminal TEXT, spent_usd REAL DEFAULT 0, tokens INTEGER DEFAULT 0, digest TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, run_id INTEGER, stage TEXT, item TEXT, reason TEXT, harness TEXT,
  started TEXT, finished TEXT, terminal TEXT, cost_usd REAL DEFAULT 0, tokens INTEGER DEFAULT 0, failure_digest TEXT,
  failures TEXT, notes TEXT);
CREATE INDEX IF NOT EXISTS attempts_item ON attempts(stage, item);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT, kind TEXT, item TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS queue(item TEXT PRIMARY KEY, status TEXT, path TEXT, created TEXT, updated TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY, created TEXT, kind TEXT, status TEXT, source TEXT, payload TEXT, decided TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS judgments(id INTEGER PRIMARY KEY, at TEXT, item TEXT, subject TEXT, judge TEXT, score REAL, detail TEXT, version INTEGER);
CREATE TABLE IF NOT EXISTS playbook(version INTEGER PRIMARY KEY, created TEXT, parent INTEGER, change TEXT, status TEXT, activated TEXT, note TEXT);
"""


def now_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()


def digest_of(failures: list[str]) -> str:
    """A short fingerprint of a failure list with line numbers and counts ignored, so 'the same failure again' is detectable."""
    import re
    flat = "\n".join(sorted(re.sub(r"\d+", "#", f.strip().splitlines()[0] if f.strip() else "") for f in failures))
    return hashlib.sha1(flat.encode()).hexdigest()[:12] if flat else ""


class Store:
    def __init__(self, me: Path | str):
        self.me = Path(me)
        self.path = self.me / "engine" / "engine.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # ── runs ──
    def start_run(self, mode: str, config: str, now: datetime | None = None) -> int:
        with self.tx() as db:
            return db.execute("INSERT INTO runs(started, mode, config) VALUES(?,?,?)", (now_stamp(now), mode, config)).lastrowid

    def finish_run(self, run_id: int, terminal: str, spent: float, tokens: int, digest: str, summary: dict, now: datetime | None = None) -> None:
        with self.tx() as db:
            db.execute("UPDATE runs SET finished=?, terminal=?, spent_usd=?, tokens=?, digest=?, summary=? WHERE id=?",
                       (now_stamp(now), terminal, spent, tokens, digest, json.dumps(summary), run_id))

    def runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # ── attempts ──
    def record_attempt(self, run_id: int, stage: str, item: str, reason: str, harness: str, started: str, finished: str,
                       terminal: str, cost: float, tokens: int, failures: list[str], notes: list[str]) -> int:
        with self.tx() as db:
            return db.execute(
                "INSERT INTO attempts(run_id, stage, item, reason, harness, started, finished, terminal, cost_usd, tokens, failure_digest, failures, notes)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, stage, item, reason, harness, started, finished, terminal, cost, tokens, digest_of(failures),
                 json.dumps(failures[:20]), json.dumps(notes[:20]))).lastrowid

    def attempts(self, stage: str, item: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM attempts WHERE stage=? AND item=? ORDER BY id", (stage, item)).fetchall()

    def attempts_in_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY id", (run_id,)).fetchall()

    def last_success(self, stage: str, item: str = "*") -> str | None:
        row = self.db.execute("SELECT finished FROM attempts WHERE stage=? AND item=? AND terminal IN ('success','no_op') "
                              "ORDER BY id DESC LIMIT 1", (stage, item)).fetchone()
        return row["finished"] if row else None

    def has_success(self, stage: str, item: str) -> bool:
        return self.db.execute("SELECT 1 FROM attempts WHERE stage=? AND item=? AND terminal='success' LIMIT 1", (stage, item)).fetchone() is not None

    # ── events (what the person did, what the loop observed) ──
    def add_event(self, kind: str, item: str = "", data: Any = None, now: datetime | None = None) -> int:
        with self.tx() as db:
            return db.execute("INSERT INTO events(at, kind, item, data) VALUES(?,?,?,?)",
                              (now_stamp(now), kind, item, json.dumps(data))).lastrowid

    def events(self, kind: str | None = None, item: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if kind:
            sql, args = sql + " AND kind=?", args + [kind]
        if item:
            sql, args = sql + " AND item=?", args + [item]
        return self.db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()

    # ── key/value ──
    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value: Any) -> None:
        with self.tx() as db:
            db.execute("INSERT INTO kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    # ── the person's queue ──
    def queue_put(self, item: str, status: str, path: str, data: dict | None = None, now: datetime | None = None) -> None:
        if status not in ("ready", "needs_decision", "blocked", "done"):
            raise ValueError(f"queue status is ready, needs_decision, blocked or done, not {status!r}")
        stamp = now_stamp(now)
        with self.tx() as db:
            db.execute("INSERT INTO queue(item, status, path, created, updated, data) VALUES(?,?,?,?,?,?) ON CONFLICT(item) DO UPDATE SET "
                       "status=excluded.status, path=excluded.path, updated=excluded.updated, data=excluded.data",
                       (item, status, path, stamp, stamp, json.dumps(data or {})))

    def queue(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.db.execute("SELECT * FROM queue WHERE status=? ORDER BY created", (status,)).fetchall()
        return self.db.execute("SELECT * FROM queue ORDER BY created").fetchall()

    def queue_item(self, item: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM queue WHERE item=?", (item,)).fetchone()

    # ── proposals (things a person decides) ──
    def add_proposal(self, kind: str, payload: dict, source: str = "", now: datetime | None = None) -> int:
        with self.tx() as db:
            return db.execute("INSERT INTO proposals(created, kind, status, source, payload) VALUES(?,?,?,?,?)",
                              (now_stamp(now), kind, "pending", source, json.dumps(payload))).lastrowid

    def proposals(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.db.execute("SELECT * FROM proposals WHERE status=? ORDER BY id", (status,)).fetchall()
        return self.db.execute("SELECT * FROM proposals ORDER BY id").fetchall()

    def decide_proposal(self, proposal_id: int, status: str, note: str = "", now: datetime | None = None) -> None:
        if status not in ("accepted", "rejected", "auto_applied", "withdrawn"):
            raise ValueError(f"a proposal ends accepted, rejected, auto_applied or withdrawn, not {status!r}")
        with self.tx() as db:
            db.execute("UPDATE proposals SET status=?, decided=?, note=? WHERE id=?", (status, now_stamp(now), note, proposal_id))

    # ── judgments ──
    def add_judgment(self, item: str, subject: str, judge: str, score: float, detail: dict, version: int = 0,
                     now: datetime | None = None) -> int:
        with self.tx() as db:
            return db.execute("INSERT INTO judgments(at, item, subject, judge, score, detail, version) VALUES(?,?,?,?,?,?,?)",
                              (now_stamp(now), item, subject, judge, score, json.dumps(detail), version)).lastrowid

    def judgments(self, item: str | None = None, subject: str | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM judgments WHERE 1=1", []
        if item:
            sql, args = sql + " AND item=?", args + [item]
        if subject:
            sql, args = sql + " AND subject=?", args + [subject]
        return self.db.execute(sql + " ORDER BY id", args).fetchall()

    # ── playbook versions (the files live in me/playbook/) ──
    def playbook_add(self, version: int, parent: int | None, change: dict, status: str = "candidate", now: datetime | None = None) -> None:
        with self.tx() as db:
            db.execute("INSERT INTO playbook(version, created, parent, change, status) VALUES(?,?,?,?,?)",
                       (version, now_stamp(now), parent, json.dumps(change), status))

    def playbook_set(self, version: int, status: str, note: str = "", now: datetime | None = None) -> None:
        with self.tx() as db:
            if status == "active":
                db.execute("UPDATE playbook SET status='superseded' WHERE status='active'")
                db.execute("UPDATE playbook SET status=?, activated=?, note=? WHERE version=?", (status, now_stamp(now), note, version))
            else:
                db.execute("UPDATE playbook SET status=?, note=? WHERE version=?", (status, note, version))

    def playbook_versions(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM playbook ORDER BY version").fetchall()

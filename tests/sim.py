"""Helpers for stage and loop tests: a populated `me` folder, and an engine Context wired to the fake harness-interface server."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from fake_server import FakeServer
from jobloop import archive, rank, tracker
from jobloop.engine import config as cfgmod, playbook, snapshot
from jobloop.engine.gateway import Gateway, Ledger
from jobloop.engine.stage import Context
from jobloop.engine.store import Store

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
PROFILE = ROOT / "fixtures" / "profile-cph.md"


def posting(key: str, company: str, title: str, *, status="new", url=None, score=None, verdict=None, gates=None, deadline=None, board="freehire", **extra) -> dict:
    entry = {"key": key, "board": board, "company": company, "title": title, "url": url or f"https://{company.lower().replace(' ', '')}.example/jobs/{key}",
             "status": status, "locations": ["Copenhagen"], "alt_urls": [], "tags": [], "first_seen": "2026-10-10", "last_seen": "2026-10-19",
             "posted_date": "2026-10-15", "deadline": deadline, "source": "board"}
    if score is not None:
        entry.update(rank_score=score, rank_verdict=verdict or "good", rank_date="2026-10-18", gates=gates or [], strengths=[], gaps=[])
    entry.update(extra)
    return entry


def make_me(tmp_path: Path, postings: list[dict] | None = None, tracker_rows: list[tuple] | None = None, profile: bool = True) -> Path:
    """A candidate's folder: the fictional profile, postings in the given states, tracker rows (company, role, status)."""
    me = tmp_path / "me"
    me.mkdir(parents=True, exist_ok=True)
    if profile:
        (me / "profile.md").write_text(PROFILE.read_text())
    if postings:
        (me / "state").mkdir(exist_ok=True)
        (me / "state" / "postings.json").write_text(json.dumps({"postings": {p["key"]: p for p in postings}}))
    for company, role, status in tracker_rows or ():
        tracker.record_drafted(me / "tracker.csv", company=company, role=role, today=date(2026, 10, 5), cv_file="cv.pdf", cover_letter_file="cover.pdf")
        if status != "drafted":
            tracker.set_status(me / "tracker.csv", archive.key(company, role), status, by_person=True, on=date(2026, 10, 8), me=me)
    return me


def write_fit(me: Path, key: str, total: int = 80, gates=None, requirements=None) -> None:
    folder = me / "fit"
    folder.mkdir(exist_ok=True)
    (folder / f"{rank.fit_key(key)}.json").write_text(json.dumps({
        "key": key, "total": total, "band": "good", "vetoes": [], "gates": gates or [], "requirements": requirements or [],
        "scores": {"technical": total, "experience": total, "behavioural": total, "career": total}, "strengths": [], "gaps": []}))


def context(tmp_path: Path, replies=None, *, me: Path | None = None, config=None, now: datetime = NOW) -> tuple[Context, FakeServer]:
    """An engine Context for one action, on a fake server that answers `replies` in order (see tests/fake_server.py)."""
    me = me or make_me(tmp_path)
    config = config or cfgmod.Config().validate()
    server = FakeServer(replies or [])
    store = Store(me)
    ledger = Ledger(config)
    gateway = Gateway(config, store, ledger, me, server.transport())
    run_id = store.start_run("test", "{}", now)
    snap = snapshot.take(me, store, config, now)
    pb = playbook.load(me, store, now=now)
    return Context(me=me, config=config, store=store, gateway=gateway, snapshot=snap, run_id=run_id, playbook=pb, say=lambda s: None), server

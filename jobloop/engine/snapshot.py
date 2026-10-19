"""What the loop sees at the start of every action: one read-only view assembled from the stores that own the facts.

Nothing here is cached between actions; a person's edit to the tracker or the queue is seen on the very next look.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .. import archive, rankstate, state as postings_store, tracker
from ..rank import fit_key
from .config import Config
from .store import Store


@dataclass
class Snapshot:
    me: Path
    now: datetime
    config: Config
    store: Store
    postings: dict[str, dict] = field(default_factory=dict)
    rows: tuple[tracker.Row, ...] = ()
    profile: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def today(self) -> date:
        return self.now.date()

    @property
    def tracker_path(self) -> Path:
        return self.me / "tracker.csv"

    @property
    def row_by_key(self) -> dict[str, tracker.Row]:
        return {r.key: r for r in self.rows if r.key}

    def fit(self, key: str) -> dict | None:
        """The stored fit evaluation of a posting (me/fit/<name>.json), or None."""
        path = self.me / "fit" / f"{fit_key(key)}.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def application_key(self, entry: dict) -> str:
        try:
            return archive.key(entry.get("company") or "", entry.get("title") or "")
        except ValueError:
            return ""

    def state_of(self, key: str) -> str:
        """new, unreachable, expired, gave_up, vetoed, below_floor, shortlisted, skipped, drafted, or (once the person moved the
        application on) the tracker's own word for where it is: applied, interview, offer, hired, rejected, no_response, ..."""
        entry = self.postings[key]
        app = self.application_key(entry)
        if app and app in self.row_by_key:
            return tracker.normalize_status(self.row_by_key[app].status) or "applied"
        status = entry.get("status", "new")
        if status == "ranked":
            if rankstate.vetoes(entry):
                return "vetoed"
            score = entry.get("rank_score")
            return "below_floor" if not isinstance(score, (int, float)) or score < self.config.min_fit else "shortlisted"
        return status

    def keys_in(self, *states: str) -> list[str]:
        return [k for k in self.postings if self.state_of(k) in states]

    def config_disabled(self) -> set[str]:
        """Stages the person switched off in loop.json (`disabled_stages`)."""
        return set(getattr(self.config, "disabled_stages", ()) or ())

    @property
    def profile_ready(self) -> bool:
        return bool(self.profile.strip())


def fingerprint(me: Path) -> dict:
    """What a pass must not change except through the rules: the profile's bytes and the tracker's rows."""
    me = Path(me)
    profile = me / "profile.md"
    try:
        rows = tracker.read_rows(me / "tracker.csv")
    except (OSError, ValueError):           # an unreadable tracker is reported by the snapshot; it is not a pass-ending surprise
        rows = ()
    return {"profile": hashlib.sha256(profile.read_bytes()).hexdigest() if profile.exists() else "",
            "rows": {r.key or f"row{i}": (r.status, r.company, r.role) for i, r in enumerate(rows)}}


def take(me: Path | str, store: Store, config: Config, now: datetime | None = None) -> Snapshot:
    me = Path(me)
    now = now or datetime.now(timezone.utc)
    snap = Snapshot(me=me, now=now, config=config, store=store)
    try:
        snap.postings = postings_store.load(me)
    except postings_store.StateCorrupt as error:
        snap.problems.append(str(error))
    try:
        snap.rows = tracker.read_rows(snap.tracker_path)
    except (OSError, ValueError) as error:
        snap.problems.append(f"tracker: {error}")
    profile = me / "profile.md"
    snap.profile = profile.read_text() if profile.exists() else ""
    return snap

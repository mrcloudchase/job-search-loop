"""The contract between the engine and a stage: a Spec, `propose` and `run`, and the Context a run receives."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

from ..term import say
from .config import Config
from .gateway import Gateway
from .snapshot import Snapshot
from .spec import Action, Outcome, Spec
from .store import Store


@dataclass
class Context:
    me: Path
    config: Config
    store: Store
    gateway: Gateway
    snapshot: Snapshot
    run_id: int
    playbook: "object | None" = None            # engine.playbook.Playbook (the active version), or None before one exists
    say: Callable[[str], None] = say
    notes: list[str] = field(default_factory=list)

    @property
    def now(self) -> datetime:
        return self.snapshot.now

    def done_this_run(self, stage: str) -> int:
        """How many successful actions this stage has finished in the current pass."""
        return sum(1 for a in self.store.attempts_in_run(self.run_id) if a["stage"] == stage and a["terminal"] == "success")


class Stage(Protocol):
    spec: Spec

    def propose(self, snap: Snapshot) -> list[Action]: ...

    def run(self, action: Action, ctx: Context) -> Outcome: ...


def due(snap: Snapshot, stage: str, hours: float, key: str = "*") -> bool:
    """True when a cadence stage has not succeeded within `hours` (never succeeded counts as due)."""
    last = snap.store.last_success(stage, key)
    if not last:
        return True
    then = datetime.fromisoformat(last)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return snap.now - then >= timedelta(hours=hours)

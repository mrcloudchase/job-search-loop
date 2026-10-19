"""What a stage is: a loop spec, the actions it proposes, and the outcome of one bounded step.

The vocabulary of docs/loop-spec.md as plain dataclasses, so the engine, the stages and the tests share one definition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TERMINALS = ("success", "no_op", "blocked", "stalled", "exhausted")
SHAPES = ("solo", "maker-checker", "panel")
LEVELS = {1: "deterministic", 2: "rule or constraint", 3: "delayed field truth", 4: "model judge", 5: "human checkpoint"}
ROLES = ("drafter", "reviewer", "researcher", "judge", "builder", "planner")


@dataclass(frozen=True)
class Brakes:
    max_attempts: int = 3               # attempts per (stage, item) before it is parked as blocked
    repeat_stall: int = 2               # the same failure digest this many times in a row is `stalled`
    share: float = 1.0                  # the most of a pass's budget one action of this stage may use
    backoff_hours: float = 6.0          # how long a stalled or blocked item rests before another attempt


@dataclass(frozen=True)
class Trigger:
    kind: str = "state"                 # "state": runs when items are in the right state; "every": when a cadence is due
    hours: float = 0.0                  # for "every"

    def __post_init__(self):
        if self.kind not in ("state", "every"):
            raise ValueError(f"trigger kind is state or every, not {self.kind!r}")


@dataclass(frozen=True)
class Spec:
    name: str
    goal: str
    trigger: Trigger = Trigger()
    shape: str = "solo"
    levels: tuple[int, ...] = (1,)
    roles: tuple[str, ...] = ()
    brakes: Brakes = Brakes()
    human_gate: bool = False
    rank: int = 50                      # funnel position: later stages finish work in progress before earlier ones start more

    def __post_init__(self):
        if self.shape not in SHAPES:
            raise ValueError(f"{self.name}: shape is one of {SHAPES}, not {self.shape!r}")
        if not set(self.levels) <= set(LEVELS):
            raise ValueError(f"{self.name}: verification levels are {sorted(LEVELS)}")
        if not set(self.roles) <= set(ROLES):
            raise ValueError(f"{self.name}: roles are {ROLES}")


@dataclass(frozen=True)
class Action:
    stage: str
    key: str                            # the item's key; "*" for a stage that works on the whole search
    reason: str
    priority: float = 0.0               # higher first, within the policy's ordering rules
    payload: dict[str, Any] = field(default_factory=dict)
    deadline: str | None = None         # ISO date a person is waiting on, if any


@dataclass
class Outcome:
    terminal: str
    notes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)     # verbatim check failures: their digest drives the no-progress brake
    cost_usd: float = 0.0
    tokens: int = 0                                       # tokens the harnesses reported without a dollar cost
    produced: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.terminal not in TERMINALS:
            raise ValueError(f"terminal is one of {TERMINALS}, not {self.terminal!r}")

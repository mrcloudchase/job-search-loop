"""The one door to harness-interface: roles map to harness ladders, every turn is costed against the pass's ledger.

A stage never names a server or a budget. It asks the gateway for a `Turns` for one action and for the harness a role should
use for this item right now; the gateway counts what was spent, stops model turns at the ceiling, and rotates a stalled
item to the next harness on the ladder.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from ..turns import Api, Turns
from .config import Config
from .store import Store


class Ledger:
    """Dollars and tokens spent in one pass, and the deadline. Unpriced tokens count against the dollar ceiling at a set rate."""

    def __init__(self, config: Config, started: float | None = None):
        self.config = config
        self.started = started if started is not None else time.time()
        self.usd = 0.0
        self.tokens = 0
        self.stop = ""

    def add(self, usd: float, tokens: int) -> None:
        self.usd += usd
        self.tokens += tokens

    @property
    def spent_usd(self) -> float:
        return self.usd + self.tokens / 1e6 * self.config.unpriced_usd_per_mtok

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.config.pass_budget_usd - self.spent_usd)

    @property
    def remaining_minutes(self) -> float:
        return max(0.0, self.config.pass_minutes - (time.time() - self.started) / 60)

    def exhausted(self) -> str:
        if self.stop:
            return self.stop
        if self.remaining_minutes <= 0:
            return "time"
        if self.remaining_usd <= 0 or self.tokens >= self.config.pass_max_tokens:
            return "budget"
        return ""


class LedgerTurns(Turns):
    """Turns that also draw on the pass's ledger and stop when it is exhausted."""

    def __init__(self, api: Api, log: Path | str, minutes: float, budget: float, ledger: Ledger):
        super().__init__(api, log, minutes, budget)
        self.ledger = ledger

    def stopped(self) -> str:
        return super().stopped() or self.ledger.exhausted()

    def turn(self, *args, **kwargs):
        before = (self.spent, self.unpriced_tokens)
        record = super().turn(*args, **kwargs)
        self.ledger.add(self.spent - before[0], self.unpriced_tokens - before[1])
        return record


class Gateway:
    def __init__(self, config: Config, store: Store, ledger: Ledger, me: Path | str, transport: httpx.BaseTransport | None = None):
        self.config, self.store, self.ledger, self.me = config, store, ledger, Path(me)
        self.api = Api(config.server, transport)

    def harness(self, role: str, stage: str, item: str, exclude: str = "") -> str:
        """The harness this role uses for this item now: the first on its ladder, then the next after each stalled attempt.
        `exclude` names a harness the answer must differ from (a maker-checker pair is never one harness)."""
        ladder = self.config.ladder(role)
        stalls = sum(1 for a in self.store.attempts(stage, item) if a["terminal"] == "stalled")
        order = ladder[min(stalls, len(ladder) - 1):] + ladder[:min(stalls, len(ladder) - 1)]
        for name in order:
            if not exclude or name.split("/")[0] != exclude.split("/")[0]:
                return name
        return order[0]

    def others(self, role: str, exclude: str) -> list[str]:
        """The role's ladder without harnesses of the excluded family: a judge panel never includes the maker's harness."""
        pool = [h for h in self.config.ladder(role) if h.split("/")[0] != exclude.split("/")[0]]
        return pool or list(self.config.ladder(role))

    def turns(self, stage: str, item: str, max_usd: float | None = None, max_minutes: float | None = None) -> LedgerTurns:
        usd = min(max_usd or self.config.action_budget_usd, self.config.action_budget_usd, self.ledger.remaining_usd)
        minutes = min(max_minutes or self.config.action_minutes, self.config.action_minutes, self.ledger.remaining_minutes)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in item)[:80] or "all"
        return LedgerTurns(self.api, self.me / "engine" / "logs" / stage / f"{safe}.jsonl", max(minutes, 0.01), max(usd, 0.01), self.ledger)

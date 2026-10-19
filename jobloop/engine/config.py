"""me/loop.json: the person's standing authorisation for the loop. Written with documented defaults on first use.

Every pass records exactly what this file authorised. Unknown keys are refused, so a typo cannot silently turn a limit off.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

NAME = "loop.json"
DEFAULT_LADDERS = {
    "drafter": ["claude-deepseek/deepseek-chat", "codex"],
    "reviewer": ["codex", "claude-reviewer/deepseek-chat"],
    "researcher": ["claude-research/deepseek-chat"],
    "judge": ["codex", "claude-reviewer/deepseek-chat"],
    "builder": ["claude-portal/deepseek-chat"],
    "planner": ["claude-deepseek/deepseek-chat", "codex"],
}


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    server: str = "http://127.0.0.1:8000"
    pass_minutes: float = 60.0              # a pass's deadline
    pass_budget_usd: float = 10.0           # a pass's dollar ceiling (as the harnesses report it)
    pass_max_tokens: int = 6_000_000        # a pass's ceiling on tokens that were reported without a dollar cost
    unpriced_usd_per_mtok: float = 1.0      # what unpriced tokens count as, against the dollar ceiling
    action_budget_usd: float = 3.0          # the most any single action may spend
    action_minutes: float = 30.0
    max_new_drafts: int = 3                 # new applications drafted per pass
    min_fit: int = 60                       # a posting must score at least this to be drafted
    best_of: int = 2                        # drafts made per posting, judged against each other (1 = no judging between drafts)
    judge_drafts: bool = True
    judge_triage: bool = True
    followups: bool = True
    ladders: dict = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_LADDERS.items()})
    company_domains: dict = field(default_factory=dict)      # company name -> its own domain, for company research
    form_questions: str = ""                # a file of application-form questions to answer for each queued application
    discover_every_hours: float = 12.0
    maintain_every_hours: float = 24.0
    learn_every_hours: float = 24.0
    upskill_every_hours: float = 168.0
    recency_days: int = 14
    search_limit: int = 20
    acknowledge_terms: bool = False         # the person's acknowledgement for boards that have terms (personal, low-volume use)
    self_improve: bool = True               # the loop may change its own playbook (versioned, bounded, rolled back when worse)
    rollback_margin: float = 0.15           # a playbook version worse than its parent by this fraction is rolled back
    rollback_after: int = 5                 # applications before a version is judged
    disabled_stages: list = field(default_factory=list)      # stage names the loop must not run
    notify_file: str = ""                   # if set, the digest is also copied here

    def validate(self) -> "Config":
        for name in ("pass_minutes", "pass_budget_usd", "action_budget_usd", "action_minutes"):
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name} must be positive")
        if not 0 <= self.min_fit <= 100:
            raise ConfigError("min_fit is 0 to 100")
        if not 1 <= self.best_of <= 4:
            raise ConfigError("best_of is 1 to 4")
        if self.max_new_drafts < 0:
            raise ConfigError("max_new_drafts cannot be negative")
        for role, ladder in self.ladders.items():
            if not isinstance(ladder, list) or not ladder or not all(isinstance(h, str) and h for h in ladder):
                raise ConfigError(f"ladders.{role} must be a non-empty list of harness names")
        for role in DEFAULT_LADDERS:
            self.ladders.setdefault(role, list(DEFAULT_LADDERS[role]))
        return self

    def ladder(self, role: str) -> list[str]:
        return self.ladders[role]

    def as_text(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"


def load(me: Path | str) -> Config:
    path = Path(me) / NAME
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(Config().as_text())
        return Config().validate()
    try:
        data = json.loads(path.read_text())
    except ValueError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from None
    known = {f.name for f in fields(Config)}
    unknown = sorted(set(data) - known) if isinstance(data, dict) else ["(not an object)"]
    if unknown:
        raise ConfigError(f"{path} has keys the loop does not know: {', '.join(unknown)}")
    base = Config()
    for key, value in data.items():
        setattr(base, key, value)
    return base.validate()

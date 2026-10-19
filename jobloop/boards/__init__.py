"""Job-board adapters, one module per board, all keeping `boards.contract`. `load(name)` imports one by name, so the
list of boards a person has on or off is just names in their config. A person's own board (built by jobloop/addboard.py)
is found only through that person's folder: `load(name, me)` resolves it if, and only if, `<me>/boards.json` registers it."""
from __future__ import annotations

import importlib
from pathlib import Path

BUILT_IN = ("freehire", "linkedin", "jobindex", "jobnet", "jobbank", "jobdanmark")


def load(name: str, me: Path | str | None = None):
    if name in BUILT_IN:
        return importlib.import_module(f"{__name__}.{name}")
    if me is not None and name not in ("", ".", ".."):
        from . import external
        try:
            return external.load_board(me, name)
        except ValueError as e:
            if "no external board" not in str(e):
                raise
    raise ValueError(f"no built-in board {name!r}; the boards are {', '.join(BUILT_IN)}")


def names(me: Path | str | None = None) -> tuple[str, ...]:
    """Every board that can be run: the built-in ones, then this person's registered external ones."""
    if me is None:
        return BUILT_IN
    from . import external
    return BUILT_IN + external.names(me)

"""Job-board adapters, one module per board, all keeping `boards.contract`. `load(name)` imports one by name, so the
list of boards a person has on or off is just names in their config."""
from __future__ import annotations

import importlib

BUILT_IN = ("freehire", "linkedin", "jobindex", "jobnet", "jobbank", "jobdanmark")


def load(name: str):
    if name not in BUILT_IN:
        raise ValueError(f"no built-in board {name!r}; the boards are {', '.join(BUILT_IN)}")
    return importlib.import_module(f"{__name__}.{name}")

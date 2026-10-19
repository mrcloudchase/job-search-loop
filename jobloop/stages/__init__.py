"""The stages of the loop. Each module exposes `STAGE`: an object with `spec`, `propose(snapshot)` and `run(action, ctx)`.

`all_stages()` imports every stage named in ORDER. A stage module that does not exist yet is skipped only when the engine
is being tested with a partial set; a real pass (`jobloop run`) refuses to start unless every stage in ORDER loads.
"""
from __future__ import annotations

import importlib

ORDER = ("onboard", "boards", "discover", "triage", "locate", "research", "draft", "package", "followup", "interview", "offer",
         "learn", "upskill", "maintain")


def all_stages(strict: bool = True) -> list:
    found = []
    for name in ORDER:
        try:
            found.append(importlib.import_module(f"{__name__}.{name}").STAGE)
        except ModuleNotFoundError as error:
            if strict or error.name != f"{__name__}.{name}":
                raise
    return found

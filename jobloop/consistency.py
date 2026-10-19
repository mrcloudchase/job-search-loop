"""The CV and the letter agree: the letter never gives an employer a job title the profile does not, and every number and
date it states is in the CV or the profile. Pure functions over text; findings are messages for the drafter.

The gate (verify.py) uses `titles` for the contradiction, because grounding already holds the letter's numbers and dates to the
profile; `check` runs both, for a caller that has only the two documents."""
from __future__ import annotations

import re
from datetime import date

from jobloop import grounding
from jobloop.text import flat


def titles(letter_text: str, profile: str) -> list[str]:
    """A sentence naming an employer together with another role's title, and not its own, contradicts the CV."""
    known = grounding.roles(profile)
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", letter_text):
        s = flat(sentence)
        for role in known:
            if flat(role.employer) not in s or flat(role.title) in s:
                continue
            other = next((o.title for o in known if o is not role and flat(o.title) in s and flat(o.title) not in flat(role.title)), None)
            if other:
                found.append(f'"{sentence.strip()}" gives {role.employer} the title "{other}", but the CV and profile say '
                             f'"{role.title}": the letter and the CV must not contradict each other')
    return found


def check(cv_text: str, letter_text: str, profile: str, extra: tuple[str, ...] = (), today: date | None = None) -> list[str]:
    """Contradictory titles, then any number or date the letter states that neither the CV nor the profile has."""
    stated = [m for m in grounding.check(letter_text, [cv_text, profile, *extra], today)
              if "number that is in none" in m or "date that is in none" in m or "year that is in none" in m]
    return titles(letter_text, profile) + stated

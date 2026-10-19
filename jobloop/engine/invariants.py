"""Tripwires: what a pass must never do, checked from the outside after every action and at the end of every pass.

A violation aborts the pass and is written into the digest. These are not the only guards (each stage has its own); they
are the ones that hold even if a stage is wrong.
"""
from __future__ import annotations

from .. import rankstate
from .snapshot import Snapshot

HUMAN_ONLY = frozenset({"applied", "interview", "offer", "hired", "rejected", "no_response", "offer_declined", "withdrawn"})


class InvariantViolation(RuntimeError):
    pass


def violations(before: dict, after: dict, snap: Snapshot | None = None) -> list[str]:
    """Differences between two fingerprints (snapshot.fingerprint) that no pass may cause."""
    found = []
    if before["profile"] != after["profile"]:
        found.append("profile.md changed during a pass: the loop never writes the profile (a person accepts a proposal)")
    for key, (status, company, role) in before["rows"].items():
        if key not in after["rows"]:
            found.append(f"tracker row {key} disappeared: the loop never deletes a row")
        elif after["rows"][key][0] != status:
            found.append(f"tracker row {key} changed from {status} to {after['rows'][key][0]}: only a person changes a status")
    for key, (status, company, role) in after["rows"].items():
        if key not in before["rows"] and status != "drafted":
            found.append(f"tracker row {key} was added as {status}: the loop writes drafted and nothing else")
    if snap is not None:
        for key, (status, company, role) in after["rows"].items():
            if key in before["rows"] or status != "drafted":
                continue
            for pkey, entry in snap.postings.items():
                if snap.application_key(entry) == key and rankstate.vetoes(entry):
                    found.append(f"{key} was drafted for a vetoed posting ({'; '.join(rankstate.vetoes(entry))[:120]})")
    return found


def enforce(before: dict, after: dict, snap: Snapshot | None = None) -> None:
    found = violations(before, after, snap)
    if found:
        raise InvariantViolation("; ".join(found))

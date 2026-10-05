"""Committee vote tally (ADR-08). Lives in ``fcrm_contracts`` rather than
in either S5 (``fcrm_decision``, the system of record for votes) or H1
(``fcrm_caseflow``, which only reacts to the tallied outcome) because both
depend on this package and neither should depend on the other — a
service and a worker should never import each other directly.

The rule: the outcome is whichever option two of the three votes agree
on. Anything else — a three-way split, or any count other than exactly
three distinct votes reaching tally — is ``no_majority``. No outcome is
ever produced by default; a split is reported, never silently resolved
to approve, reject or defer.
"""

from __future__ import annotations

from typing import Optional


def tally_votes(votes: list[str]) -> tuple[Optional[str], bool]:
    """Returns ``(outcome, no_majority)``; exactly one of
    ``outcome is not None`` / ``no_majority`` holds.
    """
    if len(votes) != 3:
        return None, True
    counts: dict[str, int] = {}
    for v in votes:
        counts[v] = counts.get(v, 0) + 1
    winner = max(counts.items(), key=lambda kv: kv[1])
    if winner[1] >= 2:
        return winner[0], False
    return None, True

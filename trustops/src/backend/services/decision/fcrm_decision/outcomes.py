"""Turning three cast votes into an outcome, or a no-majority re-vote.

This module is the thin, pure wiring between ``fcrm_contracts.voting.
tally_votes`` and the decision-service's own vote-round bookkeeping — it
owns nothing about *storage* (one vote per member per round, votes hidden
until all are cast) since that needs a database's atomicity to be real;
it owns the part that is pure arithmetic once the three votes exist.
"""

from __future__ import annotations

from typing import Optional

from fcrm_contracts.enums import Outcome
from fcrm_contracts.voting import tally_votes
from pydantic import BaseModel, ConfigDict


class TallyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Optional[Outcome]
    no_majority: bool
    round: int
    next_round: Optional[int] = None  # set only when no_majority


def tally_round(votes: list[str], round_number: int) -> TallyResult:
    """``votes`` must be exactly the three cast options for this round, in
    any order — the tally does not care who cast which, only the
    multiset of options (member identity and hidden/revealed state are
    the caller's concern).
    """
    outcome_str, no_majority = tally_votes(votes)
    return TallyResult(
        outcome=Outcome(outcome_str) if outcome_str else None,
        no_majority=no_majority,
        round=round_number,
        next_round=(round_number + 1) if no_majority else None,
    )

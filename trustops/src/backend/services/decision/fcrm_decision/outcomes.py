"""Turning three cast votes into an outcome, or a no-majority re-vote.

This is the thin, pure wiring between :func:`fcrm_contracts.voting.tally`
and a round number. It owns nothing about *storage* (one vote per member per
round, votes hidden until all are cast): that needs a database's atomicity to
be real and lives in :mod:`fcrm_decision.commands_vote`. What is left here is
arithmetic on a multiset of three options.
"""

from __future__ import annotations

from typing import Optional

from fcrm_contracts.enums import Outcome
from fcrm_contracts.voting import tally
from pydantic import BaseModel, ConfigDict


class TallyResult(BaseModel):
    """A tally with the round it belongs to."""

    model_config = ConfigDict(extra="forbid")

    outcome: Optional[Outcome]
    no_majority: bool
    round: int
    next_round: Optional[int] = None  # set only when no_majority


def tally_round(votes: list[str], round_number: int) -> TallyResult:
    """``votes`` must be exactly the three cast options for this round, in
    any order: the tally does not care who cast which, only the multiset of
    options (member identity and hidden/revealed state are the caller's
    concern). Anything else is no majority (ADR-31: never a default outcome).
    """
    result = tally(votes)
    return TallyResult(
        outcome=result.outcome,
        no_majority=result.no_majority,
        round=round_number,
        next_round=(round_number + 1) if result.no_majority else None,
    )

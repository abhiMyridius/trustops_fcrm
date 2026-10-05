"""Committee vote tally (ADR-08, ADR-31). Lives in ``fcrm_contracts`` rather than
in either S5 (``fcrm_decision``, the system of record for votes) or H1
(``fcrm_caseflow``, which only reacts to the tallied outcome) because both
depend on this package and neither should depend on the other — a
service and a worker should never import each other directly.

:func:`tally` is the one place three votes become an outcome. Each member
casts one of the four options; the options are the four outcomes
(``VoteOption`` mirrors ``Outcome``), so there is no mapping step to get
wrong. Cases, with ``A`` approve, ``C`` approve with conditions, ``D`` defer
and ``R`` reject:

* **3-0** — the unanimous option.
* **2-1** — the option two members agree on (ADR-08: "two identical votes of
  three"). One refinement, **(proposed)**: when the whole ballot sits inside
  the approve family (``A`` and ``C`` only) the outcome is
  ``approve_with_conditions`` whichever side holds the two votes. ``C, C, A``
  is plain majority; ``A, A, C`` is the refinement: a member who asked for
  conditions is never silently outvoted into an unconditional approval, so
  the conditions stay attached to the approval. Every other 2-1 (``A, A, D``,
  ``C, C, R``, ...) is the plain majority option: the family never merges
  across a defer or a reject.
* **1-1-1** — no option has two votes: ``no_majority`` (ADR-31). It is never
  mapped to ``defer`` or any other outcome; S5 opens round two and asks the
  three members to vote again.
* **Any other number of votes** — ``no_majority`` too: a round is tallied
  only when exactly three (the quorum) are in, and a dropped or duplicated
  vote must fail closed, never into an outcome.

Nothing here reads who voted what: the tally is a function of the multiset
of options, so the order the votes arrived in cannot matter.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from typing import Final, Optional

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.enums import Outcome, VoteOption

__all__ = ["QUORUM", "Tally", "TallyRule", "tally", "tally_votes"]

#: R2: a committee round is tallied when exactly this many votes are in.
QUORUM: Final = 3

#: The two options that approve; the (proposed) refinement merges only these.
_APPROVE_FAMILY: Final = frozenset(
    {VoteOption.APPROVE, VoteOption.APPROVE_WITH_CONDITIONS}
)
_OPTION_ORDER: Final = {option: index for index, option in enumerate(VoteOption)}


class TallyRule(StrEnum):
    """Which clause of the tally produced a result, for the audit trail."""

    UNANIMOUS = "unanimous"
    MAJORITY = "majority"
    APPROVE_FAMILY = "approve_family"
    NO_MAJORITY = "no_majority"


class Tally(BaseModel):
    """The result of tallying one round.

    Attributes:
        outcome: The decided outcome, ``None`` exactly when ``no_majority``.
        no_majority: A split (or a wrong number of votes): no outcome, round
            two.
        counts: Votes per option as cast, in option order, options with no
            vote left out (``{"approve_with_conditions": 2, "defer": 1}``).
        rule: Which clause decided.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Optional[Outcome]
    no_majority: bool
    counts: dict[str, int]
    rule: TallyRule


def _counts(options: Sequence[VoteOption]) -> dict[str, int]:
    tallied = Counter(options)
    ordered = sorted(tallied, key=_OPTION_ORDER.__getitem__)
    return {option.value: tallied[option] for option in ordered}


def tally(votes: Sequence[str]) -> Tally:
    """Tally one round's votes.

    Args:
        votes: The cast options as their wire values, in any order.

    Returns:
        The :class:`Tally`; see the module docstring for the cases.

    Raises:
        ValueError: A vote is not one of the four :class:`VoteOption` values.
    """
    options = [VoteOption(vote) for vote in votes]
    counts = _counts(options)
    if len(options) != QUORUM:
        return Tally(
            outcome=None, no_majority=True, counts=counts, rule=TallyRule.NO_MAJORITY
        )
    top_option, top_votes = Counter(options).most_common(1)[0]
    if top_votes == QUORUM:
        return Tally(
            outcome=Outcome(top_option.value),
            no_majority=False,
            counts=counts,
            rule=TallyRule.UNANIMOUS,
        )
    if top_votes == 1:
        return Tally(
            outcome=None, no_majority=True, counts=counts, rule=TallyRule.NO_MAJORITY
        )
    if set(options) == _APPROVE_FAMILY and top_option is VoteOption.APPROVE:
        return Tally(
            outcome=Outcome.APPROVE_WITH_CONDITIONS,
            no_majority=False,
            counts=counts,
            rule=TallyRule.APPROVE_FAMILY,
        )
    return Tally(
        outcome=Outcome(top_option.value),
        no_majority=False,
        counts=counts,
        rule=TallyRule.MAJORITY,
    )


def tally_votes(votes: list[str]) -> tuple[Optional[str], bool]:
    """:func:`tally` as the ``(outcome, no_majority)`` pair H1 and the seed
    loaders already use; exactly one of ``outcome is not None`` /
    ``no_majority`` holds.
    """
    result = tally(votes)
    return (
        result.outcome.value if result.outcome is not None else None,
        result.no_majority,
    )

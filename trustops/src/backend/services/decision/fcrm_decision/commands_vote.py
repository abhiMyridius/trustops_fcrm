"""Casting a vote, and what the third vote does.

One transaction does all of it: lock the pack, check it is open, count the
round, store the vote, announce it (``vote.cast``, never the option), and, if
this was the third vote, tally the round and either decide it or open the next
one. A vote that is refused (a duplicate, a full round, a closed pack) leaves
no vote row and no outbox row.

The tally is :func:`fcrm_contracts.voting.tally` (ADR-08, ADR-31):

* a **majority** (including the ``approve``/``approve_with_conditions``
  refinement, proposed) decides the pack: the round is revealed and
  ``vote.tallied`` carries every vote;
* a **1-1-1 split** is ``no_majority``: the round is revealed and kept, round
  two opens on the same pack, ``vote.no_majority`` and ``vote.rerequested``
  go out, and a system thread opens for the discussion (ADR-08). Nothing is
  mapped to ``defer``.
"""

from __future__ import annotations

import logging
from typing import Optional

from fcrm_common.errors import NotFoundError
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.api.decision import OutcomeSet
from fcrm_contracts.enums import VoteOption
from fcrm_contracts.voting import QUORUM, Tally, tally
from pydantic import BaseModel, ConfigDict

from fcrm_decision import events
from fcrm_decision.errors import (
    AlreadyVotedError,
    PackDecidedError,
    PackNotOpenError,
    PackNotReleasedError,
    PackSupersededError,
    RoundFullError,
)
from fcrm_decision.rows import (
    SYSTEM_ASKER,
    SYSTEM_ASKER_NAME,
    OutcomeRow,
    PackRow,
    ThreadRow,
    VoteRow,
)
from fcrm_decision.store import DecisionStore, DecisionTx

logger = logging.getLogger(__name__)


class VoteRecorded(BaseModel):
    """What :func:`cast_vote` reports: that the vote counted, and, only for the
    vote that completed the round, how the round came out.
    """

    model_config = ConfigDict(extra="forbid")

    vote_id: str
    case_id: str
    round: int
    cast_count: int
    result: Optional[Tally] = None


def require_open(pack: PackRow) -> None:
    """Raise unless ``pack`` is released, live and taking votes.

    Raises:
        PackSupersededError: It was superseded.
        PackDecidedError: It already has an outcome.
        PackNotReleasedError: It never went to committee.
        PackNotOpenError: It was unlocked and awaits its replacement.
    """
    if pack.superseded:
        raise PackSupersededError(
            "This pack was superseded; vote on its replacement.",
            case_id=pack.case_id,
            pack_id=pack.pack_id,
        )
    if pack.decided:
        raise PackDecidedError(
            "This pack already has a committee outcome.",
            case_id=pack.case_id,
            pack_id=pack.pack_id,
        )
    if pack.released_at is None:
        raise PackNotReleasedError(
            "This pack has not been released to the committee.",
            case_id=pack.case_id,
            pack_id=pack.pack_id,
        )
    if not pack.open_for_voting:
        raise PackNotOpenError(
            "This pack is closed to votes while its numbers are corrected.",
            case_id=pack.case_id,
            pack_id=pack.pack_id,
        )


def _split_text(result: Tally) -> str:
    parts = " / ".join(f"{n} {option}" for option, n in result.counts.items())
    return f"No majority: {parts}. Discuss and re-vote (ADR-08)."


async def _conclude(
    tx: DecisionTx, pack: PackRow, round_: int, ballot: list[VoteRow]
) -> Tally:
    """Tally the full ballot and apply the result in the caller's transaction."""
    result = tally([vote.option.value for vote in ballot])
    await tx.put_outcome(
        OutcomeRow(
            outcome=OutcomeSet(
                case_id=pack.case_id,
                pack_id=pack.pack_id,
                round=round_,
                outcome=result.outcome,
                no_majority=result.no_majority,
            ),
            mode="committee",
            decided_at=utcnow(),
        )
    )
    if not result.no_majority:
        await tx.update_pack(pack.pack_id, {"open_for_voting": False, "decided": True})
        await tx.enqueue(events.vote_tallied(pack, round_, result, ballot))
        return result
    next_round = round_ + 1
    await tx.update_pack(pack.pack_id, {"current_round": next_round})
    await tx.enqueue(events.vote_no_majority(pack, round_, result))
    await tx.enqueue(
        events.vote_rerequested(
            pack,
            version=pack.version,
            round_=next_round,
            member_ids=[vote.member_id for vote in ballot],
            reason=events.REASON_NO_MAJORITY,
        )
    )
    await tx.insert_thread(
        ThreadRow(
            thread_id=new_id("thr"),
            pack_id=pack.pack_id,
            case_id=pack.case_id,
            asked_by=SYSTEM_ASKER,
            asked_by_name=SYSTEM_ASKER_NAME,
            text=_split_text(result),
            created_at=utcnow(),
            system=True,
        )
    )
    return result


async def cast_vote(
    store: DecisionStore,
    *,
    pack_id: str,
    member_id: str,
    option: VoteOption,
    rationale: str,
) -> VoteRecorded:
    """Record ``member_id``'s vote in the pack's current round.

    Args:
        store: The decision store.
        pack_id: The pack voted on.
        member_id: The verified caller (never taken from a body).
        option: Their choice.
        rationale: Their written reason (already checked non-trivial).

    Returns:
        The receipt; ``result`` is set only when this was the third vote.

    Raises:
        NotFoundError: No such pack.
        PackSupersededError: The pack was superseded.
        PackDecidedError: The pack already has an outcome.
        PackNotReleasedError: The pack never went to committee.
        PackNotOpenError: The pack is closed while its numbers are corrected.
        AlreadyVotedError: The member already voted this round.
        RoundFullError: The round already holds three votes.
    """

    async def work(tx: DecisionTx) -> VoteRecorded:
        pack = await tx.lock_pack(pack_id)
        if pack is None:
            raise NotFoundError(f"no pack {pack_id}")
        require_open(pack)
        round_ = pack.current_round
        ballot = await tx.round_votes(pack_id, round_)
        if any(vote.member_id == member_id for vote in ballot):
            raise AlreadyVotedError(
                "This member already voted in this round.",
                case_id=pack.case_id,
                pack_id=pack_id,
            )
        if len(ballot) >= QUORUM:
            raise RoundFullError(
                f"This round already has its {QUORUM} votes.",
                case_id=pack.case_id,
                pack_id=pack_id,
            )
        vote = VoteRow(
            pack_id=pack_id,
            round=round_,
            member_id=member_id,
            option=option,
            rationale=rationale,
            cast_at=utcnow(),
        )
        await tx.insert_vote(vote)
        ballot = [*ballot, vote]
        await tx.enqueue(events.vote_cast(pack, vote, len(ballot)))
        recorded = VoteRecorded(
            vote_id=vote.vote_id,
            case_id=pack.case_id,
            round=round_,
            cast_count=len(ballot),
        )
        if len(ballot) < QUORUM:
            return recorded
        recorded.result = await _conclude(tx, pack, round_, ballot)
        return recorded

    recorded = await store.run(work)
    _log(recorded, pack_id)
    return recorded


def _log(recorded: VoteRecorded, pack_id: str) -> None:
    """One line per vote: the count, never the option. The tally's outcome is
    logged only once the round is revealed.
    """
    context = {
        "case_id": recorded.case_id,
        "pack_id": pack_id,
        "round": recorded.round,
        "cast_count": recorded.cast_count,
    }
    result = recorded.result
    if result is None:
        logger.info("vote recorded", extra=context)
    elif result.no_majority:
        logger.info("no majority; next round opened", extra=context)
    else:
        outcome = result.outcome.value if result.outcome else None
        logger.info("committee outcome tallied", extra={**context, "outcome": outcome})

"""Rows to the web's shapes: pure projections, no I/O.

This is where vote secrecy (R2) is decided for every response S5 sends:
:func:`votes_view` is the only function that turns a stored vote into a
choice a client can read, and it does so for a round that has been tallied,
or for the caller's own vote. A peer's choice is never in a view before the
round is revealed; it is not hidden by the client, it is not sent.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final, Optional, cast

from fcrm_contracts.api.decision import (
    BandNo,
    CaseOutcome,
    CommitteeQueueItem,
    CommitteeThread,
    MyVote,
    PackAddendum,
    PackBody,
    PackContent,
    PackSummary,
    PackSummaryCounts,
    PackView,
    ThreadAnswer,
    Verdict,
    VoteMember,
    VotesView,
)
from fcrm_contracts.api.identity import RosterUser
from fcrm_contracts.enums import Outcome, VoteOption
from fcrm_contracts.voting import QUORUM

from fcrm_decision.labels import NO_MAJORITY_LABEL, outcome_label
from fcrm_decision.rows import (
    OutcomeRow,
    PackRow,
    QueueEntry,
    ThreadRow,
    VoteRow,
)

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_BODY_FIELDS: Final = frozenset(PackBody.model_fields)


def pack_view(row: PackRow, addenda: Sequence[ThreadRow]) -> PackView:
    """The pack as the committee and the analyst read it: the frozen content,
    S5's own fields, and the addenda (answered threads, which sit beside the
    pack and never change its hash).
    """
    content = PackContent.model_validate(row.content)
    body = content.model_dump(include=set(_BODY_FIELDS), by_alias=True)
    return PackView.model_validate(
        {
            **body,
            "pack_id": row.pack_id,
            "version": row.version,
            "content_hash": row.content_hash,
            "released_at": row.released_at,
            "status": row.status,
            "addenda": [
                addendum(thread) for thread in addenda if thread.answer is not None
            ],
        }
    )


def addendum(thread: ThreadRow) -> PackAddendum:
    """The pack addendum an answered thread is: question and answer together."""
    answer = thread.answer
    if answer is None:
        raise ValueError(f"thread {thread.thread_id} has no answer")
    return PackAddendum(
        no=answer.addendum_no,
        text=(
            f"Q ({thread.asked_by_name}): {thread.text} "
            f"— A ({answer.by_name}): {answer.text}"
        ),
        by=answer.by_name,
        at=answer.at,
        numbers_changed=answer.numbers_changed,
    )


def pack_summary(row: PackRow) -> PackSummary:
    """One line of a case's pack list, counted from the frozen content."""
    content = PackContent.model_validate(row.content)
    return PackSummary(
        pack_id=row.pack_id,
        version=row.version,
        content_hash=row.content_hash,
        status=row.status,
        built_at=row.built_at or row.released_at or _EPOCH,
        counts=PackSummaryCounts(
            composite=content.score.composite,
            band=content.score.band,
            areas=len(content.score.areas),
            controls=len(content.controls),
            overrides=len(content.overrides),
            planned=len(content.planned),
            clauses=len(content.clauses),
            gaps=len(content.gaps),
            rounds=len(content.clarification_rounds),
        ),
    )


def _band(value: int) -> BandNo:
    if value not in (1, 2, 3, 4):
        raise ValueError(f"band {value} is not 1-4")
    return cast(BandNo, value)


def queue_item(entry: QueueEntry, member_id: str) -> CommitteeQueueItem:
    """A queue row for ``member_id``: the pack's headline, their own vote
    status and how many have voted (never how).
    """
    outcome = entry.outcome
    return CommitteeQueueItem(
        case_id=entry.case_id,
        title=entry.title,
        change_types=entry.change_types,
        band=_band(entry.band),
        band_label=entry.band_label,
        analyst_name=entry.analyst_name,
        received=entry.released_at,
        pack_id=entry.pack_id,
        pack_version=entry.version,
        your_vote="voted" if member_id in entry.voters else "not_voted",
        cast_count=len(entry.voters),
        quorum=QUORUM,
        status="decided" if entry.decided else "open",
        outcome_label=outcome_label(outcome) if outcome is not None else None,
    )


def _counts(votes: Sequence[VoteRow]) -> dict[str, int]:
    tallied = Counter(vote.option for vote in votes)
    return {option.value: tallied[option] for option in VoteOption if tallied[option]}


def votes_view(
    *,
    pack_id: str,
    round_: int,
    votes: Sequence[VoteRow],
    outcome: Optional[OutcomeRow],
    roster: Sequence[RosterUser],
    viewer_id: Optional[str],
) -> VotesView:
    """One round's votes for one viewer.

    Args:
        pack_id: The pack.
        round_: The round shown.
        votes: The round's votes that are not voided.
        outcome: The round's result, or ``None`` while it is still open. Its
            existence is what reveals the round (the reveal and the tally are
            one event, on the third vote).
        roster: The committee, in seat order.
        viewer_id: The caller's user id when they are a committee member,
            else ``None`` (the analyst and the service see no seat as theirs).

    A seat shows ``voted`` or ``not_voted`` to everyone; its ``choice`` and
    ``rationale`` only once the round is revealed, or to the member who cast it.
    """
    revealed = outcome is not None
    by_member = {vote.member_id: vote for vote in votes}
    members: list[VoteMember] = []
    for person in roster:
        vote = by_member.get(person.id)
        is_you = viewer_id is not None and person.id == viewer_id
        show = vote is not None and (revealed or is_you)
        members.append(
            VoteMember(
                name=person.name,
                status="voted" if vote is not None else "not_voted",
                is_you=is_you,
                choice=Outcome(vote.option.value) if show and vote else None,
                rationale=vote.rationale if show and vote else None,
                cast_at=vote.cast_at if show and vote else None,
            )
        )
    mine_vote = by_member.get(viewer_id) if viewer_id is not None else None
    mine = (
        MyVote(
            choice=Outcome(mine_vote.option.value),
            rationale=mine_vote.rationale,
            cast_at=mine_vote.cast_at,
        )
        if mine_vote is not None
        else None
    )
    decided = outcome.outcome if outcome is not None else None
    verdict: Optional[Verdict] = None
    label: Optional[str] = None
    if decided is not None:
        if decided.no_majority:
            verdict, label = "no_majority", NO_MAJORITY_LABEL
        elif decided.outcome is not None:
            verdict = decided.outcome.value
            label = outcome_label(decided.outcome)
    return VotesView(
        pack_id=pack_id,
        round=round_,
        quorum=QUORUM,
        cast_count=len(votes),
        revealed=revealed,
        members=members,
        mine=mine,
        tally=_counts(votes) if revealed else None,
        outcome=verdict,
        outcome_label=label,
    )


def thread_view(row: ThreadRow) -> CommitteeThread:
    """A thread, with its answer when it has one."""
    answer = row.answer
    return CommitteeThread.model_validate(
        {
            "id": row.thread_id,
            "from": row.asked_by_name,
            "question": row.text,
            "at": row.created_at,
            "answer": (
                ThreadAnswer(
                    by=answer.by_name,
                    text=answer.text,
                    at=answer.at,
                    addendum_no=answer.addendum_no,
                    numbers_changed=answer.numbers_changed,
                )
                if answer is not None
                else None
            ),
        }
    )


def case_outcome_view(row: OutcomeRow) -> CaseOutcome:
    """What was decided for the case, and how it was recorded.

    Raises:
        ValueError: The row is a no-majority round, which decided nothing.
    """
    result = row.outcome
    if result.outcome is None:
        raise ValueError(f"{row.row_id} decided nothing")
    recorded = row.recorded
    return CaseOutcome(
        case_id=result.case_id,
        outcome=result.outcome,
        label=outcome_label(result.outcome),
        mode=row.mode,
        pack_id=None if row.mode == "delegated" else result.pack_id,
        round=result.round,
        decided_at=row.decided_at,
        decided_by=recorded.decided_by if recorded else None,
        reason=recorded.reason if recorded else None,
        plain_summary=recorded.plain_summary if recorded else None,
        conditions=recorded.conditions if recorded else [],
        recorded=recorded is not None,
    )

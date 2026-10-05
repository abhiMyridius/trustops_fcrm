"""The events S5 publishes, built from its rows (design A §4.3).

Each builder validates its payload through the model in
``fcrm_contracts.events.payloads`` before it becomes an
:class:`~fcrm_contracts.events.envelope.Envelope`, so a producer cannot drift
from the contract. They are pure: the store enqueues what they return inside
the same Mongo transaction as the state change (the outbox rule).

**Secrecy (R2).** Nothing built before a round is tallied carries a vote's
option or rationale. ``vote.cast`` says that a member voted and how many have;
:class:`~fcrm_contracts.events.payloads.VoteCastPayload` refuses an option
key outright. The options leave S5 only in ``vote.tallied``, which is built
from the whole ballot at the moment the third vote reveals it, and in
``vote.no_majority``, whose counts name no member.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from fcrm_common.events import build_envelope
from fcrm_contracts.api.decision import OutcomeRecord
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    CommitteeAnswered,
    CommitteeQuestionAsked,
    OutcomeCondition,
    OutcomeSetPayload,
    PackReleased,
    PackSuperseded,
    RevealedVote,
    VoteCastPayload,
    VoteNoMajority,
    VoteRerequested,
    VoteTallied,
)
from fcrm_contracts.voting import QUORUM, Tally

from fcrm_decision.rows import AnswerRow, PackRow, ThreadRow, VoteRow

#: Why a round is re-requested after a split.
REASON_NO_MAJORITY: Final = "no_majority"


def _envelope(event_type: str, case_id: str, payload: Any) -> Envelope:
    return build_envelope(event_type, payload.model_dump(mode="json"), case_id=case_id)


def pack_released(pack: PackRow, names: Sequence[str]) -> Envelope:
    """``pack.released``: the pack went to these members. Only ever built for a
    pack whose four checks passed, hence ``release_checks_passed``.
    """
    return _envelope(
        "pack.released",
        pack.case_id,
        PackReleased(
            pack_id=pack.pack_id,
            version=pack.version,
            released_to=list(pack.released_to),
            released_to_names=list(names),
            content_hash=pack.content_hash,
            release_checks_passed=pack.checks.all_passed,
        ),
    )


def vote_cast(pack: PackRow, vote: VoteRow, cast_count: int) -> Envelope:
    """``vote.cast``: that ``vote.member_id`` voted, never what they voted."""
    return _envelope(
        "vote.cast",
        pack.case_id,
        VoteCastPayload(
            pack_id=pack.pack_id,
            round=vote.round,
            member_id=vote.member_id,
            cast_count=cast_count,
            quorum=QUORUM,
        ),
    )


def vote_tallied(
    pack: PackRow, round_: int, result: Tally, ballot: Sequence[VoteRow]
) -> Envelope:
    """``vote.tallied``: the decided outcome and every vote, revealed together."""
    return _envelope(
        "vote.tallied",
        pack.case_id,
        VoteTallied(
            pack_id=pack.pack_id,
            round=round_,
            outcome=result.outcome,
            vote_outcome=result.outcome,
            counts=result.counts,
            votes=[
                RevealedVote(
                    member_id=v.member_id, vote=v.option, rationale=v.rationale
                )
                for v in ballot
            ],
        ),
    )


def vote_no_majority(pack: PackRow, round_: int, result: Tally) -> Envelope:
    """``vote.no_majority``: round ``round_`` split (ADR-31); counts only."""
    return _envelope(
        "vote.no_majority",
        pack.case_id,
        VoteNoMajority(pack_id=pack.pack_id, round=round_, counts=result.counts),
    )


def vote_rerequested(
    pack: PackRow, *, version: int, round_: int, member_ids: Sequence[str], reason: str
) -> Envelope:
    """``vote.rerequested``: these members are asked to vote again, in round
    ``round_`` of pack version ``version``.
    """
    return _envelope(
        "vote.rerequested",
        pack.case_id,
        VoteRerequested(
            pack_id=pack.pack_id,
            version=version,
            round=round_,
            member_ids=list(member_ids),
            reason=reason,
        ),
    )


def pack_superseded(pack: PackRow, reason: str, voided: int) -> Envelope:
    """``pack.superseded``: the pack was unlocked; ``voided`` cast votes were
    voided (kept) and are re-requested on the next version.
    """
    return _envelope(
        "pack.superseded",
        pack.case_id,
        PackSuperseded(
            pack_id=pack.pack_id,
            version=pack.version,
            reason=reason,
            voided_votes=voided,
        ),
    )


def committee_question_asked(pack: PackRow, thread: ThreadRow) -> Envelope:
    """``committee.question_asked``: a member opened a thread (R6)."""
    return _envelope(
        "committee.question_asked",
        pack.case_id,
        CommitteeQuestionAsked(
            pack_id=pack.pack_id,
            thread_id=thread.thread_id,
            member_id=thread.asked_by,
            text=thread.text,
        ),
    )


def committee_answered(pack: PackRow, thread: ThreadRow, answer: AnswerRow) -> Envelope:
    """``committee.answered``: the analyst answered. ``numbers_changed`` sends
    the case back to review (R6) and carries the unlock reason.
    """
    return _envelope(
        "committee.answered",
        pack.case_id,
        CommitteeAnswered(
            pack_id=pack.pack_id,
            thread_id=thread.thread_id,
            addendum_id=answer.addendum_id,
            by=answer.by,
            numbers_changed=answer.numbers_changed,
            unlock_reason=answer.unlock_reason,
        ),
    )


def outcome_set(record: OutcomeRecord, *, pack_id: str | None) -> Envelope:
    """``outcome.set``: an outcome was recorded, by the committee or by the
    analyst's delegated close.
    """
    return _envelope(
        "outcome.set",
        record.case_id,
        OutcomeSetPayload(
            case_id=record.case_id,
            mode=record.mode,
            outcome=record.outcome,
            conditions=[
                OutcomeCondition(
                    id=c.id,
                    text=c.text,
                    owner_id=c.owner_id,
                    due_label=c.due_label,
                )
                for c in record.conditions
            ],
            plain_summary=record.plain_summary,
            decided_by=record.decided_by,
            pack_id=pack_id,
            reason=record.reason,
        ),
    )

"""The owner's follow-up screens, as the contract's PO shapes: the inbox of
questions FCRM asked, the plain outcome and the conditions in plain words.

Every function maps stored rows onto ``fcrm_contracts.api.case_po`` models, which
have no field that could carry a score, band, rating, clause, vote or harness
identifier (the first fence). The rows these functions read have already been cut
at the data layer (:mod:`fcrm_case.po_followup_store`), and text staff wrote that
could name a reserved term is replaced by a plain sentence rather than shown (the
second fence); the response guard re-scans everything on the way out (the third).

**The outcome is plain.** The label, the owner-facing summary, the date and the
conditions in words; the reminder lead time is a constant. Nothing about how the
committee voted, the rationale, the pack or the numbers.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final, Optional

from fcrm_contracts.api.case_po import (
    ConditionPO,
    InboxPO,
    InboxRound,
    OutcomePO,
    ThreadAnswer,
    ThreadPO,
)
from fcrm_contracts.enums import Outcome

from fcrm_case.domain.activity import names_a_restricted_term
from fcrm_case.domain.clarifications import ClarificationThreadRow, open_threads
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ConditionRow
from fcrm_case.po_followup_store import DecidedOutcome

#: Days before a condition's due date the owner is reminded (the demo's 14 d).
REMINDER_DAYS: Final = 14

#: The words for an outcome (the same four the rest of S1 uses; a test holds the
#: two lists together).
OUTCOME_WORDS: Final[dict[Outcome, str]] = {
    Outcome.APPROVE: "Approved",
    Outcome.APPROVE_WITH_CONDITIONS: "Approved with conditions",
    Outcome.DEFER: "Deferred",
    Outcome.REJECT: "Rejected",
}

QUESTION_FALLBACK: Final = "FCRM has a question about this part of your submission."
CONDITION_FALLBACK: Final = (
    "A condition applies to this approval; ask FCRM for its wording."
)
SUMMARY_FALLBACK: Final = "Your change was decided. Ask FCRM for the details."
FALLBACK_FIELD_LABEL: Final = "Your submission"


def plain(text: str, fallback: str) -> str:
    """``text`` unless it names a score, band, rating or control, in which case
    ``fallback``: wording staff wrote is shown only when it is safe to.
    """
    return fallback if names_a_restricted_term(text) else text


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def thread_po(row: ClarificationThreadRow) -> ThreadPO:
    """One question as the owner reads it: the field it is about (never the
    analyst's anchor), the words and the owner's own answer.
    """
    answer = (
        ThreadAnswer(
            text=row.answer.text,
            documents=list(row.answer.documents),
            at=_iso(row.answer.at),
        )
        if row.answer is not None
        else None
    )
    return ThreadPO(
        id=row.thread_id,
        round=row.round,
        field_label=plain(row.field_label, FALLBACK_FIELD_LABEL),
        question=plain(row.text, QUESTION_FALLBACK),
        status="open" if row.status == "awaiting_response" else "answered",
        answer=answer,
    )


def inbox_view(record: CaseRecord, rows: Sequence[ClarificationThreadRow]) -> InboxPO:
    """The owner's inbox: the questions by round, and a thank-you once the last
    round is answered.
    """
    sender = record.analyst_name or "FCRM"
    by_round: dict[int, list[ClarificationThreadRow]] = {}
    for row in rows:
        by_round.setdefault(row.round, []).append(row)
    rounds = [
        InboxRound.model_validate(
            {
                "round": number,
                "from": sender,
                "sent_at": _iso(min(r.opened_at for r in group)),
                "threads": [
                    thread_po(r) for r in sorted(group, key=lambda r: r.question_id)
                ],
            }
        )
        for number, group in sorted(by_round.items())
    ]
    return InboxPO(rounds=rounds, banner=_banner(by_round))


def _banner(by_round: dict[int, list[ClarificationThreadRow]]) -> Optional[str]:
    """What the owner is told once the latest round is answered."""
    if not by_round:
        return None
    last = by_round[max(by_round)]
    if open_threads(last):
        return None
    version = max((r.resulting_version or 0 for r in last), default=0)
    saved = f" Your answers are saved as version {version}." if version else ""
    return f"Thank you. FCRM has your answers and is reviewing them.{saved}"


def condition_po(row: ConditionRow, owner_sub: str) -> ConditionPO:
    """One condition in words: what it asks, who owns it, when it is due and where
    it stands. ``mine`` is whether the owner is the one who must evidence it.
    """
    status = (
        row.status
        if row.status in ("open", "scheduled", "evidence_submitted", "closed")
        else "open"
    )
    return ConditionPO.model_validate(
        {
            "id": row.condition_id,
            "text": plain(row.text, CONDITION_FALLBACK),
            "owner": row.owner_label,
            "due": row.due_label,
            "status": status,
            "mine": row.owner_id is not None and row.owner_id == owner_sub,
        }
    )


def outcome_view(
    decided: Optional[DecidedOutcome],
    conditions: Sequence[ConditionRow],
    owner_sub: str,
) -> OutcomePO:
    """The plain outcome, or "not decided yet" before there is one."""
    if decided is None:
        return OutcomePO(
            decided=False,
            outcome_label=None,
            plain_summary=None,
            decided_at=None,
            conditions=[],
            reminder_days=REMINDER_DAYS,
        )
    summary = decided.plain_summary
    return OutcomePO(
        decided=True,
        outcome_label=OUTCOME_WORDS[decided.outcome],
        plain_summary=plain(summary, SUMMARY_FALLBACK) if summary else None,
        decided_at=_iso(decided.decided_at) if decided.decided_at else None,
        conditions=[condition_po(c, owner_sub) for c in conditions],
        reminder_days=REMINDER_DAYS,
    )

"""Committee questions and the analyst's answers (R6).

A member's question opens a thread every member and the analyst can read. The
analyst's answer is attached to the pack as an addendum: the pack's frozen
content and ``content_hash`` stay exactly as they were. If the answer admits a
number changed, the pack stops taking votes in the same transaction (members
must not decide on numbers just called wrong) and ``committee.answered``
carries the unlock reason; H1 then drives the supersede, the corrected pack and
the re-vote.
"""

from __future__ import annotations

import logging
from typing import Optional

from fcrm_common.errors import NotFoundError
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow

from fcrm_decision import events
from fcrm_decision.errors import (
    AlreadyAnsweredError,
    PackDecidedError,
    PackNotReleasedError,
    PackSupersededError,
)
from fcrm_decision.rows import AnswerRow, PackRow, ThreadRow
from fcrm_decision.store import DecisionStore, DecisionTx

logger = logging.getLogger(__name__)


def _require_live(pack: PackRow) -> None:
    """A thread is asked or answered only on a released pack that is neither
    superseded nor decided: after that the conversation is closed.
    """
    if pack.superseded:
        raise PackSupersededError(
            "This pack was superseded.", case_id=pack.case_id, pack_id=pack.pack_id
        )
    if pack.decided:
        raise PackDecidedError(
            "This pack already has a committee outcome.",
            case_id=pack.case_id,
            pack_id=pack.pack_id,
        )


async def ask_thread(
    store: DecisionStore,
    *,
    pack_id: str,
    asker_id: str,
    asker_name: str,
    text: str,
) -> ThreadRow:
    """Open a question thread on a released pack.

    Raises:
        NotFoundError: No such pack, or it was never released.
        PackSupersededError: The pack was superseded.
        PackDecidedError: The pack already has an outcome.
    """

    async def work(tx: DecisionTx) -> ThreadRow:
        pack = await tx.get_pack(pack_id)
        if pack is None or pack.released_at is None:
            raise NotFoundError(f"no pack {pack_id}")
        _require_live(pack)
        thread = ThreadRow(
            thread_id=new_id("thr"),
            pack_id=pack_id,
            case_id=pack.case_id,
            asked_by=asker_id,
            asked_by_name=asker_name,
            text=text,
            created_at=utcnow(),
        )
        await tx.insert_thread(thread)
        await tx.enqueue(events.committee_question_asked(pack, thread))
        return thread

    thread = await store.run(work)
    logger.info(
        "committee question asked",
        extra={
            "case_id": thread.case_id,
            "pack_id": pack_id,
            "thread_id": thread.thread_id,
        },
    )
    return thread


async def answer_thread(
    store: DecisionStore,
    *,
    thread_id: str,
    answerer_id: str,
    answerer_name: str,
    text: str,
    numbers_changed: bool,
    unlock_reason: Optional[str],
) -> ThreadRow:
    """Answer a thread; attach the answer as the pack's next addendum.

    With ``numbers_changed`` the pack is closed to votes at once and the event
    carries ``unlock_reason`` (the contract already refuses one shorter than
    twenty characters). Without it nothing about the pack changes.

    Raises:
        NotFoundError: No such thread.
        AlreadyAnsweredError: The thread already has its answer.
        PackSupersededError: The pack was superseded.
        PackDecidedError: The pack already has an outcome (closed means closed;
            a decided pack's numbers can no longer be reopened).
        PackNotReleasedError: ``numbers_changed`` on a pack never released.
    """

    async def work(tx: DecisionTx) -> ThreadRow:
        thread = await tx.get_thread(thread_id)
        if thread is None:
            raise NotFoundError(f"no committee thread {thread_id}")
        pack = await tx.lock_pack(thread.pack_id)
        if pack is None:
            raise NotFoundError(f"no pack {thread.pack_id}")
        if thread.answer is not None:
            raise AlreadyAnsweredError(
                "This question already has its answer.",
                case_id=pack.case_id,
                pack_id=pack.pack_id,
            )
        _require_live(pack)
        if numbers_changed and pack.released_at is None:
            raise PackNotReleasedError(
                "This pack was never released; there are no committee numbers "
                "to unlock.",
                case_id=pack.case_id,
                pack_id=pack.pack_id,
            )
        now = utcnow()
        answer = AnswerRow(
            by=answerer_id,
            by_name=answerer_name,
            text=text,
            at=now,
            addendum_id=new_id("addm"),
            addendum_no=pack.addendum_count + 1,
            numbers_changed=numbers_changed,
            unlock_reason=unlock_reason if numbers_changed else None,
        )
        await tx.set_answer(thread_id, answer)
        changes: dict[str, object] = {"addendum_count": answer.addendum_no}
        if numbers_changed:
            changes |= {"open_for_voting": False, "unlocked_at": now}
        await tx.update_pack(pack.pack_id, changes)
        await tx.enqueue(events.committee_answered(pack, thread, answer))
        return thread.model_copy(update={"answer": answer})

    answered = await store.run(work)
    logger.info(
        "committee question answered",
        extra={
            "case_id": answered.case_id,
            "pack_id": answered.pack_id,
            "thread_id": thread_id,
            "numbers_changed": numbers_changed,
        },
    )
    return answered

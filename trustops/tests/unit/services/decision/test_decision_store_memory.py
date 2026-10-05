"""The in-memory store's transaction semantics, which the command tests lean on:
a transaction that raises rolls back everything it wrote, the outbox included,
and the unique constraints the Mongo indexes enforce are enforced here too.
Plus the command-level refusals that HTTP cannot reach (a full round).
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_common.errors import ConflictError
from fcrm_common.events import build_envelope
from fcrm_common.time import utcnow
from fcrm_contracts.api.decision import ReleaseChecks
from fcrm_contracts.enums import VoteOption
from fcrm_decision.commands_vote import cast_vote
from fcrm_decision.errors import (
    AlreadyAnsweredError,
    AlreadyVotedError,
    RoundFullError,
)
from fcrm_decision.rows import AnswerRow, PackRow, ThreadRow, VoteRow
from fcrm_decision.store import DecisionTx
from fcrm_decision.store_memory import InMemoryDecisionStore

CHECKS = ReleaseChecks(
    locked=True,
    all_items_reviewed=True,
    boundary_and_band_acknowledged=True,
    business_summary_present=True,
)


def pack(pack_id: str = "pack_1", case_id: str = "C1", version: int = 1) -> PackRow:
    return PackRow(
        pack_id=pack_id,
        case_id=case_id,
        version=version,
        content={},
        checks=CHECKS,
        released_at=utcnow(),
        open_for_voting=True,
    )


def vote(member: str, round_: int = 1, pack_id: str = "pack_1") -> VoteRow:
    return VoteRow(
        pack_id=pack_id,
        round=round_,
        member_id=member,
        option=VoteOption.APPROVE,
        rationale="a position long enough to be a rationale",
        cast_at=utcnow(),
    )


async def test_a_raising_transaction_rolls_back_its_writes_and_its_events() -> None:
    store = InMemoryDecisionStore()

    async def failing(tx: DecisionTx) -> None:
        await tx.insert_pack(pack())
        await tx.insert_vote(vote("u-a"))
        await tx.enqueue(build_envelope("vote.cast", {"x": 1}, case_id="C1"))
        raise RuntimeError("the transaction failed")

    with pytest.raises(RuntimeError, match="failed"):
        await store.run(failing)

    assert await store.get_pack("pack_1") is None
    assert await store.round_votes("pack_1", 1) == []
    assert store.events == []


async def test_a_committed_transaction_keeps_its_writes_and_its_events() -> None:
    store = InMemoryDecisionStore()

    async def work(tx: DecisionTx) -> None:
        await tx.insert_pack(pack())
        await tx.enqueue(build_envelope("pack.released", {"x": 1}, case_id="C1"))

    await store.run(work)

    assert (await store.get_pack("pack_1")) is not None
    assert [e.type for e in store.events] == ["pack.released"]


async def test_a_duplicate_vote_leaves_neither_a_vote_nor_an_event_behind() -> None:
    store = InMemoryDecisionStore()

    async def first(tx: DecisionTx) -> None:
        await tx.insert_pack(pack())
        await tx.insert_vote(vote("u-a"))

    async def duplicate(tx: DecisionTx) -> None:
        await tx.enqueue(build_envelope("vote.cast", {"x": 1}, case_id="C1"))
        await tx.insert_vote(vote("u-a"))

    await store.run(first)
    with pytest.raises(AlreadyVotedError):
        await store.run(duplicate)

    assert len(await store.round_votes("pack_1", 1)) == 1
    assert store.events == []  # the event enqueued before the failure went too


async def test_the_same_member_may_vote_in_another_round() -> None:
    store = InMemoryDecisionStore()

    async def work(tx: DecisionTx) -> None:
        await tx.insert_pack(pack())
        await tx.insert_vote(vote("u-a", round_=1))
        await tx.insert_vote(vote("u-a", round_=2))

    await store.run(work)
    assert len(await store.round_votes("pack_1", 2)) == 1


async def test_a_case_cannot_have_two_packs_of_one_version() -> None:
    store = InMemoryDecisionStore()

    async def work(tx: DecisionTx) -> None:
        await tx.insert_pack(pack("pack_1", "C1", 1))
        await tx.insert_pack(pack("pack_2", "C1", 1))

    with pytest.raises(ConflictError):
        await store.run(work)
    assert await store.get_pack("pack_1") is None


async def test_voided_votes_are_kept_but_no_longer_counted() -> None:
    store = InMemoryDecisionStore()

    async def work(tx: DecisionTx) -> int:
        await tx.insert_pack(pack())
        await tx.insert_vote(vote("u-a"))
        await tx.insert_vote(vote("u-b"))
        return await tx.void_round_votes("pack_1", 1)

    assert await store.run(work) == 2
    assert await store.round_votes("pack_1", 1) == []

    async def again(tx: DecisionTx) -> None:
        with pytest.raises(AlreadyVotedError):  # the voided row still holds the seat
            await tx.insert_vote(vote("u-a"))

    await store.run(again)


async def test_lock_pack_bumps_the_ballot_version_and_misses_cleanly() -> None:
    store = InMemoryDecisionStore()

    async def work(tx: DecisionTx) -> tuple[int, Any]:
        await tx.insert_pack(pack())
        locked = await tx.lock_pack("pack_1")
        assert locked is not None
        return locked.ballot_version, await tx.lock_pack("pack_nope")

    assert await store.run(work) == (1, None)


async def test_update_pack_refuses_a_field_that_does_not_exist() -> None:
    store = InMemoryDecisionStore()

    async def work(tx: DecisionTx) -> None:
        await tx.insert_pack(pack())
        await tx.update_pack("pack_1", {"released": True})

    with pytest.raises(ValueError, match="no field"):
        await store.run(work)


async def test_set_answer_refuses_a_second_answer() -> None:
    store = InMemoryDecisionStore()
    answer = AnswerRow(
        by="u-riyer",
        by_name="Rahul Iyer",
        text="Because.",
        at=utcnow(),
        addendum_id="addm_1",
        addendum_no=1,
        numbers_changed=False,
    )

    async def work(tx: DecisionTx) -> None:
        await tx.insert_thread(
            ThreadRow(
                thread_id="thr_1",
                pack_id="pack_1",
                case_id="C1",
                asked_by="u-a",
                asked_by_name="A",
                text="Why?",
                created_at=utcnow(),
            )
        )
        await tx.set_answer("thr_1", answer)
        await tx.set_answer("thr_1", answer)

    with pytest.raises(AlreadyAnsweredError):
        await store.run(work)


async def test_a_round_that_already_holds_three_votes_takes_no_fourth() -> None:
    """Unreachable over HTTP (the third vote tallies and closes the round), so
    the state is built directly: the count check is the guard if it ever is.
    """
    store = InMemoryDecisionStore()

    async def setup(tx: DecisionTx) -> None:
        await tx.insert_pack(pack())
        for member in ("u-a", "u-b", "u-c"):
            await tx.insert_vote(vote(member))

    await store.run(setup)

    with pytest.raises(RoundFullError):
        await cast_vote(
            store,
            pack_id="pack_1",
            member_id="u-d",
            option=VoteOption.APPROVE,
            rationale="a position long enough to be a rationale",
        )
    assert len(await store.round_votes("pack_1", 1)) == 3
    assert store.events == []

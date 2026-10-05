"""S5's two atomic primitives against a real MongoDB 8 replica set
(``MongoDecisionStore.record_vote`` and ``unlock_for_changed_numbers``).

The in-memory store is atomic because nothing in its critical section
awaits; that proves nothing about Mongo, where every call is a network
round trip. These tests race real concurrent callers against one pack and
check the invariants the unit tests pin: a round never holds more than
three votes, it is tallied exactly once, and a decided pack cannot be
unlocked.

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27217/?replicaSet=rs0'
    uv run --project src/backend --all-packages \\
        pytest tests/integration/live_mongo -m integration
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from fcrm_common.errors import ConflictError, RuleViolationError
from fcrm_common.time import utcnow
from fcrm_contracts.api.decision import OutcomeSet, ReleaseChecks
from fcrm_contracts.enums import VoteOption
from fcrm_decision.outcomes import tally_round
from fcrm_decision.store import (
    QUORUM,
    MongoDecisionStore,
    _PackRow,
    _VoteRow,
    ensure_indexes,
)
from pymongo import AsyncMongoClient

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
DB = "fcrm_decision_votes_live_test"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

CHECKS = ReleaseChecks(
    numbers_locked=True,
    conditions_drafted=True,
    all_review_items_decided=True,
    boundary_and_band_acknowledged=True,
)


@pytest.fixture
async def store() -> AsyncIterator[MongoDecisionStore]:
    client: AsyncMongoClient = AsyncMongoClient(URI)
    await client.drop_database(DB)
    db = client[DB]
    await ensure_indexes(db)
    yield MongoDecisionStore(db)
    await client.drop_database(DB)
    await client.close()


async def _open_pack(store: MongoDecisionStore, pack_id: str) -> _PackRow:
    row = _PackRow(
        pack_id=pack_id,
        case_id="FCRM-2026-LIVE",
        version=1,
        content={"case_id": "FCRM-2026-LIVE"},
        checks=CHECKS,
        released_at=utcnow(),
        open_for_voting=True,
    )
    await store.insert_pack(row)
    return row


def _vote(pack_id: str, member: str, option: VoteOption, round_: int = 1) -> _VoteRow:
    return _VoteRow(
        pack_id=pack_id,
        round=round_,
        member_id=member,
        option=option,
        rationale=f"{member} votes {option.value}.",
        cast_at=utcnow(),
    )


def _decider(row: _PackRow, round_: int) -> object:
    def decide(ballot: list[_VoteRow]) -> OutcomeSet:
        result = tally_round([v.option.value for v in ballot], round_)
        return OutcomeSet(
            case_id=row.case_id,
            pack_id=row.pack_id,
            round=round_,
            outcome=result.outcome,
            no_majority=result.no_majority,
        )

    return decide


async def _try(store: MongoDecisionStore, row: _PackRow, vote: _VoteRow) -> object:
    try:
        return await store.record_vote(vote, _decider(row, vote.round))  # type: ignore[arg-type]
    except (ConflictError, RuleViolationError) as exc:
        return exc


@pytest.mark.parametrize("racers", [4, 6])
async def test_concurrent_votes_on_one_round_never_exceed_quorum_and_tally_once(
    store: MongoDecisionStore, racers: int
) -> None:
    row = await _open_pack(store, f"pack_live_race_{racers}")
    options = [VoteOption.APPROVE, VoteOption.DEFER, VoteOption.REJECT]
    votes = [
        _vote(row.pack_id, f"u-m{i}", options[i % len(options)]) for i in range(racers)
    ]
    results = await asyncio.gather(*(_try(store, row, v) for v in votes))

    accepted = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, Exception)]
    assert len(accepted) == QUORUM
    assert len(refused) == racers - QUORUM
    with_outcome = [r for r in accepted if getattr(r, "outcome", None) is not None]
    assert len(with_outcome) == 1

    assert len(await store.votes_for_round(row.pack_id, 1)) == QUORUM
    pack = await store.get_pack(row.pack_id)
    assert pack is not None
    outcome = await store.get_outcome(row.pack_id, 1)
    assert outcome is not None
    # Bumped at most once: round 2 on a split, closed on a decision.
    if outcome.no_majority:
        assert pack.current_round == 2
        assert pack.open_for_voting is True
    else:
        assert pack.current_round == 1
        assert pack.decided is True
        assert pack.open_for_voting is False


async def test_a_decided_pack_cannot_be_unlocked_and_its_votes_stay_valid(
    store: MongoDecisionStore,
) -> None:
    row = await _open_pack(store, "pack_live_decided")
    for member in ("u-a", "u-b", "u-c"):
        await store.record_vote(
            _vote(row.pack_id, member, VoteOption.APPROVE),
            _decider(row, 1),  # type: ignore[arg-type]
        )
    assert await store.unlock_for_changed_numbers(row.pack_id) is False
    ballot = await store.votes_for_round(row.pack_id, 1)
    assert len(ballot) == 3
    assert not any(v.voided for v in ballot)


async def test_an_undecided_pack_unlocks_and_then_refuses_votes(
    store: MongoDecisionStore,
) -> None:
    row = await _open_pack(store, "pack_live_unlock")
    await store.record_vote(
        _vote(row.pack_id, "u-a", VoteOption.APPROVE),
        _decider(row, 1),  # type: ignore[arg-type]
    )
    assert await store.unlock_for_changed_numbers(row.pack_id) is True
    assert all(v.voided for v in await store.votes_for_round(row.pack_id, 1))
    with pytest.raises(RuleViolationError):
        await store.record_vote(
            _vote(row.pack_id, "u-b", VoteOption.APPROVE),
            _decider(row, 1),  # type: ignore[arg-type]
        )


async def test_unlock_and_deciding_vote_racing_never_both_win(
    store: MongoDecisionStore,
) -> None:
    row = await _open_pack(store, "pack_live_unlock_race")
    for member in ("u-a", "u-b"):
        await store.record_vote(
            _vote(row.pack_id, member, VoteOption.APPROVE),
            _decider(row, 1),  # type: ignore[arg-type]
        )

    async def unlock() -> object:
        return await store.unlock_for_changed_numbers(row.pack_id)

    third, unlocked = await asyncio.gather(
        _try(store, row, _vote(row.pack_id, "u-c", VoteOption.APPROVE)), unlock()
    )
    decided = not isinstance(third, Exception)
    assert decided != (unlocked is True), (third, unlocked)
    ballot = await store.votes_for_round(row.pack_id, 1)
    if decided:
        assert not any(v.voided for v in ballot)
    else:
        assert all(v.voided for v in ballot)

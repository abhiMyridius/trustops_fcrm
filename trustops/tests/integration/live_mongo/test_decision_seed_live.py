"""S5's seed rows against a real MongoDB: one bulk insert per collection into
an empty store, nothing on a second start, and a seeded committee-stage pack
that really takes a vote through the store's transaction."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fcrm_common.errors import RuleViolationError
from fcrm_contracts.enums import Outcome, VoteOption
from fcrm_decision.outcomes import tally_round
from fcrm_decision.seedmodel import DELEGATED_PACK_PREFIX, build_seed_rows, pack_id_for
from fcrm_decision.store import MongoDecisionStore, _VoteRow, ensure_indexes
from fcrm_seed.loader import cases, closed_cases, load_raw, platform_outcome
from pymongo import AsyncMongoClient

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
DB = "fcrm_decision_seedtest"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

COMMITTEE = [u["id"] for u in load_raw()["users"]["committee"]]
OPEN_PACK = pack_id_for("FCRM-2026-0138")


@pytest.fixture
async def db() -> AsyncIterator[AsyncMongoClient]:
    client: AsyncMongoClient = AsyncMongoClient(URI)
    await client.drop_database(DB)
    yield client[DB]
    await client.drop_database(DB)
    await client.close()


async def _seed(db) -> tuple[MongoDecisionStore, dict[str, int]]:  # type: ignore[no-untyped-def]
    await ensure_indexes(db)
    store = MongoDecisionStore(db)
    rows = build_seed_rows(cases(), COMMITTEE)
    return store, await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)


async def test_an_empty_store_gets_every_seed_row(db) -> None:  # type: ignore[no-untyped-def]
    _, loaded = await _seed(db)
    assert loaded == {"packs": 13, "votes": 33, "outcomes": 65}
    assert await db["packs"].count_documents({}) == 13
    assert await db["votes"].count_documents({}) == 33
    assert await db["outcomes"].count_documents({}) == 65


async def test_a_restart_does_not_duplicate_anything(db) -> None:  # type: ignore[no-untyped-def]
    store, _ = await _seed(db)
    rows = build_seed_rows(cases(), COMMITTEE)
    again = await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)
    assert again == {"packs": 0, "votes": 0, "outcomes": 0}
    assert await db["outcomes"].count_documents({}) == 65


async def test_closed_cases_read_back_through_the_store(db) -> None:  # type: ignore[no-untyped-def]
    store, _ = await _seed(db)
    for case in closed_cases():
        expected = Outcome(platform_outcome(case["outcome"]))
        if case["score"]["route"] == "committee":
            pack = await store.latest_pack(case["id"])
            assert pack is not None
            assert pack.decided is True
            outcome = await store.get_outcome(pack.pack_id)
            assert await store.get_outcome_mode(pack.pack_id) == "committee"
            votes = await store.votes_for_round(pack.pack_id, 1)
            assert tally_round([v.option.value for v in votes], 1).outcome == expected
        else:
            pack_id = f"{DELEGATED_PACK_PREFIX}{case['id']}"
            outcome = await store.get_outcome(pack_id)
            assert await store.get_outcome_mode(pack_id) == "delegated"
            assert await store.latest_pack(case["id"]) is None
        assert outcome is not None
        assert outcome.outcome == expected


async def test_the_case_index_exists_for_latest_pack(db) -> None:  # type: ignore[no-untyped-def]
    await ensure_indexes(db)
    names = {ix["name"] async for ix in await db["packs"].list_indexes()}
    assert {"pack_id_unique", "case_id_version"} <= names


async def test_a_seeded_open_pack_takes_a_vote_and_a_decided_one_refuses(db) -> None:  # type: ignore[no-untyped-def]
    store, _ = await _seed(db)
    vote = _VoteRow(
        pack_id=OPEN_PACK,
        round=1,
        member_id=COMMITTEE[0],
        option=VoteOption.APPROVE,
        rationale="Residual risk is within appetite for this change.",
        cast_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    recorded = await store.record_vote(vote, decide=lambda ballot: None)  # type: ignore[arg-type, return-value]
    assert recorded.votes_cast == 1
    assert recorded.outcome is None

    decided = next(c for c in closed_cases() if c["score"]["route"] == "committee")
    with pytest.raises(RuleViolationError):
        await store.record_vote(
            vote.model_copy(update={"pack_id": pack_id_for(decided["id"])}),
            decide=lambda ballot: None,  # type: ignore[arg-type, return-value]
        )

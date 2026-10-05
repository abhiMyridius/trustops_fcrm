"""S5's seed rows against a real MongoDB: one bulk insert per collection into
an empty store (65 outcomes, 13 packs, 33 votes), nothing on a second start, the
closed cases reading back through the store, and a seeded committee-stage pack
that really takes a vote through S5's transaction and outbox."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fcrm_common.outbox import Outbox
from fcrm_contracts.enums import Outcome, Role
from fcrm_contracts.voting import tally
from fcrm_decision.seedmodel import (
    DELEGATED_PACK_PREFIX,
    SeedReference,
    build_seed_rows,
    pack_id_for,
)
from fcrm_decision.store_mongo import MongoDecisionStore, ensure_indexes
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
async def db() -> AsyncIterator[Any]:
    client: AsyncMongoClient[Any] = AsyncMongoClient(URI, tz_aware=True)
    await client.drop_database(DB)
    yield client[DB]
    await client.drop_database(DB)
    await client.close()


def _rows() -> Any:
    return build_seed_rows(cases(), COMMITTEE, SeedReference.from_seed(load_raw()))


async def _seed(db: Any) -> tuple[MongoDecisionStore, dict[str, int]]:
    outbox = Outbox(db["outbox"])
    await ensure_indexes(db, outbox)
    store = MongoDecisionStore(db, outbox)
    rows = _rows()
    return store, await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)


async def test_an_empty_store_gets_every_seed_row(db: Any) -> None:
    _, loaded = await _seed(db)
    assert loaded == {"packs": 13, "votes": 33, "outcomes": 65}
    assert await db["packs"].count_documents({}) == 13
    assert await db["votes"].count_documents({}) == 33
    assert await db["outcomes"].count_documents({}) == 65


async def test_a_restart_does_not_duplicate_anything(db: Any) -> None:
    store, _ = await _seed(db)
    rows = _rows()
    again = await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)
    assert again == {"packs": 0, "votes": 0, "outcomes": 0}
    assert await db["outcomes"].count_documents({}) == 65


async def test_closed_cases_read_back_through_the_store(db: Any) -> None:
    store, _ = await _seed(db)
    for case in closed_cases():
        expected = Outcome(platform_outcome(case["outcome"]))
        row = await store.case_outcome(case["id"])
        assert row is not None, case["id"]
        assert row.outcome.outcome == expected
        if case["score"]["route"] == "committee":
            pack = await store.latest_pack(case["id"])
            assert pack is not None
            assert pack.decided is True
            assert row.mode == "committee"
            votes = await store.round_votes(pack.pack_id, 1)
            assert tally([v.option.value for v in votes]).outcome == expected
        else:
            assert row.mode == "delegated"
            assert row.outcome.pack_id == f"{DELEGATED_PACK_PREFIX}{case['id']}"
            assert await store.latest_pack(case["id"]) is None


async def test_the_committee_queue_reads_the_seed_in_one_aggregation(db: Any) -> None:
    store, _ = await _seed(db)
    queue = await store.committee_queue(limit=50)
    assert len(queue) == 13
    by_status = {entry.pack_id: entry for entry in queue}
    assert by_status[OPEN_PACK].decided is False
    assert by_status[OPEN_PACK].voters == []
    decided = [entry for entry in queue if entry.decided]
    assert len(decided) == 11
    assert all(
        len(entry.voters) == 3 and entry.outcome is not None for entry in decided
    )
    assert all(entry.band_label for entry in queue)
    assert all(entry.analyst_name for entry in queue)


async def test_a_seeded_committee_stage_pack_takes_a_vote_and_announces_it(
    live_decision: Any,
) -> None:
    live = live_decision
    first = await live.http.post(
        f"/packs/{OPEN_PACK}/votes",
        json={"vote": "approve_with_conditions", "rationale": "x" * 30},
        headers=live.headers(Role.COMMITTEE, COMMITTEE[0]),
    )
    assert first.status_code == 201, first.text
    assert first.json()["round"] == 1
    assert first.json()["cast_n_of_3"] == 1
    rows = await live.db["outbox"].find({"type": "vote.cast"}).to_list()
    assert [r["case_id"] for r in rows] == ["FCRM-2026-0138"]

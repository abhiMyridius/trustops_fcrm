"""S5's lifespan, indexes, queue aggregation and idempotency keys on a real
MongoDB 8 replica set (see ``test_decision_votes_live.py`` for how to start one).

What only a real database can show: the indexes S5's queries and invariants
depend on exist (and replace the legacy ones an earlier build created), the
committee queue is one aggregation, an ``Idempotency-Key`` replays across
requests and carries its case, and the relay marks what it publishes.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_common.outbox import Outbox
from fcrm_contracts.enums import Role
from fcrm_decision.store_mongo import MongoDecisionStore, ensure_indexes
from pymongo import AsyncMongoClient, monitoring
from pymongo.errors import DuplicateKeyError

if TYPE_CHECKING:
    from conftest import LiveDecision

pytestmark = pytest.mark.integration

CASE = "FCRM-2026-0151"
SERVICE = "svc:caseflow"
ANALYST = "u-riyer"
REASON = "The corridor review lowers the CTL-T01 rating, so the numbers change."


class CommandLog(monitoring.CommandListener):
    """Records the name of every command a client sends, to count what a read
    costs.
    """

    def __init__(self) -> None:
        self.names: list[str] = []

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        self.names.append(event.command_name)

    def succeeded(self, event: monitoring.CommandSucceededEvent) -> None:
        return None

    def failed(self, event: monitoring.CommandFailedEvent) -> None:
        return None


async def _index_info(db: Any, collection: str) -> dict[str, Any]:
    info: dict[str, Any] = await db[collection].index_information()
    return info


# ── indexes ──────────────────────────────────────────────────────────────


async def test_every_index_s5_depends_on_exists_with_the_right_options(
    live_decision: LiveDecision,
) -> None:
    db = live_decision.db
    packs = await _index_info(db, "packs")
    assert packs["case_id_version_unique"]["unique"] is True
    assert packs["case_id_version_unique"]["key"] == [("case_id", 1), ("version", 1)]
    assert packs["pack_id_unique"]["unique"] is True
    assert packs["released_queue"]["key"] == [("released_at", -1)]
    votes = await _index_info(db, "votes")
    assert votes["one_vote_per_member_per_round"]["unique"] is True
    assert votes["one_vote_per_member_per_round"]["key"] == [
        ("pack_id", 1),
        ("round", 1),
        ("member_id", 1),
    ]
    outcomes = await _index_info(db, "outcomes")
    assert outcomes["outcome_pack_round"]["key"] == [
        ("outcome.pack_id", 1),
        ("outcome.round", -1),
    ]
    assert outcomes["outcome_case_round"]["key"] == [
        ("outcome.case_id", 1),
        ("outcome.round", -1),
    ]
    assert (await _index_info(db, "questions"))["pack_created"]["key"] == [
        ("pack_id", 1),
        ("created_at", 1),
    ]
    assert (await _index_info(db, "idempotency_keys"))["key_ttl"][
        "expireAfterSeconds"
    ] == 86_400
    outbox = await _index_info(db, "outbox")
    assert {"unpublished_scan", "event_id_unique", "published_ttl"} <= set(outbox)


async def test_ensure_indexes_is_idempotent_and_replaces_the_legacy_ones(
    live_decision: LiveDecision,
) -> None:
    db = live_decision.db
    # What an earlier build left behind: a non-unique (case_id, version desc)
    # index and a unique index on a field threads no longer have.
    await db["packs"].create_index(
        [("case_id", 1), ("version", -1)], name="case_id_version"
    )
    await db["questions"].create_index(
        [("question_id", 1)], unique=True, name="question_id_unique"
    )

    await ensure_indexes(db, Outbox(db["outbox"]))
    await ensure_indexes(db, Outbox(db["outbox"]))  # twice: still fine

    assert "case_id_version" not in await _index_info(db, "packs")
    assert "question_id_unique" not in await _index_info(db, "questions")
    assert "case_id_version_unique" in await _index_info(db, "packs")


async def test_a_second_pack_of_the_same_version_is_refused_by_the_index(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    built = await live_decision.http.post(
        "/packs",
        json={"case_id": CASE, "snapshot": release_snapshot(CASE)},
        headers=live_decision.headers(Role.SERVICE, SERVICE),
    )
    assert built.status_code == 201
    stored = await live_decision.db["packs"].find_one({"case_id": CASE})
    assert stored is not None
    duplicate = {key: value for key, value in stored.items() if key != "_id"}
    duplicate["pack_id"] = "pack_dup"
    with pytest.raises(DuplicateKeyError):
        await live_decision.db["packs"].insert_one(duplicate)


# ── the queue is one aggregation ─────────────────────────────────────────


async def test_the_committee_queue_costs_one_aggregation_and_nothing_else(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    pack = await live_decision.http.post(
        "/packs",
        json={"case_id": CASE, "snapshot": release_snapshot(CASE)},
        headers=live_decision.headers(Role.SERVICE, SERVICE),
    )
    await live_decision.http.post(
        f"/packs/{pack.json()['pack_id']}/release",
        headers=live_decision.headers(Role.ANALYST, ANALYST),
    )
    log = CommandLog()
    watched: AsyncMongoClient[Any] = AsyncMongoClient(
        os.environ["FCRM_TEST_MONGO_URI"], tz_aware=True, event_listeners=[log]
    )
    try:
        db = watched[live_decision.db.name]
        store = MongoDecisionStore(db, Outbox(db["outbox"]))
        log.names.clear()
        entries = await store.committee_queue(limit=50)
    finally:
        await watched.close()

    assert len(entries) == 14  # 13 seeded + the demo case's pack
    data_commands = [
        n for n in log.names if n in {"find", "aggregate", "getMore", "count"}
    ]
    assert data_commands == ["aggregate"]
    mine = next(e for e in entries if e.case_id == CASE)
    assert mine.voters == []
    assert mine.decided is False


# ── Idempotency-Key on a real database ───────────────────────────────────


async def test_an_idempotency_key_replays_across_requests_and_carries_its_case(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    http = live_decision.http
    built = await http.post(
        "/packs",
        json={"case_id": CASE, "snapshot": release_snapshot(CASE)},
        headers=live_decision.headers(Role.SERVICE, SERVICE),
    )
    pack_id = built.json()["pack_id"]
    headers = {
        **live_decision.headers(Role.SERVICE, SERVICE),
        "Idempotency-Key": "step-unlock-1",
    }

    first = await http.post(
        f"/packs/{pack_id}/supersede", json={"reason": REASON}, headers=headers
    )
    again = await http.post(
        f"/packs/{pack_id}/supersede", json={"reason": REASON}, headers=headers
    )

    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    assert (
        await live_decision.db["outbox"].count_documents({"type": "pack.superseded"})
        == 1
    )
    key = await live_decision.db["idempotency_keys"].find_one({"_id": "step-unlock-1"})
    assert key is not None
    assert key["case_id"] == CASE
    assert key["kind"] == "pack.supersede"
    reused = await http.post(
        f"/packs/{pack_id}/supersede",
        json={"reason": "a different reason, still long enough"},
        headers=headers,
    )
    assert reused.status_code == 409
    assert reused.json()["type"].endswith("/idempotency_key_reused")


async def test_concurrent_requests_with_one_key_produce_one_effect(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    http = live_decision.http
    headers = {
        **live_decision.headers(Role.SERVICE, SERVICE),
        "Idempotency-Key": "build-race-1",
    }
    body = {"case_id": CASE, "snapshot": release_snapshot(CASE)}

    results = await asyncio.gather(
        *(http.post("/packs", json=body, headers=headers) for _ in range(4))
    )

    assert all(r.status_code in (200, 201, 409) for r in results), [
        r.text for r in results
    ]
    assert await live_decision.db["packs"].count_documents({"case_id": CASE}) == 1
    succeeded = {r.json()["pack_id"] for r in results if r.status_code in (200, 201)}
    assert len(succeeded) == 1


# ── the relay ────────────────────────────────────────────────────────────


async def test_the_relay_stamps_what_it_publishes_and_reports_ready(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    built = await live_decision.http.post(
        "/packs",
        json={"case_id": CASE, "snapshot": release_snapshot(CASE)},
        headers=live_decision.headers(Role.SERVICE, SERVICE),
    )
    await live_decision.http.post(
        f"/packs/{built.json()['pack_id']}/release",
        headers=live_decision.headers(Role.ANALYST, ANALYST),
    )

    async with asyncio.timeout(5):
        while not live_decision.publisher.sent:
            await asyncio.sleep(0.02)
    sent = live_decision.publisher.sent[0]
    assert sent.type == "pack.released"
    assert sent.case_id == CASE
    async with asyncio.timeout(5):
        while await live_decision.db["outbox"].count_documents({"published_at": None}):
            await asyncio.sleep(0.02)
    lease = await live_decision.db["leases"].find_one({"_id": "outbox-relay:decision"})
    assert lease is not None
    assert lease["holder"] == "relay_test"
    status, body = await live_decision.app.state.probe.run()
    assert status == 200
    assert body["checks"]["outbox"]["ok"] is True

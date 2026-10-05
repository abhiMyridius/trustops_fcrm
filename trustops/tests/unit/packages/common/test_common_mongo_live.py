"""fcrm_common against a real MongoDB 8 replica set: Repository, pagination,
transactions, the outbox + relay, leases, consumer dedupe and seed loading.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set, e.g. a
single-node set started with ``mongod --replSet rs0 --port 27117`` and
initiated with ``rs.initiate()``::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27117/?replicaSet=rs0'
    uv run --project src --package fcrm-common pytest \\
        tests/unit/packages/common/test_common_mongo_live.py

The testcontainers-based integration lane will run the same scenarios
under ``tests/integration`` with an ephemeral container.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import Any

import pytest
from fcrm_common.events import Consumer, Disposition, build_envelope, encode_envelope
from fcrm_common.ids import new_id
from fcrm_common.mongo import MongoLease, Repository, mongo_client, with_transaction
from fcrm_common.outbox import Outbox, OutboxRelay
from fcrm_common.seedload import load_if_empty
from fcrm_contracts.enums import ItemStatus
from fcrm_contracts.events.envelope import Envelope
from pydantic import BaseModel, ConfigDict
from pymongo import ASCENDING, IndexModel
from pymongo.errors import DuplicateKeyError

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    case_id: str
    status: ItemStatus
    rating: Decimal


@pytest.fixture
async def db():
    async with mongo_client(URI) as client:
        name = f"fcrm_common_it_{new_id('db')[3:].lower()}"
        database = client[name]
        yield client, database
        await client.drop_database(name)


def item(n: int) -> Item:
    return Item(
        item_id=f"itm_{n:02d}",
        case_id="FCRM-2026-0151",
        status=ItemStatus.PROPOSED,
        rating=Decimal("7.6"),
    )


async def test_repository_crud_round_trip(db):
    _, database = db
    repo = Repository(database["items"], Item, id_of=lambda i: i.item_id)
    await repo.ensure_indexes([IndexModel([("case_id", ASCENDING)])])
    assert await repo.insert_one(item(1)) == "itm_01"
    with pytest.raises(DuplicateKeyError):
        await repo.insert_one(item(1))
    assert await repo.insert_many([item(2), item(3)]) == ["itm_02", "itm_03"]
    assert await repo.find_one({"item_id": "itm_01"}) == item(1)
    updated = await repo.update_one(
        {"_id": "itm_01"}, {"status": ItemStatus.OVERRIDDEN, "rating": Decimal("4.5")}
    )
    assert updated.modified_count == 1
    after = await repo.find_one_and_update(
        {"_id": "itm_02"}, {"$set": {"status": ItemStatus.CONFIRMED}}
    )
    assert after is not None
    assert after.status is ItemStatus.CONFIRMED
    first = await repo.find_one({"_id": "itm_01"})
    assert first is not None
    assert first.rating == Decimal("4.5")
    assert await repo.count({"case_id": "FCRM-2026-0151"}) == 3
    grouped = await repo.aggregate(
        [{"$group": {"_id": "$status", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    )
    assert {g["_id"]: g["n"] for g in grouped} == {
        "confirmed": 1,
        "overridden": 1,
        "proposed": 1,
    }
    ordered = await repo.find(sort=[("_id", -1)], limit=2)
    assert [i.item_id for i in ordered] == ["itm_03", "itm_02"]


async def test_find_paginated_walks_all_pages(db):
    _, database = db
    repo = Repository(database["items"], Item, id_of=lambda i: i.item_id)
    await repo.insert_many([item(n) for n in range(1, 8)])
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = await repo.find_paginated(
            {"case_id": "FCRM-2026-0151"}, limit=3, cursor=cursor, with_total=True
        )
        assert page.total == 7
        seen += [i.item_id for i in page.items]
        pages += 1
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert pages == 3
    assert seen == [f"itm_{n:02d}" for n in range(1, 8)]
    newest = await repo.find_paginated(limit=2, descending=True)
    assert [i.item_id for i in newest.items] == ["itm_07", "itm_06"]
    older = await repo.find_paginated(
        limit=2, descending=True, cursor=newest.next_cursor
    )
    assert [i.item_id for i in older.items] == ["itm_05", "itm_04"]


async def test_outbox_commits_and_aborts_with_state(db):
    client, database = db
    state = database["cases"]
    outbox = Outbox(database["outbox"])
    await outbox.ensure_indexes()
    await state.insert_one({"_id": "warmup"})  # collections exist before txns

    async def write(session) -> Envelope:
        await state.insert_one({"_id": "FCRM-2026-0152"}, session=session)
        return await outbox.enqueue(
            session, build_envelope("case.created", {}, case_id="FCRM-2026-0152")
        )

    queued = await with_transaction(client, write)
    assert await database["outbox"].count_documents({"event_id": queued.event_id}) == 1

    async def failing(session) -> None:
        await state.insert_one({"_id": "FCRM-2026-0153"}, session=session)
        await outbox.enqueue(
            session, build_envelope("case.created", {}, case_id="FCRM-2026-0153")
        )
        raise RuntimeError("business rule failed after the writes")

    with pytest.raises(RuntimeError):
        await with_transaction(client, failing)
    assert await state.count_documents({"_id": "FCRM-2026-0153"}) == 0
    assert await database["outbox"].count_documents({"case_id": "FCRM-2026-0153"}) == 0


class Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Envelope, dict[str, Any]]] = []

    async def publish(self, exchange, routing_key, body, headers) -> None:
        self.sent.append(
            (exchange, routing_key, Envelope.model_validate_json(body), headers)
        )


async def test_relay_publishes_committed_rows_once_with_lease(db):
    client, database = db
    outbox = Outbox(database["outbox"])
    await outbox.ensure_indexes()
    events = []
    for n in range(3):

        async def write(session, n=n) -> Envelope:
            return await outbox.enqueue(
                session, build_envelope("agent.run", {"n": n}, run_id="run_1")
            )

        events.append(await with_transaction(client, write))
    publisher = Recorder()
    leader = MongoLease(database["leases"], "outbox-relay", "relay-a")
    relay = OutboxRelay(database["outbox"], leader, publisher)
    assert await relay.tick() == (3, False)
    assert await relay.tick() == (0, False)
    assert [s[2].event_id for s in publisher.sent] == [e.event_id for e in events]
    assert {s[0] for s in publisher.sent} == {"agent.jobs"}
    standby = OutboxRelay(
        database["outbox"],
        MongoLease(database["leases"], "outbox-relay", "relay-b"),
        Recorder(),
    )
    assert await standby.maintain_lease() is False


async def test_mongo_lease_contention_release_and_expiry(db):
    _, database = db
    a = MongoLease(database["leases"], "lease:case:x", "a", ttl_s=1.5, margin_s=0.5)
    b = MongoLease(database["leases"], "lease:case:x", "b", ttl_s=1.5, margin_s=0.5)
    assert await a.acquire() is True
    assert await b.acquire() is False
    assert await a.acquire() is True  # renew
    await a.release()
    assert await b.acquire() is True
    assert b.held
    await asyncio.sleep(1.7)  # b's lease lapses on the server clock
    assert not b.held
    assert await a.acquire() is True


class Applier(Consumer):
    def __init__(self, database, publisher, client) -> None:
        super().__init__(
            "caseflow.events", database["consumed_messages"], publisher, client=client
        )
        self.state = database["applied"]

    async def handle(self, envelope, session) -> None:
        await self.state.insert_one({"event_id": envelope.event_id}, session=session)


class Acks:
    def __init__(self) -> None:
        self.acked = 0

    async def ack(self) -> None:
        self.acked += 1

    async def nack(self, requeue: bool) -> None:
        raise AssertionError("unexpected nack")


async def test_consumer_transactional_dedupe(db):
    client, database = db
    consumer = Applier(database, Recorder(), client)
    await consumer.ensure_indexes()
    await database["applied"].insert_one({"warmup": True})
    body = encode_envelope(build_envelope("gate.decided", {}, run_id="run_1"))
    acks = Acks()
    first = await consumer.process(body, {}, acks.ack, acks.nack)
    second = await consumer.process(body, {}, acks.ack, acks.nack)
    assert (first, second) == (Disposition.PROCESSED, Disposition.DUPLICATE)
    assert (
        await database["applied"].count_documents({"event_id": {"$exists": True}}) == 1
    )
    assert acks.acked == 2


async def test_load_if_empty_against_real_collection(db):
    _, database = db
    docs = [{"_id": "u-riyer", "role": "fcrm_analyst"}, {"_id": "u-pnair"}]
    assert await load_if_empty(database["users"], docs) == 2
    assert await load_if_empty(database["users"], docs) == 0
    assert await database["users"].count_documents({}) == 2

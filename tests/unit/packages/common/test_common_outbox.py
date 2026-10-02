"""fcrm_common.outbox: transactional enqueue and the relay's ordering,
failure and leadership behaviour, against in-memory fakes. The same paths
against a real replica set are in ``test_common_mongo_live.py``.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from fcrm_common.events import build_envelope
from fcrm_common.metrics import get_metrics
from fcrm_common.outbox import Outbox, OutboxRelay, OutboxTransactionError
from fcrm_common.time import utcnow
from fcrm_contracts.events.envelope import Envelope
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


class FakeSession:
    def __init__(self, in_transaction: bool) -> None:
        self.in_transaction = in_transaction


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self) -> list[dict[str, Any]]:
        return self._docs


class FakeOutboxCollection:
    """The handful of operations Outbox/OutboxRelay issue."""

    full_name = "fcrm_case.outbox"

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.sessions: list[Any] = []

    async def insert_one(self, doc, session=None):
        self.sessions.append(session)
        self.docs.append(doc)

    def _unpublished(self) -> list[dict[str, Any]]:
        return sorted(
            (d for d in self.docs if d["published_at"] is None), key=lambda d: d["_id"]
        )

    def find(self, query, sort=None, limit=0):
        assert query == {"published_at": None}
        docs = self._unpublished()
        return FakeCursor(docs[:limit] if limit else docs)

    async def find_one(self, query, sort=None, projection=None):
        docs = self._unpublished()
        return docs[0] if docs else None

    async def update_one(self, query, update):
        for doc in self.docs:
            if doc["_id"] == query["_id"] and all(
                doc.get(k) == v for k, v in query.items() if k != "_id"
            ):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                for key, value in update.get("$inc", {}).items():
                    doc[key] = doc.get(key, 0) + value


class FakeLease:
    def __init__(self, held: bool = True) -> None:
        self._held = held
        self.acquired = 0
        self.released = False

    @property
    def held(self) -> bool:
        return self._held

    async def acquire(self) -> bool:
        self.acquired += 1
        return self._held

    async def release(self) -> None:
        self.released = True
        self._held = False


class FlakyPublisher:
    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.sent: list[Envelope] = []
        self.calls = 0
        self.fail_on = fail_on or set()

    async def publish(self, exchange, routing_key, body, headers) -> None:
        self.calls += 1
        if self.calls in self.fail_on:
            raise ConnectionError("broker down")
        self.sent.append(Envelope.model_validate_json(body))


def env(n: int) -> Envelope:
    return build_envelope(
        "gate.decided", {"n": n}, case_id="FCRM-2026-0151", run_id="run_1"
    )


async def filled_outbox(
    count: int,
) -> tuple[Outbox, FakeOutboxCollection, list[Envelope]]:
    coll = FakeOutboxCollection()
    outbox = Outbox(coll)  # type: ignore[arg-type]
    session = FakeSession(in_transaction=True)
    queued = [await outbox.enqueue(session, env(i)) for i in range(count)]  # type: ignore[arg-type]
    return outbox, coll, queued


def relay_for(coll, lease, publisher, reader=None) -> OutboxRelay:
    metrics = get_metrics(MeterProvider(metric_readers=[reader] if reader else []))
    return OutboxRelay(coll, lease, publisher, metrics=metrics, interval_s=0.01)


async def test_enqueue_requires_an_active_transaction():
    outbox = Outbox(FakeOutboxCollection())  # type: ignore[arg-type]
    with pytest.raises(OutboxTransactionError):
        await outbox.enqueue(FakeSession(in_transaction=False), env(0))  # type: ignore[arg-type]


async def test_enqueue_writes_in_callers_session_with_routing():
    _, coll, queued = await filled_outbox(1)
    [doc] = coll.docs
    assert coll.sessions[0].in_transaction
    assert doc["event_id"] == queued[0].event_id
    assert doc["exchange"] == "case.events"
    assert doc["routing_key"] == "gate.decided"
    assert doc["published_at"] is None
    assert doc["headers"]["x-schema"] == "fcrm.events/gate.decided@1"
    assert Envelope.model_validate_json(doc["body"]) == queued[0]


async def test_relay_publishes_in_id_order_and_stamps_rows():
    _, coll, queued = await filled_outbox(5)
    publisher = FlakyPublisher()
    relay = relay_for(coll, FakeLease(), publisher)
    published, failed = await relay.tick()
    assert (published, failed) == (5, False)
    assert [e.event_id for e in publisher.sent] == [e.event_id for e in queued]
    assert all(d["published_at"] is not None for d in coll.docs)


async def test_relay_stops_at_first_failure_to_keep_order():
    _, coll, queued = await filled_outbox(4)
    publisher = FlakyPublisher(fail_on={2})
    relay = relay_for(coll, FakeLease(), publisher)
    assert await relay.publish_batch() == (1, True)
    assert coll.docs[1]["attempts"] == 1
    assert coll.docs[1]["last_error"] == "ConnectionError"
    assert [d["published_at"] is None for d in coll.docs] == [False, True, True, True]
    assert await relay.publish_batch() == (3, False)
    assert [e.event_id for e in publisher.sent] == [e.event_id for e in queued]


async def test_relay_does_not_publish_without_the_lease():
    _, coll, _ = await filled_outbox(2)
    publisher = FlakyPublisher()
    lease = FakeLease(held=False)
    relay = relay_for(coll, lease, publisher)
    assert await relay.tick() == (0, False)
    assert publisher.sent == []
    assert lease.acquired == 1


async def test_relay_records_outbox_lag():
    _, coll, _ = await filled_outbox(1)
    coll.docs[0]["created_at"] = utcnow() - timedelta(seconds=30)
    reader = InMemoryMetricReader()
    relay = relay_for(coll, FakeLease(), FlakyPublisher(fail_on={1}), reader)
    await relay.publish_batch()
    data = reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics
    [lag] = [m for m in data if m.name == "fcrm_outbox_lag_seconds"]
    assert lag.data.data_points[0].value >= 30


async def test_relay_run_drains_and_releases_on_stop():
    _, coll, _ = await filled_outbox(3)
    publisher = FlakyPublisher()
    lease = FakeLease()
    relay = relay_for(coll, lease, publisher)
    stop = asyncio.Event()
    task = asyncio.create_task(relay.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert len(publisher.sent) == 3
    assert lease.released

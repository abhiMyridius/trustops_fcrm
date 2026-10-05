"""S1's ``case-service.events`` consumer on a real MongoDB 8 replica set: the
dedupe marker and the activity row commit in one transaction, concurrent events
for one case get gap-free sequence numbers, and a failure after the row was
staged leaves neither the row nor the marker. The in-memory behaviour is
``tests/unit/services/case/test_case_consumer.py``; the real broker path was
exercised ad hoc (a late subscription on a started ``RabbitBroker``).

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28317/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/integration/live_mongo/test_case_consumer_live.py
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.consumer import QUEUE, CaseServiceEventsConsumer
from fcrm_common.events import (
    Disposition,
    build_envelope,
    encode_envelope,
    message_headers,
)
from fcrm_contracts.events.envelope import Envelope

if TYPE_CHECKING:
    from conftest import LiveCase, TopicRecorder

pytestmark = pytest.mark.integration

DEMO = "FCRM-2026-0151"


def reset(round_no: int, case_id: str = DEMO) -> Envelope:
    return build_envelope(
        "vote.rerequested",
        {
            "pack_id": "pack_1",
            "version": 1,
            "round": round_no,
            "member_ids": ["u-jokafor"],
            "reason": "no_majority",
        },
        case_id=case_id,
    )


async def deliver(
    consumer: CaseServiceEventsConsumer, envelope: Envelope
) -> Disposition:
    async def ack() -> None:
        return None

    async def nack(requeue: bool) -> None:
        return None

    return await consumer.process(
        encode_envelope(envelope), message_headers(envelope), ack, nack
    )


@pytest.fixture
async def consumer(
    live_case: LiveCase, topic_recorder: TopicRecorder
) -> CaseServiceEventsConsumer:
    consumer = CaseServiceEventsConsumer(
        live_case.db["consumed_messages"],
        topic_recorder,
        activity=live_case.app.state.store.activity,
        hub=live_case.app.state.hub,
        client=live_case.db.client,
        max_attempts=0,
    )
    await consumer.ensure_indexes()
    return consumer


async def _rows(live: LiveCase, event_type: str = "vote.rerequested") -> list[Any]:
    cursor = live.db["activity"].find({"case_id": DEMO, "type": event_type})
    return await cursor.sort("seq", 1).to_list()


async def test_the_row_and_the_marker_commit_together_and_a_redelivery_is_a_noop(
    live_case: LiveCase, consumer: CaseServiceEventsConsumer
) -> None:
    envelope = reset(1)
    assert await deliver(consumer, envelope) is Disposition.PROCESSED
    assert await deliver(consumer, envelope) is Disposition.DUPLICATE
    assert len(await _rows(live_case)) == 1
    markers = (
        await live_case.db["consumed_messages"].find({"consumer": QUEUE}).to_list()
    )
    assert [m["message_id"] for m in markers] == [envelope.event_id]


async def test_concurrent_events_for_one_case_get_gap_free_sequence_numbers(
    live_case: LiveCase, consumer: CaseServiceEventsConsumer
) -> None:
    before = (await live_case.db["cases"].find_one({"_id": DEMO}))["activity_seq"]  # type: ignore[index]
    results = await asyncio.gather(*[deliver(consumer, reset(n)) for n in range(1, 13)])
    assert set(results) == {Disposition.PROCESSED}
    seqs = [row["seq"] for row in await _rows(live_case)]
    assert seqs == list(range(before + 1, before + 13))


async def test_a_failure_after_the_row_was_staged_leaves_neither_row_nor_marker(
    live_case: LiveCase,
    consumer: CaseServiceEventsConsumer,
    topic_recorder: TopicRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity = live_case.app.state.store.activity
    real_stage = activity.stage

    async def stage_then_fail(*args: Any, **kwargs: Any) -> Any:
        await real_stage(*args, **kwargs)
        raise RuntimeError("the connection dropped after the row")

    monkeypatch.setattr(activity, "stage", stage_then_fail)
    disposition = await deliver(consumer, reset(1))
    assert disposition is Disposition.DEAD_LETTERED  # max_attempts=0 in this fixture
    assert await _rows(live_case) == []
    assert await live_case.db["consumed_messages"].count_documents({}) == 0
    seq = (await live_case.db["cases"].find_one({"_id": DEMO}))["activity_seq"]  # type: ignore[index]
    assert seq == 0  # the sequence number was rolled back with the row
    assert [x for x, _, _ in topic_recorder.published] == ["dlx"]


async def test_an_event_for_a_case_that_is_not_there_writes_nothing(
    live_case: LiveCase, consumer: CaseServiceEventsConsumer
) -> None:
    assert await deliver(consumer, reset(1, "FCRM-2026-9999")) is Disposition.PROCESSED
    assert (
        await live_case.db["activity"].count_documents({"type": "vote.rerequested"})
        == 0
    )

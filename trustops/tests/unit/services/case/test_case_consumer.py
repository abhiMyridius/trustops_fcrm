"""S1's bus consumer (``case-service.events``): what each event becomes on the case
stream and who may see it, that the PO is told one plain thing and nothing of the
committee's process, and that delivery is exactly once (dedupe, retry, drop).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from faststream.rabbit import RabbitBroker
from fcrm_case.consumer import (
    EXCHANGE,
    QUEUE,
    ROUTING_KEYS,
    CaseServiceEventsConsumer,
    subscribe,
)
from fcrm_case.domain.activity import ActivityEntry, visible_to
from fcrm_case.domain.mirror import mirror_for
from fcrm_common.events import (
    Disposition,
    build_envelope,
    encode_envelope,
    message_headers,
)
from fcrm_contracts.events.envelope import Envelope

if TYPE_CHECKING:
    from conftest import CaseService, FakeConsumedCollection, TopicRecorder
    from fcrm_case.domain.record import CaseRecord

CASE = "FCRM-2026-0151"

RELEASED = {
    "pack_id": "pack_1",
    "version": 1,
    "released_to": ["u-amendes", "u-jokafor", "u-lchen"],
    "released_to_names": ["Ana Mendes", "Joseph Okafor", "Lin Chen"],
    "content_hash": "ab" * 32,
    "release_checks_passed": True,
}
RESET = {
    "pack_id": "pack_1",
    "version": 2,
    "round": 1,
    "member_ids": ["u-jokafor"],
    "reason": "superseded",
}
OUTCOME_SET = {
    "case_id": CASE,
    "mode": "committee",
    "outcome": "approve_with_conditions",
    "pack_id": "pack_1",
    "conditions": [{"id": "CND-1", "text": "Review filed"}],
}
DELIVERED = {
    "outcome": "approve_with_conditions",
    "outcome_label": "Approved with conditions",
    "conditions": 3,
}


def event(event_type: str, payload: dict[str, Any], case_id: str = CASE) -> Envelope:
    return build_envelope(event_type, payload, case_id=case_id)


# ── what each event becomes ─────────────────────────────────────────────────


def entry_for(envelope: Envelope) -> ActivityEntry:
    mirrored = mirror_for(envelope)
    assert mirrored is not None
    return ActivityEntry(
        case_id=CASE, seq=1, at=envelope.occurred_at, **mirrored.model_dump()
    )


@pytest.mark.parametrize(
    ("event_type", "payload", "summary"),
    [
        (
            "pack.released",
            RELEASED,
            "Pack v1 released to the committee (3 members)",
        ),
        (
            "vote.rerequested",
            RESET,
            "Votes requested again: round 1 on pack v2",
        ),
        (
            "outcome.set",
            OUTCOME_SET,
            "Outcome recorded: Approved with conditions (committee)",
        ),
    ],
)
def test_the_committees_process_is_the_analysts_and_never_the_pos(
    event_type: str, payload: dict[str, Any], summary: str
) -> None:
    entry = entry_for(event(event_type, payload))
    assert (entry.type, entry.visibility, entry.summary) == (
        event_type,
        "analyst",
        summary,
    )
    assert visible_to(entry, "staff")
    assert not visible_to(entry, "po")


def test_the_po_is_told_one_plain_outcome_with_a_count() -> None:
    entry = entry_for(event("outcome.delivered", DELIVERED))
    assert entry.visibility == "all"
    assert entry.summary == (
        "Your change was decided: Approved with conditions · 3 conditions apply"
    )
    assert entry.detail == {
        "outcome_label": "Approved with conditions",
        "conditions": 3,
    }
    assert visible_to(entry, "po")
    assert visible_to(entry, "staff")


def test_a_decided_outcome_with_no_condition_says_nothing_about_conditions() -> None:
    entry = entry_for(
        event(
            "outcome.delivered",
            {"outcome": "approve", "outcome_label": "Approved", "conditions": 0},
        )
    )
    assert entry.summary == "Your change was decided: Approved"


def test_an_outcome_label_that_names_a_restricted_term_never_reaches_the_po() -> None:
    entry = entry_for(
        event(
            "outcome.delivered",
            {
                "outcome": "approve",
                "outcome_label": "Approved at Band 2",
                "conditions": 0,
            },
        )
    )
    assert entry.visibility == "analyst"
    assert not visible_to(entry, "po")


@pytest.mark.parametrize("event_type", ["index.built", "case.closed", "gate.decided"])
def test_an_event_s1_shows_nothing_for_is_not_mirrored(event_type: str) -> None:
    assert mirror_for(event(event_type, {"anything": 1})) is None


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("pack.released", {"pack_id": "pack_1"}),
        ("outcome.delivered", {"outcome": "approve"}),
        ("outcome.delivered", {**DELIVERED, "extra": "x"}),
        ("outcome.set", {"mode": "committee"}),
    ],
)
def test_a_payload_that_is_not_what_its_type_promises_raises(
    event_type: str, payload: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="validation"):
        mirror_for(event(event_type, payload))


# ── delivery ────────────────────────────────────────────────────────────────


class Acks:
    def __init__(self) -> None:
        self.acked = 0
        self.nacked = 0

    async def ack(self) -> None:
        self.acked += 1

    async def nack(self, requeue: bool) -> None:
        self.nacked += 1


@pytest.fixture
def consumer(
    case_service: CaseService,
    stored_case: CaseRecord,
    case_consumed: FakeConsumedCollection,
    topic_recorder: TopicRecorder,
) -> CaseServiceEventsConsumer:
    return CaseServiceEventsConsumer(
        case_consumed,  # type: ignore[arg-type]
        topic_recorder,
        activity=case_service.store.activity,  # type: ignore[arg-type]
        hub=case_service.hub,
    )


async def deliver(
    consumer: CaseServiceEventsConsumer, envelope: Envelope
) -> tuple[Disposition, Acks]:
    acks = Acks()
    disposition = await consumer.process(
        encode_envelope(envelope), message_headers(envelope), acks.ack, acks.nack
    )
    return disposition, acks


async def test_an_event_becomes_a_row_and_reaches_the_open_streams(
    consumer: CaseServiceEventsConsumer, case_service: CaseService
) -> None:
    with case_service.hub.subscription(CASE) as watcher:
        disposition, acks = await deliver(consumer, event("pack.released", RELEASED))
        pushed = watcher.queue.get_nowait()
    assert (disposition, acks.acked) == (Disposition.PROCESSED, 1)
    [row] = case_service.store.activity.entries
    assert row.type == "pack.released"
    assert pushed == row


async def test_a_redelivery_writes_and_publishes_nothing_more(
    consumer: CaseServiceEventsConsumer, case_service: CaseService
) -> None:
    envelope = event("outcome.delivered", DELIVERED)
    await deliver(consumer, envelope)
    with case_service.hub.subscription(CASE) as watcher:
        disposition, acks = await deliver(consumer, envelope)
        assert watcher.queue.empty()
    assert (disposition, acks.acked) == (Disposition.DUPLICATE, 1)
    assert len(case_service.store.activity.entries) == 1


async def test_rows_for_one_case_get_gap_free_sequence_numbers(
    consumer: CaseServiceEventsConsumer, case_service: CaseService
) -> None:
    envelopes = [event("vote.rerequested", {**RESET, "round": n}) for n in range(1, 6)]
    await asyncio.gather(*[deliver(consumer, e) for e in envelopes])
    seqs = sorted(e.seq for e in case_service.store.activity.entries)
    assert seqs == [1, 2, 3, 4, 5]


async def test_an_event_for_a_case_s1_does_not_have_is_acked_and_dropped(
    consumer: CaseServiceEventsConsumer,
    case_service: CaseService,
    topic_recorder: TopicRecorder,
) -> None:
    disposition, acks = await deliver(
        consumer, event("pack.released", RELEASED, case_id="FCRM-2026-9999")
    )
    assert (disposition, acks.acked) == (Disposition.PROCESSED, 1)
    assert case_service.store.activity.entries == []
    assert topic_recorder.published == []


async def test_an_event_s1_shows_nothing_for_is_acked_without_a_row(
    consumer: CaseServiceEventsConsumer, case_service: CaseService
) -> None:
    disposition, acks = await deliver(consumer, event("index.built", {"index": "x"}))
    assert (disposition, acks.acked) == (Disposition.PROCESSED, 1)
    assert case_service.store.activity.entries == []


async def test_a_malformed_payload_is_retried_then_parked_and_never_shown(
    consumer: CaseServiceEventsConsumer,
    case_service: CaseService,
    topic_recorder: TopicRecorder,
) -> None:
    bad = event("outcome.delivered", {"outcome": "approve"})
    disposition, acks = await deliver(consumer, bad)
    assert disposition is Disposition.RETRIED
    assert [exchange for exchange, _, _ in topic_recorder.published] == ["retry"]
    assert case_service.store.activity.entries == []
    assert acks.acked == 1  # the original is acknowledged once the retry is queued


async def test_a_message_with_no_attempts_left_goes_to_the_dead_letter_queue(
    case_service: CaseService,
    stored_case: CaseRecord,
    case_consumed: FakeConsumedCollection,
    topic_recorder: TopicRecorder,
) -> None:
    consumer = CaseServiceEventsConsumer(
        case_consumed,  # type: ignore[arg-type]
        topic_recorder,
        activity=case_service.store.activity,  # type: ignore[arg-type]
        hub=case_service.hub,
        max_attempts=0,
    )
    disposition, _ = await deliver(
        consumer, event("outcome.delivered", {"outcome": "approve"})
    )
    assert disposition is Disposition.DEAD_LETTERED
    assert [exchange for exchange, _, _ in topic_recorder.published] == ["dlx"]


# ── the subscriptions ───────────────────────────────────────────────────────


def test_the_consumer_binds_every_key_ops_bound_to_its_queue(
    consumer: CaseServiceEventsConsumer,
) -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    new = subscribe(broker, consumer)
    assert len(new) == len(ROUTING_KEYS) == 5
    assert {sub.queue.name for sub in new} == {QUEUE}
    assert {sub.queue.routing_key for sub in new} == set(ROUTING_KEYS)
    assert {sub.exchange.name for sub in new if sub.exchange} == {EXCHANGE}
    assert {sub.queue.declare for sub in new} == {False}  # ops owns the topology


def test_a_second_subscribe_returns_only_the_new_subscribers(
    consumer: CaseServiceEventsConsumer,
) -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    first = subscribe(broker, consumer)
    second = subscribe(broker, consumer)
    assert len(first) == len(second) == len(ROUTING_KEYS)
    assert not {id(s) for s in first} & {id(s) for s in second}


async def test_without_a_broker_nothing_is_consumed(
    case_service: CaseService,
    case_consumed: FakeConsumedCollection,
) -> None:
    from contextlib import AsyncExitStack

    from fcrm_case.consumer import start_consumer
    from fcrm_common.service import ServiceDeps

    deps = ServiceDeps(mongo=None, token_verifier=None, opa=None)  # type: ignore[arg-type]
    async with AsyncExitStack() as stack:
        started = await start_consumer(
            stack,
            deps,
            consumed=case_consumed,  # type: ignore[arg-type]
            activity=case_service.store.activity,  # type: ignore[arg-type]
            hub=case_service.hub,
        )
    assert started is None

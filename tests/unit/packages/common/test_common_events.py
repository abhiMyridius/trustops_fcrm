"""fcrm_common.events: envelope completion, headers, retry/DLQ and dedupe
semantics (design A §4.2, §4.5), broker-free.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import pytest
from fcrm_common import events
from fcrm_common.events import (
    Consumer,
    Disposition,
    UnknownEventTypeError,
    build_envelope,
    complete_envelope,
    default_exchange,
    deliver_with_retry,
    encode_envelope,
    message_headers,
    publish,
)
from fcrm_contracts.events.envelope import Envelope
from opentelemetry.sdk.trace import TracerProvider
from pymongo.errors import DuplicateKeyError


class RecordingPublisher:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str, bytes, dict[str, Any]]] = []
        self.fail = fail

    async def publish(self, exchange, routing_key, body, headers) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.sent.append((exchange, routing_key, body, headers))


class Acks:
    def __init__(self) -> None:
        self.acked = 0
        self.nacked: list[bool] = []

    async def ack(self) -> None:
        self.acked += 1

    async def nack(self, requeue: bool) -> None:
        self.nacked.append(requeue)


def envelope(**overrides: Any) -> Envelope:
    return build_envelope(
        overrides.pop("event_type", "case.submitted"),
        {"submission_version": 1},
        case_id="FCRM-2026-0151",
        run_id="run_1",
        **overrides,
    )


def test_build_envelope_fills_id_time_and_correlation():
    env = envelope()
    assert env.event_id.startswith("evt_")
    assert env.occurred_at.tzinfo is not None
    assert env.correlation_id == "run_1"
    assert env.trace_id is None


def test_build_envelope_rejects_uncatalogued_type():
    with pytest.raises(UnknownEventTypeError):
        envelope(event_type="case.made_up")


def test_build_envelope_captures_current_trace():
    tracer = TracerProvider().get_tracer("t")
    with tracer.start_as_current_span("s") as span:
        env = envelope()
        expected = format(span.get_span_context().trace_id, "032x")
    assert env.trace_id == expected
    assert env.span_id is not None


def test_complete_envelope_fills_only_missing_fields():
    raw = Envelope(
        event_id="",
        type="gate.decided",
        run_id="run_9",
        occurred_at=datetime(2026, 10, 1, 12, 0),
        payload={},
    )
    done = complete_envelope(raw)
    assert done.event_id.startswith("evt_")
    assert done.occurred_at.tzinfo is not None
    assert done.correlation_id == "run_9"
    kept = complete_envelope(done)
    assert kept is done


@pytest.mark.parametrize(
    ("event_type", "exchange"),
    [
        ("agent.run", "agent.jobs"),
        ("agent.done", "agent.jobs"),
        ("ingest.run", "ingestion.jobs"),
        ("index.build", "context.jobs"),
        ("case.submitted", "case.events"),
        ("index.built", "case.events"),
    ],
)
def test_default_exchange_follows_topology(event_type, exchange):
    assert default_exchange(event_type) == exchange


def test_message_headers_carry_schema_attempt_and_traceparent():
    env = envelope()
    tracer = TracerProvider().get_tracer("t")
    with tracer.start_as_current_span("s"):
        headers = message_headers(env, attempt=2)
    assert headers["message_id"] == env.event_id
    assert headers["type"] == "case.submitted"
    assert headers["correlation_id"] == "run_1"
    assert headers["x-attempt"] == 2
    assert headers["x-schema"] == "fcrm.events/case.submitted@1"
    assert headers["traceparent"].startswith("00-")


async def test_publish_sends_completed_envelope():
    pub = RecordingPublisher()
    env = envelope()
    final = await publish(pub, "case.events", "case.submitted", env)
    [(exchange, key, body, headers)] = pub.sent
    assert (exchange, key) == ("case.events", "case.submitted")
    assert Envelope.model_validate_json(body) == final
    assert headers["message_id"] == final.event_id


async def ok_handler(_env: Envelope) -> Disposition:
    return Disposition.PROCESSED


async def failing_handler(_env: Envelope) -> Disposition:
    raise RuntimeError("handler bug")


async def run(
    handler,
    attempt: Optional[int] = None,
    body: Optional[bytes] = None,
    publisher: Optional[RecordingPublisher] = None,
):
    pub = publisher or RecordingPublisher()
    acks = Acks()
    headers = {} if attempt is None else {"x-attempt": attempt}
    result = await deliver_with_retry(
        handler,
        body if body is not None else encode_envelope(envelope()),
        headers,
        queue="caseflow.events",
        publisher=pub,
        ack=acks.ack,
        nack=acks.nack,
    )
    return result, pub, acks


async def test_deliver_with_retry_acks_on_success():
    result, pub, acks = await run(ok_handler)
    assert result is Disposition.PROCESSED
    assert acks.acked == 1
    assert pub.sent == []


async def test_deliver_with_retry_republishes_to_retry_queue_with_next_attempt():
    result, pub, acks = await run(failing_handler, attempt=0)
    assert result is Disposition.RETRIED
    [(exchange, key, _, headers)] = pub.sent
    assert (exchange, key) == ("retry", "caseflow.events")
    assert headers["x-attempt"] == 1
    assert headers["x-last-error"] == "RuntimeError"
    assert acks.acked == 1


async def test_deliver_with_retry_dead_letters_after_max_attempts():
    result, pub, acks = await run(failing_handler, attempt=3)
    assert result is Disposition.DEAD_LETTERED
    assert pub.sent[0][0:2] == ("dlx", "caseflow.events")
    assert acks.acked == 1


async def test_deliver_with_retry_sends_poison_straight_to_dlq():
    result, pub, acks = await run(ok_handler, attempt=0, body=b"{not json")
    assert result is Disposition.DEAD_LETTERED
    assert pub.sent[0][0] == "dlx"
    assert pub.sent[0][2] == b"{not json"
    assert acks.acked == 1


async def test_deliver_with_retry_requeues_when_republish_fails():
    result, _, acks = await run(
        failing_handler, attempt=0, publisher=RecordingPublisher(fail=True)
    )
    assert result is Disposition.REQUEUED
    assert acks.acked == 0
    assert acks.nacked == [True]


def test_attempt_of_parses_bytes_and_garbage():
    assert events.attempt_of({"x-attempt": b"2"}) == 2
    assert events.attempt_of({"x-attempt": "nope"}) == 0
    assert events.attempt_of(None) == 0


class FakeConsumed:
    """Just enough of a collection for the non-transactional dedupe path."""

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def insert_one(self, doc, session=None):
        if await self.find_one(
            {"message_id": doc["message_id"], "consumer": doc["consumer"]}
        ):
            raise DuplicateKeyError("dup")
        self.docs.append(doc)


class CountingConsumer(Consumer):
    def __init__(self, consumed, publisher, fail: bool = False) -> None:
        super().__init__("case-service.events", consumed, publisher)
        self.handled: list[str] = []
        self.fail = fail

    async def handle(self, envelope, session) -> None:
        if self.fail:
            raise ValueError("cannot apply")
        self.handled.append(envelope.event_id)


async def test_consumer_processes_once_and_acks_duplicates():
    consumed = FakeConsumed()
    consumer = CountingConsumer(consumed, RecordingPublisher())
    body = encode_envelope(envelope())
    acks = Acks()
    first = await consumer.process(body, {}, acks.ack, acks.nack)
    second = await consumer.process(body, {}, acks.ack, acks.nack)
    assert (first, second) == (Disposition.PROCESSED, Disposition.DUPLICATE)
    assert len(consumer.handled) == 1
    assert acks.acked == 2
    assert consumed.docs[0]["consumer"] == "case-service.events"


async def test_consumer_failure_leaves_no_marker_and_retries():
    consumed = FakeConsumed()
    pub = RecordingPublisher()
    consumer = CountingConsumer(consumed, pub, fail=True)
    acks = Acks()
    result = await consumer.process(
        encode_envelope(envelope()), {}, acks.ack, acks.nack
    )
    assert result is Disposition.RETRIED
    assert consumed.docs == []
    assert pub.sent[0][0] == "retry"

"""H4's consumer: one ``index.build`` in, ``index.built`` or ``index.failed`` out.

The builder is a fake that answers or raises what a real build would, so each
path of the consumer is pinned: what is stored, what is published and on which
exchange, which failures are answers and which are outages to retry.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, Optional

import pytest
from evoq_context.llm.client import EmbeddingInputTooLarge
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.events import Disposition, build_envelope
from fcrm_context_worker.builder import (
    BuildResult,
    NoVectorBackendError,
    UnknownIndexError,
)
from fcrm_context_worker.consumer import (
    CODE_INPUT_TOO_LARGE,
    CODE_NO_BACKEND,
    CODE_NO_RECORDS,
    CODE_RECORD_LIMIT,
    CODE_TIMEOUT,
    CODE_UNKNOWN_INDEX,
    ContextConsumer,
    failure_code,
)
from fcrm_context_worker.sources import RecordLimitError, RecordsNotFoundError
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import IndexBuild, IndexBuilt, IndexFailed
from pydantic import ValidationError

BUCKET = "vectors-test"
CASE = "FCRM-2026-0151"


class _Builder:
    """Answers ``build`` with a result or raises; records what it was asked."""

    def __init__(
        self,
        result: Optional[BuildResult] = None,
        error: Optional[BaseException] = None,
        delay_s: float = 0.0,
    ) -> None:
        self.result = result or BuildResult(vector_count=197, fixture=b'{"v":1}')
        self.error = error
        self.delay_s = delay_s
        self.calls: list[tuple[str, int]] = []

    async def build(self, index: str, version: int) -> BuildResult:
        self.calls.append((index, version))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.result


def _consumer(
    publisher: Any,
    objects: Any,
    builder: _Builder,
    *,
    consumed: Any = None,
    timeout_s: float = 5.0,
    max_attempts: int = 3,
) -> ContextConsumer:
    return ContextConsumer(
        consumed,
        publisher,
        builder=builder,  # type: ignore[arg-type]
        store=objects,
        bucket=BUCKET,
        timeout_s=timeout_s,
        max_attempts=max_attempts,
    )


def _job(
    index: str = "policy_clauses", version: int = 14, **overrides: Any
) -> Envelope:
    payload = IndexBuild(
        job_id=overrides.pop("job_id", "job_1"),
        index=index,  # type: ignore[arg-type]
        version=version,
        **overrides,
    )
    return build_envelope(
        "index.build",
        payload.model_dump(mode="json"),
        case_id=CASE,
        run_id="run_9",
        correlation_id="corr_9",
    )


def test_the_consumer_reads_the_context_queue(
    routed_publisher: Any, fake_objects: Any
) -> None:
    consumer = _consumer(routed_publisher, fake_objects, _Builder())
    assert consumer.queue == "context-worker.run"


# ── success ───────────────────────────────────────────────────────────────


async def test_a_build_stores_the_fixture_and_announces_it_on_case_events(
    routed_publisher: Any, fake_objects: Any
) -> None:
    builder = _Builder(BuildResult(vector_count=197, fixture=b'{"fixture":true}'))
    consumer = _consumer(routed_publisher, fake_objects, builder)

    await consumer.handle(_job(), None)

    assert builder.calls == [("policy_clauses", 14)]
    key = (BUCKET, "policy_clauses/14.json")
    assert fake_objects.objects == {key: b'{"fixture":true}'}
    assert fake_objects.content_types[key] == "application/json"
    [(exchange, routing_key, event)] = routed_publisher.of_type("index.built")
    assert (exchange, routing_key) == ("case.events", "index.built")
    built = IndexBuilt.model_validate(event.payload)
    assert built == IndexBuilt(
        job_id="job_1",
        index="policy_clauses",
        version=14,
        vector_count=197,
        fixture_key="policy_clauses/14.json",
    )
    assert routed_publisher.of_type("index.failed") == []


async def test_the_result_event_keeps_the_requests_case_run_and_correlation(
    routed_publisher: Any, fake_objects: Any
) -> None:
    await _consumer(routed_publisher, fake_objects, _Builder()).handle(_job(), None)

    [(_, _, event)] = routed_publisher.of_type("index.built")
    assert (event.case_id, event.run_id, event.correlation_id) == (
        CASE,
        "run_9",
        "corr_9",
    )


@pytest.mark.parametrize(
    ("index", "version"),
    [("policy_clauses", 14), ("precedent_cases", 66), ("controls", 9)],
)
async def test_every_index_gets_its_own_fixture_key(
    routed_publisher: Any, fake_objects: Any, index: str, version: int
) -> None:
    await _consumer(routed_publisher, fake_objects, _Builder()).handle(
        _job(index, version), None
    )
    assert list(fake_objects.objects) == [(BUCKET, f"{index}/{version}.json")]


async def test_a_backend_with_no_fixture_announces_none_and_stores_nothing(
    routed_publisher: Any, fake_objects: Any
) -> None:
    builder = _Builder(BuildResult(vector_count=10, fixture=None))

    await _consumer(routed_publisher, fake_objects, builder).handle(_job(), None)

    assert fake_objects.objects == {}
    [(_, _, event)] = routed_publisher.of_type("index.built")
    assert IndexBuilt.model_validate(event.payload).fixture_key is None


async def test_a_rerun_of_the_same_job_announces_the_same_event(
    routed_publisher: Any, fake_objects: Any
) -> None:
    consumer = _consumer(routed_publisher, fake_objects, _Builder())
    await consumer.handle(_job(), None)
    await consumer.handle(_job(), None)
    first, second = (m[2] for m in routed_publisher.of_type("index.built"))
    assert first.event_id == second.event_id
    await consumer.handle(_job(job_id="job_2"), None)
    third = routed_publisher.of_type("index.built")[2][2]
    assert third.event_id != first.event_id


# ── answers: failures that no retry would fix ─────────────────────────────

ANSWERS: list[tuple[BaseException, str]] = [
    (RecordsNotFoundError("nothing at v15"), CODE_NO_RECORDS),
    (RecordLimitError("too many"), CODE_RECORD_LIMIT),
    (EmbeddingInputTooLarge("a clause is too long"), CODE_INPUT_TOO_LARGE),
    (NoVectorBackendError("VECTOR_BACKEND=none"), CODE_NO_BACKEND),
    (UnknownIndexError("no index"), CODE_UNKNOWN_INDEX),
]


@pytest.mark.parametrize(("error", "code"), ANSWERS)
async def test_a_build_that_cannot_succeed_is_announced_as_index_failed(
    routed_publisher: Any, fake_objects: Any, error: BaseException, code: str
) -> None:
    consumer = _consumer(routed_publisher, fake_objects, _Builder(error=error))

    await consumer.handle(_job(), None)

    [(exchange, routing_key, event)] = routed_publisher.of_type("index.failed")
    assert (exchange, routing_key) == ("case.events", "index.failed")
    failed = IndexFailed.model_validate(event.payload)
    assert (failed.job_id, failed.index, failed.code) == (
        "job_1",
        "policy_clauses",
        code,
    )
    assert failed.detail
    assert routed_publisher.of_type("index.built") == []
    assert fake_objects.objects == {}


@pytest.mark.parametrize(("error", "code"), ANSWERS)
def test_every_answer_has_exactly_one_failure_code(error: Exception, code: str) -> None:
    assert failure_code(error) == code  # type: ignore[arg-type]


def test_an_outage_has_no_failure_code() -> None:
    with pytest.raises(TypeError):
        failure_code(UpstreamUnavailableError("down", dependency="policy"))


async def test_a_build_past_its_deadline_is_announced_as_a_timeout(
    routed_publisher: Any, fake_objects: Any
) -> None:
    consumer = _consumer(
        routed_publisher, fake_objects, _Builder(delay_s=5.0), timeout_s=0.05
    )

    await consumer.handle(_job(), None)

    [(_, _, event)] = routed_publisher.of_type("index.failed")
    assert IndexFailed.model_validate(event.payload).code == CODE_TIMEOUT
    assert fake_objects.objects == {}


async def test_the_failure_detail_is_bounded(
    routed_publisher: Any, fake_objects: Any
) -> None:
    consumer = _consumer(
        routed_publisher,
        fake_objects,
        _Builder(error=RecordsNotFoundError("x" * 5000)),
    )
    await consumer.handle(_job(), None)
    [(_, _, event)] = routed_publisher.of_type("index.failed")
    assert len(IndexFailed.model_validate(event.payload).detail) == 300


# ── outages: raised so the message is retried, never lost ─────────────────


async def test_a_peer_outage_is_raised_not_announced(
    routed_publisher: Any, fake_objects: Any
) -> None:
    consumer = _consumer(
        routed_publisher,
        fake_objects,
        _Builder(error=UpstreamUnavailableError("down", dependency="policy")),
    )
    with pytest.raises(UpstreamUnavailableError):
        await consumer.handle(_job(), None)
    assert routed_publisher.messages == []


async def test_an_object_store_outage_is_raised_not_announced(
    routed_publisher: Any, fake_objects: Any
) -> None:
    fake_objects.unavailable = UpstreamUnavailableError("down", dependency="s3")
    consumer = _consumer(routed_publisher, fake_objects, _Builder())
    with pytest.raises(UpstreamUnavailableError):
        await consumer.handle(_job(), None)
    assert routed_publisher.messages == []


async def test_an_embedding_provider_failure_is_raised_for_a_retry(
    routed_publisher: Any, fake_objects: Any
) -> None:
    consumer = _consumer(
        routed_publisher, fake_objects, _Builder(error=ConnectionError("throttled"))
    )
    with pytest.raises(ConnectionError):
        await consumer.handle(_job(), None)
    assert routed_publisher.messages == []


# ── what the consumer refuses to process ──────────────────────────────────


async def test_a_message_that_is_not_an_index_build_is_refused(
    routed_publisher: Any, fake_objects: Any
) -> None:
    consumer = _consumer(routed_publisher, fake_objects, _Builder())
    with pytest.raises(ValueError, match="cannot process"):
        await consumer.handle(build_envelope("ingest.done", {}), None)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"job_id": "j", "index": "nope", "version": 1},
        {"job_id": "j", "index": "controls"},
    ],
)
async def test_a_malformed_job_is_refused(
    routed_publisher: Any, fake_objects: Any, payload: dict[str, Any]
) -> None:
    consumer = _consumer(routed_publisher, fake_objects, _Builder())
    with pytest.raises(ValidationError):
        await consumer.handle(build_envelope("index.build", payload), None)


# ── through ``process``: retry, dead-letter and dedupe ────────────────────


class _Acks:
    def __init__(self) -> None:
        self.acked = 0
        self.nacked: list[bool] = []

    async def ack(self) -> None:
        self.acked += 1

    async def nack(self, requeue: bool) -> None:
        self.nacked.append(requeue)


def _body(envelope: Envelope) -> bytes:
    return envelope.model_dump_json().encode("utf-8")


async def test_a_successful_delivery_is_acked_and_a_redelivery_is_a_duplicate(
    routed_publisher: Any, fake_objects: Any, fake_consumed: Callable[[], Any]
) -> None:
    builder = _Builder()
    consumed = fake_consumed()
    consumer = _consumer(routed_publisher, fake_objects, builder, consumed=consumed)
    message = _body(_job())
    acks = _Acks()

    first = await consumer.process(message, {}, acks.ack, acks.nack)
    second = await consumer.process(message, {}, acks.ack, acks.nack)

    assert (first, second) == (Disposition.PROCESSED, Disposition.DUPLICATE)
    assert acks.acked == 2
    assert len(builder.calls) == 1
    assert len(routed_publisher.of_type("index.built")) == 1


async def test_an_outage_is_retried_through_the_retry_exchange_then_parked(
    routed_publisher: Any, fake_objects: Any, fake_consumed: Callable[[], Any]
) -> None:
    builder = _Builder(error=UpstreamUnavailableError("down", dependency="policy"))
    consumer = _consumer(
        routed_publisher,
        fake_objects,
        builder,
        consumed=fake_consumed(),
        max_attempts=2,
    )
    message = _body(_job())
    acks = _Acks()

    first = await consumer.process(message, {"x-attempt": 0}, acks.ack, acks.nack)
    last = await consumer.process(message, {"x-attempt": 2}, acks.ack, acks.nack)

    assert (first, last) == (Disposition.RETRIED, Disposition.DEAD_LETTERED)
    assert [(e, k) for e, k, _ in routed_publisher.messages] == [
        ("retry", "context-worker.run"),
        ("dlx", "context-worker.run"),
    ]
    assert routed_publisher.raw[0][2]["x-attempt"] == 1
    assert acks.acked == 2  # each original is acked once the copy is safe
    assert acks.nacked == []


async def test_a_poison_message_goes_straight_to_the_dead_letter_queue(
    routed_publisher: Any, fake_objects: Any, fake_consumed: Callable[[], Any]
) -> None:
    consumer = _consumer(
        routed_publisher, fake_objects, _Builder(), consumed=fake_consumed()
    )
    acks = _Acks()

    outcome = await consumer.process(b"{not an envelope", {}, acks.ack, acks.nack)

    assert outcome is Disposition.DEAD_LETTERED
    assert routed_publisher.raw[0][0] == "dlx"


def test_the_failure_payload_serialises_as_json_for_the_bus() -> None:
    failed = IndexFailed(job_id="j", index="controls", code=CODE_NO_RECORDS, detail="d")
    assert json.loads(failed.model_dump_json())["code"] == "no_records"

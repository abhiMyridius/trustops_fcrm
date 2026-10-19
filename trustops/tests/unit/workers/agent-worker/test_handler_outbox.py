"""ADR-79 in :class:`~fcrm_agent_worker.handler.AgentJobConsumer`: the job is
claimed in ``agent_jobs`` and run with no transaction open, and its one
terminal event is committed to H2's outbox together with ``mark_done`` (never
published directly). Redeliveries of a finished job send nothing; a delivery
of a job still running elsewhere takes the broker's retry path.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from fcrm_agent_worker.handler import AgentJobConsumer
from fcrm_agent_worker.jobs import JobInFlightError, lease_seconds
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_agent_worker.topology import (
    AGENT_JOBS_EXCHANGE,
    ROUTING_KEY_AGENT_DONE,
    ROUTING_KEY_AGENT_FAILED,
)
from fcrm_common.events import (
    RETRY_EXCHANGE,
    Disposition,
    build_envelope,
    encode_envelope,
)
from fcrm_contracts.enums import AgentName, Tier
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import JobPayload
from fcrm_guardrails.opa_client import OPAClient
from fcrm_tools import ToolRegistry

JOB_ID = "job-research-1"


def _consumer(
    *,
    registry: ToolRegistry,
    permit_client: OPAClient,
    audit: Any,
    publisher: Any,
    tiers_config: dict[str, Any],
    model_factory: ModelFactory,
    consumed: Any,
    deps: Any,
) -> AgentJobConsumer:
    return AgentJobConsumer(
        consumed,
        publisher,
        **deps.kwargs,
        registry=registry,
        permit_client=permit_client,
        audit=audit,
        tiers_config=tiers_config,
        model_factory=model_factory,
    )


def _counting(factory: ModelFactory, calls: list[int]) -> ModelFactory:
    """``factory``, counting how many models it built (one per attempt)."""

    def wrapped(models: Any, plan: Any) -> Any:
        calls.append(1)
        return factory(models, plan)

    return wrapped


def _redelivery(job: JobPayload) -> Envelope:
    """The same job in a new message (H1's outbox publishing it again)."""
    return build_envelope(
        "agent.run", job.model_dump(mode="json"), case_id=job.case_id, run_id=job.run_id
    )


@pytest.fixture
def make_consumer(
    seed_registry: ToolRegistry,
    allow_all_opa_client: OPAClient,
    fake_tiers_config: dict[str, Any],
    recording_publisher: Any,
    recording_audit: Any,
    fake_consumed: Any,
    consumer_deps: Any,
) -> Callable[..., AgentJobConsumer]:
    def make(model_factory: ModelFactory, **overrides: Any) -> AgentJobConsumer:
        values: dict[str, Any] = {
            "registry": seed_registry,
            "permit_client": allow_all_opa_client,
            "audit": recording_audit,
            "publisher": recording_publisher,
            "tiers_config": fake_tiers_config,
            "model_factory": model_factory,
            "consumed": fake_consumed,
            "deps": consumer_deps,
            **overrides,
        }
        return _consumer(**values)

    return make


async def test_a_finished_job_leaves_one_outbox_row_and_publishes_nothing(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    recording_publisher: Any,
    consumer_deps: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    consumer = make_consumer(make_model_factory_returning(research_outputs))
    await consumer.handle(demo_job_envelope, None)

    assert recording_publisher.sent == []
    [row] = consumer_deps.outbox_collection.docs
    assert (row["exchange"], row["routing_key"], row["type"]) == (
        AGENT_JOBS_EXCHANGE,
        ROUTING_KEY_AGENT_DONE,
        "agent.done",
    )
    assert row["published_at"] is None
    # The row and mark_done shared one open transaction.
    [session] = consumer_deps.outbox_collection.sessions
    assert session.in_transaction
    assert consumer_deps.jobs.done_sessions == [session]
    job = consumer_deps.jobs.docs[JOB_ID]
    assert (job["status"], job["outcome"], job["event_id"]) == (
        "done",
        "done",
        row["event_id"],
    )


async def test_the_job_is_claimed_for_its_tiers_worst_case(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    consumer_deps: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    consumer = make_consumer(make_model_factory_returning(research_outputs))
    await consumer.handle(demo_job_envelope, None)
    [(job_id, ttl_s, _outcome)] = consumer_deps.jobs.claims
    assert job_id == JOB_ID
    assert ttl_s == lease_seconds(Tier.STRONG, 4)


async def test_a_redelivered_finished_job_sends_nothing_and_writes_no_audit(
    demo_job_envelope: Envelope,
    demo_job_payload: JobPayload,
    research_outputs: list[Any],
    recording_audit: Any,
    consumer_deps: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    calls: list[int] = []
    consumer = make_consumer(
        _counting(make_model_factory_returning(research_outputs), calls)
    )
    await consumer.handle(demo_job_envelope, None)
    rows, records, models = (
        len(consumer_deps.outbox_collection.docs),
        len(recording_audit.records),
        len(calls),
    )

    await consumer.handle(_redelivery(demo_job_payload), None)

    assert len(consumer_deps.outbox_collection.docs) == rows == 1
    assert len(recording_audit.records) == records
    assert len(calls) == models, "the model is not called again"


async def test_a_job_running_elsewhere_raises_and_goes_to_the_retry_exchange(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    recording_publisher: Any,
    recording_audit: Any,
    fake_consumed: Any,
    consumer_deps: Any,
    acks: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    calls: list[int] = []
    consumer = make_consumer(
        _counting(make_model_factory_returning(research_outputs), calls)
    )
    other = type(consumer_deps.jobs)(holder="h2_other")
    other.docs = consumer_deps.jobs.docs
    other.now = consumer_deps.jobs.now
    await other.claim(JOB_ID, 600)

    with pytest.raises(JobInFlightError):
        await consumer.handle(demo_job_envelope, None)

    disposition = await consumer.process(
        encode_envelope(demo_job_envelope), {}, acks.ack, acks.nack
    )
    assert disposition is Disposition.RETRIED
    [(exchange, routing_key, _body, headers)] = recording_publisher.sent
    assert (exchange, routing_key) == (RETRY_EXCHANGE, "agent-worker.run")
    assert headers["x-attempt"] == 1
    assert acks.acked == 1
    # Nothing ran, nothing was announced, nothing marked consumed, and the
    # other worker's claim is untouched.
    assert calls == []
    assert consumer_deps.outbox_collection.docs == []
    assert recording_audit.records == []
    assert fake_consumed.docs == []
    assert consumer_deps.jobs.docs[JOB_ID]["holder"] == "h2_other"


async def test_an_expired_claim_is_taken_over_and_run(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    consumer_deps: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    other = type(consumer_deps.jobs)(holder="h2_crashed")
    other.docs = consumer_deps.jobs.docs
    other.now = consumer_deps.jobs.now
    await other.claim(JOB_ID, 600)
    consumer_deps.jobs.advance(601)

    consumer = make_consumer(make_model_factory_returning(research_outputs))
    await consumer.handle(demo_job_envelope, None)

    assert [k for k, _ in consumer_deps.events] == [ROUTING_KEY_AGENT_DONE]
    assert consumer_deps.jobs.docs[JOB_ID]["holder"] == consumer_deps.jobs.holder


async def test_a_retried_transaction_still_enqueues_once(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    recording_audit: Any,
    consumer_deps: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    """``with_transaction`` re-runs the callback after a transient abort: the
    first try's writes roll back and the second commits, so the job has one
    outbox row and one set of audit records."""
    runs: list[bool] = []

    async def abort_once_then_commit(fn: Any) -> Any:
        jobs, outbox = consumer_deps.jobs, consumer_deps.outbox_collection
        saved = (copy.deepcopy(jobs.docs), list(outbox.docs))
        runs.append(await fn(consumer_deps.new_session()))
        jobs.docs.clear()
        jobs.docs.update(saved[0])  # the transient abort rolls back
        outbox.docs[:] = saved[1]
        result = await fn(consumer_deps.new_session())
        runs.append(result)
        return result

    consumer_deps.transact = abort_once_then_commit
    consumer = make_consumer(make_model_factory_returning(research_outputs))
    await consumer.handle(demo_job_envelope, None)

    assert runs == [True, True]
    assert len(consumer_deps.outbox_collection.docs) == 1
    assert [r.type for r in recording_audit.records] == [
        "guard.permit",
        "guard.output",
    ]


async def test_a_callback_run_again_after_its_commit_enqueues_nothing_more(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    consumer_deps: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    """Without a rollback between them, the second run finds the job done."""
    runs: list[bool] = []

    async def twice(fn: Any) -> Any:
        runs.append(await fn(consumer_deps.new_session()))
        runs.append(await fn(consumer_deps.new_session()))
        return runs[0]

    consumer_deps.transact = twice
    consumer = make_consumer(make_model_factory_returning(research_outputs))
    await consumer.handle(demo_job_envelope, None)

    assert runs == [True, False]
    assert len(consumer_deps.outbox_collection.docs) == 1


async def test_a_job_taken_over_mid_run_is_not_announced_or_audited(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    recording_audit: Any,
    consumer_deps: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    """The lease ran out during the model call and another worker claimed
    the job: ``mark_done`` refuses, so this run enqueues nothing."""
    inner = make_model_factory_returning(research_outputs)

    def lease_lost(models: Any, plan: Any) -> Any:
        consumer_deps.jobs.docs[JOB_ID]["claim"] = "claim-of-another-worker"
        return inner(models, plan)

    consumer = make_consumer(lease_lost)
    await consumer.handle(demo_job_envelope, None)

    assert consumer_deps.outbox_collection.docs == []
    assert recording_audit.records == []
    assert consumer_deps.jobs.docs[JOB_ID]["status"] == "running"


async def test_the_consumed_marker_is_written_after_the_handler_not_before(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    fake_consumed: Any,
    consumer_deps: Any,
    acks: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    inner = make_model_factory_returning(research_outputs)
    markers_during_run: list[int] = []

    def watching(models: Any, plan: Any) -> Any:
        markers_during_run.append(len(fake_consumed.docs))
        return inner(models, plan)

    consumer = make_consumer(watching)
    assert consumer._client is None  # noqa: SLF001

    disposition = await consumer.process(
        encode_envelope(demo_job_envelope), {}, acks.ack, acks.nack
    )

    assert disposition is Disposition.PROCESSED
    assert markers_during_run == [0]
    [marker] = fake_consumed.docs
    assert marker["message_id"] == demo_job_envelope.event_id
    assert len(consumer_deps.outbox_collection.docs) == 1
    # The same message again is a duplicate before the job ledger is asked.
    claims = len(consumer_deps.jobs.claims)
    assert (
        await consumer.process(
            encode_envelope(demo_job_envelope), {}, acks.ack, acks.nack
        )
        is Disposition.DUPLICATE
    )
    assert len(consumer_deps.jobs.claims) == claims


async def test_a_context_pack_failure_is_committed_through_the_same_transaction(
    seed_registry: ToolRegistry,
    recording_audit: Any,
    consumer_deps: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    from fcrm_seed.loader import load_raw
    from fcrm_tools import seed_pins

    payload = JobPayload(
        job_id="job-intake-1",
        case_id="FCRM-2026-0151",
        run_id="run-intake-1",
        agent=AgentName.INTAKE,
        tier=Tier.CHEAP,
        step_key="intake",
        attempt=1,
        pins=seed_pins(load_raw()),
    )
    consumer = make_consumer(make_model_factory_returning([]))
    await consumer.handle(_redelivery(payload), None)

    [(routing_key, envelope)] = consumer_deps.events
    assert routing_key == ROUTING_KEY_AGENT_FAILED
    assert envelope.payload["reason"].startswith("context_pack_error:")
    job = consumer_deps.jobs.docs["job-intake-1"]
    assert (job["status"], job["outcome"]) == ("done", "context_pack_error")
    assert [r.type for r in recording_audit.records] == ["agent.failed"]


async def test_a_job_that_raises_gives_its_claim_up_for_the_retry(
    demo_job_envelope: Envelope,
    consumer_deps: Any,
    make_consumer: Callable[..., AgentJobConsumer],
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    consumer = make_consumer(make_model_factory_returning([]), tiers_config={})
    with pytest.raises(Exception, match="tier"):
        await consumer.handle(demo_job_envelope, None)
    assert consumer_deps.jobs.released == [JOB_ID]
    assert JOB_ID not in consumer_deps.jobs.docs
    assert consumer_deps.outbox_collection.docs == []

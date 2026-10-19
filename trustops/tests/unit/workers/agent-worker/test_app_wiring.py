"""End-to-end FastStream/RabbitMQ plumbing: :func:`fcrm_agent_worker.app
.wire_subscriber` against ``faststream.rabbit.TestRabbitBroker`` (no real
broker). Proves the consumer/publisher/retry/DLQ wiring, not the guard
logic (:mod:`test_live_runtime` already does that against real guards).

Topology exercised (``ops/rabbitmq/definitions.json``, read verbatim --
see :mod:`fcrm_agent_worker.topology`):

- consume: exchange ``agent.jobs`` (direct), routing key ``agent.run`` ->
  queue ``agent-worker.run``.
- publish, happy path: ``agent.jobs`` / ``agent.done`` or ``agent.failed``
  -> queue ``caseflow.agent-results`` (the two bindings that queue has), by
  H2's outbox relay from the row the handler committed (ADR-79).
- publish, broker-level failure: ``retry`` / ``agent-worker.run`` (first 3
  attempts) -> ``dlx`` / ``agent-worker.run`` (the DLQ) after that.
"""

from __future__ import annotations

from typing import Any

from faststream.rabbit import RabbitBroker, RabbitMessage, TestRabbitBroker
from fcrm_agent_worker.app import wire_subscriber
from fcrm_agent_worker.handler import AgentJobConsumer
from fcrm_agent_worker.publisher import FastStreamPublisher
from fcrm_agent_worker.topology import passive_exchange, passive_queue
from fcrm_common.outbox import OutboxRelay
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import AgentDone, AgentFailed, JobPayload


class _HeldLease:
    """A relay lease this process always holds."""

    held = True

    async def acquire(self) -> bool:
        return True

    async def release(self) -> None:
        return None


async def _relay_outbox(deps: Any, publisher: FastStreamPublisher) -> int:
    """Publish what the handler committed to H2's outbox, as its relay does."""
    relay = OutboxRelay(deps.outbox_collection, _HeldLease(), publisher)
    published, failed = await relay.publish_batch()
    assert not failed
    return published


def _capture_queue(name: str, routing_key: str) -> Any:
    return passive_queue(name, routing_key=routing_key)


async def test_agent_run_to_agent_done_through_the_real_broker(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    seed_registry: Any,
    allow_all_opa_client: Any,
    fake_tiers_config: dict[str, Any],
    recording_audit: Any,
    fake_consumed: Any,
    consumer_deps: Any,
    make_model_factory_returning: Any,
) -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    publisher = FastStreamPublisher(broker)
    consumer = AgentJobConsumer(
        fake_consumed,
        publisher,
        **consumer_deps.kwargs,
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=recording_audit,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
    )
    wire_subscriber(broker, consumer)

    done_received: list[RabbitMessage] = []
    failed_received: list[RabbitMessage] = []
    agent_jobs = passive_exchange("agent.jobs")

    @broker.subscriber(
        _capture_queue("caseflow.agent-results", "agent.done"), agent_jobs
    )
    async def _capture_done(msg: RabbitMessage) -> None:
        done_received.append(msg)

    @broker.subscriber(
        _capture_queue("caseflow.agent-results", "agent.failed"), agent_jobs
    )
    async def _capture_failed(msg: RabbitMessage) -> None:
        failed_received.append(msg)

    async with TestRabbitBroker(broker) as br:
        await br.publish(
            demo_job_envelope.model_dump_json().encode(),
            exchange=agent_jobs,
            routing_key="agent.run",
        )
        assert await _relay_outbox(consumer_deps, publisher) == 1

    assert failed_received == []
    assert len(done_received) == 1
    done = AgentDone.model_validate(
        Envelope.model_validate_json(done_received[0].body).payload
    )
    assert done.job_id == "job-research-1"
    assert [r.type for r in recording_audit.records] == [
        "guard.permit",
        "guard.output",
    ]


async def test_permit_denied_publishes_agent_failed_through_the_real_broker(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    seed_registry: Any,
    deny_all_opa_client: Any,
    fake_tiers_config: dict[str, Any],
    recording_audit: Any,
    fake_consumed: Any,
    consumer_deps: Any,
    make_model_factory_returning: Any,
) -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    publisher = FastStreamPublisher(broker)
    consumer = AgentJobConsumer(
        fake_consumed,
        publisher,
        **consumer_deps.kwargs,
        registry=seed_registry,
        permit_client=deny_all_opa_client,
        audit=recording_audit,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
    )
    wire_subscriber(broker, consumer)

    failed_received: list[RabbitMessage] = []
    agent_jobs = passive_exchange("agent.jobs")

    @broker.subscriber(
        _capture_queue("caseflow.agent-results", "agent.failed"), agent_jobs
    )
    async def _capture_failed(msg: RabbitMessage) -> None:
        failed_received.append(msg)

    async with TestRabbitBroker(broker) as br:
        await br.publish(
            demo_job_envelope.model_dump_json().encode(),
            exchange=agent_jobs,
            routing_key="agent.run",
        )
        assert await _relay_outbox(consumer_deps, publisher) == 1

    assert len(failed_received) == 1
    failed = AgentFailed.model_validate(
        Envelope.model_validate_json(failed_received[0].body).payload
    )
    assert failed.reason.startswith("permit_denied:")


async def test_an_unexpected_handler_failure_is_dead_lettered(
    demo_job_envelope: Envelope,
    seed_registry: Any,
    allow_all_opa_client: Any,
    recording_audit: Any,
    fake_consumed: Any,
    consumer_deps: Any,
    make_model_factory_returning: Any,
) -> None:
    """A bug-class failure (here: a tiers.yaml that does not configure the
    agent's tier at all, raising ``ModelConfigError`` deep inside
    ``run_agent_live`` -- not one of the deterministic, expected
    context-pack errors :meth:`AgentJobConsumer.handle` catches itself)
    must never be swallowed as a false ``agent.done``: the base
    ``Consumer``'s retry/DLQ machinery (``fcrm_common.events
    .deliver_with_retry``) takes over, and with ``max_attempts=0`` here, a
    first failure goes straight to the DLQ (``dlx`` / ``agent-worker.run``
    -- the real ``agent-worker.run.dlq`` queue's binding).
    """
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    publisher = FastStreamPublisher(broker)
    consumer = AgentJobConsumer(
        fake_consumed,
        publisher,
        **consumer_deps.kwargs,
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=recording_audit,
        tiers_config={},  # no tier configured at all -> ModelConfigError
        model_factory=make_model_factory_returning([]),
        max_attempts=0,
    )
    wire_subscriber(broker, consumer)

    dlq_received: list[RabbitMessage] = []
    retry_received: list[RabbitMessage] = []

    @broker.subscriber(
        passive_queue("agent-worker.run.dlq", routing_key="agent-worker.run"),
        passive_exchange("dlx"),
    )
    async def _capture_dlq(msg: RabbitMessage) -> None:
        dlq_received.append(msg)

    @broker.subscriber(
        passive_queue("agent-worker.run.retry", routing_key="agent-worker.run"),
        passive_exchange("retry"),
    )
    async def _capture_retry(msg: RabbitMessage) -> None:
        retry_received.append(msg)

    async with TestRabbitBroker(broker) as br:
        await br.publish(
            demo_job_envelope.model_dump_json().encode(),
            exchange=passive_exchange("agent.jobs"),
            routing_key="agent.run",
        )

    assert retry_received == []
    assert len(dlq_received) == 1
    assert dlq_received[0].headers["x-last-error"] == "ModelConfigError"
    dead_job = JobPayload.model_validate(
        Envelope.model_validate_json(dlq_received[0].body).payload
    )
    assert dead_job.job_id == "job-research-1"
    # Never a false "it finished" -- no audit write, no agent.done/failed,
    # and the claim is given up so a redelivery can run the job.
    assert recording_audit.records == []
    assert consumer_deps.outbox_collection.docs == []
    assert consumer_deps.jobs.released == ["job-research-1"]


async def test_a_transient_handler_failure_is_retried_not_dead_lettered(
    demo_job_envelope: Envelope,
    seed_registry: Any,
    allow_all_opa_client: Any,
    recording_audit: Any,
    fake_consumed: Any,
    consumer_deps: Any,
    make_model_factory_returning: Any,
) -> None:
    """The same failure with the default ``max_attempts=3`` goes to the
    ``retry`` exchange first (design A §4.5: republished with
    ``x-attempt + 1``, not dropped, not yet the DLQ).
    """
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    publisher = FastStreamPublisher(broker)
    consumer = AgentJobConsumer(
        fake_consumed,
        publisher,
        **consumer_deps.kwargs,
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=recording_audit,
        tiers_config={},
        model_factory=make_model_factory_returning([]),
    )
    wire_subscriber(broker, consumer)

    retry_received: list[RabbitMessage] = []
    dlq_received: list[RabbitMessage] = []

    @broker.subscriber(
        passive_queue("agent-worker.run.retry", routing_key="agent-worker.run"),
        passive_exchange("retry"),
    )
    async def _capture_retry(msg: RabbitMessage) -> None:
        retry_received.append(msg)

    @broker.subscriber(
        passive_queue("agent-worker.run.dlq", routing_key="agent-worker.run"),
        passive_exchange("dlx"),
    )
    async def _capture_dlq(msg: RabbitMessage) -> None:
        dlq_received.append(msg)

    async with TestRabbitBroker(broker) as br:
        await br.publish(
            demo_job_envelope.model_dump_json().encode(),
            exchange=passive_exchange("agent.jobs"),
            routing_key="agent.run",
        )

    assert dlq_received == []
    assert len(retry_received) == 1
    assert retry_received[0].headers["x-attempt"] == 1

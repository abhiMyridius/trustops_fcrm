"""``fcrm_agent_worker.publisher.FastStreamPublisher``: the one place H2
talks to the broker for publishing, against FastStream's own in-memory
``TestRabbitBroker`` (no real RabbitMQ)."""

from __future__ import annotations

from faststream import AckPolicy
from faststream.rabbit import RabbitBroker, RabbitMessage, TestRabbitBroker
from fcrm_agent_worker.publisher import FastStreamPublisher
from fcrm_agent_worker.topology import passive_exchange, passive_queue
from fcrm_common.events import message_headers
from fcrm_contracts.events.envelope import Envelope


def _envelope() -> Envelope:
    from fcrm_common.events import build_envelope

    return build_envelope(
        "agent.done",
        {"job_id": "job-1", "case_id": "FCRM-2026-0151", "run_id": "run-1"},
        case_id="FCRM-2026-0151",
        run_id="run-1",
    )


async def test_publish_lifts_the_right_headers_onto_amqp_properties() -> None:
    """``message_id``/``correlation_id``/``content_type``/``type`` become
    AMQP properties (module docstring in ``fcrm_common.events``); every
    other header stays in the headers table.
    """
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    received: list[RabbitMessage] = []

    queue = passive_queue("caseflow.agent-results", routing_key="agent.done")
    exchange = passive_exchange("agent.jobs")

    @broker.subscriber(queue, exchange, ack_policy=AckPolicy.MANUAL)
    async def _consume(msg: RabbitMessage) -> None:
        received.append(msg)
        await msg.ack()

    publisher = FastStreamPublisher(broker)
    env = _envelope()
    headers = message_headers(env, attempt=0)

    async with TestRabbitBroker(broker):
        await publisher.publish(
            "agent.jobs", "agent.done", env.model_dump_json().encode(), headers
        )

    assert len(received) == 1
    msg = received[0]
    assert msg.message_id == env.event_id
    assert msg.correlation_id == env.correlation_id
    assert msg.content_type == "application/json"
    # "type" is lifted to the AMQP message-type property (aio_pika's
    # ``type_``), not left in the headers table.
    assert "type" not in msg.headers
    assert msg.headers["x-schema"] == f"fcrm.events/{env.type}@{env.version}"
    assert Envelope.model_validate_json(msg.body).event_id == env.event_id


async def test_publish_reuses_the_cached_exchange_object() -> None:
    """Repeat publishes to the same exchange build it once (a cache, not a
    correctness requirement, but worth pinning)."""
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    publisher = FastStreamPublisher(broker)
    first = publisher._exchange("agent.jobs")  # noqa: SLF001 - whitebox check
    second = publisher._exchange("agent.jobs")  # noqa: SLF001
    assert first is second
    assert first.type.value == "direct"

"""fcrm_common.broker: passive topology helpers and consumer wiring."""

from __future__ import annotations

from typing import Any

import pytest
from faststream import AckPolicy
from faststream.rabbit import ExchangeType, RabbitBroker, TestRabbitBroker
from fcrm_common.broker import (
    EXCHANGE_TYPES,
    passive_exchange,
    passive_queue,
    wire_consumer,
    wire_consumer_running,
)

AMQP = "amqp://guest:guest@localhost:5672/"


class _RecordingConsumer:
    """The two things ``wire_consumer`` needs from a ``Consumer``."""

    queue = "q.test"

    def __init__(self) -> None:
        self.deliveries: list[tuple[bytes, dict[str, Any]]] = []

    async def process(self, body: bytes, headers: dict[str, Any], **_: Any) -> None:
        self.deliveries.append((body, headers))


def test_passive_queue_never_declares_and_is_durable():
    queue = passive_queue("agent-worker.run", routing_key="agent.run")
    assert queue.name == "agent-worker.run"
    assert queue.declare is False
    assert queue.durable is True
    assert queue.routing_key == "agent.run"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("case.events", ExchangeType.TOPIC), ("agent.jobs", ExchangeType.DIRECT)],
)
def test_passive_exchange_uses_the_declared_type_and_never_declares(
    name: str, expected: ExchangeType
):
    exchange = passive_exchange(name)
    assert exchange.type == expected
    assert exchange.declare is False
    assert EXCHANGE_TYPES[name] == expected


def test_an_unknown_exchange_defaults_to_direct():
    assert passive_exchange("nope").type == ExchangeType.DIRECT


def test_wire_consumer_registers_one_manual_ack_subscriber_per_routing_key():
    broker = RabbitBroker(AMQP)
    wire_consumer(
        broker,
        _RecordingConsumer(),  # type: ignore[arg-type]
        exchange="agent.jobs",
        routing_keys=["agent.done", "agent.failed"],
        prefetch=4,
    )
    subscribers = broker.subscribers
    assert len(subscribers) == 2
    assert {s.queue.routing_key for s in subscribers} == {"agent.done", "agent.failed"}
    assert all(s.queue.name == "q.test" for s in subscribers)
    assert all(s.ack_policy == AckPolicy.MANUAL for s in subscribers)
    assert all(s.channel.prefetch_count == 4 for s in subscribers)


def test_wire_consumer_leaves_the_broker_default_when_prefetch_is_unset():
    broker = RabbitBroker(AMQP)
    wire_consumer(
        broker,
        _RecordingConsumer(),  # type: ignore[arg-type]
        exchange="agent.jobs",
        routing_keys=["agent.run"],
    )
    assert broker.subscribers[0].channel is None


async def test_a_delivery_reaches_consumer_process_with_its_body_and_headers():
    broker = RabbitBroker(AMQP)
    consumer = _RecordingConsumer()
    wire_consumer(
        broker,
        consumer,  # type: ignore[arg-type]
        exchange="agent.jobs",
        routing_keys=["agent.run"],
    )
    async with TestRabbitBroker(broker) as test_broker:
        await test_broker.publish(
            b'{"hello": "world"}',
            exchange=passive_exchange("agent.jobs"),
            routing_key="agent.run",
            headers={"attempt": 1},
        )
    assert len(consumer.deliveries) == 1
    body, headers = consumer.deliveries[0]
    assert body == b'{"hello": "world"}'
    assert headers["attempt"] == 1


class _Subscriber:
    """A FastStream subscriber as far as ``wire_consumer_running`` sees one."""

    def __init__(self, queue: object = None) -> None:
        self.queue = queue
        self.starts = 0

    async def start(self) -> None:
        self.starts += 1


class _StartedBroker:
    """A broker that has already started, with one subscriber it started."""

    def __init__(self) -> None:
        self.earlier = _Subscriber()
        self.earlier.starts = 1
        self.subscribers: list[_Subscriber] = [self.earlier]

    def subscriber(self, queue: object, *args: object, **kwargs: object) -> object:
        self.subscribers.append(_Subscriber(queue))
        return lambda handler: handler


async def test_wire_consumer_running_starts_only_the_subscribers_it_added():
    broker = _StartedBroker()

    added = await wire_consumer_running(
        broker,  # type: ignore[arg-type]
        _RecordingConsumer(),  # type: ignore[arg-type]
        exchange="case.events",
        routing_keys=["index.*"],
    )

    assert len(added) == 1
    assert added[0].starts == 1
    assert added[0].queue.name == "q.test"
    assert broker.earlier.starts == 1  # never restarted
    assert broker.subscribers == [broker.earlier, added[0]]


async def test_wire_consumer_running_starts_one_subscriber_per_routing_key():
    broker = _StartedBroker()

    added = await wire_consumer_running(
        broker,  # type: ignore[arg-type]
        _RecordingConsumer(),  # type: ignore[arg-type]
        exchange="case.events",
        routing_keys=["index.built", "index.failed"],
    )

    assert [s.starts for s in added] == [1, 1]
    assert {s.queue.routing_key for s in added} == {"index.built", "index.failed"}

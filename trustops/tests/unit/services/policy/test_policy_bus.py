"""S3's two bus connections: the outbox relay and the ``index.*`` consumer are
each started only when the host gave S3 what they need, and stopped with it."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from evoq_context.llm.client import LLMClient
from evoq_context.vector.factory import create_vector_store
from fcrm_common.health import ReadinessProbe
from fcrm_common.outbox import Outbox
from fcrm_context_worker.config import FCRM_PROJECT, context_config_from_env
from fcrm_policy.bus import start_events_consumer, start_relay
from fcrm_policy.search.vector import VectorClauseSource
from fcrm_policy.settings import PolicySettings


class _Subscriber:
    def __init__(self, queue: Any) -> None:
        self.queue = queue
        self.running = False

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False


class _Broker:
    """A broker that has already started: it takes subscribers and counts them."""

    def __init__(self) -> None:
        self.subscribers: list[_Subscriber] = []

    def subscriber(self, queue: Any, *args: Any, **kwargs: Any) -> Any:
        self.subscribers.append(_Subscriber(queue))
        return lambda handler: handler


class _Consumed:
    """The ``consumed_messages`` collection, as far as index creation goes."""

    def __init__(self) -> None:
        self.index_names: list[str] = []

    async def create_indexes(self, indexes: list[Any]) -> list[str]:
        self.index_names = [i.document["name"] for i in indexes]
        return self.index_names


class _Status:
    """The ``IndexStatusStore`` calls S3's startup makes."""

    def __init__(self) -> None:
        self.ensured = False
        self.looked_for: list[str] = []

    async def ensure_indexes(self) -> None:
        self.ensured = True

    async def latest_built(self, index: str) -> None:
        self.looked_for.append(index)


class _Db(dict[str, Any]):
    def __missing__(self, name: str) -> Any:
        collection = _Consumed()
        self[name] = collection
        return collection


@pytest.fixture
def parts(tmp_path: Path) -> SimpleNamespace:
    config = context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": "64"},
        projects_root=tmp_path,
    )
    store = create_vector_store(config, FCRM_PROJECT)
    assert store is not None
    return SimpleNamespace(
        config=config,
        source=VectorClauseSource(store, LLMClient.from_config(config), config),
        status=_Status(),
        settings=PolicySettings(),
        db=_Db(),
    )


def _deps(**kwargs: Any) -> Any:
    return SimpleNamespace(publisher=None, broker=None, lease_holder="x", **kwargs)


async def test_without_a_publisher_the_relay_is_not_started_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    probe = ReadinessProbe()
    caplog.set_level(logging.WARNING)

    async with AsyncExitStack() as stack:
        start_relay(
            stack,
            probe,
            Outbox(SimpleNamespace(full_name="fcrm_policy.outbox")),  # type: ignore[arg-type]
            {},  # type: ignore[arg-type]
            _deps(),
        )

    assert "no publisher" in caplog.text
    _status, body = await probe.run()
    assert "outbox" not in body["checks"]  # no relay, so nothing to report on


@pytest.mark.parametrize(
    ("broker", "publisher"), [(None, None), (None, object()), (_Broker(), None)]
)
async def test_the_events_consumer_needs_both_a_broker_and_a_publisher(
    parts: SimpleNamespace, broker: Any, publisher: Any
) -> None:
    async with AsyncExitStack() as stack:
        consumer = await start_events_consumer(
            stack,
            db=parts.db,
            deps=SimpleNamespace(broker=broker, publisher=publisher),  # type: ignore[arg-type]
            settings=parts.settings,
            status=parts.status,
            source=parts.source,
            config=parts.config,
            object_store=object(),  # type: ignore[arg-type]
        )
    assert consumer is None
    if broker is not None:
        assert broker.subscribers == []


async def test_the_events_consumer_subscribes_on_the_started_broker_and_stops(
    parts: SimpleNamespace,
) -> None:
    broker = _Broker()
    async with AsyncExitStack() as stack:
        consumer = await start_events_consumer(
            stack,
            db=parts.db,
            deps=SimpleNamespace(broker=broker, publisher=object()),  # type: ignore[arg-type]
            settings=parts.settings,
            status=parts.status,
            source=parts.source,
            config=parts.config,
            object_store=object(),  # type: ignore[arg-type]
        )
        assert consumer is not None
        [subscriber] = broker.subscribers
        assert subscriber.running is True
        assert subscriber.queue.name == "policy-service.events"
        assert subscriber.queue.routing_key == "index.*"
        assert parts.db["consumed_messages"].index_names == [
            "message_id_consumer_unique",
            "consumed_at_ttl",
        ]
        assert parts.status.ensured is True
        assert parts.status.looked_for == ["policy_clauses"]  # the startup reload
    assert subscriber.running is False

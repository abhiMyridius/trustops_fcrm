"""Fixtures for the H4 <-> S3 loop against a real RabbitMQ and MongoDB.

Both are opt-in: ``FCRM_TEST_MONGO_URI`` (a replica set) and
``FCRM_TEST_RABBITMQ_URL`` (a broker with ``ops/rabbitmq/definitions.json``
imported, e.g. ``rabbitmqctl import_definitions``), for example::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28017/?replicaSet=rs0'
    export FCRM_TEST_RABBITMQ_URL='amqp://t:t@127.0.0.1:28672/'
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional

import aio_pika
import pytest
from faststream.rabbit import RabbitBroker

#: The queues the loop touches; purged before and after so a run never sees
#: another run's messages.
LOOP_QUEUES = (
    "context-worker.run",
    "context-worker.run.retry",
    "context-worker.run.dlq",
    "policy-service.events",
    "policy-service.events.retry",
    "policy-service.events.dlq",
    "case-service.events",
)


class MemoryObjects:
    """The vectors bucket, in memory: H4 puts the fixture, S3 reads it. It has
    the three ``ObjectStore`` methods the loop calls.
    """

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    async def put_bytes(
        self, bucket: str, key: str, data: bytes, content_type: str
    ) -> None:
        self.objects[(bucket, key)] = data

    async def get_bytes(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]

    async def ping(self, bucket: str) -> None:
        return None


async def _purge(url: str) -> None:
    connection = await aio_pika.connect_robust(url)
    try:
        channel = await connection.channel()
        for name in LOOP_QUEUES:
            queue = await channel.get_queue(name, ensure=False)
            await queue.purge()
    finally:
        await connection.close()


@pytest.fixture
def rabbitmq_url() -> str:
    """The broker the loop runs on (the test is skipped when it is unset)."""
    url = os.environ.get("FCRM_TEST_RABBITMQ_URL", "")
    if not url:
        pytest.skip("FCRM_TEST_RABBITMQ_URL not set")
    return url


@pytest.fixture
def memory_objects() -> MemoryObjects:
    """An empty in-memory vectors bucket."""
    return MemoryObjects()


@dataclass
class Loop:
    """S3 and H4 running against one real broker and one real Mongo."""

    broker: RabbitBroker
    policy_app: Any
    objects: MemoryObjects
    db: Any
    original_store: Optional[Any]


@pytest.fixture
async def broker_url(rabbitmq_url: str) -> AsyncIterator[str]:
    """``rabbitmq_url`` with the loop's queues emptied before and after."""
    await _purge(rabbitmq_url)
    yield rabbitmq_url
    await _purge(rabbitmq_url)


@pytest.fixture
async def loop(
    live_db: Any,
    broker_url: str,
    memory_objects: MemoryObjects,
    allow_all_opa: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> AsyncIterator[Loop]:
    """The whole index loop on real infrastructure: S3's real lifespan (Mongo,
    outbox relay, its ``index.*`` consumer added to the started broker) and H4's
    real unit (its ``context-worker.run`` consumer, S3's internal routes read
    through an in-process transport), the vectors bucket in memory.

    Order matters, as in the real processes: H4's subscriber is registered
    before the broker starts (the harness), S3's after (the ``api`` process).
    """
    import httpx
    from fcrm_common.auth import TokenVerifier
    from fcrm_common.broker import FastStreamPublisher
    from fcrm_common.http import ServiceClient
    from fcrm_common.service import ServiceDeps
    from fcrm_context_worker.app import build_unit
    from fcrm_context_worker.config import context_config_from_env
    from fcrm_context_worker.settings import ContextSettings
    from fcrm_policy import bus as policy_bus
    from fcrm_policy.lifespan import register
    from fcrm_policy.main import create_app
    from fcrm_policy.settings import PolicySettings

    for name, value in {
        "VECTOR_BACKEND": "memory",
        "EMBEDDING_PROVIDER": "dummy",
        "EMBEDDING_DIMENSION": "64",
        "CONTEXT_PROJECTS_ROOT": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        policy_bus.ObjectStore, "from_settings", lambda _settings: memory_objects
    )
    secret = "x" * 32
    broker = RabbitBroker(broker_url)
    publisher = FastStreamPublisher(broker)
    verifier = TokenVerifier(service_secret=secret)
    policy_app = create_app(standalone=False)

    h4_settings = ContextSettings(
        mongodb_uri=os.environ["FCRM_TEST_MONGO_URI"],
        mongodb_db=live_db.name,
        rabbitmq_url=broker_url,
        service_token_secret=secret,
        policy_service_url="http://policy.test",
        context_prefetch=2,
        env="test",
    )
    policy_client = ServiceClient(
        "http://policy.test",
        dependency="policy",
        caller="fcrm-context-worker",
        token_secret=secret,
        transport=httpx.ASGITransport(app=policy_app),
    )
    unit = build_unit(
        h4_settings,
        mongo=live_db.client,
        publisher=publisher,
        object_store=memory_objects,  # type: ignore[arg-type]
        policy_client=policy_client,
        config=context_config_from_env(projects_root=tmp_path),
    )
    unit.wire(broker)
    await broker.start()
    for hook in unit.after_startup:
        await hook()
    s3_settings = PolicySettings(
        mongodb_db=live_db.name,
        seed_on_empty=True,
        service_token_secret=secret,
        env="test",
    )
    deps = ServiceDeps(
        mongo=live_db.client,
        token_verifier=verifier,
        opa=allow_all_opa,
        publisher=publisher,
        lease_holder="relay_loop",
        broker=broker,
    )
    try:
        async with register(policy_app, s3_settings, deps):
            vector = policy_app.state.search._vector
            yield Loop(
                broker=broker,
                policy_app=policy_app,
                objects=memory_objects,
                db=live_db,
                original_store=vector._store,
            )
    finally:
        for hook in reversed(unit.on_shutdown):
            await hook()
        await verifier.aclose()
        await broker.stop()

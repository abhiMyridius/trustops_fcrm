"""H4 as a harness unit (:func:`fcrm_context_worker.app.build_unit`) and its
environment contract (:class:`~fcrm_context_worker.settings.ContextSettings`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from faststream.rabbit import RabbitBroker, TestRabbitBroker
from fcrm_common.broker import FastStreamPublisher, passive_exchange
from fcrm_common.events import build_envelope
from fcrm_common.http import ServiceClient
from fcrm_common.mongo import get_client
from fcrm_common.settings import MissingSettingsError
from fcrm_context_worker.app import build_app, build_unit, wire_subscriber
from fcrm_context_worker.builder import BuildResult
from fcrm_context_worker.config import context_config_from_env
from fcrm_context_worker.consumer import ContextConsumer
from fcrm_context_worker.settings import ContextSettings
from fcrm_contracts.events.payloads import IndexBuild, IndexBuilt

ENV_NAMES = (
    "MONGODB_URI",
    "MONGODB_DB",
    "RABBITMQ_URL",
    "SERVICE_TOKEN_SECRET",
    "POLICY_SERVICE_URL",
    "CONTEXT_PREFETCH",
    "CONTEXT_BUILD_TIMEOUT_SECONDS",
    "VECTOR_BACKEND",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "PROMPTS_DIR",
    "OPA_URL",
    "S3_BUCKET_VECTORS",
    "S3_REGION",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides: Any) -> ContextSettings:
    values: dict[str, Any] = {
        "mongodb_uri": "mongodb://localhost:27017/?replicaSet=rs0",
        "rabbitmq_url": "amqp://guest:guest@localhost:5672/",
        "service_token_secret": "x" * 32,
        "policy_service_url": "http://localhost:8000/api/fcrm/policy",
        **overrides,
    }
    return ContextSettings(**values)


# --- settings ---------------------------------------------------------------


def test_the_defaults_are_the_ones_the_design_gives() -> None:
    settings = _settings()
    assert settings.app_name == "fcrm-context-worker"
    assert settings.mongodb_db == "fcrm_harness"
    assert settings.health_port == 9103
    assert settings.context_prefetch == 4
    assert settings.context_build_timeout_seconds == 300.0
    assert settings.s3_bucket_vectors == "vectors"


def test_it_needs_mongo_the_broker_the_token_secret_and_s3_but_no_opa() -> None:
    settings = ContextSettings.load(
        mongodb_uri="mongodb://localhost:27017/?replicaSet=rs0",
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        service_token_secret="x" * 32,
        policy_service_url="http://localhost:8000/api/fcrm/policy",
    )
    assert settings.opa_url is None
    assert ContextSettings.required_any_of == ()


def test_a_missing_policy_url_fails_fast_naming_it() -> None:
    with pytest.raises(MissingSettingsError) as info:
        ContextSettings.load(
            mongodb_uri="mongodb://localhost:27017/?replicaSet=rs0",
            rabbitmq_url="amqp://guest:guest@localhost:5672/",
            service_token_secret="x" * 32,
        )
    assert info.value.missing == ["POLICY_SERVICE_URL"]


def test_the_values_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://m:27017/?replicaSet=rs0")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://r:5672/")
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "y" * 32)
    monkeypatch.setenv("POLICY_SERVICE_URL", "http://api:8000/api/fcrm/policy")
    monkeypatch.setenv("CONTEXT_PREFETCH", "2")
    monkeypatch.setenv("CONTEXT_BUILD_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("S3_BUCKET_VECTORS", "vectors-eu")

    settings = ContextSettings.load()

    assert settings.context_prefetch == 2
    assert settings.context_build_timeout_seconds == 60
    assert settings.s3_bucket_vectors == "vectors-eu"


@pytest.mark.parametrize(
    "overrides", [{"context_prefetch": 0}, {"context_build_timeout_seconds": 0}]
)
def test_an_unusable_value_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - pydantic's ValidationError
        _settings(**overrides)


# --- the unit -----------------------------------------------------------------


def _unit(
    settings: ContextSettings, fake_objects: Any, **kwargs: Any
) -> tuple[Any, RabbitBroker]:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    unit = build_unit(
        settings,
        mongo=get_client(str(settings.mongodb_uri)),
        publisher=FastStreamPublisher(broker),
        object_store=fake_objects,
        **kwargs,
    )
    return unit, broker


def test_the_unit_is_h4_and_wires_the_context_queue_with_its_prefetch(
    fake_objects: Any,
) -> None:
    unit, broker = _unit(_settings(context_prefetch=3), fake_objects)

    unit.wire(broker)

    assert unit.component_id == "H4"
    [subscriber] = broker.subscribers
    assert subscriber.queue.name == "context-worker.run"
    assert subscriber.queue.routing_key == "index.build"
    assert subscriber.channel.prefetch_count == 3


def test_the_unit_starts_one_hook_and_closes_one_client(fake_objects: Any) -> None:
    unit, _ = _unit(_settings(), fake_objects)
    assert len(unit.after_startup) == 1
    assert len(unit.on_shutdown) == 1


async def test_the_shutdown_hook_closes_the_policy_client(fake_objects: Any) -> None:
    closed: list[bool] = []
    client = ServiceClient(
        "http://policy.test",
        dependency="policy",
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})),
    )
    original = client.aclose

    async def closing() -> None:
        closed.append(True)
        await original()

    client.aclose = closing  # type: ignore[method-assign]
    unit, _ = _unit(_settings(), fake_objects, policy_client=client)

    await unit.on_shutdown[0]()

    assert closed == [True]


async def test_the_index_hook_creates_the_dedupe_indexes(
    routed_publisher: Any, fake_objects: Any, fake_consumed: Any
) -> None:
    consumed = fake_consumed()
    unit = build_unit(
        _settings(),
        mongo={"fcrm_harness": {"consumed_messages": consumed}},  # type: ignore[arg-type]
        publisher=routed_publisher,
        object_store=fake_objects,
    )

    await unit.after_startup[0]()

    assert consumed.index_names == ["message_id_consumer_unique", "consumed_at_ttl"]


async def test_the_vectors_check_probes_the_bucket_and_is_not_critical(
    fake_objects: Any,
) -> None:
    unit, _ = _unit(_settings(s3_bucket_vectors="vectors-eu"), fake_objects)
    [check] = unit.checks

    await check.fn()

    assert (check.name, check.critical) == ("vectors_bucket", False)
    assert fake_objects.pings == ["vectors-eu"]


async def test_the_vectors_check_fails_when_the_store_is_down(
    fake_objects: Any,
) -> None:
    fake_objects.unavailable = ConnectionError("object store down")
    unit, _ = _unit(_settings(), fake_objects)
    [check] = unit.checks
    with pytest.raises(ConnectionError):
        await check.fn()


def test_without_an_injected_store_the_real_one_is_built_from_the_settings() -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    settings = _settings(s3_endpoint_url="http://localhost:9000")
    unit = build_unit(
        settings,
        mongo=get_client(str(settings.mongodb_uri)),
        publisher=FastStreamPublisher(broker),
    )
    assert unit.component_id == "H4"  # built without opening a connection


def test_a_missing_policy_url_stops_the_unit_naming_the_variable(
    fake_objects: Any,
) -> None:
    settings = _settings(policy_service_url=None)
    with pytest.raises(MissingSettingsError, match="POLICY_SERVICE_URL"):
        build_unit(
            settings,
            mongo=get_client(str(settings.mongodb_uri)),
            publisher=FastStreamPublisher(RabbitBroker("amqp://guest@localhost/")),
            object_store=fake_objects,
        )


def test_wire_subscriber_binds_only_the_index_build_key(
    routed_publisher: Any, fake_objects: Any
) -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    consumer = ContextConsumer(
        None,  # type: ignore[arg-type]
        routed_publisher,
        builder=None,  # type: ignore[arg-type]
        store=fake_objects,
        bucket="vectors",
        timeout_s=1.0,
    )

    wire_subscriber(broker, consumer, prefetch=None)

    [subscriber] = broker.subscribers
    assert subscriber.queue.routing_key == "index.build"


def test_build_app_returns_one_asgi_app_with_the_context_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_app(_settings())
    assert {sub.queue.name for sub in app.broker.subscribers} == {"context-worker.run"}


# --- a delivery reaches the builder and a result leaves ------------------------


class _FixedBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def build(self, index: str, version: int) -> BuildResult:
        self.calls.append((index, version))
        return BuildResult(vector_count=2, fixture=b'{"fixture":true}')


async def test_an_index_build_message_is_built_and_announced_through_the_broker(
    routed_publisher: Any, fake_objects: Any, fake_consumed: Any
) -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    builder = _FixedBuilder()
    consumer = ContextConsumer(
        fake_consumed(),
        routed_publisher,
        builder=builder,  # type: ignore[arg-type]
        store=fake_objects,
        bucket="vectors",
        timeout_s=5.0,
    )
    wire_subscriber(broker, consumer, prefetch=1)
    request = build_envelope(
        "index.build",
        IndexBuild(job_id="job_1", index="controls", version=9).model_dump(mode="json"),
    )

    async with TestRabbitBroker(broker) as test_broker:
        await test_broker.publish(
            request.model_dump_json().encode(),
            exchange=passive_exchange("context.jobs"),
            routing_key="index.build",
            message_id=request.event_id,
            headers={"x-attempt": 0},
        )

    assert builder.calls == [("controls", 9)]
    [(_, _, event)] = routed_publisher.of_type("index.built")
    assert IndexBuilt.model_validate(event.payload).fixture_key == "controls/9.json"
    assert ("vectors", "controls/9.json") in fake_objects.objects


# --- the embedding provider seam ------------------------------------------------


def test_the_dummy_provider_is_the_offline_default(tmp_path: Path) -> None:
    config = context_config_from_env({}, projects_root=tmp_path)
    assert config.embedding_provider == "dummy"
    assert config.synthesis_enabled is False

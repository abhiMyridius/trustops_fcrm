"""The composite process owns the one RabbitMQ broker: it starts before the
services (retrying while RabbitMQ boots), hands every service the same
publisher and lease holder, adds RabbitMQ to the aggregate readiness, and stops
after them so a relay can drain through it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Optional

import fcrm_api.lifespan as api_lifespan_module
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_api.lifespan import start_broker
from fcrm_api.main import create_app
from fcrm_api.registry import MOUNTS
from fcrm_api.settings import ApiSettings
from fcrm_common.broker import FastStreamPublisher
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.health import ReadinessCheck
from fcrm_common.service import ServiceDeps
from fcrm_common.settings import MissingSettingsError


class FakeBroker:
    """A broker that connects after ``fail_times`` refused attempts."""

    def __init__(self, events: list[str], fail_times: int = 0) -> None:
        self.events = events
        self.fail_times = fail_times
        self.attempts = 0
        self.published: list[tuple[Any, ...]] = []

    async def start(self) -> None:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise ConnectionRefusedError("rabbitmq is still starting")
        self.events.append("broker.start")

    async def stop(self) -> None:
        self.events.append("broker.stop")

    async def publish(self, body: bytes, **kwargs: Any) -> None:
        self.published.append((body, kwargs))


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_lifespan_module, "BROKER_RETRY_S", 0.001)
    monkeypatch.setattr(api_lifespan_module, "BROKER_START_TIMEOUT_S", 0.05)


async def test_the_broker_start_is_retried_while_rabbitmq_boots(
    fast_retries: None,
) -> None:
    broker = FakeBroker([], fail_times=3)
    await start_broker(broker)  # type: ignore[arg-type]
    assert broker.attempts == 4
    assert broker.events == ["broker.start"]


async def test_a_broker_that_never_comes_up_is_a_503_naming_rabbitmq(
    fast_retries: None, caplog: pytest.LogCaptureFixture
) -> None:
    broker = FakeBroker([], fail_times=10_000)
    with pytest.raises(UpstreamUnavailableError) as raised:
        await start_broker(broker)  # type: ignore[arg-type]
    assert raised.value.extra["dependency"] == "rabbitmq"
    assert broker.attempts > 1
    assert "unreachable" in caplog.text


def test_the_api_needs_a_rabbitmq_url_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "MONGODB_URI": "mongodb://localhost:1/",
        "MONGODB_URI_AUDIT_WRITER": "mongodb://localhost:1/",
        "SERVICE_TOKEN_SECRET": "x" * 32,
        "OPA_URL": "http://opa:8181",
        "IDENTITY_VALIDATE_URL": "http://api:8000/api/fcrm/auth/core/validate",
        "ENV": "test",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    with pytest.raises(MissingSettingsError) as raised:
        ApiSettings.load()
    assert raised.value.missing == ["RABBITMQ_URL"]
    assert "RABBITMQ_URL" in ApiSettings.required_settings


def test_every_service_gets_the_same_publisher_and_lease_holder_and_order_is_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    seen: list[ServiceDeps] = []
    broker = FakeBroker(events)

    @asynccontextmanager
    async def register(
        sub: FastAPI, settings: Any, deps: ServiceDeps
    ) -> AsyncIterator[None]:
        seen.append(deps)
        events.append("register")
        try:
            yield
        finally:
            events.append("unregister")

    mounts = tuple(dataclasses.replace(m, register=register) for m in MOUNTS)
    for name, value in {
        "MONGODB_URI": "mongodb://localhost:1/?appName=api-broker-test-app",
        "MONGODB_URI_AUDIT_WRITER": "mongodb://localhost:1/?appName=api-broker-test-audit",
        "SERVICE_TOKEN_SECRET": "x" * 32,
        "OPA_URL": "http://opa:8181",
        "RABBITMQ_URL": "amqp://guest:guest@rabbitmq:5672/",
        "IDENTITY_VALIDATE_URL": "http://api:8000/api/fcrm/auth/core/validate",
        "ENV": "test",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(api_lifespan_module, "MOUNTS", mounts)
    # The composite's process-wide setup is global (logging, OpenTelemetry's
    # instrumentation of the app) and each service's own settings need their
    # own environment; none of that is what is under test.
    monkeypatch.setattr(api_lifespan_module, "init_otel", lambda *a, **k: None)
    monkeypatch.setattr(api_lifespan_module, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr(
        api_lifespan_module, "_service_settings", lambda mount, api: None
    )
    monkeypatch.setattr(api_lifespan_module, "build_broker", lambda url: broker)
    monkeypatch.setattr(
        api_lifespan_module,
        "amqp_check",
        lambda url: ReadinessCheck("rabbitmq", _ok),
    )

    app = create_app()
    with TestClient(app) as client:
        ready = client.get("/readyz")
        assert "rabbitmq" in ready.json()["checks"]
        assert len(seen) == len(MOUNTS)

    publishers = {id(d.publisher) for d in seen}
    holders = {d.lease_holder for d in seen}
    assert len(publishers) == 1
    assert isinstance(seen[0].publisher, FastStreamPublisher)
    assert len(holders) == 1
    assert next(iter(holders)).startswith("relay_")
    assert events[0] == "broker.start"
    assert events[-1] == "broker.stop"
    assert events.count("register") == len(MOUNTS) == events.count("unregister")
    assert events.index("unregister") < events.index("broker.stop")


async def _ok() -> Optional[None]:
    return None

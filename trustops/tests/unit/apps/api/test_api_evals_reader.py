"""The composite hands S4, and only S4, a read-only Mongo client on the eval results
(``MONGODB_URI_EVALS_READER``, role ``fcrmEvalsReadOnly``). Without the setting no
service gets one and S4's agent-quality routes answer 503; the insert-only audit
client is never reused for it.
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
from fcrm_api.main import create_app
from fcrm_api.registry import MOUNTS
from fcrm_common.health import ReadinessCheck
from fcrm_common.service import ServiceDeps


class FakeBroker:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish(self, body: bytes, **kwargs: Any) -> None:
        return None


async def _ok() -> Optional[None]:
    return None


def _run_api(
    monkeypatch: pytest.MonkeyPatch, evals_uri: Optional[str]
) -> dict[str, ServiceDeps]:
    """Start the composite over recording registers; the deps each mount got."""
    seen: dict[str, ServiceDeps] = {}

    def recording(component_id: str) -> Any:
        @asynccontextmanager
        async def register(
            sub: FastAPI, settings: Any, deps: ServiceDeps
        ) -> AsyncIterator[None]:
            seen[component_id] = deps
            yield

        return register

    mounts = tuple(
        dataclasses.replace(m, register=recording(m.component_id)) for m in MOUNTS
    )
    env = {
        "MONGODB_URI": "mongodb://localhost:1/?appName=evals-test-app",
        "MONGODB_URI_AUDIT_WRITER": "mongodb://localhost:1/?appName=evals-test-audit",
        "SERVICE_TOKEN_SECRET": "x" * 32,
        "OPA_URL": "http://opa:8181",
        "RABBITMQ_URL": "amqp://guest:guest@rabbitmq:5672/",
        "IDENTITY_VALIDATE_URL": "http://api:8000/api/fcrm/auth/core/validate",
        "ENV": "test",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if evals_uri is None:
        monkeypatch.delenv("MONGODB_URI_EVALS_READER", raising=False)
    else:
        monkeypatch.setenv("MONGODB_URI_EVALS_READER", evals_uri)
    monkeypatch.setattr(api_lifespan_module, "MOUNTS", mounts)
    monkeypatch.setattr(api_lifespan_module, "init_otel", lambda *a, **k: None)
    monkeypatch.setattr(api_lifespan_module, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr(
        api_lifespan_module, "_service_settings", lambda mount, api: None
    )
    monkeypatch.setattr(api_lifespan_module, "build_broker", lambda url: FakeBroker())
    monkeypatch.setattr(
        api_lifespan_module, "amqp_check", lambda url: ReadinessCheck("rabbitmq", _ok)
    )
    with TestClient(create_app()):
        pass
    return seen


def test_only_the_audit_service_gets_the_read_only_evals_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _run_api(monkeypatch, "mongodb://localhost:1/?appName=evals-test-reader")
    assert seen["S4"].evals_mongo is not None
    assert seen["S4"].evals_mongo is not seen["S4"].mongo
    others = {cid: deps.evals_mongo for cid, deps in seen.items() if cid != "S4"}
    assert set(others) == {"X1", "S1", "S2", "S3", "S5"}
    assert all(client is None for client in others.values())


def test_without_the_setting_nobody_gets_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _run_api(monkeypatch, None)
    assert all(deps.evals_mongo is None for deps in seen.values())

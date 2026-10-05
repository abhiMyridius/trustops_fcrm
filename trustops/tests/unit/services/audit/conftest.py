"""Fixtures shared by S4's idempotency, proposals and row-shape tests: the real
routes over an :class:`InMemoryAuditStore`, called as the service account (the
only role that writes), plus request-body factories."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_audit.routes import router
from fcrm_audit.store import InMemoryAuditStore
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Role


@pytest.fixture
def audit_store() -> InMemoryAuditStore:
    """An empty in-memory store (the demo case 0151 starts with no records)."""
    return InMemoryAuditStore()


@pytest.fixture
def audit_app(
    audit_store: InMemoryAuditStore, wire_test_auth: Callable[[FastAPI], FastAPI]
) -> FastAPI:
    """S4's routes (no lifespan) over ``audit_store``, authenticated as whoever
    ``act_as`` last set."""
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.store = audit_store
    return wire_test_auth(application)


@pytest.fixture
def service_client(
    audit_app: FastAPI, act_as: Callable[[Role, str], None]
) -> TestClient:
    """A client calling as H1's service account."""
    act_as(Role.SERVICE, "svc:caseflow")
    return TestClient(audit_app)


@pytest.fixture
def analyst_client(
    audit_app: FastAPI, act_as: Callable[[Role, str], None]
) -> TestClient:
    """A client calling as an analyst (reads only)."""
    act_as(Role.ANALYST, "u-riyer")
    return TestClient(audit_app)


@pytest.fixture
async def async_service_client(
    audit_app: FastAPI, act_as: Callable[[Role, str], None]
) -> AsyncIterator[httpx.AsyncClient]:
    """An async client for requests that must overlap in one event loop."""
    act_as(Role.SERVICE, "svc:caseflow")
    transport = httpx.ASGITransport(app=audit_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://s4") as client:
        yield client


@pytest.fixture
def record_body() -> Callable[..., dict[str, Any]]:
    """``record_body(case_id="C1", **overrides)``: a valid ``POST /records`` body."""

    def _body(case_id: str = "FCRM-2026-0151", **overrides: Any) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "type": "case.created",
            "actor": "system",
            "summary": "case created",
            "data": {},
            **overrides,
        }

    return _body


@pytest.fixture
def proposal_body() -> Callable[..., dict[str, Any]]:
    """``proposal_body(case_id="C1", **overrides)``: a valid ``POST /proposals``
    body (a research proposal from run ``run_1``)."""

    def _body(case_id: str = "FCRM-2026-0151", **overrides: Any) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "run_id": "run_1",
            "agent": "research",
            "kind": "research",
            "payload": {"clauses": ["TP71-2.3"]},
            "model": "fake-model",
            "prompt_v": "research.system@1",
            "tier": "strong",
            **overrides,
        }

    return _body

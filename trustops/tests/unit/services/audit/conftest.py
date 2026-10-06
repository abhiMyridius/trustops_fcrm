"""Fixtures shared by S4's idempotency, proposals and row-shape tests: the real
routes over an :class:`InMemoryAuditStore`, called as the service account (the
only role that writes), plus request-body factories."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, Optional

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_audit.analytics import InMemoryAnalyticsStore
from fcrm_audit.examiner import ExaminerService, InMemoryExaminerStore
from fcrm_audit.quality import InMemoryEvalsReader
from fcrm_audit.routes import router
from fcrm_audit.routes_analytics import router as analytics_router
from fcrm_audit.routes_examiner import router as examiner_router
from fcrm_audit.store import InMemoryAuditStore
from fcrm_common.errors import install_error_handlers
from fcrm_common.events import Publisher, decode_envelope
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.api.audit import RecordAppend
from fcrm_contracts.enums import Role
from fcrm_contracts.events.envelope import Envelope


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


class RecordingPublisher:
    """A broker stand-in: remembers every envelope published, or raises
    ``failure`` on every publish when one is given.
    """

    def __init__(self, failure: Optional[Exception] = None) -> None:
        self.failure = failure
        self.published: list[tuple[str, str, Envelope]] = []

    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        """Record one publish (the :class:`fcrm_common.events.Publisher` seam)."""
        if self.failure is not None:
            raise self.failure
        self.published.append((exchange, routing_key, decode_envelope(body)))


@pytest.fixture
def recording_publisher() -> RecordingPublisher:
    """A publisher that records and never fails."""
    return RecordingPublisher()


@pytest.fixture
def failing_publisher() -> RecordingPublisher:
    """A publisher whose broker is down: every publish raises ``OSError``."""
    return RecordingPublisher(failure=OSError("broker down"))


@pytest.fixture
async def chained_case(audit_store: InMemoryAuditStore) -> str:
    """``FCRM-2026-0151`` with a three-record chain (seq 1 to 3)."""
    for seq_type in ("case.created", "workflow.started", "case.submitted"):
        await audit_store.append(
            RecordAppend(
                case_id="FCRM-2026-0151",
                type=seq_type,
                actor="system",
                summary=seq_type,
                data={},
            )
        )
    return "FCRM-2026-0151"


@pytest.fixture
def build_examiner_app(
    audit_store: InMemoryAuditStore, wire_test_auth: Callable[[FastAPI], FastAPI]
) -> Callable[..., FastAPI]:
    """``build_examiner_app(publisher, wait_s=0.3, poll_s=0.02)``: S4's routes
    with the examiner wired over an in-memory store and ``publisher`` (``None``
    for a service without a broker).
    """

    def _build(
        publisher: Optional[Publisher], wait_s: float = 0.3, poll_s: float = 0.02
    ) -> FastAPI:
        application = FastAPI()
        install_error_handlers(application)
        application.include_router(router)
        application.include_router(examiner_router)
        application.state.store = audit_store
        application.state.examiner = ExaminerService(
            audit_store,
            InMemoryExaminerStore(),
            publisher,
            wait_s=wait_s,
            poll_s=poll_s,
        )
        return wire_test_auth(application)

    return _build


@pytest.fixture
def examiner_app(
    build_examiner_app: Callable[..., FastAPI],
    recording_publisher: RecordingPublisher,
) -> FastAPI:
    """The examiner over a recording publisher with a short wait."""
    return build_examiner_app(recording_publisher)


@pytest.fixture
def build_analytics_app(
    audit_store: InMemoryAuditStore, wire_test_auth: Callable[[FastAPI], FastAPI]
) -> Callable[..., FastAPI]:
    """``build_analytics_app(analytics=None, evals=None)``: S4's assurance routes
    over the given in-memory analytics store and eval reader (either ``None``
    leaves it unwired, as a deployment without that credential would be).
    """

    def _build(
        analytics: Optional[InMemoryAnalyticsStore] = None,
        evals: Optional[InMemoryEvalsReader] = None,
    ) -> FastAPI:
        application = FastAPI()
        install_error_handlers(application)
        application.include_router(analytics_router)
        application.state.store = audit_store
        if analytics is not None:
            application.state.analytics = analytics
        if evals is not None:
            application.state.evals = evals
        return wire_test_auth(application)

    return _build

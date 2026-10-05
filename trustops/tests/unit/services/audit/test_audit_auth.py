"""S4's route authorization (D-23 follow-up): every route needs a bearer
principal and an OPA allow, then its own role list. Only the service
account appends; analyst, committee and service read; the PO reads
nothing here -- the seeded demo chain's summaries literally contain
"Composite 9.8 · Band 2", which a PO must never receive.

401 tests use the real ``fcrm_audit.main.create_app`` with no override
(the real ``current_user`` refuses); 403 tests use ``wire_test_auth`` /
``act_as`` from ``tests/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_audit.main import create_app
from fcrm_audit.routes import router
from fcrm_audit.seedmodel import build_demo_chain
from fcrm_audit.store import InMemoryAuditStore
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Role
from fcrm_seed.loader import demo_case

APPEND_BODY: dict[str, Any] = {
    "case_id": "FCRM-2026-0151",
    "type": "case.created",
    "actor": "system",
    "summary": "case created",
    "data": {},
}

PROPOSAL_BODY: dict[str, Any] = {
    "case_id": "FCRM-2026-0151",
    "run_id": "run_1",
    "agent": "research",
    "kind": "research",
    "payload": {"clauses": []},
    "model": "fake-model",
    "prompt_v": "research.system@1",
    "tier": "strong",
}

#: Every route S4 serves, as (method, path, json body or None).
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("post", "/records", APPEND_BODY),
    ("post", "/proposals", PROPOSAL_BODY),
    ("get", "/verify/FCRM-2026-0151", None),
    ("get", "/records/FCRM-2026-0151", None),
    ("get", "/records?case_id=FCRM-2026-0151", None),
    ("get", "/proposals?case_id=FCRM-2026-0151", None),
    ("get", "/head/FCRM-2026-0151", None),
]


@pytest.fixture
def bare_app() -> FastAPI:
    """Routes + health over the real 67-record demo chain, no auth wiring."""
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.store = InMemoryAuditStore(build_demo_chain(demo_case()))
    return application


@pytest.fixture
def app(bare_app: FastAPI, wire_test_auth: Callable[[FastAPI], FastAPI]) -> FastAPI:
    return wire_test_auth(bare_app)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_refuses_an_unauthenticated_caller(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = TestClient(create_app()).request(method.upper(), path, json=body)
    assert response.status_code == 401, response.text
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_health_routes_stay_unauthenticated() -> None:
    client = TestClient(create_app())
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_po_token_is_403_on_every_route(
    client: TestClient,
    act_as: Callable[[Role, str], None],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    act_as(Role.PO, "u-pnair")
    response = client.request(method.upper(), path, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("/forbidden")
    # Nothing of the chain leaks in the refusal, not even a count.
    assert "Composite" not in response.text
    assert "Band" not in response.text
    assert "record_count" not in response.text


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.OBSERVER, Role.PO])
@pytest.mark.parametrize(
    ("path", "body"), [("/records", APPEND_BODY), ("/proposals", PROPOSAL_BODY)]
)
def test_only_the_service_account_may_append(
    client: TestClient,
    act_as: Callable[[Role, str], None],
    role: Role,
    path: str,
    body: dict[str, Any],
) -> None:
    act_as(role, "someone")
    assert client.post(path, json=body).status_code == 403
    act_as(Role.SERVICE, "svc:caseflow")
    assert client.post(path, json=body).status_code == 201


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.SERVICE])
def test_analyst_committee_and_service_read_the_chain(
    client: TestClient, act_as: Callable[[Role, str], None], role: Role
) -> None:
    act_as(role, "reader")
    verify = client.get("/verify/FCRM-2026-0151")
    assert verify.status_code == 200
    assert verify.json()["record_count"] == 67
    page = client.get("/records/FCRM-2026-0151", params={"limit": 5})
    assert page.status_code == 200
    assert len(page.json()["items"]) == 5
    rows = client.get("/records", params={"case_id": "FCRM-2026-0151", "limit": 5})
    assert rows.status_code == 200
    assert len(rows.json()["items"]) == 5
    assert client.get("/head/FCRM-2026-0151").json()["seq"] == 67
    assert client.get("/proposals", params={"case_id": "FCRM-2026-0151"}).json() == {
        "items": [],
        "next_cursor": None,
        "total": None,
    }


def test_observer_is_monitor_only_and_does_not_read_the_chain(
    client: TestClient, act_as: Callable[[Role, str], None]
) -> None:
    """ADR-19 scopes ``fcrm_observer`` to the harness monitor; ``ops/opa/
    tests`` pins the same for ``audit``. Kept in step here.
    """
    act_as(Role.OBSERVER, "u-observer")
    assert client.get("/verify/FCRM-2026-0151").status_code == 403
    assert client.get("/records/FCRM-2026-0151").status_code == 403
    assert (
        client.get("/records", params={"case_id": "FCRM-2026-0151"}).status_code == 403
    )
    assert (
        client.get("/proposals", params={"case_id": "FCRM-2026-0151"}).status_code
        == 403
    )
    assert client.get("/head/FCRM-2026-0151").status_code == 403


def test_an_opa_deny_is_403_even_for_the_service_account(
    app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    def deny(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"allow": False}})

    transport = httpx.MockTransport(deny)
    install_authz(
        app.state,
        OPAClient("http://opa.invalid", client=httpx.AsyncClient(transport=transport)),
    )
    act_as(Role.SERVICE, "svc:caseflow")
    assert TestClient(app).post("/records", json=APPEND_BODY).status_code == 403


def test_no_opa_installed_is_503_never_an_allow(
    bare_app: FastAPI, act_as: Callable[[Role, str], None], acting_principal: dict
) -> None:
    from fcrm_common.auth import current_user

    bare_app.dependency_overrides[current_user] = lambda: acting_principal["principal"]
    act_as(Role.ANALYST, "u-riyer")
    response = TestClient(bare_app).get("/verify/FCRM-2026-0151")
    assert response.status_code == 503
    assert response.json()["type"].endswith("/authz_unavailable")

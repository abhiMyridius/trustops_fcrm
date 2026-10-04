"""S3's route authorization (D-23 follow-up): ``POST /search`` needs a
bearer principal and an OPA allow, then one of analyst / committee /
service. Never the PO: precedent hits carry a case's band and control
ratings, and the PO never sees either.

401 tests use the real ``fcrm_policy.main.create_app`` with no override
(the real ``current_user`` refuses); 403 tests use ``wire_test_auth`` /
``act_as`` from ``tests/conftest.py``. The search service is a stub here:
authorization is decided before any search runs, which the stub proves
by counting calls.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.api.policy import SearchRequest, SearchResponse
from fcrm_contracts.enums import Role
from fcrm_policy.main import create_app
from fcrm_policy.routes import router

SEARCH_BODY: dict[str, Any] = {
    "index": "precedent_cases",
    "query": "payout partner",
    "top_k": 5,
}


class _CountingSearch:
    """Stands in for ``SearchService``: records whether the route body was
    ever reached, and answers an empty page when it was.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, _body: SearchRequest) -> SearchResponse:
        self.calls += 1
        return SearchResponse(hits=[])


@pytest.fixture
def search() -> _CountingSearch:
    return _CountingSearch()


@pytest.fixture
def bare_app(search: _CountingSearch) -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.search = search
    return application


@pytest.fixture
def app(bare_app: FastAPI, wire_test_auth: Callable[[FastAPI], FastAPI]) -> FastAPI:
    return wire_test_auth(bare_app)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_search_refuses_an_unauthenticated_caller() -> None:
    response = TestClient(create_app()).post("/search", json=SEARCH_BODY)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_health_routes_stay_unauthenticated() -> None:
    client = TestClient(create_app())
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


@pytest.mark.parametrize("role", [Role.PO, Role.OBSERVER])
def test_po_and_observer_tokens_are_403_before_any_search_runs(
    client: TestClient,
    act_as: Callable[[Role, str], None],
    search: _CountingSearch,
    role: Role,
) -> None:
    act_as(role, "u-pnair")
    response = client.post("/search", json=SEARCH_BODY)
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("/forbidden")
    assert search.calls == 0


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.SERVICE])
def test_analyst_committee_and_service_may_search(
    client: TestClient,
    act_as: Callable[[Role, str], None],
    search: _CountingSearch,
    role: Role,
) -> None:
    act_as(role, "caller")
    assert client.post("/search", json=SEARCH_BODY).status_code == 200
    assert search.calls == 1


def test_an_opa_deny_is_403_even_for_the_analyst(
    app: FastAPI, act_as: Callable[[Role, str], None], search: _CountingSearch
) -> None:
    def deny(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"allow": False}})

    transport = httpx.MockTransport(deny)
    install_authz(
        app.state,
        OPAClient("http://opa.invalid", client=httpx.AsyncClient(transport=transport)),
    )
    act_as(Role.ANALYST, "u-riyer")
    assert TestClient(app).post("/search", json=SEARCH_BODY).status_code == 403
    assert search.calls == 0


def test_no_opa_installed_is_503_never_an_allow(
    bare_app: FastAPI, act_as: Callable[[Role, str], None], acting_principal: dict
) -> None:
    from fcrm_common.auth import current_user

    bare_app.dependency_overrides[current_user] = lambda: acting_principal["principal"]
    act_as(Role.ANALYST, "u-riyer")
    response = TestClient(bare_app).post("/search", json=SEARCH_BODY)
    assert response.status_code == 503
    assert response.json()["type"].endswith("/authz_unavailable")

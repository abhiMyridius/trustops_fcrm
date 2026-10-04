"""S2's route authorization (D-23 follow-up): every route needs a bearer
principal and an OPA allow, then its own role list. The PO role is on no
route here -- a score, band or route never reaches a PO from this service.

The 401 tests use the real ``fcrm_scoring.main.create_app`` with *no*
dependency override, so it is the real ``fcrm_common.auth.current_user``
refusing the call. The 403 tests use the ``wire_test_auth`` /``act_as``
fixtures (``tests/conftest.py``), so ``require_roles`` runs for real
against the set principal while OPA is an allow-all stub -- the OPA data
itself is covered by ``ops/opa/tests`` (``opa test``).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Role
from fcrm_scoring.main import create_app
from fcrm_scoring.routes import router
from fcrm_scoring.seedmodel import scoring_model_from_seed
from fcrm_scoring.store import InMemoryConfigStore
from fcrm_seed.loader import scoring_model

#: A structurally valid one-domain request (ScoreRequest refuses an empty
#: domain list with 422, so auth is checked against a body that would score).
SCORE_BODY: dict = {
    "case_id": "FCRM-2026-0151",
    "scoring_v": 3,
    "domains": [
        {
            "domain": "channel",
            "likelihood": 4,
            "impact": 5,
            "controls": [
                {
                    "control_id": "CTL-T01",
                    "domain": "channel",
                    "rating": 7.6,
                    "catalogue_weight": 0.35,
                }
            ],
        }
    ],
}

#: Every route S2 serves, as (method, path, json body or None).
ROUTES: list[tuple[str, str, dict | None]] = [
    ("post", "/score", SCORE_BODY),
    ("get", "/models/active", None),
    ("get", "/models/3", None),
    ("get", "/routing/active", None),
]


def _call(
    client: TestClient, method: str, path: str, body: dict | None
) -> httpx.Response:
    return client.request(method.upper(), path, json=body)


@pytest.fixture
def bare_app() -> FastAPI:
    """Routes + health over the seeded in-memory store, *no* auth wiring:
    what ``create_app()`` gives before any lifespan runs.
    """
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    model = scoring_model_from_seed(scoring_model())
    application.state.store = InMemoryConfigStore(models=[model], routing=[])
    return application


@pytest.fixture
def app(bare_app: FastAPI, wire_test_auth: Callable[[FastAPI], FastAPI]) -> FastAPI:
    return wire_test_auth(bare_app)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ── 401: no token ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_refuses_an_unauthenticated_caller(
    method: str, path: str, body: dict | None
) -> None:
    response = _call(TestClient(create_app()), method, path, body)
    assert response.status_code == 401, response.text
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert response.json()["type"].endswith("/unauthorized")


def test_health_routes_stay_unauthenticated() -> None:
    client = TestClient(create_app())
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


# ── 403: wrong role ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_po_token_is_403_on_every_route(
    client: TestClient,
    act_as: Callable[[Role, str], None],
    method: str,
    path: str,
    body: dict | None,
) -> None:
    act_as(Role.PO, "u-pnair")
    response = _call(client, method, path, body)
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("/forbidden")


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_an_observer_token_is_403_on_every_route(
    client: TestClient,
    act_as: Callable[[Role, str], None],
    method: str,
    path: str,
    body: dict | None,
) -> None:
    act_as(Role.OBSERVER, "u-observer")
    assert _call(client, method, path, body).status_code == 403


def test_committee_may_read_models_and_routing_but_never_score(
    client: TestClient, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.COMMITTEE, "u-amendes")
    assert client.get("/models/active").status_code == 200
    assert client.get("/models/3").status_code == 200
    # routing=[] in the store: the route is reached (not 403); the store
    # has nothing active, which is this fixture's concern, not auth's.
    assert client.get("/routing/active").status_code != 403
    scored = client.post("/score", json=ROUTES[0][2])
    assert scored.status_code == 403


@pytest.mark.parametrize("role", [Role.SERVICE, Role.ANALYST])
def test_service_and_analyst_reach_post_score(
    client: TestClient, act_as: Callable[[Role, str], None], role: Role
) -> None:
    act_as(role, "caller")
    response = client.post("/score", json=SCORE_BODY)
    assert response.status_code == 200, response.text
    assert "composite" in response.json()


# ── OPA is consulted on every route and fails closed ────────────────────


def _opa(handler: Callable[[httpx.Request], httpx.Response]) -> OPAClient:
    transport = httpx.MockTransport(handler)
    return OPAClient(
        "http://opa.invalid", client=httpx.AsyncClient(transport=transport)
    )


def test_an_opa_deny_is_403_even_for_a_role_the_route_lists(
    app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    seen: list[dict] = []

    def deny(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"result": {"allow": False}})

    install_authz(app.state, _opa(deny))
    act_as(Role.ANALYST, "u-riyer")
    response = TestClient(app).get("/models/active")
    assert response.status_code == 403
    assert seen, "OPA was not consulted"


def test_opa_input_is_the_route_template_for_service_scoring(
    app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    inputs: list[dict] = []

    def record(request: httpx.Request) -> httpx.Response:
        inputs.append(json.loads(request.content)["input"])
        return httpx.Response(200, json={"result": {"allow": True}})

    install_authz(app.state, _opa(record))
    act_as(Role.ANALYST, "u-riyer")
    assert TestClient(app).get("/models/3").status_code == 200
    assert inputs == [
        {
            "service": "scoring",
            "method": "GET",
            "path": "/models/{version}",
            "user": {"id": "u-riyer", "roles": ["fcrm_analyst"]},
        }
    ]


def test_opa_unreachable_is_503_never_an_allow(
    app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    install_authz(app.state, _opa(down))
    act_as(Role.ANALYST, "u-riyer")
    response = TestClient(app).get("/models/active")
    assert response.status_code == 503
    assert response.json()["type"].endswith("/authz_unavailable")


def test_no_opa_installed_is_503_never_an_allow(
    bare_app: FastAPI,
    act_as: Callable[[Role, str], None],
    acting_principal: dict,
) -> None:
    """A principal override alone (no ``install_authz``) must not open the
    routes: the router-level dependency fails closed when the lifespan's
    wiring is missing.
    """
    from fcrm_common.auth import current_user

    bare_app.dependency_overrides[current_user] = lambda: acting_principal["principal"]
    act_as(Role.ANALYST, "u-riyer")
    response = TestClient(bare_app).get("/models/active")
    assert response.status_code == 503

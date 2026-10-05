"""Every route S3 gained for the corpus is behind the router-wide check: no
token is a 401, an OPA deny is a 403 even for the analyst, and no OPA at all is
a 503 (fail closed). The routes are included from sub-routers, so this pins that
the parent's authorization really covers them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.auth import current_user
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.errors import install_error_handlers
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from fcrm_policy.main import create_app
from fcrm_policy.routes import router

ROUTES: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/clauses/FCRM42-3.1", {}),
    ("POST", "/policy/search", {"q": "cash pickup agent"}),
    ("POST", "/search", {"q": "cash pickup agent", "case_id": "FCRM-2026-0151"}),
    ("POST", "/precedents/search", {"query": "payout partner"}),
    ("GET", "/internal/clauses", {}),
    ("GET", "/internal/precedents", {}),
    ("POST", "/internal/precedents", {}),
    ("POST", "/jurisdictions/versions/fixture/activate", {}),
]


def _call(client: TestClient, method: str, path: str, body: dict[str, Any]) -> Any:
    if method == "GET":
        return client.get(path)
    return client.post(path, json=body)


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_an_unauthenticated_call_is_a_401_on_every_new_route(
    method: str, path: str, body: dict[str, Any]
) -> None:
    response = _call(TestClient(create_app()), method, path, body)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_an_opa_deny_is_a_403_before_any_route_body_runs(
    corpus_app: FastAPI,
    act_as: Callable[[Role, str], None],
    method: str,
    path: str,
    body: dict[str, Any],
) -> None:
    def deny(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"allow": False}})

    install_authz(
        corpus_app.state,
        OPAClient(
            "http://opa.invalid",
            client=httpx.AsyncClient(transport=httpx.MockTransport(deny)),
        ),
    )
    act_as(Role.SERVICE, "svc:test")
    response = _call(TestClient(corpus_app), method, path, body)
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_no_opa_installed_is_a_503_on_every_new_route(
    acting_principal: dict[str, Principal],
    act_as: Callable[[Role, str], None],
    method: str,
    path: str,
    body: dict[str, Any],
) -> None:
    bare = FastAPI()
    install_error_handlers(bare)
    bare.include_router(router)
    bare.dependency_overrides[current_user] = lambda: acting_principal["principal"]
    act_as(Role.SERVICE, "svc:test")

    response = _call(TestClient(bare), method, path, body)

    assert response.status_code == 503
    assert response.json()["type"].endswith("/authz_unavailable")

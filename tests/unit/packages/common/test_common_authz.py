"""fcrm_common.authz: OPA decision shapes, caching, fail-closed, and the
route-template dependency (design A §8.2-8.3).
"""

from __future__ import annotations

import json
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from fcrm_common.auth import TokenVerifier, install_auth, mint_service_token
from fcrm_common.authz import OPAClient, install_authz, opa_dependency
from fcrm_common.cache import InMemoryTTLCache
from fcrm_common.errors import AuthzUnavailableError, install_error_handlers
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

ANALYST = Principal(
    sub="u-riyer", roles=[Role.ANALYST], name="Rahul Iyer", typ="access"
)
SECRET = "z" * 40


class Opa:
    def __init__(self, result) -> None:
        self.result = result
        self.inputs: list[tuple[str, dict]] = []
        self.status = 200
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        self.inputs.append((request.url.path, json.loads(request.content)["input"]))
        if self.result is None:
            return httpx.Response(self.status, json={})
        return httpx.Response(self.status, json={"result": self.result})


def opa_client(opa: Opa, **kwargs) -> OPAClient:
    return OPAClient(
        "http://opa:8181/",
        client=httpx.AsyncClient(transport=httpx.MockTransport(opa)),
        **kwargs,
    )


async def test_authz_posts_route_template_input_to_response_rule():
    opa = Opa({"allow": True, "matched": ["case-get"]})
    assert await opa_client(opa).authz(
        ANALYST, "case", "get", "/cases/{id}/items/{item_id}"
    )
    [(path, payload)] = opa.inputs
    assert path == "/v1/data/authz/response"
    assert payload == {
        "service": "case",
        "method": "GET",
        "path": "/cases/{id}/items/{item_id}",
        "user": {"id": "u-riyer", "roles": ["fcrm_analyst"]},
    }


@pytest.mark.parametrize(
    ("result", "allowed"),
    [
        ({"allow": False}, False),
        (True, True),
        (False, False),
        (None, False),
        ({"allow": "yes"}, False),
    ],
)
async def test_authz_interprets_decision_shapes(result, allowed):
    assert (
        await opa_client(Opa(result)).authz(ANALYST, "case", "GET", "/cases") is allowed
    )


async def test_tool_permit_input_and_package():
    opa = Opa({"allow": True})
    client = opa_client(opa, rule="allow")
    assert await client.tool_permit(
        "research", "policy_search", "FCRM-2026-0151", "fcrm_service", "run_1"
    )
    [(path, payload)] = opa.inputs
    assert path == "/v1/data/tools/allow"
    assert payload == {
        "agent": "research",
        "tool": "policy_search",
        "case_id": "FCRM-2026-0151",
        "run_id": "run_1",
        "role": "fcrm_service",
    }


async def test_decisions_are_cached():
    opa = Opa({"allow": True})
    client = opa_client(opa, cache=InMemoryTTLCache())
    for _ in range(3):
        assert await client.authz(ANALYST, "case", "GET", "/cases/{id}")
    assert len(opa.inputs) == 1


async def test_tool_permit_cache_distinguishes_missing_case():
    opa = Opa({"allow": True})
    client = opa_client(opa, cache=InMemoryTTLCache())
    await client.tool_permit(
        "critic", "rules_check", "FCRM-2026-0151", "fcrm_service", "r"
    )
    await client.tool_permit("critic", "rules_check", None, "fcrm_service", "r")
    assert len(opa.inputs) == 2


async def test_transport_failure_fails_closed():
    opa = Opa({"allow": True})
    opa.down = True
    with pytest.raises(AuthzUnavailableError):
        await opa_client(opa).authz(ANALYST, "case", "GET", "/cases")


async def test_opa_error_status_fails_closed():
    opa = Opa({"allow": True})
    opa.status = 500
    with pytest.raises(AuthzUnavailableError):
        await opa_client(opa).tool_permit("critic", "x", "c", "fcrm_service", "r")


class BrokenCache:
    async def get(self, key):
        raise ConnectionError("redis down")

    async def set(self, key, value, ttl_s):
        raise ConnectionError("redis down")


async def test_cache_outage_does_not_change_the_answer():
    opa = Opa({"allow": False})
    client = opa_client(opa, cache=BrokenCache())
    assert await client.authz(ANALYST, "case", "GET", "/cases") is False


def test_opa_dependency_uses_route_template_and_maps_deny_to_403():
    opa = Opa(None)
    seen: list[dict] = []

    def decide(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)["input"]
        seen.append(payload)
        return httpx.Response(
            200, json={"result": {"allow": payload["method"] == "GET"}}
        )

    app = FastAPI()
    install_error_handlers(app)
    install_auth(app.state, TokenVerifier(service_secret=SECRET))
    install_authz(
        app.state,
        OPAClient(
            "http://opa:8181",
            client=httpx.AsyncClient(transport=httpx.MockTransport(decide)),
        ),
    )

    @app.get("/cases/{case_id}")
    async def get_case(
        case_id: str, principal: Annotated[Principal, Depends(opa_dependency("case"))]
    ) -> dict:
        return {"case_id": case_id, "sub": principal.sub}

    @app.post("/cases/{case_id}")
    async def post_case(
        case_id: str, principal: Annotated[Principal, Depends(opa_dependency("case"))]
    ) -> dict:
        return {}

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {mint_service_token(SECRET, 'caseflow')}"}
    ok = client.get("/cases/FCRM-2026-0151", headers=headers)
    assert ok.json() == {"case_id": "FCRM-2026-0151", "sub": "svc:caseflow"}
    assert seen[0]["path"] == "/cases/{case_id}"
    denied = client.post("/cases/FCRM-2026-0151", headers=headers)
    assert denied.status_code == 403
    assert denied.headers["content-type"] == "application/problem+json"
    assert opa.inputs == []

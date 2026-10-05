"""OPA authorizes against the unprefixed route template; mounting a service
under its public prefix must not change what ``route_template`` returns."""

from __future__ import annotations

from fastapi.testclient import TestClient
from fcrm_api.main import create_app
from fcrm_common.authz import route_template
from starlette.requests import Request


def test_route_template_inside_a_mounted_service_is_unprefixed():
    app = create_app()
    scoring = app.state.mounted["S2"]
    seen: list[str] = []

    @scoring.get("/_probe/{item}")
    async def probe(request: Request, item: str) -> dict[str, str]:
        seen.append(route_template(request))
        return {"item": item}

    response = TestClient(app).get("/api/fcrm/scoring/_probe/abc")

    assert response.status_code == 200
    assert response.json() == {"item": "abc"}
    assert seen == ["/_probe/{item}"]


def test_request_app_state_is_the_mounted_service_not_the_composite():
    app = create_app()
    scoring = app.state.mounted["S2"]
    seen: list[object] = []

    @scoring.get("/_state")
    async def state(request: Request) -> dict[str, bool]:
        seen.append(request.app)
        return {"ok": True}

    TestClient(app).get("/api/fcrm/scoring/_state")

    assert seen == [scoring]
    assert seen[0] is not app

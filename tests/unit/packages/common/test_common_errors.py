"""fcrm_common.errors: every error maps to RFC 9457 problem+json
(BUILD-PLAN §3.9, design A §10.1-10.2).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common import errors
from fcrm_common.errors import install_error_handlers
from fcrm_contracts.common import Problem
from pydantic import BaseModel, ConfigDict
from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError

CASES = [
    (errors.NotFoundError, 404, "not_found", "Not found"),
    (errors.ValidationError, 422, "validation_failed", "Validation failed"),
    (errors.ConflictError, 409, "conflict", "Conflict"),
    (errors.RuleViolationError, 409, "rule_violation", "Rule violation"),
    (errors.GateRequiredError, 409, "gate_required", "Gate required"),
    (errors.IllegalTransitionError, 409, "illegal_transition", "Illegal transition"),
    (errors.UnauthorizedError, 401, "unauthorized", "Unauthorized"),
    (errors.ForbiddenError, 403, "forbidden", "Forbidden"),
    (errors.BudgetExhaustedError, 429, "budget_exceeded", "Budget exceeded"),
    (errors.GuardRejectedError, 422, "guard_rejected", "Guard rejected"),
    (errors.ChainBrokenError, 500, "chain_broken", "Audit chain broken"),
    (
        errors.UpstreamUnavailableError,
        503,
        "upstream_unavailable",
        "Upstream unavailable",
    ),
    (
        errors.AuthzUnavailableError,
        503,
        "authz_unavailable",
        "Authorization unavailable",
    ),
    (errors.UpstreamTimeoutError, 504, "upstream_timeout", "Upstream timeout"),
]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str


def make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/raise/{index}")
    async def raise_error(index: int) -> None:
        cls = CASES[index][0]
        raise cls(
            "specific detail", errors=[{"loc": "body.x", "msg": "bad"}], rule_id="R6"
        )

    @app.post("/items")
    async def create(body: Body) -> dict[str, str]:
        return {"reason": body.reason}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals: password=hunter2")

    @app.get("/dup")
    async def dup() -> None:
        raise DuplicateKeyError("E11000 duplicate key")

    @app.get("/mongo-down")
    async def mongo_down() -> None:
        raise ServerSelectionTimeoutError("no servers")

    return app


client = TestClient(make_app(), raise_server_exceptions=False)


@pytest.mark.parametrize(("index", "case"), list(enumerate(CASES)))
def test_install_error_handlers_maps_each_class_to_problem(index, case):
    _, status, code, title = case
    response = client.get(f"/raise/{index}")
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    problem = Problem.model_validate(response.json())
    assert problem.type == f"https://trustops.local/errors/{code}"
    assert problem.title == title
    assert problem.status == status
    assert problem.detail == "specific detail"
    assert problem.instance == f"/raise/{index}"
    assert problem.errors == [{"loc": "body.x", "msg": "bad"}]


def test_unauthorized_problem_carries_www_authenticate():
    index = next(i for i, c in enumerate(CASES) if c[0] is errors.UnauthorizedError)
    response = client.get(f"/raise/{index}")
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_error_defaults_detail_to_title_and_keeps_extra():
    err = errors.RuleViolationError(rule_id="R6")
    assert err.detail == "Rule violation"
    assert err.extra == {"rule_id": "R6"}
    assert err.type_uri.endswith("/rule_violation")
    assert isinstance(err, errors.ConflictError)


def test_request_validation_maps_to_422_problem_without_input_echo():
    response = client.post("/items", json={"reason": 1, "password": "hunter2"})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"].endswith("/validation_failed")
    locs = {e["loc"] for e in body["errors"]}
    assert "body.reason" in locs
    assert "hunter2" not in response.text


def test_unknown_route_maps_to_404_problem():
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"].endswith("/not_found")


def test_method_not_allowed_maps_to_405_problem():
    response = client.delete("/items")
    assert response.status_code == 405
    assert response.json()["type"].endswith("/method_not_allowed")


def test_unexpected_error_is_500_problem_without_internals():
    response = client.get("/boom")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
    assert response.json()["type"].endswith("/internal_error")


def test_duplicate_key_maps_to_409_problem():
    response = client.get("/dup")
    assert response.status_code == 409
    assert response.json()["type"].endswith("/conflict")


def test_mongo_unavailable_maps_to_503_problem():
    response = client.get("/mongo-down")
    assert response.status_code == 503
    assert response.json()["type"].endswith("/upstream_unavailable")
    assert response.headers["retry-after"] == "5"

"""The PO surface's third fence (rule 3): ``po_guard.PoGuardedRoute`` re-scans
every PO response and turns a restricted key or term into a 500
``rule_violation:R3``, logs it and counts it.

Two angles: a tiny app with handlers that leak, which shows the mechanism and
the metric; and the real S1 app with its own service patched to leak, which
shows the fence is on the real router.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Optional

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from fcrm_case import po_guard
from fcrm_case.po_guard import PoGuardedRoute
from fcrm_case.po_service import PoService
from fcrm_case.routes_po import router as po_router
from fcrm_common.auth import current_user
from fcrm_common.errors import ValidationError, install_error_handlers
from fcrm_contracts.api.case_po import CasePO
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from starlette.requests import Request
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"


class Recorder:
    """A counter that remembers what was added to it."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, str]]] = []

    def add(self, amount: int, attributes: Optional[dict[str, str]] = None) -> None:
        self.calls.append((amount, dict(attributes or {})))


@pytest.fixture
def counter(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(po_guard, "violation_counter", lambda: recorder)
    return recorder


@pytest.fixture
def leaky(counter: Recorder) -> TestClient:
    """An app whose PO-guarded routes return what a buggy handler might."""
    router = APIRouter(route_class=PoGuardedRoute)
    caller: dict[str, Principal] = {}

    @router.get("/cases/{case_id}/clean")
    async def clean(case_id: str) -> dict[str, Any]:
        return {
            "id": case_id,
            "next_action": "Review & submit",
            "completion": {"pct": 83},
        }

    @router.get("/cases/{case_id}/leak-key")
    async def leak_key(case_id: str) -> dict[str, Any]:
        return {"id": case_id, "score": 9.8}

    @router.get("/cases/{case_id}/leak-nested")
    async def leak_nested(case_id: str) -> dict[str, Any]:
        return {"rows": [{"detail": {"composite_value": 10.4}}]}

    @router.get("/cases/{case_id}/leak-term")
    async def leak_term(case_id: str) -> dict[str, Any]:
        return {"next_action": "Band 3 committee review"}

    @router.get("/cases/{case_id}/refusal")
    async def refusal(case_id: str) -> Any:
        raise ValidationError("The title contains a word the system reserves (band).")

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: caller["principal"]
    app.state.caller = caller
    return TestClient(app)


def act(client: TestClient, role: Role) -> None:
    client.app.state.caller["principal"] = Principal(  # type: ignore[attr-defined]
        sub="u-pnair", roles=[role], name="Priya", typ="access"
    )


def test_a_clean_response_passes_through_untouched(
    leaky: TestClient, counter: Recorder
) -> None:
    act(leaky, Role.PO)
    # No principal on the request state (the dependency override does not set
    # it): the guard scans anyway.
    response = leaky.get(f"/cases/{CASE}/clean")
    assert response.status_code == 200
    assert response.json()["completion"] == {"pct": 83}
    assert counter.calls == []


@pytest.mark.parametrize("path", ["leak-key", "leak-nested", "leak-term"])
def test_a_leaking_handler_becomes_a_500_rule_violation_and_is_counted(
    leaky: TestClient,
    counter: Recorder,
    caplog: pytest.LogCaptureFixture,
    path: str,
) -> None:
    act(leaky, Role.PO)
    with caplog.at_level(logging.ERROR, logger="fcrm_case.po_guard"):
        response = leaky.get(f"/cases/{CASE}/{path}")
    assert response.status_code == 500
    body = response.json()
    assert body["type"].endswith("/rule_violation")
    assert body["detail"].startswith("rule_violation:R3")
    # Nothing of the original body is sent.
    for leaked in ("9.8", "10.4", "Band 3", "composite", "score"):
        assert leaked not in response.text
    assert counter.calls == [(1, {"rule": "R3", "route": "/cases/{case_id}/" + path})]
    record = next(r for r in caplog.records if r.name == "fcrm_case.po_guard")
    assert record.case_id == CASE  # type: ignore[attr-defined]
    assert record.route == "/cases/{case_id}/" + path  # type: ignore[attr-defined]


def test_an_error_body_is_not_scanned(leaky: TestClient, counter: Recorder) -> None:
    """A refusal that names the reserved word it refused must stay readable."""
    act(leaky, Role.PO)
    response = leaky.get(f"/cases/{CASE}/refusal")
    assert response.status_code == 422
    assert "band" in response.json()["detail"]
    assert counter.calls == []


def test_a_staff_caller_is_not_this_guard_s_concern(counter: Recorder) -> None:
    """The feed route is shared with the analyst, who may see a score. The guard
    reads the principal ``current_user`` stored on the request.
    """
    request = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    request.state.principal = Principal(
        sub="u-riyer", roles=[Role.ANALYST], name="Rahul", typ="access"
    )
    response = JSONResponse({"score": 9.8})
    assert po_guard.guard_response(request, response) is response
    request.state.principal = Principal(
        sub="u-pnair", roles=[Role.PO], name="Priya", typ="access"
    )
    assert po_guard.guard_response(request, response).status_code == 500
    assert counter.calls == [(1, {"rule": "R3", "route": "/x"})]


def test_the_metric_is_a_real_counter_with_the_right_name() -> None:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("fcrm")
    po_guard.build_counter(meter).add(1, {"rule": "R3", "route": "/x"})
    data = reader.get_metrics_data()
    names = {
        metric.name
        for rm in data.resource_metrics  # type: ignore[union-attr]
        for sm in rm.scope_metrics
        for metric in sm.metrics
    }
    assert names == {"fcrm_rule_violations_total"}


def test_every_route_of_the_po_router_is_guarded() -> None:
    routes = [r for r in po_router.routes if hasattr(r, "methods")]
    assert len(routes) >= 18
    assert all(isinstance(r, PoGuardedRoute) for r in routes)


# -- on the real router ------------------------------------------------------------


@pytest.fixture
def real(
    seeded_service: CaseService, act_as: Callable[[Role, str], None]
) -> CaseService:
    act_as(Role.PO, "u-pnair")
    return seeded_service


def test_the_real_router_withholds_a_leaking_case_view(
    real: CaseService, counter: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def leaking(self: PoService, principal: Principal, case_id: str) -> CasePO:
        view = await original(self, principal, case_id)
        return view.model_copy(update={"next_action": "Band 3: committee review"})

    original = PoService.get_case
    monkeypatch.setattr(PoService, "get_case", leaking)
    response = real.client.get(f"/cases/{CASE}")
    assert response.status_code == 500
    assert response.json()["detail"].startswith("rule_violation:R3")
    assert counter.calls[0][1]["route"] == "/cases/{case_id}"


def test_the_real_router_serves_the_same_view_when_it_is_clean(
    real: CaseService, counter: Recorder
) -> None:
    response = real.client.get(f"/cases/{CASE}")
    assert response.status_code == 200
    assert counter.calls == []

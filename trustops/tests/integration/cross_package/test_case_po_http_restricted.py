"""BB-02's HTTP half, now that S1 has routes: drive the real ``fcrm_case``
app as a PO across every PO-reachable endpoint for a demo-shaped case and
run the real rule-3 scanner (``fcrm_guardrails.rules.r3_po_no_restricted``)
over every response body and every SSE ``data:`` frame.

``tests/integration/blackbox/test_bb_02_po_no_restricted_fields.py`` proves
the *contract* models are clean; this proves what the route layer actually
puts on the wire. The analyst/internal bodies are scanned too, as a
sanity check that the scanner is not passing vacuously: they *must* be
flagged, because their shapes carry ``score``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_case.routes import router
from fcrm_case.settings import CaseSettings
from fcrm_case.sse import EventHub
from fcrm_case.store import InMemoryCaseStore
from fcrm_common.auth import current_user
from fcrm_common.errors import install_error_handlers
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from fcrm_guardrails.rules.r3_po_no_restricted import check

pytestmark = pytest.mark.integration

PO = "u-priya"


@pytest.fixture
def principal_box() -> dict[str, Principal]:
    return {}


@pytest.fixture
def client(principal_box: dict[str, Principal]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.state.store = InMemoryCaseStore()
    app.state.settings = CaseSettings(sse_heartbeat_s=0.01, sse_max_lifetime_s=0.03)
    app.state.hub = EventHub()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: principal_box["principal"]
    return TestClient(app)


def _as(box: dict[str, Principal], role: Role, sub: str) -> None:
    box["principal"] = Principal(sub=sub, roles=[role], name=sub, typ="access")


def _assert_clean(label: str, body: Any) -> None:
    result = check(body, Role.PO)
    assert result.ok, f"{label}: {result.detail}"


def test_every_po_response_for_a_demo_shaped_case_passes_rule_3(
    client: TestClient, principal_box: dict[str, Principal], demo: dict[str, Any]
) -> None:
    _as(principal_box, Role.PO, PO)
    created = client.post(
        "/cases",
        json={
            "title": demo["title"],
            "unit": demo["unit"],
            "owner": PO,
            "change_types": demo["change_types"],
        },
    )
    assert created.status_code == 201
    case_id = created.json()["case_id"]
    _assert_clean("POST /cases", created.json())

    _assert_clean("GET /cases", client.get("/cases").json())
    _assert_clean("GET /cases/{id} (empty)", client.get(f"/cases/{case_id}").json())

    for field in demo["fields"]:
        written = client.put(
            f"/cases/{case_id}/fields/{field['id']}",
            json={"value": field["value"], "source": field.get("source")},
        )
        assert written.status_code == 200, written.text
        _assert_clean(f"PUT fields/{field['id']}", written.json())

    renamed = client.put(f"/cases/{case_id}/title", json={"title": demo["title"]})
    _assert_clean("PUT title", renamed.json())

    submitted = client.post(
        f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
    )
    assert submitted.status_code == 200
    _assert_clean("POST submit", submitted.json())

    detail = client.get(f"/cases/{case_id}").json()
    _assert_clean("GET /cases/{id} (submitted)", detail)
    assert detail["submitted_at"] is not None

    # The stream carries what was written to the activity log: a PO-visible
    # event reaches the PO; an analyst-visible one never does.
    log = client.app.state.store.activity  # type: ignore[attr-defined]
    actor = UiActor(id="svc:caseflow", role="fcrm_service", component="H1")
    for visibility, summary in (("po", "Documents read"), ("analyst", "Score ready")):
        asyncio.run(
            log.append(
                case_id,
                UiEventIn(
                    type="document.extracted"
                    if visibility == "po"
                    else "score.computed",
                    visibility=visibility,
                    summary=summary,
                    actor=actor,
                ),
            )
        )
    stream = client.get(f"/cases/{case_id}/stream", headers={"Last-Event-ID": "0"})
    frames = [
        json.loads(line[len("data: ") :])
        for line in stream.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [f["type"] for f in frames] == ["document.extracted"]
    for frame in frames:
        _assert_clean("SSE frame", frame)


def test_the_scanner_is_not_vacuous_the_analyst_and_internal_shapes_are_flagged(
    client: TestClient, principal_box: dict[str, Principal]
) -> None:
    _as(principal_box, Role.PO, PO)
    case_id = client.post(
        "/cases",
        json={"title": "t", "unit": "Payments", "owner": PO, "change_types": []},
    ).json()["case_id"]
    for role in (Role.ANALYST, Role.SERVICE):
        _as(principal_box, role, "u-other")
        body = client.get(f"/cases/{case_id}").json()
        assert "score" in body
        assert not check(body, Role.PO).ok


def test_a_po_is_refused_the_analyst_only_routes(
    client: TestClient, principal_box: dict[str, Principal]
) -> None:
    _as(principal_box, Role.PO, PO)
    case_id = client.post(
        "/cases",
        json={"title": "t", "unit": "Payments", "owner": PO, "change_types": []},
    ).json()["case_id"]
    assert client.get(f"/cases/{case_id}/versions").status_code == 403

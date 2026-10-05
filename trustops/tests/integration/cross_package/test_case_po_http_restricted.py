"""BB-02's HTTP half: drive the real ``fcrm_case`` app as a PO through the demo
case's whole intake (scenes 3-7) and run the real rule-3 scanner
(``fcrm_guardrails.rules.r3_po_no_restricted``) over every response body and
every SSE ``data:`` frame the owner is sent.

``test_bb_02_po_no_restricted_fields.py`` proves the *contract* models are
clean; ``tests/unit/services/case/test_po_routes.py`` scans every route's
response; this proves what the route layer and the stream actually put on the
wire for a case that has been through the whole intake. The scanner is also shown
not to pass vacuously: a body with a score in it is flagged.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.enums import Role
from fcrm_guardrails.rules.r3_po_no_restricted import check

if TYPE_CHECKING:
    from conftest import CaseService

pytestmark = pytest.mark.integration

PO = "u-pnair"
CASE = "FCRM-2026-0151"
READS = (
    "/dashboard",
    "/cases",
    f"/cases/{CASE}",
    f"/cases/{CASE}/documents",
    f"/cases/{CASE}/intake",
    f"/cases/{CASE}/review",
    f"/cases/{CASE}/activity",
    "/integrations",
)


def assert_clean(label: str, body: Any) -> None:
    result = check(body, Role.PO)
    assert result.ok, f"{label}: {result.detail}"


def test_every_po_response_for_the_demo_case_passes_rule_3(
    seeded_service: CaseService,
    act_as: Callable[[Role, str], None],
    fill_and_submit: Callable[[str, str], None],
) -> None:
    fill_and_submit(CASE, PO)
    client = seeded_service.client
    act_as(Role.PO, PO)
    for url in READS:
        response = client.get(url)
        assert response.status_code == 200, f"{url}: {response.text}"
        assert_clean(f"GET {url}", response.json())
    # Page 2 of the queue, and the seeded closed cases the owner also holds.
    queue = client.get("/cases", params={"limit": 5}).json()
    assert queue["next_cursor"] is not None
    assert_clean("GET /cases?limit=5", queue)
    page_two = client.get("/cases", params={"limit": 5, "cursor": queue["next_cursor"]})
    assert_clean("GET /cases page 2", page_two.json())


def test_the_po_stream_carries_only_what_the_po_may_see(
    seeded_service: CaseService,
    act_as: Callable[[Role, str], None],
    fill_and_submit: Callable[[str, str], None],
) -> None:
    fill_and_submit(CASE, PO)
    actor = UiActor(id="svc:caseflow", role="fcrm_service", component="H1")
    log = seeded_service.store.activity
    for type_, visibility, summary in (
        ("document.extracted", "po", "Documents read"),
        ("score.computed", "analyst", "Score ready"),
        ("harness.step", "po", "A step ran"),
    ):
        asyncio.run(
            log.append(
                CASE,
                UiEventIn(
                    type=type_, visibility=visibility, summary=summary, actor=actor
                ),
            )
        )
    act_as(Role.PO, PO)
    stream = seeded_service.client.get(
        f"/cases/{CASE}/stream", headers={"Last-Event-ID": "0"}
    )
    frames = [
        json.loads(line[len("data: ") :])
        for line in stream.text.splitlines()
        if line.startswith("data: ")
    ]
    types = [f["type"] for f in frames]
    assert "document.extracted" in types
    assert "case.submitted" in types  # the owner's own action
    assert "score.computed" not in types
    assert "harness.step" not in types  # a PO-visible row of a blocked type
    for frame in frames:
        assert_clean(f"SSE {frame['type']}", frame)


def test_the_scanner_is_not_vacuous() -> None:
    assert not check({"id": CASE, "score": 9.8}, Role.PO).ok
    assert not check({"next_action": "Band 3 committee review"}, Role.PO).ok


def test_a_po_is_refused_the_internal_routes(
    seeded_service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, PO)
    for url in (
        f"/internal/cases/{CASE}/record",
        f"/internal/cases/{CASE}/items",
        "/internal/analysts/load",
    ):
        assert seeded_service.client.get(url).status_code == 403

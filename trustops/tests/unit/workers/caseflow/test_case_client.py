""":class:`fcrm_caseflow.clients.case_client.CaseClient`: one method per S1
internal route, the exact path and body each sends, the typed answer it
returns, and the failures it raises. S1 is :class:`conftest.FakeCaseService`
behind an ``httpx.MockTransport``, so what these tests assert is what would go
over the wire.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import FakeCaseService, problem
from fcrm_caseflow.clients.case_client import CaseClient
from fcrm_common.errors import NotFoundError, UpstreamUnavailableError
from fcrm_contracts.api.case_internal import (
    AssignAnalystRequest,
    DocumentPatch,
    IntakePush,
    ItemsUpsert,
    ReviewItemUpsert,
    StatePatch,
    UiActor,
    UiEventIn,
)
from fcrm_contracts.enums import CaseState, ItemKind

CASE = "FCRM-2026-0151"


async def test_get_record_reads_the_internal_record_route(
    case_client: CaseClient, case_service: FakeCaseService
) -> None:
    case_service.add_document("doc_1", "business_case.pdf", status="extracted")
    case_service.fields = [
        {"field_id": "F01", "label": "Change owner", "origin": "extracted"}
    ]

    record = await case_client.get_record(CASE)

    assert record.case_id == CASE
    assert record.owner.name == "Priya Nair"
    assert [d.doc_id for d in record.documents] == ["doc_1"]
    assert record.fields[0].field_id == "F01"
    [call] = case_service.calls
    assert (call.method, call.path) == ("GET", f"/internal/cases/{CASE}/record")


async def test_get_record_of_an_unknown_case_is_not_found(
    case_client: CaseClient,
) -> None:
    with pytest.raises(NotFoundError):
        await case_client.get_record("FCRM-2026-9999")


async def test_get_document_reads_one_document(
    case_client: CaseClient, case_service: FakeCaseService
) -> None:
    case_service.add_document("doc_1", "business_case.pdf")
    document = await case_client.get_document("doc_1")
    assert document.status == "stored"
    assert case_service.calls[0].path == "/internal/documents/doc_1"


async def test_patch_document_sends_the_whole_patch_as_a_patch(
    case_client: CaseClient, case_service: FakeCaseService
) -> None:
    case_service.add_document("doc_1", "business_case.pdf")
    patch = DocumentPatch(
        status="extracted",
        canonical_key="canonical/x/doc_1.md",
        anchored_key="canonical/x/doc_1.anchored.md",
        pages=14,
        page_map={"1": (1, 20)},
        guard1={"status": "masked", "pii_total": 3},
    )

    await case_client.patch_document("doc_1", patch)

    [call] = case_service.calls
    assert (call.method, call.path) == ("PATCH", "/internal/documents/doc_1")
    assert call.body["status"] == "extracted"
    assert call.body["page_map"] == {"1": [1, 20]}
    assert call.body["guard1"] == {"status": "masked", "pii_total": 3}


async def test_put_intake_is_an_idempotent_put_of_the_merged_proposals(
    case_client: CaseClient, case_service: FakeCaseService
) -> None:
    push = IntakePush(run_id="run_1", candidates_found=15)
    await case_client.put_intake(CASE, push)
    await case_client.put_intake(CASE, push)
    puts = case_service.calls_to("PUT", "/intake")
    assert [c.path for c in puts] == [f"/internal/cases/{CASE}/intake"] * 2
    assert puts[0].body == puts[1].body
    assert puts[0].body["candidates_found"] == 15


async def test_put_items_returns_the_upsert_result(
    case_service: FakeCaseService,
) -> None:
    case_service.fail(
        "PUT",
        "/items",
        httpx.Response(200, json={"upserted": 1, "item_ids": ["itm_1"]}),
    )
    client = CaseClient(case_service.client("case"))
    item = ReviewItemUpsert(item_id="itm_1", kind=ItemKind.CLAUSE)

    result = await client.put_items(CASE, ItemsUpsert(run_id="run_1", items=[item]))

    assert (result.upserted, result.item_ids) == (1, ["itm_1"])
    [call] = case_service.calls
    assert (call.method, call.path) == ("PUT", f"/internal/cases/{CASE}/items")
    assert call.body["items"][0]["item_id"] == "itm_1"


async def test_get_items_filters_by_kind_and_ignores_fields_it_does_not_model(
    case_service: FakeCaseService,
) -> None:
    row = {"item_id": "itm_1", "kind": "gap", "status": "proposed", "decision": "x"}
    case_service.fail("GET", "/items", httpx.Response(200, json={"items": [row]}))
    client = CaseClient(case_service.client("case"))

    [item] = await client.get_items(CASE, ItemKind.GAP)

    assert item.item_id == "itm_1"
    assert case_service.calls[0].params == {"kind": "gap"}


async def test_get_items_accepts_a_bare_list(case_service: FakeCaseService) -> None:
    row = {"item_id": "itm_1", "kind": "category"}
    case_service.fail("GET", "/items", httpx.Response(200, json=[row]))
    client = CaseClient(case_service.client("case"))
    assert [i.item_id for i in await client.get_items(CASE)] == ["itm_1"]


async def test_get_items_with_an_unexpected_body_is_an_upstream_failure(
    case_service: FakeCaseService,
) -> None:
    case_service.fail("GET", "/items", httpx.Response(200, json={"oops": 1}))
    client = CaseClient(case_service.client("case"))
    with pytest.raises(UpstreamUnavailableError):
        await client.get_items(CASE)


async def test_patch_state_sends_only_what_is_set(
    case_client: CaseClient, case_service: FakeCaseService
) -> None:
    await case_client.patch_state(
        CASE, StatePatch(stage=CaseState.SCREENING, run_id="run_1")
    )
    [call] = case_service.calls
    assert (call.method, call.path) == ("PATCH", f"/internal/cases/{CASE}/state")
    assert call.body == {"stage": "screening", "run_id": "run_1"}


async def test_assign_posts_the_r5_result_with_its_idempotency_key(
    case_client: CaseClient, case_service: FakeCaseService
) -> None:
    request = AssignAnalystRequest(
        analyst_id="u-riyer", reason="R5 lowest load", open_counts={"u-riyer": 4}
    )

    await case_client.assign(CASE, request, idempotency_key="idem_assign_1")

    [call] = case_service.calls
    assert (call.method, call.path) == ("POST", f"/internal/cases/{CASE}/assign")
    assert call.body == {
        "analyst_id": "u-riyer",
        "reason": "R5 lowest load",
        "open_counts": {"u-riyer": 4},
    }
    assert call.headers["idempotency-key"] == "idem_assign_1"


async def test_post_event_carries_the_visibility_and_the_idempotency_key(
    case_client: CaseClient, case_service: FakeCaseService
) -> None:
    event = UiEventIn(
        type="document.extracted",
        visibility="po",
        summary="business_case.pdf was read (14 pages)",
        detail={"doc_id": "doc_1"},
        actor=UiActor(id="H1", role="system", component="H1"),
        record_seq=5,
    )

    await case_client.post_event(CASE, event, idempotency_key="idem_ui_1")

    [call] = case_service.calls
    assert (call.method, call.path) == ("POST", f"/internal/cases/{CASE}/events")
    assert call.body["visibility"] == "po"
    assert call.body["record_seq"] == 5
    assert call.headers["idempotency-key"] == "idem_ui_1"


async def test_analyst_load_reads_the_open_counts(case_client: CaseClient) -> None:
    load = await case_client.analyst_load()
    assert {a.analyst_id: a.open_case_count for a in load.loads} == {
        "u-dbrooks": 6,
        "u-mshah": 5,
        "u-riyer": 4,
    }


async def test_a_problem_response_becomes_the_matching_platform_error(
    case_client: CaseClient, case_service: FakeCaseService
) -> None:
    case_service.fail("PATCH", "/state", problem(409, "case is closed"))
    from fcrm_common.errors import ConflictError

    with pytest.raises(ConflictError, match="case is closed"):
        await case_client.patch_state(CASE, StatePatch(stage=CaseState.INTAKE))


async def test_a_body_that_breaks_the_contract_is_an_upstream_failure(
    case_service: FakeCaseService,
) -> None:
    case_service.fail("GET", "/record", httpx.Response(200, json={"case_id": CASE}))
    client = CaseClient(case_service.client("case"))
    with pytest.raises(UpstreamUnavailableError, match="unexpected body"):
        await client.get_record(CASE)


async def test_a_5xx_is_retried_for_an_idempotent_put(
    case_service: FakeCaseService,
) -> None:
    case_service.fail("PUT", "/intake", problem(503))
    client = CaseClient(case_service.client("case"))

    await client.put_intake(CASE, IntakePush(run_id="run_1", candidates_found=1))

    assert len(case_service.calls_to("PUT", "/intake")) == 2


async def test_ids_are_escaped_as_one_path_segment(
    case_client: CaseClient, case_service: FakeCaseService
) -> None:
    with pytest.raises(NotFoundError):
        await case_client.get_document("../etc/passwd")
    assert case_service.calls[0].path.count("/") == 3  # /internal/documents/<one>


async def test_put_harness_sends_the_snapshot_as_a_put_and_returns_the_count(
    case_client: CaseClient, case_service: FakeCaseService
) -> None:
    from fcrm_contracts.api.case_analyst_read import HarnessPush

    push = HarnessPush.model_validate(
        {
            "run_id": "run_1",
            "snapshot": {
                "case_id": CASE,
                "trace_id": "t1",
                "status": "idle",
                "core": "assessment core",
                "counters": {
                    "agents_run": 1,
                    "agents_total": 9,
                    "guards_passed": 0,
                    "guards_blocked": 0,
                    "audit_records": 2,
                    "chain_verified": True,
                    "tokens_used": 10,
                    "tokens_budget": 120000,
                },
                "guards": [],
                "log": [
                    {
                        "seq": 1,
                        "at": "2026-10-05T12:00:00Z",
                        "who": "H1 core",
                        "text": "started",
                        "sub": None,
                        "level": "info",
                        "guard": None,
                        "progress": None,
                    }
                ],
                "trace_url": "http://localhost:5080/web/traces?trace_id=t1",
                "tokens_note": "n",
            },
        }
    )
    result = await case_client.put_harness(CASE, push)
    assert (result.case_id, result.lines) == (CASE, 1)
    [call] = case_service.calls
    assert (call.method, call.path) == ("PUT", f"/internal/cases/{CASE}/harness")
    assert case_service.harness_pushes[0]["snapshot"]["trace_id"] == "t1"

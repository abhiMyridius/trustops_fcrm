"""S1's internal routes (``/internal/*``), driven over HTTP against in-memory
stores: the service account only, every write idempotent, the PO-visible rows
carrying names and stage names and nothing of the harness.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import DocumentRow, FieldSource
from fcrm_contracts.api.case_internal import (
    AnalystLoadResponse,
    AssignResult,
    CaseRecordInternal,
    DocumentInternal,
    IntakePush,
    IntakePushResult,
    ItemsUpsertResult,
    ReviewItemInternal,
    StateResult,
    UiEventResult,
)
from fcrm_contracts.enums import CaseState, Role
from fcrm_contracts.events.payloads import CaseCreated

if TYPE_CHECKING:
    from conftest import CaseService

SERVICE_SUB = "svc:caseflow"
CASE = "FCRM-2026-0151"
SPAN = {"kind": "doc", "doc_id": "doc_a", "page": 1, "line_start": 2, "line_end": 3}


@pytest.fixture
def service(
    case_service: CaseService,
    act_as: Callable[[Role, str], None],
    stored_case: CaseRecord,
) -> CaseService:
    """The S1 app with the demo case stored, called as H1's service account."""
    act_as(Role.SERVICE, SERVICE_SUB)
    return case_service


def intake_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "run_id": "run_1",
        "candidates_found": 15,
        "required_fields": 18,
        "values": [
            {
                "field_id": "F03",
                "label": "Go-live date",
                "value": "Q1 2027",
                "source": SPAN,
                "quote": "go-live Q1 2027",
                "confidence": 0.9,
            }
        ],
        "categories": [
            {
                "change_type": "vendor_onboarding",
                "label": "Vendor onboarding",
                "source": SPAN,
                "quote": "payout partner",
                "confidence": 0.94,
                "gate_id": "gate_cat_1",
            }
        ],
        "gaps": [
            {
                "question_id": "q1",
                "field_id": "F31",
                "label": "Limits",
                "text": "What are the limits?",
                "blocking": True,
                "gate_id": "gate_gap_1",
            }
        ],
        "flags": [{"field_id": "F21", "text": "cash pickup", "kind": "new_fact"}],
    }
    return {**body, **overrides}


# ── who may call ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.ANALYST, Role.COMMITTEE, Role.OBSERVER])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", f"/internal/cases/{CASE}/record"),
        ("get", f"/internal/cases/{CASE}/items"),
        ("put", f"/internal/cases/{CASE}/items"),
        ("put", f"/internal/cases/{CASE}/intake"),
        ("patch", f"/internal/cases/{CASE}/state"),
        ("post", f"/internal/cases/{CASE}/assign"),
        ("post", f"/internal/cases/{CASE}/events"),
        ("get", "/internal/documents/doc_a"),
        ("patch", "/internal/documents/doc_a"),
        ("get", "/internal/analysts/load"),
    ],
)
def test_no_person_role_may_call_an_internal_route(
    case_service: CaseService,
    act_as: Callable[[Role, str], None],
    role: Role,
    method: str,
    path: str,
) -> None:
    act_as(role, "u-someone")
    response = case_service.client.request(method, path, json={})
    assert response.status_code == 403


def test_internal_routes_are_not_in_the_public_openapi_document(
    case_service: CaseService,
) -> None:
    assert not [
        p for p in case_service.client.app.openapi()["paths"] if "internal" in p
    ]  # type: ignore[attr-defined]


# ── the record ──────────────────────────────────────────────────────────────


def test_the_demo_case_record_is_empty_but_for_its_open_required_fields(
    service: CaseService,
) -> None:
    response = service.client.get(f"/internal/cases/{CASE}/record")
    assert response.status_code == 200
    record = CaseRecordInternal.model_validate(response.json())
    assert record.case_id == CASE
    assert record.state == CaseState.INTAKE
    assert (record.owner.id, record.owner.name) == ("u-pnair", "Priya Nair")
    assert record.analyst is None
    assert record.change_types == []
    assert record.documents == []
    assert record.run_id is None
    assert record.pins.fieldset_v == record.fieldset_v == 6
    assert len(record.required_field_ids) == 6
    assert [f.field_id for f in record.fields] == record.required_field_ids
    assert {f.origin for f in record.fields} == {"open"}
    assert all(f.value is None for f in record.fields)


def test_the_record_carries_values_with_origins_documents_and_originals(
    service: CaseService,
) -> None:
    async def arrange() -> None:
        await service.views.apply_intake_push(
            CASE,
            _push_model(
                values=[
                    {**intake_body()["values"][0]},
                    {
                        "field_id": "F05",
                        "label": "Expected annual volume",
                        "value": "$48M",
                        "source": SPAN,
                        "quote": "48M",
                        "confidence": 0.8,
                    },
                ]
            ),
        )
        await service.views.write_field_value(
            CASE,
            "F05",
            label="Expected annual volume",
            value="$52M",
            origin="owner_declared",
            source=FieldSource(label="PO"),
            by="u-pnair",
        )
        await service.views.insert_documents(
            [
                DocumentRow(
                    doc_id="doc_a",
                    case_id=CASE,
                    name="case.pdf",
                    status="extracted",
                    bucket="uploads",
                    key="k",
                    size_bytes=10,
                    content_type="application/pdf",
                    uploaded_at=service.store._cases[CASE].created_at,
                    pages=14,
                    anchored_key="canonical/doc_a.anchored.md",
                )
            ]
        )

    asyncio.run(arrange())
    record = CaseRecordInternal.model_validate(
        service.client.get(f"/internal/cases/{CASE}/record").json()
    )
    by_id = {f.field_id: f for f in record.fields}
    assert by_id["F03"].origin == "extracted"
    assert by_id["F03"].source is not None
    assert (by_id["F03"].source.doc_id, by_id["F03"].source.page) == ("doc_a", 1)
    assert by_id["F05"].origin == "owner_declared"
    assert by_id["F05"].value == "$52M"
    assert by_id["F05"].original_value == "$48M"
    assert by_id["F01"].origin == "open"
    assert [d.doc_id for d in record.documents] == ["doc_a"]
    assert record.documents[0].pages == 14


def test_the_record_of_an_unknown_case_is_404(service: CaseService) -> None:
    assert (
        service.client.get("/internal/cases/FCRM-2026-9999/record").status_code == 404
    )


# ── items ───────────────────────────────────────────────────────────────────


def items_body(*ids: str, run_id: str = "run_1") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "items": [
            {
                "item_id": item_id,
                "kind": "clause",
                "proposal_id": f"prp_{item_id}",
                "gate_id": f"gate_{item_id}",
                "subject": {"clause_id": item_id},
                "proposed": {"quote": "text"},
                "source_refs": [
                    {"kind": "clause", "clause_id": item_id, "corpus_v": 14}
                ],
            }
            for item_id in ids
        ],
    }


def test_items_are_upserted_in_one_call_and_read_back_by_kind(
    service: CaseService,
) -> None:
    put = service.client.put(f"/internal/cases/{CASE}/items", json=items_body("a", "b"))
    assert put.status_code == 200
    result = ItemsUpsertResult.model_validate(put.json())
    assert (result.upserted, result.item_ids) == (2, ["a", "b"])

    listed = service.client.get(
        f"/internal/cases/{CASE}/items", params={"kind": "clause"}
    )
    items = [ReviewItemInternal.model_validate(i) for i in listed.json()]
    assert [i.item_id for i in items] == ["a", "b"]
    assert items[0].gate_id == "gate_a"
    assert items[0].source_refs == [
        {"kind": "clause", "clause_id": "a", "corpus_v": 14}
    ]
    assert items[0].decided is None
    assert (
        service.client.get(
            f"/internal/cases/{CASE}/items", params={"kind": "gap"}
        ).json()
        == []
    )


def test_replaying_an_items_push_is_the_same_state(service: CaseService) -> None:
    body = items_body("a", "b")
    service.client.put(f"/internal/cases/{CASE}/items", json=body)
    first = service.client.get(f"/internal/cases/{CASE}/items").json()
    service.client.put(f"/internal/cases/{CASE}/items", json=body)
    assert service.client.get(f"/internal/cases/{CASE}/items").json() == first


def test_a_proposal_id_used_twice_is_a_409_and_an_unknown_case_a_404(
    service: CaseService,
) -> None:
    service.client.put(f"/internal/cases/{CASE}/items", json=items_body("a"))
    clash = items_body("z")
    clash["items"][0]["proposal_id"] = "prp_a"
    assert (
        service.client.put(f"/internal/cases/{CASE}/items", json=clash).status_code
        == 409
    )
    assert (
        service.client.put(
            "/internal/cases/FCRM-2026-9999/items", json=items_body("a")
        ).status_code
        == 404
    )
    assert service.client.get("/internal/cases/FCRM-2026-9999/items").status_code == 404


def test_an_items_body_with_an_unknown_field_is_a_422(service: CaseService) -> None:
    body = items_body("a")
    body["items"][0]["surprise"] = 1
    assert (
        service.client.put(f"/internal/cases/{CASE}/items", json=body).status_code
        == 422
    )


# ── the intake push ─────────────────────────────────────────────────────────


def _push_model(**overrides: Any) -> IntakePush:
    return IntakePush.model_validate(intake_body(**overrides))


def test_the_intake_push_materialises_and_reports_what_it_did(
    service: CaseService,
) -> None:
    response = service.client.put(f"/internal/cases/{CASE}/intake", json=intake_body())
    assert response.status_code == 200
    result = IntakePushResult.model_validate(response.json())
    assert (result.values_written, result.values_kept) == (1, 0)
    assert (result.categories, result.gaps) == (1, 1)
    record = CaseRecordInternal.model_validate(
        service.client.get(f"/internal/cases/{CASE}/record").json()
    )
    assert next(f for f in record.fields if f.field_id == "F03").origin == "extracted"
    kinds = {
        i["kind"] for i in service.client.get(f"/internal/cases/{CASE}/items").json()
    }
    assert kinds == {"category", "gap"}


def test_the_intake_push_never_overwrites_a_value_the_po_declared(
    service: CaseService,
) -> None:
    asyncio.run(
        service.views.write_field_value(
            CASE,
            "F03",
            label="Go-live date",
            value="Q3 2027",
            origin="owner_declared",
            source=None,
            by="u-pnair",
        )
    )
    result = IntakePushResult.model_validate(
        service.client.put(f"/internal/cases/{CASE}/intake", json=intake_body()).json()
    )
    assert (result.values_written, result.values_kept) == (0, 1)
    record = CaseRecordInternal.model_validate(
        service.client.get(f"/internal/cases/{CASE}/record").json()
    )
    field = next(f for f in record.fields if f.field_id == "F03")
    assert (field.value, field.origin) == ("Q3 2027", "owner_declared")


def test_the_same_intake_push_twice_leaves_the_same_state(service: CaseService) -> None:
    service.client.put(f"/internal/cases/{CASE}/intake", json=intake_body())
    state = (
        service.client.get(f"/internal/cases/{CASE}/record").json(),
        service.client.get(f"/internal/cases/{CASE}/items").json(),
    )
    service.client.put(f"/internal/cases/{CASE}/intake", json=intake_body())
    assert state == (
        service.client.get(f"/internal/cases/{CASE}/record").json(),
        service.client.get(f"/internal/cases/{CASE}/items").json(),
    )


def test_an_intake_push_to_an_unknown_case_is_404_and_a_bad_body_422(
    service: CaseService,
) -> None:
    assert (
        service.client.put(
            "/internal/cases/FCRM-2026-9999/intake", json=intake_body()
        ).status_code
        == 404
    )
    bad = intake_body()
    bad["values"][0]["confidence"] = 2
    assert (
        service.client.put(f"/internal/cases/{CASE}/intake", json=bad).status_code
        == 422
    )


# ── state ───────────────────────────────────────────────────────────────────


def test_a_state_patch_moves_the_case_publishes_the_change_and_replays_quietly(
    service: CaseService,
) -> None:
    with service.hub.subscription(CASE) as sub:
        response = service.client.patch(
            f"/internal/cases/{CASE}/state",
            json={"stage": "screening", "run_id": "run_1", "round_open": False},
        )
        assert response.status_code == 200
        result = StateResult.model_validate(response.json())
        assert (result.stage, result.run_id, result.stage_changed) == (
            CaseState.SCREENING,
            "run_1",
            True,
        )
        entry = sub.queue.get_nowait()
        assert (entry.type, entry.visibility, entry.seq) == (
            "case.stage_changed",
            "all",
            1,
        )
        assert "run_1" not in entry.model_dump_json()

        again = StateResult.model_validate(
            service.client.patch(
                f"/internal/cases/{CASE}/state",
                json={"stage": "screening", "run_id": "run_1", "round_open": False},
            ).json()
        )
        assert again.stage_changed is False
        assert sub.queue.empty()
    record = service.store._cases[CASE]
    assert (record.state, record.run_id, record.stage_flags.round_open) == (
        CaseState.SCREENING,
        "run_1",
        False,
    )


def test_a_state_patch_to_an_unknown_case_is_404_and_an_unknown_stage_422(
    service: CaseService,
) -> None:
    assert (
        service.client.patch(
            "/internal/cases/FCRM-2026-9999/state", json={"stage": "triage"}
        ).status_code
        == 404
    )
    assert (
        service.client.patch(
            f"/internal/cases/{CASE}/state", json={"stage": "nonsense"}
        ).status_code
        == 422
    )


# ── assign ──────────────────────────────────────────────────────────────────


def test_assigning_sets_the_analyst_by_name_and_tells_the_po_the_name_only(
    service: CaseService,
) -> None:
    with service.hub.subscription(CASE) as sub:
        response = service.client.post(
            f"/internal/cases/{CASE}/assign",
            json={
                "analyst_id": "u-riyer",
                "reason": "R5 lowest load",
                "open_counts": {"u-riyer": 4, "u-mshah": 5, "u-dbrooks": 5},
            },
        )
        assert response.status_code == 200
        result = AssignResult.model_validate(response.json())
        assert (result.analyst_name, result.changed) == ("Rahul Iyer", True)
        entry = sub.queue.get_nowait()
    assert (entry.type, entry.visibility) == ("case.assigned", "po")
    assert entry.detail == {"analyst_name": "Rahul Iyer"}
    assert "u-riyer" not in entry.model_dump_json().replace('"id":"svc:caseflow"', "")
    assert service.store._cases[CASE].analyst_id == "u-riyer"

    replay = AssignResult.model_validate(
        service.client.post(
            f"/internal/cases/{CASE}/assign", json={"analyst_id": "u-riyer"}
        ).json()
    )
    assert replay.changed is False
    assert sub.queue.empty()


def test_only_an_analyst_on_the_roster_can_be_assigned(service: CaseService) -> None:
    assert (
        service.client.post(
            f"/internal/cases/{CASE}/assign", json={"analyst_id": "u-pnair"}
        ).status_code
        == 422
    )
    assert (
        service.client.post(
            "/internal/cases/FCRM-2026-9999/assign", json={"analyst_id": "u-riyer"}
        ).status_code
        == 404
    )
    assert service.store._cases[CASE].analyst_id is None


def test_the_po_stream_shows_the_assignment_by_name_and_no_stage_internals(
    service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    service.client.patch(
        f"/internal/cases/{CASE}/state", json={"stage": "triage", "run_id": "run_1"}
    )
    service.client.post(
        f"/internal/cases/{CASE}/assign", json={"analyst_id": "u-riyer"}
    )
    service.client.post(
        f"/internal/cases/{CASE}/events",
        json={
            "type": "score.computed",
            "visibility": "analyst",
            "summary": "Composite 9.8, band 2",
            "actor": {"id": "svc:caseflow", "role": "fcrm_service", "component": "H1"},
        },
    )
    act_as(Role.PO, "u-pnair")
    body = service.client.get(
        f"/cases/{CASE}/stream", headers={"Last-Event-ID": "0"}
    ).text
    assert "case.stage_changed" in body
    assert "case.assigned" in body
    assert "Rahul Iyer" in body
    for forbidden in ("score.computed", "9.8", "band", "run_1", "caseflow", "u-riyer"):
        assert forbidden not in body, forbidden
    act_as(Role.ANALYST, "u-riyer")
    staff = service.client.get(
        f"/cases/{CASE}/stream", headers={"Last-Event-ID": "0"}
    ).text
    assert "score.computed" in staff


# ── events ──────────────────────────────────────────────────────────────────


def event_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "document.extracted",
        "visibility": "po",
        "summary": "Documents read",
        "detail": {"count": 2},
        "actor": {"id": "svc:caseflow", "role": "fcrm_service", "component": "H1"},
        "record_seq": 5,
    }
    return {**body, **overrides}


def test_an_event_is_stored_published_and_answers_with_its_seq(
    service: CaseService,
) -> None:
    with service.hub.subscription(CASE) as sub:
        response = service.client.post(
            f"/internal/cases/{CASE}/events", json=event_body()
        )
        assert response.status_code == 201
        assert UiEventResult.model_validate(response.json()).seq == 1
        assert sub.queue.get_nowait().record_seq == 5
    second = service.client.post(f"/internal/cases/{CASE}/events", json=event_body())
    assert second.json()["seq"] == 2
    assert [e.seq for e in service.store.activity.entries] == [1, 2]


@pytest.mark.parametrize("visibility", ["po", "all"])
@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "score.computed"},
        {"type": "harness.step"},
        {"detail": {"nested": {"band": 3}}},
        {"detail": {"run_id": "run_1"}},
        {"summary": "Composite 9.8, band 2"},
        {"detail": {"note": "scored 9.8"}},
    ],
)
def test_an_event_the_po_must_never_see_cannot_be_written_as_po_visible(
    service: CaseService, visibility: str, overrides: dict[str, Any]
) -> None:
    response = service.client.post(
        f"/internal/cases/{CASE}/events",
        json=event_body(visibility=visibility, **overrides),
    )
    assert response.status_code == 409
    assert "rule_violation" in response.json()["type"]
    assert service.store.activity.entries == []


def test_the_same_events_are_fine_as_analyst_visibility(service: CaseService) -> None:
    for overrides in ({"type": "score.computed"}, {"detail": {"band": 3}}):
        response = service.client.post(
            f"/internal/cases/{CASE}/events",
            json=event_body(visibility="analyst", **overrides),
        )
        assert response.status_code == 201


@pytest.mark.parametrize(
    "bad_type", ["", "NoDots", "Has.Caps", "x" * 70 + ".y", "a..b", "1a.b"]
)
def test_an_event_type_must_be_dotted_lower_case(
    service: CaseService, bad_type: str
) -> None:
    response = service.client.post(
        f"/internal/cases/{CASE}/events", json=event_body(type=bad_type)
    )
    assert response.status_code == 422


def test_an_event_for_an_unknown_case_is_404_and_publishes_nothing(
    service: CaseService,
) -> None:
    with service.hub.subscription("FCRM-2026-9999") as sub:
        response = service.client.post(
            "/internal/cases/FCRM-2026-9999/events", json=event_body()
        )
        assert response.status_code == 404
        assert sub.queue.empty()


# ── documents ───────────────────────────────────────────────────────────────


def test_documents_are_read_and_patched_by_id(service: CaseService) -> None:
    asyncio.run(
        service.views.insert_documents(
            [
                DocumentRow(
                    doc_id="doc_a",
                    case_id=CASE,
                    name="case.pdf",
                    status="stored",
                    bucket="uploads",
                    key=f"uploads/{CASE}/doc_a/case.pdf",
                    size_bytes=10,
                    content_type="application/pdf",
                    uploaded_at=service.store._cases[CASE].created_at,
                )
            ]
        )
    )
    read = DocumentInternal.model_validate(
        service.client.get("/internal/documents/doc_a").json()
    )
    assert (read.status, read.pages, read.case_id) == ("stored", None, CASE)
    patch = {
        "status": "extracted",
        "canonical_key": "canonical/doc_a.md",
        "anchored_key": "canonical/doc_a.anchored.md",
        "pages": 14,
        "page_map": {"1": [1, 40]},
        "guard1": {"clean": True},
    }
    patched = service.client.patch("/internal/documents/doc_a", json=patch)
    assert patched.status_code == 200
    again = service.client.patch("/internal/documents/doc_a", json=patch)
    assert again.json() == patched.json()
    assert (
        DocumentInternal.model_validate(patched.json()).anchored_key
        == "canonical/doc_a.anchored.md"
    )
    assert service.client.get("/internal/documents/missing").status_code == 404
    assert (
        service.client.patch("/internal/documents/missing", json=patch).status_code
        == 404
    )
    assert (
        service.client.patch(
            "/internal/documents/doc_a", json={"status": "weird"}
        ).status_code
        == 422
    )


# ── analyst load ────────────────────────────────────────────────────────────


def test_analyst_load_lists_the_roster_fewest_open_cases_first(
    service: CaseService, make_case_record: Callable[..., CaseRecord]
) -> None:
    for number, (analyst, state) in enumerate(
        [
            ("u-mshah", CaseState.REVIEW),
            ("u-mshah", CaseState.REVIEW),
            ("u-dbrooks", CaseState.REVIEW),
            ("u-riyer", CaseState.CLOSED),
        ],
        start=1,
    ):
        record = make_case_record(
            f"FCRM-2026-{number:04d}", analyst_id=analyst, state=state
        )
        service.store._cases[record.case_id] = record
    response = service.client.get("/internal/analysts/load")
    assert response.status_code == 200
    loads = AnalystLoadResponse.model_validate(response.json()).loads
    assert [(x.analyst_id, x.open_case_count) for x in loads] == [
        ("u-riyer", 0),
        ("u-dbrooks", 1),
        ("u-mshah", 2),
    ]
    assert {"u-riyer", "u-mshah", "u-dbrooks"} == {x.analyst_id for x in loads}


def test_assigning_drops_the_load_cache_so_the_next_triage_sees_the_new_count(
    service: CaseService,
) -> None:
    before = AnalystLoadResponse.model_validate(
        service.client.get("/internal/analysts/load").json()
    )
    assert {x.open_case_count for x in before.loads} == {0}
    service.client.post(
        f"/internal/cases/{CASE}/assign", json={"analyst_id": "u-mshah"}
    )
    after = AnalystLoadResponse.model_validate(
        service.client.get("/internal/analysts/load").json()
    )
    assert {x.analyst_id: x.open_case_count for x in after.loads}["u-mshah"] == 1


# ── what the public create route publishes ─────────────────────────────────


def test_case_created_carries_the_pins_and_validates_as_the_typed_payload(
    service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, "u-pnair")
    created = service.client.post(
        "/cases", json={"title": "Another case", "unit": "Payments"}
    )
    assert created.status_code == 201
    event = service.store.events[-1]
    assert event.type == "case.created"
    payload = CaseCreated.model_validate(event.payload)
    assert payload.actor == "u-pnair"
    assert payload.data.pins is not None
    assert (payload.data.pins.scoring_v, payload.data.pins.fieldset_v) == (3, 6)
    assert payload.data.unit == "Payments"
    record = service.store.records[created.json()["id"]]
    assert record.owner_name == "u-pnair"
    assert payload.data.pins == record.pinned_versions

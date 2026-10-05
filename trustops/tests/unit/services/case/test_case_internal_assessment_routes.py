"""S1's internal assessment routes (``PUT .../screening|score|critic|
completeness|recommendation-draft|handoff``, ``GET .../score-request|evidence``)
driven over HTTP against in-memory stores: the service account only, every write
idempotent by ``Idempotency-Key``, the score request built from review items with
no planned control in it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.views import ReviewItemRow
from fcrm_contracts.api.case_internal_assessment import PreviousScore
from fcrm_contracts.enums import Band, ItemKind, Role

if TYPE_CHECKING:
    from conftest import CaseService
    from fcrm_case.assessment_store import InMemoryAssessmentStore

CASE = "FCRM-2026-0151"
CLAUSE_SPAN = {"kind": "clause", "clause_id": "FFIEC-TPPP-04", "corpus_v": 14}

PUT_ROUTES = ("screening", "score", "critic", "completeness", "recommendation-draft")


def _screening_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "run_id": "run_1",
        "fast_lane_proposed": False,
        "reasons": [
            {
                "text": "Three change types including a new geography",
                "source_ref": CLAUSE_SPAN,
                "quote": "new corridors require jurisdiction screening",
                "confidence": 0.9,
            }
        ],
        "proposal_id": "prp_1",
    }
    return {**body, **overrides}


def _critic_body() -> dict[str, Any]:
    return {
        "run_id": "run_1",
        "findings": [
            {
                "finding_id": "itm_f1",
                "gate_id": "gate_f1",
                "finding_type": "precedent_deviation",
                "severity": "high",
                "record_refs": ["#30"],
                "text": "Vendor due-diligence rating 7.6 is 2.7 above the 4.9 average",
                "source_ref": {"kind": "record", "case_id": CASE, "seq": 30},
                "quote": "7.6",
                "confidence": 0.9,
            },
            {
                "finding_id": "itm_f2",
                "gate_id": "gate_f2",
                "finding_type": "evidence_recency",
                "severity": "medium",
                "record_refs": ["#30"],
                "text": "Only evidence is a 2023 audit",
                "source_ref": {"kind": "record", "case_id": CASE, "seq": 30},
                "quote": "2023",
                "confidence": 0.8,
            },
        ],
    }


def _completeness_body() -> dict[str, Any]:
    return {
        "run_id": "run_1",
        "report": {
            "missing": [],
            "stale": ["Partner audit Nov 2023, domestic scope"],
            "contradictory": ["Cash pickup channel vs business case"],
            "questions_drafted": 3,
            "questions_kept": 2,
        },
        "questions": [
            {
                "question_id": "q1",
                "anchored_to": "CTL-T01",
                "text": "Is a review planned?",
            }
        ],
        "gate_id": "gate_completeness",
    }


def _draft_body(text: str = "Residual risk sits in Band 3.") -> dict[str, Any]:
    return {
        "run_id": "run_1",
        "sections": [
            {
                "key": "recommendation",
                "text": text,
                "source_ref": {"kind": "record", "case_id": CASE, "seq": 32},
                "quote": "Composite 10.4",
                "confidence": 0.9,
            }
        ],
        "conditions": [
            {
                "condition_id": "CND-1",
                "text": "Corridor-scope vendor DD review completed and filed",
                "owner": "Priya Nair",
                "due": "before go-live",
                "status": "open",
                "source": "overridden control CTL-T01",
            }
        ],
        "business_summary": {
            "text": "The change is ready for committee.",
            "restricted_term_hits": 0,
        },
        "score_record_seq": 32,
        "proposal_ids": ["prp_9"],
    }


def _handoff_body() -> dict[str, Any]:
    return {
        "run_id": "run_1",
        "pack_id": "pack_1",
        "pack_version": 1,
        "content_hash": "ab" * 32,
        "checks": {
            "numbers_locked": True,
            "conditions_drafted": True,
            "all_review_items_decided": True,
            "boundary_and_band_acknowledged": True,
        },
        "audit_head_seq": 54,
        "audit_head_hash": "cd" * 32,
        "gate_id": "gate_pack_release",
    }


def _put(service: CaseService, route: str, body: Any, key: str | None = None) -> Any:
    headers = {"Idempotency-Key": key} if key else {}
    return service.client.put(
        f"/internal/cases/{CASE}/{route}", json=body, headers=headers
    )


def _score_body(
    make_score_push: Callable[..., Any], rows: list[ReviewItemRow], **kwargs: Any
) -> dict[str, Any]:
    return make_score_push(rows, **kwargs).model_dump(mode="json")  # type: ignore[no-any-return]


# ── who may call ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.ANALYST, Role.COMMITTEE, Role.OBSERVER])
@pytest.mark.parametrize(
    ("method", "route"),
    [
        *(("put", route) for route in (*PUT_ROUTES, "handoff")),
        ("get", "score-request"),
        ("get", "recommendation"),
        ("get", "evidence?control_id=CTL-T01"),
    ],
)
def test_no_person_role_may_call_an_internal_assessment_route(
    assessment_service: CaseService,
    act_as: Callable[[Role, str], None],
    role: Role,
    method: str,
    route: str,
) -> None:
    act_as(role, "u-someone")

    response = assessment_service.client.request(
        method, f"/internal/cases/{CASE}/{route}", json={}
    )

    assert response.status_code == 403


def test_the_routes_are_not_in_the_public_openapi_document(
    assessment_service: CaseService,
) -> None:
    paths = assessment_service.client.get("/openapi.json").json()["paths"]

    assert not [p for p in paths if "/internal/" in p]


def test_an_unwired_assessment_store_answers_503_not_a_crash(
    case_service: CaseService,
    act_as: Callable[[Role, str], None],
    stored_case: Any,
) -> None:
    act_as(Role.SERVICE, "svc:caseflow")

    response = case_service.client.put(
        f"/internal/cases/{CASE}/screening", json=_screening_body()
    )

    assert response.status_code == 503


def test_an_unknown_case_is_a_404(assessment_service: CaseService) -> None:
    response = assessment_service.client.put(
        "/internal/cases/FCRM-2026-9999/screening", json=_screening_body()
    )

    assert response.status_code == 404


# ── the PUT views ───────────────────────────────────────────────────────────


def test_a_screening_push_is_stored(
    assessment_service: CaseService, assessment_store: InMemoryAssessmentStore
) -> None:
    response = _put(assessment_service, "screening", _screening_body(), key="k1")

    assert response.status_code == 200
    assert response.json() == {
        "case_id": CASE,
        "view": "screening",
        "id": None,
        "replayed": False,
    }
    row = asyncio.run(assessment_store.get_screening(CASE))
    assert row is not None
    assert (row.run_id, row.fast_lane_proposed, row.proposal_id) == (
        "run_1",
        False,
        "prp_1",
    )
    assert row.reasons[0]["quote"] == "new corridors require jurisdiction screening"


def test_the_same_key_and_body_replays_and_writes_nothing(
    assessment_service: CaseService, assessment_store: InMemoryAssessmentStore
) -> None:
    first = _put(assessment_service, "screening", _screening_body(), key="k1")
    stored = asyncio.run(assessment_store.get_screening(CASE))
    second = _put(assessment_service, "screening", _screening_body(), key="k1")

    assert first.json()["replayed"] is False
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert asyncio.run(assessment_store.get_screening(CASE)) == stored


def test_the_same_key_with_another_body_is_a_conflict(
    assessment_service: CaseService,
) -> None:
    _put(assessment_service, "screening", _screening_body(), key="k1")

    response = _put(
        assessment_service,
        "screening",
        _screening_body(fast_lane_proposed=True),
        key="k1",
    )

    assert response.status_code == 409


def test_one_key_cannot_be_reused_for_another_view(
    assessment_service: CaseService,
) -> None:
    _put(assessment_service, "screening", _screening_body(), key="k1")

    response = _put(assessment_service, "critic", _critic_body(), key="k1")

    assert response.status_code == 409


def test_the_same_key_on_another_case_is_independent(
    assessment_service: CaseService,
    case_service: CaseService,
    make_case_record: Callable[..., Any],
) -> None:
    other = make_case_record("FCRM-2026-0152")
    case_service.store._cases[other.case_id] = other  # type: ignore[attr-defined]
    _put(assessment_service, "screening", _screening_body(), key="k1")

    response = assessment_service.client.put(
        "/internal/cases/FCRM-2026-0152/screening",
        json=_screening_body(),
        headers={"Idempotency-Key": "k1"},
    )

    assert response.json()["replayed"] is False


@pytest.mark.parametrize("key", ["", "has space", "x" * 256])
def test_a_malformed_idempotency_key_is_refused(
    assessment_service: CaseService, key: str
) -> None:
    response = assessment_service.client.put(
        f"/internal/cases/{CASE}/screening",
        json=_screening_body(),
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 422


def test_a_body_with_an_unknown_field_is_refused(
    assessment_service: CaseService,
) -> None:
    response = _put(
        assessment_service, "screening", _screening_body(composite=9.8), key="k1"
    )

    assert response.status_code == 422


def test_a_score_push_appends_a_snapshot_with_the_domain_figures(
    assessment_service: CaseService,
    assessment_store: InMemoryAssessmentStore,
    demo_review_items: list[ReviewItemRow],
    make_score_push: Callable[..., Any],
) -> None:
    body = _score_body(make_score_push, demo_review_items)

    response = _put(assessment_service, "score", body, key="step1/score")

    assert response.status_code == 200
    snapshot_id = response.json()["id"]
    latest = asyncio.run(assessment_store.latest_score(CASE))
    assert latest is not None
    assert latest.snapshot_id == snapshot_id
    assert (latest.composite, latest.band, latest.route) == (9.8, 2, "analyst")
    assert latest.boundary_flag is True
    assert latest.boundary_distance == 0.3
    assert latest.purpose == "proposed"
    assert latest.scoring_v == 3
    assert latest.domains["channel"] == {
        "L": 4.0,
        "I": 5.0,
        "inherent": 20.0,
        "ce": 7.59,
        "retention": 0.54,
        "residual": 10.9,
        "weight": 0.45,
        "contribution": 4.9,
    }
    assert latest.request is not None
    assert latest.response is not None


def test_a_replayed_score_push_is_one_snapshot(
    assessment_service: CaseService,
    assessment_store: InMemoryAssessmentStore,
    demo_review_items: list[ReviewItemRow],
    make_score_push: Callable[..., Any],
) -> None:
    body = _score_body(make_score_push, demo_review_items)

    first = _put(assessment_service, "score", body, key="step1/score").json()
    second = _put(assessment_service, "score", body, key="step1/score").json()

    assert second == {**first, "replayed": True}
    assert len(assessment_store.snapshots) == 1


def test_a_keyless_score_push_of_the_same_body_is_still_one_snapshot(
    assessment_service: CaseService,
    assessment_store: InMemoryAssessmentStore,
    demo_review_items: list[ReviewItemRow],
    make_score_push: Callable[..., Any],
) -> None:
    body = _score_body(make_score_push, demo_review_items)

    _put(assessment_service, "score", body)
    _put(assessment_service, "score", body)

    assert len(assessment_store.snapshots) == 1


def test_a_rescore_keeps_history_and_records_what_it_replaced(
    assessment_service: CaseService,
    assessment_store: InMemoryAssessmentStore,
    demo_review_items: list[ReviewItemRow],
    make_score_push: Callable[..., Any],
) -> None:
    _put(
        assessment_service,
        "score",
        _score_body(make_score_push, demo_review_items),
        key="step1/score",
    )
    rows = list(demo_review_items)
    index = next(
        i
        for i, r in enumerate(rows)
        if r.kind is ItemKind.CONTROL
        and r.subject["control_id"] == "CTL-T01"
        and not r.subject["planned"]
    )
    from fcrm_case.domain.views import DecisionRow
    from fcrm_contracts.enums import ItemStatus

    rows[index] = rows[index].model_copy(
        update={
            "status": ItemStatus.OVERRIDDEN,
            "decided": DecisionRow(
                value={"rating": 4.5},
                reason="Audit predates the corridor.",
                by="u-riyer",
                at=rows[index].created_at,
            ),
        }
    )
    rescored = _score_body(
        make_score_push,
        rows,
        purpose="confirmed",
        previous=PreviousScore(composite=9.8, band=Band.BAND_2),
    )
    rescored["gate_id"] = "gate_ack_band"

    _put(assessment_service, "score", rescored, key="step2/score")

    assert len(assessment_store.snapshots) == 2
    newest = max(assessment_store.snapshots.values(), key=lambda r: r.computed_at)
    assert (newest.composite, newest.band, newest.purpose) == (10.4, 3, "confirmed")
    assert (newest.previous_composite, newest.previous_band) == (9.8, 2)
    assert newest.band_changed is True
    assert newest.gate_id == "gate_ack_band"


def test_a_score_push_that_names_another_case_is_refused(
    assessment_service: CaseService,
    demo_review_items: list[ReviewItemRow],
    make_score_push: Callable[..., Any],
) -> None:
    body = _score_body(make_score_push, demo_review_items)
    body["score"]["case_id"] = "FCRM-2026-0001"

    response = _put(assessment_service, "score", body, key="k")

    assert response.status_code == 422


def test_a_critic_push_counts_findings_by_severity(
    assessment_service: CaseService, assessment_store: InMemoryAssessmentStore
) -> None:
    _put(assessment_service, "critic", _critic_body(), key="k")

    row = asyncio.run(assessment_store.get_critic(CASE))
    assert row is not None
    assert row.counts == {"total": 2, "high": 1, "medium": 1}
    assert [f["finding_id"] for f in row.findings] == ["itm_f1", "itm_f2"]


def test_a_completeness_push_keeps_the_report_questions_and_gate(
    assessment_service: CaseService, assessment_store: InMemoryAssessmentStore
) -> None:
    _put(assessment_service, "completeness", _completeness_body(), key="k")

    row = asyncio.run(assessment_store.get_completeness(CASE))
    assert row is not None
    assert row.gate_id == "gate_completeness"
    assert row.report["questions_kept"] == 2
    assert row.questions[0]["question_id"] == "q1"


def test_a_new_recommendation_draft_never_erases_the_analysts_edit(
    assessment_service: CaseService, assessment_store: InMemoryAssessmentStore
) -> None:
    _put(assessment_service, "recommendation-draft", _draft_body(), key="k1")
    row = asyncio.run(assessment_store.get_recommendation(CASE))
    assert row is not None
    assert row.draft_seq == 1
    # The analyst surface (the next lane) edits and locks beside the draft.
    assessment_store.recommendations[CASE] = row.model_copy(
        update={"edits": {"sections": [{"key": "recommendation", "text": "mine"}]}}
    )

    _put(
        assessment_service,
        "recommendation-draft",
        _draft_body("A re-drafted recommendation."),
        key="k2",
    )

    updated = asyncio.run(assessment_store.get_recommendation(CASE))
    assert updated is not None
    assert updated.draft["sections"][0]["text"] == "A re-drafted recommendation."
    assert updated.draft_seq == 2
    assert updated.edits == {"sections": [{"key": "recommendation", "text": "mine"}]}


def test_the_same_draft_pushed_twice_does_not_bump_the_draft_number(
    assessment_service: CaseService, assessment_store: InMemoryAssessmentStore
) -> None:
    _put(assessment_service, "recommendation-draft", _draft_body())
    _put(assessment_service, "recommendation-draft", _draft_body())

    row = asyncio.run(assessment_store.get_recommendation(CASE))
    assert row is not None
    assert row.draft_seq == 1


def test_a_handoff_push_is_stored(
    assessment_service: CaseService, assessment_store: InMemoryAssessmentStore
) -> None:
    _put(assessment_service, "handoff", _handoff_body(), key="k")

    row = asyncio.run(assessment_store.get_handoff(CASE))
    assert row is not None
    assert (row.pack_id, row.pack_version, row.audit_head_seq) == ("pack_1", 1, 54)
    assert all(row.checks.values())


# ── the reads ───────────────────────────────────────────────────────────────


def test_the_score_request_is_a_conflict_before_the_assessment_has_run(
    assessment_service: CaseService,
) -> None:
    response = assessment_service.client.get(f"/internal/cases/{CASE}/score-request")

    assert response.status_code == 409


def test_the_score_request_carries_the_pinned_version_and_no_planned_control(
    assessment_service: CaseService, demo_review_items: list[ReviewItemRow]
) -> None:
    for row in demo_review_items:
        assessment_service.views.items[row.item_id] = row  # type: ignore[attr-defined]

    response = assessment_service.client.get(f"/internal/cases/{CASE}/score-request")

    assert response.status_code == 200
    body = response.json()
    assert body["request"]["scoring_v"] == 3
    assert body["planned_control_ids"] == ["CTL-T01"]
    controls = [
        c["control_id"] for d in body["request"]["domains"] for c in d["controls"]
    ]
    assert controls.count("CTL-T01") == 1
    assert len(controls) == 13
    assert body["undecided_items"] == 17


def test_evidence_for_a_control_names_its_artefacts(
    assessment_service: CaseService, demo_review_items: list[ReviewItemRow]
) -> None:
    for row in demo_review_items:
        assessment_service.views.items[row.item_id] = row  # type: ignore[attr-defined]

    response = assessment_service.client.get(
        f"/internal/cases/{CASE}/evidence", params={"control_id": "CTL-T01"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["control_id"] == "CTL-T01"
    assert {i["artifact"] for i in body["items"]} == {
        "DD report, audit 2023-11, domestic scope",
        "vendor_DD_scope_letter_2026.pdf",
    }


def test_evidence_needs_a_control_id(assessment_service: CaseService) -> None:
    response = assessment_service.client.get(f"/internal/cases/{CASE}/evidence")

    assert response.status_code == 422


def test_there_is_no_recommendation_before_the_narrative_has_drafted_one(
    assessment_service: CaseService,
) -> None:
    response = assessment_service.client.get(f"/internal/cases/{CASE}/recommendation")

    assert response.status_code == 404


def test_the_recommendation_read_carries_the_draft_the_edits_and_the_lock(
    assessment_service: CaseService, assessment_store: InMemoryAssessmentStore
) -> None:
    _put(assessment_service, "recommendation-draft", _draft_body(), key="k1")
    row = asyncio.run(assessment_store.get_recommendation(CASE))
    assert row is not None
    assessment_store.recommendations[CASE] = row.model_copy(
        update={
            "edits": {
                "sections": [{"key": "recommendation", "text": "Edited."}],
                "business_summary": "Ready for the committee.",
                "edit_count": 2,
            },
            "locked": True,
            "locked_by": "u-riyer",
        }
    )

    response = assessment_service.client.get(f"/internal/cases/{CASE}/recommendation")

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["sections"][0]["text"] == "Residual risk sits in Band 3."
    assert body["draft"]["conditions"][0]["condition_id"] == "CND-1"
    assert body["edits"]["sections"] == [{"key": "recommendation", "text": "Edited."}]
    assert body["edits"]["conditions"] is None  # not edited: the draft stands
    assert body["edits"]["edit_count"] == 2
    assert (body["draft_seq"], body["locked"], body["locked_by"]) == (
        1,
        True,
        "u-riyer",
    )


def test_stored_edits_of_a_shape_the_analyst_surface_does_not_promise_are_a_conflict(
    assessment_service: CaseService, assessment_store: InMemoryAssessmentStore
) -> None:
    _put(assessment_service, "recommendation-draft", _draft_body(), key="k1")
    row = asyncio.run(assessment_store.get_recommendation(CASE))
    assert row is not None
    assessment_store.recommendations[CASE] = row.model_copy(
        update={"edits": {"unexpected": True}}
    )

    response = assessment_service.client.get(f"/internal/cases/{CASE}/recommendation")

    assert response.status_code == 409

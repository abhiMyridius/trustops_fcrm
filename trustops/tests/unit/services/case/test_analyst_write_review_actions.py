"""The analyst's other review actions (scenes 11-14): ``POST controls/planned``,
``POST gaps``, ``POST change-types`` and ``POST runs``.

What these tests hold: a planned control is recorded and announced but can never
reach the score request (rule 7); a gap is recorded once; a re-tag stores the
fields and domains that follow from the new change type before ``case.retagged``
reaches Caseflow; a re-run carries its reason; and each one is the analyst's own,
in review, on an open case, with the same request twice a replay.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.assessment import build_score_view
from fcrm_contracts.enums import CaseState, Role
from fcrm_contracts.events.payloads import (
    CaseControlPlanned,
    CaseGapRecorded,
    CaseRerunRequested,
    CaseRetagged,
)

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"
REASON = "The partner also pays out through a second agent network in Guatemala."
PLANNED = f"/cases/{CASE}/controls/planned"
GAPS = f"/cases/{CASE}/gaps"
CHANGE_TYPES = f"/cases/{CASE}/change-types"
RUNS = f"/cases/{CASE}/runs"


def code(response: Any) -> str:
    return str(response.json()["type"]).rsplit("/", 1)[-1]


def events(service: CaseService, type_: str) -> list[dict[str, Any]]:
    return [e.payload for e in service.store.events if e.type == type_]


def stage(service: CaseService, state: CaseState) -> None:
    record = service.store._cases[CASE]
    service.store._cases[CASE] = record.model_copy(update={"state": state})


def planned_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "control_id": "CTL-T01",
        "description": "Corridor-specific due-diligence review with the auditor",
        "owner": "Priya Nair",
        "due": "Nov 2026",
    }
    return {**body, **overrides}


# ── who may call ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_only_the_analyst_records_or_retags(
    review_service: CaseService, act_as: Callable[[Role, str], None], role: Role
) -> None:
    act_as(role, "someone")
    client = review_service.client

    statuses = [
        client.post(PLANNED, json=planned_body()).status_code,
        client.post(GAPS, json={"text": "No clause on agent onboarding."}).status_code,
        client.post(
            CHANGE_TYPES, json={"change_type": "new_segment", "reason": REASON}
        ).status_code,
        client.post(RUNS, json={"reason": REASON}).status_code,
    ]

    assert statuses == [403, 403, 403, 403]
    assert review_service.store.events == []


def test_an_analyst_who_does_not_hold_the_case_may_not_change_it(
    review_service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-mshah")

    assert review_service.client.post(PLANNED, json=planned_body()).status_code == 403
    assert review_service.client.post(RUNS, json={"reason": REASON}).status_code == 403


# ── a planned control (rule 7) ──────────────────────────────────────────────────


def test_a_planned_control_is_recorded_and_announced(
    review_service: CaseService,
) -> None:
    response = review_service.client.post(PLANNED, json=planned_body())

    assert response.status_code == 201, response.text
    assert response.json()["replayed"] is False
    [payload] = events(review_service, "case.control_planned")
    planned = CaseControlPlanned.model_validate(payload).planned_control
    assert planned["control_id"] == "CTL-T01"
    assert planned["description"].startswith("Corridor-specific")
    assert planned["owner"] == "Priya Nair"
    assert planned["target_date"] == "Nov 2026"  # the key Caseflow reads
    assert planned["planned_id"] == response.json()["id"]
    rows = review_service.client.app.state.analyst_store.recorded["planned_controls"]  # type: ignore[attr-defined]
    assert set(rows) == {response.json()["id"]}


def test_a_planned_control_never_reaches_the_score_request(
    review_service: CaseService,
) -> None:
    items = list(review_service.views.items.values())
    before = build_score_view(CASE, 3, items)

    review_service.client.post(PLANNED, json=planned_body())
    review_service.client.post(
        PLANNED, json=planned_body(control_id=None, description="A second plan")
    )
    after = build_score_view(CASE, 3, list(review_service.views.items.values()))

    assert len(review_service.views.items) == len(items)  # no review item was added
    assert after.request == before.request
    assert after.planned_control_ids == before.planned_control_ids


def test_the_same_planned_control_twice_replays_and_announces_once(
    review_service: CaseService,
) -> None:
    first = review_service.client.post(PLANNED, json=planned_body())
    second = review_service.client.post(PLANNED, json=planned_body())

    assert second.status_code == 201
    assert second.json() == {**first.json(), "replayed": True}
    assert len(events(review_service, "case.control_planned")) == 1


def test_a_planned_control_may_name_no_control(review_service: CaseService) -> None:
    response = review_service.client.post(PLANNED, json=planned_body(control_id=None))

    assert response.status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        {"control_id": "CTL-Z99"},
        {"description": ""},
        {"owner": "   "},
        {"due": ""},
        {"score": 3},
    ],
)
def test_a_planned_control_that_is_not_valid_is_refused(
    review_service: CaseService, body: dict[str, Any]
) -> None:
    response = review_service.client.post(PLANNED, json=planned_body(**body))

    assert response.status_code == 422
    assert events(review_service, "case.control_planned") == []


# ── a gap ───────────────────────────────────────────────────────────────────────


def test_a_gap_is_recorded_once_and_announced(review_service: CaseService) -> None:
    text = "No clause covers payout-agent onboarding in Mexico."

    first = review_service.client.post(GAPS, json={"text": text})
    second = review_service.client.post(GAPS, json={"text": text})
    other = review_service.client.post(GAPS, json={"text": text + " (second search)"})

    assert first.status_code == second.status_code == 201
    assert second.json()["replayed"] is True
    assert other.json()["id"] != first.json()["id"]
    payloads = events(review_service, "case.gap_recorded")
    assert len(payloads) == 2
    gap = CaseGapRecorded.model_validate(payloads[0])
    assert (gap.gap_id, gap.text, gap.actor) == (first.json()["id"], text, "u-riyer")


def test_a_gap_needs_words(review_service: CaseService) -> None:
    assert review_service.client.post(GAPS, json={"text": "  "}).status_code == 422
    assert review_service.client.post(GAPS, json={}).status_code == 422


# ── a re-tag ────────────────────────────────────────────────────────────────────


def test_a_retag_stores_the_fields_and_domains_that_follow_before_it_is_announced(
    review_service: CaseService,
) -> None:
    before = review_service.store._cases[CASE]
    assert before.change_types == []

    response = review_service.client.post(
        CHANGE_TYPES, json={"change_type": "new_geography", "reason": REASON}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["change_types"] == ["new_geography"]
    assert body["required_fields"] > len(before.required_fields)
    assert body["replayed"] is False
    after = review_service.store._cases[CASE]
    assert [t.value for t in after.change_types] == ["new_geography"]
    assert "geography" in after.domains
    assert len(after.required_fields) == body["required_fields"]
    [payload] = events(review_service, "case.retagged")
    retagged = CaseRetagged.model_validate(payload)
    assert retagged.change_type.value == "new_geography"
    assert retagged.reason == REASON


def test_a_retag_adds_to_what_the_case_has_and_a_repeat_replays(
    review_service: CaseService,
) -> None:
    client = review_service.client
    client.post(CHANGE_TYPES, json={"change_type": "new_geography", "reason": REASON})

    second = client.post(
        CHANGE_TYPES, json={"change_type": "new_product", "reason": REASON}
    )
    again = client.post(
        CHANGE_TYPES, json={"change_type": "new_geography", "reason": REASON}
    )

    assert second.json()["change_types"] == ["new_geography", "new_product"]
    assert again.status_code == 200
    assert again.json()["replayed"] is True
    assert len(events(review_service, "case.retagged")) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"change_type": "new_geography", "reason": "too short"},
        {"change_type": "new_geography"},
        {"change_type": "buy_a_bank", "reason": REASON},
        {"change_type": "new_geography", "reason": REASON, "removed": True},
    ],
)
def test_a_retag_needs_a_known_type_and_a_reason_of_twenty_characters(
    review_service: CaseService, body: dict[str, Any]
) -> None:
    response = review_service.client.post(CHANGE_TYPES, json=body)

    assert response.status_code == 422
    assert events(review_service, "case.retagged") == []
    assert review_service.store._cases[CASE].change_types == []


# ── a re-run ────────────────────────────────────────────────────────────────────


def test_a_rerun_carries_its_reason(review_service: CaseService) -> None:
    response = review_service.client.post(
        RUNS, json={"reason": REASON, "scope": "changed_fields"}
    )

    assert response.status_code == 202, response.text
    assert response.json() == {
        "case_id": CASE,
        "scope": "changed_fields",
        "requested": True,
        "replayed": False,
    }
    [payload] = events(review_service, "case.rerun_requested")
    requested = CaseRerunRequested.model_validate(payload)
    assert requested.rerun_reason == REASON
    assert requested.scope == "changed_fields"


def test_the_same_rerun_twice_replays_a_new_reason_is_a_new_request(
    review_service: CaseService,
) -> None:
    client = review_service.client
    client.post(RUNS, json={"reason": REASON})

    same = client.post(RUNS, json={"reason": REASON})
    other = client.post(RUNS, json={"reason": REASON + " Second look."})

    assert same.json()["replayed"] is True
    assert other.json()["replayed"] is False
    assert len(events(review_service, "case.rerun_requested")) == 2


@pytest.mark.parametrize(
    "body", [{}, {"reason": "too short"}, {"reason": REASON, "scope": "everything"}]
)
def test_a_rerun_needs_a_reason_and_a_known_scope(
    review_service: CaseService, body: dict[str, Any]
) -> None:
    assert review_service.client.post(RUNS, json=body).status_code == 422


# ── stage and state ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("state", [CaseState.CLARIFICATION, CaseState.RECOMMEND])
def test_these_actions_belong_to_review(
    review_service: CaseService, state: CaseState
) -> None:
    stage(review_service, state)
    client = review_service.client

    responses = [
        client.post(PLANNED, json=planned_body()),
        client.post(GAPS, json={"text": "A gap."}),
        client.post(
            CHANGE_TYPES, json={"change_type": "new_segment", "reason": REASON}
        ),
        client.post(RUNS, json={"reason": REASON}),
    ]

    assert [r.status_code for r in responses] == [409] * 4
    assert {code(r) for r in responses} == {"wrong_stage"}
    assert review_service.store.events == []


def test_a_closed_case_takes_none_of_them(review_service: CaseService) -> None:
    stage(review_service, CaseState.CLOSED)
    client = review_service.client

    responses = [
        client.post(PLANNED, json=planned_body()),
        client.post(GAPS, json={"text": "A gap."}),
        client.post(
            CHANGE_TYPES, json={"change_type": "new_segment", "reason": REASON}
        ),
        client.post(RUNS, json={"reason": REASON}),
    ]

    assert {code(r) for r in responses} == {"case_closed"}


def test_each_action_leaves_one_analyst_activity_row(
    review_service: CaseService,
) -> None:
    client = review_service.client
    client.post(PLANNED, json=planned_body())
    client.post(GAPS, json={"text": "A gap."})
    client.post(CHANGE_TYPES, json={"change_type": "new_geography", "reason": REASON})
    client.post(RUNS, json={"reason": REASON})

    rows = review_service.store.activity.entries

    assert [e.type for e in rows] == [
        "control.planned",
        "gap.recorded",
        "case.retagged",
        "rerun.requested",
    ]
    assert {e.visibility for e in rows} == {"analyst"}
    assert all(REASON not in e.summary for e in rows)

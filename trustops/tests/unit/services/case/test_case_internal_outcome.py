"""``PUT /internal/cases/{id}/outcome`` (H1 records what S5 decided): the outcome
row, the activated conditions and the case's move to ``closed`` land together,
the same body twice changes nothing, and a closed case takes no more writes from
any role.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.record import CaseRecord
from fcrm_contracts.enums import CaseState, Role

if TYPE_CHECKING:
    from conftest import CaseService

SERVICE_SUB = "svc:caseflow"
CASE = "FCRM-2026-0151"
URL = f"/internal/cases/{CASE}/outcome"


def condition(ref: str, **overrides: Any) -> dict[str, Any]:
    """One activated condition, as H1 sends it."""
    body: dict[str, Any] = {
        "condition_id": f"cnd_{ref.lower()}",
        "ref": ref,
        "text": f"Condition {ref}",
        "owner_id": "u-pnair",
        "owner_label": "Priya Nair",
        "due_label": "before go-live",
        "due_date": "2027-02-01",
        "status": "open",
        "origin": "overridden control CTL-T01",
    }
    return {**body, **overrides}


def committee_body(**overrides: Any) -> dict[str, Any]:
    """The demo outcome: approved with three conditions by the committee."""
    body: dict[str, Any] = {
        "outcome": "approve_with_conditions",
        "mode": "committee",
        "decided_by": "committee",
        "pack_id": "pack_1",
        "plain_summary": "The committee approved this change with conditions.",
        "conditions": [
            condition("CND-1"),
            condition(
                "CND-2", owner_id=None, owner_label="FCRM Ops", status="scheduled"
            ),
            condition("CND-3", due_label="6 months", status="scheduled"),
        ],
    }
    return {**body, **overrides}


@pytest.fixture
def service(
    case_service: CaseService,
    act_as: Callable[[Role, str], None],
    stored_case: CaseRecord,
) -> CaseService:
    """The S1 app with the demo case stored, called as H1's service account."""
    act_as(Role.SERVICE, SERVICE_SUB)
    return case_service


def put(service: CaseService, body: dict[str, Any], key: str | None = None) -> Any:
    headers = {"Idempotency-Key": key} if key is not None else {}
    return service.client.put(URL, json=body, headers=headers)


# ── who may call and what the body must say ─────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.ANALYST, Role.COMMITTEE, Role.OBSERVER])
def test_no_person_role_may_record_an_outcome(
    case_service: CaseService, act_as: Callable[[Role, str], None], role: Role
) -> None:
    act_as(role, "u-someone")
    assert case_service.client.put(URL, json=committee_body()).status_code == 403


def test_an_outcome_for_an_unknown_case_is_404(service: CaseService) -> None:
    response = service.client.put(
        "/internal/cases/FCRM-2026-9999/outcome", json=committee_body()
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"pack_id": None},
        {"mode": "delegated", "pack_id": None, "reason": "too short"},
        {"mode": "delegated", "reason": "Band 2, delegated authority."},
        {"outcome": "approve"},
        {"conditions": [condition("CND-1"), condition("CND-1")]},
    ],
    ids=[
        "committee-without-pack",
        "delegated-short-reason",
        "delegated-with-pack",
        "conditions-on-a-plain-approval",
        "duplicate-condition",
    ],
)
def test_a_body_that_breaks_the_contract_is_422(
    service: CaseService, overrides: dict[str, Any]
) -> None:
    assert put(service, committee_body(**overrides)).status_code == 422


def test_the_route_is_not_in_the_public_openapi_document(
    case_service: CaseService,
) -> None:
    paths = case_service.client.app.openapi()["paths"]  # type: ignore[attr-defined]
    assert not [p for p in paths if "outcome" in p and "internal" in p]


# ── the close ───────────────────────────────────────────────────────────────


def test_the_outcome_closes_the_case_and_activates_every_condition(
    service: CaseService,
) -> None:
    response = put(service, committee_body())
    assert response.status_code == 200
    assert response.json() == {
        "case_id": CASE,
        "outcome": "approve_with_conditions",
        "mode": "committee",
        "closed": True,
        "conditions_activated": 3,
        "replayed": False,
    }
    record = service.store._cases[CASE]
    assert record.state is CaseState.CLOSED
    assert record.closed_at is not None
    assert record.decided_at == record.closed_at
    assert record.outcome_ref == CASE
    assert (
        record.outcome_summary == "The committee approved this change with conditions."
    )
    rows = {r.ref: r for r in service.outcomes.conditions.values()}
    assert sorted(rows) == ["CND-1", "CND-2", "CND-3"]
    assert rows["CND-1"].due_date == "2027-02-01"
    assert (rows["CND-2"].owner_id, rows["CND-2"].owner_label) == (None, "FCRM Ops")
    assert rows["CND-2"].status == "scheduled"
    assert {r.pack_id for r in rows.values()} == {"pack_1"}
    row = service.outcomes.outcomes[CASE]
    assert (row.status, row.mode, row.decided_by) == ("set", "committee", "committee")


def test_the_close_is_announced_on_the_stream_and_through_the_outbox(
    service: CaseService,
) -> None:
    put(service, committee_body())
    stage_rows = [
        e for e in service.store.activity.entries if e.type == "case.stage_changed"
    ]
    assert [(e.visibility, e.detail["stage"]) for e in stage_rows] == [
        ("all", "closed")
    ]
    types = sorted(e.type for e in service.outcomes.events)
    assert types == ["case.closed", *["condition.activated"] * 3]


def test_a_delegated_close_with_a_plain_approval_activates_no_condition(
    service: CaseService,
) -> None:
    body = committee_body(
        outcome="approve",
        mode="delegated",
        pack_id=None,
        decided_by="u-riyer",
        reason="Band 2: closed under delegated authority.",
        conditions=[],
    )
    response = put(service, body)
    assert response.status_code == 200
    assert response.json()["conditions_activated"] == 0
    assert service.outcomes.conditions == {}
    assert service.store._cases[CASE].state is CaseState.CLOSED


# ── idempotency ─────────────────────────────────────────────────────────────


def test_the_same_body_again_replays_and_writes_nothing(service: CaseService) -> None:
    first = put(service, committee_body())
    events, rows = len(service.outcomes.events), len(service.outcomes.conditions)
    entries = len(service.store.activity.entries)
    second = put(service, committee_body())
    assert second.status_code == 200
    assert second.json() == {**first.json(), "replayed": True}
    assert len(service.outcomes.events) == events
    assert len(service.outcomes.conditions) == rows
    assert len(service.store.activity.entries) == entries


def test_the_same_key_with_the_same_body_replays(service: CaseService) -> None:
    first = put(service, committee_body(), key="idem-1")
    second = put(service, committee_body(), key="idem-1")
    assert second.json() == {**first.json(), "replayed": True}


def test_the_same_key_with_another_body_is_a_409(service: CaseService) -> None:
    put(service, committee_body(), key="idem-1")
    other = committee_body(outcome="defer", conditions=[])
    response = put(service, other, key="idem-1")
    assert response.status_code == 409
    assert response.json()["type"].endswith("/idempotency_key_reused")


def test_a_different_outcome_for_a_decided_case_is_a_409(service: CaseService) -> None:
    put(service, committee_body())
    response = put(service, committee_body(outcome="defer", conditions=[]))
    assert response.status_code == 409
    assert response.json()["type"].endswith("/outcome_already_set")
    assert service.outcomes.outcomes[CASE].outcome.value == "approve_with_conditions"


@pytest.mark.parametrize("key", ["", "has space", "x" * 300])
def test_a_malformed_key_is_422(service: CaseService, key: str) -> None:
    assert put(service, committee_body(), key=key).status_code == 422


# ── closed is closed (rule 5) ───────────────────────────────────────────────


@pytest.fixture
def closed(service: CaseService) -> CaseService:
    """The demo case after its outcome was recorded."""
    assert put(service, committee_body()).status_code == 200
    return service


def _problem_code(response: Any) -> str:
    return str(response.json()["type"]).rsplit("/", 1)[-1]


@pytest.mark.parametrize(
    ("role", "sub", "method", "path", "body"),
    [
        (
            Role.PO,
            "u-pnair",
            "post",
            f"/cases/{CASE}/documents",
            {"demo_files": []},
        ),
        (
            Role.PO,
            "u-pnair",
            "post",
            f"/cases/{CASE}/intake/answers",
            {"answers": [{"question_id": "q_F10", "value": "x1"}]},
        ),
        (Role.PO, "u-pnair", "post", f"/cases/{CASE}/submit", {}),
        (
            Role.SERVICE,
            SERVICE_SUB,
            "put",
            f"/internal/cases/{CASE}/items",
            {"run_id": "run_1", "items": []},
        ),
        (
            Role.SERVICE,
            SERVICE_SUB,
            "put",
            f"/internal/cases/{CASE}/intake",
            {"run_id": "run_1", "candidates_found": 0},
        ),
        (
            Role.SERVICE,
            SERVICE_SUB,
            "patch",
            f"/internal/cases/{CASE}/state",
            {"stage": "review"},
        ),
        (
            Role.SERVICE,
            SERVICE_SUB,
            "post",
            f"/internal/cases/{CASE}/assign",
            {"analyst_id": "u-riyer"},
        ),
    ],
    ids=[
        "po-upload",
        "po-answers",
        "po-submit",
        "service-items",
        "service-intake",
        "service-state-away-from-closed",
        "service-assign",
    ],
)
def test_a_closed_case_takes_no_write_from_any_role(
    closed: CaseService,
    act_as: Callable[[Role, str], None],
    role: Role,
    sub: str,
    method: str,
    path: str,
    body: dict[str, Any],
) -> None:
    act_as(role, sub)
    response = closed.client.request(method, path, json=body)
    assert response.status_code == 409
    assert _problem_code(response) == "case_closed"


def test_h1_may_repeat_the_move_to_closed_but_not_leave_it(
    closed: CaseService,
) -> None:
    repeat = closed.client.patch(
        f"/internal/cases/{CASE}/state", json={"stage": "closed"}
    )
    assert repeat.status_code == 200
    away = closed.client.patch(
        f"/internal/cases/{CASE}/state", json={"stage": "decide"}
    )
    assert away.status_code == 409
    assert closed.store._cases[CASE].state is CaseState.CLOSED


def test_a_closed_case_still_takes_a_stream_event(closed: CaseService) -> None:
    """The close is announced to the PO after the case is closed
    (``outcome.delivered``), so events are not a write the guard refuses.
    """
    response = closed.client.post(
        f"/internal/cases/{CASE}/events",
        json={
            "type": "outcome.delivered",
            "visibility": "all",
            "summary": "Your change was approved with conditions",
            "detail": {},
            "actor": {"id": "H1", "role": "system", "component": "H1"},
        },
    )
    assert response.status_code == 201

"""S1's analyst routes for closing a case and tracking its conditions: the
delegated close is a *request* (R1: Band 1-2, locked, the analyst's own case, a
reason, a plain summary) and the conditions tracker moves a status only with a
reason of at least 20 characters.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.record import CaseRecord, StageFlags
from fcrm_case.domain.views import ConditionRow
from fcrm_contracts.enums import CaseState, Role

if TYPE_CHECKING:
    from conftest import CaseService

ANALYST = "u-riyer"
OTHER_ANALYST = "u-mshah"
CASE = "FCRM-2026-0151"
CLOSE = f"/cases/{CASE}/close"
REASON = "Band 2: closed under delegated authority, nothing is open."
SUMMARY = "Your change is approved. No conditions apply."


def close_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "outcome": "approve",
        "reason": REASON,
        "plain_summary": SUMMARY,
    }
    return {**body, **overrides}


@pytest.fixture
def case(case_service: CaseService, stored_case: CaseRecord) -> CaseRecord:
    """The demo case held by Rahul, its recommendation locked, scored Band 2."""
    held = stored_case.model_copy(
        update={
            "state": CaseState.RECOMMEND,
            "analyst_id": ANALYST,
            "analyst_name": "Rahul Iyer",
            "run_id": "run_1",
            "stage_flags": StageFlags(locked=True),
        }
    )
    case_service.store._cases[CASE] = held
    case_service.outcomes.bands[CASE] = 2
    return held


@pytest.fixture
def analyst(
    case_service: CaseService,
    case: CaseRecord,
    act_as: Callable[[Role, str], None],
) -> CaseService:
    act_as(Role.ANALYST, ANALYST)
    return case_service


def _problem_code(response: Any) -> str:
    return str(response.json()["type"]).rsplit("/", 1)[-1]


# ── who may call ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", CLOSE, close_body()),
        ("patch", "/conditions/cnd_x", {"note": "seen"}),
    ],
)
def test_only_an_analyst_may_close_or_update_conditions(
    case_service: CaseService,
    act_as: Callable[[Role, str], None],
    role: Role,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    act_as(role, "u-someone")
    response = case_service.client.request(method, path, json=body)
    assert response.status_code == 403


@pytest.mark.parametrize("role", [Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_only_the_analyst_and_the_owner_read_the_conditions(
    case_service: CaseService,
    act_as: Callable[[Role, str], None],
    role: Role,
) -> None:
    """The owner reads the plain shape of the same route (see
    ``test_po_followup_routes``); no other role reads it.
    """
    act_as(role, "u-someone")

    response = case_service.client.get(f"/cases/{CASE}/conditions")

    assert response.status_code == 403


# ── the delegated close request ─────────────────────────────────────────────


def test_a_band_two_locked_case_asks_h1_to_close_it(analyst: CaseService) -> None:
    response = analyst.client.post(CLOSE, json=close_body())
    assert response.status_code == 202
    assert response.json() == {
        "case_id": CASE,
        "outcome": "approve",
        "requested": True,
        "replayed": False,
    }
    [event] = analyst.outcomes.events
    assert event.type == "case.close_requested"
    assert event.payload["actor"] == ANALYST
    assert event.payload["band"] == 2
    assert event.payload["reason"] == REASON
    assert event.payload["plain_summary"] == SUMMARY
    assert event.run_id == "run_1"
    # S1 only requests: the case is not closed until the outcome comes back.
    assert analyst.store._cases[CASE].state is CaseState.RECOMMEND
    [entry] = [
        e for e in analyst.store.activity.entries if e.type == "case.close_requested"
    ]
    assert entry.visibility == "analyst"


def test_the_same_request_again_is_a_noop_and_a_different_one_is_a_409(
    analyst: CaseService,
) -> None:
    analyst.client.post(CLOSE, json=close_body())
    again = analyst.client.post(CLOSE, json=close_body())
    assert again.status_code == 202
    assert again.json()["replayed"] is True
    assert len(analyst.outcomes.events) == 1
    other = analyst.client.post(CLOSE, json=close_body(outcome="defer"))
    assert other.status_code == 409
    assert _problem_code(other) == "close_already_requested"


@pytest.mark.parametrize("band", [3, 4])
def test_a_case_that_routes_to_committee_cannot_be_closed_by_the_analyst(
    analyst: CaseService, band: int
) -> None:
    analyst.outcomes.bands[CASE] = band
    response = analyst.client.post(CLOSE, json=close_body())
    assert response.status_code == 409
    assert _problem_code(response) == "rule_violation"
    assert analyst.outcomes.events == []


def test_a_case_with_no_score_yet_cannot_be_closed(analyst: CaseService) -> None:
    del analyst.outcomes.bands[CASE]
    assert analyst.client.post(CLOSE, json=close_body()).status_code == 409


def test_an_unlocked_recommendation_blocks_the_close(analyst: CaseService) -> None:
    held = analyst.store._cases[CASE]
    analyst.store._cases[CASE] = held.model_copy(
        update={"stage_flags": StageFlags(locked=False)}
    )
    response = analyst.client.post(CLOSE, json=close_body())
    assert response.status_code == 409
    assert _problem_code(response) == "gate_required"


def test_a_closed_case_cannot_be_closed_again(analyst: CaseService) -> None:
    held = analyst.store._cases[CASE]
    analyst.store._cases[CASE] = held.model_copy(update={"state": CaseState.CLOSED})
    response = analyst.client.post(CLOSE, json=close_body())
    assert response.status_code == 409
    assert _problem_code(response) == "case_closed"


def test_only_the_analyst_who_holds_the_case_may_close_it(
    case_service: CaseService, case: CaseRecord, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, OTHER_ANALYST)
    assert case_service.client.post(CLOSE, json=close_body()).status_code == 403


def test_a_close_for_an_unknown_case_is_404(analyst: CaseService) -> None:
    response = analyst.client.post("/cases/FCRM-2026-9999/close", json=close_body())
    assert response.status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"reason": "too short"},
        {"plain_summary": "   "},
        {"plain_summary": "The composite score was 9.8 so Band 2 applies."},
        {"outcome": "maybe"},
    ],
    ids=["short-reason", "blank-summary", "summary-names-a-score", "unknown-outcome"],
)
def test_a_close_that_breaks_the_rules_is_422(
    analyst: CaseService, overrides: dict[str, Any]
) -> None:
    response = analyst.client.post(CLOSE, json=close_body(**overrides))
    assert response.status_code == 422
    assert analyst.outcomes.events == []


# ── the conditions tracker ──────────────────────────────────────────────────


def condition_row(ref: str, status: str = "open", **overrides: Any) -> ConditionRow:
    fields: dict[str, Any] = {
        "condition_id": f"cnd_{ref.lower()}",
        "case_id": CASE,
        "text": f"Condition {ref}",
        "owner_id": "u-pnair",
        "owner_label": "Priya Nair",
        "due_label": "before go-live",
        "due_date": "2027-02-01",
        "status": status,
        "source": {"kind": "pack", "label": "overridden control CTL-T01"},
        "ref": ref,
    }
    return ConditionRow(**{**fields, **overrides})


@pytest.fixture
def conditions(analyst: CaseService) -> CaseService:
    for ref, status in (("CND-1", "open"), ("CND-2", "scheduled")):
        row = condition_row(ref, status)
        analyst.outcomes.conditions[row.condition_id] = row
    held = analyst.store._cases[CASE]
    analyst.store._cases[CASE] = held.model_copy(update={"state": CaseState.CLOSED})
    return analyst


def test_the_analyst_reads_the_cases_conditions_in_the_web_shape(
    conditions: CaseService,
) -> None:
    response = conditions.client.get(f"/cases/{CASE}/conditions")
    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["next_cursor"]) == (2, None)
    assert body["items"][0] == {
        "id": "cnd_cnd-1",
        "text": "Condition CND-1",
        "owner": "Priya Nair",
        "due": "before go-live",
        "status": "open",
        "from": "overridden control CTL-T01",
        "due_date": "2027-02-01",
        "note": None,
    }
    assert body["items"][1]["status"] == "scheduled"


def test_a_status_change_needs_a_reason_of_twenty_characters(
    conditions: CaseService,
) -> None:
    short = conditions.client.patch(
        "/conditions/cnd_cnd-1", json={"status": "closed", "reason": "done"}
    )
    assert short.status_code == 422
    ok = conditions.client.patch(
        "/conditions/cnd_cnd-1",
        json={"status": "closed", "reason": "Corridor review filed and checked."},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "closed"
    # a closed case still tracks its conditions (rule 5 allows condition updates)
    assert conditions.store._cases[CASE].state is CaseState.CLOSED
    [event] = [e for e in conditions.outcomes.events if e.type == "condition.completed"]
    assert event.payload["data"]["status"] == "closed"
    [entry] = [
        e for e in conditions.store.activity.entries if e.type == "condition.updated"
    ]
    assert entry.visibility == "analyst"
    assert "Corridor review filed and checked." in entry.summary


def test_an_evidence_note_alone_needs_no_reason(conditions: CaseService) -> None:
    response = conditions.client.patch(
        "/conditions/cnd_cnd-2", json={"note": "Scope letter section 2 read."}
    )
    assert response.status_code == 200
    assert response.json()["note"] == "Scope letter section 2 read."
    assert response.json()["status"] == "scheduled"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": "evidence_submitted", "reason": "x" * 25},
        {"status": "closed"},
    ],
    ids=["nothing", "analyst-cannot-set-evidence-submitted", "no-reason"],
)
def test_an_update_that_breaks_the_rules_is_422(
    conditions: CaseService, body: dict[str, Any]
) -> None:
    assert (
        conditions.client.patch("/conditions/cnd_cnd-1", json=body).status_code == 422
    )


def test_a_closed_condition_cannot_change(conditions: CaseService) -> None:
    conditions.client.patch(
        "/conditions/cnd_cnd-1",
        json={"status": "closed", "reason": "Corridor review filed and checked."},
    )
    again = conditions.client.patch("/conditions/cnd_cnd-1", json={"note": "late note"})
    assert again.status_code == 409
    assert _problem_code(again) == "condition_closed"


def test_an_unknown_condition_is_404_and_a_foreign_one_is_403(
    conditions: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    assert (
        conditions.client.patch("/conditions/cnd_nope", json={"note": "x"}).status_code
        == 404
    )
    act_as(Role.ANALYST, OTHER_ANALYST)
    foreign = conditions.client.patch("/conditions/cnd_cnd-1", json={"note": "x"})
    assert foreign.status_code == 403

"""The two paths the web reads as an owner and as staff: the activity feed (the
owner's plain feed and the analyst's, one handler) and the recorded outcome (for
the analyst and the committee).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.outcome import CloseRequestRow, OutcomeRow
from fcrm_case.domain.views import ConditionRow
from fcrm_contracts.api.case_analyst_read import OutcomeAnalyst
from fcrm_contracts.enums import CaseState, Outcome, Role

if TYPE_CHECKING:
    from conftest import AssessedCase

CASE = "FCRM-2026-0151"
ACTIVITY = f"/cases/{CASE}/activity"
OUTCOME = f"/cases/{CASE}/outcome"
PO = "u-pnair"
ANALYST = "u-riyer"
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


@pytest.fixture
def feed(assessed_case: AssessedCase, add_activity: Callable[..., Any]) -> AssessedCase:
    """The demo case with a mixed log: what the owner may read, what only the
    analyst may, and what is shared.
    """
    assessed_case.update_record(activity_seq=0)
    add_activity(
        CASE,
        "document.extracted",
        "po",
        "Two documents were read",
        actor=("svc:caseflow", "fcrm_service", "ingestion"),
    )
    add_activity(
        CASE,
        "case.stage_changed",
        "all",
        "Moved from triage to screening",
        {"from_stage": "triage", "stage": "screening"},
    )
    add_activity(
        CASE,
        "score.recomputed",
        "analyst",
        "Re-scored after the override",
        {"record_seq": 49, "gate_id": "gate_rescore"},
        actor=("u-riyer", "fcrm_analyst", "case"),
    )
    add_activity(
        CASE,
        "gate.decided",
        "analyst",
        "Override of the vendor review recorded",
        {"gate_id": "gate_override", "record_seq": 48},
        actor=("u-riyer", "fcrm_analyst", "case"),
    )
    add_activity(
        CASE,
        "case.submitted",
        "po",
        "You submitted the case",
        actor=("u-pnair", "fcrm_po", "case"),
    )
    return assessed_case


# ── the activity feed ───────────────────────────────────────────────────────────


def test_an_analyst_reads_every_row_with_who_did_it_and_the_record_it_cites(
    feed: AssessedCase,
) -> None:
    response = feed.client.get(ACTIVITY)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [i["id"] for i in items] == ["ev_5", "ev_4", "ev_3", "ev_2", "ev_1"]
    by_id = {i["id"]: i for i in items}
    assert by_id["ev_5"]["actor"] == "Priya Nair"
    assert by_id["ev_3"]["actor"] == "Rahul Iyer"
    assert by_id["ev_1"]["actor"] == "ingestion"
    assert by_id["ev_2"]["actor"] == "caseflow"
    assert (by_id["ev_3"]["record_seq"], by_id["ev_3"]["gate_id"]) == (
        49,
        "gate_rescore",
    )
    assert by_id["ev_4"]["type"] == "gate.decided"
    assert by_id["ev_1"]["record_seq"] is None
    assert by_id["ev_1"]["gate_id"] is None
    assert by_id["ev_3"]["text"] == "Re-scored after the override"


def test_the_owner_reads_only_their_plain_feed_from_the_same_path(
    feed: AssessedCase, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, PO)
    response = feed.client.get(ACTIVITY)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [i["id"] for i in items] == ["ev_5", "ev_2", "ev_1"]
    for item in items:
        assert set(item) == {"id", "at", "actor", "text"}
    assert {i["actor"] for i in items} <= {"You", "FCRM", "System", "AI"}
    assert "override" not in response.text.lower()
    assert "record_seq" not in response.text


def test_another_owner_does_not_see_the_case_activity(
    feed: AssessedCase, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, "u-tgold")
    assert feed.client.get(ACTIVITY).status_code == 404


@pytest.mark.parametrize("role", [Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_only_the_owner_and_the_analyst_read_the_feed(
    feed: AssessedCase, act_as: Callable[[Role, str], None], role: Role
) -> None:
    act_as(role, "u-someone")
    assert feed.client.get(ACTIVITY).status_code == 403


def test_the_feed_pages_newest_first_in_windows_of_sequence_numbers(
    feed: AssessedCase,
) -> None:
    client = feed.client
    first = client.get(ACTIVITY, params={"limit": 2}).json()
    assert [i["id"] for i in first["items"]] == ["ev_5", "ev_4"]
    assert first["next_cursor"] is not None
    second = client.get(
        ACTIVITY, params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert [i["id"] for i in second["items"]] == ["ev_3", "ev_2"]
    third = client.get(
        ACTIVITY, params={"limit": 2, "cursor": second["next_cursor"]}
    ).json()
    assert [i["id"] for i in third["items"]] == ["ev_1"]
    assert third["next_cursor"] is None


def test_a_bad_page_request_is_a_422(feed: AssessedCase) -> None:
    from fcrm_common.pagination import encode_cursor

    client = feed.client
    for params in (
        {"limit": 0},
        {"limit": 201},
        {"cursor": "garbage"},
        {"cursor": encode_cursor("seven")},
        {"cursor": encode_cursor(0)},
    ):
        assert client.get(ACTIVITY, params=params).status_code == 422, params


def test_an_empty_log_is_an_empty_page(assessed_case: AssessedCase) -> None:
    body = assessed_case.client.get(ACTIVITY).json()
    assert body == {"items": [], "next_cursor": None, "total": 0}


def test_an_unknown_case_has_no_feed(assessed_case: AssessedCase) -> None:
    assert assessed_case.client.get("/cases/FCRM-2099-0001/activity").status_code == 404


def test_a_row_cites_the_record_caseflow_sent_beside_the_event(
    assessed_case: AssessedCase, add_activity: Callable[..., Any]
) -> None:
    assessed_case.update_record(activity_seq=0)
    add_activity(CASE, "score.computed", "analyst", "Scored", record_seq=52)
    add_activity(
        CASE,
        "score.recomputed",
        "analyst",
        "Re-scored",
        {"record_seq": 11},
        record_seq=53,
    )
    add_activity(CASE, "gate.decided", "analyst", "Decided", {"record_seq": 12})
    items = {i["id"]: i for i in assessed_case.client.get(ACTIVITY).json()["items"]}
    assert items["ev_1"]["record_seq"] == 52
    assert items["ev_2"]["record_seq"] == 53
    assert items["ev_3"]["record_seq"] == 12


# ── the recorded outcome ────────────────────────────────────────────────────────


def condition(case_id: str, ref: str, status: str = "open") -> ConditionRow:
    return ConditionRow(
        condition_id=f"cnd_{ref}",
        case_id=case_id,
        text=f"Condition {ref}",
        owner_label="Priya Nair",
        due_label="before go-live",
        due_date="2027-02-01",
        status=status,
        ref=ref,
    )


def outcome(case: AssessedCase) -> OutcomeAnalyst:
    response = case.client.get(OUTCOME)
    assert response.status_code == 200, response.text
    return OutcomeAnalyst.model_validate(response.json())


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE])
def test_a_committee_outcome_names_how_it_was_decided_and_its_conditions(
    assessed_case: AssessedCase, act_as: Callable[[Role, str], None], role: Role
) -> None:
    service = assessed_case.read.service
    assessed_case.update_record(state=CaseState.CLOSED, decided_at=NOW)
    service.outcomes.outcomes[CASE] = OutcomeRow(
        case_id=CASE,
        status="set",
        outcome=Outcome.APPROVE_WITH_CONDITIONS,
        mode="committee",
        decided_by="svc:caseflow",
        decided_at=NOW,
        pack_id="pack_1",
        plain_summary="Your change is approved with three conditions.",
    )
    for ref in ("CND-1", "CND-2", "CND-3"):
        service.outcomes.conditions[f"cnd_{ref}"] = condition(CASE, ref)
    act_as(role, "u-member")
    result = outcome(assessed_case)
    assert result.decided is True
    assert result.outcome == "approve_with_conditions"
    assert result.outcome_label == "Approved with conditions"
    assert (result.mode, result.decided_by) == ("committee", "Risk Committee")
    assert result.pack_id == "pack_1"
    assert result.plain_summary == "Your change is approved with three conditions."
    assert [c.ref for c in result.conditions] == ["CND-1", "CND-2", "CND-3"]
    assert result.conditions[0].due_date == "2027-02-01"
    assert result.close_requested is False


def test_a_delegated_close_names_the_analyst_and_the_reason(
    assessed_case: AssessedCase,
) -> None:
    assessed_case.update_record(state=CaseState.CLOSED)
    assessed_case.read.service.outcomes.outcomes[CASE] = OutcomeRow(
        case_id=CASE,
        status="set",
        outcome=Outcome.APPROVE,
        mode="delegated",
        decided_by=ANALYST,
        decided_at=NOW,
        reason="Band 2: nothing is open.",
    )
    result = outcome(assessed_case)
    assert (result.mode, result.decided_by) == ("delegated", "Rahul Iyer")
    assert result.reason == "Band 2: nothing is open."
    assert result.pack_id is None
    assert result.conditions == []


def test_a_requested_close_is_not_yet_an_outcome(assessed_case: AssessedCase) -> None:
    assessed_case.read.service.outcomes.outcomes[CASE] = OutcomeRow(
        case_id=CASE,
        status="requested",
        request=CloseRequestRow(
            outcome=Outcome.APPROVE,
            reason="Band 2: nothing is open.",
            plain_summary="Approved.",
            requested_by=ANALYST,
            requested_at=NOW,
            band=2,
        ),
    )
    result = outcome(assessed_case)
    assert result.decided is False
    assert result.close_requested is True
    assert (result.outcome, result.outcome_label, result.mode) == (None, None, None)


def test_an_open_case_has_no_outcome(assessed_case: AssessedCase) -> None:
    result = outcome(assessed_case)
    assert result.decided is False
    assert result.close_requested is False
    assert result.conditions == []


def test_a_seeded_closed_case_reads_its_outcome_from_the_seed(
    seeded_analyst_read: Any,
) -> None:
    closed = next(
        r
        for r in seeded_analyst_read.service.store.records.values()
        if r.state is CaseState.CLOSED
    )
    response = seeded_analyst_read.client.get(f"/cases/{closed.case_id}/outcome")
    assert response.status_code == 200, response.text
    result = OutcomeAnalyst.model_validate(response.json())
    assert result.decided is True
    assert result.outcome in {"approve", "approve_with_conditions", "defer", "reject"}
    assert result.mode is None
    assert result.decided_at == closed.decided_at


@pytest.mark.parametrize("role", [Role.OBSERVER, Role.SERVICE, Role.PO])
def test_the_observer_the_service_and_the_owner_do_not_read_the_staff_outcome(
    assessed_case: AssessedCase, act_as: Callable[[Role, str], None], role: Role
) -> None:
    act_as(role, "u-someone")
    assert assessed_case.client.get(OUTCOME).status_code == 403


def test_an_unknown_case_has_no_outcome(assessed_case: AssessedCase) -> None:
    assert assessed_case.client.get("/cases/FCRM-2099-0001/outcome").status_code == 404

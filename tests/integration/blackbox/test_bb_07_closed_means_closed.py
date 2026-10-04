"""BB-07, "closed means closed" (design doc numbering: "After outcome: every
write endpoint on the case -> 409 ``case_closed``, for analyst, PO and
committee tokens"; CLAUDE.md "closed cases reject writes").

Driven through the real S1 app (``fcrm_case.main.create_app``) over its
in-memory store, plus the real S5 app for the committee side. HTTP only,
with one seam: no route in S1 or H1 moves a case into DECIDE or CLOSED, so
the ``set_case_state`` fixture writes the record the way H1's transition
would. Bearer verification is replaced by the usual ``current_user``
dependency override; role checks and ownership checks still run for real.

Honest differences from the table, asserted as they actually are:

- The error is the generic ``conflict`` problem type (409), not a dedicated
  ``case_closed`` code. The detail text names the closed case.
- S1 has three write routes (title, field value, submit) and all three are
  PO-only. A PO therefore gets 409 on a closed case; an analyst, committee
  member or service caller never reaches the state check and gets 403
  instead. Either way nothing is written, which is what is asserted.

Committee side: once S5 has tallied an outcome its pack takes no further
vote. Known gap, deliberately not asserted because it would fail today: S5
does not know the case is closed, so a ``POST /packs`` with *changed* content
after an outcome mints a new released pack v2 whose votes can produce a
second, different outcome for the same case.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import CaseState, Role

pytestmark = pytest.mark.integration

OTHER_ROLES = [
    (Role.ANALYST, "u-riyer"),
    (Role.COMMITTEE, "u-amendes"),
    (Role.SERVICE, "svc:caseflow"),
]


@pytest.fixture
def closed_case(
    case_client: TestClient,
    create_case: Callable[[str], str],
    fill_and_submit: Callable[[str, str], None],
    set_case_state: Callable[..., None],
    demo: dict[str, Any],
) -> str:
    """The demo-shaped case, submitted by its PO and then closed with an
    outcome, as it stands after the committee has decided.
    """
    case_id = create_case(demo["owner"])
    fill_and_submit(case_id, demo["owner"])
    set_case_state(
        case_id,
        CaseState.CLOSED,
        decided_at=datetime.now(UTC),
        outcome_summary="Approved with conditions, 2-1.",
    )
    return case_id


def _write_attempts(client: TestClient, case_id: str, field_id: str) -> dict[str, Any]:
    """Every S1 write route, once each, as whoever is currently acting."""
    return {
        "title": client.put(f"/cases/{case_id}/title", json={"title": "renamed"}),
        "field": client.put(
            f"/cases/{case_id}/fields/{field_id}", json={"value": "overwritten"}
        ),
        "submit": client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        ),
    }


def _snapshot_of(client: TestClient, case_id: str) -> tuple[Any, ...]:
    """What a reviewer can see of the case, as raw bytes (detail and
    submission versions), so any change at all is detectable.
    """
    detail = client.get(f"/cases/{case_id}")
    versions = client.get(f"/cases/{case_id}/versions")
    assert detail.status_code == 200
    assert versions.status_code == 200
    return (detail.content, versions.content)


def test_every_write_on_a_closed_case_is_refused_for_every_role(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    closed_case: str,
    demo: dict[str, Any],
) -> None:
    act_as(Role.ANALYST, "u-riyer")
    field_id = case_client.get(f"/cases/{closed_case}").json()["fields"][0]["field_id"]
    before = _snapshot_of(case_client, closed_case)

    # The owning PO: the state check itself, 409.
    act_as(Role.PO, demo["owner"])
    for route, response in _write_attempts(case_client, closed_case, field_id).items():
        assert response.status_code == 409, f"PO {route}: {response.text}"
        assert response.json()["type"].endswith("/conflict")

    # Every other role: refused before the state check, 403.
    for role, sub in OTHER_ROLES:
        act_as(role, sub)
        for route, response in _write_attempts(
            case_client, closed_case, field_id
        ).items():
            assert response.status_code == 403, f"{role} {route}: {response.text}"

    act_as(Role.ANALYST, "u-riyer")
    assert _snapshot_of(case_client, closed_case) == before  # nothing changed


@pytest.mark.parametrize("state", [CaseState.DECIDE, CaseState.CLOSED])
def test_the_state_alone_closes_a_case_to_its_owner_even_before_any_submission(
    case_client: TestClient,
    create_case: Callable[[str], str],
    set_case_state: Callable[..., None],
    act_as: Callable[[Role, str], None],
    demo: dict[str, Any],
    state: CaseState,
) -> None:
    """An unsubmitted record in a closed state (so ``submitted_at`` cannot
    be what refuses the write): the PO's three writes are all 409 and the
    case stays empty.
    """
    case_id = create_case(demo["owner"])
    set_case_state(case_id, state)
    field_id = case_client.get(f"/cases/{case_id}").json()["fields"][0]["field_id"]

    for route, response in _write_attempts(case_client, case_id, field_id).items():
        assert response.status_code == 409, f"{state} {route}: {response.text}"

    detail = case_client.get(f"/cases/{case_id}").json()
    assert detail["required_fields_filled"] == 0
    assert detail["title"] == demo["title"]


def test_a_closed_case_can_still_be_read_by_every_role_that_may_read_it(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    closed_case: str,
    demo: dict[str, Any],
) -> None:
    """Closed is read-only, not hidden: the outcome stays visible."""
    for role, sub in [(Role.PO, demo["owner"]), *OTHER_ROLES]:
        act_as(role, sub)
        response = case_client.get(f"/cases/{closed_case}")
        assert response.status_code == 200, f"{role}: {response.text}"
        body = response.json()
        assert body["state"] == "closed"
        assert body["outcome_summary"] == "Approved with conditions, 2-1."
        assert body["decided_at"] is not None


def test_creating_a_new_case_is_unaffected_by_another_case_being_closed(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    closed_case: str,
    demo: dict[str, Any],
) -> None:
    """The control for the tests above: a refusal is about *that* case,
    not a blanket lock on the PO.
    """
    act_as(Role.PO, demo["owner"])
    response = case_client.post(
        "/cases",
        json={
            "title": "A second change",
            "unit": demo["unit"],
            "owner": demo["owner"],
            "change_types": ["new_feature"],
        },
    )
    assert response.status_code == 201
    assert response.json()["case_id"] != closed_case


def test_once_the_committee_has_an_outcome_its_pack_takes_no_further_vote(
    decision_client: TestClient,
    build_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    committee_member_ids: dict[str, str],
    demo: dict[str, Any],
) -> None:
    pack = build_pack(demo["id"])
    ids = committee_member_ids
    for member, option in (
        ("Joseph Okafor", "approve_with_conditions"),
        ("Ana Mendes", "approve_with_conditions"),
        ("Lin Chen", "defer"),
    ):
        assert cast_vote(pack["pack_id"], ids[member], option).status_code == 200
    decided = decision_client.get(f"/packs/{pack['pack_id']}/outcome").json()
    assert decided["outcome"] == demo["outcome"]

    # A fourth member, a re-vote by a member who already voted, and an
    # attempt to flip the result: all refused; the outcome does not move.
    for late_member, option in (
        ("a-fourth-member", "reject"),
        (ids["Lin Chen"], "reject"),
    ):
        late = cast_vote(pack["pack_id"], late_member, option)
        assert late.status_code == 409
        assert late.json()["type"].endswith("/rule_violation")
    assert decision_client.get(f"/packs/{pack['pack_id']}/outcome").json() == decided

"""BB-07, "closed means closed" (design doc numbering: "After outcome: every
write endpoint on the case -> 409 ``case_closed``, for analyst, PO and
committee tokens"; CLAUDE.md "closed cases reject writes").

Driven through the real S1 app (``fcrm_case.main.create_app``) over its
in-memory stores, plus the real S5 app for the committee side. HTTP only, with
one seam: no route in S1 or H1 moves a case into DECIDE or CLOSED, so the
``set_case_state`` fixture writes the record the way H1's transition would.
Bearer verification is replaced by the usual ``current_user`` dependency
override; role checks and ownership checks still run for real.

What the table says, asserted as it is: the error is the dedicated
``case_closed`` problem (409). S1's write routes are the owner's (the PO
surface); an analyst, committee member or service caller never reaches the state
check and gets 403 instead (the service account is also denied by OPA, Guard 7).
Either way nothing is written, which is what is asserted.

Committee side: once S5 has tallied an outcome its pack takes no further
vote, and the case takes no second pack: ``POST /packs`` with changed content
after an outcome is refused (409 ``case_decided``), so there is no pack v2
whose votes could produce a second, different outcome for the same case.
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
OWNER = "u-pnair"


@pytest.fixture
def closed_case(
    case_client: TestClient,
    create_case: Callable[[str], str],
    fill_and_submit: Callable[[str, str], None],
    set_case_state: Callable[..., None],
) -> str:
    """The demo-shaped case, submitted by its PO and then closed with an
    outcome, as it stands after the committee has decided.
    """
    case_id = create_case(OWNER)
    fill_and_submit(case_id, OWNER)
    set_case_state(
        case_id,
        CaseState.CLOSED,
        decided_at=datetime.now(UTC),
        outcome_summary="Approved with conditions, 2-1.",
    )
    return case_id


def _write_attempts(client: TestClient, case_id: str) -> dict[str, Any]:
    """Every write route of the PO surface, once each, as whoever is acting."""
    base = f"/cases/{case_id}"
    return {
        "documents": client.post(f"{base}/documents", json={"demo_files": []}),
        "category": client.post(
            f"{base}/intake/categories/new_product", json={"decision": "confirm"}
        ),
        "value": client.post(
            f"{base}/intake/values/F03",
            json={"decision": "correct", "value": "overwritten"},
        ),
        "reuse": client.post(
            f"{base}/intake/reuse",
            json={"from_case_id": "FCRM-2026-0118", "field_ids": ["F31"]},
        ),
        "answers": client.post(
            f"{base}/intake/answers",
            json={"answers": [{"question_id": "q_F10", "value": "x"}]},
        ),
        "submit": client.post(f"{base}/submit", json={}),
        "discard": client.post(
            f"{base}/discard", json={"reason": "no longer needed, closed already"}
        ),
    }


def _snapshot_of(client: TestClient, case_id: str) -> tuple[Any, ...]:
    """What the owner can see of the case, as raw bytes (the case, its values and
    its activity), so any change at all is detectable.
    """
    base = f"/cases/{case_id}"
    responses = [
        client.get(f"{base}{suffix}") for suffix in ("", "/review", "/activity")
    ]
    assert all(r.status_code == 200 for r in responses)
    return tuple(r.content for r in responses)


def test_every_write_on_a_closed_case_is_refused_for_every_role(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    closed_case: str,
) -> None:
    act_as(Role.PO, OWNER)
    before = _snapshot_of(case_client, closed_case)

    # The owning PO: the state check itself, 409 case_closed.
    for route, response in _write_attempts(case_client, closed_case).items():
        assert response.status_code == 409, f"PO {route}: {response.text}"
        assert response.json()["type"].endswith("/case_closed"), route

    # Every other role: refused before the state check, 403.
    for role, sub in OTHER_ROLES:
        act_as(role, sub)
        for route, response in _write_attempts(case_client, closed_case).items():
            assert response.status_code == 403, f"{role} {route}: {response.text}"

    act_as(Role.PO, OWNER)
    assert _snapshot_of(case_client, closed_case) == before  # nothing changed


@pytest.mark.parametrize("state", [CaseState.DECIDE, CaseState.CLOSED])
def test_the_state_alone_closes_a_case_to_its_owner_even_before_any_submission(
    case_client: TestClient,
    create_case: Callable[[str], str],
    set_case_state: Callable[..., None],
    state: CaseState,
) -> None:
    """An unsubmitted record in a closed state (so ``submitted_at`` cannot be
    what refuses the write): the PO's writes are all 409 ``case_closed`` and the
    case stays empty.
    """
    case_id = create_case(OWNER)
    set_case_state(case_id, state)

    for route, response in _write_attempts(case_client, case_id).items():
        assert response.status_code == 409, f"{state} {route}: {response.text}"
        assert response.json()["type"].endswith("/case_closed"), route

    detail = case_client.get(f"/cases/{case_id}").json()
    assert detail["completion"]["filled"] == 0


def test_a_closed_case_can_still_be_read_by_its_owner(
    case_client: TestClient, act_as: Callable[[Role, str], None], closed_case: str
) -> None:
    """Closed is read-only, not hidden: the owner still sees the case, closed."""
    act_as(Role.PO, OWNER)
    body = case_client.get(f"/cases/{closed_case}").json()
    assert body["stage"] == "closed"
    assert body["closed"] is True
    assert case_client.get(f"/cases/{closed_case}/review").status_code == 200
    # ... and H1 reads the same state from the internal record.
    act_as(Role.SERVICE, "svc:caseflow")
    record = case_client.get(f"/internal/cases/{closed_case}/record").json()
    assert record["state"] == "closed"


def test_creating_a_new_case_is_unaffected_by_another_case_being_closed(
    case_client: TestClient, act_as: Callable[[Role, str], None], closed_case: str
) -> None:
    """The control for the tests above: a refusal is about *that* case, not a
    blanket lock on the PO.
    """
    act_as(Role.PO, OWNER)
    response = case_client.post("/cases", json={"title": "A second change"})
    assert response.status_code == 201
    assert response.json()["id"] != closed_case


def test_once_the_committee_has_an_outcome_its_pack_takes_no_further_vote(
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    committee_member_ids: dict[str, str],
    demo: dict[str, Any],
) -> None:
    pack = released_pack(demo["id"])
    ids = committee_member_ids
    for member, option in (
        ("Joseph Okafor", "approve_with_conditions"),
        ("Ana Mendes", "approve_with_conditions"),
        ("Lin Chen", "defer"),
    ):
        assert cast_vote(pack["pack_id"], ids[member], option).status_code == 201
    act_as(Role.ANALYST, "u-riyer")
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
        assert late.json()["type"].endswith("/pack_decided")
    act_as(Role.ANALYST, "u-riyer")
    assert decision_client.get(f"/packs/{pack['pack_id']}/outcome").json() == decided


def test_a_new_pack_for_a_case_that_has_an_outcome_is_refused(
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    release_snapshot: Callable[..., dict[str, Any]],
    committee_member_ids: dict[str, str],
    demo: dict[str, Any],
) -> None:
    """Closed means closed on S5's side too: after an outcome the case takes no
    second pack whose votes could produce a second, different outcome.
    """
    pack = released_pack(demo["id"])
    for member, option in zip(
        committee_member_ids.values(), ("approve", "approve", "defer"), strict=True
    ):
        assert cast_vote(pack["pack_id"], member, option).status_code == 201

    act_as(Role.SERVICE, "svc:caseflow")
    changed = release_snapshot(demo["id"], business_summary="A different pack.")
    refused = decision_client.post(
        "/packs", json={"case_id": demo["id"], "snapshot": changed}
    )

    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/case_decided")

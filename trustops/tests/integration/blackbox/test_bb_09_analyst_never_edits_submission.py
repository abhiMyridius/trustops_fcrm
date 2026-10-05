"""BB-09, "analyst never edits submission" -- the half that exists today
(design doc numbering: "Analyst ``POST intake/values/{id}`` -> 403; PO answer
creates v2 and v1 stays byte-identical"; rule 4).

Driven through the real S1 app (``fcrm_case.main.create_app``) over its
in-memory stores, HTTP only; bearer verification is replaced by the usual
``current_user`` dependency override while ``require_roles`` and the owner check
still run for real.

Covered: no role other than the owning PO can change a field value, and a
submitted version cannot be changed by anyone -- every attempt leaves
submission v1 and the field values byte-identical.

Not covered, and left skipped in ``test_bb_not_yet_testable.py``: "a PO answer
creates v2". The route that folds an answer into a second submission version is
the clarification round's (``POST clarifications/{id}/answer``), not built yet;
until it is, v1 can never be superseded over HTTP.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role

if TYPE_CHECKING:
    from conftest import CaseService

pytestmark = pytest.mark.integration

OWNER = "u-pnair"
NON_PO_ROLES = [
    (Role.ANALYST, "u-riyer"),
    (Role.COMMITTEE, "u-amendes"),
    (Role.SERVICE, "svc:caseflow"),
]
CORRECT = {"decision": "correct", "value": "tampered"}


def test_only_the_owning_po_can_write_a_value_everyone_else_gets_403(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    create_case: Callable[[str], str],
    run_h1_intake: Callable[..., None],
) -> None:
    case_id = create_case(OWNER)
    case_client.post(f"/cases/{case_id}/documents", json={"demo_files": []})
    run_h1_intake(case_id)
    url = f"/cases/{case_id}/intake/values/F03"

    # Control: the owner's own correction is accepted.
    act_as(Role.PO, OWNER)
    accepted = case_client.post(url, json={"decision": "correct", "value": "original"})
    assert accepted.status_code == 200, accepted.text

    for role, sub in NON_PO_ROLES:
        act_as(role, sub)
        refused = case_client.post(url, json=CORRECT)
        assert refused.status_code == 403, f"{role}: {refused.text}"
        assert refused.json()["type"].endswith("/forbidden")

    # Another PO is not the owner: a write is refused, whatever they send.
    act_as(Role.PO, "u-someone-else")
    other = case_client.post(url, json=CORRECT)
    assert other.status_code == 403

    act_as(Role.PO, OWNER)
    review = case_client.get(f"/cases/{case_id}/review").json()
    stored = next(r for r in review["rows"] if r["field_id"] == "F03")
    assert stored["value"] == "original"


def _frozen(service: CaseService, case_id: str) -> dict[str, Any]:
    row = asyncio.run(service.po.get_submission(case_id, 1))
    assert row is not None
    return row.model_dump(mode="json")


def test_submission_v1_is_byte_identical_after_every_attempt_to_change_it(
    case_service: CaseService,
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    create_case: Callable[[str], str],
    fill_and_submit: Callable[[str, str], None],
) -> None:
    case_id = create_case(OWNER)
    fill_and_submit(case_id, OWNER)
    before = _frozen(case_service, case_id)
    review_before = case_client.get(f"/cases/{case_id}/review").content
    url = f"/cases/{case_id}/intake/values/F03"

    # Every role tries to change a value in the submitted version.
    for role, sub in NON_PO_ROLES:
        act_as(role, sub)
        assert case_client.post(url, json=CORRECT).status_code == 403
    # The owner cannot rewrite it either: a submitted case is frozen.
    act_as(Role.PO, OWNER)
    assert case_client.post(url, json=CORRECT).status_code == 409
    replay = case_client.post(f"/cases/{case_id}/submit", json={})
    assert replay.status_code == 200
    assert replay.json()["message"] == "Already submitted."

    assert _frozen(case_service, case_id) == before  # byte-identical
    assert case_client.get(f"/cases/{case_id}/review").content == review_before
    assert len(case_service.po.submissions) == 1  # a replay wrote no second version


def test_no_route_lets_anyone_but_the_owner_touch_a_value_by_any_method(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    create_case: Callable[[str], str],
) -> None:
    """The only method the value path offers is the owner's POST; the other
    methods are 405 and the old ``PUT /fields/{id}`` is gone.
    """
    case_id = create_case(OWNER)
    act_as(Role.ANALYST, "u-riyer")
    url = f"/cases/{case_id}/intake/values/F03"
    assert case_client.post(url, json=CORRECT).status_code == 403
    assert case_client.put(url, json=CORRECT).status_code == 405
    assert case_client.patch(url, json=CORRECT).status_code == 405
    assert case_client.delete(url).status_code == 405
    gone = case_client.put(f"/cases/{case_id}/fields/F03", json={"value": "x"})
    assert gone.status_code in (404, 405)

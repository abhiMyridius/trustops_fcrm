"""BB-09, "analyst never edits submission" -- the half that exists today
(design doc numbering: "Analyst ``PUT fields/{id}`` -> 403; PO answer
creates v2 and v1 stays byte-identical"; rule 4).

Driven through the real S1 app (``fcrm_case.main.create_app``) over its
in-memory store, HTTP only; bearer verification is replaced by the usual
``current_user`` dependency override while ``require_roles`` and the
owner check still run for real.

Covered: no role other than the owning PO can change a field value, and a
submitted version cannot be changed by anyone -- every attempt leaves
submission v1 and the field values byte-identical.

Not covered, and left skipped in ``test_bb_not_yet_testable.py``: "a PO
answer creates v2". S1 has no way to create a second submission version:
``POST /cases/{id}/submit`` is one-shot (a second submit is 409), there is
no clarification-answer route, and a PO's field write after submission is
409. v1 can therefore never be superseded over HTTP yet.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role

pytestmark = pytest.mark.integration

NON_PO_ROLES = [
    (Role.ANALYST, "u-riyer"),
    (Role.COMMITTEE, "u-amendes"),
    (Role.SERVICE, "svc:caseflow"),
]


def test_only_the_owning_po_can_write_a_field_everyone_else_gets_403_or_404(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    create_case: Callable[[str], str],
    demo: dict[str, Any],
) -> None:
    case_id = create_case(demo["owner"])
    field_id = case_client.get(f"/cases/{case_id}").json()["fields"][0]["field_id"]
    url = f"/cases/{case_id}/fields/{field_id}"

    # Control: the owner's own write is accepted.
    accepted = case_client.put(url, json={"value": "original", "how": "answered"})
    assert accepted.status_code == 200

    for role, sub in NON_PO_ROLES:
        act_as(role, sub)
        refused = case_client.put(url, json={"value": f"tampered by {role}"})
        assert refused.status_code == 403, f"{role}: {refused.text}"
        assert refused.json()["type"].endswith("/forbidden")

    # Another PO is not the owner: S1 answers as if the case did not exist.
    act_as(Role.PO, "u-someone-else")
    other = case_client.put(url, json={"value": "tampered by another PO"})
    assert other.status_code == 404

    act_as(Role.PO, demo["owner"])
    detail = case_client.get(f"/cases/{case_id}").json()
    stored = next(f for f in detail["fields"] if f["field_id"] == field_id)
    assert stored["value"] == "original"
    assert detail["required_fields_filled"] == 1


def test_submission_v1_is_byte_identical_after_every_attempt_to_change_it(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    create_case: Callable[[str], str],
    fill_and_submit: Callable[[str, str], None],
    demo: dict[str, Any],
) -> None:
    case_id = create_case(demo["owner"])
    fill_and_submit(case_id, demo["owner"])

    act_as(Role.ANALYST, "u-riyer")
    versions_before = case_client.get(f"/cases/{case_id}/versions")
    assert versions_before.status_code == 200
    assert [v["version"] for v in versions_before.json()] == [1]
    fields_before = case_client.get(f"/cases/{case_id}").json()["fields"]
    field_id = fields_before[0]["field_id"]
    url = f"/cases/{case_id}/fields/{field_id}"
    title_url = f"/cases/{case_id}/title"

    # Every role tries to change a value in the submitted version.
    for role, sub in NON_PO_ROLES:
        act_as(role, sub)
        assert case_client.put(url, json={"value": "edited"}).status_code == 403
        assert case_client.put(title_url, json={"title": "x"}).status_code == 403
    # The owner cannot rewrite it either: a submitted case is frozen.
    act_as(Role.PO, demo["owner"])
    assert case_client.put(url, json={"value": "edited"}).status_code == 409
    resubmit = case_client.post(
        f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
    )
    assert resubmit.status_code == 409

    act_as(Role.ANALYST, "u-riyer")
    versions_after = case_client.get(f"/cases/{case_id}/versions")
    assert versions_after.content == versions_before.content  # byte-identical
    assert case_client.get(f"/cases/{case_id}").json()["fields"] == fields_before
    no_change = case_client.get(
        f"/cases/{case_id}/versions/diff", params={"from_version": 1, "to_version": 1}
    )
    assert no_change.json() == {"changed_fields": []}


def test_the_analyst_view_of_the_submission_is_read_only_data_not_an_edit_surface(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    create_case: Callable[[str], str],
    fill_and_submit: Callable[[str, str], None],
    demo: dict[str, Any],
) -> None:
    """The analyst can read every submitted value (and diff versions) but
    the only methods the case routes offer them on a field are GET-shaped:
    PUT/POST/DELETE on the field path are 403/405, never a write.
    """
    case_id = create_case(demo["owner"])
    fill_and_submit(case_id, demo["owner"])
    act_as(Role.ANALYST, "u-riyer")
    field_id = case_client.get(f"/cases/{case_id}").json()["fields"][0]["field_id"]
    url = f"/cases/{case_id}/fields/{field_id}"

    assert case_client.get(f"/cases/{case_id}").status_code == 200
    assert case_client.put(url, json={"value": "x"}).status_code == 403
    assert case_client.post(url, json={"value": "x"}).status_code == 405
    assert case_client.patch(url, json={"value": "x"}).status_code == 405
    assert case_client.delete(url).status_code == 405

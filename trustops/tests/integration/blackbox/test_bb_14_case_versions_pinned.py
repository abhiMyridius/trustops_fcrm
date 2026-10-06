"""BB-14, "versions pinned" at the case level (design doc numbering:
"Publish a new control inventory v10; the open demo case still reports
controls v9 and its score is unchanged"; CLAUDE.md "each case pins the
versions of its scoring model, corpus, controls, field set and prompts").

Driven through the real S1 app (``fcrm_case.main.create_app``) over its
in-memory stores, HTTP only; bearer verification is replaced by the usual
``current_user`` dependency override. A case's pins are visible in the internal
record H1 reads (``GET /internal/cases/{id}/record``).

"Publishing" a newer inventory or field set is simulated at the one seam S1 has
for it: S3's ``GET /internal/config/pins`` and ``GET /field-sets/{version}``,
which S1 reads through ``fcrm_case.policy_config.PolicyConfig`` when a case is
created (``fake_policy`` answers them over ``httpx.MockTransport``). What S3 holds
in force is the *input* here, not the logic under test; the logic under test is
that a case records its pins when it is created and never re-reads them
afterwards.

Not covered, and left skipped in ``test_bb_not_yet_testable.py``: "its score is
unchanged". S1 holds no score for a case a PO can read, so there is no
case-level score to compare before and after a publish. The scoring-model half
is proved against S2 in ``test_bb_14_scoring_version_pinned.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role

if TYPE_CHECKING:
    from conftest import CaseService, FakePolicy

pytestmark = pytest.mark.integration

OWNER = "u-pnair"

#: Controls v9, corpus v14, field set v6, scoring v3, routing v4: the seed's
#: pinned versions (CLAUDE.md "Seed package"), exactly as the walkthrough
#: and every one of the 80 seeded cases pin them.
SEEDED_PINS = {
    "scoring_v": 3,
    "corpus_v": 14,
    "controls_v": 9,
    "fieldset_v": 6,
    "jurisdictions_v": "v2026-06",
    "routing_v": 4,
    "prompts_v": "2026-09-28",
}


def _record_of(client: TestClient, act_as: Callable[..., None], case_id: str) -> Any:
    """The case as H1 reads it (the internal record), then back to the PO."""
    act_as(Role.SERVICE, "svc:caseflow")
    response = client.get(f"/internal/cases/{case_id}/record")
    assert response.status_code == 200, response.text
    act_as(Role.PO, OWNER)
    return response.json()


def test_a_new_case_pins_the_seeded_versions(
    case_service_on_policy: CaseService,
    act_as: Callable[..., None],
    create_case: Callable[[str], str],
) -> None:
    case_client = case_service_on_policy.client
    case_id = create_case(OWNER)
    record = _record_of(case_client, act_as, case_id)
    assert record["pins"] == SEEDED_PINS
    assert record["fieldset_v"] == SEEDED_PINS["fieldset_v"]


def test_publishing_controls_v10_does_not_move_an_open_case_but_pins_the_next_one(
    case_service_on_policy: CaseService,
    fake_policy: FakePolicy,
    act_as: Callable[..., None],
    create_case: Callable[[str], str],
    fill_and_submit: Callable[[str, str], None],
) -> None:
    case_client = case_service_on_policy.client
    open_case = create_case(OWNER)
    assert _record_of(case_client, act_as, open_case)["pins"]["controls_v"] == 9

    # Control inventory v10 is published: S3's versions in force now say so.
    fake_policy.publish_pins(controls_v=10)

    # The open case still reports controls v9, and every other pin is intact.
    assert _record_of(case_client, act_as, open_case)["pins"] == SEEDED_PINS

    # ... through its whole life: intake and submission change nothing.
    fill_and_submit(open_case, OWNER)
    assert _record_of(case_client, act_as, open_case)["pins"] == SEEDED_PINS

    # A case created after the publish pins v10; the two coexist.
    later_case = create_case(OWNER)
    assert _record_of(case_client, act_as, later_case)["pins"] == {
        **SEEDED_PINS,
        "controls_v": 10,
    }
    assert _record_of(case_client, act_as, open_case)["pins"]["controls_v"] == 9


def test_publishing_a_new_field_set_does_not_change_an_open_case_s_required_fields(
    case_service_on_policy: CaseService,
    fake_policy: FakePolicy,
    act_as: Callable[..., None],
    create_case: Callable[[str], str],
) -> None:
    case_client = case_service_on_policy.client
    open_case = create_case(OWNER)
    before = _record_of(case_client, act_as, open_case)

    # Field set v7 is published with one extra always-required field.
    fake_policy.publish_field_set(
        7, extra_always_required=({"id": "F99", "label": "Sanctions list reviewed"},)
    )

    after = _record_of(case_client, act_as, open_case)
    assert after["required_field_ids"] == before["required_field_ids"]
    assert "F99" not in before["required_field_ids"]
    assert after["fieldset_v"] == 6

    # The next case is created against v7 and carries the extra field.
    later_case = create_case(OWNER)
    later = _record_of(case_client, act_as, later_case)
    assert "F99" in later["required_field_ids"]
    assert len(later["required_field_ids"]) == len(before["required_field_ids"]) + 1
    assert later["fieldset_v"] == 7


def test_a_po_cannot_read_or_set_the_pins(
    case_client: TestClient,
    create_case: Callable[[str], str],
) -> None:
    case_id = create_case(OWNER)
    for url in (f"/cases/{case_id}", f"/cases/{case_id}/review", "/dashboard"):
        body = case_client.get(url).text
        assert "pins" not in body
        assert "pinned" not in body
        assert "controls_v" not in body
    # A case-creation request that tries to name its own pins is refused
    # outright (extra fields are forbidden), not silently honoured.
    forged = case_client.post(
        "/cases",
        json={
            "title": "forged pins",
            "pinned_versions": {**SEEDED_PINS, "controls_v": 1},
        },
    )
    assert forged.status_code == 422

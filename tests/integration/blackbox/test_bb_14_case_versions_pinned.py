"""BB-14, "versions pinned" at the case level (design doc numbering:
"Publish a new control inventory v10; the open demo case still reports
controls v9 and its score is unchanged"; CLAUDE.md "each case pins the
versions of its scoring model, corpus, controls, field set and prompts").

Driven through the real S1 app (``fcrm_case.main.create_app``) over its
in-memory store, HTTP only; bearer verification is replaced by the usual
``current_user`` dependency override. A case's pins are visible in the
service-caller (``fcrm_service``) detail shape, which is what H1 reads.

"Publishing" a newer inventory or field set is simulated at the one seam
S1 has for it: the "what is active now" lookup the create route calls
(``fcrm_case.routes.active_pinned_versions`` / ``active_field_set``, a
disclosed seed-backed placeholder until the owning services expose their
own active-version routes). That lookup is the *input* here, not the logic
under test; the logic under test is that a case records its pins when it is
created and never re-reads them afterwards.

Not covered, and left skipped in ``test_bb_not_yet_testable.py``: "its
score is unchanged". S1 holds no score for a case (the analyst shape's
``score`` is always null), so there is no case-level score to compare
before and after a publish. The scoring-model half is proved against S2 in
``test_bb_14_scoring_version_pinned.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_case.fieldset_provider import active_field_set, active_pinned_versions
from fcrm_contracts.enums import Role

pytestmark = pytest.mark.integration

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


def _pins_of(client: TestClient, case_id: str) -> dict[str, Any]:
    """The case's pinned versions as the service caller (H1) reads them."""
    response = client.get(f"/cases/{case_id}")
    assert response.status_code == 200, response.text
    pins: dict[str, Any] = response.json()["pinned_versions"]
    return pins


@pytest.fixture
def as_service(act_as: Callable[[Role, str], None]) -> Callable[[], None]:
    """Switch the caller to the service account H1 reads cases as."""

    def _switch() -> None:
        act_as(Role.SERVICE, "svc:caseflow")

    return _switch


def test_a_new_case_pins_the_seeded_versions(
    case_client: TestClient,
    create_case: Callable[[str], str],
    as_service: Callable[[], None],
    demo: dict[str, Any],
) -> None:
    case_id = create_case(demo["owner"])
    as_service()

    assert _pins_of(case_client, case_id) == SEEDED_PINS
    detail = case_client.get(f"/cases/{case_id}").json()
    assert detail["fieldset_v"] == SEEDED_PINS["fieldset_v"]


def test_publishing_controls_v10_does_not_move_an_open_case_but_pins_the_next_one(
    case_client: TestClient,
    create_case: Callable[[str], str],
    fill_and_submit: Callable[[str, str], None],
    as_service: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
    demo: dict[str, Any],
) -> None:
    open_case = create_case(demo["owner"])
    as_service()
    assert _pins_of(case_client, open_case)["controls_v"] == 9

    # Control inventory v10 is published: the active versions now say so.
    v10 = active_pinned_versions().model_copy(update={"controls_v": 10})
    monkeypatch.setattr("fcrm_case.routes.active_pinned_versions", lambda: v10)

    # The open case still reports controls v9, and every other pin is intact.
    assert _pins_of(case_client, open_case) == SEEDED_PINS

    # ... through its whole life: field writes and submission change nothing.
    fill_and_submit(open_case, demo["owner"])
    as_service()
    assert _pins_of(case_client, open_case) == SEEDED_PINS

    # A case created after the publish pins v10; the two coexist.
    later_case = create_case(demo["owner"])
    as_service()
    assert _pins_of(case_client, later_case) == {**SEEDED_PINS, "controls_v": 10}
    assert _pins_of(case_client, open_case)["controls_v"] == 9


def test_publishing_a_new_field_set_does_not_change_an_open_case_s_required_fields(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    create_case: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
    demo: dict[str, Any],
) -> None:
    open_case = create_case(demo["owner"])
    before = case_client.get(f"/cases/{open_case}").json()
    before_ids = [f["field_id"] for f in before["fields"]]

    # Field set v7 is published with one extra always-required field.
    current = active_field_set()
    v7 = current.model_copy(
        update={
            "version": 7,
            "always_required": [
                *current.always_required,
                {"id": "F99", "label": "Sanctions list reviewed"},
            ],
        }
    )
    monkeypatch.setattr("fcrm_case.routes.active_field_set", lambda: v7)

    after = case_client.get(f"/cases/{open_case}").json()
    assert [f["field_id"] for f in after["fields"]] == before_ids
    assert "F99" not in before_ids
    assert after["required_field_count"] == before["required_field_count"]
    act_as(Role.SERVICE, "svc:caseflow")
    assert case_client.get(f"/cases/{open_case}").json()["fieldset_v"] == 6

    # The next case is created against v7 and carries the extra field.
    later_case = create_case(demo["owner"])
    later = case_client.get(f"/cases/{later_case}").json()
    assert "F99" in [f["field_id"] for f in later["fields"]]
    assert later["required_field_count"] == before["required_field_count"] + 1
    act_as(Role.SERVICE, "svc:caseflow")
    assert case_client.get(f"/cases/{later_case}").json()["fieldset_v"] == 7


def test_only_the_service_shape_exposes_pins_so_a_po_cannot_read_or_change_them(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    create_case: Callable[[str], str],
    demo: dict[str, Any],
) -> None:
    case_id = create_case(demo["owner"])
    for role, sub in ((Role.PO, demo["owner"]), (Role.ANALYST, "u-riyer")):
        act_as(role, sub)
        assert "pinned_versions" not in case_client.get(f"/cases/{case_id}").json()
    # A case-creation request that tries to name its own pins is refused
    # outright (extra fields are forbidden), not silently honoured.
    act_as(Role.PO, demo["owner"])
    forged = case_client.post(
        "/cases",
        json={
            "title": "forged pins",
            "unit": demo["unit"],
            "owner": demo["owner"],
            "change_types": ["new_feature"],
            "pinned_versions": {**SEEDED_PINS, "controls_v": 1},
        },
    )
    assert forged.status_code == 422

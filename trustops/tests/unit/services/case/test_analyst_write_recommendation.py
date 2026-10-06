"""S1's recommendation routes: ``GET/PUT recommendation`` and ``POST
recommendation/lock|unlock`` (scene 15).

What these tests hold: the analyst's edits are stored in the exact shape H1's
pack builder reads (and read back the way the pack builder composes them); the
lock is refused, naming each blocker, unless S1's own stored state says every item
is decided and every acknowledgement given; a lock and an unlock are announced
once, an unlock carries its reason, and a replay changes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_caseflow.cores import assessment_pack
from fcrm_contracts.api.case_internal_assessment import (
    RecommendationEdits,
    RecommendationInternal,
)
from fcrm_contracts.enums import CaseState, ItemKind, Role
from fcrm_contracts.events.payloads import CaseLocked, CaseUnlocked

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"
ANALYST = "u-riyer"
REASON = "The vendor review changed a rating, so the numbers must be reopened."
RECOMMENDATION = f"/cases/{CASE}/recommendation"


def code(response: Any) -> str:
    return str(response.json()["type"]).rsplit("/", 1)[-1]


def events(service: CaseService, type_: str) -> list[dict[str, Any]]:
    return [e.payload for e in service.store.events if e.type == type_]


def stage(service: CaseService, state: CaseState) -> None:
    record = service.store._cases[CASE]
    service.store._cases[CASE] = record.model_copy(update={"state": state})


def decide_everything(service: CaseService) -> None:
    """Confirm every gated item and acknowledge nothing (the demo's items have no
    open acknowledgement unless a test adds one).
    """
    for row in list(service.views.items.values()):
        body = {"decision": "accept" if row.kind is ItemKind.CLAUSE else "confirm"}
        response = service.client.post(f"/cases/{CASE}/items/{row.item_id}", json=body)
        assert response.status_code == 200, response.text


@pytest.fixture
def recommendation(drafted_recommendation: CaseService) -> CaseService:
    """The demo case with the narrative's draft stored, in ``recommend``."""
    stage(drafted_recommendation, CaseState.RECOMMEND)
    return drafted_recommendation


@pytest.fixture
def ready(recommendation: CaseService) -> CaseService:
    """:func:`recommendation` with every item decided (decided while the case was in
    review, as H1 would have it, then moved on to ``recommend``).
    """
    stage(recommendation, CaseState.REVIEW)
    decide_everything(recommendation)
    stage(recommendation, CaseState.RECOMMEND)
    return recommendation


# ── who may call ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_only_the_analyst_reaches_the_recommendation(
    recommendation: CaseService,
    act_as: Callable[[Role, str], None],
    role: Role,
) -> None:
    act_as(role, "someone")
    client = recommendation.client

    statuses = [
        client.get(RECOMMENDATION).status_code,
        client.put(RECOMMENDATION, json={"outcome": "defer"}).status_code,
        client.post(f"{RECOMMENDATION}/lock", json={}).status_code,
        client.post(f"{RECOMMENDATION}/unlock", json={"reason": REASON}).status_code,
    ]

    assert statuses == [403, 403, 403, 403]


def test_an_analyst_who_does_not_hold_the_case_may_read_but_not_write(
    recommendation: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-mshah")
    client = recommendation.client

    assert client.get(RECOMMENDATION).status_code == 200
    assert client.put(RECOMMENDATION, json={"outcome": "defer"}).status_code == 403
    assert client.post(f"{RECOMMENDATION}/lock", json={}).status_code == 403
    assert (
        client.post(f"{RECOMMENDATION}/unlock", json={"reason": REASON}).status_code
        == 403
    )


# ── reading ─────────────────────────────────────────────────────────────────────


def test_the_draft_reads_as_one_narrative_with_the_outcome_its_conditions_imply(
    recommendation: CaseService,
) -> None:
    response = recommendation.client.get(RECOMMENDATION)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["narrative"] == (
        "Approve with three conditions.\n\nThe vendor review is the main residual risk."
    )
    assert body["outcome"] == "approve_with_conditions"
    assert body["drafted_by"] == "narrative agent"
    assert body["edits_by_analyst"] == 0
    assert body["business_summary"] == "Your change is approved with three conditions."
    assert body["restricted_scan"] == {"terms_found": 0, "terms": []}
    assert body["locked"] is False
    assert [c["id"] for c in body["conditions"]] == ["CND-1", "CND-2", "CND-3"]
    assert body["conditions"][0]["from"] == "overridden control CTL-T01"
    assert body["conditions"][1]["status"] == "scheduled"


def test_the_blockers_name_each_undecided_item_and_each_open_acknowledgement(
    recommendation: CaseService, make_ack_snapshot: Callable[..., Any]
) -> None:
    make_ack_snapshot("gate_band_2", band_changed=True)

    blockers = recommendation.client.get(RECOMMENDATION).json()["lock_blockers"]

    assert "Control CTL-T01: decide it" in blockers
    assert "Rating channel: decide it" in blockers
    assert "Band change: acknowledge it" in blockers


def test_a_case_with_no_draft_yet_is_not_ready(review_service: CaseService) -> None:
    response = review_service.client.get(RECOMMENDATION)

    assert response.status_code == 409
    assert code(response) == "not_ready"


def test_an_unknown_case_is_a_404(recommendation: CaseService) -> None:
    assert (
        recommendation.client.get("/cases/FCRM-2026-9999/recommendation").status_code
        == 404
    )


# ── editing ─────────────────────────────────────────────────────────────────────


def test_an_edit_is_stored_in_the_shape_the_pack_builder_reads(
    recommendation: CaseService,
) -> None:
    response = recommendation.client.put(
        RECOMMENDATION,
        json={
            "narrative": (
                "Approve with three conditions, subject to the vendor review.\n\n"
                "The vendor review is the main residual risk."
            ),
            "outcome": "approve_with_conditions",
            "business_summary": "Your change is approved. Three conditions apply.",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["narrative"].startswith("Approve with three conditions, subject to")
    assert (
        body["business_summary"] == "Your change is approved. Three conditions apply."
    )
    # The outcome equals what the conditions imply, so it is not an edit.
    assert body["edits_by_analyst"] == 2
    stored = recommendation.client.app.state.assessment.recommendations[CASE]  # type: ignore[attr-defined]
    edits = RecommendationEdits.model_validate(stored.edits)
    assert [s.key for s in edits.sections] == ["recommendation"]
    assert edits.business_summary == "Your change is approved. Three conditions apply."
    assert edits.outcome is None
    assert edits.edit_count == 2


def test_the_pack_builder_reads_back_exactly_what_the_analyst_sees(
    recommendation: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    recommendation.client.put(
        RECOMMENDATION,
        json={
            "narrative": "First.\n\nSecond.\n\nThird, a new paragraph.",
            "outcome": "defer",
            "business_summary": "We need more information.",
        },
    )
    web = recommendation.client.get(RECOMMENDATION).json()
    act_as(Role.SERVICE, "svc:caseflow")
    internal = recommendation.client.get(f"/internal/cases/{CASE}/recommendation")
    rec = RecommendationInternal.model_validate(internal.json())

    sections = assessment_pack.effective_sections(rec)

    assert "\n\n".join(text for _, text in sections) == web["narrative"]
    assert [key for key, _ in sections] == ["recommendation", "section_1", "section_2"]
    assert assessment_pack.effective_summary(rec) == web["business_summary"]
    assert [c.condition_id for c in assessment_pack.effective_conditions(rec)] == [
        c["id"] for c in web["conditions"]
    ]
    assert rec.edits is not None
    assert rec.edits.outcome is not None
    assert rec.edits.outcome.value == web["outcome"] == "defer"
    assert rec.edits.edit_count == web["edits_by_analyst"] == 3


def test_an_edit_that_changes_nothing_writes_nothing(
    recommendation: CaseService,
) -> None:
    current = recommendation.client.get(RECOMMENDATION).json()

    response = recommendation.client.put(
        RECOMMENDATION,
        json={
            "narrative": current["narrative"],
            "outcome": current["outcome"],
            "business_summary": current["business_summary"],
        },
    )

    assert response.status_code == 200
    assert response.json()["edits_by_analyst"] == 0
    assert [
        e
        for e in recommendation.store.activity.entries
        if e.type == "recommendation.edited"
    ] == []


def test_a_shorter_narrative_empties_the_sections_it_no_longer_has(
    recommendation: CaseService,
) -> None:
    response = recommendation.client.put(
        RECOMMENDATION, json={"narrative": "Approve, nothing else to add."}
    )

    assert response.status_code == 200
    assert response.json()["narrative"] == "Approve, nothing else to add."


def test_an_edit_says_something_and_knows_its_fields(
    recommendation: CaseService,
) -> None:
    client = recommendation.client

    assert client.put(RECOMMENDATION, json={}).status_code == 422
    assert client.put(RECOMMENDATION, json={"outcome": "maybe"}).status_code == 422
    assert client.put(RECOMMENDATION, json={"composite": 10.4}).status_code == 422


@pytest.mark.parametrize(
    "summary",
    [
        "The score is 9.8.",
        "Band 3 applies.",
        "A rating of 4.5 was used.",
        "CTL-T01 is weak.",
    ],
)
def test_a_business_summary_that_names_a_restricted_term_is_refused(
    recommendation: CaseService, summary: str
) -> None:
    response = recommendation.client.put(
        RECOMMENDATION, json={"business_summary": summary}
    )

    assert response.status_code == 422
    assert recommendation.client.get(RECOMMENDATION).json()["edits_by_analyst"] == 0


def test_the_summary_the_agent_withheld_is_reported_in_the_scan(
    recommendation: CaseService,
) -> None:
    stored = recommendation.client.app.state.assessment.recommendations[CASE]  # type: ignore[attr-defined]
    draft = {
        **stored.draft,
        "business_summary": {"text": "", "restricted_term_hits": 2},
    }
    recommendation.client.app.state.assessment.recommendations[CASE] = (  # type: ignore[attr-defined]
        stored.model_copy(update={"draft": draft})
    )

    body = recommendation.client.get(RECOMMENDATION).json()

    assert body["business_summary"] == ""
    assert body["restricted_scan"]["terms_found"] == 2


def test_an_edit_is_refused_once_locked_and_before_the_draft_stage(
    ready: CaseService,
) -> None:
    client = ready.client
    client.post(f"{RECOMMENDATION}/lock", json={})

    locked = client.put(RECOMMENDATION, json={"outcome": "defer"})

    assert locked.status_code == 409
    assert code(locked) == "recommendation_locked"


def test_an_edit_is_made_at_the_recommendation_stage(
    drafted_recommendation: CaseService,
) -> None:
    response = drafted_recommendation.client.put(
        RECOMMENDATION, json={"outcome": "defer"}
    )

    assert response.status_code == 409
    assert code(response) == "wrong_stage"


# ── locking ─────────────────────────────────────────────────────────────────────


def test_the_lock_is_refused_naming_each_blocker_from_s1s_own_state(
    recommendation: CaseService,
) -> None:
    response = recommendation.client.post(f"{RECOMMENDATION}/lock", json={})

    assert response.status_code == 409
    assert code(response) == "gate_required"
    messages = [e["msg"] for e in response.json()["errors"]]
    assert "Control CTL-T01: decide it" in messages
    assert events(recommendation, "case.locked") == []


def test_an_open_acknowledgement_blocks_the_lock(
    ready: CaseService, make_ack_snapshot: Callable[..., Any]
) -> None:
    make_ack_snapshot("gate_band_2", band_changed=True)

    response = ready.client.post(f"{RECOMMENDATION}/lock", json={})

    assert response.status_code == 409
    assert [e["msg"] for e in response.json()["errors"]] == [
        "Band change: acknowledge it"
    ]
    ready.client.post(f"/cases/{CASE}/acknowledge", json={"kind": "band_change"})
    assert ready.client.post(f"{RECOMMENDATION}/lock", json={}).status_code == 200


def test_locking_stores_the_lock_then_announces_case_locked(
    ready: CaseService, emit_probe: Any
) -> None:
    response = ready.client.post(f"{RECOMMENDATION}/lock", json={})

    assert response.status_code == 200, response.text
    assert response.json()["locked"] is True
    assert response.json()["lock_blockers"] == []
    row = ready.client.app.state.assessment.recommendations[CASE]  # type: ignore[attr-defined]
    assert row.locked is True
    assert row.locked_by == ANALYST
    [payload] = events(ready, "case.locked")
    assert CaseLocked.model_validate(payload) == CaseLocked(case_id=CASE, actor=ANALYST)


def test_locking_twice_replays_and_announces_once(ready: CaseService) -> None:
    first = ready.client.post(f"{RECOMMENDATION}/lock", json={})
    second = ready.client.post(f"{RECOMMENDATION}/lock", json={})

    assert first.status_code == second.status_code == 200
    assert len(events(ready, "case.locked")) == 1


def test_the_lock_waits_for_the_recommendation_stage(
    drafted_recommendation: CaseService,
) -> None:
    decide_everything(drafted_recommendation)

    response = drafted_recommendation.client.post(f"{RECOMMENDATION}/lock", json={})

    assert response.status_code == 409
    assert code(response) == "wrong_stage"


def test_there_is_nothing_to_lock_before_the_narrative(
    review_service: CaseService,
) -> None:
    response = review_service.client.post(f"{RECOMMENDATION}/lock", json={})

    assert response.status_code == 409
    assert code(response) == "not_ready"


def test_a_closed_case_cannot_be_locked_or_edited(ready: CaseService) -> None:
    stage(ready, CaseState.CLOSED)

    assert (
        ready.client.put(RECOMMENDATION, json={"outcome": "defer"}).status_code == 409
    )
    assert ready.client.post(f"{RECOMMENDATION}/lock", json={}).status_code == 409


# ── unlocking ───────────────────────────────────────────────────────────────────


def test_an_unlock_needs_a_reason_of_twenty_characters(ready: CaseService) -> None:
    ready.client.post(f"{RECOMMENDATION}/lock", json={})

    for body in ({}, {"reason": "too short"}, {"reason": "x" * 19}):
        assert (
            ready.client.post(f"{RECOMMENDATION}/unlock", json=body).status_code == 422
        )
    assert events(ready, "case.unlocked") == []


def test_unlocking_carries_its_reason_to_caseflow(ready: CaseService) -> None:
    ready.client.post(f"{RECOMMENDATION}/lock", json={})

    response = ready.client.post(f"{RECOMMENDATION}/unlock", json={"reason": REASON})

    assert response.status_code == 200, response.text
    assert response.json()["locked"] is False
    [payload] = events(ready, "case.unlocked")
    assert CaseUnlocked.model_validate(payload).unlock_reason == REASON
    row = ready.client.app.state.assessment.recommendations[CASE]  # type: ignore[attr-defined]
    assert row.locked is False
    assert row.unlock_reason == REASON
    assert row.unlocked_by == ANALYST


def test_unlocking_twice_with_one_reason_replays(ready: CaseService) -> None:
    ready.client.post(f"{RECOMMENDATION}/lock", json={})
    ready.client.post(f"{RECOMMENDATION}/unlock", json={"reason": REASON})

    again = ready.client.post(f"{RECOMMENDATION}/unlock", json={"reason": REASON})
    other = ready.client.post(
        f"{RECOMMENDATION}/unlock", json={"reason": REASON + " Another reason."}
    )

    assert again.status_code == 200
    assert other.status_code == 409
    assert code(other) == "not_locked"
    assert len(events(ready, "case.unlocked")) == 1


def test_an_unlocked_recommendation_has_nothing_to_unlock(ready: CaseService) -> None:
    response = ready.client.post(f"{RECOMMENDATION}/unlock", json={"reason": REASON})

    assert response.status_code == 409
    assert code(response) == "not_locked"


def test_once_the_pack_is_with_the_committee_the_analyst_cannot_unlock(
    ready: CaseService,
) -> None:
    ready.client.post(f"{RECOMMENDATION}/lock", json={})
    stage(ready, CaseState.DECIDE)

    response = ready.client.post(f"{RECOMMENDATION}/unlock", json={"reason": REASON})

    assert response.status_code == 409
    assert code(response) == "wrong_stage"
    assert events(ready, "case.unlocked") == []


def test_after_an_unlock_the_items_must_be_decided_again_before_the_next_lock(
    ready: CaseService,
) -> None:
    ready.client.post(f"{RECOMMENDATION}/lock", json={})
    ready.client.post(f"{RECOMMENDATION}/unlock", json={"reason": REASON})
    # What H1's unlock.supersede does: every item gets a new gate.
    for row in list(ready.views.items.values()):
        ready.views.items[row.item_id] = row.model_copy(
            update={"gate_id": f"{row.gate_id}@unlock"}
        )
    stage(ready, CaseState.REVIEW)

    refused = ready.client.post(f"{RECOMMENDATION}/lock", json={})
    stage(ready, CaseState.REVIEW)
    decide_everything(ready)
    stage(ready, CaseState.RECOMMEND)
    relocked = ready.client.post(f"{RECOMMENDATION}/lock", json={})

    assert refused.status_code == 409
    assert code(refused) == "gate_required"
    assert relocked.status_code == 200
    assert len(events(ready, "case.locked")) == 2


# ── the scene ───────────────────────────────────────────────────────────────────


def test_scene_14_to_15_items_override_acknowledge_lock_pack_unlock(
    drafted_recommendation: CaseService,
    make_ack_snapshot: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    service = drafted_recommendation
    client = service.client
    items = {
        (r.kind, r.subject.get("control_id") or r.subject.get("domain")): r
        for r in service.views.items.values()
        if not r.subject.get("planned")
    }
    t01 = items[(ItemKind.CONTROL, "CTL-T01")]
    # Scene 13: every item is decided one at a time; CTL-T01 is overridden.
    for key, row in items.items():
        if row is t01:
            continue
        decided = client.post(
            f"/cases/{CASE}/items/{row.item_id}", json={"decision": "confirm"}
        )
        assert decided.status_code == 200, key
    planned = next(r for r in service.views.items.values() if r.subject.get("planned"))
    assert (
        client.post(
            f"/cases/{CASE}/items/{planned.item_id}", json={"decision": "confirm"}
        ).status_code
        == 200
    )
    override = client.post(
        f"/cases/{CASE}/items/{t01.item_id}",
        json={"decision": "override", "value": 4.5, "reason": REASON},
    )
    assert override.status_code == 200
    # Scene 14: H1 re-scored, the band changed, an acknowledgement is open.
    make_ack_snapshot("gate_band_change", band_changed=True)
    blocked = client.post(f"{RECOMMENDATION}/lock", json={})
    assert blocked.status_code == 409
    assert (
        client.post(
            f"/cases/{CASE}/acknowledge",
            json={"kind": "band_change", "note": "Band 3 routes to the committee."},
        ).status_code
        == 202
    )
    # Scene 15: H1 moves on to the narrative; the analyst edits and locks.
    stage(service, CaseState.RECOMMEND)
    assert (
        client.put(
            RECOMMENDATION, json={"outcome": "approve_with_conditions"}
        ).status_code
        == 200
    )
    locked = client.post(f"{RECOMMENDATION}/lock", json={})
    assert locked.status_code == 200
    # H1's lock.build_pack reads S1's recommendation as the service account.
    act_as(Role.SERVICE, "svc:caseflow")
    rec = RecommendationInternal.model_validate(
        client.get(f"/internal/cases/{CASE}/recommendation").json()
    )
    act_as(Role.ANALYST, ANALYST)
    assert rec.locked is True
    assert rec.locked_by == ANALYST
    # The post-lock change (F10): unlock with a reason, both packs kept by H1.
    unlocked = client.post(f"{RECOMMENDATION}/unlock", json={"reason": REASON})
    assert unlocked.status_code == 200

    types = [e.type for e in service.store.events]
    # One gate.decided per item and one for the acknowledgement, then the lock.
    assert types.count("gate.decided") == len(service.views.items) + 1
    assert types[-2:] == ["case.locked", "case.unlocked"]

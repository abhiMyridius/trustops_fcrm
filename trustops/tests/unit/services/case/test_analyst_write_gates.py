"""S1's analyst gate routes: ``POST items/{item_id}`` (Gate A, one item per call),
``POST acknowledge`` (R3) and ``POST screening`` (R4).

What these tests hold: only the analyst who holds the case may decide; a
decision is stored *before* ``gate.decided`` is enqueued, in the shape Caseflow's
handler reads; the same decision twice replays and a different one is a 409; the
stage, the kind and the reason rules refuse what would be dead-lettered or
unexplained; and a planned control is never given a rating.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.assessment import build_score_view
from fcrm_case.domain.views import ReviewItemRow
from fcrm_contracts.enums import CaseState, ItemKind, ItemStatus, Role
from fcrm_contracts.events.payloads import GateDecided

if TYPE_CHECKING:
    from conftest import CaseService, EmitProbe

CASE = "FCRM-2026-0151"
ANALYST = "u-riyer"
REASON = "Audit predates the corridor arrangement and covers domestic scope only."


def code(response: Any) -> str:
    return str(response.json()["type"]).rsplit("/", 1)[-1]


def gate_events(service: CaseService) -> list[dict[str, Any]]:
    return [e.payload for e in service.store.events if e.type == "gate.decided"]


def item_where(service: CaseService, kind: ItemKind, **subject: Any) -> ReviewItemRow:
    return next(
        r
        for r in service.views.items.values()
        if r.kind is kind and all(r.subject.get(k) == v for k, v in subject.items())
    )


def scored_t01(service: CaseService) -> ReviewItemRow:
    return item_where(service, ItemKind.CONTROL, control_id="CTL-T01", planned=False)


def planned_t01(service: CaseService) -> ReviewItemRow:
    return item_where(service, ItemKind.CONTROL, control_id="CTL-T01", planned=True)


def post_item(service: CaseService, row: ReviewItemRow, body: Any) -> Any:
    return service.client.post(f"/cases/{CASE}/items/{row.item_id}", json=body)


# ── who may call ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_only_the_analyst_may_decide_a_gate(
    review_service: CaseService, act_as: Callable[[Role, str], None], role: Role
) -> None:
    row = scored_t01(review_service)
    act_as(role, "someone")

    responses = [
        post_item(review_service, row, {"decision": "confirm"}),
        review_service.client.post(
            f"/cases/{CASE}/acknowledge", json={"kind": "boundary"}
        ),
        review_service.client.post(
            f"/cases/{CASE}/screening", json={"decision": "confirm_fast_lane"}
        ),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403]
    assert gate_events(review_service) == []


def test_an_analyst_who_does_not_hold_the_case_may_not_decide_it(
    review_service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-mshah")

    response = post_item(
        review_service, scored_t01(review_service), {"decision": "confirm"}
    )

    assert response.status_code == 403
    assert gate_events(review_service) == []


def test_a_closed_case_takes_no_decision(review_service: CaseService) -> None:
    record = review_service.store._cases[CASE]
    review_service.store._cases[CASE] = record.model_copy(
        update={"state": CaseState.CLOSED}
    )

    response = post_item(
        review_service, scored_t01(review_service), {"decision": "confirm"}
    )

    assert response.status_code == 409
    assert code(response) == "case_closed"


# ── bodies ──────────────────────────────────────────────────────────────────────


def test_an_array_body_is_refused_so_there_is_no_bulk_accept(
    review_service: CaseService,
) -> None:
    row = scored_t01(review_service)

    response = review_service.client.post(
        f"/cases/{CASE}/items/{row.item_id}",
        json=[{"decision": "confirm"}, {"decision": "confirm"}],
    )

    assert response.status_code == 422
    assert gate_events(review_service) == []


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "override", "value": 4.5},
        {"decision": "override", "value": 4.5, "reason": "too short"},
        {"decision": "remove", "reason": "x" * 19},
        {"decision": "explain"},
    ],
)
def test_a_decision_that_changes_something_needs_a_reason_of_twenty_characters(
    review_service: CaseService, body: dict[str, Any]
) -> None:
    response = post_item(review_service, scored_t01(review_service), body)

    assert response.status_code == 422
    assert gate_events(review_service) == []


def test_an_unknown_field_or_decision_is_refused(review_service: CaseService) -> None:
    row = scored_t01(review_service)

    assert (
        post_item(review_service, row, {"decision": "bulk_accept"}).status_code == 422
    )
    assert (
        post_item(
            review_service, row, {"decision": "confirm", "composite": 10.4}
        ).status_code
        == 422
    )


# ── the id the web posts ────────────────────────────────────────────────────────


def test_the_web_posts_the_control_id_the_read_view_shows_not_the_item_id(
    review_service: CaseService,
) -> None:
    """The read views give a control its ``CTL-`` id and a clause its clause id
    as ``id`` (the real ``itm_`` id travels as ``item_id``); the web posts that
    ``id``, so the write route must find the item by it (first live run: 404).
    """
    row = scored_t01(review_service)

    response = review_service.client.post(
        f"/cases/{CASE}/items/CTL-T01",
        json={"decision": "override", "value": 4.5, "reason": REASON},
    )

    assert response.status_code == 200, response.text
    assert response.json()["item_id"] == row.item_id
    assert review_service.views.items[row.item_id].status is ItemStatus.OVERRIDDEN
    assert [e["decision"] for e in gate_events(review_service)] == ["override"]


def test_the_web_posts_the_clause_id_the_read_view_shows(
    review_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    clause = make_review_item(
        ItemKind.CLAUSE,
        {"clause_id": "AML-12", "domain": "channel", "corpus_v": 14},
        {"quote": "q", "applicability": "a", "confidence": 0.9},
    )
    review_service.views.items[clause.item_id] = clause

    response = review_service.client.post(
        f"/cases/{CASE}/items/AML-12", json={"decision": "accept"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["item_id"] == clause.item_id


def test_a_control_id_with_no_item_is_not_found(review_service: CaseService) -> None:
    response = review_service.client.post(
        f"/cases/{CASE}/items/CTL-X99", json={"decision": "confirm"}
    )

    assert response.status_code == 404
    assert gate_events(review_service) == []


# ── the override, the golden decision ───────────────────────────────────────────


def test_overriding_ctl_t01_is_stored_then_emitted_in_the_shape_h1_reads(
    review_service: CaseService, emit_probe: EmitProbe
) -> None:
    row = scored_t01(review_service)
    assert row.status is ItemStatus.PROPOSED

    response = post_item(
        review_service,
        row,
        {"decision": "override", "value": 4.5, "reason": REASON},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "item_id": row.item_id,
        "status": "overridden",
        "replayed": False,
    }
    stored = review_service.views.items[row.item_id]
    assert stored.status is ItemStatus.OVERRIDDEN
    assert stored.decided is not None
    assert stored.decided.value == {"rating": 4.5}
    assert stored.decided.reason == REASON
    assert stored.decided.by == ANALYST
    # Store before emit: when gate.decided reached the outbox the item was decided.
    assert [s[row.item_id] for s in emit_probe.statuses] == ["overridden"]
    [payload] = gate_events(review_service)
    event = GateDecided.model_validate(payload)
    assert event.gate_id == row.gate_id
    assert event.gate_type.value == "review_item"
    assert event.run_id == "run_1"
    assert event.decision == "override"
    assert event.decided_by == ANALYST
    assert event.reason == REASON
    assert event.before == {"rating": 7.6}
    assert event.after == {"rating": 4.5}
    assert event.rating_or_control_changed is True
    assert event.subject == {"kind": "control", "id": row.item_id}
    assert event.item_kind is ItemKind.CONTROL
    assert event.role is Role.ANALYST


def test_the_override_moves_the_score_request_s1_builds_to_4_5(
    review_service: CaseService,
) -> None:
    row = scored_t01(review_service)
    post_item(
        review_service,
        row,
        {"decision": "override", "value": 4.5, "reason": REASON},
    )

    view = build_score_view(CASE, 3, list(review_service.views.items.values()))

    channel = next(d for d in view.request.domains if d.domain.value == "channel")
    rating = next(c.rating for c in channel.controls if c.control_id == "CTL-T01")
    assert rating == 4.5
    assert view.overrides[0].proposed == {"rating": 7.6}
    assert view.overrides[0].reason == REASON


def test_a_planned_control_is_never_given_a_rating(
    review_service: CaseService,
) -> None:
    row = planned_t01(review_service)

    response = post_item(
        review_service,
        row,
        {"decision": "override", "value": 4.5, "reason": REASON},
    )

    assert response.status_code == 409
    assert code(response) == "decision_not_allowed"
    assert gate_events(review_service) == []


def test_overriding_to_the_proposed_value_is_refused_confirm_it_instead(
    review_service: CaseService,
) -> None:
    response = post_item(
        review_service,
        scored_t01(review_service),
        {"decision": "override", "value": 7.6, "reason": REASON},
    )

    assert response.status_code == 422
    assert gate_events(review_service) == []


def test_a_rating_override_takes_a_likelihood_and_an_impact(
    review_service: CaseService,
) -> None:
    factor = item_where(review_service, ItemKind.FACTOR, domain="channel")

    refused = post_item(
        review_service,
        factor,
        {"decision": "override", "value": 3, "reason": REASON},
    )
    accepted = post_item(
        review_service,
        factor,
        {"decision": "override", "likelihood": 2, "impact": 5, "reason": REASON},
    )

    assert refused.status_code == 422
    assert accepted.status_code == 200, accepted.text
    [payload] = gate_events(review_service)
    assert payload["after"] == {"likelihood": 2, "impact": 5}
    assert payload["rating_or_control_changed"] is True


# ── the other decisions ─────────────────────────────────────────────────────────


def test_confirming_a_control_changes_no_input(review_service: CaseService) -> None:
    row = scored_t01(review_service)

    response = post_item(review_service, row, {"decision": "confirm"})

    assert response.status_code == 200
    [payload] = gate_events(review_service)
    assert payload["before"] is None
    assert payload["after"] is None
    assert payload["rating_or_control_changed"] is False
    assert review_service.views.items[row.item_id].status is ItemStatus.CONFIRMED


def test_removing_a_scored_control_changes_an_input_and_carries_a_reason(
    review_service: CaseService,
) -> None:
    row = scored_t01(review_service)

    response = post_item(review_service, row, {"decision": "remove", "reason": REASON})

    assert response.status_code == 200
    [payload] = gate_events(review_service)
    assert payload["before"] == {"rating": 7.6}
    assert payload["after"] is None
    assert payload["rating_or_control_changed"] is True
    assert review_service.views.items[row.item_id].status is ItemStatus.REMOVED


def test_a_clause_is_accepted_or_removed_and_never_overridden(
    review_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    clause = make_review_item(
        ItemKind.CLAUSE,
        {"clause_id": "AML-12", "domain": "channel", "corpus_v": 14},
        {"quote": "q", "applicability": "a", "confidence": 0.9},
    )
    review_service.views.items[clause.item_id] = clause

    refused = post_item(
        review_service,
        clause,
        {"decision": "override", "value": 1, "reason": REASON},
    )
    accepted = post_item(review_service, clause, {"decision": "accept"})

    assert code(refused) == "decision_not_allowed"
    assert accepted.status_code == 200
    assert review_service.views.items[clause.item_id].status is ItemStatus.CONFIRMED


def test_a_critic_finding_is_explained_with_a_reason(
    review_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    finding = make_review_item(
        ItemKind.CRITIC_FINDING,
        {
            "finding_type": "precedent_deviation",
            "severity": "high",
            "record_refs": ["30"],
            "text": "CTL-T01 7.6 against 4.9",
            "confidence": 0.8,
            "quote": "q",
        },
        {},
    )
    review_service.views.items[finding.item_id] = finding

    response = post_item(
        review_service, finding, {"decision": "explain", "reason": REASON}
    )

    assert response.status_code == 200
    assert review_service.views.items[finding.item_id].status is ItemStatus.EXPLAINED


def test_an_intake_item_is_not_decided_here(
    review_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    category = make_review_item(ItemKind.CATEGORY, {"change_type": "new_geography"}, {})
    review_service.views.items[category.item_id] = category

    response = post_item(review_service, category, {"decision": "confirm"})

    assert response.status_code == 409
    assert code(response) == "decision_not_allowed"


# ── states ──────────────────────────────────────────────────────────────────────


def test_an_item_is_not_decided_before_review_has_started(
    review_service: CaseService,
) -> None:
    record = review_service.store._cases[CASE]
    review_service.store._cases[CASE] = record.model_copy(
        update={"state": CaseState.COMPLETENESS}
    )

    response = post_item(
        review_service, scored_t01(review_service), {"decision": "confirm"}
    )

    assert response.status_code == 409
    assert code(response) == "wrong_stage"
    assert gate_events(review_service) == []


def test_a_superseded_item_cannot_be_decided(
    review_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    old = scored_t01(review_service)
    newer = make_review_item(
        ItemKind.CONTROL,
        dict(old.subject),
        dict(old.proposed),
        supersedes=old.item_id,
        revision="r2",
    )
    review_service.views.items[newer.item_id] = newer

    stale = post_item(review_service, old, {"decision": "confirm"})
    current = post_item(review_service, newer, {"decision": "confirm"})

    assert stale.status_code == 409
    assert code(stale) == "item_superseded"
    assert current.status_code == 200


def test_an_item_without_a_gate_cannot_be_decided(
    review_service: CaseService,
) -> None:
    row = scored_t01(review_service)
    review_service.views.items[row.item_id] = row.model_copy(update={"gate_id": None})

    response = post_item(review_service, row, {"decision": "confirm"})

    assert response.status_code == 409
    assert code(response) == "decision_not_allowed"


def test_an_unknown_item_is_a_404(review_service: CaseService) -> None:
    response = review_service.client.post(
        f"/cases/{CASE}/items/itm_missing", json={"decision": "confirm"}
    )

    assert response.status_code == 404


def test_a_case_whose_run_is_not_open_is_not_ready(
    review_service: CaseService,
) -> None:
    record = review_service.store._cases[CASE]
    review_service.store._cases[CASE] = record.model_copy(update={"run_id": None})
    row = scored_t01(review_service)
    review_service.views.items[row.item_id] = row.model_copy(update={"run_id": None})

    response = post_item(review_service, row, {"decision": "confirm"})

    assert response.status_code == 409
    assert code(response) == "not_ready"


# ── replays ─────────────────────────────────────────────────────────────────────


def test_the_same_decision_twice_replays_and_emits_once(
    review_service: CaseService,
) -> None:
    row = scored_t01(review_service)
    body = {"decision": "override", "value": 4.5, "reason": REASON}

    first = post_item(review_service, row, body)
    second = post_item(review_service, row, body)

    assert first.json()["replayed"] is False
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert len(gate_events(review_service)) == 1


def test_a_different_decision_on_a_decided_gate_is_a_conflict(
    review_service: CaseService,
) -> None:
    row = scored_t01(review_service)
    post_item(review_service, row, {"decision": "confirm"})

    response = post_item(
        review_service, row, {"decision": "override", "value": 4.5, "reason": REASON}
    )

    assert response.status_code == 409
    assert code(response) == "gate_already_decided"
    assert len(gate_events(review_service)) == 1
    assert review_service.views.items[row.item_id].status is ItemStatus.CONFIRMED


def test_after_an_unlock_a_new_gate_takes_a_new_decision(
    review_service: CaseService,
) -> None:
    row = scored_t01(review_service)
    post_item(review_service, row, {"decision": "confirm"})
    review_service.views.items[row.item_id] = review_service.views.items[
        row.item_id
    ].model_copy(update={"gate_id": "gate_after_unlock"})

    response = post_item(
        review_service, row, {"decision": "override", "value": 4.5, "reason": REASON}
    )

    assert response.status_code == 200, response.text
    assert response.json()["replayed"] is False
    assert [p["gate_id"] for p in gate_events(review_service)] == [
        row.gate_id,
        "gate_after_unlock",
    ]


def test_each_decision_leaves_one_analyst_activity_row(
    review_service: CaseService,
) -> None:
    row = scored_t01(review_service)

    post_item(
        review_service, row, {"decision": "override", "value": 4.5, "reason": REASON}
    )
    post_item(
        review_service, row, {"decision": "override", "value": 4.5, "reason": REASON}
    )

    [entry] = [
        e for e in review_service.store.activity.entries if e.type == "item.decided"
    ]
    assert entry.visibility == "analyst"
    assert entry.detail["decision"] == "override"
    assert REASON not in entry.summary


# ── acknowledgements (R3) ───────────────────────────────────────────────────────


def test_acknowledging_with_no_open_gate_is_a_409(review_service: CaseService) -> None:
    response = review_service.client.post(
        f"/cases/{CASE}/acknowledge", json={"kind": "boundary"}
    )

    assert response.status_code == 409
    assert code(response) == "no_open_gate"


def test_a_band_change_is_acknowledged_with_the_gate_id_the_score_recorded(
    review_service: CaseService, make_ack_snapshot: Callable[..., Any]
) -> None:
    make_ack_snapshot("gate_boundary_1", band_changed=False, n=1)
    make_ack_snapshot("gate_band_2", band_changed=True, n=2)

    response = review_service.client.post(
        f"/cases/{CASE}/acknowledge",
        json={"kind": "band_change", "note": "Band 3 now routes to the committee."},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {
        "kind": "band_change",
        "acknowledged": 1,
        "replayed": False,
    }
    [payload] = gate_events(review_service)
    event = GateDecided.model_validate(payload)
    assert event.gate_id == "gate_band_2"
    assert event.gate_type.value == "ack"
    assert event.subject == {"kind": "ack", "id": "band_change"}
    assert event.reason == "Band 3 now routes to the committee."
    # The boundary gate is a different kind and stays open.
    again = review_service.client.post(
        f"/cases/{CASE}/acknowledge", json={"kind": "boundary"}
    )
    assert again.json()["acknowledged"] == 1
    assert [p["gate_id"] for p in gate_events(review_service)] == [
        "gate_band_2",
        "gate_boundary_1",
    ]


def test_acknowledging_twice_replays_and_emits_once(
    review_service: CaseService, make_ack_snapshot: Callable[..., Any]
) -> None:
    make_ack_snapshot("gate_boundary_1")

    first = review_service.client.post(
        f"/cases/{CASE}/acknowledge", json={"kind": "boundary"}
    )
    second = review_service.client.post(
        f"/cases/{CASE}/acknowledge", json={"kind": "boundary"}
    )

    assert first.json()["acknowledged"] == 1
    assert second.status_code == 202
    assert second.json() == {"kind": "boundary", "acknowledged": 0, "replayed": True}
    assert len(gate_events(review_service)) == 1


def test_every_open_gate_of_a_kind_is_acknowledged_by_one_call(
    review_service: CaseService, make_ack_snapshot: Callable[..., Any]
) -> None:
    make_ack_snapshot("gate_band_1", band_changed=True, n=1)
    make_ack_snapshot("gate_band_2", band_changed=True, n=2)

    response = review_service.client.post(
        f"/cases/{CASE}/acknowledge", json={"kind": "band_change"}
    )

    assert response.json()["acknowledged"] == 2
    assert [p["gate_id"] for p in gate_events(review_service)] == [
        "gate_band_1",
        "gate_band_2",
    ]


# ── the fast lane (R4) ──────────────────────────────────────────────────────────


@pytest.fixture
def screening_view(review_service: CaseService, assessment_store: Any) -> CaseService:
    from fcrm_case.domain.assessment import ScreeningViewRow

    assessment_store.screening[CASE] = ScreeningViewRow(
        case_id=CASE,
        run_id="run_1",
        fast_lane_proposed=True,
        gate_id="gate_fast_lane",
        updated_at=datetime(2026, 10, 5, 9, tzinfo=UTC),
    )
    record = review_service.store._cases[CASE]
    review_service.store._cases[CASE] = record.model_copy(
        update={"state": CaseState.SCREENING}
    )
    return review_service


def test_the_fast_lane_is_confirmed_as_confirm_which_is_what_h1_reads(
    screening_view: CaseService,
) -> None:
    response = screening_view.client.post(
        f"/cases/{CASE}/screening", json={"decision": "confirm_fast_lane"}
    )

    assert response.status_code == 202, response.text
    [payload] = gate_events(screening_view)
    assert payload["gate_type"] == "fast_lane_confirm"
    assert payload["gate_id"] == "gate_fast_lane"
    assert payload["decision"] == "confirm"


def test_declining_the_fast_lane_needs_a_reason(screening_view: CaseService) -> None:
    refused = screening_view.client.post(
        f"/cases/{CASE}/screening", json={"decision": "decline"}
    )
    accepted = screening_view.client.post(
        f"/cases/{CASE}/screening", json={"decision": "decline", "reason": REASON}
    )

    assert refused.status_code == 422
    assert accepted.status_code == 202
    assert gate_events(screening_view)[0]["decision"] == "decline"


def test_the_fast_lane_is_decided_once_and_replays(
    screening_view: CaseService,
) -> None:
    body = {"decision": "confirm_fast_lane"}

    first = screening_view.client.post(f"/cases/{CASE}/screening", json=body)
    second = screening_view.client.post(f"/cases/{CASE}/screening", json=body)
    other = screening_view.client.post(
        f"/cases/{CASE}/screening", json={"decision": "decline", "reason": REASON}
    )

    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert other.status_code == 409
    assert code(other) == "gate_already_decided"
    assert len(gate_events(screening_view)) == 1


def test_no_fast_lane_proposal_means_nothing_to_decide(
    review_service: CaseService,
) -> None:
    response = review_service.client.post(
        f"/cases/{CASE}/screening", json={"decision": "confirm_fast_lane"}
    )

    assert response.status_code == 409
    assert code(response) == "no_open_gate"


def test_the_fast_lane_is_decided_at_screening_only(
    screening_view: CaseService,
) -> None:
    record = screening_view.store._cases[CASE]
    screening_view.store._cases[CASE] = record.model_copy(
        update={"state": CaseState.REVIEW}
    )

    response = screening_view.client.post(
        f"/cases/{CASE}/screening", json={"decision": "confirm_fast_lane"}
    )

    assert response.status_code == 409
    assert code(response) == "wrong_stage"

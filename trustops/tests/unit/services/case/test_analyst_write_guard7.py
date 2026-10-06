"""What S1 emits for every analyst gate is what Caseflow's Guard 7 accepts.

Caseflow validates each ``gate.decided`` against the gate it opened
(``fcrm_guardrails.human_gate.validate_gate_decision``): the same gate, type, case
and run, the gate's required role, a ``decided_by`` that is the principal, a reason
where a decision needs one, and rule 6 where a value changed. S1 builds its events
by hand from the item, the view or the score snapshot that carries the gate id, so
this test replays every gate route's event through the real guard, built the way the
engine builds its principal (from the gate's own required role), and requires a pass.
It also holds the one place S1 and Caseflow could silently disagree: the decision
strings Caseflow reads (``confirm`` is the only fast lane it treats as confirmed).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fcrm_contracts.common import GateDecision, Principal
from fcrm_contracts.enums import GateStatus, GateType, ItemKind, Role
from fcrm_guardrails.human_gate import GateRecord, validate_gate_decision

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"
REASON = "Audit predates the corridor arrangement and covers domestic scope only."
KNOWN = set(GateDecision.model_fields)


def verdict(payload: dict[str, Any]) -> Any:
    """Guard 7's answer for ``payload`` against a gate opened as it names, with the
    principal the engine synthesises (the gate's required role, an access token).
    """
    decision = GateDecision.model_validate(
        {k: v for k, v in payload.items() if k in KNOWN}
    )
    gate = GateRecord(
        gate_id=decision.gate_id,
        gate_type=decision.gate_type,
        case_id=decision.case_id,
        run_id=decision.run_id,
        required_role=Role.ANALYST,
        status=GateStatus.OPEN,
        subject=payload["subject"],
    )
    principal = Principal(
        sub=decision.decided_by, roles=[Role.ANALYST], name="x", typ="access"
    )
    return validate_gate_decision(gate, decision, principal)


def gate_payloads(service: CaseService) -> list[dict[str, Any]]:
    return [e.payload for e in service.store.events if e.type == "gate.decided"]


def scored(service: CaseService) -> Any:
    return next(
        r
        for r in service.views.items.values()
        if r.kind is ItemKind.CONTROL
        and r.subject.get("control_id") == "CTL-T01"
        and not r.subject.get("planned")
    )


def test_every_item_decision_passes_guard_7(review_service: CaseService) -> None:
    client = review_service.client
    items = [
        r
        for r in review_service.views.items.values()
        if r.kind in (ItemKind.CONTROL, ItemKind.FACTOR)
    ]
    t01 = scored(review_service)
    client.post(
        f"/cases/{CASE}/items/{t01.item_id}",
        json={"decision": "override", "value": 4.5, "reason": REASON},
    )
    others = [r for r in items if r.item_id != t01.item_id]
    client.post(
        f"/cases/{CASE}/items/{others[0].item_id}", json={"decision": "confirm"}
    )
    client.post(
        f"/cases/{CASE}/items/{others[1].item_id}",
        json={"decision": "remove", "reason": REASON},
    )

    payloads = gate_payloads(review_service)

    assert [p["decision"] for p in payloads] == ["override", "confirm", "remove"]
    for payload in payloads:
        result = verdict(payload)
        assert result.passed, result.summary


def test_a_clause_acceptance_and_a_critic_explanation_pass_guard_7(
    review_service: CaseService, make_review_item: Callable[..., Any]
) -> None:
    clause = make_review_item(
        ItemKind.CLAUSE,
        {"clause_id": "AML-12", "domain": "channel", "corpus_v": 14},
        {"quote": "q", "applicability": "a", "confidence": 0.9},
    )
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
    for row in (clause, finding):
        review_service.views.items[row.item_id] = row
    client = review_service.client
    client.post(f"/cases/{CASE}/items/{clause.item_id}", json={"decision": "accept"})
    client.post(
        f"/cases/{CASE}/items/{finding.item_id}",
        json={"decision": "explain", "reason": REASON},
    )

    for payload in gate_payloads(review_service):
        result = verdict(payload)
        assert result.passed, result.summary


def test_an_acknowledgement_passes_guard_7(
    review_service: CaseService, make_ack_snapshot: Callable[..., Any]
) -> None:
    make_ack_snapshot("gate_band", band_changed=True)
    review_service.client.post(
        f"/cases/{CASE}/acknowledge", json={"kind": "band_change"}
    )

    [payload] = gate_payloads(review_service)

    assert payload["gate_type"] == GateType.ACK.value
    assert verdict(payload).passed


def test_the_completeness_gate_passes_guard_7_either_way(
    clarification_service: CaseService,
) -> None:
    client = clarification_service.client
    client.post(f"/cases/{CASE}/completeness", json={"decision": "ask"})

    [payload] = gate_payloads(clarification_service)

    assert payload["gate_type"] == GateType.COMPLETENESS.value
    assert verdict(payload).passed


def test_a_proceed_passes_guard_7(clarification_service: CaseService) -> None:
    clarification_service.client.post(
        f"/cases/{CASE}/completeness", json={"decision": "proceed"}
    )

    [payload] = gate_payloads(clarification_service)

    assert verdict(payload).passed


def test_the_fast_lane_decisions_are_the_words_caseflow_reads(
    review_service: CaseService, assessment_store: Any
) -> None:
    from datetime import UTC, datetime

    from fcrm_case.domain.assessment import ScreeningViewRow
    from fcrm_contracts.enums import CaseState

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
    review_service.client.post(
        f"/cases/{CASE}/screening", json={"decision": "confirm_fast_lane"}
    )

    [payload] = gate_payloads(review_service)

    # Caseflow treats exactly these as confirmed: confirm, confirmed, true, yes.
    assert payload["decision"] == "confirm"
    assert verdict(payload).passed


def test_a_decline_with_its_reason_passes_guard_7(
    review_service: CaseService, assessment_store: Any
) -> None:
    from datetime import UTC, datetime

    from fcrm_case.domain.assessment import ScreeningViewRow
    from fcrm_contracts.enums import CaseState

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
    review_service.client.post(
        f"/cases/{CASE}/screening", json={"decision": "decline", "reason": REASON}
    )

    [payload] = gate_payloads(review_service)

    assert payload["decision"] == "decline"
    assert verdict(payload).passed


def test_the_guard_is_not_vacuous_a_short_reason_on_an_override_fails_it(
    review_service: CaseService,
) -> None:
    t01 = scored(review_service)
    review_service.client.post(
        f"/cases/{CASE}/items/{t01.item_id}",
        json={"decision": "override", "value": 4.5, "reason": REASON},
    )
    [payload] = gate_payloads(review_service)

    assert not verdict({**payload, "reason": "short"}).passed
    assert not verdict({**payload, "reason": None}).passed

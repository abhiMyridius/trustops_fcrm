"""Tests for :mod:`fcrm_caseflow.gates`: opening, counting and deciding
gates, including Guard 7 refusals (:class:`GateDecisionError`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

import pytest
from fcrm_caseflow.gates import GateDecisionError, GateStore, gate_document
from fcrm_contracts.common import GateDecision, Principal
from fcrm_contracts.enums import GateStatus, GateType, Role

CASE_ID = "FCRM-2026-0151"
RUN_ID = "run_1"


def _decision(
    *,
    gate_id: str,
    gate_type: GateType = GateType.REVIEW_ITEM,
    decision: str = "confirm",
    reason: Optional[str] = None,
    decided_by: str = "u-riyer",
) -> GateDecision:
    return GateDecision(
        gate_id=gate_id,
        gate_type=gate_type,
        case_id=CASE_ID,
        run_id=RUN_ID,
        decision=decision,
        reason=reason,
        decided_by=decided_by,
        decided_at=datetime.now(UTC),
    )


def _principal(role: Role, sub: str = "u-riyer") -> Principal:
    return Principal(sub=sub, roles=[role], name=sub, typ="access")


@pytest.fixture
def store(gate_store: GateStore) -> GateStore:
    """Alias for this file's ``gate_store`` fixture (``conftest.py``)."""
    return gate_store


async def test_open_then_count_open_sees_it(store: GateStore) -> None:
    await store.open(
        gate_document(
            gate_id="gate_1",
            case_id=CASE_ID,
            run_id=RUN_ID,
            gate_type=GateType.PO_CONFIRM_CATEGORY,
            required_role=Role.PO,
        )
    )
    assert await store.count_open(CASE_ID, GateType.PO_CONFIRM_CATEGORY) == 1
    assert await store.count_open(CASE_ID, GateType.REVIEW_ITEM) == 0


async def test_deciding_a_gate_removes_it_from_the_open_count(
    store: GateStore,
) -> None:
    await store.open(
        gate_document(
            gate_id="gate_1",
            case_id=CASE_ID,
            run_id=RUN_ID,
            gate_type=GateType.FAST_LANE_CONFIRM,
            required_role=Role.ANALYST,
        )
    )
    await store.decide(
        _decision(gate_id="gate_1", gate_type=GateType.FAST_LANE_CONFIRM),
        _principal(Role.ANALYST),
    )
    assert await store.count_open(CASE_ID, GateType.FAST_LANE_CONFIRM) == 0


async def test_deciding_an_unknown_gate_raises(store: GateStore) -> None:
    with pytest.raises(GateDecisionError):
        await store.decide(_decision(gate_id="no-such-gate"), _principal(Role.ANALYST))


async def test_deciding_with_the_wrong_role_is_refused(store: GateStore) -> None:
    await store.open(
        gate_document(
            gate_id="gate_1",
            case_id=CASE_ID,
            run_id=RUN_ID,
            gate_type=GateType.PO_CONFIRM_CATEGORY,
            required_role=Role.PO,
        )
    )
    with pytest.raises(GateDecisionError):
        await store.decide(
            _decision(gate_id="gate_1", gate_type=GateType.PO_CONFIRM_CATEGORY),
            _principal(Role.ANALYST),
        )
    # still open: the refused decision never mutated it.
    assert await store.count_open(CASE_ID, GateType.PO_CONFIRM_CATEGORY) == 1


async def test_a_vote_decision_without_a_reason_is_refused(store: GateStore) -> None:
    await store.open(
        gate_document(
            gate_id="gate_1",
            case_id=CASE_ID,
            run_id=RUN_ID,
            gate_type=GateType.VOTE,
            required_role=Role.COMMITTEE,
        )
    )
    with pytest.raises(GateDecisionError):
        await store.decide(
            _decision(
                gate_id="gate_1",
                gate_type=GateType.VOTE,
                decision="approve",
                reason=None,
                decided_by="u-chen",
            ),
            _principal(Role.COMMITTEE, sub="u-chen"),
        )


async def test_a_vote_decision_with_a_reason_is_accepted(store: GateStore) -> None:
    await store.open(
        gate_document(
            gate_id="gate_1",
            case_id=CASE_ID,
            run_id=RUN_ID,
            gate_type=GateType.VOTE,
            required_role=Role.COMMITTEE,
        )
    )
    await store.decide(
        _decision(
            gate_id="gate_1",
            gate_type=GateType.VOTE,
            decision="approve_with_conditions",
            reason="controls are adequate with the conditions attached",
            decided_by="u-chen",
        ),
        _principal(Role.COMMITTEE, sub="u-chen"),
    )
    doc = await store.get("gate_1")
    assert doc is not None
    assert doc["status"] == GateStatus.DECIDED.value


async def test_count_open_for_run_counts_every_type_and_only_that_run(
    store: GateStore,
) -> None:
    for gate_id, gate_type, run_id in (
        ("gate_a", GateType.PO_CONFIRM_CATEGORY, RUN_ID),
        ("gate_b", GateType.REVIEW_ITEM, RUN_ID),
        ("gate_c", GateType.REVIEW_ITEM, "run_other"),
    ):
        await store.open(
            gate_document(
                gate_id=gate_id,
                case_id=CASE_ID,
                run_id=run_id,
                gate_type=gate_type,
                required_role=Role.ANALYST,
            )
        )
    assert await store.count_open_for_run(RUN_ID) == 2
    assert await store.count_open_for_run("run_other") == 1
    assert await store.count_open_for_run("run_none") == 0

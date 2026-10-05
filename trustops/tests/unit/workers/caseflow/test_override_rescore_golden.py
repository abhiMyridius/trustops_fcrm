"""The walkthrough's central moment as a golden test (D-42): the analyst
overrides vendor due diligence (CTL-T01) from 7.6 to 4.5 and the composite moves
from 9.8 (Band 2, boundary flag) to 10.4 (Band 3), which asks for one
acknowledgement before release (R3).

The score is S2's: ``FakeScoringService`` is the real scoring engine over the seed's
inputs, so the figures below are what the engine computes, not what a test says.
H1 builds nothing numeric: it asks S1 for the request, posts it, and concludes what
must be acknowledged. No model is involved anywhere in this file.

A planned control (the corridor review of CTL-T01, no rating) is recorded and shown
and never scored: the assertions on the request bodies S2 received are the proof.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from conftest import (
    FakeCaseService,
    FakeScoringService,
    RecordingAudit,
    harness_step,
    make_run,
)
from fcrm_caseflow.cores import assessment_review
from fcrm_caseflow.cores.assessment_items import item_id_for
from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.steps import StepDeps, StepResult
from fcrm_contracts.enums import CaseState, GateType, ItemKind, StepKind

CASE = "FCRM-2026-0151"
RUN = "run_1"
T01 = item_id_for(CASE, ItemKind.CONTROL, "CTL-T01", "scored")
REASON = "Audit predates the corridor arrangement and covers domestic scope only."
DepsFactory = Callable[..., StepDeps]


def _controls_sent(request: dict[str, Any]) -> list[tuple[str, float]]:
    return [
        (c["control_id"], c["rating"])
        for d in request["domains"]
        for c in d["controls"]
    ]


@pytest.fixture
async def overridden(
    published: tuple[StepResult, StepDeps],
    step_deps_factory: DepsFactory,
    assessed_case: FakeCaseService,
) -> StepResult:
    """``review.item`` for the CTL-T01 override, after the first score of record."""
    first, _ = published
    assessed_case.decide_item(T01, "overridden", value={"rating": 4.5}, reason=REASON)
    deps = step_deps_factory(
        make_run(
            run_id=RUN,
            state=CaseState.REVIEW,
            refs=first.refs_patch,
            band=first.band,
        )
    )
    return await assessment_review.review_item(
        harness_step(
            StepKind.REVIEW_ITEM,
            {
                "gate_id": deterministic_gate_id(RUN, GateType.REVIEW_ITEM, T01),
                "gate_type": "review_item",
                "case_id": CASE,
                "run_id": RUN,
                "decision": "override",
                "reason": REASON,
                "decided_by": "u-riyer",
                "decided_at": "2026-10-05T12:00:00Z",
                "before": {"rating": 7.6},
                "after": {"rating": 4.5},
                "subject": {"kind": "control", "id": T01},
                "item_kind": "control",
                "rating_or_control_changed": True,
            },
            step_id="step_override",
        ),
        deps,
    )


async def test_before_the_override_the_composite_is_9_8_band_2_with_a_boundary_flag(
    published: tuple[StepResult, StepDeps], assessed_case: FakeCaseService
) -> None:
    result, _ = published

    [first] = assessed_case.views["score"]
    assert first["score"]["composite"] == 9.8
    assert first["score"]["band"] == "2"
    assert first["score"]["route"] == "analyst"
    assert first["score"]["boundary_flag"] is True
    assert first["score"]["boundary_distance"] == 0.3
    assert [
        (d["domain"], d["control_effectiveness"], d["retention"], d["residual"])
        for d in first["score"]["domains"]
    ] == [
        ("channel", 7.59, 0.54, 10.9),
        ("geography", 6.0, 0.64, 10.2),
        ("product", 6.5, 0.61, 7.3),
    ]
    assert [d["contribution"] for d in first["score"]["domains"]] == [4.9, 3.07, 1.83]
    assert result.band == 2


async def test_the_override_moves_the_composite_to_10_4_band_3(
    overridden: StepResult, assessed_case: FakeCaseService
) -> None:
    after = assessed_case.views["score"][-1]

    assert after["purpose"] == "confirmed"
    assert after["score"]["composite"] == 10.4
    assert after["score"]["band"] == "3"
    assert after["score"]["route"] == "committee"
    assert after["score"]["boundary_flag"] is True
    assert after["score"]["boundary_distance"] == 0.3  # 0.3 above the Band 3 edge
    channel = after["score"]["domains"][0]
    assert (
        channel["control_effectiveness"],
        channel["retention"],
        channel["residual"],
        channel["contribution"],
    ) == (6.51, 0.61, 12.2, 5.49)
    assert [d["contribution"] for d in after["score"]["domains"]] == [5.49, 3.07, 1.83]
    assert after["previous"] == {"composite": 9.8, "band": "2"}
    assert after["band_changed"] is True
    assert overridden.band == 3


async def test_the_band_change_raises_one_acknowledgement_and_the_run_records_the_route(
    overridden: StepResult,
) -> None:
    [ack] = overridden.gates_to_open

    assert ack.gate_type is GateType.ACK
    assert dict(ack.subject) == {"kind": "ack", "id": "band_change"}
    assert overridden.band == 3
    assert overridden.refs_patch["score_summary"]["route"] == "committee"


async def test_the_records_come_in_the_walkthroughs_order(
    published: tuple[StepResult, StepDeps],
    overridden: StepResult,
    recording_audit: RecordingAudit,
) -> None:
    first_publish = [
        "proposal.research",
        "proposal.assessment",
        "score.computed",
        "boundary.flagged",
    ]
    override = ["human.override", "score.recomputed", "boundary.flagged"]

    assert [r.type for r in recording_audit.records] == first_publish + override
    human, rescored, flagged = recording_audit.records[-3:]
    assert human.summary == (
        "CTL-T01 7.6 → 4.5 by u-riyer · reason recorded · hash-chained"
    )
    assert human.data["reason"] == REASON
    assert rescored.summary == "Composite 10.4 · Band 3 · route committee"
    assert rescored.data["previous_composite"] == 9.8
    assert rescored.data["band_changed"] is True
    assert flagged.data["ack"] == "band_change"


# ── rule 7: the planned control is never scored ─────────────────────────────


async def test_the_planned_control_never_appears_in_either_request_s2_received(
    overridden: StepResult,
    scoring_service: FakeScoringService,
    assessed_case: FakeCaseService,
) -> None:
    planned = [
        row
        for row in assessed_case.items.values()
        if row["kind"] == "control" and row["subject"]["planned"]
    ]
    assert [p["subject"]["control_id"] for p in planned] == ["CTL-T01"]  # it exists
    assert planned[0]["proposed"]["rating"] is None

    before, after = scoring_service.requests
    for request in (before, after):
        sent = _controls_sent(request)
        assert len(sent) == 13
        assert [c for c, _ in sent].count("CTL-T01") == 1  # the scored instance only
        assert all(rating is not None for _, rating in sent)
        assert "planned" not in str(request)
    assert dict(_controls_sent(before))["CTL-T01"] == 7.6
    assert dict(_controls_sent(after))["CTL-T01"] == 4.5


async def test_a_planned_control_changes_no_number_even_when_its_item_is_decided(
    overridden: StepResult,
    step_deps_factory: DepsFactory,
    assessed_case: FakeCaseService,
    scoring_service: FakeScoringService,
) -> None:
    planned_id = item_id_for(CASE, ItemKind.CONTROL, "CTL-T01", "planned")

    assessed_case.decide_item(planned_id, "confirmed")
    response = assessed_case._score_request()

    sent = _controls_sent(response.json()["request"])
    assert (
        dict(sent)["CTL-T01"] == 4.5
    )  # still the override; the planned one adds nothing
    assert len(sent) == 13
    assert len(scoring_service.requests) == 2  # asking S1 scored nothing

"""A case in ``handoff`` as the analyst reads it (D-116): ``handoff`` means an
agent failed after its retry and the analyst finishes that step by hand, so the
stage strip stands at the stage the run was in, the label says the step is with
the analyst, the queue lists it under its own filter, and nothing that never ran
reads as done. Where it came from is recorded by the state patch into it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.analyst_read_store import QueueQuery, queue_match
from fcrm_case.domain.analyst_views import (
    STAGE_STATES,
    QueueRow,
    StageSpan,
    analyst_stage,
    assessment_ready,
    completeness_decision,
    handoff_origin,
    lifecycle,
    queue_item,
    working_state,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.stage import handoff_changes, state_changes
from fcrm_case.store import InMemoryCaseStore
from fcrm_contracts.api.case_analyst_read import AssessmentSummary, WorkbenchHeader
from fcrm_contracts.api.case_internal import StatePatch, UiActor
from fcrm_contracts.enums import CaseState

if TYPE_CHECKING:
    from conftest import AnalystRead, AssessedCase

CASE = "FCRM-2026-0151"
RAHUL = "u-riyer"
AT = datetime(2026, 10, 8, 9, tzinfo=UTC)
ACTOR = UiActor(id="svc:caseflow", role="fcrm_service", component="caseflow")


def span(state: CaseState, began: datetime, ended: datetime | None) -> StageSpan:
    return StageSpan(state=state.value, began=began, ended=ended)


# ── the stage, the strip and the queue filter ───────────────────────────────────


def test_a_handoff_from_assess_stands_at_assess_with_the_rest_upcoming() -> None:
    info = analyst_stage(CaseState.HANDOFF, locked=False, handoff_from=CaseState.ASSESS)
    assert (info.stage, info.label) == ("handoff", "Handed to you · assess")
    steps = {s.key: s.state for s in lifecycle(info)}
    assert [steps[k] for k in ("intake", "triage", "screening")] == ["done"] * 3
    assert steps["assess"] == "current"
    for later in ("critic", "completeness", "clarification", "review", "decide"):
        assert steps[later] == "upcoming"


def test_a_handoff_names_a_two_word_stage_in_words() -> None:
    info = analyst_stage(
        CaseState.HANDOFF, locked=False, handoff_from=CaseState.REVIEW_AND_SUBMIT
    )
    assert info.label == "Handed to you · review and submit"
    assert info.step == 1


def test_a_handoff_whose_origin_is_unknown_still_reads_as_handed_to_the_analyst() -> (
    None
):
    info = analyst_stage(CaseState.HANDOFF, locked=False)
    assert (info.stage, info.label) == ("handoff", "Handed to you")


def test_a_locked_recommendation_is_not_a_handoff() -> None:
    locked = analyst_stage(CaseState.RECOMMEND, locked=True)
    assert (locked.stage, locked.label) == ("recommend", "Locked · handoff")


def test_the_queue_filters_a_handoff_on_its_own_key() -> None:
    assert STAGE_STATES["handoff"] == (CaseState.HANDOFF,)
    assert CaseState.HANDOFF not in STAGE_STATES["recommend"]
    match = queue_match(
        QueueQuery(
            scope="all", viewer_id=RAHUL, stage="handoff", band=None, limit=50, offset=0
        )
    )
    assert match["state"] == {"$in": ["handoff"]}


def test_a_queue_row_in_handoff_says_where_it_came_from() -> None:
    row = QueueRow(
        case_id=CASE,
        title="Remittances",
        unit="Payments",
        owner="u-pnair",
        owner_name="Priya Nair",
        analyst_id=RAHUL,
        analyst_name="Rahul Iyer",
        change_types=(),
        state=CaseState.HANDOFF,
        submitted_at=AT,
        review_started=False,
        fast_lane_proposed=False,
        fast_lane_confirmed=False,
        locked=False,
        band=None,
        composite=None,
        boundary_flag=False,
        snapshot_purpose=None,
        critic_count=0,
        handoff_from=CaseState.ASSESS,
    )
    item = queue_item(row, RAHUL)
    assert (item.stage, item.stage_label) == ("handoff", "Handed to you · assess")
    assert item.is_new is False


# ── what has and has not happened ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "origin", [CaseState.SCREENING, CaseState.ASSESS, CaseState.CRITIC]
)
def test_a_handoff_before_the_completeness_report_has_no_decision_and_is_not_ready(
    make_case_record: Callable[..., CaseRecord], origin: CaseState
) -> None:
    record = make_case_record(state=CaseState.HANDOFF, handoff_from=origin)
    assert working_state(record) is origin
    assert completeness_decision(record) is None
    assert assessment_ready(record, has_completeness_view=False) is False
    assert assessment_ready(record, has_completeness_view=True) is True


def test_a_handoff_after_review_started_reads_as_past_the_gate(
    make_case_record: Callable[..., CaseRecord],
) -> None:
    record = make_case_record(state=CaseState.HANDOFF, handoff_from=CaseState.REVIEW)
    assert completeness_decision(record) == "proceed"
    assert assessment_ready(record, has_completeness_view=False) is True


def test_the_origin_is_read_from_the_stage_log_when_the_case_does_not_hold_it() -> None:
    one, two, three = AT, AT + timedelta(hours=1), AT + timedelta(hours=2)
    assert (
        handoff_origin(
            [
                span(CaseState.SCREENING, one, two),
                span(CaseState.ASSESS, two, three),
                span(CaseState.HANDOFF, three, None),
            ]
        )
        is CaseState.ASSESS
    )
    assert handoff_origin([span(CaseState.ASSESS, one, None)]) is None
    assert (
        handoff_origin(
            [span(CaseState.HANDOFF, one, two), span(CaseState.REVIEW, two, None)]
        )
        is None
    )


# ── recording the origin ────────────────────────────────────────────────────────


def test_moving_into_handoff_records_the_state_left_and_leaving_clears_it() -> None:
    assert handoff_changes(CaseState.ASSESS, CaseState.HANDOFF) == {
        "handoff_from": CaseState.ASSESS
    }
    assert handoff_changes(CaseState.HANDOFF, CaseState.HANDOFF) == {}
    assert handoff_changes(CaseState.HANDOFF, CaseState.ASSESS) == {
        "handoff_from": None
    }
    assert handoff_changes(CaseState.ASSESS, CaseState.CRITIC) == {}
    patch = StatePatch(stage=CaseState.HANDOFF)
    assert state_changes(patch, AT)["state"] is CaseState.HANDOFF
    assert "handoff_from" not in state_changes(patch, AT)
    assert (
        state_changes(patch, AT, previous=CaseState.ASSESS)["handoff_from"]
        is CaseState.ASSESS
    )


async def test_the_state_patch_records_where_the_handoff_came_from(
    case_store: InMemoryCaseStore,
    make_case_record: Callable[..., CaseRecord],
) -> None:
    case_store._cases[CASE] = make_case_record(state=CaseState.ASSESS)
    write = await case_store.apply_state(
        CASE, StatePatch(stage=CaseState.HANDOFF), ACTOR
    )
    assert write is not None
    assert write.record.handoff_from is CaseState.ASSESS
    again = await case_store.apply_state(
        CASE, StatePatch(stage=CaseState.HANDOFF), ACTOR
    )
    assert again is not None
    assert again.record.handoff_from is CaseState.ASSESS
    resumed = await case_store.apply_state(
        CASE, StatePatch(stage=CaseState.ASSESS), ACTOR
    )
    assert resumed is not None
    assert resumed.record.handoff_from is None


# ── through the routes ──────────────────────────────────────────────────────────


def _held_in_handoff(
    analyst_read: AnalystRead,
    make_case_record: Callable[..., Any],
    **overrides: Any,
) -> CaseRecord:
    record = make_case_record(
        state=CaseState.HANDOFF,
        analyst_id=RAHUL,
        analyst_name="Rahul Iyer",
        submitted_at=AT - timedelta(hours=2),
        run_id="run_1",
        current_submission_v=1,
        **overrides,
    )
    analyst_read.service.store.records[record.case_id] = record
    return record


def test_the_header_of_a_case_handed_off_in_assess(
    analyst_read: AnalystRead, make_case_record: Callable[..., Any]
) -> None:
    _held_in_handoff(analyst_read, make_case_record, handoff_from=CaseState.ASSESS)
    response = analyst_read.client.get(f"/cases/{CASE}/workbench")
    assert response.status_code == 200, response.text
    head = WorkbenchHeader.model_validate(response.json())
    assert (head.stage, head.stage_label) == ("handoff", "Handed to you · assess")
    states = {s.key: s.state for s in head.lifecycle}
    assert states["assess"] == "current"
    assert states["critic"] == states["completeness"] == "upcoming"
    assert head.completeness_decision is None


def test_the_header_reads_the_origin_from_the_stage_log_for_an_older_case(
    analyst_read: AnalystRead, make_case_record: Callable[..., Any]
) -> None:
    from fcrm_case.domain.stage import stage_changed_event

    _held_in_handoff(analyst_read, make_case_record, activity_seq=1)
    analyst_read.service.store.activity.stage(
        CASE,
        1,
        stage_changed_event(
            CaseState.ASSESS, StatePatch(stage=CaseState.HANDOFF), ACTOR
        ),
    )
    # The log needs the stretch before the handoff too.
    analyst_read.service.store.activity.entries.insert(
        0,
        analyst_read.service.store.activity.entries[-1].model_copy(
            update={
                "seq": 0,
                "at": AT - timedelta(hours=1),
                "detail": {"from_stage": "screening", "stage": "assess"},
            }
        ),
    )
    head = WorkbenchHeader.model_validate(
        analyst_read.client.get(f"/cases/{CASE}/workbench").json()
    )
    assert head.stage_label == "Handed to you · assess"


def test_the_queue_lists_a_handed_off_case_under_the_handoff_filter(
    analyst_read: AnalystRead, make_case_record: Callable[..., Any]
) -> None:
    _held_in_handoff(analyst_read, make_case_record, handoff_from=CaseState.ASSESS)
    response = analyst_read.client.get("/queue", params={"stage": "handoff"})
    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert (item["id"], item["stage"], item["stage_label"]) == (
        CASE,
        "handoff",
        "Handed to you · assess",
    )
    recommend = analyst_read.client.get("/queue", params={"stage": "recommend"})
    assert recommend.json()["items"] == []


def test_the_summary_of_a_case_handed_off_before_the_report_is_not_ready(
    assessed_case: AssessedCase,
) -> None:
    del assessed_case.read.assessment.completeness[CASE]
    assessed_case.update_record(state=CaseState.HANDOFF, handoff_from=CaseState.ASSESS)
    response = assessed_case.client.get(f"/cases/{CASE}/assessment")
    assert response.status_code == 200, response.text
    summary = AssessmentSummary.model_validate(response.json())
    assert summary.ready is False
    assert summary.agent_failed is not None
    assert summary.critic_findings == []

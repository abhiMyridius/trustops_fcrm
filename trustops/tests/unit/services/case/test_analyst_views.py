"""``fcrm_case.domain.analyst_views`` and the queue pipeline: the analyst's stage
names and order, the score box's visibility, the clocks and what is owed (R3).
Pure inputs and fixed expectations; nothing here computes a score.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fcrm_case.analyst_read_store import (
    QueueQuery,
    queue_match,
    queue_pipeline,
    queue_row_from_document,
    rank_expression,
    spans_from_changes,
)
from fcrm_case.domain.analyst_views import (
    NEEDS_RANK,
    QUEUE_STAGES,
    STAGE_STATES,
    WITHHELD_REASON,
    Acknowledgement,
    QueueRow,
    StageSpan,
    ack_message,
    analyst_stage,
    boundary_text,
    clocks,
    due_date,
    hm,
    is_approaching_due,
    lifecycle,
    pending_acknowledgements,
    queue_item,
    score_box,
    score_visible,
)
from fcrm_contracts.api.case_analyst_read import ScoreShown, ScoreWithheld
from fcrm_contracts.enums import CaseState

AT = datetime(2026, 10, 5, 9, tzinfo=UTC)


def row(**overrides: Any) -> QueueRow:
    fields: dict[str, Any] = {
        "case_id": "FCRM-2026-0140",
        "title": "Case",
        "unit": "Payments",
        "owner": "u-pnair",
        "owner_name": "Priya Nair",
        "analyst_id": "u-riyer",
        "analyst_name": "Rahul Iyer",
        "change_types": ("new_product",),
        "state": CaseState.REVIEW,
        "submitted_at": AT,
        "review_started": False,
        "fast_lane_proposed": False,
        "locked": False,
        "band": None,
        "composite": None,
        "boundary_flag": False,
        "snapshot_purpose": None,
        "critic_count": 0,
    }
    return QueueRow(**{**fields, **overrides})


# ── stages and the order the queue is worked in ─────────────────────────────────


@pytest.mark.parametrize("state", list(CaseState))
def test_analyst_stage_names_every_state(state: CaseState) -> None:
    info = analyst_stage(state, locked=False)
    assert info.stage
    assert info.label
    assert 0 <= info.step <= 10
    assert state in NEEDS_RANK


def test_the_queue_stage_filters_cover_every_state_a_submitted_case_can_be_in() -> None:
    covered = {s for states in STAGE_STATES.values() for s in states}
    submitted_states = set(CaseState) - {
        CaseState.INTAKE,
        CaseState.REVIEW_AND_SUBMIT,
    }
    assert submitted_states <= covered
    assert "fast_lane" in QUEUE_STAGES


def test_a_locked_recommendation_reads_as_handoff_at_step_nine() -> None:
    open_ = analyst_stage(CaseState.RECOMMEND, locked=False)
    locked = analyst_stage(CaseState.RECOMMEND, locked=True)
    assert (open_.stage, open_.step) == ("recommend", 8)
    assert (locked.label, locked.step) == ("Locked · handoff", 9)


def test_the_strip_marks_what_is_done_current_and_upcoming() -> None:
    steps = lifecycle(analyst_stage(CaseState.REVIEW, locked=False))
    states = [s.state for s in steps]
    assert states[:8] == ["done"] * 8
    assert states[8] == "current"
    assert states[9:] == ["upcoming", "upcoming"]
    assert steps[8].key == "review"


def test_what_needs_the_analyst_first_is_the_decision_waiting_on_them() -> None:
    order = sorted(NEEDS_RANK, key=lambda s: (NEEDS_RANK[s], s.value))
    assert order[0] in (CaseState.TRIAGE, CaseState.COMPLETENESS)
    assert NEEDS_RANK[CaseState.REVIEW] < NEEDS_RANK[CaseState.ASSESS]
    assert NEEDS_RANK[CaseState.ASSESS] < NEEDS_RANK[CaseState.CLARIFICATION]
    assert NEEDS_RANK[CaseState.CLARIFICATION] < NEEDS_RANK[CaseState.DECIDE]
    assert NEEDS_RANK[CaseState.DECIDE] < NEEDS_RANK[CaseState.CLOSED]


# ── the one queue aggregation ───────────────────────────────────────────────────


def query(**overrides: Any) -> QueueQuery:
    fields: dict[str, Any] = {
        "scope": "mine",
        "viewer_id": "u-riyer",
        "stage": None,
        "band": None,
        "limit": 50,
        "offset": 0,
    }
    return QueueQuery(**{**fields, **overrides})


def stage_names(pipeline: list[dict[str, Any]]) -> list[str]:
    return [next(iter(stage)) for stage in pipeline]


def test_the_queue_is_one_pipeline_with_the_lookups_inside_it() -> None:
    pipeline = queue_pipeline(query())
    assert stage_names(pipeline) == [
        "$match",
        "$lookup",
        "$lookup",
        "$addFields",
        "$sort",
        "$facet",
    ]
    lookups = [s["$lookup"]["from"] for s in pipeline if "$lookup" in s]
    assert lookups == ["score_snapshots", "critic_views"]
    assert set(pipeline[-1]["$facet"]) == {"items", "total"}


def test_the_latest_snapshot_is_looked_up_by_the_indexed_pair() -> None:
    lookup = queue_pipeline(query())[1]["$lookup"]
    assert (lookup["localField"], lookup["foreignField"]) == ("_id", "case_id")
    assert lookup["pipeline"][0] == {"$sort": {"computed_at": -1}}
    assert lookup["pipeline"][1] == {"$limit": 1}


def test_the_page_is_cut_inside_the_facet_after_the_sort() -> None:
    facet = queue_pipeline(query(limit=7, offset=14))[-1]["$facet"]
    assert facet["items"][0] == {"$skip": 14}
    assert facet["items"][1] == {"$limit": 7}
    assert facet["total"] == [{"$count": "n"}]


def test_the_band_filter_adds_one_match_after_the_lookups() -> None:
    plain = stage_names(queue_pipeline(query()))
    banded = queue_pipeline(query(band=3))
    assert stage_names(banded).count("$match") == plain.count("$match") + 1
    assert banded[3] == {"$match": {"snap.0.band": 3}}


def test_the_sort_puts_rank_first_then_oldest_submission() -> None:
    sort = queue_pipeline(query())[4]["$sort"]
    assert list(sort) == ["rank", "submitted_at", "_id"]


def test_the_rank_switch_covers_every_state_with_the_shared_table() -> None:
    switch = rank_expression()["$switch"]
    thens = {b["case"]["$eq"][1]: b["then"] for b in switch["branches"]}
    assert thens == {state.value: rank for state, rank in NEEDS_RANK.items()}


def test_the_match_scopes_stages_and_leaves_closed_out_by_default() -> None:
    base = queue_match(query())
    assert base["analyst_id"] == "u-riyer"
    assert base["state"] == {"$ne": "closed"}
    assert base["discarded_at"] is None
    assert queue_match(query(scope="all")).get("analyst_id") is None
    assert queue_match(query(scope="unassigned"))["analyst_id"] is None
    closed = queue_match(query(scope="all", stage="closed"))
    assert closed["state"] == {"$in": ["closed"]}
    fast = queue_match(query(stage="fast_lane"))
    assert fast["stage_flags.fast_lane_proposed"] is True


def test_a_queue_document_becomes_a_row() -> None:
    doc = {
        "_id": "FCRM-2026-0140",
        "title": "T",
        "unit": "U",
        "owner": "u-pnair",
        "owner_name": "Priya Nair",
        "analyst_id": "u-riyer",
        "analyst_name": "Rahul Iyer",
        "change_types": ["new_product"],
        "state": "review",
        "submitted_at": AT,
        "stage_flags": {"fast_lane_proposed": True, "locked": False},
        "snap": [
            {"band": 1, "composite": 3.2, "boundary_flag": False, "purpose": "seed"}
        ],
        "critic": [{"n": 2}],
    }
    parsed = queue_row_from_document(doc)
    assert (parsed.band, parsed.composite, parsed.critic_count) == (1, 3.2, 2)
    assert parsed.fast_lane_proposed
    assert not parsed.review_started
    bare = queue_row_from_document({**doc, "snap": [], "critic": []})
    assert (bare.band, bare.critic_count) == (None, 0)


# ── the score box ───────────────────────────────────────────────────────────────


def test_a_live_score_is_withheld_until_review_starts(
    make_snapshot: Callable[..., Any],
) -> None:
    snap = make_snapshot()
    withheld = score_box(snap, review_started=False, assessment_ready=True)
    assert isinstance(withheld, ScoreWithheld)
    assert withheld.reason == WITHHELD_REASON
    shown = score_box(snap, review_started=True, assessment_ready=True)
    assert isinstance(shown, ScoreShown)
    assert (shown.composite, shown.band, shown.band_label) == (9.8, 2, "Moderate")
    assert shown.band_range == "5.1–10.0"
    assert shown.route_label == "Delegated close (R1)"


def test_a_seeded_score_shows_at_once(make_snapshot: Callable[..., Any]) -> None:
    snap = make_snapshot(purpose="seed")
    assert score_visible(snap, review_started=False)
    assert isinstance(
        score_box(snap, review_started=False, assessment_ready=True), ScoreShown
    )


def test_before_the_assessment_has_finished_there_is_no_score_to_show(
    make_snapshot: Callable[..., Any],
) -> None:
    box = score_box(make_snapshot(), review_started=True, assessment_ready=False)
    assert isinstance(box, ScoreWithheld)
    assert "still running" in box.reason
    assert isinstance(
        score_box(None, review_started=True, assessment_ready=True), ScoreWithheld
    )


def test_a_rescored_case_carries_the_previous_score(
    make_snapshot: Callable[..., Any],
) -> None:
    snap = make_snapshot(
        band=3,
        composite=10.4,
        boundary_flag=True,
        boundary_distance=0.3,
        previous_composite=9.8,
        previous_band=2,
        band_changed=True,
        scoring_v=3,
    )
    shown = score_box(snap, review_started=True, assessment_ready=True)
    assert isinstance(shown, ScoreShown)
    assert shown.previous is not None
    assert (shown.previous.composite, shown.previous.band) == (9.8, 2)
    assert shown.route_label == "Risk Committee (R2)"
    assert shown.model_version == "v3"
    assert shown.boundary_text == ("Boundary flag · 0.3 above the Band 3 edge (10.1)")


def test_a_score_just_under_the_next_edge_says_from_the_next_band_edge() -> None:
    assert boundary_text(9.8, 2, 0.3) == "Boundary flag · 0.3 from the Band 3 edge"
    assert boundary_text(9.8, 2, None) is None


# ── the queue row ───────────────────────────────────────────────────────────────


def test_a_queue_row_hides_a_live_band_until_review_started() -> None:
    hidden = queue_item(
        row(band=3, composite=10.4, snapshot_purpose="proposed"), "u-riyer"
    )
    assert (hidden.band, hidden.composite, hidden.band_label) == (None, None, None)
    shown = queue_item(
        row(band=3, composite=10.4, snapshot_purpose="proposed", review_started=True),
        "u-riyer",
    )
    assert (shown.band, shown.composite, shown.band_label) == (3, 10.4, "Elevated")


def test_a_queue_row_carries_mine_new_fast_lane_and_due() -> None:
    item = queue_item(
        row(
            state=CaseState.TRIAGE,
            fast_lane_proposed=True,
            submitted_at=datetime(2026, 10, 2, 10, tzinfo=UTC),
        ),
        "u-riyer",
    )
    assert item.mine
    assert item.is_new
    assert item.fast_lane is not None
    assert item.fast_lane.proposed
    assert (item.arrived, item.due) == ("2026-10-02", "2026-10-06")
    other = queue_item(row(), "u-mshah")
    assert not other.mine
    assert other.fast_lane is None


def test_a_closed_case_has_no_due_date() -> None:
    assert due_date(AT, CaseState.CLOSED) is None
    assert due_date(None, CaseState.REVIEW) is None
    assert due_date(AT, CaseState.REVIEW) == date(2026, 10, 7)


def test_approaching_due_counts_a_late_case_too() -> None:
    today = date(2026, 10, 6)
    assert is_approaching_due(date(2026, 10, 7), today)
    assert is_approaching_due(date(2026, 10, 1), today)
    assert not is_approaching_due(date(2026, 10, 9), today)
    assert not is_approaching_due(None, today)


# ── clocks and what is owed ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("span", "text"),
    [
        (timedelta(minutes=25), "0h 25m"),
        (timedelta(hours=4, minutes=5), "4h 05m"),
        (timedelta(days=1, hours=2), "1d 2h"),
        (timedelta(seconds=-5), "0h 00m"),
    ],
)
def test_hm_prints_hours_and_minutes_then_days(span: timedelta, text: str) -> None:
    assert hm(span) == text


def test_the_analyst_clock_stops_while_the_owner_has_the_round() -> None:
    submitted = AT
    spans = [
        StageSpan("review", AT + timedelta(hours=1), AT + timedelta(hours=2)),
        StageSpan("clarification", AT + timedelta(hours=2), AT + timedelta(hours=6)),
        StageSpan("review", AT + timedelta(hours=6), None),
    ]
    result = clocks(submitted, None, spans, AT + timedelta(hours=8))
    assert (result.analyst_active, result.total_elapsed) == ("4h 00m", "8h 00m")
    assert result.analyst_paused is False


def test_an_open_round_pauses_the_clock_now() -> None:
    spans = [StageSpan("clarification", AT + timedelta(hours=2), None)]
    result = clocks(AT, None, spans, AT + timedelta(hours=5))
    assert (result.analyst_active, result.analyst_paused) == ("2h 00m", True)


def test_a_closed_case_stops_both_clocks_at_the_close() -> None:
    result = clocks(AT, AT + timedelta(days=2), [], AT + timedelta(days=30))
    assert (result.total_elapsed, result.analyst_paused) == ("2d 0h", False)
    assert clocks(None, None, [], AT).total_elapsed == "0h 00m"


def test_spans_run_from_one_stage_change_to_the_next() -> None:
    changes = [(AT, "review"), (AT + timedelta(hours=1), "clarification")]
    spans = spans_from_changes(changes)
    assert spans[0].ended == AT + timedelta(hours=1)
    assert spans[1].ended is None


def test_a_band_change_is_owed_until_an_acknowledgement_after_that_score(
    make_snapshot: Callable[..., Any],
) -> None:
    snap = make_snapshot(band=3, band_changed=True, previous_band=2)
    assert pending_acknowledgements(snap, []) == ["band_change"]
    before = Acknowledgement("band_change", snap.computed_at - timedelta(hours=1))
    assert pending_acknowledgements(snap, [before]) == ["band_change"]
    after = Acknowledgement("band_change", snap.computed_at + timedelta(minutes=1))
    assert pending_acknowledgements(snap, [after]) == []


def test_a_boundary_flag_is_owed_until_it_is_acknowledged_once(
    make_snapshot: Callable[..., Any],
) -> None:
    snap = make_snapshot(boundary_flag=True, boundary_distance=0.3)
    assert pending_acknowledgements(snap, []) == ["boundary"]
    assert pending_acknowledgements(snap, [Acknowledgement("boundary", AT)]) == []
    assert pending_acknowledgements(None, []) == []


def test_the_banner_names_what_is_owed(make_snapshot: Callable[..., Any]) -> None:
    snap = make_snapshot(
        band=3,
        boundary_flag=True,
        boundary_distance=0.3,
        band_changed=True,
        previous_band=2,
    )
    both = ack_message(["band_change", "boundary"], snap)
    assert both is not None
    assert "Band changed (2 → 3)" in both
    only = ack_message(["boundary"], snap)
    assert only is not None
    assert "(R3)" in only
    assert ack_message([], snap) is None

"""S1's analyst read routes: the queue, the dashboard and the workbench header,
over the real seed's S1 slice (80 cases, 14 open and submitted) held in the
in-memory stores. The role matrix, the order, the filters, the paging and the
score withholding; the Mongo pipeline itself is covered against a live database
by ``tests/integration/services/case/test_analyst_read_live.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.analyst_views import Acknowledgement
from fcrm_case.routes_analyst_read import get_clock
from fcrm_contracts.api.case_analyst_read import (
    DashboardAnalyst,
    QueueItem,
    WorkbenchHeader,
)
from fcrm_contracts.enums import CaseState, Role

if TYPE_CHECKING:
    from conftest import AnalystRead

RAHUL = "u-riyer"
TODAY = datetime(2026, 10, 6, 12, tzinfo=UTC)

#: Rahul's four open submitted seed cases in the order he works them: the two
#: in review (oldest first), then the one with the owner, then the committee's.
RAHUL_QUEUE = [
    "FCRM-2026-0140",
    "FCRM-2026-0146",
    "FCRM-2026-0143",
    "FCRM-2026-0138",
]


@pytest.fixture
def today(analyst_read: AnalystRead) -> None:
    """Pin the read views' clock so 'approaching due' does not move with today."""
    analyst_read.client.app.dependency_overrides[get_clock] = lambda: lambda: TODAY  # type: ignore[attr-defined]


def ids(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["items"]]


# ── who may read ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
@pytest.mark.parametrize(
    "path", ["/queue", "/dashboard/analyst", "/cases/FCRM-2026-0140/workbench"]
)
def test_only_an_analyst_may_read_the_queue_dashboard_or_workbench(
    seeded_analyst_read: AnalystRead,
    act_as: Callable[[Role, str], None],
    role: Role,
    path: str,
) -> None:
    act_as(role, "u-someone")
    assert seeded_analyst_read.client.get(path).status_code == 403


def test_an_unauthenticated_caller_is_refused(analyst_read: AnalystRead) -> None:
    from fcrm_common.auth import current_user
    from fcrm_common.errors import UnauthorizedError

    def refuse() -> None:
        raise UnauthorizedError("Bearer token required.")

    analyst_read.client.app.dependency_overrides[current_user] = refuse  # type: ignore[attr-defined]
    assert analyst_read.client.get("/queue").status_code == 401


# ── the queue ───────────────────────────────────────────────────────────────────


def test_my_queue_lists_my_open_cases_the_ones_waiting_on_me_first(
    seeded_analyst_read: AnalystRead,
) -> None:
    response = seeded_analyst_read.client.get("/queue")
    assert ids(response) == RAHUL_QUEUE
    assert response.json()["total"] == 4
    assert response.json()["next_cursor"] is None
    # The demo case is at intake: it is not in anyone's queue yet.
    assert "FCRM-2026-0151" not in RAHUL_QUEUE


def test_everyones_queue_is_ordered_by_what_needs_an_analyst_first(
    seeded_analyst_read: AnalystRead,
) -> None:
    response = seeded_analyst_read.client.get("/queue", params={"scope": "all"})
    listed = ids(response)
    assert len(listed) == response.json()["total"] == 14
    items = {row["id"]: row for row in response.json()["items"]}
    stages = [items[i]["stage"] for i in listed]
    order = {"review": 0, "awaiting_business": 1, "committee": 2}
    assert stages == sorted(stages, key=order.__getitem__)
    review = [i for i in listed if items[i]["stage"] == "review"]
    assert review == sorted(review, key=lambda i: (items[i]["arrived"], i))


def test_a_queue_row_has_the_stored_figures_for_a_seeded_case(
    seeded_analyst_read: AnalystRead,
) -> None:
    body = seeded_analyst_read.client.get("/queue").json()
    first = QueueItem.model_validate(body["items"][0])
    assert first.id == "FCRM-2026-0140"
    assert first.mine
    assert not first.is_new
    assert (first.band, first.band_label) == (1, "Low")
    assert first.fast_lane is not None
    assert first.fast_lane.proposed
    assert first.owner_name
    assert first.analyst_name == "Rahul Iyer"
    assert first.stage == "review"
    assert first.due is not None


@pytest.mark.parametrize(
    ("params", "expected_total"),
    [
        ({"scope": "all", "stage": "review"}, 8),
        ({"scope": "all", "stage": "awaiting_business"}, 4),
        ({"scope": "all", "stage": "committee"}, 2),
        ({"scope": "all", "stage": "fast_lane"}, 1),
        ({"scope": "all", "band": "4"}, 1),
        ({"scope": "all", "band": "1"}, 6),
        ({"scope": "all", "stage": "closed"}, 65),
        ({"scope": "all", "stage": "review", "band": "2"}, 3),
        ({"scope": "mine", "stage": "review"}, 2),
        ({"scope": "unassigned"}, 0),
    ],
)
def test_the_stage_band_and_scope_filters(
    seeded_analyst_read: AnalystRead, params: dict[str, str], expected_total: int
) -> None:
    response = seeded_analyst_read.client.get("/queue", params={**params, "limit": 5})
    assert response.status_code == 200
    assert response.json()["total"] == expected_total


def test_a_bad_filter_value_is_a_422(seeded_analyst_read: AnalystRead) -> None:
    for params in ({"stage": "nonsense"}, {"band": "9"}, {"scope": "everyone"}):
        assert (
            seeded_analyst_read.client.get("/queue", params=params).status_code == 422
        )


def test_the_queue_pages_with_a_cursor_and_stays_bounded(
    seeded_analyst_read: AnalystRead,
) -> None:
    client = seeded_analyst_read.client
    first = client.get("/queue", params={"scope": "all", "limit": 6}).json()
    second = client.get(
        "/queue", params={"scope": "all", "limit": 6, "cursor": first["next_cursor"]}
    ).json()
    third = client.get(
        "/queue", params={"scope": "all", "limit": 6, "cursor": second["next_cursor"]}
    ).json()
    pages = [first, second, third]
    assert [len(p["items"]) for p in pages] == [6, 6, 2]
    assert third["next_cursor"] is None
    every = [row["id"] for p in pages for row in p["items"]]
    assert len(set(every)) == 14
    assert all(p["total"] == 14 for p in pages)
    assert client.get("/queue", params={"limit": 201}).status_code == 422
    assert client.get("/queue", params={"limit": 0}).status_code == 422


def test_a_malformed_or_foreign_cursor_is_a_422(
    seeded_analyst_read: AnalystRead,
) -> None:
    from fcrm_common.pagination import encode_cursor

    client = seeded_analyst_read.client
    assert client.get("/queue", params={"cursor": "not-a-cursor"}).status_code == 422
    assert (
        client.get(
            "/queue", params={"cursor": encode_cursor("FCRM-2026-0140")}
        ).status_code
        == 422
    )
    assert (
        client.get("/queue", params={"cursor": encode_cursor(10**9)}).status_code == 422
    )


def test_a_live_score_stays_out_of_the_queue_until_review_starts(
    analyst_read: AnalystRead,
    make_case_record: Callable[..., Any],
    make_snapshot: Callable[..., Any],
) -> None:
    record = make_case_record(
        state=CaseState.REVIEW,
        analyst_id=RAHUL,
        analyst_name="Rahul Iyer",
        submitted_at=TODAY - timedelta(days=1),
        run_id="run_1",
    )
    analyst_read.service.store.records[record.case_id] = record
    snap = make_snapshot(band=3, composite=10.4)
    analyst_read.assessment.snapshots[snap.snapshot_id] = snap
    client = analyst_read.client
    row = client.get("/queue").json()["items"][0]
    assert (row["band"], row["composite"], row["boundary_flag"]) == (None, None, False)
    assert client.get("/queue", params={"band": "3"}).json()["total"] == 1

    analyst_read.service.store.records[record.case_id] = record.model_copy(
        update={"review_started_at": TODAY}
    )
    row = client.get("/queue").json()["items"][0]
    assert (row["band"], row["composite"]) == (3, 10.4)


def test_the_critic_count_is_the_findings_the_critic_pushed(
    analyst_read: AnalystRead,
    make_case_record: Callable[..., Any],
) -> None:
    from fcrm_case.domain.assessment import CriticViewRow

    record = make_case_record(
        state=CaseState.REVIEW,
        analyst_id=RAHUL,
        submitted_at=TODAY,
    )
    analyst_read.service.store.records[record.case_id] = record
    analyst_read.assessment.critic[record.case_id] = CriticViewRow(
        case_id=record.case_id,
        run_id="run_1",
        findings=[{"finding_id": "a"}, {"finding_id": "b"}],
        counts={"total": 2},
        updated_at=TODAY,
    )
    assert analyst_read.client.get("/queue").json()["items"][0]["critic_count"] == 2


# ── the dashboard ───────────────────────────────────────────────────────────────


def test_the_dashboard_is_computed_from_the_stored_cases(
    seeded_analyst_read: AnalystRead, today: None
) -> None:
    response = seeded_analyst_read.client.get("/dashboard/analyst")
    assert response.status_code == 200
    dash = DashboardAnalyst.model_validate(response.json())
    assert dash.kpis.open_assigned == 4
    assert dash.kpis.closed_cases == 65
    assert dash.kpis.avg_days_intake_to_decision == pytest.approx(3.3, abs=0.05)
    assert [m.id for m in dash.mine] == RAHUL_QUEUE
    assert sum(m.count for m in dash.closed_by_month) == 65
    assert [b.band for b in dash.band_distribution] == [1, 2, 3, 4]
    # 79 stored snapshots: the demo case has no score until H1 pushes one.
    assert [b.count for b in dash.band_distribution] == [19, 48, 11, 1]
    assert dash.cases_by_type[0].count >= dash.cases_by_type[-1].count
    assert len(dash.agent_eval) == 9
    assert dash.control_health.total == 24
    assert dash.control_health.healthy + dash.control_health.stale == 24


def test_approaching_due_counts_my_cases_due_within_a_business_day(
    seeded_analyst_read: AnalystRead, today: None
) -> None:
    dash = DashboardAnalyst.model_validate(
        seeded_analyst_read.client.get("/dashboard/analyst").json()
    )
    # Every seeded open case was submitted weeks before TODAY: all are late.
    assert dash.kpis.approaching_due == dash.kpis.open_assigned


# ── the workbench header ────────────────────────────────────────────────────────


def header(client: Any, case_id: str) -> WorkbenchHeader:
    response = client.get(f"/cases/{case_id}/workbench")
    assert response.status_code == 200, response.text
    return WorkbenchHeader.model_validate(response.json())


def test_a_seeded_review_case_header(seeded_analyst_read: AnalystRead) -> None:
    head = header(seeded_analyst_read.client, "FCRM-2026-0140")
    assert (head.stage, head.stage_label) == ("review", "Review")
    assert [s.state for s in head.lifecycle].count("current") == 1
    assert head.lifecycle[8].state == "current"
    assert head.review_started
    assert head.route == "analyst"
    assert head.completeness_decision == "proceed"
    assert head.submitted
    assert not head.closed
    assert not head.locked
    assert head.is_demo is False
    assert head.pins.scoring == "v3"
    assert head.pins.corpus == "v14"
    assert head.pending_acknowledgements == []
    assert head.ack_message is None
    assert head.analyst_name == "Rahul Iyer"
    assert head.owner_name


def test_a_clarification_case_asked_the_owner(
    seeded_analyst_read: AnalystRead,
) -> None:
    head = header(seeded_analyst_read.client, "FCRM-2026-0143")
    assert (head.stage, head.completeness_decision) == ("awaiting_business", "ask")


def test_a_committee_and_a_closed_case_header(
    seeded_analyst_read: AnalystRead,
) -> None:
    committee = header(seeded_analyst_read.client, "FCRM-2026-0138")
    assert (committee.stage, committee.route, committee.locked) == (
        "committee",
        "committee",
        True,
    )
    closed_id = next(
        r.case_id
        for r in seeded_analyst_read.service.store.records.values()
        if r.state is CaseState.CLOSED
    )
    closed = header(seeded_analyst_read.client, closed_id)
    assert closed.closed
    assert closed.lifecycle[-1].state == "current"


def test_the_demo_case_at_intake_is_not_submitted(
    seeded_analyst_read: AnalystRead,
) -> None:
    head = header(seeded_analyst_read.client, "FCRM-2026-0151")
    assert head.submitted is False
    assert head.stage == "intake"
    assert head.route is None
    assert head.review_started is False


def test_a_live_header_withholds_the_route_until_review_started(
    analyst_read: AnalystRead,
    make_case_record: Callable[..., Any],
    make_snapshot: Callable[..., Any],
) -> None:
    record = make_case_record(
        state=CaseState.REVIEW,
        analyst_id=RAHUL,
        submitted_at=TODAY - timedelta(hours=3),
        run_id="run_1",
        current_submission_v=2,
    )
    analyst_read.service.store.records[record.case_id] = record
    snap = make_snapshot(
        band=3, composite=10.4, boundary_flag=True, boundary_distance=0.3
    )
    analyst_read.assessment.snapshots[snap.snapshot_id] = snap
    before = header(analyst_read.client, record.case_id)
    assert before.route is None
    assert before.review_started is False
    assert before.pending_acknowledgements == []
    assert before.is_demo
    assert before.completeness_decision == "ask"
    analyst_read.service.store.records[record.case_id] = record.model_copy(
        update={"review_started_at": TODAY}
    )
    after = header(analyst_read.client, record.case_id)
    assert after.route == "committee"
    assert after.review_started
    assert after.pending_acknowledgements == ["boundary"]
    assert after.ack_message is not None


def test_a_band_change_and_a_boundary_flag_are_owed_until_acknowledged(
    analyst_read: AnalystRead,
    make_case_record: Callable[..., Any],
    make_snapshot: Callable[..., Any],
) -> None:
    record = make_case_record(
        state=CaseState.REVIEW,
        analyst_id=RAHUL,
        submitted_at=TODAY - timedelta(hours=3),
        review_started_at=TODAY,
    )
    analyst_read.service.store.records[record.case_id] = record
    snap = make_snapshot(
        band=3,
        composite=10.4,
        boundary_flag=True,
        boundary_distance=0.3,
        previous_band=2,
        previous_composite=9.8,
        band_changed=True,
    )
    analyst_read.assessment.snapshots[snap.snapshot_id] = snap
    owed = header(analyst_read.client, record.case_id)
    assert owed.pending_acknowledgements == ["band_change", "boundary"]
    assert owed.ack_message is not None
    assert "2 → 3" in owed.ack_message
    analyst_read.reads.acks.extend(
        [
            (
                record.case_id,
                Acknowledgement("band_change", snap.computed_at + timedelta(minutes=1)),
            ),
            (
                record.case_id,
                Acknowledgement("boundary", snap.computed_at + timedelta(minutes=1)),
            ),
        ]
    )
    cleared = header(analyst_read.client, record.case_id)
    assert cleared.pending_acknowledgements == []


def test_the_analyst_clock_pauses_while_a_round_is_with_the_owner(
    analyst_read: AnalystRead,
    make_case_record: Callable[..., Any],
    today: None,
) -> None:
    from fcrm_case.domain.stage import stage_changed_event
    from fcrm_contracts.api.case_internal import StatePatch, UiActor

    submitted = TODAY - timedelta(hours=6)
    record = make_case_record(
        state=CaseState.CLARIFICATION,
        analyst_id=RAHUL,
        submitted_at=submitted,
        activity_seq=1,
    )
    analyst_read.service.store.records[record.case_id] = record
    actor = UiActor(id="svc:caseflow", role="fcrm_service", component="caseflow")
    entry = analyst_read.service.store.activity.stage(
        record.case_id,
        1,
        stage_changed_event(
            CaseState.REVIEW, StatePatch(stage=CaseState.CLARIFICATION), actor
        ),
    )
    analyst_read.service.store.activity.entries[-1] = entry.model_copy(
        update={"at": TODAY - timedelta(hours=4)}
    )
    head = header(analyst_read.client, record.case_id)
    assert head.clocks.analyst_paused is True
    assert head.clocks.total_elapsed == "6h 00m"
    assert head.clocks.analyst_active == "2h 00m"


@pytest.mark.parametrize("case_id", ["FCRM-2099-0001", "nonsense"])
def test_an_unknown_case_is_a_404(
    seeded_analyst_read: AnalystRead, case_id: str
) -> None:
    assert (
        seeded_analyst_read.client.get(f"/cases/{case_id}/workbench").status_code == 404
    )


def test_a_discarded_draft_reads_as_missing(
    analyst_read: AnalystRead, make_case_record: Callable[..., Any]
) -> None:
    record = make_case_record(discarded_at=TODAY, discard_reason="not needed")
    analyst_read.service.store.records[record.case_id] = record
    assert (
        analyst_read.client.get(f"/cases/{record.case_id}/workbench").status_code == 404
    )


def test_any_analyst_may_read_another_analysts_case(
    seeded_analyst_read: AnalystRead, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-mshah")
    assert (
        header(seeded_analyst_read.client, "FCRM-2026-0140").analyst_name
        == "Rahul Iyer"
    )

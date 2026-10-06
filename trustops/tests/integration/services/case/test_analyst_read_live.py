"""S1's analyst read routes against a real MongoDB 8 replica set, over the seed
the startup loads (80 cases: 0151 at intake with nothing else, 14 open and
submitted, 65 closed): the queue aggregation, the dashboard, the workbench header
and the one write a read makes (``review_started_at``).

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28517/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/integration/services/case/test_analyst_read_live.py
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fcrm_case.analyst_read_store import MongoAnalystReadStore, QueueQuery
from fcrm_case.store import build_cases_repository
from fcrm_contracts.api.case_analyst_read import (
    AssessmentSummary,
    DashboardAnalyst,
    HandoffView,
    QueueItem,
    RiskView,
    WorkbenchHeader,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from pymongo import AsyncMongoClient, monitoring

if TYPE_CHECKING:
    from conftest import LiveCase

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

RAHUL = "u-riyer"
RAHUL_QUEUE = [
    "FCRM-2026-0140",
    "FCRM-2026-0146",
    "FCRM-2026-0143",
    "FCRM-2026-0138",
]
#: The seed's 8 review and 4 clarification cases (each has a score snapshot).
REVIEW_AND_CLARIFICATION = [
    "FCRM-2026-0137",
    "FCRM-2026-0140",
    "FCRM-2026-0141",
    "FCRM-2026-0142",
    "FCRM-2026-0146",
    "FCRM-2026-0147",
    "FCRM-2026-0148",
    "FCRM-2026-0150",
    "FCRM-2026-0136",
    "FCRM-2026-0139",
    "FCRM-2026-0143",
    "FCRM-2026-0144",
]


class CommandLog(monitoring.CommandListener):
    """Records the name of every command a client starts."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        if event.command_name not in {"hello", "isMaster", "ismaster", "ping"}:
            self.names.append(event.command_name)

    def succeeded(self, event: monitoring.CommandSucceededEvent) -> None:
        return None

    def failed(self, event: monitoring.CommandFailedEvent) -> None:
        return None


async def ids(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["items"]]


# ── the queue ───────────────────────────────────────────────────────────────────


async def test_my_queue_is_ordered_by_what_needs_me_first(
    live_analyst: httpx.AsyncClient,
) -> None:
    response = await live_analyst.get("/queue")
    assert await ids(response) == RAHUL_QUEUE
    assert response.json()["total"] == 4


async def test_the_queue_is_one_aggregate_command(live_case: LiveCase) -> None:
    log = CommandLog()
    client: AsyncMongoClient[Any] = AsyncMongoClient(
        URI, tz_aware=True, uuidRepresentation="standard", event_listeners=[log]
    )
    try:
        db = client[live_case.db.name]
        store = MongoAnalystReadStore(client, db, build_cases_repository(db))
        page = await store.queue(
            QueueQuery(
                scope="all",
                viewer_id=RAHUL,
                stage=None,
                band=None,
                limit=50,
                offset=0,
            )
        )
        commands = list(log.names)
    finally:
        await client.close()
    assert page.total == 14
    assert commands == ["aggregate"]


async def test_everyones_queue_filters_and_pages(
    live_analyst: httpx.AsyncClient,
) -> None:
    everyone = await live_analyst.get("/queue", params={"scope": "all", "limit": 6})
    assert everyone.json()["total"] == 14
    seen = await ids(everyone)
    cursor = everyone.json()["next_cursor"]
    while cursor is not None:
        page = await live_analyst.get(
            "/queue", params={"scope": "all", "limit": 6, "cursor": cursor}
        )
        seen += await ids(page)
        cursor = page.json()["next_cursor"]
    assert len(seen) == len(set(seen)) == 14
    expected = {
        ("review", None): 8,
        ("awaiting_business", None): 4,
        ("committee", None): 2,
        ("fast_lane", None): 1,
        (None, "4"): 1,
        (None, "1"): 6,
        ("closed", None): 65,
        ("review", "2"): 3,
    }
    for (stage, band), total in expected.items():
        params: dict[str, str] = {"scope": "all", "limit": "1"}
        if stage:
            params["stage"] = stage
        if band:
            params["band"] = band
        response = await live_analyst.get("/queue", params=params)
        assert response.json()["total"] == total, (stage, band)


async def test_the_seeded_queue_row_has_the_stored_score(
    live_analyst: httpx.AsyncClient,
) -> None:
    body = (await live_analyst.get("/queue")).json()
    first = QueueItem.model_validate(body["items"][0])
    assert first.id == "FCRM-2026-0140"
    assert (first.band, first.band_label) == (1, "Low")
    assert first.fast_lane is not None
    assert first.mine


async def test_the_demo_case_is_not_queued_until_it_is_submitted(
    live_analyst: httpx.AsyncClient,
) -> None:
    everyone = await live_analyst.get("/queue", params={"scope": "all", "limit": 200})
    assert "FCRM-2026-0151" not in await ids(everyone)
    unassigned = await live_analyst.get("/queue", params={"scope": "unassigned"})
    assert unassigned.json()["total"] == 0


async def test_a_live_score_is_withheld_in_the_queue_until_review_starts(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    cases = live_case.db["cases"]
    await cases.update_one(
        {"_id": "FCRM-2026-0140"},
        {"$set": {"run_id": "run_live"}},
    )
    await live_case.db["score_snapshots"].insert_one(
        {
            "_id": "snap_live",
            "snapshot_id": "snap_live",
            "case_id": "FCRM-2026-0140",
            "computed_at": datetime(2026, 10, 6, tzinfo=UTC),
            "purpose": "proposed",
            "composite": 10.4,
            "band": 3,
            "route": "committee",
            "boundary_flag": True,
            "boundary_distance": 0.3,
            "domains": {},
            "band_changed": False,
        }
    )
    row = (await live_analyst.get("/queue")).json()["items"][0]
    assert (row["id"], row["band"], row["composite"]) == ("FCRM-2026-0140", None, None)
    await cases.update_one(
        {"_id": "FCRM-2026-0140"},
        {"$set": {"review_started_at": datetime(2026, 10, 6, tzinfo=UTC)}},
    )
    row = (await live_analyst.get("/queue")).json()["items"][0]
    assert (row["band"], row["composite"]) == (3, 10.4)


# ── the dashboard ───────────────────────────────────────────────────────────────


async def test_the_dashboard_is_computed_from_the_seeded_cases(
    live_analyst: httpx.AsyncClient,
) -> None:
    response = await live_analyst.get("/dashboard/analyst")
    assert response.status_code == 200, response.text
    dash = DashboardAnalyst.model_validate(response.json())
    assert dash.kpis.open_assigned == 4
    assert dash.kpis.closed_cases == 65
    assert dash.kpis.avg_days_intake_to_decision == pytest.approx(3.3, abs=0.05)
    assert [b.count for b in dash.band_distribution] == [19, 48, 11, 1]
    assert sum(m.count for m in dash.closed_by_month) == 65
    assert [m.id for m in dash.mine] == RAHUL_QUEUE
    assert dash.control_health.total == 24


# ── the workbench header ────────────────────────────────────────────────────────


async def test_the_demo_case_is_untouched_at_intake(
    live_analyst: httpx.AsyncClient,
) -> None:
    response = await live_analyst.get("/cases/FCRM-2026-0151/workbench")
    assert response.status_code == 200, response.text
    head = WorkbenchHeader.model_validate(response.json())
    assert head.stage == "intake"
    assert not head.submitted
    assert head.route is None
    assert not head.review_started
    assert head.pending_acknowledgements == []


async def test_every_review_and_clarification_case_reads_with_its_snapshot(
    live_analyst: httpx.AsyncClient,
) -> None:
    assert len(REVIEW_AND_CLARIFICATION) == 12
    for case_id in REVIEW_AND_CLARIFICATION:
        response = await live_analyst.get(f"/cases/{case_id}/workbench")
        assert response.status_code == 200, (case_id, response.text)
        head = WorkbenchHeader.model_validate(response.json())
        assert head.submitted, case_id
        assert head.review_started, case_id
        assert head.route in {"analyst", "committee"}, case_id
    unknown = await live_analyst.get("/cases/FCRM-2099-0001/workbench")
    assert unknown.status_code == 404


async def test_the_clock_pauses_from_the_stage_change_log(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    case_id = "FCRM-2026-0143"
    await live_case.db["activity"].insert_one(
        {
            "case_id": case_id,
            "seq": 1,
            "at": datetime(2026, 10, 2, tzinfo=UTC),
            "type": "case.stage_changed",
            "detail": {"from_stage": "review", "stage": "clarification"},
            "visibility": "all",
            "summary": "x",
            "actor": {"id": "svc:caseflow", "role": "fcrm_service", "component": "c"},
        }
    )
    head = WorkbenchHeader.model_validate(
        (await live_analyst.get(f"/cases/{case_id}/workbench")).json()
    )
    assert head.clocks.analyst_paused is True


async def test_an_acknowledgement_in_the_ledger_clears_what_is_owed(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    case_id = "FCRM-2026-0146"
    await live_case.db["score_snapshots"].insert_one(
        {
            "_id": "snap_ack",
            "snapshot_id": "snap_ack",
            "case_id": case_id,
            "computed_at": datetime(2026, 10, 6, tzinfo=UTC),
            "purpose": "confirmed",
            "composite": 10.4,
            "band": 3,
            "route": "committee",
            "boundary_flag": True,
            "boundary_distance": 0.3,
            "domains": {},
            "previous_composite": 4.0,
            "previous_band": 1,
            "band_changed": True,
            "gate_id": "gate_ack_live",
        }
    )
    await live_case.db["cases"].update_one(
        {"_id": case_id},
        {"$set": {"review_started_at": datetime(2026, 10, 6, tzinfo=UTC)}},
    )
    owed = WorkbenchHeader.model_validate(
        (await live_analyst.get(f"/cases/{case_id}/workbench")).json()
    )
    assert owed.pending_acknowledgements == ["band_change"]
    await live_case.db["analyst_decisions"].insert_one(
        {
            "_id": "gate_ack_live",
            "gate_id": "gate_ack_live",
            "case_id": case_id,
            "gate_type": "ack",
            "decision": "band_change",
            "fingerprint": "f" * 16,
            "decided_by": RAHUL,
            "decided_at": datetime(2026, 10, 6, 1, tzinfo=UTC),
        }
    )
    cleared = WorkbenchHeader.model_validate(
        (await live_analyst.get(f"/cases/{case_id}/workbench")).json()
    )
    assert cleared.pending_acknowledgements == []


async def test_the_queue_reads_a_confirmed_fast_lane_from_the_ledger(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    first = (await live_analyst.get("/queue")).json()["items"][0]
    assert first["id"] == "FCRM-2026-0140"
    assert first["fast_lane"] == {"proposed": True, "confirmed": False}
    await live_case.db["analyst_decisions"].insert_one(
        {
            "_id": "gate_fast_live",
            "gate_id": "gate_fast_live",
            "case_id": "FCRM-2026-0140",
            "gate_type": "fast_lane_confirm",
            "decision": "confirm",
            "fingerprint": "f" * 16,
            "decided_by": RAHUL,
            "decided_at": datetime(2026, 10, 6, tzinfo=UTC),
        }
    )
    again = (await live_analyst.get("/queue")).json()["items"][0]
    assert again["fast_lane"] == {"proposed": True, "confirmed": True}


async def test_the_completeness_decision_and_the_recorded_rows_come_from_the_ledger(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    case_id = "FCRM-2026-0146"
    db = live_case.db
    await db["completeness_views"].insert_one(
        {
            "_id": case_id,
            "case_id": case_id,
            "run_id": "run_1",
            "report": {"missing": [], "stale": [], "contradictory": []},
            "questions": [],
            "gate_id": "gate_completeness_live",
            "proposal_id": None,
            "updated_at": datetime(2026, 10, 6, tzinfo=UTC),
        }
    )
    await db["analyst_decisions"].insert_one(
        {
            "_id": "gate_completeness_live",
            "gate_id": "gate_completeness_live",
            "case_id": case_id,
            "gate_type": "completeness",
            "decision": "ask",
            "fingerprint": "f" * 16,
            "decided_by": RAHUL,
            "decided_at": datetime(2026, 10, 6, tzinfo=UTC),
        }
    )
    await db["planned_controls"].insert_one(
        {
            "_id": "pc_live",
            "planned_id": "pc_live",
            "case_id": case_id,
            "control_id": "CTL-T01",
            "description": "Corridor review",
            "owner": "FCRM Ops",
            "due": "2026-11-30",
            "created_at": datetime(2026, 10, 6, tzinfo=UTC),
            "created_by": RAHUL,
        }
    )
    await db["gaps"].insert_one(
        {
            "_id": "gap_live",
            "gap_id": "gap_live",
            "case_id": case_id,
            "text": "no clause",
            "scope": None,
            "created_at": datetime(2026, 10, 6, tzinfo=UTC),
            "created_by": RAHUL,
        }
    )
    head = (await live_analyst.get(f"/cases/{case_id}/workbench")).json()
    assert head["completeness_decision"] == "ask"
    summary = (await live_analyst.get(f"/cases/{case_id}/assessment")).json()
    assert summary["completeness"]["gate_open"] is False
    risk = (await live_analyst.get(f"/cases/{case_id}/risk")).json()
    assert [p["id"] for p in risk["planned"]] == ["pc_live"]
    assert (risk["planned"][0]["owner"], risk["planned"][0]["due"]) == (
        "FCRM Ops",
        "2026-11-30",
    )


# ── the tabs ────────────────────────────────────────────────────────────────────


async def test_every_review_and_clarification_case_reads_in_every_tab(
    live_analyst: httpx.AsyncClient,
) -> None:
    for case_id in REVIEW_AND_CLARIFICATION:
        for tab in ("assessment", "risk", "policy", "handoff"):
            response = await live_analyst.get(f"/cases/{case_id}/{tab}")
            assert response.status_code == 200, (case_id, tab, response.text)
        risk = RiskView.model_validate(
            (await live_analyst.get(f"/cases/{case_id}/risk")).json()
        )
        assert risk.score.withheld is False, case_id
        assert risk.areas, case_id
        assert all(a.controls for a in risk.areas), case_id


async def test_the_demo_case_has_nothing_to_show_at_intake(
    live_analyst: httpx.AsyncClient,
) -> None:
    summary = AssessmentSummary.model_validate(
        (await live_analyst.get("/cases/FCRM-2026-0151/assessment")).json()
    )
    assert summary.ready is False
    assert summary.score.withheld is True
    risk = RiskView.model_validate(
        (await live_analyst.get("/cases/FCRM-2026-0151/risk")).json()
    )
    assert risk.areas == []


async def test_opening_the_risk_view_stores_review_started_once(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    case_id = "FCRM-2026-0140"
    cases = live_case.db["cases"]
    before = await cases.find_one({"_id": case_id})
    assert before["review_started_at"] is None
    assert (await live_analyst.get(f"/cases/{case_id}/risk")).status_code == 200
    first = (await cases.find_one({"_id": case_id}))["review_started_at"]
    assert first is not None
    assert (await live_analyst.get(f"/cases/{case_id}/risk")).status_code == 200
    assert (await cases.find_one({"_id": case_id}))["review_started_at"] == first


async def test_a_pack_and_its_committee_are_read_from_stored_rows(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    case_id = "FCRM-2026-0138"
    await live_case.db["handoff_views"].insert_one(
        {
            "_id": case_id,
            "case_id": case_id,
            "run_id": "run_1",
            "pack_id": "pack_live",
            "pack_version": 1,
            "content_hash": "a" * 64,
            "checks": {
                "locked": True,
                "all_items_reviewed": True,
                "boundary_and_band_acknowledged": True,
                "business_summary_present": True,
            },
            "audit_head_seq": 60,
            "audit_head_hash": "b" * 64,
            "gate_id": None,
            "updated_at": datetime(2026, 10, 6, tzinfo=UTC),
        }
    )
    await live_case.db["activity"].insert_one(
        {
            "case_id": case_id,
            "seq": 1,
            "at": datetime(2026, 10, 6, 1, tzinfo=UTC),
            "type": "pack.released",
            "visibility": "analyst",
            "summary": "Pack v1 released",
            "detail": {"released_to": ["Ana Mendes", "Joseph Okafor", "Lin Chen"]},
            "actor": {"id": "svc:case", "role": "system", "component": "case"},
        }
    )
    view = HandoffView.model_validate(
        (await live_analyst.get(f"/cases/{case_id}/handoff")).json()
    )
    assert view.pack is not None
    assert (view.pack.pack_id, view.pack.status) == ("pack_live", "released")
    assert view.released_to == ["Ana Mendes", "Joseph Okafor", "Lin Chen"]
    assert all(c.ok for c in view.release_checks)
    assert view.band == 4


# ── the one write a read makes ──────────────────────────────────────────────────


async def test_review_starts_once_and_the_first_time_stands(
    live_case: LiveCase,
) -> None:
    store = live_case.app.state.analyst_reads
    first = datetime(2026, 10, 6, 9, tzinfo=UTC)
    later = datetime(2026, 10, 6, 10, tzinfo=UTC)
    started = await store.mark_review_started("FCRM-2026-0140", RAHUL, first)
    again = await store.mark_review_started("FCRM-2026-0140", "u-mshah", later)
    assert started == first
    assert again == first
    assert await store.mark_review_started("FCRM-2099-0001", RAHUL, first) is None
    stored = await live_case.db["cases"].find_one({"_id": "FCRM-2026-0140"})
    assert stored["review_started_by"] == RAHUL


async def test_the_queue_uses_its_indexes(live_case: LiveCase) -> None:
    names = {i["name"] async for i in await live_case.db["cases"].list_indexes()}
    assert {"state_submitted_idx", "analyst_state_idx"} <= names
    snaps = {
        i["name"] async for i in await live_case.db["score_snapshots"].list_indexes()
    }
    assert "case_computed" in snaps


# ── the activity feed and the outcome ───────────────────────────────────────────


async def _log(live_case: LiveCase, case_id: str, rows: int) -> None:
    """``rows`` activity rows for ``case_id`` with the case's own gap-free seq."""
    docs = [
        {
            "case_id": case_id,
            "seq": n,
            "at": datetime(2026, 10, 6, n, tzinfo=UTC),
            "type": "score.recomputed" if n % 2 else "case.stage_changed",
            "visibility": "analyst" if n % 2 else "all",
            "summary": f"entry {n}",
            "detail": {"record_seq": 40 + n, "gate_id": f"gate_{n}"} if n % 2 else {},
            "actor": {"id": "u-riyer", "role": "fcrm_analyst", "component": "case"},
        }
        for n in range(1, rows + 1)
    ]
    await live_case.db["activity"].insert_many(docs)
    await live_case.db["cases"].update_one(
        {"_id": case_id}, {"$set": {"activity_seq": rows}}
    )


async def test_the_analyst_feed_pages_newest_first_over_the_real_log(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    await _log(live_case, "FCRM-2026-0140", 5)
    path = "/cases/FCRM-2026-0140/activity"
    first = (await live_analyst.get(path, params={"limit": 3})).json()
    assert [i["id"] for i in first["items"]] == ["ev_5", "ev_4", "ev_3"]
    assert first["items"][0]["actor"] == "Rahul Iyer"
    assert first["items"][0]["record_seq"] == 45
    assert first["items"][0]["gate_id"] == "gate_5"
    second = (
        await live_analyst.get(
            path, params={"limit": 3, "cursor": first["next_cursor"]}
        )
    ).json()
    assert [i["id"] for i in second["items"]] == ["ev_2", "ev_1"]
    assert second["next_cursor"] is None


async def test_the_owner_reads_a_plain_feed_of_the_same_log(
    live_case: LiveCase, live_caller: Callable[..., Any]
) -> None:
    await _log(live_case, "FCRM-2026-0140", 4)
    owner = await live_case.db["cases"].find_one({"_id": "FCRM-2026-0140"})
    owner_principal = Principal(
        sub=owner["owner"], roles=[Role.PO], name="Owner", typ="access"
    )
    async with live_caller(owner_principal) as client:
        response = await client.get("/cases/FCRM-2026-0140/activity")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [i["id"] for i in items] == ["ev_4", "ev_2"]
    assert all(set(i) == {"id", "at", "actor", "text"} for i in items)


async def test_the_outcome_reads_the_recorded_row_and_its_conditions(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    case_id = "FCRM-2026-0138"
    await live_case.db["outcomes"].insert_one(
        {
            "_id": case_id,
            "case_id": case_id,
            "status": "set",
            "request": None,
            "outcome": "approve_with_conditions",
            "mode": "committee",
            "decided_by": "svc:caseflow",
            "decided_at": datetime(2026, 10, 7, tzinfo=UTC),
            "pack_id": "pack_1",
            "reason": None,
            "plain_summary": "Approved with conditions.",
            "condition_ids": ["cnd_x"],
            "fingerprint": "f",
        }
    )
    await live_case.db["conditions"].insert_one(
        {
            "_id": "cnd_x",
            "condition_id": "cnd_x",
            "case_id": case_id,
            "text": "Review the vendor",
            "owner_id": None,
            "owner_label": "FCRM Ops",
            "due_label": "go-live",
            "due_date": "2027-02-01",
            "status": "scheduled",
            "source": {},
            "ref": "CND-1",
        }
    )
    result = (await live_analyst.get(f"/cases/{case_id}/outcome")).json()
    assert result["decided"] is True
    assert (result["outcome_label"], result["mode"], result["decided_by"]) == (
        "Approved with conditions",
        "committee",
        "Risk Committee",
    )
    assert [c["ref"] for c in result["conditions"]] == ["CND-1"]
    seeded = (await live_analyst.get("/cases/FCRM-2026-0140/outcome")).json()
    assert seeded["decided"] is False


# ── submissions and the tool routes ─────────────────────────────────────────────


async def _freeze_two_submissions(live_case: LiveCase) -> None:
    """Two frozen submissions of the demo case, as the PO surface writes them."""
    from fcrm_case.domain.po_rows import (
        CapturedBy,
        SnapshotCompletion,
        SnapshotValue,
        SubmissionVersionRow,
        submission_version_id,
    )
    from fcrm_common.mongo import Repository

    repo = Repository(
        live_case.db["submission_versions"],
        SubmissionVersionRow,
        id_of=lambda r: r.version_id,
    )
    for version, value, origin in (
        (1, None, "open"),
        (2, "Licence MX-114", "answered"),
    ):
        await repo.insert_one(
            SubmissionVersionRow(
                version_id=submission_version_id("FCRM-2026-0151", version),
                case_id="FCRM-2026-0151",
                version=version,
                captured_at=datetime(2026, 10, 1, 9 + version, tzinfo=UTC),
                captured_by=CapturedBy(sub="u-pnair", role="fcrm_po"),
                reason="submit" if version == 1 else "round",
                round=None if version == 1 else 1,
                field_values={"F11": SnapshotValue(value=value, origin=origin)},  # type: ignore[arg-type]
                document_ids=[],
                changed_field_ids=[],
                completion=SnapshotCompletion(filled=1, required=1),
            )
        )


async def test_the_submissions_and_their_diff_are_read_from_the_collection(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    await _freeze_two_submissions(live_case)
    listed = (await live_analyst.get("/cases/FCRM-2026-0151/submissions")).json()
    assert [v["version"] for v in listed["items"]] == [1, 2]
    diff = (
        await live_analyst.get(
            "/cases/FCRM-2026-0151/submissions/diff", params={"from": 1, "to": 2}
        )
    ).json()
    assert [(c["field_id"], c["before"], c["after"]) for c in diff["changed"]] == [
        ("F11", None, "Licence MX-114")
    ]
    missing = await live_analyst.get(
        "/cases/FCRM-2026-0151/submissions/diff", params={"from": 1, "to": 5}
    )
    assert missing.status_code == 404


async def test_the_service_route_answers_the_tool_in_its_own_model(
    live_case: LiveCase,
) -> None:
    from fcrm_contracts.tools.submission_diff import SubmissionDiffOutput

    await _freeze_two_submissions(live_case)
    http = live_case.http
    diff = await http.get(
        "/internal/cases/FCRM-2026-0151/submissions/diff",
        params={"from_version": 1, "to_version": 2},
    )
    assert diff.status_code == 200, diff.text
    assert [
        c.field_id
        for c in SubmissionDiffOutput.model_validate(diff.json()).changed_fields
    ] == ["F11"]


async def test_a_person_cannot_call_the_service_routes(
    live_case: LiveCase, live_analyst: httpx.AsyncClient
) -> None:
    refused = await live_analyst.get(
        "/internal/cases/FCRM-2026-0151/submissions/diff",
        params={"from_version": 1, "to_version": 2},
    )
    assert refused.status_code == 403


# ── the harness monitor ─────────────────────────────────────────────────────────


async def test_the_harness_snapshot_is_stored_replaced_and_read_by_staff_only(
    live_case: LiveCase,
    live_caller: Callable[..., Any],
    make_harness_push: Callable[..., Any],
) -> None:
    case_id = "FCRM-2026-0140"
    http = live_case.http  # the service account
    first = make_harness_push(case_id, lines=2, audit_records=40)
    pushed = await http.put(
        f"/internal/cases/{case_id}/harness", json=first.model_dump(mode="json")
    )
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["lines"] == 2
    second = make_harness_push(case_id, lines=4, audit_records=41)
    await http.put(
        f"/internal/cases/{case_id}/harness", json=second.model_dump(mode="json")
    )
    stored = await live_case.db["harness_views"].count_documents({"_id": case_id})
    assert stored == 1
    analyst = Principal(sub=RAHUL, roles=[Role.ANALYST], name="Rahul", typ="access")
    async with live_caller(analyst) as client:
        snapshot = (await client.get(f"/cases/{case_id}/harness")).json()
        assert [line["seq"] for line in snapshot["log"]] == [1, 2, 3, 4]
        header = (await client.get(f"/cases/{case_id}/workbench")).json()
        assert header["audit"] == {"count": 41, "verified": True}
    observer = Principal(sub="u-obs", roles=[Role.OBSERVER], name="O", typ="access")
    async with live_caller(observer) as client:
        assert (await client.get(f"/cases/{case_id}/harness")).status_code == 200
    owner = await live_case.db["cases"].find_one({"_id": case_id})
    po = Principal(sub=owner["owner"], roles=[Role.PO], name="PO", typ="access")
    async with live_caller(po) as client:
        assert (await client.get(f"/cases/{case_id}/harness")).status_code == 403
        feed = await client.get(f"/cases/{case_id}/activity")
        assert "agent line" not in feed.text


async def test_a_case_with_no_pushed_snapshot_has_no_monitor(
    live_analyst: httpx.AsyncClient,
) -> None:
    response = await live_analyst.get("/cases/FCRM-2026-0140/harness")
    assert response.status_code == 404


async def test_caseflows_reporter_pushes_into_the_real_s1_and_staff_read_it(
    live_case: LiveCase, live_caller: Callable[..., Any]
) -> None:
    """The H1 side end to end: the reporter assembles its snapshot from a run, an
    audit chain and the gates, sends it through its own ``CaseClient`` to the live
    S1 app, and the analyst reads back what it sent.
    """
    from datetime import timedelta

    from fcrm_caseflow.clients.case_client import CaseClient
    from fcrm_caseflow.harness_view import HarnessReporter
    from fcrm_caseflow.store import (
        DEFAULT_PINS,
        case_run_from_document,
        new_case_run_document,
    )
    from fcrm_common.http import ServiceClient
    from fcrm_contracts.api.audit import AuditRecord, VerifyResult

    case_id = "FCRM-2026-0140"
    document = new_case_run_document(
        run_id="run_live", case_id=case_id, pins=DEFAULT_PINS
    )
    document["counters"] = {"agents_run": 3, "tokens_used": 30_000}
    document["trace_id"] = "trace-live-1"
    run = case_run_from_document(document)
    base = datetime(2026, 10, 5, 12, tzinfo=UTC)
    chain = [
        AuditRecord(
            case_id=case_id,
            seq=n,
            type=kind,
            actor="system",
            summary=f"record {n}",
            data={},
            prev_hash="0" * 64,
            hash="1" * 64,
            created_at=base + timedelta(seconds=n),
        )
        for n, kind in enumerate(
            ("workflow.started", "guard.input", "proposal.research", "score.computed"),
            start=1,
        )
    ]

    class Runs:
        async def get(self, run_id: str) -> Any:
            return run

    class Gates:
        async def count_open_for_run(self, run_id: str) -> int:
            return 2

    class Chain:
        async def list_records(self, _case: str) -> list[AuditRecord]:
            return chain

        async def verify(self, _case: str) -> VerifyResult:
            return VerifyResult(case_id=case_id, record_count=len(chain), ok=True)

    import httpx as _httpx

    client = ServiceClient(
        "http://case.test",
        dependency="case",
        caller="caseflow",
        token_secret="x" * 32,
        transport=_httpx.ASGITransport(app=live_case.app),
    )
    try:
        reporter = HarnessReporter(Runs(), Gates(), Chain(), CaseClient(client))
        assert await reporter.report(case_id, "run_live") is True
    finally:
        await client.aclose()

    analyst = Principal(sub=RAHUL, roles=[Role.ANALYST], name="Rahul", typ="access")
    async with live_caller(analyst) as http:
        snapshot = (await http.get(f"/cases/{case_id}/harness")).json()
        header = (await http.get(f"/cases/{case_id}/workbench")).json()
    assert snapshot["status"] == "waiting_on_person"
    assert snapshot["trace_id"] == "trace-live-1"
    assert snapshot["counters"]["agents_run"] == 3
    assert snapshot["counters"]["tokens_used"] == 30_000
    assert snapshot["counters"]["chain_verified"] is True
    assert [line["who"] for line in snapshot["log"]] == [
        "H1 core",
        "P5 guard",
        "research agent",
        "S2 scoring",
    ]
    assert header["audit"] == {"count": 4, "verified": True}

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
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fcrm_case.analyst_read_store import MongoAnalystReadStore, QueueQuery
from fcrm_case.store import build_cases_repository
from fcrm_common.auth import current_user
from fcrm_contracts.api.case_analyst_read import (
    DashboardAnalyst,
    QueueItem,
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


@pytest.fixture
async def analyst(live_case: LiveCase) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client of the live S1 app, calling as Rahul Iyer."""
    app = live_case.app
    app.dependency_overrides[current_user] = lambda: Principal(
        sub=RAHUL, roles=[Role.ANALYST], name="Rahul Iyer", typ="access"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://case.test"
    ) as client:
        yield client


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
    analyst: httpx.AsyncClient,
) -> None:
    response = await analyst.get("/queue")
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


async def test_everyones_queue_filters_and_pages(analyst: httpx.AsyncClient) -> None:
    everyone = await analyst.get("/queue", params={"scope": "all", "limit": 6})
    assert everyone.json()["total"] == 14
    seen = await ids(everyone)
    cursor = everyone.json()["next_cursor"]
    while cursor is not None:
        page = await analyst.get(
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
        response = await analyst.get("/queue", params=params)
        assert response.json()["total"] == total, (stage, band)


async def test_the_seeded_queue_row_has_the_stored_score(
    analyst: httpx.AsyncClient,
) -> None:
    body = (await analyst.get("/queue")).json()
    first = QueueItem.model_validate(body["items"][0])
    assert first.id == "FCRM-2026-0140"
    assert (first.band, first.band_label) == (1, "Low")
    assert first.fast_lane is not None
    assert first.mine


async def test_the_demo_case_is_not_queued_until_it_is_submitted(
    analyst: httpx.AsyncClient,
) -> None:
    everyone = await analyst.get("/queue", params={"scope": "all", "limit": 200})
    assert "FCRM-2026-0151" not in await ids(everyone)
    unassigned = await analyst.get("/queue", params={"scope": "unassigned"})
    assert unassigned.json()["total"] == 0


async def test_a_live_score_is_withheld_in_the_queue_until_review_starts(
    live_case: LiveCase, analyst: httpx.AsyncClient
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
    row = (await analyst.get("/queue")).json()["items"][0]
    assert (row["id"], row["band"], row["composite"]) == ("FCRM-2026-0140", None, None)
    await cases.update_one(
        {"_id": "FCRM-2026-0140"},
        {"$set": {"review_started_at": datetime(2026, 10, 6, tzinfo=UTC)}},
    )
    row = (await analyst.get("/queue")).json()["items"][0]
    assert (row["band"], row["composite"]) == (3, 10.4)


# ── the dashboard ───────────────────────────────────────────────────────────────


async def test_the_dashboard_is_computed_from_the_seeded_cases(
    analyst: httpx.AsyncClient,
) -> None:
    response = await analyst.get("/dashboard/analyst")
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
    analyst: httpx.AsyncClient,
) -> None:
    response = await analyst.get("/cases/FCRM-2026-0151/workbench")
    assert response.status_code == 200, response.text
    head = WorkbenchHeader.model_validate(response.json())
    assert head.stage == "intake"
    assert not head.submitted
    assert head.route is None
    assert not head.review_started
    assert head.pending_acknowledgements == []


async def test_every_review_and_clarification_case_reads_with_its_snapshot(
    analyst: httpx.AsyncClient,
) -> None:
    assert len(REVIEW_AND_CLARIFICATION) == 12
    for case_id in REVIEW_AND_CLARIFICATION:
        response = await analyst.get(f"/cases/{case_id}/workbench")
        assert response.status_code == 200, (case_id, response.text)
        head = WorkbenchHeader.model_validate(response.json())
        assert head.submitted, case_id
        assert head.review_started, case_id
        assert head.route in {"analyst", "committee"}, case_id
    unknown = await analyst.get("/cases/FCRM-2099-0001/workbench")
    assert unknown.status_code == 404


async def test_the_clock_pauses_from_the_stage_change_log(
    live_case: LiveCase, analyst: httpx.AsyncClient
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
        (await analyst.get(f"/cases/{case_id}/workbench")).json()
    )
    assert head.clocks.analyst_paused is True


async def test_an_acknowledgement_clears_what_is_owed(
    live_case: LiveCase, analyst: httpx.AsyncClient
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
        }
    )
    await live_case.db["cases"].update_one(
        {"_id": case_id},
        {"$set": {"review_started_at": datetime(2026, 10, 6, tzinfo=UTC)}},
    )
    owed = WorkbenchHeader.model_validate(
        (await analyst.get(f"/cases/{case_id}/workbench")).json()
    )
    assert owed.pending_acknowledgements == ["band_change", "boundary"]
    when = datetime(2026, 10, 6, 1, tzinfo=UTC)
    await live_case.db["acknowledgements"].insert_many(
        [
            {"case_id": case_id, "kind": "band_change", "at": when},
            {"case_id": case_id, "kind": "boundary", "at": when},
        ]
    )
    cleared = WorkbenchHeader.model_validate(
        (await analyst.get(f"/cases/{case_id}/workbench")).json()
    )
    assert cleared.pending_acknowledgements == []


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
    acks = {
        i["name"] async for i in await live_case.db["acknowledgements"].list_indexes()
    }
    assert "case_at" in acks

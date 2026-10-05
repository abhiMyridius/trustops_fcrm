"""S1's outcome, close-request and condition writes on a real MongoDB 8 replica
set. The in-memory twins are covered by ``tests/unit/services/case``; what only a
real database can show is here: the outcome row, the conditions, the move to
``closed`` (with ``closed_at`` and ``outcome_ref``), the activity row, the
outbox events and the idempotency key commit **together or not at all**, a replay
writes nothing, and the indexes the queries rely on exist.

    docker run -d --name i4a-mongo -p 28317:28317 mongo:8.0 \\
        mongod --replSet rs0 --port 28317 --bind_ip_all
    docker exec i4a-mongo mongosh --quiet --port 28317 --eval \\
        'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:28317"}]}).ok'
    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28317/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/integration/live_mongo/test_case_outcome_live.py
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.outcome import CloseRequestRow
from fcrm_case.errors import CloseAlreadyRequestedError, ConditionClosedError
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_close import ConditionPatch
from fcrm_contracts.api.case_internal import UiActor
from fcrm_contracts.enums import CaseState, Outcome

if TYPE_CHECKING:
    from conftest import LiveCase

pytestmark = pytest.mark.integration

DEMO = "FCRM-2026-0151"
URL = f"/internal/cases/{DEMO}/outcome"
ANALYST = UiActor(id="u-riyer", role="fcrm_analyst", component="case")


def condition(ref: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "condition_id": f"cnd_{ref.lower()}",
        "ref": ref,
        "text": f"Condition {ref}",
        "owner_id": "u-pnair",
        "owner_label": "Priya Nair",
        "due_label": "before go-live",
        "due_date": "2027-02-01",
        "status": "open",
        "origin": "overridden control CTL-T01",
    }
    return {**body, **overrides}


def committee_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "outcome": "approve_with_conditions",
        "mode": "committee",
        "decided_by": "committee",
        "pack_id": "pack_1",
        "plain_summary": "The committee approved this change with conditions.",
        "conditions": [
            condition("CND-1"),
            condition(
                "CND-2", owner_id=None, owner_label="FCRM Ops", status="scheduled"
            ),
            condition("CND-3", due_label="6 months", status="scheduled"),
        ],
    }
    return {**body, **overrides}


async def _counts(live: LiveCase) -> dict[str, int]:
    """How many rows the demo case has in each collection a close touches."""
    db = live.db
    outbox = await db["outbox"].count_documents({"type": "case.closed"})
    activated = await db["outbox"].count_documents({"type": "condition.activated"})
    return {
        "outcomes": await db["outcomes"].count_documents({"_id": DEMO}),
        "conditions": await db["conditions"].count_documents({"case_id": DEMO}),
        "closed": await db["cases"].count_documents(
            {"_id": DEMO, "state": CaseState.CLOSED.value}
        ),
        "stage_rows": await db["activity"].count_documents(
            {"case_id": DEMO, "type": "case.stage_changed", "detail.stage": "closed"}
        ),
        "case_closed_events": outbox,
        "condition_events": activated,
        "keys": await db["idempotency_keys"].count_documents({}),
    }


async def test_the_outcome_close_conditions_events_and_key_commit_together(
    live_case: LiveCase,
) -> None:
    response = await live_case.http.put(
        URL, json=committee_body(), headers={"Idempotency-Key": "k-1"}
    )
    assert response.status_code == 200
    assert response.json()["conditions_activated"] == 3

    case = await live_case.db["cases"].find_one({"_id": DEMO})
    assert case is not None
    assert case["state"] == "closed"
    assert case["outcome_ref"] == DEMO
    assert case["closed_at"] is not None
    assert case["decided_at"] == case["closed_at"]
    assert case["outcome_summary"].startswith("The committee approved")
    outcome = await live_case.db["outcomes"].find_one({"_id": DEMO})
    assert outcome is not None
    assert (outcome["status"], outcome["mode"], outcome["outcome"]) == (
        "set",
        "committee",
        "approve_with_conditions",
    )
    rows = await live_case.db["conditions"].find({"case_id": DEMO}).to_list()
    assert sorted(r["ref"] for r in rows) == ["CND-1", "CND-2", "CND-3"]
    assert {r["pack_id"] for r in rows} == {"pack_1"}
    assert {r["due_date"] for r in rows if r["ref"] != "CND-3"} == {"2027-02-01"}
    assert await _counts(live_case) == {
        "outcomes": 1,
        "conditions": 3,
        "closed": 1,
        "stage_rows": 1,
        "case_closed_events": 1,
        "condition_events": 3,
        "keys": 1,
    }


async def test_a_failure_after_some_writes_rolls_every_write_back(
    live_case: LiveCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conditions, the outcome row, the closed case and its activity row are
    written before the outbox; when the outbox write fails the whole transaction
    aborts and the case is exactly as it was.
    """
    before = await _counts(live_case)

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the outbox is down")

    monkeypatch.setattr(live_case.app.state.outcomes._outbox, "enqueue", boom)
    with pytest.raises(RuntimeError, match="outbox is down"):
        await live_case.http.put(
            URL, json=committee_body(), headers={"Idempotency-Key": "k-boom"}
        )
    assert await _counts(live_case) == before
    case = await live_case.db["cases"].find_one({"_id": DEMO})
    assert case is not None
    assert case["state"] != "closed"
    assert case.get("outcome_ref") is None
    assert case["closed_at"] is None


async def test_a_replay_writes_nothing_and_a_different_body_is_refused(
    live_case: LiveCase,
) -> None:
    first = await live_case.http.put(URL, json=committee_body())
    assert first.status_code == 200
    counts = await _counts(live_case)
    replay = await live_case.http.put(URL, json=committee_body())
    assert replay.status_code == 200
    assert replay.json() == {**first.json(), "replayed": True}
    assert await _counts(live_case) == counts
    other = await live_case.http.put(
        URL, json=committee_body(outcome="defer", conditions=[])
    )
    assert other.status_code == 409
    assert other.json()["type"].endswith("/outcome_already_set")
    assert await _counts(live_case) == counts


async def test_the_same_key_with_another_body_is_a_409_and_writes_nothing(
    live_case: LiveCase,
) -> None:
    await live_case.http.put(
        URL, json=committee_body(), headers={"Idempotency-Key": "k"}
    )
    counts = await _counts(live_case)
    clash = await live_case.http.put(
        URL, json=committee_body(conditions=[]), headers={"Idempotency-Key": "k"}
    )
    assert clash.status_code == 409
    assert clash.json()["type"].endswith("/idempotency_key_reused")
    assert await _counts(live_case) == counts


async def test_concurrent_identical_puts_close_the_case_once(
    live_case: LiveCase,
) -> None:
    results = await asyncio.gather(
        *[live_case.http.put(URL, json=committee_body()) for _ in range(4)]
    )
    assert [r.status_code for r in results] == [200] * 4
    assert sorted(r.json()["replayed"] for r in results) == [False, True, True, True]
    counts = await _counts(live_case)
    assert counts["conditions"] == 3
    assert counts["case_closed_events"] == 1


async def test_the_indexes_the_condition_queries_use_exist(live_case: LiveCase) -> None:
    conditions = {
        i["name"] async for i in await live_case.db["conditions"].list_indexes()
    }
    assert {"case_idx", "due_status"} <= conditions
    keys = {
        i["name"] async for i in await live_case.db["idempotency_keys"].list_indexes()
    }
    assert "at_ttl" in keys


# ── a delegated close request and the condition tracker ───────────────────────────


def close_request(**overrides: Any) -> CloseRequestRow:
    fields: dict[str, Any] = {
        "outcome": Outcome.APPROVE,
        "reason": "Band 2: closed under delegated authority.",
        "plain_summary": "Your change is approved. No conditions apply.",
        "requested_by": "u-riyer",
        "requested_at": utcnow(),
        "band": 2,
    }
    return CloseRequestRow(**{**fields, **overrides})


async def test_a_close_request_commits_its_row_activity_and_event_together(
    live_case: LiveCase,
) -> None:
    store = live_case.app.state.outcomes
    write = await store.request_close(DEMO, close_request(), actor=ANALYST)
    assert write is not None
    assert not write.replayed
    assert len(write.entries) == 1
    row = await live_case.db["outcomes"].find_one({"_id": DEMO})
    assert row is not None
    assert row["status"] == "requested"
    [event] = (
        await live_case.db["outbox"].find({"type": "case.close_requested"}).to_list()
    )
    assert event["case_id"] == DEMO
    again = await store.request_close(DEMO, close_request(), actor=ANALYST)
    assert again is not None
    assert again.replayed
    assert (
        await live_case.db["outbox"].count_documents({"type": "case.close_requested"})
        == 1
    )
    with pytest.raises(CloseAlreadyRequestedError):
        await store.request_close(
            DEMO, close_request(outcome=Outcome.DEFER), actor=ANALYST
        )


async def test_the_outcome_that_answers_a_close_request_replaces_it(
    live_case: LiveCase,
) -> None:
    await live_case.app.state.outcomes.request_close(
        DEMO, close_request(), actor=ANALYST
    )
    body = {
        "outcome": "approve",
        "mode": "delegated",
        "decided_by": "u-riyer",
        "reason": "Band 2: closed under delegated authority.",
        "plain_summary": "Your change is approved. No conditions apply.",
    }
    response = await live_case.http.put(URL, json=body)
    assert response.status_code == 200
    row = await live_case.db["outcomes"].find_one({"_id": DEMO})
    assert row is not None
    assert (row["status"], row["mode"], row["decided_by"]) == (
        "set",
        "delegated",
        "u-riyer",
    )


async def test_a_condition_update_commits_status_activity_and_event_together(
    live_case: LiveCase,
) -> None:
    await live_case.http.put(URL, json=committee_body())
    store = live_case.app.state.outcomes
    patch = ConditionPatch(status="closed", reason="Corridor review filed and checked.")
    write = await store.update_condition(
        "cnd_cnd-1", patch, by="u-riyer", actor=ANALYST
    )
    assert write is not None
    assert write.condition.status == "closed"
    stored = await live_case.db["conditions"].find_one({"_id": "cnd_cnd-1"})
    assert stored is not None
    assert (stored["status"], stored["updated_by"]) == ("closed", "u-riyer")
    assert (
        await live_case.db["outbox"].count_documents({"type": "condition.completed"})
        == 1
    )
    assert (
        await live_case.db["activity"].count_documents(
            {"case_id": DEMO, "type": "condition.updated", "visibility": "analyst"}
        )
        == 1
    )
    with pytest.raises(ConditionClosedError):
        await store.update_condition(
            "cnd_cnd-1", ConditionPatch(note="late"), by="u-riyer", actor=ANALYST
        )


async def test_the_latest_band_is_read_from_the_newest_score_snapshot(
    live_case: LiveCase,
) -> None:
    now = utcnow()
    for index, (band, age) in enumerate(
        ((3, timedelta(days=2)), (2, timedelta(days=1)))
    ):
        await live_case.db["score_snapshots"].insert_one(
            {
                "_id": f"snap_{index}",
                "snapshot_id": f"snap_{index}",
                "case_id": DEMO,
                "computed_at": now - age,
                "band": band,
            }
        )
    assert await live_case.app.state.outcomes.latest_band(DEMO) == 2

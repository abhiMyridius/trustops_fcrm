"""``fcrm_agent_worker.jobs.AgentJobStore`` and the H2 terminal transaction
against a real MongoDB 8 replica set (ADR-79): the claim pipeline on the
server's clock, ``mark_done`` and the outbox row committing together, and a
racing pair of claims taking one job once.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a throwaway replica set
(``CLAUDE.md`` has the recipe; never the stack's own Mongo)::

    FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28999/?replicaSet=rs0' \\
        uv run --project src/backend --all-packages pytest -m integration \\
        tests/unit/workers/agent-worker/test_jobs_live.py
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from fcrm_agent_worker.handler import mongo_transact
from fcrm_agent_worker.jobs import (
    AGENT_JOBS_COLLECTION,
    AGENT_OUTBOX_COLLECTION,
    AgentJobStore,
    ClaimOutcome,
)
from fcrm_common.events import build_envelope
from fcrm_common.ids import new_id
from fcrm_common.mongo import mongo_client
from fcrm_common.outbox import Outbox

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]


@pytest.fixture
async def db() -> Any:
    async with mongo_client(URI) as client:
        name = f"fcrm_h2_jobs_it_{new_id('db')[3:].lower()}"
        database = client[name]
        # Collections must exist before a transaction writes to them.
        await database.create_collection(AGENT_JOBS_COLLECTION)
        await database.create_collection(AGENT_OUTBOX_COLLECTION)
        yield client, database
        await client.drop_database(name)


async def test_claim_running_done_and_takeover_on_the_servers_clock(db: Any) -> None:
    _client, database = db
    first = AgentJobStore(database[AGENT_JOBS_COLLECTION], holder="h2_a")
    other = AgentJobStore(database[AGENT_JOBS_COLLECTION], holder="h2_b")
    await first.ensure_indexes()

    assert await first.claim("job-1", 0.3, case_id="C", agent="research") is (
        ClaimOutcome.CLAIMED
    )
    assert await other.claim("job-1", 60) is ClaimOutcome.RUNNING
    assert await first.claim("job-1", 60) is ClaimOutcome.RUNNING
    await asyncio.sleep(0.5)
    assert await other.claim("job-1", 60, case_id="C", agent="research") is (
        ClaimOutcome.CLAIMED
    )
    assert not await first.renew("job-1", 60)
    assert await other.renew("job-1", 60)

    doc = await database[AGENT_JOBS_COLLECTION].find_one({"_id": "job-1"})
    assert doc["holder"] == "h2_b"
    assert doc["case_id"] == "C"


async def test_mark_done_and_the_outbox_row_commit_together(db: Any) -> None:
    client, database = db
    jobs = AgentJobStore(database[AGENT_JOBS_COLLECTION], holder="h2_a")
    outbox = Outbox(database[AGENT_OUTBOX_COLLECTION])
    await outbox.ensure_indexes()
    transact = mongo_transact(client)
    envelope = build_envelope(
        "agent.failed",
        {"job_id": "job-1", "reason": "x"},
        case_id="C",
        run_id="R",
    )
    await jobs.claim("job-1", 60)

    async def finish(session: Any) -> bool:
        if not await jobs.mark_done(
            "job-1", outcome="blocked", event_id=envelope.event_id, session=session
        ):
            return False
        await outbox.enqueue(session, envelope)
        return True

    assert await transact(finish)
    assert not await transact(finish), "a second finish enqueues nothing"
    assert await database[AGENT_OUTBOX_COLLECTION].count_documents({}) == 1
    doc = await database[AGENT_JOBS_COLLECTION].find_one({"_id": "job-1"})
    assert (doc["status"], doc["event_id"]) == ("done", envelope.event_id)
    assert doc["finished_at"] is not None
    assert await jobs.claim("job-1", 60) is ClaimOutcome.DONE


async def test_a_failed_commit_leaves_the_job_claimed_and_no_row(db: Any) -> None:
    client, database = db
    jobs = AgentJobStore(database[AGENT_JOBS_COLLECTION], holder="h2_a")
    outbox = Outbox(database[AGENT_OUTBOX_COLLECTION])
    envelope = build_envelope("agent.done", {"job_id": "job-3"}, case_id="C")
    await jobs.claim("job-3", 60)

    async def finish_and_abort(session: Any) -> None:
        await jobs.mark_done(
            "job-3", outcome="done", event_id=envelope.event_id, session=session
        )
        await outbox.enqueue(session, envelope)
        raise RuntimeError("abort before commit")

    with pytest.raises(RuntimeError):
        await mongo_transact(client)(finish_and_abort)
    doc = await database[AGENT_JOBS_COLLECTION].find_one({"_id": "job-3"})
    assert doc["status"] == "running"
    assert await database[AGENT_OUTBOX_COLLECTION].count_documents({}) == 0
    await jobs.release("job-3")
    assert await database[AGENT_JOBS_COLLECTION].find_one({"_id": "job-3"}) is None


async def test_racing_claims_take_a_job_once(db: Any) -> None:
    _client, database = db
    stores = [
        AgentJobStore(database[AGENT_JOBS_COLLECTION], holder=f"h2_{n}")
        for n in range(8)
    ]
    outcomes = await asyncio.gather(*(s.claim("job-race", 60) for s in stores))
    assert outcomes.count(ClaimOutcome.CLAIMED) == 1
    assert set(outcomes) <= {ClaimOutcome.CLAIMED, ClaimOutcome.RUNNING}

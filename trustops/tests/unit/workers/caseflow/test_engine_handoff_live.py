"""The human handoff in the engine against a real MongoDB 8 replica set, with the real
outbox: what the in-memory collections of ``test_handoff.py`` only imitate.

Held here: a refused output rolls the whole transaction back (the gate stays open, the
run and the outbox are as they were), a late ``agent.done`` is kept while a person holds
the case, two failed jobs resume the run only on the last decision and the join then
fires the trigger the agent's own ``agent.done`` would have, and the stored run
replays to the state it is in.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set, like the other live
engine tests::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28917/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/unit/workers/caseflow/test_engine_handoff_live.py
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from conftest import RecordingAudit, clause_proposal, factor_proposal
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.engine import CaseflowEngine, mongo_transact
from fcrm_caseflow.eventstore import rebuild
from fcrm_caseflow.gates import GateStore, deterministic_gate_id
from fcrm_caseflow.handoff import HandoffError
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    AgentResultStore,
    CaseRun,
    CaseRunStore,
    new_case_run_document,
)
from fcrm_common.ids import new_id
from fcrm_common.mongo import mongo_client, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import AgentName, CaseState, GateStatus, GateType
from fcrm_contracts.events.envelope import Envelope
from pymongo.asynchronous.database import AsyncDatabase

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

CASE_ID = "FCRM-2026-0151"
RUN_ID = "run_live_handoff"
ANALYST = "u-riyer"
REASON = "The agent failed twice; I wrote it up from the policy text."


@dataclass(frozen=True)
class World:
    """The engine over a real database, with the stores it was built from."""

    client: Any
    database: AsyncDatabase[Any]
    engine: CaseflowEngine
    runs: CaseRunStore
    gates: GateStore
    results: AgentResultStore

    async def seed_assess(self, pending: list[str]) -> CaseRun:
        document = new_case_run_document(
            run_id=RUN_ID, case_id=CASE_ID, pins=DEFAULT_PINS
        )
        document["state"] = CaseState.ASSESS.value
        document["pending_agents"] = pending
        return await self.runs.create(document)

    async def send(self, event_type: str, payload: dict[str, Any]) -> None:
        envelope = Envelope(
            event_id=new_id("evt"),
            type=event_type,
            case_id=CASE_ID,
            run_id=RUN_ID,
            occurred_at=utcnow(),
            payload=payload,
        )
        await with_transaction(self.client, lambda s: self.engine.handle(envelope, s))

    async def fail(self, agent: str, job_id: str) -> None:
        await self.send(
            "agent.failed",
            {
                "job_id": job_id,
                "case_id": CASE_ID,
                "run_id": RUN_ID,
                "agent": agent,
                "attempt": 2,
                "reason": "guard4_rejected: schema mismatch",
            },
        )

    async def complete(self, job_id: str, output: Any) -> None:
        await self.send(
            "gate.decided",
            GateDecision(
                gate_id=deterministic_gate_id(RUN_ID, GateType.HANDOFF, job_id),
                gate_type=GateType.HANDOFF,
                case_id=CASE_ID,
                run_id=RUN_ID,
                decision="complete",
                reason=REASON,
                decided_by=ANALYST,
                decided_at=utcnow(),
                after={"output": output},
            ).model_dump(mode="json"),
        )

    async def run(self) -> CaseRun:
        run = await self.runs.get(RUN_ID)
        assert run is not None
        return run

    async def counts(self) -> tuple[int, int]:
        return (
            await self.database["run_events"].count_documents({}),
            await self.database["outbox"].count_documents({}),
        )

    async def agent_runs(self) -> list[str]:
        rows = self.database["outbox"].find({"type": "agent.run"})
        return [
            Envelope.model_validate_json(bytes(r["body"])).payload["agent"]
            async for r in rows
        ]


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with mongo_client(URI) as client:
        name = f"fcrm_caseflow_handoff_it_{new_id('db')[3:].lower()}"
        database = client[name]
        runs = CaseRunStore(database["case_runs"], database["run_events"])
        gates = GateStore(database["gates"])
        await runs.ensure_indexes()
        await gates.ensure_indexes()
        results = AgentResultStore(database["agent_results"])
        engine = CaseflowEngine(
            runs,
            gates,
            Outbox(database["outbox"]),
            RecordingAudit(),
            step_map=dict(STEP_FOR),
            transact=mongo_transact(client),
            results=results,
        )
        yield World(client, database, engine, runs, gates, results)
        await client.drop_database(name)


async def test_a_refused_output_rolls_the_whole_transaction_back(world: World) -> None:
    await world.seed_assess(["research", "assessment"])
    await world.fail("research", "job_r")
    before = await world.counts()

    with pytest.raises(HandoffError):
        await world.complete("job_r", [{"score": 4.1}])

    gate = await world.gates.get(
        deterministic_gate_id(RUN_ID, GateType.HANDOFF, "job_r")
    )
    assert gate is not None
    assert gate["status"] == GateStatus.OPEN.value
    assert (await world.run()).state is CaseState.HANDOFF
    assert await world.counts() == before


async def test_a_job_that_finishes_while_a_person_holds_the_case_is_kept_not_applied(
    world: World,
) -> None:
    await world.seed_assess(["research", "assessment"])
    await world.fail("research", "job_r")

    await world.send(
        "agent.done",
        {
            "job_id": "job_a",
            "case_id": CASE_ID,
            "run_id": RUN_ID,
            "agent": "assessment",
            "output": {"accepted": [factor_proposal("channel", 3, 4)]},
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "model": "agent-model",
            "attempt": 1,
        },
    )

    run = await world.run()
    assert run.state is CaseState.HANDOFF
    assert run.pending_agents == ("research",)
    assert [
        r.job_id for r in await world.results.list_for(RUN_ID, AgentName.ASSESSMENT)
    ] == ["job_a"]
    assert await world.agent_runs() == []


async def test_two_failed_jobs_resume_the_run_only_on_the_last_decision(
    world: World,
) -> None:
    await world.seed_assess(["research", "assessment"])
    await world.fail("research", "job_r")
    await world.fail("assessment", "job_a")
    assert (await world.run()).suspended_from is CaseState.ASSESS

    await world.complete("job_r", [clause_proposal("FFIEC-TPPP-04", "channel")])
    held = await world.run()
    assert (held.state, held.pending_agents) == (CaseState.HANDOFF, ("assessment",))

    await world.complete("job_a", [factor_proposal("channel", 3, 4)])

    done = await world.run()
    assert done.state is CaseState.CRITIC  # the join fired as for an agent's own done
    assert done.suspended_from is None
    # The real step map: the join's own step (assess.publish) is enqueued and the
    # critic waits for it, exactly as when the agent itself had finished.
    assert len(done.pending_steps) >= 2
    assert await world.agent_runs() == []
    steps = [
        Envelope.model_validate_json(bytes(r["body"])).payload["step"]
        async for r in world.database["outbox"].find({"type": "harness.step"})
    ]
    assert "assess.publish" in steps
    assert steps.count("handoff.open") == 4  # two opened, two completed
    humans = [
        r.model
        for agent in (AgentName.RESEARCH, AgentName.ASSESSMENT)
        for r in await world.results.list_for(RUN_ID, agent)
    ]
    assert humans == ["human", "human"]
    replayed = rebuild(
        CaseState.ASSESS, await world.runs.load_recorded_transitions(RUN_ID)
    )
    assert replayed is done.state

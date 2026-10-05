"""The engine's intake additions against a real MongoDB 8 replica set: the
upserts and the ``$pull``/``$set`` operators the fake collections only imitate
(agent results, the per-job pending join, deterministic gates opened by a step,
the ``ingest.run`` row), each inside a real transaction.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set, like
``test_engine_live.py``::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27517/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/unit/workers/caseflow/test_engine_intake_live.py
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from conftest import RecordingAudit, agent_done_body, document_stored_payload
from fcrm_caseflow.dispatch import AgentDispatch
from fcrm_caseflow.engine import CaseflowEngine, mongo_transact
from fcrm_caseflow.gates import GateStore, deterministic_gate_id, gate_document
from fcrm_caseflow.steps import AgentJobSpec, GateSpec, StepResult
from fcrm_caseflow.store import AgentResultStore, CaseRunStore
from fcrm_caseflow.triggers import Trigger
from fcrm_common.ids import new_id
from fcrm_common.mongo import mongo_client, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.enums import AgentName, GateStatus, GateType, Role, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep, JobPayload

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

CASE_ID = "FCRM-2026-0151"
STEP_MAP = {
    Trigger.CASE_DOCUMENT_STORED: StepKind.INTAKE_DOCUMENT_STORED,
    Trigger.AGENT_DONE_INTAKE: StepKind.INTAKE_PUBLISH,
    Trigger.GATE_DECIDED_PO_ACTION: StepKind.INTAKE_PO_ACTION,
}


@pytest.fixture
async def world():
    async with mongo_client(URI) as client:
        name = f"fcrm_caseflow_intake_it_{new_id('db')[3:].lower()}"
        database = client[name]
        runs = CaseRunStore(database["case_runs"], database["run_events"])
        gates = GateStore(database["gates"])
        results = AgentResultStore(database["agent_results"])
        await runs.ensure_indexes()
        await gates.ensure_indexes()
        engine = CaseflowEngine(
            runs,
            gates,
            Outbox(database["outbox"]),
            RecordingAudit(),
            step_map=STEP_MAP,
            transact=mongo_transact(client),
            results=results,
        )
        yield client, database, engine, runs, gates, results
        await client.drop_database(name)


def _env(event_type: str, payload: dict[str, Any], run_id: str | None) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id=CASE_ID,
        run_id=run_id,
        occurred_at=utcnow(),
        payload=payload,
    )


async def _outbox(database: Any, event_type: str) -> list[Envelope]:
    rows = [row async for row in database["outbox"].find({"type": event_type})]
    return [Envelope.model_validate_json(bytes(row["body"])) for row in rows]


async def test_the_join_the_results_and_the_step_gates_work_in_real_transactions(
    world,
) -> None:
    client, database, engine, runs, gates, results = world

    async def handle(envelope: Envelope) -> None:
        await with_transaction(client, lambda s: engine.handle(envelope, s))

    await handle(_env("case.created", {"data": {}}, None))
    run = await runs.get_by_case_id(CASE_ID)
    assert run is not None
    run_id = run.snapshot.run_id

    # A stored document: the ingest.run goes out in the same transaction.
    await handle(_env("case.document_stored", document_stored_payload("doc_1"), run_id))
    [ingest] = await _outbox(database, "ingest.run")
    assert ingest.payload["source"]["key"] == f"{CASE_ID}/doc_1/doc_1.pdf"
    [doc_step] = [
        HarnessStep.model_validate(e.payload)
        for e in await _outbox(database, "harness.step")
    ]

    # Its step dispatches two intake jobs and opens deterministic gates.
    jobs = tuple(
        AgentJobSpec(AgentDispatch(AgentName.INTAKE, scope="full"), {"doc_id": d})
        for d in ("doc_1", "doc_2")
    )
    gate = GateSpec(GateType.PO_CONFIRM_VALUE, "F03", {"kind": "field", "id": "F03"})
    result = StepResult(agent_jobs_to_dispatch=jobs, gates_to_open=(gate, gate))
    await engine.complete_step(run_id, doc_step.step_id, result)
    await engine.complete_step(run_id, doc_step.step_id, result)  # a no-op rerun

    gate_id = deterministic_gate_id(run_id, GateType.PO_CONFIRM_VALUE, "F03")
    stored_gate = await gates.get(gate_id)
    assert stored_gate is not None
    assert stored_gate["status"] == GateStatus.OPEN.value
    assert await database["gates"].count_documents({}) == 1
    job_ids = [
        JobPayload.model_validate(e.payload).job_id
        for e in await _outbox(database, "agent.run")
    ]
    assert len(job_ids) == 2
    pending = (await runs.get(run_id)).pending_agents  # type: ignore[union-attr]
    assert pending == tuple(f"intake:{j}" for j in job_ids)

    # The agents report: each result is stored, the join clears one key at a time.
    await handle(_env("agent.done", agent_done_body(job_ids[0], run_id), run_id))
    assert len(await _outbox(database, "harness.step")) == 1  # still waiting
    await handle(_env("agent.done", agent_done_body(job_ids[1], run_id), run_id))
    publish = [
        s
        for s in (
            HarnessStep.model_validate(e.payload)
            for e in await _outbox(database, "harness.step")
        )
        if s.step is StepKind.INTAKE_PUBLISH
    ]
    assert len(publish) == 1
    stored = await results.list_for(run_id, AgentName.INTAKE)
    assert sorted(r.job_id for r in stored) == sorted(job_ids)
    assert (await runs.get(run_id)).pending_agents == ()  # type: ignore[union-attr]

    # A redelivered done changes nothing: the result is upserted by job id.
    await handle(_env("agent.done", agent_done_body(job_ids[1], run_id), run_id))
    assert await database["agent_results"].count_documents({}) == 2

    # A PO decision that moves nothing starts the recording step.
    await gates.open(
        gate_document(
            gate_id="gate_gap_1",
            case_id=CASE_ID,
            run_id=run_id,
            gate_type=GateType.PO_ANSWER_GAPS,
            required_role=Role.PO,
        )
    )
    decided = {
        "gate_id": "gate_gap_1",
        "gate_type": GateType.PO_ANSWER_GAPS.value,
        "case_id": CASE_ID,
        "run_id": run_id,
        "decision": "answer",
        "decided_by": "u-pnair",
        "decided_at": utcnow().isoformat(),
        "subject": {"kind": "gap", "id": "q1"},
    }
    version = (await runs.get(run_id)).snapshot.version  # type: ignore[union-attr]
    await handle(_env("gate.decided", decided, run_id))
    steps = [
        HarnessStep.model_validate(e.payload)
        for e in await _outbox(database, "harness.step")
    ]
    assert [s.step for s in steps].count(StepKind.INTAKE_PO_ACTION) == 1
    after = await runs.get(run_id)
    assert after is not None
    assert after.snapshot.version == version  # no transition
    assert after.pending_steps

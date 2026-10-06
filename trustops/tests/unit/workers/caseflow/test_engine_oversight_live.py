"""The oversight path against a real MongoDB 8 replica set: the two events that
name no open run (``examiner.asked``, ``jurisdiction.list_published``) find or
open their run and enqueue their step **in one real transaction**; the harness
run of a list version is created once however often the event is redelivered, is
never mistaken for a case in flight, and owns the re-flag gates; and a step
replays to the same effect.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set, like
``test_engine_live.py``::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28717/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/unit/workers/caseflow/test_engine_oversight_live.py
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from conftest import (
    FakeExaminerService,
    FakeJurisdictionPolicyService,
    FakeReflagService,
    RecordingAudit,
    agent_done_body,
)
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.clients.oversight_client import (
    ExaminerClient,
    OversightClients,
    ReflagClient,
)
from fcrm_caseflow.clients.policy_client import PolicyClient
from fcrm_caseflow.cores import oversight
from fcrm_caseflow.engine import CaseflowEngine, mongo_transact
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.oversight_runs import OversightRuns
from fcrm_caseflow.steps import StepRegistry, StepRunner
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    AgentResultStore,
    CaseRunStore,
    new_case_run_document,
)
from fcrm_common.ids import deterministic_id, new_id
from fcrm_common.mongo import mongo_client, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.enums import CaseState, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep, JobPayload

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

CASE = "FCRM-2026-0151"
VERSION = "v2026-10-fixture"
HARNESS_RUN = deterministic_id("run", "watchlist", VERSION)
STEP_MAP = {t: k for t, k in STEP_FOR.items() if k in frozenset(oversight.HANDLERS)}


@pytest.fixture
async def world(
    closing_case_client: Any,
    examiner_service: FakeExaminerService,
    reflag_service: FakeReflagService,
    jurisdiction_policy_service: FakeJurisdictionPolicyService,
):
    async with mongo_client(URI) as client:
        name = f"fcrm_caseflow_oversight_it_{new_id('db')[3:].lower()}"
        database = client[name]
        runs = CaseRunStore(database["case_runs"], database["run_events"])
        await runs.ensure_indexes()
        audit = RecordingAudit()
        engine = CaseflowEngine(
            runs,
            GateStore(database["gates"]),
            Outbox(database["outbox"]),
            audit,
            step_map=STEP_MAP,
            transact=mongo_transact(client),
            results=AgentResultStore(database["agent_results"]),
            oversight=OversightRuns(runs),
        )
        runner = StepRunner(
            runs,
            StepRegistry(oversight.HANDLERS),
            engine,
            audit,
            case=closing_case_client,
            policy=PolicyClient(jurisdiction_policy_service.client("policy")),
            agent_results=AgentResultStore(database["agent_results"]),
            gates=GateStore(database["gates"]),
            oversight=OversightClients(
                examiner=ExaminerClient(examiner_service.client("audit")),
                reflags=ReflagClient(reflag_service.client("case")),
            ),
        )
        yield client, database, engine, runs, runner, audit
        await client.drop_database(name)


async def _steps(database: Any) -> list[HarnessStep]:
    rows = [row async for row in database["outbox"].find({"type": "harness.step"})]
    return [
        HarnessStep.model_validate(
            Envelope.model_validate_json(bytes(r["body"])).payload
        )
        for r in rows
    ]


async def _jobs(database: Any) -> list[JobPayload]:
    rows = [row async for row in database["outbox"].find({"type": "agent.run"})]
    return [
        JobPayload.model_validate(
            Envelope.model_validate_json(bytes(r["body"])).payload
        )
        for r in rows
    ]


def _event(event_type: str, payload: dict[str, Any], **ids: Any) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        occurred_at=utcnow(),
        payload=payload,
        **ids,
    )


async def test_a_question_about_a_closed_run_enqueues_its_step_and_its_job_once(
    world: Any,
) -> None:
    client, database, engine, runs, runner, audit = world
    document = new_case_run_document(run_id="run_c", case_id=CASE, pins=DEFAULT_PINS)
    document["state"] = CaseState.CLOSED.value
    document["closed_at"] = utcnow()
    document["dispatched_in_state"] = ["internal.closed_dispatch"]
    await database["case_runs"].insert_one(document)
    assert await runs.get_by_case_id(CASE) is None  # a closed run is no run in flight

    asked = _event(
        "examiner.asked",
        {"case_id": CASE, "question_id": "exq_1", "question": "Why?", "asked_by": "u"},
        case_id=CASE,
    )
    await with_transaction(client, lambda session: engine.handle(asked, session))
    [step] = await _steps(database)
    assert (step.step, step.run_id) == (StepKind.EXAMINER_ANSWER, "run_c")
    run = await runs.get("run_c")
    assert run is not None
    assert run.pending_steps == (step.step_id,)
    assert run.state is CaseState.CLOSED

    await runner.run(step)
    [job] = await _jobs(database)
    assert job.job_id == deterministic_id("job", "run_c", "examiner", "exq_1")
    assert (await runs.get("run_c")).pending_steps == ()  # type: ignore[union-attr]

    # a replay of the step dispatches nothing more
    await runner.run(step)
    assert len(await _jobs(database)) == 1
    assert (
        await database["run_events"].count_documents(
            {"run_id": "run_c", "kind": "step_done"}
        )
        == 1
    )

    # the agent's result is kept and starts the answering step on the same run
    done = agent_done_body(job.job_id, "run_c", agent="examiner")
    done["output"]["accepted"] = []
    await with_transaction(
        client,
        lambda session: engine.handle(
            _event("agent.done", done, case_id=CASE, run_id="run_c"), session
        ),
    )
    assert await database["agent_results"].count_documents({"run_id": "run_c"}) == 1
    steps = await _steps(database)
    assert [s.step for s in steps] == [StepKind.EXAMINER_ANSWER] * 2


async def test_a_list_gets_one_harness_run_however_often_its_event_is_redelivered(
    world: Any, reflag_service: FakeReflagService
) -> None:
    client, database, engine, runs, runner, audit = world
    reflag_service.candidates = [
        {
            "case_id": "FCRM-2026-0144",
            "title": "North Africa trade finance (Algeria)",
            "stage": "review",
            "jurisdiction": "Algeria",
        }
    ]
    payload = {"version": VERSION, "added": ["Algeria"], "removed": [], "fixture": True}
    for _ in range(2):
        await with_transaction(
            client,
            lambda session: engine.handle(
                _event("jurisdiction.list_published", payload), session
            ),
        )
    assert await database["case_runs"].count_documents({}) == 1
    run = await runs.get(HARNESS_RUN)
    assert run is not None
    assert run.snapshot.case_id == f"WATCHLIST-{VERSION}"
    assert run.state is CaseState.CLOSED
    assert await runs.get_by_case_id(f"WATCHLIST-{VERSION}") is None
    assert len(await _steps(database)) == 2  # one per delivered event, one run

    # the step runs outside any transaction and dispatches the watchlist job
    first = (await _steps(database))[0]
    await runner.run(first)
    [job] = await _jobs(database)
    assert job.run_id == HARNESS_RUN
    assert job.pins.jurisdictions_v == "v2026-06"


async def test_the_re_flag_gates_are_opened_on_the_harness_run_for_the_real_case(
    world: Any, reflag_service: FakeReflagService
) -> None:
    client, database, engine, runs, runner, audit = world
    reflag_service.candidates = [
        {
            "case_id": "FCRM-2026-0144",
            "title": "North Africa trade finance (Algeria)",
            "stage": "review",
            "jurisdiction": "Algeria",
        }
    ]
    payload = {"version": VERSION, "added": ["Algeria"], "removed": [], "fixture": True}
    await with_transaction(
        client,
        lambda session: engine.handle(
            _event("jurisdiction.list_published", payload), session
        ),
    )
    await runner.run((await _steps(database))[0])
    [job] = await _jobs(database)
    done = agent_done_body(job.job_id, HARNESS_RUN, agent="watchlist")
    done["case_id"] = job.case_id
    done["output"]["accepted"] = []  # the agent explained nothing: code writes it
    await with_transaction(
        client,
        lambda session: engine.handle(
            _event(
                "agent.done",
                done,
                case_id=job.case_id,
                run_id=HARNESS_RUN,
            ),
            session,
        ),
    )
    await runner.run((await _steps(database))[1])

    [gate] = [g async for g in database["gates"].find({})]
    assert gate["gate_type"] == "reflag_ack"
    assert gate["case_id"] == "FCRM-2026-0144"
    assert gate["run_id"] == HARNESS_RUN
    assert gate["status"] == "open"
    [row] = reflag_service.rows.values()
    assert row["gate_id"] == gate["_id"]

    # S1's gate.decided is accepted by Guard 7 and decides the gate for real
    decision = {
        "gate_id": gate["_id"],
        "gate_type": "reflag_ack",
        "case_id": "FCRM-2026-0144",
        "run_id": HARNESS_RUN,
        "decision": "acknowledge",
        "reason": "Checked the corridor; nothing clears through Algeria.",
        "decided_by": "u-riyer",
        "decided_at": utcnow().isoformat(),
        "role": "fcrm_analyst",
        "subject": {"kind": "reflag", "id": row["reflag_id"]},
        "value": {"rerun": True, "jurisdiction": "Algeria"},
    }
    await with_transaction(
        client,
        lambda session: engine.handle(
            _event(
                "gate.decided",
                decision,
                case_id="FCRM-2026-0144",
                run_id=HARNESS_RUN,
            ),
            session,
        ),
    )
    decided = await database["gates"].find_one({"_id": gate["_id"]})
    assert decided["status"] == "decided"
    assert decided["decided_by"] == "u-riyer"
    last = (await _steps(database))[-1]
    assert last.step is StepKind.REFLAG_ACK
    await runner.run(last)
    assert [r.type for r in audit.records][-1] == "reflag.acknowledged"
    assert audit.records[-1].case_id == "FCRM-2026-0144"


async def test_a_case_with_no_run_gets_an_oversight_run_that_is_never_a_case_in_flight(
    world: Any,
) -> None:
    client, database, engine, runs, runner, audit = world
    asked = _event(
        "examiner.asked",
        {"case_id": CASE, "question_id": "exq_9", "question": "Why?", "asked_by": "u"},
        case_id=CASE,
    )
    await with_transaction(client, lambda session: engine.handle(asked, session))
    run = await runs.get_any_by_case_id(CASE)
    assert run is not None
    assert run.refs["oversight"] == "examiner"
    assert run.snapshot.closed_at is not None
    assert await runs.get_by_case_id(CASE) is None

"""The decide path against a real MongoDB 8 replica set: ``vote.tallied`` moves the
run to CLOSED in one real transaction together with the ``decide.outcome`` step's
outbox row, the step runs outside any transaction, and its completion (the
``step_done`` row, the pending-step bookkeeping, the chained ``closed.finalize``
and the PO's ``outcome.delivered``) commits in one more, exactly once however
often it is replayed.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set, like
``test_engine_live.py``::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28317/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/unit/workers/caseflow/test_engine_decision_live.py
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from conftest import (
    FakeClosingCaseService,
    FakeDecisionVotesService,
    RecordingAudit,
)
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.cores import closing, decision
from fcrm_caseflow.engine import CaseflowEngine, mongo_transact
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.steps import StepRegistry, StepRunner
from fcrm_caseflow.store import DEFAULT_PINS, CaseRunStore, new_case_run_document
from fcrm_common.ids import new_id
from fcrm_common.mongo import mongo_client, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.enums import CaseState, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

CASE = "FCRM-2026-0151"
RUN = "run_decide"
OWNED = frozenset(decision.HANDLERS) | frozenset(closing.HANDLERS)
STEP_MAP = {t: k for t, k in STEP_FOR.items() if k in OWNED}

TALLIED = {
    "pack_id": "pack_1",
    "round": 1,
    "outcome": "approve_with_conditions",
    "vote_outcome": "approve_with_conditions",
    "counts": {"approve_with_conditions": 2, "defer": 1},
    "votes": [
        {
            "member_id": "u-jokafor",
            "vote": "approve_with_conditions",
            "rationale": "ok",
        },
        {
            "member_id": "u-amendes",
            "vote": "approve_with_conditions",
            "rationale": "ok",
        },
        {"member_id": "u-lchen", "vote": "defer", "rationale": "wait"},
    ],
}


@pytest.fixture
async def world(
    decision_votes_service: FakeDecisionVotesService,
    closing_case_service: FakeClosingCaseService,
    closing_case_client: Any,
    decision_votes_client: Any,
):
    async with mongo_client(URI) as client:
        name = f"fcrm_caseflow_decide_it_{new_id('db')[3:].lower()}"
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
        )
        runner = StepRunner(
            runs,
            StepRegistry({**decision.HANDLERS, **closing.HANDLERS}),
            engine,
            audit,
            case=closing_case_client,
            decision_votes=decision_votes_client,
        )
        document = new_case_run_document(run_id=RUN, case_id=CASE, pins=DEFAULT_PINS)
        document["state"] = CaseState.DECIDE.value
        document["band"] = 3
        document["flags"] = RunFlags(
            locked=True, pack_version=1, vote_round=1
        ).model_dump(mode="json")
        await database["case_runs"].insert_one(document)
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


async def _outbox(database: Any, event_type: str) -> list[Envelope]:
    rows = [row async for row in database["outbox"].find({"type": event_type})]
    return [Envelope.model_validate_json(bytes(r["body"])) for r in rows]


async def test_a_tally_closes_the_run_and_the_closing_step_follows_exactly_once(
    world: Any,
    decision_votes_service: FakeDecisionVotesService,
    closing_case_service: FakeClosingCaseService,
) -> None:
    client, database, engine, runs, runner, audit = world
    decision_votes_service.tallied = "approve_with_conditions"
    tallied = Envelope(
        event_id=new_id("evt"),
        type="vote.tallied",
        case_id=CASE,
        run_id=RUN,
        occurred_at=utcnow(),
        payload=TALLIED,
    )
    await with_transaction(client, lambda session: engine.handle(tallied, session))

    # The transition and its step's outbox row committed together.
    run = await runs.get(RUN)
    assert run is not None
    assert run.state is CaseState.CLOSED
    assert run.snapshot.closed_at is not None
    [outcome_step] = await _steps(database)
    assert outcome_step.step is StepKind.DECIDE_OUTCOME
    assert run.pending_steps == (outcome_step.step_id,)

    # The step runs outside any transaction; its completion chains the closing step.
    await runner.run(outcome_step)
    steps = await _steps(database)
    assert [s.step for s in steps] == [
        StepKind.DECIDE_OUTCOME,
        StepKind.CLOSED_FINALIZE,
    ]
    finalize = steps[1]
    assert (await runs.get(RUN)).pending_steps == (finalize.step_id,)  # type: ignore[union-attr]
    await runner.run(finalize)

    run = await runs.get(RUN)
    assert run is not None
    assert run.pending_steps == ()
    assert run.refs["chain_verified"] is True
    assert run.refs["conditions_activated"] == 3
    assert len(closing_case_service.outcome_puts) == 1
    assert len(decision_votes_service.outcomes) == 1
    [delivered] = await _outbox(database, "outcome.delivered")
    assert delivered.payload["outcome_label"] == "Approved with conditions"
    assert delivered.case_id == CASE
    done = await database["run_events"].count_documents(
        {"run_id": RUN, "kind": "step_done"}
    )
    assert done == 2

    # A redelivery of either step, or of its completion, changes nothing.
    calls = len(decision_votes_service.calls) + len(closing_case_service.calls)
    for step in steps:
        await runner.run(step)
    assert len(decision_votes_service.calls) + len(closing_case_service.calls) == calls
    assert len(await _steps(database)) == 2
    assert len(await _outbox(database, "outcome.delivered")) == 1
    assert (
        await database["run_events"].count_documents(
            {"run_id": RUN, "kind": "step_done"}
        )
        == 2
    )


async def test_a_failing_closing_step_is_not_marked_done_and_the_po_is_not_told(
    world: Any, decision_votes_service: FakeDecisionVotesService
) -> None:
    client, database, engine, runs, runner, audit = world
    decision_votes_service.tallied = "approve_with_conditions"
    audit.verify_ok = False
    tallied = Envelope(
        event_id=new_id("evt"),
        type="vote.tallied",
        case_id=CASE,
        run_id=RUN,
        occurred_at=utcnow(),
        payload=TALLIED,
    )
    await with_transaction(client, lambda session: engine.handle(tallied, session))
    [outcome_step] = await _steps(database)
    await runner.run(outcome_step)
    finalize = (await _steps(database))[1]
    with pytest.raises(Exception, match="does not verify"):  # noqa: B017,PT011
        await runner.run(finalize)
    assert (await runs.get(RUN)).pending_steps == (finalize.step_id,)  # type: ignore[union-attr]
    assert await _outbox(database, "outcome.delivered") == []
    assert (
        await database["run_events"].count_documents(
            {"run_id": RUN, "kind": "step_done"}
        )
        == 1
    )
    audit.verify_ok = True  # the chain was repaired; the replay finishes the step
    await runner.run(finalize)
    assert (await runs.get(RUN)).pending_steps == ()  # type: ignore[union-attr]
    assert len(await _outbox(database, "outcome.delivered")) == 1

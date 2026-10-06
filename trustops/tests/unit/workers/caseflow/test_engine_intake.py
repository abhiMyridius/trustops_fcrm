"""The engine's side of the intake core (ADR-28): the ``ingest.run`` it
publishes with a stored document, the join over one intake job per document,
the agent results it keeps for the step that merges them, the PO decisions
that start a step without moving the run, and the generic audit record it
skips when a step writes that very type.

Every collection is a ``conftest.FakeCollection``; "the same transaction" is
proved the way ``test_engine_steps.py`` proves it, by the session each write
carries.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pytest
from conftest import (
    FakeCollection,
    FakeSession,
    RecordingAudit,
    document_stored_payload,
)
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.dispatch import REQUIRED_ROLE, AgentDispatch
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore, gate_document
from fcrm_caseflow.steps import AgentJobSpec, StepResult
from fcrm_caseflow.store import AgentResultStore, CaseRun, CaseRunStore
from fcrm_caseflow.triggers import Trigger
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import AgentName, CaseState, GateType, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    CaseDocumentStored,
    GateDecided,
    HarnessStep,
    JobPayload,
)

CASE_ID = "FCRM-2026-0151"
EngineFactory = Callable[..., CaseflowEngine]


def _env(event_type: str, payload: Mapping[str, Any], run_id: str) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id=CASE_ID,
        run_id=run_id,
        occurred_at=utcnow(),
        payload=dict(payload),
    )


def _outbox(outbox_collection: FakeCollection, event_type: str) -> list[Envelope]:
    envelopes = [
        Envelope.model_validate_json(bytes(doc["body"]))
        for doc in outbox_collection.docs
    ]
    return [e for e in envelopes if e.type == event_type]


def _steps(outbox_collection: FakeCollection) -> list[HarnessStep]:
    return [
        HarnessStep.model_validate(e.payload)
        for e in _outbox(outbox_collection, "harness.step")
    ]


async def _start(
    engine: CaseflowEngine, run_store: CaseRunStore, session: FakeSession
) -> str:
    created = Envelope(
        event_id=new_id("evt"),
        type="case.created",
        case_id=CASE_ID,
        occurred_at=utcnow(),
        payload={"actor": "u-pnair", "data": {}},
    )
    await engine.handle(created, session)
    run = await run_store.get_by_case_id(CASE_ID)
    assert run is not None
    return run.snapshot.run_id


async def _load(run_store: CaseRunStore, run_id: str) -> CaseRun:
    run = await run_store.get(run_id)
    assert run is not None
    return run


def _done(job_id: str, run_id: str, agent: str = "intake") -> dict[str, Any]:
    return {
        "job_id": job_id,
        "case_id": CASE_ID,
        "run_id": run_id,
        "agent": agent,
        "output": {"accepted": [], "dropped": [], "notes_for_analyst": []},
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "model": "anthropic:claude-haiku",
        "attempt": 1,
    }


# --- ingest.run: published with the stored document -----------------------------


class TestIngestDispatch:
    async def test_a_stored_document_publishes_ingest_run_in_the_same_transaction(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        run_id = await _start(engine, run_store, session)
        stored = _env("case.document_stored", document_stored_payload("doc_1"), run_id)

        await engine.handle(stored, session)

        [ingest] = _outbox(outbox_collection, "ingest.run")
        assert (ingest.case_id, ingest.run_id) == (CASE_ID, run_id)
        assert ingest.payload["source"] == {
            "bucket": "uploads",
            "key": f"{CASE_ID}/doc_1/doc_1.pdf",
        }
        assert ingest.payload["doc_id"] == "doc_1"
        assert ingest.payload["purpose"] == "case_document"
        row = next(d for d in outbox_collection.docs if d["type"] == "ingest.run")
        assert (row["exchange"], row["routing_key"]) == (
            "ingestion.jobs",
            "ingest.run",
        )
        assert row["published_at"] is None
        assert all(s is session for s in outbox_collection.sessions)

    async def test_it_writes_into_the_canonical_bucket_the_engine_was_given(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(ingest_bucket="canonical-eu")
        run_id = await _start(engine, run_store, session)

        await engine.handle(
            _env("case.document_stored", document_stored_payload(), run_id), session
        )

        [ingest] = _outbox(outbox_collection, "ingest.run")
        assert ingest.payload["target"] == {
            "bucket": "canonical-eu",
            "key_prefix": f"canonical/{CASE_ID}/",
        }

    async def test_a_redelivered_document_gets_the_same_job_id(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        run_id = await _start(engine, run_store, session)
        for _ in range(2):
            await engine.handle(
                _env("case.document_stored", document_stored_payload(), run_id),
                session,
            )
        first, second = _outbox(outbox_collection, "ingest.run")
        assert first.payload["job_id"] == second.payload["job_id"]

    async def test_a_document_event_without_its_location_fails_loudly(
        self, engine: CaseflowEngine, run_store: CaseRunStore, session: FakeSession
    ) -> None:
        run_id = await _start(engine, run_store, session)
        with pytest.raises(ValueError, match="bucket"):
            await engine.handle(
                _env("case.document_stored", {"doc_id": "doc_1"}, run_id), session
            )

    async def test_no_other_event_publishes_an_ingest_run(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        run_id = await _start(engine, run_store, session)
        await engine.handle(_env("case.submitted", {}, run_id), session)
        assert _outbox(outbox_collection, "ingest.run") == []


# --- one intake job per document: the join, and the results the step merges ----

INTAKE_MAP = {
    Trigger.CASE_DOCUMENT_STORED: StepKind.INTAKE_DOCUMENT_STORED,
    Trigger.AGENT_DONE_INTAKE: StepKind.INTAKE_PUBLISH,
}


async def _dispatch_intake_jobs(
    engine: CaseflowEngine,
    run_store: CaseRunStore,
    session: FakeSession,
    outbox_collection: FakeCollection,
    docs: tuple[str, ...],
) -> tuple[str, list[str]]:
    """Opens a run, leaves a step pending, and completes it by dispatching one
    intake job per document. Returns the run id and the job ids.
    """
    run_id = await _start(engine, run_store, session)
    await engine.handle(
        _env("case.document_stored", document_stored_payload("doc_0"), run_id),
        session,
    )
    [step] = _steps(outbox_collection)
    jobs = tuple(
        AgentJobSpec(
            AgentDispatch(AgentName.INTAKE, scope="full", step_key=f"intake:{doc}"),
            {"doc_id": doc},
        )
        for doc in docs
    )
    await engine.complete_step(
        run_id, step.step_id, StepResult(agent_jobs_to_dispatch=jobs)
    )
    job_ids = [
        JobPayload.model_validate(e.payload).job_id
        for e in _outbox(outbox_collection, "agent.run")
    ]
    return run_id, job_ids


class TestFanOutJoin:
    async def test_two_jobs_of_one_agent_are_tracked_by_job(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(INTAKE_MAP)

        run_id, jobs = await _dispatch_intake_jobs(
            engine, run_store, session, outbox_collection, ("doc_1", "doc_2")
        )

        assert len(jobs) == 2
        pending = (await _load(run_store, run_id)).pending_agents
        assert pending == tuple(f"intake:{job}" for job in jobs)

    async def test_the_first_done_waits_for_the_other_and_the_last_fires_the_trigger(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(INTAKE_MAP)
        run_id, (job_a, job_b) = await _dispatch_intake_jobs(
            engine, run_store, session, outbox_collection, ("doc_1", "doc_2")
        )
        steps_before = len(_steps(outbox_collection))
        assert steps_before == 1  # the document step

        await engine.handle(_env("agent.done", _done(job_a, run_id), run_id), session)

        assert len(_steps(outbox_collection)) == steps_before
        assert (await _load(run_store, run_id)).pending_agents == (f"intake:{job_b}",)

        await engine.handle(_env("agent.done", _done(job_b, run_id), run_id), session)

        publish = [
            s for s in _steps(outbox_collection) if s.step is StepKind.INTAKE_PUBLISH
        ]
        assert len(publish) == 1
        assert publish[0].args["trigger"] == "agent.done.intake"
        assert (await _load(run_store, run_id)).pending_agents == ()

    async def test_the_order_the_jobs_finish_in_does_not_matter(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(INTAKE_MAP)
        run_id, (job_a, job_b) = await _dispatch_intake_jobs(
            engine, run_store, session, outbox_collection, ("doc_1", "doc_2")
        )
        await engine.handle(_env("agent.done", _done(job_b, run_id), run_id), session)
        await engine.handle(_env("agent.done", _done(job_a, run_id), run_id), session)
        publish = [
            s for s in _steps(outbox_collection) if s.step is StepKind.INTAKE_PUBLISH
        ]
        assert len(publish) == 1

    async def test_a_single_job_fires_the_trigger_at_once(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(INTAKE_MAP)
        run_id, [job] = await _dispatch_intake_jobs(
            engine, run_store, session, outbox_collection, ("doc_1",)
        )
        assert (await _load(run_store, run_id)).pending_agents == ()

        await engine.handle(_env("agent.done", _done(job, run_id), run_id), session)

        publish = [
            s for s in _steps(outbox_collection) if s.step is StepKind.INTAKE_PUBLISH
        ]
        assert len(publish) == 1

    async def test_a_done_nobody_is_waiting_for_still_fires_the_trigger(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        """The ``questions_only`` job a transition dispatches is tracked by no
        join: its done is a single-agent done like before.
        """
        engine = engine_factory(INTAKE_MAP)
        run_id = await _start(engine, run_store, session)
        await engine.handle(_env("agent.done", _done("job_x", run_id), run_id), session)
        assert [s.step for s in _steps(outbox_collection)] == [StepKind.INTAKE_PUBLISH]


class TestAgentResultsAreKept:
    async def test_every_done_is_stored_with_the_transaction_that_applied_it(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
        agent_results: AgentResultStore,
        agent_results_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(INTAKE_MAP, results=agent_results)
        run_id, (job_a, job_b) = await _dispatch_intake_jobs(
            engine, run_store, session, outbox_collection, ("doc_1", "doc_2")
        )

        await engine.handle(_env("agent.done", _done(job_a, run_id), run_id), session)
        await engine.handle(_env("agent.done", _done(job_b, run_id), run_id), session)

        stored = await agent_results.list_for(run_id, AgentName.INTAKE)
        assert sorted(r.job_id for r in stored) == sorted([job_a, job_b])
        assert all(s is session for s in agent_results_collection.sessions)

    async def test_a_redelivered_done_is_stored_once(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        agent_results: AgentResultStore,
        agent_results_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(INTAKE_MAP, results=agent_results)
        run_id = await _start(engine, run_store, session)
        for _ in range(2):
            await engine.handle(
                _env("agent.done", _done("job_x", run_id), run_id), session
            )
        assert len(agent_results_collection.docs) == 1

    async def test_a_second_done_for_a_finished_job_is_applied_no_second_time(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
        agent_results: AgentResultStore,
    ) -> None:
        """H2 ran a redelivered job again and published a second ``agent.done``
        under a fresh event id: the consumer's message-id dedupe cannot see it,
        so the engine must, or the run enqueues a second ``intake.publish``
        and every ``proposal.*`` record is written twice (found by the
        resilience drill D1c).
        """
        engine = engine_factory(INTAKE_MAP, results=agent_results)
        run_id, [job] = await _dispatch_intake_jobs(
            engine, run_store, session, outbox_collection, ("doc_1",)
        )

        await engine.handle(_env("agent.done", _done(job, run_id), run_id), session)
        await engine.handle(_env("agent.done", _done(job, run_id), run_id), session)

        publish = [
            s for s in _steps(outbox_collection) if s.step is StepKind.INTAKE_PUBLISH
        ]
        assert len(publish) == 1

    async def test_a_second_done_does_not_replace_the_result_a_step_merged(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
        agent_results: AgentResultStore,
    ) -> None:
        engine = engine_factory(INTAKE_MAP, results=agent_results)
        run_id, [job] = await _dispatch_intake_jobs(
            engine, run_store, session, outbox_collection, ("doc_1",)
        )
        first = _done(job, run_id)
        first["output"] = {"accepted": [{"field_id": "from-the-first-run"}]}
        second = _done(job, run_id)
        second["output"] = {"accepted": [{"field_id": "from-the-second-run"}]}

        await engine.handle(_env("agent.done", first, run_id), session)
        await engine.handle(_env("agent.done", second, run_id), session)

        [kept] = await agent_results.list_for(run_id, AgentName.INTAKE)
        assert kept.accepted() == [{"field_id": "from-the-first-run"}]

    async def test_an_engine_without_a_store_keeps_nothing(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        agent_results_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(INTAKE_MAP)
        run_id = await _start(engine, run_store, session)
        await engine.handle(_env("agent.done", _done("job_x", run_id), run_id), session)
        assert agent_results_collection.docs == []


# --- the PO's decisions ----------------------------------------------------------


def _decision(
    gate_id: str,
    gate_type: GateType,
    run_id: str,
    decision: str = "confirm",
    **extra: Any,
) -> dict[str, Any]:
    return GateDecided(
        gate_id=gate_id,
        gate_type=gate_type,
        case_id=CASE_ID,
        run_id=run_id,
        decision=decision,
        decided_by="u-pnair",
        decided_at=utcnow(),
        subject=extra.pop("subject", {}),
        **extra,
    ).model_dump(mode="json")


async def _open(
    gate_store: GateStore,
    session: FakeSession,
    run_id: str,
    gate_id: str,
    gate_type: GateType,
) -> None:
    await gate_store.open(
        gate_document(
            gate_id=gate_id,
            case_id=CASE_ID,
            run_id=run_id,
            gate_type=gate_type,
            required_role=REQUIRED_ROLE[gate_type],
        ),
        session=session,  # type: ignore[arg-type]
    )


PO_MAP = {
    Trigger.GATE_DECIDED_PO_ACTION: StepKind.INTAKE_PO_ACTION,
    Trigger.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL: StepKind.INTAKE_FIELDSET_RESOLVED,
}


class TestPoDecisions:
    async def test_a_change_type_confirmed_while_others_are_open_starts_the_step_only(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(PO_MAP)
        run_id = await _start(engine, run_store, session)
        for gate in ("gate_1", "gate_2"):
            await _open(gate_store, session, run_id, gate, GateType.PO_CONFIRM_CATEGORY)
        version = (await _load(run_store, run_id)).snapshot.version

        await engine.handle(
            _env(
                "gate.decided",
                _decision("gate_1", GateType.PO_CONFIRM_CATEGORY, run_id),
                run_id,
            ),
            session,
        )

        run = await _load(run_store, run_id)
        assert run.snapshot.version == version  # no transition
        assert run.state is CaseState.INTAKE
        [step] = _steps(outbox_collection)
        assert step.step is StepKind.INTAKE_PO_ACTION
        assert step.args["payload"]["gate_id"] == "gate_1"
        assert run.pending_steps == (step.step_id,)

    async def test_the_last_change_type_resolves_the_fieldset_and_asks_what_is_open(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(PO_MAP)
        run_id = await _start(engine, run_store, session)
        await _open(gate_store, session, run_id, "gate_1", GateType.PO_CONFIRM_CATEGORY)
        await _open(gate_store, session, run_id, "gate_gap", GateType.PO_ANSWER_GAPS)
        # What the first round's ingest step recorded.
        await run_store.merge_refs(
            run_id,
            {"intake_docs": ["doc_1", "doc_2"]},
            session=session,  # type: ignore[arg-type]
        )

        await engine.handle(
            _env(
                "gate.decided",
                _decision("gate_1", GateType.PO_CONFIRM_CATEGORY, run_id),
                run_id,
            ),
            session,
        )

        [step] = _steps(outbox_collection)
        assert step.step is StepKind.INTAKE_FIELDSET_RESOLVED
        [job] = _outbox(outbox_collection, "agent.run")
        payload = JobPayload.model_validate(job.payload)
        assert (payload.agent, payload.scope) == (AgentName.INTAKE, "questions_only")
        # H2's intake builder refuses a job without a document to read.
        assert payload.refs == {"doc_id": "doc_1"}

    @pytest.mark.parametrize(
        "gate_type",
        [GateType.PO_CONFIRM_VALUE, GateType.PO_REUSE, GateType.PO_ANSWER_GAPS],
    )
    async def test_a_value_a_reuse_and_an_answer_start_the_step_without_moving_the_run(
        self,
        gate_type: GateType,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(PO_MAP)
        run_id = await _start(engine, run_store, session)
        await _open(gate_store, session, run_id, "gate_x", gate_type)
        version = (await _load(run_store, run_id)).snapshot.version

        await engine.handle(
            _env("gate.decided", _decision("gate_x", gate_type, run_id), run_id),
            session,
        )

        assert (await _load(run_store, run_id)).snapshot.version == version
        [step] = _steps(outbox_collection)
        assert step.step is StepKind.INTAKE_PO_ACTION

    async def test_a_decision_after_intake_starts_no_step(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(PO_MAP)
        run_id = await _start(engine, run_store, session)
        await _open(gate_store, session, run_id, "gate_x", GateType.PO_ANSWER_GAPS)
        await engine.handle(_env("case.submitted", {}, run_id), session)
        assert (await _load(run_store, run_id)).state is not CaseState.INTAKE

        await engine.handle(
            _env(
                "gate.decided",
                _decision("gate_x", GateType.PO_ANSWER_GAPS, run_id),
                run_id,
            ),
            session,
        )

        assert _steps(outbox_collection) == []

    async def test_a_gate_the_analyst_decides_starts_no_intake_step(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(PO_MAP)
        run_id = await _start(engine, run_store, session)
        await _open(gate_store, session, run_id, "gate_ack", GateType.ACK)

        payload = GateDecision(
            gate_id="gate_ack",
            gate_type=GateType.ACK,
            case_id=CASE_ID,
            run_id=run_id,
            decision="acknowledge",
            decided_by="u-riyer",
            decided_at=utcnow(),
        ).model_dump(mode="json")
        # A real ack arrives from an analyst; the gate's role is the engine's
        # own record, so the decision itself is valid.
        await engine.handle(_env("gate.decided", payload, run_id), session)

        assert _steps(outbox_collection) == []

    async def test_the_internal_chain_waits_for_the_recording_step(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
    ) -> None:
        engine = engine_factory(PO_MAP)
        run_id = await _start(engine, run_store, session)
        await _open(gate_store, session, run_id, "gate_x", GateType.PO_REUSE)

        await engine.handle(
            _env(
                "gate.decided",
                _decision("gate_x", GateType.PO_REUSE, run_id),
                run_id,
            ),
            session,
        )

        assert len((await _load(run_store, run_id)).pending_steps) == 1


# --- no duplicate record of one type -----------------------------------------------


class TestGenericAuditRecordSkipped:
    async def test_the_engine_skips_case_created_when_the_open_step_writes_it(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        engine = engine_factory(dict(STEP_FOR))
        await _start(engine, run_store, session)
        assert [r.type for r in recording_audit.records] == []

    async def test_it_writes_case_created_itself_when_no_step_does(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        await _start(engine, run_store, session)
        assert [r.type for r in recording_audit.records] == ["case.created"]

    async def test_it_skips_case_submitted_when_the_submit_step_writes_it(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        engine = engine_factory(dict(STEP_FOR))
        run_id = await _start(engine, run_store, session)
        await engine.handle(
            _env("case.submitted", {"four_submit_checks_passed": True}, run_id),
            session,
        )
        assert "case.submitted" not in {r.type for r in recording_audit.records}

    async def test_it_keeps_a_generic_record_the_step_does_not_duplicate(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        engine = engine_factory(dict(STEP_FOR))
        run_id = await _start(engine, run_store, session)
        await engine.handle(
            _env("case.document_stored", document_stored_payload(), run_id), session
        )
        # The step writes document.stored; the transition's own record is a
        # different type and stays.
        assert [r.type for r in recording_audit.records] == ["case.document_stored"]

    async def test_a_custom_records_table_decides(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        engine = engine_factory(
            {Trigger.CASE_CREATED: StepKind.INTAKE_OPEN},
            records_for={StepKind.INTAKE_OPEN: ()},
        )
        await _start(engine, run_store, session)
        assert [r.type for r in recording_audit.records] == ["case.created"]


def test_the_helper_payload_is_a_valid_document_stored_event() -> None:
    """The fixtures above stay valid if S1's contract gains a required field:
    this fails first, naming it.
    """
    CaseDocumentStored.model_validate(document_stored_payload())

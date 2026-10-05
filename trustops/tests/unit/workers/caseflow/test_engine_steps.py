"""The engine half of the step mechanism (ADR-28): a mapped trigger enqueues a
``harness.step`` in the transaction that applied it, the internal chain waits
while a step is pending, and ``complete_step`` closes it in one short
transaction, exactly once.

Every collection is :class:`~conftest.FakeCollection`; each write records the
session it was issued with, which is how these tests prove "the same
transaction" at the API level (real commit/abort is ``test_engine_live.py``'s
job against a replica set).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Optional

import pytest
from conftest import (
    FakeCollection,
    FakeSession,
    FakeTransact,
    RecordingAudit,
    document_stored_payload,
)
from fcrm_caseflow.dispatch import REQUIRED_ROLE, AgentDispatch
from fcrm_caseflow.engine import CaseflowEngine, OrphanEventError
from fcrm_caseflow.eventstore import rebuild
from fcrm_caseflow.gates import GateStore, gate_document
from fcrm_caseflow.steps import (
    AgentJobSpec,
    FollowUpStep,
    GateSpec,
    StepResult,
    UiEvent,
)
from fcrm_caseflow.store import CaseRun, CaseRunStore
from fcrm_caseflow.transitions import open_run
from fcrm_caseflow.triggers import Trigger
from fcrm_common.events import UnknownEventTypeError
from fcrm_common.ids import deterministic_id, new_id
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.enums import AgentName, CaseState, GateStatus, GateType, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import GateOpened, HarnessStep, JobPayload

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


async def _start(
    engine: CaseflowEngine, run_store: CaseRunStore, session: FakeSession
) -> str:
    """Opens the case's run and returns its id."""
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


def _outbox_envelopes(
    outbox_collection: FakeCollection, event_type: Optional[str] = None
) -> list[Envelope]:
    envelopes = [
        Envelope.model_validate_json(bytes(doc["body"]))
        for doc in outbox_collection.docs
    ]
    return [e for e in envelopes if event_type is None or e.type == event_type]


def _steps(outbox_collection: FakeCollection) -> list[HarnessStep]:
    return [
        HarnessStep.model_validate(e.payload)
        for e in _outbox_envelopes(outbox_collection, "harness.step")
    ]


async def _load(run_store: CaseRunStore, run_id: str) -> CaseRun:
    run = await run_store.get(run_id)
    assert run is not None
    return run


# --- a mapped trigger enqueues a step in the transition's transaction -----


class TestStepEnqueuedWithTheTransition:
    async def test_a_mapped_trigger_enqueues_a_harness_step_in_the_same_transaction(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
        case_runs_collection: FakeCollection,
        run_events_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(
            {Trigger.CASE_DOCUMENT_STORED: StepKind.INTAKE_DOCUMENT_STORED}
        )
        run_id = await _start(engine, run_store, session)
        stored = _env("case.document_stored", document_stored_payload("doc_1"), run_id)

        await engine.handle(stored, session)

        [step] = _steps(outbox_collection)
        expected_id = deterministic_id(
            "step", run_id, "intake.document_stored", stored.event_id
        )
        assert step.step_id == expected_id
        assert step.step is StepKind.INTAKE_DOCUMENT_STORED
        assert (step.case_id, step.run_id) == (CASE_ID, run_id)
        assert step.args == {
            "trigger": "case.document_stored",
            "message_id": stored.event_id,
            "payload": document_stored_payload("doc_1"),
        }
        run = await _load(run_store, run_id)
        assert run.pending_steps == (expected_id,)
        # Routed on case.events with the type as key, like every outbox row.
        row = next(d for d in outbox_collection.docs if d["type"] == "harness.step")
        assert (row["exchange"], row["routing_key"]) == ("case.events", "harness.step")
        assert row["published_at"] is None
        # One transaction: the transition, its run event, the pending marker
        # and the outbox row were all written through the caller's session.
        for collection in (outbox_collection, run_events_collection):
            assert collection.sessions
            assert all(s is session for s in collection.sessions)
        assert all(s is session for s in case_runs_collection.sessions)

    async def test_opening_a_run_can_start_a_step_too(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        """``case.created`` opens the run without a transition, but
        ``Trigger.CASE_CREATED`` in the step map still enqueues ``intake.open``
        (field set, ``workflow.started``) in the same transaction.
        """
        engine = engine_factory({Trigger.CASE_CREATED: StepKind.INTAKE_OPEN})
        created = Envelope(
            event_id=new_id("evt"),
            type="case.created",
            case_id=CASE_ID,
            occurred_at=utcnow(),
            payload={"actor": "u-pnair", "data": {"title": "Remittances"}},
        )

        await engine.handle(created, session)

        run = await run_store.get_by_case_id(CASE_ID)
        assert run is not None
        [step] = _steps(outbox_collection)
        assert step.step is StepKind.INTAKE_OPEN
        assert step.step_id == deterministic_id(
            "step", run.snapshot.run_id, "intake.open", created.event_id
        )
        assert step.args["payload"]["data"] == {"title": "Remittances"}
        assert run.pending_steps == (step.step_id,)
        assert run.state is CaseState.INTAKE

    async def test_an_unmapped_trigger_enqueues_nothing(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        run_id = await _start(engine, run_store, session)
        await engine.handle(
            _env("case.document_stored", document_stored_payload("doc_1"), run_id),
            session,
        )
        assert _steps(outbox_collection) == []
        assert (await _load(run_store, run_id)).pending_steps == ()

    async def test_each_message_gets_its_own_step_id(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(
            {Trigger.CASE_DOCUMENT_STORED: StepKind.INTAKE_DOCUMENT_STORED}
        )
        run_id = await _start(engine, run_store, session)
        first = _env("case.document_stored", document_stored_payload("doc_1"), run_id)
        second = _env("case.document_stored", document_stored_payload("doc_2"), run_id)
        await engine.handle(first, session)
        await engine.handle(second, session)
        ids = [s.step_id for s in _steps(outbox_collection)]
        assert ids == [
            deterministic_id("step", run_id, "intake.document_stored", first.event_id),
            deterministic_id("step", run_id, "intake.document_stored", second.event_id),
        ]
        assert len(set(ids)) == 2

    async def test_a_chained_trigger_derives_its_step_id_from_the_originating_message(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        """TRIAGE_DISPATCH has no message of its own: it fires in the chain
        that ``case.submitted`` started, so its step id comes from that
        message plus the trigger, stable if the message is redelivered.
        """
        engine = engine_factory({Trigger.TRIAGE_DISPATCH: StepKind.TRIAGE_ASSIGN})
        run_id = await _start(engine, run_store, session)
        submitted = _env("case.submitted", {}, run_id)

        await engine.handle(submitted, session)

        [step] = _steps(outbox_collection)
        assert step.step_id == deterministic_id(
            "step",
            run_id,
            "triage.assign",
            f"{submitted.event_id}/internal.triage_dispatch",
        )
        assert step.args["message_id"] is None
        assert step.args["trigger"] == "internal.triage_dispatch"

    async def test_the_step_is_announced_after_the_transition_is_recorded(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        engine = engine_factory(
            {Trigger.CASE_DOCUMENT_STORED: StepKind.INTAKE_DOCUMENT_STORED}
        )
        run_id = await _start(engine, run_store, session)
        await engine.handle(
            _env("case.document_stored", document_stored_payload("doc_1"), run_id),
            session,
        )
        assert recording_audit.records[-1].type == "case.document_stored"


# --- the internal chain waits for a pending step ---------------------------


class TestChainBlockedWhileAStepIsPending:
    async def test_the_chain_stops_at_the_step_and_complete_step_resumes_it(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory({Trigger.CASE_SUBMITTED: StepKind.SUBMIT_RECORD})
        run_id = await _start(engine, run_store, session)

        await engine.handle(_env("case.submitted", {}, run_id), session)

        run = await _load(run_store, run_id)
        # Without the step this would have chained TRIAGE -> SCREENING and
        # dispatched the screening agent in the same call.
        assert run.state is CaseState.TRIAGE
        assert len(run.pending_steps) == 1
        assert not [
            e
            for e in _outbox_envelopes(outbox_collection, "agent.run")
            if e.payload["agent"] == "screening"
        ]

        await engine.complete_step(
            run_id, run.pending_steps[0], StepResult(), step=StepKind.SUBMIT_RECORD
        )

        run = await _load(run_store, run_id)
        assert run.state is CaseState.SCREENING
        assert run.pending_steps == ()
        screening = [
            e
            for e in _outbox_envelopes(outbox_collection, "agent.run")
            if e.payload["agent"] == "screening"
        ]
        assert len(screening) == 1

    async def test_the_chain_waits_until_every_pending_step_is_done(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory({Trigger.CASE_SUBMITTED: StepKind.SUBMIT_RECORD})
        run_id = await _start(engine, run_store, session)
        await engine.handle(_env("case.submitted", {}, run_id), session)
        first = (await _load(run_store, run_id)).pending_steps[0]

        # The first step asks for a second one; the chain must keep waiting.
        await engine.complete_step(
            run_id,
            first,
            StepResult(follow_up_steps=(FollowUpStep(StepKind.TRIAGE_ASSIGN),)),
            step=StepKind.SUBMIT_RECORD,
        )
        run = await _load(run_store, run_id)
        [follow_up] = run.pending_steps
        assert follow_up != first
        assert run.state is CaseState.TRIAGE
        assert [s.step for s in _steps(outbox_collection)] == [
            StepKind.SUBMIT_RECORD,
            StepKind.TRIAGE_ASSIGN,
        ]

        await engine.complete_step(
            run_id, follow_up, StepResult(), step=StepKind.TRIAGE_ASSIGN
        )
        run = await _load(run_store, run_id)
        assert run.state is CaseState.SCREENING
        assert run.pending_steps == ()

    async def test_an_unrelated_event_does_not_chain_past_a_pending_step(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
    ) -> None:
        """Even an event with no trigger of its own (the fallback chain check)
        must not run the chain while the step is outstanding.
        """
        engine = engine_factory({Trigger.CASE_SUBMITTED: StepKind.SUBMIT_RECORD})
        run_id = await _start(engine, run_store, session)
        await engine.handle(_env("case.submitted", {}, run_id), session)

        await engine.handle(_env("index.built", {"index": "controls"}, run_id), session)

        assert (await _load(run_store, run_id)).state is CaseState.TRIAGE


# --- complete_step ----------------------------------------------------------


async def _pending_step_run(
    engine: CaseflowEngine, run_store: CaseRunStore, session: FakeSession
) -> tuple[str, str]:
    """A run in INTAKE with one pending step; returns ``(run_id, step_id)``."""
    run_id = await _start(engine, run_store, session)
    await engine.handle(
        _env("case.document_stored", document_stored_payload("doc_1"), run_id), session
    )
    run = await _load(run_store, run_id)
    return run_id, run.pending_steps[0]


STORED_MAP = {Trigger.CASE_DOCUMENT_STORED: StepKind.INTAKE_DOCUMENT_STORED}


class TestCompleteStep:
    async def test_it_opens_every_gate_with_a_deterministic_id_and_announces_each(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        gates_collection: FakeCollection,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        result = StepResult(
            gates_to_open=(
                GateSpec(
                    GateType.REVIEW_ITEM,
                    "CTL-T01",
                    {"kind": "control", "id": "CTL-T01"},
                ),
                GateSpec(
                    GateType.REVIEW_ITEM,
                    "CTL-T02",
                    {"kind": "control", "id": "CTL-T02"},
                ),
                GateSpec(
                    GateType.PO_CONFIRM_CATEGORY,
                    "vendor_onboarding",
                    {"kind": "change_type", "id": "vendor_onboarding"},
                ),
            )
        )

        await engine.complete_step(
            run_id, step_id, result, step=StepKind.INTAKE_DOCUMENT_STORED
        )

        expected = {
            deterministic_id("gate", run_id, "review_item", "CTL-T01"),
            deterministic_id("gate", run_id, "review_item", "CTL-T02"),
            deterministic_id(
                "gate", run_id, "po_confirm_category", "vendor_onboarding"
            ),
        }
        assert {d["_id"] for d in gates_collection.docs} == expected
        by_id = {d["_id"]: d for d in gates_collection.docs}
        control = by_id[deterministic_id("gate", run_id, "review_item", "CTL-T01")]
        assert control["status"] == GateStatus.OPEN.value
        assert control["required_role"] == REQUIRED_ROLE[GateType.REVIEW_ITEM].value
        assert control["subject"] == {"kind": "control", "id": "CTL-T01"}
        assert (control["case_id"], control["run_id"]) == (CASE_ID, run_id)
        opened = [
            GateOpened.model_validate(e.payload)
            for e in _outbox_envelopes(outbox_collection, "gate.opened")
        ]
        assert {g.gate_id for g in opened} == expected
        assert {g.gate_type for g in opened} == {
            GateType.REVIEW_ITEM,
            GateType.PO_CONFIRM_CATEGORY,
        }

    async def test_running_it_twice_changes_nothing_the_second_time(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        gates_collection: FakeCollection,
        outbox_collection: FakeCollection,
        run_events_collection: FakeCollection,
        case_runs_collection: FakeCollection,
        transact: FakeTransact,
    ) -> None:
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        result = StepResult(
            gates_to_open=(GateSpec(GateType.REVIEW_ITEM, "CTL-T01"),),
            agent_jobs_to_dispatch=(
                AgentJobSpec(AgentDispatch(AgentName.NARRATIVE, scope="completeness")),
            ),
            follow_up_steps=(FollowUpStep(StepKind.STATE_PUSH),),
            counter_deltas={"agents_run": 1},
        )
        await engine.complete_step(run_id, step_id, result)
        snapshot = (
            list(gates_collection.docs),
            list(outbox_collection.docs),
            list(run_events_collection.docs),
            list(case_runs_collection.docs),
        )

        await engine.complete_step(run_id, step_id, result)

        assert (
            list(gates_collection.docs),
            list(outbox_collection.docs),
            list(run_events_collection.docs),
            list(case_runs_collection.docs),
        ) == snapshot
        assert len(transact.sessions) == 2, "each call is one transaction"
        run = await _load(run_store, run_id)
        assert run.counters == {"agents_run": 1}, "the counter was bumped once"

    async def test_it_runs_in_one_transaction(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        gates_collection: FakeCollection,
        outbox_collection: FakeCollection,
        run_events_collection: FakeCollection,
        case_runs_collection: FakeCollection,
        transact: FakeTransact,
    ) -> None:
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        before = {
            id(c): len(c.sessions)
            for c in (
                gates_collection,
                outbox_collection,
                run_events_collection,
                case_runs_collection,
            )
        }
        result = StepResult(
            gates_to_open=(GateSpec(GateType.REVIEW_ITEM, "CTL-T01"),),
            agent_jobs_to_dispatch=(AgentJobSpec(AgentDispatch(AgentName.CRITIC)),),
            refs_patch={"score_snapshot": "snap_1"},
            band=3,
        )

        await engine.complete_step(run_id, step_id, result)

        [txn_session] = transact.sessions
        for collection in (
            gates_collection,
            outbox_collection,
            run_events_collection,
            case_runs_collection,
        ):
            written = collection.sessions[before[id(collection)] :]
            assert written, "every collection should have been written"
            assert all(s is txn_session for s in written)

    async def test_a_gate_listed_twice_or_already_open_is_created_and_announced_once(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        gates_collection: FakeCollection,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        gate_id = deterministic_id("gate", run_id, "review_item", "CTL-T01")
        decided = gate_document(
            gate_id=gate_id,
            case_id=CASE_ID,
            run_id=run_id,
            gate_type=GateType.REVIEW_ITEM,
            required_role=REQUIRED_ROLE[GateType.REVIEW_ITEM],
        )
        decided["status"] = GateStatus.DECIDED.value
        decided["decision"] = "confirm"
        await GateStore(gates_collection).open(decided, session=session)  # type: ignore[arg-type]
        result = StepResult(
            gates_to_open=(
                GateSpec(GateType.REVIEW_ITEM, "CTL-T01"),
                GateSpec(GateType.REVIEW_ITEM, "CTL-T02"),
                GateSpec(GateType.REVIEW_ITEM, "CTL-T02"),
            )
        )

        await engine.complete_step(run_id, step_id, result)

        assert len(gates_collection.docs) == 2
        existing = next(d for d in gates_collection.docs if d["_id"] == gate_id)
        assert existing["status"] == GateStatus.DECIDED.value, "left exactly as it was"
        announced = [
            GateOpened.model_validate(e.payload).gate_id
            for e in _outbox_envelopes(outbox_collection, "gate.opened")
        ]
        assert announced == [deterministic_id("gate", run_id, "review_item", "CTL-T02")]

    async def test_agent_jobs_carry_their_refs(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        refs = {"mode": "completeness", "submission_version": 2}
        result = StepResult(
            agent_jobs_to_dispatch=(
                AgentJobSpec(
                    AgentDispatch(AgentName.NARRATIVE, scope="completeness"), refs
                ),
            )
        )

        await engine.complete_step(run_id, step_id, result)

        [job] = _outbox_envelopes(outbox_collection, "agent.run")
        payload = JobPayload.model_validate(job.payload)
        assert payload.agent is AgentName.NARRATIVE
        assert payload.refs == refs
        assert payload.run_id == run_id

    async def test_two_jobs_make_the_run_wait_for_both(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
    ) -> None:
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        result = StepResult(
            agent_jobs_to_dispatch=(
                AgentJobSpec(AgentDispatch(AgentName.RESEARCH)),
                AgentJobSpec(AgentDispatch(AgentName.ASSESSMENT)),
            )
        )
        await engine.complete_step(run_id, step_id, result)
        assert (await _load(run_store, run_id)).pending_agents == (
            "research",
            "assessment",
        )

    async def test_refs_counters_and_band_are_persisted(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
    ) -> None:
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        await engine.complete_step(
            run_id,
            step_id,
            StepResult(
                refs_patch={"score_snapshot": "snap_1", "pack_id": None},
                counter_deltas={"agents_run": 2, "tokens_used": 5000},
                band=3,
            ),
        )
        # A later step merges key by key and keeps adding to the counters.
        await engine.handle(
            _env("case.document_stored", document_stored_payload("doc_2"), run_id),
            session,
        )
        second = (await _load(run_store, run_id)).pending_steps[0]
        await engine.complete_step(
            run_id,
            second,
            StepResult(
                refs_patch={"pack_id": "pack_1"}, counter_deltas={"agents_run": 1}
            ),
        )

        run = await _load(run_store, run_id)
        assert run.refs == {"score_snapshot": "snap_1", "pack_id": "pack_1"}
        assert run.counters == {"agents_run": 3, "tokens_used": 5000}
        assert run.band == 3

    async def test_ui_events_go_through_the_outbox_with_the_case_and_run_ids(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        await engine.complete_step(
            run_id,
            step_id,
            StepResult(
                ui_events=(UiEvent("score.computed", {"composite": 9.8, "band": 2}),)
            ),
        )
        [event] = _outbox_envelopes(outbox_collection, "score.computed")
        assert (event.case_id, event.run_id) == (CASE_ID, run_id)
        assert event.payload == {"composite": 9.8, "band": 2}

    async def test_an_uncatalogued_ui_event_fails_the_whole_step(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
    ) -> None:
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        with pytest.raises(UnknownEventTypeError):
            await engine.complete_step(
                run_id,
                step_id,
                StepResult(ui_events=(UiEvent("not.a.real.event"),)),
            )

    async def test_the_step_done_row_is_not_a_transition_and_replay_still_works(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        run_events_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        before = await _load(run_store, run_id)

        await engine.complete_step(
            run_id, step_id, StepResult(), step=StepKind.INTAKE_DOCUMENT_STORED
        )

        done = [d for d in run_events_collection.docs if d["kind"] == "step_done"]
        assert len(done) == 1
        assert done[0]["data"] == {
            "step_id": step_id,
            "step": "intake.document_stored",
        }
        assert done[0]["state_from"] == done[0]["state_to"] == "intake"
        after = await _load(run_store, run_id)
        assert after.snapshot.version == before.snapshot.version + 1
        events = await run_store.load_recorded_transitions(run_id)
        assert rebuild(open_run(), events) is after.state
        assert all(d["kind"] == "transition" for d in run_events_collection.docs[:-1])

    async def test_without_a_transact_it_refuses_instead_of_running_unprotected(
        self,
        run_store: CaseRunStore,
        gate_store: GateStore,
        outbox: Outbox,
        recording_audit: RecordingAudit,
    ) -> None:
        engine = CaseflowEngine(run_store, gate_store, outbox, recording_audit)
        with pytest.raises(ValueError, match="transact"):
            await engine.complete_step("run_1", "step_1", StepResult())

    async def test_a_step_for_a_missing_run_raises(
        self, engine: CaseflowEngine
    ) -> None:
        with pytest.raises(OrphanEventError):
            await engine.complete_step("run_nope", "step_1", StepResult())

    async def test_completing_a_finished_step_again_does_not_announce_its_gates_again(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        """A redelivered trigger after completion enqueues the *same* step id:
        the unique ``step_done`` row means completing it again is a no-op, so
        the engine cannot re-open gates a human already decided.
        """
        engine = engine_factory(STORED_MAP)
        run_id, step_id = await _pending_step_run(engine, run_store, session)
        await engine.complete_step(
            run_id,
            step_id,
            StepResult(gates_to_open=(GateSpec(GateType.REVIEW_ITEM, "CTL-T01"),)),
        )
        announced_before = len(_outbox_envelopes(outbox_collection, "gate.opened"))

        await engine.complete_step(
            run_id,
            step_id,
            StepResult(gates_to_open=(GateSpec(GateType.REVIEW_ITEM, "CTL-T01"),)),
        )

        assert (
            len(_outbox_envelopes(outbox_collection, "gate.opened")) == announced_before
        )

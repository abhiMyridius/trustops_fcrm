"""The human handoff (flow F-path): an agent that failed after its retry sends the
run to ``HANDOFF`` with a gate that names the failed job; the analyst completes the
step by hand; the engine treats that output exactly like the agent's ``agent.done``
(``actor`` human) and resumes the run to the state it was suspended from.

Every collection is the in-memory :class:`~conftest.FakeCollection`; the real
transaction is ``test_engine_handoff_live.py``'s job.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

import pytest
from conftest import (
    FakeCollection,
    FakeSession,
    RecordingAudit,
    clause_proposal,
    factor_proposal,
    screening_proposal,
)
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.dispatch import REQUIRED_ROLE
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.gates import (
    GateDecisionError,
    GateStore,
    deterministic_gate_id,
    gate_document,
)
from fcrm_caseflow.handoff import (
    HandoffError,
    handoff_subject,
    manual_done_payload,
)
from fcrm_caseflow.store import AgentResultStore, CaseRun, CaseRunStore
from fcrm_caseflow.transitions import TransitionContext, apply
from fcrm_caseflow.triggers import Trigger
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import (
    AgentName,
    CaseState,
    GateStatus,
    GateType,
)
from fcrm_contracts.events.envelope import Envelope

CASE_ID = "FCRM-2026-0151"
ANALYST = "u-riyer"
REASON = "The agent failed twice; I wrote the clauses from the policy text."
EngineFactory = Callable[..., CaseflowEngine]


def _env(event_type: str, payload: dict[str, Any], run_id: str) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id=CASE_ID,
        run_id=run_id,
        occurred_at=utcnow(),
        payload=payload,
    )


def _failed(run_id: str, agent: str, job_id: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "case_id": CASE_ID,
        "run_id": run_id,
        "agent": agent,
        "attempt": 2,
        "reason": "guard4_rejected: schema mismatch",
    }


def _done(run_id: str, agent: str, job_id: str, *accepted: dict[str, Any]) -> dict:
    return {
        "job_id": job_id,
        "case_id": CASE_ID,
        "run_id": run_id,
        "agent": agent,
        "output": {"accepted": list(accepted), "dropped": [], "notes_for_analyst": []},
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "model": "agent-model",
        "attempt": 1,
    }


def _complete(
    gate_id: str,
    run_id: str,
    output: Any,
    *,
    reason: Optional[str] = REASON,
) -> dict[str, Any]:
    return GateDecision(
        gate_id=gate_id,
        gate_type=GateType.HANDOFF,
        case_id=CASE_ID,
        run_id=run_id,
        decision="complete",
        reason=reason,
        decided_by=ANALYST,
        decided_at=utcnow(),
        after={"output": output},
    ).model_dump(mode="json")


async def _run_in_assess(
    engine: CaseflowEngine,
    run_store: CaseRunStore,
    session: FakeSession,
    pending: tuple[str, ...] = ("research", "assessment"),
) -> str:
    """A run in ``ASSESS`` with both agents dispatched (the state the screening
    chain leaves)."""
    await engine.handle(
        _env_no_run("case.created", {"actor": "u-pnair", "data": {}}), session
    )
    run = await run_store.get_by_case_id(CASE_ID)
    assert run is not None
    for _ in range(2):
        run = await run_store.apply_transition(
            run,
            to_state=CaseState.ASSESS,
            flags=run.snapshot.flags,
            trigger=Trigger.ASSESS_DISPATCH,
            action="test setup: jump straight to ASSESS",
            message_id=None,
            context=TransitionContext(),
            session=session,
        )
    await run_store.set_pending_agents(
        run.snapshot.run_id, list(pending), session=session
    )
    return run.snapshot.run_id


def _env_no_run(event_type: str, payload: dict[str, Any]) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id=CASE_ID,
        occurred_at=utcnow(),
        payload=payload,
    )


async def _load(run_store: CaseRunStore, run_id: str) -> CaseRun:
    run = await run_store.get(run_id)
    assert run is not None
    return run


def _gate_id(run_id: str, job_id: str) -> str:
    return deterministic_gate_id(run_id, GateType.HANDOFF, job_id)


def _outbox_types(outbox: FakeCollection, event_type: str) -> list[Envelope]:
    return [
        Envelope.model_validate_json(bytes(d["body"]))
        for d in outbox.docs
        if d["type"] == event_type
    ]


# --- the pure parts ------------------------------------------------------------


class TestHandoffSubject:
    def test_it_names_the_failed_job_and_the_stage_it_was_in(self) -> None:
        subject = handoff_subject(
            _failed("run_1", "research", "job_7"), CaseState.ASSESS
        )

        assert subject == {
            "agent": "research",
            "job_id": "job_7",
            "attempt": "2",
            "reason": "guard4_rejected: schema mismatch",
            "step": "assess",
        }

    def test_it_carries_the_raw_inputs_reference_when_h2_recorded_one(self) -> None:
        failed = {**_failed("run_1", "research", "job_7"), "raw_inputs_ref": "jobs/x"}

        assert handoff_subject(failed, None)["raw_inputs_ref"] == "jobs/x"
        assert "step" not in handoff_subject(failed, None)

    def test_a_payload_that_is_not_an_agent_failed_body_is_refused(self) -> None:
        with pytest.raises(ValueError):  # noqa: PT011
            handoff_subject({"agent": "research"}, None)


class TestManualDonePayload:
    SUBJECT = {"agent": "research", "job_id": "job_7", "attempt": "2"}

    def _decision(self, output: Any) -> GateDecision:
        return GateDecision.model_validate(_complete("gate_1", "run_1", output))

    def test_the_output_becomes_the_done_of_the_failed_job_with_actor_human(
        self,
    ) -> None:
        item = clause_proposal("FFIEC-TPPP-04", "channel")

        done = manual_done_payload(self._decision([item]), self.SUBJECT)

        assert done["job_id"] == "job_7"
        assert done["agent"] == "research"
        assert done["model"] == "human"
        assert done["usage"] == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        assert done["output"]["accepted"] == [item]
        assert done["output"]["actor"] == "human"
        assert done["output"]["completed_by"] == ANALYST
        assert done["fast_lane_proposed"] is None

    def test_no_claim_is_counted_as_matched_to_its_source(self) -> None:
        done = manual_done_payload(
            self._decision([clause_proposal("FFIEC-TPPP-04", "channel")]), self.SUBJECT
        )

        summary = done["output"]["guard_summary"]
        assert (summary["claims_total"], summary["claims_with_source"]) == (1, 0)
        assert (
            "not matched against the documents"
            in done["output"]["notes_for_analyst"][0]
        )

    def test_a_screening_output_carries_its_fast_lane_flag_for_the_guard(self) -> None:
        subject = {"agent": "screening", "job_id": "job_1", "attempt": "2"}

        done = manual_done_payload(
            self._decision([screening_proposal(fast_lane=True)]), subject
        )

        assert done["fast_lane_proposed"] is True

    @pytest.mark.parametrize(
        "output",
        [None, "text", {"accepted": []}, [], [{"nope": 1}]],
        ids=["none", "string", "object", "empty", "wrong shape"],
    )
    def test_an_output_that_fails_the_checks_is_refused(self, output: Any) -> None:
        with pytest.raises(HandoffError):
            manual_done_payload(self._decision(output), self.SUBJECT)

    def test_a_gate_that_names_no_job_cannot_be_completed(self) -> None:
        with pytest.raises(HandoffError, match="names no failed job"):
            manual_done_payload(self._decision([clause_proposal("X-1", "channel")]), {})


class TestTheTransition:
    def test_the_run_resumes_to_the_state_it_was_suspended_from(self) -> None:
        ctx = TransitionContext(resume_state=CaseState.ASSESS)

        result = apply(CaseState.HANDOFF, Trigger.GATE_DECIDED_HANDOFF, ctx)

        assert result.to_state is CaseState.ASSESS

    def test_a_run_with_no_remembered_state_resumes_to_review(self) -> None:
        result = apply(
            CaseState.HANDOFF, Trigger.GATE_DECIDED_HANDOFF, TransitionContext()
        )

        assert result.to_state is CaseState.REVIEW

    @pytest.mark.parametrize("target", [CaseState.HANDOFF, CaseState.CLOSED])
    def test_a_run_never_resumes_into_handoff_or_closed(
        self, target: CaseState
    ) -> None:
        with pytest.raises(IllegalTransition):
            apply(
                CaseState.HANDOFF,
                Trigger.GATE_DECIDED_HANDOFF,
                TransitionContext(resume_state=target),
            )

    def test_a_resume_state_that_came_back_from_mongo_as_a_string_still_resolves(
        self,
    ) -> None:
        ctx = TransitionContext(resume_state="intake")  # type: ignore[arg-type]

        assert (
            apply(CaseState.HANDOFF, Trigger.GATE_DECIDED_HANDOFF, ctx).to_state
            is CaseState.INTAKE
        )


# --- the engine ----------------------------------------------------------------


class TestAFailedJobOpensAGateThatNamesIt:
    async def test_the_gate_carries_the_job_and_the_run_remembers_where_it_was(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        run_id = await _run_in_assess(engine, run_store, session)

        await engine.handle(
            _env("agent.failed", _failed(run_id, "research", "job_r"), run_id), session
        )

        run = await _load(run_store, run_id)
        assert run.state is CaseState.HANDOFF
        assert run.suspended_from is CaseState.ASSESS
        gate = await gate_store.get(_gate_id(run_id, "job_r"))
        assert gate is not None
        assert (gate["gate_type"], gate["status"]) == ("handoff", "open")
        assert gate["subject"]["agent"] == "research"
        assert gate["subject"]["job_id"] == "job_r"
        assert gate["subject"]["step"] == "assess"
        [opened] = _outbox_types(outbox_collection, "gate.opened")
        assert opened.payload["subject"]["job_id"] == "job_r"

    async def test_a_second_failure_keeps_the_state_it_first_suspended_from(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
    ) -> None:
        run_id = await _run_in_assess(engine, run_store, session)

        for agent, job in (("research", "job_r"), ("assessment", "job_a")):
            await engine.handle(
                _env("agent.failed", _failed(run_id, agent, job), run_id), session
            )

        run = await _load(run_store, run_id)
        assert (run.state, run.suspended_from) == (CaseState.HANDOFF, CaseState.ASSESS)
        assert await gate_store.count_open(CASE_ID, GateType.HANDOFF) == 2

    async def test_a_replayed_failure_opens_the_same_gate_once(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
    ) -> None:
        run_id = await _run_in_assess(engine, run_store, session)
        failed = _env("agent.failed", _failed(run_id, "research", "job_r"), run_id)

        await engine.handle(failed, session)
        await engine.handle(failed, session)

        assert await gate_store.count_open(CASE_ID, GateType.HANDOFF) == 1


class TestTheAnalystCompletesTheStep:
    async def test_the_run_resumes_and_the_join_fires_as_if_the_agent_had_finished(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        gate_store: GateStore,
        agent_results: AgentResultStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
        recording_audit: RecordingAudit,
    ) -> None:
        engine = engine_factory(results=agent_results)
        run_id = await _run_in_assess(engine, run_store, session)
        await engine.handle(
            _env("agent.failed", _failed(run_id, "research", "job_r"), run_id), session
        )
        # The other half of the pair finishes while the case is with a person.
        await engine.handle(
            _env(
                "agent.done",
                _done(run_id, "assessment", "job_a", factor_proposal("channel", 3, 4)),
                run_id,
            ),
            session,
        )
        waiting = await _load(run_store, run_id)
        assert waiting.state is CaseState.HANDOFF
        assert waiting.pending_agents == ("research",)
        assert _outbox_types(outbox_collection, "agent.run") == []

        item = clause_proposal("FFIEC-TPPP-04", "channel")
        await engine.handle(
            _env(
                "gate.decided",
                _complete(_gate_id(run_id, "job_r"), run_id, [item]),
                run_id,
            ),
            session,
        )

        run = await _load(run_store, run_id)
        assert run.state is CaseState.CRITIC  # the pair's join fired, as for an agent
        assert run.suspended_from is None
        assert run.pending_agents == ()
        gate = await gate_store.get(_gate_id(run_id, "job_r"))
        assert gate is not None
        assert (gate["status"], gate["decided_by"]) == (
            GateStatus.DECIDED.value,
            ANALYST,
        )
        assert gate["reason"] == REASON
        [critic] = [
            e.payload
            for e in _outbox_types(outbox_collection, "agent.run")
            if e.payload["agent"] == "critic"
        ]
        assert critic["run_id"] == run_id
        [stored] = await agent_results.list_for(run_id, AgentName.RESEARCH)
        assert stored.job_id == "job_r"
        assert stored.model == "human"
        assert stored.accepted() == [item]
        assert stored.usage["input_tokens"] == 0

    async def test_with_two_failed_jobs_the_run_resumes_on_the_last_decision(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        agent_results: AgentResultStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(results=agent_results)
        run_id = await _run_in_assess(engine, run_store, session)
        for agent, job in (("research", "job_r"), ("assessment", "job_a")):
            await engine.handle(
                _env("agent.failed", _failed(run_id, agent, job), run_id), session
            )

        await engine.handle(
            _env(
                "gate.decided",
                _complete(
                    _gate_id(run_id, "job_r"),
                    run_id,
                    [clause_proposal("FFIEC-TPPP-04", "channel")],
                ),
                run_id,
            ),
            session,
        )
        first = await _load(run_store, run_id)
        assert first.state is CaseState.HANDOFF  # another job still waits
        assert first.pending_agents == ("assessment",)

        await engine.handle(
            _env(
                "gate.decided",
                _complete(
                    _gate_id(run_id, "job_a"),
                    run_id,
                    [factor_proposal("channel", 3, 4)],
                ),
                run_id,
            ),
            session,
        )

        last = await _load(run_store, run_id)
        assert last.state is CaseState.CRITIC
        assert [
            e.payload["agent"] for e in _outbox_types(outbox_collection, "agent.run")
        ] == ["critic"]

    async def test_a_replay_of_the_run_lands_where_the_live_run_did(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        engine = engine_factory(results=agent_results)
        run_id = await _run_in_assess(engine, run_store, session, ("assessment",))
        await engine.handle(
            _env("agent.failed", _failed(run_id, "assessment", "job_a"), run_id),
            session,
        )
        await engine.handle(
            _env(
                "gate.decided",
                _complete(
                    _gate_id(run_id, "job_a"),
                    run_id,
                    [factor_proposal("channel", 3, 4)],
                ),
                run_id,
            ),
            session,
        )

        run = await _load(run_store, run_id)
        recorded = await run_store.load_recorded_transitions(run_id)

        [resume] = [r for r in recorded if r.trigger is Trigger.GATE_DECIDED_HANDOFF]
        assert resume.context.resume_state is CaseState.ASSESS
        replayed = apply(CaseState.HANDOFF, resume.trigger, resume.context)
        assert replayed.to_state is CaseState.ASSESS
        assert run.state is CaseState.CRITIC

    async def test_a_screening_step_resumes_with_the_fast_lane_the_person_chose(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        gate_store: GateStore,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        engine = engine_factory(results=agent_results)
        await engine.handle(
            _env_no_run("case.created", {"actor": "u-pnair", "data": {}}), session
        )
        run = await run_store.get_by_case_id(CASE_ID)
        assert run is not None
        run_id = run.snapshot.run_id
        for _ in range(2):
            run = await run_store.apply_transition(
                run,
                to_state=CaseState.SCREENING,
                flags=run.snapshot.flags,
                trigger=Trigger.SCREENING_DISPATCH,
                action="test setup",
                message_id=None,
                context=TransitionContext(),
                session=session,
            )
        await engine.handle(
            _env("agent.failed", _failed(run_id, "screening", "job_s"), run_id), session
        )

        await engine.handle(
            _env(
                "gate.decided",
                _complete(
                    _gate_id(run_id, "job_s"),
                    run_id,
                    [screening_proposal(fast_lane=True)],
                ),
                run_id,
            ),
            session,
        )

        resumed = await _load(run_store, run_id)
        assert resumed.state is CaseState.SCREENING  # back where it was
        # The person's fast-lane proposal reached the guard: the analyst is asked.
        assert await gate_store.count_open(CASE_ID, GateType.FAST_LANE_CONFIRM) == 1


class TestRefusals:
    async def test_an_output_that_fails_the_checks_leaves_the_gate_open(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        gate_store: GateStore,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        engine = engine_factory(results=agent_results)
        run_id = await _run_in_assess(engine, run_store, session)
        await engine.handle(
            _env("agent.failed", _failed(run_id, "research", "job_r"), run_id), session
        )

        with pytest.raises(HandoffError):
            await engine.handle(
                _env(
                    "gate.decided",
                    _complete(_gate_id(run_id, "job_r"), run_id, [{"score": 4.1}]),
                    run_id,
                ),
                session,
            )

        gate = await gate_store.get(_gate_id(run_id, "job_r"))
        assert gate is not None
        assert gate["status"] == GateStatus.OPEN.value
        run = await _load(run_store, run_id)
        assert run.state is CaseState.HANDOFF

    async def test_a_decision_without_a_reason_is_refused_by_guard_7(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        engine = engine_factory(results=agent_results)
        run_id = await _run_in_assess(engine, run_store, session)
        await engine.handle(
            _env("agent.failed", _failed(run_id, "research", "job_r"), run_id), session
        )

        with pytest.raises(GateDecisionError, match="needs a reason"):
            await engine.handle(
                _env(
                    "gate.decided",
                    _complete(
                        _gate_id(run_id, "job_r"),
                        run_id,
                        [clause_proposal("FFIEC-TPPP-04", "channel")],
                        reason="ok",
                    ),
                    run_id,
                ),
                session,
            )

        assert (await _load(run_store, run_id)).state is CaseState.HANDOFF

    async def test_a_gate_with_no_subject_cannot_be_completed(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        gate_store: GateStore,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        """A handoff gate opened before the subject existed names no job."""
        engine = engine_factory(results=agent_results)
        run_id = await _run_in_assess(engine, run_store, session)
        await gate_store.open(
            gate_document(
                gate_id="gate_legacy",
                case_id=CASE_ID,
                run_id=run_id,
                gate_type=GateType.HANDOFF,
                required_role=REQUIRED_ROLE[GateType.HANDOFF],
            ),
            session=session,
        )

        with pytest.raises(HandoffError, match="names no failed job"):
            await engine.handle(
                _env(
                    "gate.decided",
                    _complete(
                        "gate_legacy",
                        run_id,
                        [clause_proposal("FFIEC-TPPP-04", "channel")],
                    ),
                    run_id,
                ),
                session,
            )


class TestTheStepsThatTellS1:
    async def test_the_failure_and_the_resume_each_enqueue_the_handoff_step(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        agent_results: AgentResultStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(results=agent_results, step_map=dict(STEP_FOR))
        run_id = await _run_in_assess(engine, run_store, session, ("assessment",))

        await engine.handle(
            _env("agent.failed", _failed(run_id, "assessment", "job_a"), run_id),
            session,
        )
        after_failure = _outbox_types(outbox_collection, "harness.step")
        await engine.handle(
            _env(
                "gate.decided",
                _complete(
                    _gate_id(run_id, "job_a"),
                    run_id,
                    [factor_proposal("channel", 3, 4)],
                ),
                run_id,
            ),
            session,
        )

        steps = _outbox_types(outbox_collection, "harness.step")
        handoff = [s for s in steps if s.payload["step"] == "handoff.open"]
        assert [s.payload["step"] for s in after_failure][-1] == "handoff.open"
        assert [s.payload["args"]["trigger"] for s in handoff] == [
            "agent.failed.after_retry",
            "gate.decided.handoff",
        ]

    async def test_a_completion_that_leaves_the_run_in_handoff_still_enqueues_it(
        self,
        engine_factory: EngineFactory,
        run_store: CaseRunStore,
        agent_results: AgentResultStore,
        session: FakeSession,
        outbox_collection: FakeCollection,
    ) -> None:
        engine = engine_factory(results=agent_results, step_map=dict(STEP_FOR))
        run_id = await _run_in_assess(engine, run_store, session)
        for agent, job in (("research", "job_r"), ("assessment", "job_a")):
            await engine.handle(
                _env("agent.failed", _failed(run_id, agent, job), run_id), session
            )
        before = len(_outbox_types(outbox_collection, "harness.step"))

        await engine.handle(
            _env(
                "gate.decided",
                _complete(
                    _gate_id(run_id, "job_r"),
                    run_id,
                    [clause_proposal("FFIEC-TPPP-04", "channel")],
                ),
                run_id,
            ),
            session,
        )

        steps = _outbox_types(outbox_collection, "harness.step")
        assert (await _load(run_store, run_id)).state is CaseState.HANDOFF
        assert len(steps) == before + 1
        assert steps[-1].payload["args"]["trigger"] == "gate.decided.handoff"
        assert steps[-1].payload["step"] == "handoff.open"

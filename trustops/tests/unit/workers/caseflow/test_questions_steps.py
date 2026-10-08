"""H1's steps for the analyst's ad-hoc questions to the owner
(:mod:`fcrm_caseflow.cores.questions`, ADR-73), the transitions that enqueue them
and their wiring.

What these tests hold: a question asked is recorded and nothing else happens; an
answer is always recorded; an answer that changed a value re-runs the agents on
the changed fields only in REVIEW with the agents idle (assessment alone unless a
field research reads changed, waiting for an added document), and is flagged to
the analyst at every other stage or while agents run; the run never moves on a
question, and the D-16 round's clock flag is untouched.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, Optional

import pytest
from conftest import (
    FakeCaseService,
    FakeCollection,
    FakeSession,
    RecordingAudit,
    harness_step,
    make_run,
)
from fcrm_caseflow.audit_map import RECORDS_FOR, STEP_FOR
from fcrm_caseflow.cores import default_handlers, questions
from fcrm_caseflow.cores.refs import RERUN_ACTIVE_REF, RERUN_PENDING_REF, RERUN_REF
from fcrm_caseflow.dispatch import REQUIRED_ROLE
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.flags import next_flags
from fcrm_caseflow.gates import GateStore, gate_document
from fcrm_caseflow.resolve import resolve_simple
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.steps import StepDeps
from fcrm_caseflow.store import CaseRun, CaseRunStore
from fcrm_caseflow.transitions import (
    ADHOC_QUESTION_STATES,
    TransitionContext,
    apply,
)
from fcrm_caseflow.triggers import Trigger
from fcrm_common.events import build_envelope
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import AgentName, CaseState, GateType, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep

CASE = "FCRM-2026-0151"
RUN = "run_1"
DepsFactory = Callable[..., StepDeps]

ASKED = {
    "case_id": CASE,
    "actor": "u-riyer",
    "thread_ids": ["thr_a1", "thr_a2"],
    "question_count": 2,
}


def answered(
    changed: Optional[list[str]] = None,
    docs: Optional[list[str]] = None,
    version: Optional[int] = 3,
) -> dict[str, Any]:
    return {
        "case_id": CASE,
        "actor": "u-pnair",
        "thread_id": "thr_a1",
        "question_id": "A1",
        "changed_field_ids": changed if changed is not None else ["F11"],
        "added_document_ids": docs or [],
        "submission_version": version,
    }


def deps_in(
    factory: DepsFactory,
    state: CaseState,
    *,
    refs: Optional[dict[str, Any]] = None,
    pending_agents: tuple[str, ...] = (),
) -> StepDeps:
    run = make_run(run_id=RUN, state=state, refs=refs or {})
    return factory(dataclasses.replace(run, pending_agents=pending_agents))


def types(audit: RecordingAudit) -> list[str]:
    return [r.type for r in audit.records]


# ── wiring ──────────────────────────────────────────────────────────────────────


class TestWiring:
    def test_the_events_resolve_to_their_triggers(self) -> None:
        for wire, trigger in (
            ("case.question_asked", Trigger.CASE_QUESTION_ASKED),
            ("case.question_answered", Trigger.CASE_QUESTION_ANSWERED),
        ):
            resolution = resolve_simple(
                build_envelope(wire, {}, case_id=CASE, run_id=RUN)
            )
            assert resolution is not None
            assert resolution.trigger is trigger

    def test_each_trigger_starts_its_step_and_the_step_has_a_handler(self) -> None:
        assert STEP_FOR[Trigger.CASE_QUESTION_ASKED] is StepKind.QUESTION_ASKED
        assert STEP_FOR[Trigger.CASE_QUESTION_ANSWERED] is StepKind.QUESTION_ANSWERED
        handlers = default_handlers()
        assert handlers[StepKind.QUESTION_ASKED] is questions.question_asked
        assert handlers[StepKind.QUESTION_ANSWERED] is questions.question_answered

    def test_the_records_each_step_writes_are_documented(self) -> None:
        assert RECORDS_FOR[StepKind.QUESTION_ASKED] == ("question.asked",)
        assert RECORDS_FOR[StepKind.QUESTION_ANSWERED] == (
            "question.answered",
            "agent.rerun",
        )

    def test_the_triggers_are_not_internal_so_they_never_self_fire(self) -> None:
        assert not Trigger.CASE_QUESTION_ASKED.value.startswith("internal.")
        assert not Trigger.CASE_QUESTION_ANSWERED.value.startswith("internal.")


# ── transitions ─────────────────────────────────────────────────────────────────


class TestTransitions:
    @pytest.mark.parametrize("state", ADHOC_QUESTION_STATES)
    @pytest.mark.parametrize(
        "trigger", [Trigger.CASE_QUESTION_ASKED, Trigger.CASE_QUESTION_ANSWERED]
    )
    def test_a_question_never_moves_the_run(
        self, state: CaseState, trigger: Trigger
    ) -> None:
        assert apply(state, trigger, TransitionContext()).to_state is state

    def test_the_states_cover_triage_to_decide_and_the_handoff(self) -> None:
        assert set(ADHOC_QUESTION_STATES) == set(CaseState) - {
            CaseState.INTAKE,
            CaseState.REVIEW_AND_SUBMIT,
            CaseState.CLOSED,
        }

    @pytest.mark.parametrize(
        "state", [CaseState.INTAKE, CaseState.REVIEW_AND_SUBMIT, CaseState.CLOSED]
    )
    @pytest.mark.parametrize(
        "trigger", [Trigger.CASE_QUESTION_ASKED, Trigger.CASE_QUESTION_ANSWERED]
    )
    def test_no_question_before_submission_or_after_the_close(
        self, state: CaseState, trigger: Trigger
    ) -> None:
        with pytest.raises(IllegalTransition):
            apply(state, trigger, TransitionContext())

    def test_only_review_says_it_re_runs(self) -> None:
        review = apply(
            CaseState.REVIEW, Trigger.CASE_QUESTION_ANSWERED, TransitionContext()
        )
        decide = apply(
            CaseState.DECIDE, Trigger.CASE_QUESTION_ANSWERED, TransitionContext()
        )
        assert "re-run" in review.action
        assert "re-run" not in decide.action
        assert "flag" in decide.action

    @pytest.mark.parametrize(
        "trigger", [Trigger.CASE_QUESTION_ASKED, Trigger.CASE_QUESTION_ANSWERED]
    )
    def test_the_analyst_clock_is_not_paused(self, trigger: Trigger) -> None:
        """``round_open`` pauses the analyst's clock; it belongs to the D-16
        round, and a question leaves every flag as it was.
        """
        for flags in (RunFlags(), RunFlags(round_open=True)):
            assert next_flags(trigger, TransitionContext(), flags) == flags

    @pytest.mark.parametrize("state", [CaseState.REVIEW, CaseState.DECIDE])
    @pytest.mark.parametrize(
        "trigger",
        [Trigger.CASE_DOCUMENT_STORED, Trigger.INGEST_DONE, Trigger.INGEST_FAILED],
    )
    def test_a_document_added_with_an_answer_is_taken_at_any_stage(
        self, state: CaseState, trigger: Trigger
    ) -> None:
        assert apply(state, trigger, TransitionContext()).to_state is state


# ── the question asked ──────────────────────────────────────────────────────────


async def test_a_question_asked_is_recorded_and_nothing_else_happens(
    assessed_case: FakeCaseService,
    step_deps_factory: DepsFactory,
    recording_audit: RecordingAudit,
) -> None:
    result = await questions.question_asked(
        harness_step(StepKind.QUESTION_ASKED, ASKED),
        deps_in(step_deps_factory, CaseState.SCREENING),
    )

    [record] = recording_audit.records
    assert record.type == "question.asked"
    assert record.actor == "u-riyer"
    assert record.summary == (
        "2 questions sent to the owner outside the clarification round · analyst "
        "clock keeps running"
    )
    assert record.data == {
        "thread_ids": ["thr_a1", "thr_a2"],
        "question_count": 2,
        "run_id": RUN,
        "step_id": "step_1",
    }
    assert result.agent_jobs_to_dispatch == ()
    assert result.refs_patch == {}
    assert assessed_case.state_patches == []


# ── the answer ──────────────────────────────────────────────────────────────────


async def test_an_answer_that_changed_nothing_is_recorded_only(
    assessed_case: FakeCaseService,
    step_deps_factory: DepsFactory,
    recording_audit: RecordingAudit,
) -> None:
    result = await questions.question_answered(
        harness_step(StepKind.QUESTION_ANSWERED, answered([], version=None)),
        deps_in(step_deps_factory, CaseState.REVIEW),
    )

    [record] = recording_audit.records
    assert record.type == "question.answered"
    assert record.summary == (
        "Owner answered question A1 · 0 fields changed · 0 documents added"
    )
    assert record.data["rerun"] is False
    assert result.agent_jobs_to_dispatch == ()
    assert assessed_case.events == []


async def test_in_review_with_the_agents_idle_a_changed_value_re_runs_assessment(
    assessed_case: FakeCaseService,
    step_deps_factory: DepsFactory,
    recording_audit: RecordingAudit,
) -> None:
    """F11 is not a field research's queries are worded from, so assessment
    alone re-runs, on F11 only, as the round's re-run does.
    """
    result = await questions.question_answered(
        harness_step(StepKind.QUESTION_ANSWERED, answered(["F11"])),
        deps_in(step_deps_factory, CaseState.REVIEW),
    )

    assert types(recording_audit) == ["question.answered", "agent.rerun"]
    record = recording_audit.records[0]
    assert record.summary == (
        "Owner answered question A1 · 1 field changed · 0 documents added · "
        "submission v3 · re-run on the changed fields"
    )
    assert record.data["rerun"] is True
    assert recording_audit.records[1].data == {
        "changed_fields": ["F11"],
        "round": None,
        "thread_id": "thr_a1",
        "run_id": RUN,
        "step_id": "step_1",
    }
    [job] = result.agent_jobs_to_dispatch
    assert job.dispatch.agent is AgentName.ASSESSMENT
    assert job.dispatch.scope == "changed_fields"
    assert job.dispatch.changed_fields == ("F11",)
    assert job.refs["submission_version"] == 3
    assert result.refs_patch[RERUN_REF]["reason"] == "owner answer to question A1"
    assert result.refs_patch[RERUN_ACTIVE_REF] is True
    assert result.refs_patch[RERUN_PENDING_REF] is None


async def test_a_changed_field_research_reads_re_runs_research_too(
    assessed_case: FakeCaseService,
    step_deps_factory: DepsFactory,
    recording_audit: RecordingAudit,
) -> None:
    result = await questions.question_answered(
        harness_step(StepKind.QUESTION_ANSWERED, answered(["F06"])),
        deps_in(step_deps_factory, CaseState.REVIEW),
    )

    assert {j.dispatch.agent for j in result.agent_jobs_to_dispatch} == {
        AgentName.RESEARCH,
        AgentName.ASSESSMENT,
    }
    assert result.counter_deltas == {"agents_run": 2}


async def test_the_re_run_waits_for_a_document_the_answer_added(
    assessed_case: FakeCaseService,
    step_deps_factory: DepsFactory,
    recording_audit: RecordingAudit,
) -> None:
    assessed_case.add_document("doc_lic", "licence.pdf")

    result = await questions.question_answered(
        harness_step(StepKind.QUESTION_ANSWERED, answered(["F11"], ["doc_lic"])),
        deps_in(step_deps_factory, CaseState.REVIEW),
    )

    assert result.agent_jobs_to_dispatch == ()
    pending = result.refs_patch[RERUN_PENDING_REF]
    assert pending["doc_ids"] == ["doc_lic"]
    assert pending["thread_id"] == "thr_a1"
    assert types(recording_audit) == ["question.answered"]


@pytest.mark.parametrize(
    ("refs", "pending_agents"),
    [
        ({}, ("research:job_r2", "assessment:job_a2")),
        ({RERUN_ACTIVE_REF: True}, ()),
        ({RERUN_PENDING_REF: {"doc_ids": ["doc_x"]}}, ()),
    ],
    ids=["a join is pending", "a re-run is running", "a re-run waits"],
)
async def test_in_review_with_agents_running_the_value_is_flagged_not_re_run(
    assessed_case: FakeCaseService,
    step_deps_factory: DepsFactory,
    recording_audit: RecordingAudit,
    refs: dict[str, Any],
    pending_agents: tuple[str, ...],
) -> None:
    result = await questions.question_answered(
        harness_step(StepKind.QUESTION_ANSWERED, answered(["F11"])),
        deps_in(
            step_deps_factory,
            CaseState.REVIEW,
            refs=refs,
            pending_agents=pending_agents,
        ),
    )

    assert result.agent_jobs_to_dispatch == ()
    assert types(recording_audit) == ["question.answered"]
    assert recording_audit.records[0].data["rerun"] is False
    [shown] = assessed_case.events
    assert shown["type"] == "values.changed"
    assert shown["visibility"] == "analyst"
    assert shown["detail"] == {"changed_fields": ["F11"], "state": "review"}


@pytest.mark.parametrize(
    "state",
    [
        CaseState.ASSESS,
        CaseState.CRITIC,
        CaseState.COMPLETENESS,
        CaseState.CLARIFICATION,
        CaseState.RECOMMEND,
        CaseState.DECIDE,
        CaseState.HANDOFF,
    ],
)
async def test_outside_review_the_value_is_recorded_and_flagged(
    assessed_case: FakeCaseService,
    step_deps_factory: DepsFactory,
    recording_audit: RecordingAudit,
    state: CaseState,
) -> None:
    result = await questions.question_answered(
        harness_step(StepKind.QUESTION_ANSWERED, answered(["F11"])),
        deps_in(step_deps_factory, state),
    )

    assert result.agent_jobs_to_dispatch == ()
    record = recording_audit.records[0]
    assert record.summary.endswith(
        f"flagged to the analyst, no re-run at {state.value}"
    )
    [shown] = assessed_case.events
    assert shown["summary"] == (
        "The owner's answer to question A1 changed 1 field since the agents ran; "
        "ask for a re-run in review if it matters"
    )


@pytest.mark.parametrize("state", [CaseState.TRIAGE, CaseState.SCREENING])
async def test_before_assessment_the_new_value_is_read_when_it_runs(
    assessed_case: FakeCaseService,
    step_deps_factory: DepsFactory,
    recording_audit: RecordingAudit,
    state: CaseState,
) -> None:
    result = await questions.question_answered(
        harness_step(StepKind.QUESTION_ANSWERED, answered(["F11"])),
        deps_in(step_deps_factory, state),
    )

    assert result.agent_jobs_to_dispatch == ()
    [shown] = assessed_case.events
    assert shown["summary"].endswith("the assessment reads the new values when it runs")


def test_the_agents_are_idle_only_with_no_join_no_run_and_no_wait() -> None:
    run = make_run(run_id=RUN, state=CaseState.REVIEW)

    assert questions.agents_idle(run)
    assert not questions.agents_idle(
        dataclasses.replace(run, pending_agents=("research:j",))
    )
    assert not questions.reruns_now(run, [])
    assert questions.reruns_now(run, ["F11"])
    assert not questions.reruns_now(
        make_run(run_id=RUN, state=CaseState.DECIDE), ["F11"]
    )


# ── the engine, over fakes ──────────────────────────────────────────────────────


async def _run_in(
    engine: CaseflowEngine,
    run_store: CaseRunStore,
    session: FakeSession,
    gates: GateStore,
    state: CaseState,
    flags: Optional[RunFlags] = None,
) -> CaseRun:
    """A run opened by ``case.created`` and moved to ``state`` (as the engine's
    other tests move one), with one undecided review item: without one, REVIEW
    chains on to RECOMMEND.
    """
    await engine.handle(
        Envelope(
            event_id=new_id("evt"),
            type="case.created",
            case_id=CASE,
            occurred_at=utcnow(),
            payload={"actor": "u-pnair", "data": {}},
        ),
        session,
    )
    run = await run_store.get_by_case_id(CASE)
    assert run is not None
    run = await run_store.apply_transition(
        run,
        to_state=state,
        flags=flags or run.snapshot.flags,
        trigger=Trigger.CASE_DOCUMENT_STORED,
        action="test setup",
        message_id=None,
        context=TransitionContext(),
        session=session,  # type: ignore[arg-type]
    )
    await gates.open(
        gate_document(
            gate_id=new_id("gate"),
            case_id=CASE,
            run_id=run.snapshot.run_id,
            gate_type=GateType.REVIEW_ITEM,
            required_role=REQUIRED_ROLE[GateType.REVIEW_ITEM],
        ),
        session=session,  # type: ignore[arg-type]
    )
    reloaded = await run_store.get(run.snapshot.run_id)
    assert reloaded is not None
    return reloaded


def _steps(outbox_collection: FakeCollection) -> list[HarnessStep]:
    envelopes = [
        Envelope.model_validate_json(bytes(doc["body"]))
        for doc in outbox_collection.docs
    ]
    return [
        HarnessStep.model_validate(e.payload)
        for e in envelopes
        if e.type == "harness.step"
    ]


@pytest.mark.parametrize(
    ("event_type", "payload", "kind"),
    [
        ("case.question_asked", ASKED, StepKind.QUESTION_ASKED),
        ("case.question_answered", answered(["F11"]), StepKind.QUESTION_ANSWERED),
    ],
)
@pytest.mark.parametrize("state", [CaseState.REVIEW, CaseState.DECIDE])
async def test_the_engine_records_a_question_where_the_run_is_and_starts_its_step(
    engine_factory: Callable[..., CaseflowEngine],
    run_store: CaseRunStore,
    gate_store: GateStore,
    outbox_collection: FakeCollection,
    session: FakeSession,
    event_type: str,
    payload: dict[str, Any],
    kind: StepKind,
    state: CaseState,
) -> None:
    engine = engine_factory(step_map=STEP_FOR)
    flags = RunFlags(locked=True) if state is CaseState.DECIDE else None
    run = await _run_in(engine, run_store, session, gate_store, state, flags)

    await engine.handle(
        Envelope(
            event_id=new_id("evt"),
            type=event_type,
            case_id=CASE,
            run_id=run.snapshot.run_id,
            occurred_at=utcnow(),
            payload=payload,
        ),
        session,
    )

    after = await run_store.get(run.snapshot.run_id)
    assert after is not None
    assert after.state is state
    assert after.snapshot.version == run.snapshot.version + 1
    assert after.snapshot.flags == run.snapshot.flags
    assert [s.step for s in _steps(outbox_collection)][-1] is kind

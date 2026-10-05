"""Tests for :mod:`fcrm_caseflow.dispatch`: which ``agent.run`` job(s) a
trigger implies, which gate (if any) opens, and the ``agent.run`` envelope
builder.
"""

from __future__ import annotations

from fcrm_caseflow.dispatch import (
    AGENT_TIERS,
    AgentDispatch,
    build_job_envelope,
    dispatch_for,
    gate_to_open,
)
from fcrm_caseflow.states import RunFlags, RunSnapshot
from fcrm_caseflow.store import DEFAULT_PINS, CaseRun
from fcrm_caseflow.transitions import TransitionContext
from fcrm_caseflow.triggers import Trigger
from fcrm_contracts.enums import AgentName, CaseState, GateType
from fcrm_contracts.events.payloads import JobPayload


def _run(flags: RunFlags | None = None) -> CaseRun:
    return CaseRun(
        snapshot=RunSnapshot(
            run_id="run_1",
            case_id="FCRM-2026-0151",
            state=CaseState.ASSESS,
            flags=flags or RunFlags(),
        ),
        pins=DEFAULT_PINS,
        prev_state=None,
        trace_id=None,
        root_span_id=None,
        opened_at=None,
        updated_at=None,
    )


class TestDispatchFor:
    def test_screening_dispatch_runs_the_screening_agent(self) -> None:
        plan = dispatch_for(Trigger.SCREENING_DISPATCH, TransitionContext(), RunFlags())
        assert plan.agents == (AgentDispatch(AgentName.SCREENING),)

    def test_assess_dispatch_runs_research_and_assessment_in_parallel(self) -> None:
        plan = dispatch_for(Trigger.ASSESS_DISPATCH, TransitionContext(), RunFlags())
        agents = {a.agent for a in plan.agents}
        assert agents == {AgentName.RESEARCH, AgentName.ASSESSMENT}

    def test_assess_dispatch_runs_assessment_only_on_the_fast_lane(self) -> None:
        plan = dispatch_for(
            Trigger.ASSESS_DISPATCH, TransitionContext(), RunFlags(fast_lane=True)
        )
        assert plan.agents == (AgentDispatch(AgentName.ASSESSMENT),)

    def test_critic_dispatch_runs_the_critic_agent(self) -> None:
        plan = dispatch_for(Trigger.CRITIC_DISPATCH, TransitionContext(), RunFlags())
        assert plan.agents == (AgentDispatch(AgentName.CRITIC),)

    def test_completeness_dispatch_runs_narrative_in_completeness_mode(self) -> None:
        plan = dispatch_for(
            Trigger.COMPLETENESS_DISPATCH, TransitionContext(), RunFlags()
        )
        assert plan.agents == (
            AgentDispatch(AgentName.NARRATIVE, scope="completeness"),
        )

    def test_case_answered_dispatches_nothing_the_rerun_step_does(self) -> None:
        """The re-run must wait for a document the owner added with the answer to
        be extracted, and only the ``rerun.record`` step can see whether one is
        pending, so the transition dispatches nothing.
        """
        plan = dispatch_for(
            Trigger.CASE_ANSWERED,
            TransitionContext(),
            RunFlags(),
            payload={"changed_field_ids": ["f1", "f2"]},
        )
        assert plan.agents == ()

    def test_all_items_decided_dispatches_narrative_recommendation(self) -> None:
        plan = dispatch_for(Trigger.ALL_ITEMS_DECIDED, TransitionContext(), RunFlags())
        assert plan.agents == (
            AgentDispatch(AgentName.NARRATIVE, scope="recommendation"),
        )

    def test_po_confirm_category_all_dispatches_intake_only_if_gaps_open(self) -> None:
        with_gaps = dispatch_for(
            Trigger.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL,
            TransitionContext(gaps_open=True),
            RunFlags(),
        )
        assert with_gaps.agents == (
            AgentDispatch(AgentName.INTAKE, scope="questions_only"),
        )
        without_gaps = dispatch_for(
            Trigger.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL,
            TransitionContext(gaps_open=False),
            RunFlags(),
        )
        assert without_gaps.agents == ()

    def test_an_unrelated_trigger_dispatches_nothing(self) -> None:
        plan = dispatch_for(Trigger.VOTE_CAST, TransitionContext(), RunFlags())
        assert plan.agents == ()


class TestGateToOpen:
    def test_agent_done_screening_opens_fast_lane_confirm_only_when_proposed(
        self,
    ) -> None:
        assert (
            gate_to_open(
                Trigger.AGENT_DONE_SCREENING, TransitionContext(fast_lane_proposed=True)
            )
            is GateType.FAST_LANE_CONFIRM
        )
        assert (
            gate_to_open(
                Trigger.AGENT_DONE_SCREENING,
                TransitionContext(fast_lane_proposed=False),
            )
            is None
        )

    def test_agent_done_narrative_completeness_opens_completeness_gate(self) -> None:
        assert (
            gate_to_open(Trigger.AGENT_DONE_NARRATIVE_COMPLETENESS, TransitionContext())
            is GateType.COMPLETENESS
        )

    def test_agent_failed_after_retry_opens_handoff_gate(self) -> None:
        assert (
            gate_to_open(Trigger.AGENT_FAILED_AFTER_RETRY, TransitionContext())
            is GateType.HANDOFF
        )

    def test_case_locked_opens_pack_release_gate(self) -> None:
        result = gate_to_open(Trigger.CASE_LOCKED, TransitionContext())
        assert result is GateType.PACK_RELEASE

    def test_agent_done_intake_opens_no_combined_gate_any_more(self) -> None:
        """ADR-28: one ``po_confirm_category`` gate per proposed change type
        now comes from the ``intake.publish`` step's result, not from this
        table.
        """
        assert gate_to_open(Trigger.AGENT_DONE_INTAKE, TransitionContext()) is None

    def test_agent_done_assess_both_opens_no_combined_gate_any_more(self) -> None:
        """One ``review_item`` gate per clause, rating, control and critic
        finding now comes from the ``assess.publish`` step's result.
        """
        assert gate_to_open(Trigger.AGENT_DONE_ASSESS_BOTH, TransitionContext()) is None

    def test_a_review_item_decision_opens_no_gate_by_itself(self) -> None:
        """Whether a decision crossed a band edge is S2's answer, which only the
        ``review.item`` step has, so it opens the ``ack`` gate: a payload's own
        ``band_changed`` or ``boundary_flagged`` never does.
        """
        for context in (
            TransitionContext(band_changed=True),
            TransitionContext(boundary_flagged=True),
            TransitionContext(band_changed=False, boundary_flagged=False),
        ):
            assert gate_to_open(Trigger.GATE_DECIDED_REVIEW_ITEM, context) is None

    def test_an_unrelated_trigger_opens_nothing(self) -> None:
        assert gate_to_open(Trigger.VOTE_CAST, TransitionContext()) is None


class TestBuildJobEnvelope:
    def test_builds_an_agent_run_envelope_with_the_right_tier_and_pins(self) -> None:
        run = _run()
        env = build_job_envelope(AgentDispatch(AgentName.CRITIC), run)
        assert env.type == "agent.run"
        assert env.case_id == run.snapshot.case_id
        assert env.run_id == run.snapshot.run_id
        payload = JobPayload.model_validate(env.payload)
        assert payload.agent is AgentName.CRITIC
        assert payload.tier == AGENT_TIERS[AgentName.CRITIC]
        assert payload.pins == DEFAULT_PINS

    def test_scope_and_changed_fields_carry_through(self) -> None:
        run = _run()
        dispatch = AgentDispatch(
            AgentName.RESEARCH, scope="changed_fields", changed_fields=("a", "b")
        )
        env = build_job_envelope(dispatch, run)
        payload = JobPayload.model_validate(env.payload)
        assert payload.scope == "changed_fields"
        assert payload.changed_fields == ["a", "b"]

    def test_refs_default_to_empty(self) -> None:
        env = build_job_envelope(AgentDispatch(AgentName.CRITIC), _run())
        assert JobPayload.model_validate(env.payload).refs == {}

    def test_refs_become_the_job_payload_refs(self) -> None:
        refs = {"mode": "completeness", "submission_version": 2}
        env = build_job_envelope(
            AgentDispatch(AgentName.NARRATIVE, scope="completeness"),
            _run(),
            refs=refs,
        )
        assert JobPayload.model_validate(env.payload).refs == refs
        assert env.payload["refs"] == refs

    def test_the_refs_are_copied_not_shared_with_the_caller(self) -> None:
        refs = {"mode": "completeness"}
        env = build_job_envelope(AgentDispatch(AgentName.NARRATIVE), _run(), refs=refs)
        refs["mode"] = "recommendation"
        assert env.payload["refs"] == {"mode": "completeness"}

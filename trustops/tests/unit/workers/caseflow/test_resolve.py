"""Tests for :mod:`fcrm_caseflow.resolve`: the literal-mapping table, the
D-15 ``agent.failed`` resolution, the agent-done single/pair tables and
the ``gate.decided`` sub-trigger mapping.
"""

from __future__ import annotations

import pytest
from fcrm_caseflow.resolve import (
    agent_done_agent,
    agent_done_pair_members,
    agent_done_single,
    context_from_payload,
    gate_decided_trigger,
    resolve_simple,
)
from fcrm_caseflow.triggers import Trigger
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import AgentName, CaseState, GateType
from fcrm_contracts.events.envelope import Envelope
from pydantic import ValidationError


def _env(event_type: str, payload: dict[str, object]) -> Envelope:
    """Built directly against the ``Envelope`` model, not
    ``fcrm_common.events.build_envelope`` — a real consumer only ever sees
    an envelope through ``decode_envelope`` (``Envelope.model_validate_json``),
    which never checks ``EVENT_TYPES`` (only publishing does). Some of the
    triggers this module resolves have wire values with no catalogue entry
    at all (the module docstring's documented gaps: ``pack.released``,
    ``case.gap_recorded``, ...) — going through ``build_envelope`` here
    would reject exactly the cases this layer must still handle.
    """
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id="FCRM-2026-0151",
        run_id="run_1",
        occurred_at=utcnow(),
        payload=payload,
    )


class TestResolveSimple:
    def test_agent_failed_always_resolves_to_after_retry(self) -> None:
        """D-15: every ``agent.failed`` this consumer sees is terminal by
        construction (H2 owns the whole attempt loop and publishes exactly
        one terminal event per job — see module docstring), so the mapping
        is unconditional on the wire type alone, not on any payload field.
        """
        env = _env(
            "agent.failed",
            {
                "job_id": "job_1",
                "case_id": "FCRM-2026-0151",
                "run_id": "run_1",
                "agent": "research",
                "attempt": 2,
                "reason": "budget",
            },
        )
        resolution = resolve_simple(env)
        assert resolution is not None
        assert resolution.trigger is Trigger.AGENT_FAILED_AFTER_RETRY

    def test_agent_failed_with_a_malformed_payload_is_poison(self) -> None:
        env = _env("agent.failed", {"not": "a real AgentFailed payload"})
        with pytest.raises(ValidationError):
            resolve_simple(env)

    @pytest.mark.parametrize(
        ("event_type", "trigger"),
        [
            ("case.document_stored", Trigger.CASE_DOCUMENT_STORED),
            ("case.submitted", Trigger.CASE_SUBMITTED),
            ("case.answered", Trigger.CASE_ANSWERED),
            ("case.retagged", Trigger.CASE_RETAGGED),
            ("case.rerun_requested", Trigger.CASE_RERUN_REQUESTED),
            ("case.gap_recorded", Trigger.CASE_GAP_RECORDED),
            ("case.locked", Trigger.CASE_LOCKED),
            ("case.unlocked", Trigger.CASE_UNLOCKED),
            ("case.close_requested", Trigger.CASE_CLOSE_REQUESTED),
            ("vote.cast", Trigger.VOTE_CAST),
            ("pack.released", Trigger.PACK_RELEASED),
            ("vote.no_majority", Trigger.VOTE_NO_MAJORITY),
        ],
    )
    def test_literal_wire_mappings(self, event_type: str, trigger: Trigger) -> None:
        env = _env(event_type, {})
        resolution = resolve_simple(env)
        assert resolution is not None
        assert resolution.trigger is trigger

    def test_unmapped_event_type_resolves_to_none(self) -> None:
        env = _env("index.built", {})
        assert resolve_simple(env) is None

    def test_literal_mapping_carries_payload_fields_into_context(self) -> None:
        env = _env(
            "case.close_requested", {"band": 1, "acks_done": True, "reason": "x"}
        )
        resolution = resolve_simple(env)
        assert resolution is not None
        assert resolution.context.band == 1
        assert resolution.context.acks_done is True


class TestAgentDone:
    def test_agent_done_agent_parses_the_agent_field(self) -> None:
        env = _env("agent.done", {"agent": "critic"})
        assert agent_done_agent(env) is AgentName.CRITIC

    def test_agent_done_agent_rejects_an_unknown_agent(self) -> None:
        with pytest.raises(ValueError, match="not-a-real-agent"):
            agent_done_agent(_env("agent.done", {"agent": "not-a-real-agent"}))

    def test_agent_done_agent_tolerates_extra_keys_a_future_producer_might_add(
        self,
    ) -> None:
        """Deliberately not a strict ``AgentDone`` parse (see
        :func:`fcrm_caseflow.resolve.agent_done_agent`'s docstring): a
        ``band``/``boundary_flagged`` key alongside the real payload must
        not break trigger resolution.
        """
        env = _env(
            "agent.done",
            {
                "job_id": "job_1",
                "case_id": "FCRM-2026-0151",
                "run_id": "run_1",
                "agent": "assessment",
                "output": {},
                "usage": {},
                "model": "m",
                "attempt": 1,
                "band": 2,
                "boundary_flagged": True,
            },
        )
        assert agent_done_agent(env) is AgentName.ASSESSMENT

    @pytest.mark.parametrize(
        ("state", "agent", "trigger"),
        [
            (CaseState.INTAKE, AgentName.INTAKE, Trigger.AGENT_DONE_INTAKE),
            (CaseState.SCREENING, AgentName.SCREENING, Trigger.AGENT_DONE_SCREENING),
            (CaseState.CRITIC, AgentName.CRITIC, Trigger.AGENT_DONE_CRITIC),
            (
                CaseState.COMPLETENESS,
                AgentName.NARRATIVE,
                Trigger.AGENT_DONE_NARRATIVE_COMPLETENESS,
            ),
            (
                CaseState.RECOMMEND,
                AgentName.NARRATIVE,
                Trigger.AGENT_DONE_NARRATIVE_RECOMMENDATION,
            ),
        ],
    )
    def test_single_agent_mapping(
        self, state: CaseState, agent: AgentName, trigger: Trigger
    ) -> None:
        assert agent_done_single(state, agent) is trigger

    def test_single_agent_mapping_rejects_the_wrong_agent_for_the_state(self) -> None:
        assert agent_done_single(CaseState.SCREENING, AgentName.CRITIC) is None

    def test_pair_members_for_assess(self) -> None:
        members, trigger = agent_done_pair_members(CaseState.ASSESS)  # type: ignore[misc]
        assert members == frozenset({AgentName.RESEARCH, AgentName.ASSESSMENT})
        assert trigger is Trigger.AGENT_DONE_ASSESS_BOTH

    def test_pair_members_for_clarification(self) -> None:
        members, trigger = agent_done_pair_members(CaseState.CLARIFICATION)  # type: ignore[misc]
        assert members == frozenset({AgentName.RESEARCH, AgentName.ASSESSMENT})
        assert trigger is Trigger.AGENT_DONE_RERUN_BOTH

    def test_pair_members_none_outside_a_pair_state(self) -> None:
        assert agent_done_pair_members(CaseState.REVIEW) is None


class TestGateDecidedTrigger:
    def test_fast_lane_confirm(self) -> None:
        assert (
            gate_decided_trigger(GateType.FAST_LANE_CONFIRM, "confirm")
            is Trigger.GATE_DECIDED_FAST_LANE_CONFIRM
        )

    def test_review_item(self) -> None:
        assert (
            gate_decided_trigger(GateType.REVIEW_ITEM, "override")
            is Trigger.GATE_DECIDED_REVIEW_ITEM
        )

    def test_handoff(self) -> None:
        assert (
            gate_decided_trigger(GateType.HANDOFF, "complete")
            is Trigger.GATE_DECIDED_HANDOFF
        )

    def test_completeness_ask(self) -> None:
        assert (
            gate_decided_trigger(GateType.COMPLETENESS, "ask")
            is Trigger.GATE_DECIDED_COMPLETENESS_ASK
        )

    def test_completeness_proceed(self) -> None:
        assert (
            gate_decided_trigger(GateType.COMPLETENESS, "proceed")
            is Trigger.GATE_DECIDED_COMPLETENESS_PROCEED
        )

    def test_completeness_with_an_unrecognised_decision_raises(self) -> None:
        with pytest.raises(ValueError, match="unrecognised decision"):
            gate_decided_trigger(GateType.COMPLETENESS, "maybe")

    @pytest.mark.parametrize(
        "gate_type",
        [
            GateType.PO_CONFIRM_VALUE,
            GateType.PO_REUSE,
            GateType.PO_ANSWER_GAPS,
            GateType.ACK,
            GateType.VOTE,
            GateType.LOCK,
            GateType.PACK_RELEASE,
            GateType.UNLOCK,
            GateType.REFLAG_ACK,
            GateType.PO_CONFIRM_CATEGORY,
        ],
    )
    def test_informational_only_gate_types_resolve_to_none(
        self, gate_type: GateType
    ) -> None:
        assert gate_decided_trigger(gate_type, "anything") is None


class TestContextFromPayload:
    def test_picks_known_fields_only(self) -> None:
        ctx = context_from_payload(
            {"band": 3, "boundary_flagged": True, "unrelated_key": "x"}
        )
        assert ctx.band == 3
        assert ctx.boundary_flagged is True

    def test_flags_is_never_sourced_from_payload(self) -> None:
        ctx = context_from_payload({"flags": {"fast_lane": True}})
        # "flags" is excluded on purpose (module docstring); the dataclass
        # default (a fresh RunFlags()) wins, never the raw dict a payload
        # might (incorrectly) carry under that name.
        assert ctx.flags.fast_lane is False

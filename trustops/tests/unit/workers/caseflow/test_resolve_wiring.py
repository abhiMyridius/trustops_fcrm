"""The wire events ADR-28 added to :mod:`fcrm_caseflow.resolve`: bare
``committee.answered`` by ``numbers_changed``, bare ``outcome.set`` by
``mode``, ``vote.tallied`` with either outcome key, and the new
``ingest.done`` / ``ingest.failed`` / ``case.control_planned`` triggers.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.resolve import resolve_simple
from fcrm_caseflow.transitions import apply
from fcrm_caseflow.triggers import Trigger
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import CaseState
from fcrm_contracts.events.envelope import Envelope


def _env(event_type: str, payload: dict[str, Any]) -> Envelope:
    """Built directly, not through ``build_envelope``: a consumer only ever
    sees ``decode_envelope``, which never checks the catalogue.
    """
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id="FCRM-2026-0151",
        run_id="run_1",
        occurred_at=utcnow(),
        payload=payload,
    )


class TestCommitteeAnswered:
    def test_changed_numbers_send_the_case_back_to_review_with_the_reason(
        self,
    ) -> None:
        resolution = resolve_simple(
            _env(
                "committee.answered",
                {"numbers_changed": True, "unlock_reason": "rating corrected"},
            )
        )
        assert resolution is not None
        assert resolution.trigger is Trigger.COMMITTEE_ANSWERED_CHANGED
        assert resolution.context.committee_numbers_changed is True
        assert resolution.context.unlock_reason == "rating corrected"
        assert (
            apply(CaseState.DECIDE, resolution.trigger, resolution.context).to_state
            is CaseState.REVIEW
        )

    @pytest.mark.parametrize("payload", [{"numbers_changed": False}, {}])
    def test_unchanged_or_unstated_numbers_only_add_an_addendum(
        self, payload: dict[str, Any]
    ) -> None:
        resolution = resolve_simple(_env("committee.answered", payload))
        assert resolution is not None
        assert resolution.trigger is Trigger.COMMITTEE_ANSWERED_NO_CHANGE
        assert resolution.context.committee_numbers_changed is False
        assert (
            apply(CaseState.DECIDE, resolution.trigger, resolution.context).to_state
            is CaseState.DECIDE
        )

    def test_the_mirror_key_is_accepted_when_numbers_changed_is_absent(self) -> None:
        resolution = resolve_simple(
            _env("committee.answered", {"committee_numbers_changed": True})
        )
        assert resolution is not None
        assert resolution.trigger is Trigger.COMMITTEE_ANSWERED_CHANGED

    def test_numbers_changed_wins_over_a_stale_mirror(self) -> None:
        resolution = resolve_simple(
            _env(
                "committee.answered",
                {"numbers_changed": False, "committee_numbers_changed": True},
            )
        )
        assert resolution is not None
        assert resolution.trigger is Trigger.COMMITTEE_ANSWERED_NO_CHANGE
        assert resolution.context.committee_numbers_changed is False

    @pytest.mark.parametrize("bad", ["false", "no", 0, 1])
    def test_a_flag_that_is_not_a_boolean_is_refused_not_coerced(
        self, bad: Any
    ) -> None:
        """``bool("false")`` is ``True``: coercing would unlock a locked case."""
        with pytest.raises(ValueError, match="numbers_changed must be a boolean"):
            resolve_simple(_env("committee.answered", {"numbers_changed": bad}))

    def test_the_older_dotted_triggers_still_resolve_literally(self) -> None:
        resolution = resolve_simple(
            _env("committee.answered.no_change", {"committee_numbers_changed": False})
        )
        assert resolution is not None
        assert resolution.trigger is Trigger.COMMITTEE_ANSWERED_NO_CHANGE


class TestOutcomeSet:
    def test_a_delegated_outcome_closes_the_run(self) -> None:
        resolution = resolve_simple(
            _env("outcome.set", {"mode": "delegated", "outcome": "approve"})
        )
        assert resolution is not None
        assert resolution.trigger is Trigger.OUTCOME_SET_DELEGATED
        assert (
            apply(CaseState.DECIDE, resolution.trigger, resolution.context).to_state
            is CaseState.CLOSED
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {"mode": "committee", "outcome": "approve_with_conditions"},
            {"outcome": "approve"},
            {"mode": None},
        ],
    )
    def test_any_other_mode_is_informational(self, payload: dict[str, Any]) -> None:
        """A committee outcome already moved the run on ``vote.tallied``."""
        assert resolve_simple(_env("outcome.set", payload)) is None

    def test_the_older_dotted_trigger_still_resolves_literally(self) -> None:
        resolution = resolve_simple(_env("outcome.set.delegated", {}))
        assert resolution is not None
        assert resolution.trigger is Trigger.OUTCOME_SET_DELEGATED


class TestVoteTallied:
    def test_vote_outcome_is_the_guard_key(self) -> None:
        resolution = resolve_simple(
            _env("vote.tallied", {"vote_outcome": "approve_with_conditions"})
        )
        assert resolution is not None
        assert resolution.trigger is Trigger.VOTE_TALLIED
        assert resolution.context.vote_outcome == "approve_with_conditions"

    def test_the_designs_outcome_key_is_accepted_too(self) -> None:
        resolution = resolve_simple(_env("vote.tallied", {"outcome": "defer"}))
        assert resolution is not None
        assert resolution.context.vote_outcome == "defer"

    def test_vote_outcome_wins_when_both_are_present(self) -> None:
        resolution = resolve_simple(
            _env("vote.tallied", {"outcome": "defer", "vote_outcome": "approve"})
        )
        assert resolution is not None
        assert resolution.context.vote_outcome == "approve"

    def test_the_trigger_is_literal_and_moves_decide_to_closed(self) -> None:
        resolution = resolve_simple(_env("vote.tallied", {"vote_outcome": "reject"}))
        assert resolution is not None
        assert (
            apply(CaseState.DECIDE, resolution.trigger, resolution.context).to_state
            is CaseState.CLOSED
        )


class TestNewLiteralTriggers:
    @pytest.mark.parametrize(
        ("event_type", "trigger"),
        [
            ("ingest.done", Trigger.INGEST_DONE),
            ("ingest.failed", Trigger.INGEST_FAILED),
            ("case.control_planned", Trigger.CASE_CONTROL_PLANNED),
        ],
    )
    def test_each_maps_to_its_trigger(self, event_type: str, trigger: Trigger) -> None:
        resolution = resolve_simple(_env(event_type, {}))
        assert resolution is not None
        assert resolution.trigger is trigger

    def test_the_trigger_values_are_the_wire_types(self) -> None:
        assert Trigger.INGEST_DONE.value == "ingest.done"
        assert Trigger.INGEST_FAILED.value == "ingest.failed"
        assert Trigger.CASE_CONTROL_PLANNED.value == "case.control_planned"

    @pytest.mark.parametrize("state", [CaseState.INTAKE, CaseState.CLARIFICATION])
    @pytest.mark.parametrize("trigger", [Trigger.INGEST_DONE, Trigger.INGEST_FAILED])
    def test_ingest_results_are_self_loops_in_intake_and_clarification(
        self, state: CaseState, trigger: Trigger
    ) -> None:
        resolution = resolve_simple(_env(trigger.value, {}))
        assert resolution is not None
        assert apply(state, trigger, resolution.context).to_state is state

    def test_a_control_planned_in_review_is_a_self_loop(self) -> None:
        resolution = resolve_simple(_env("case.control_planned", {}))
        assert resolution is not None
        result = apply(CaseState.REVIEW, resolution.trigger, resolution.context)
        assert result.to_state is CaseState.REVIEW

    @pytest.mark.parametrize(
        ("state", "trigger"),
        [
            (CaseState.TRIAGE, Trigger.INGEST_DONE),
            (CaseState.REVIEW, Trigger.INGEST_FAILED),
            (CaseState.INTAKE, Trigger.CASE_CONTROL_PLANNED),
            (CaseState.DECIDE, Trigger.CASE_CONTROL_PLANNED),
        ],
    )
    def test_they_are_illegal_anywhere_else(
        self, state: CaseState, trigger: Trigger
    ) -> None:
        resolution = resolve_simple(_env(trigger.value, {}))
        assert resolution is not None
        with pytest.raises(IllegalTransition):
            apply(state, trigger, resolution.context)

    def test_new_triggers_are_not_internal_so_they_never_self_fire(self) -> None:
        for trigger in (
            Trigger.INGEST_DONE,
            Trigger.INGEST_FAILED,
            Trigger.CASE_CONTROL_PLANNED,
        ):
            assert not trigger.value.startswith("internal.")

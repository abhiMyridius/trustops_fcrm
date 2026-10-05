"""Tests for the pure Caseflow state machine: a full replay of the demo
case's lifecycle, illegal-transition handling, and the routing helpers.
"""

from __future__ import annotations

import pytest
from fcrm_caseflow.eventstore import RecordedTransition, rebuild
from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.routing import (
    is_fast_lane_candidate,
    pick_auto_triage_analyst,
    route_for_band,
    tally_votes,
)
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.transitions import TransitionContext, apply, open_run
from fcrm_caseflow.triggers import Trigger
from fcrm_contracts.enums import CaseState, Route

S = CaseState
T = Trigger


# The demo case's trigger sequence (FCRM-2026-0151), matching the scenes in
# docs/requirements/research/06-walkthrough-seed-extraction.md: no fast
# lane, one clarification round, an override that moves the band from 2 to
# 3 (boundary flag stays on), committee question answered with no number
# change, then a 2-1 vote.
_DEMO_SEQUENCE: list[tuple[Trigger, TransitionContext]] = [
    (T.CASE_DOCUMENT_STORED, TransitionContext()),
    (T.AGENT_DONE_INTAKE, TransitionContext()),
    (T.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL, TransitionContext(gaps_open=False)),
    (T.CASE_SUBMITTED, TransitionContext(four_submit_checks_passed=True)),
    (T.TRIAGE_DISPATCH, TransitionContext()),
    (T.SCREENING_DISPATCH, TransitionContext()),
    (T.AGENT_DONE_SCREENING, TransitionContext(fast_lane_proposed=False)),
    (T.ASSESS_DISPATCH, TransitionContext()),
    (T.AGENT_DONE_ASSESS_BOTH, TransitionContext(band=2, boundary_flagged=True)),
    (T.CRITIC_DISPATCH, TransitionContext()),
    (T.AGENT_DONE_CRITIC, TransitionContext()),
    (T.COMPLETENESS_DISPATCH, TransitionContext()),
    (T.AGENT_DONE_NARRATIVE_COMPLETENESS, TransitionContext()),
    (T.GATE_DECIDED_COMPLETENESS_ASK, TransitionContext()),
    (T.CASE_ANSWERED, TransitionContext()),
    (T.AGENT_DONE_RERUN_BOTH, TransitionContext()),
    # The vendor due-diligence override: band changes 2 -> 3, boundary
    # flag stays on, so the ack gate opens and is then decided.
    (
        T.GATE_DECIDED_REVIEW_ITEM,
        TransitionContext(
            rating_or_control_changed=True, band_changed=True, boundary_flagged=True
        ),
    ),
    (T.ALL_ITEMS_DECIDED, TransitionContext(all_items_decided=True, acks_done=True)),
    (T.AGENT_DONE_NARRATIVE_RECOMMENDATION, TransitionContext()),
    (T.CASE_LOCKED, TransitionContext()),
    (T.PACK_RELEASED, TransitionContext(release_checks_passed=True)),
    (T.COMMITTEE_QUESTION_ASKED, TransitionContext()),
    (
        T.COMMITTEE_ANSWERED_NO_CHANGE,
        TransitionContext(committee_numbers_changed=False),
    ),
    (T.VOTE_CAST, TransitionContext()),
    (T.VOTE_CAST, TransitionContext()),
    (T.VOTE_CAST, TransitionContext()),
    (T.VOTE_TALLIED, TransitionContext(vote_outcome="approve_with_conditions")),
    (T.CLOSED_DISPATCH, TransitionContext()),
]


def test_open_run_starts_in_intake() -> None:
    assert open_run() == S.INTAKE


def test_the_demo_case_replays_from_intake_to_closed() -> None:
    """Runs the exact sequence a real FCRM-2026-0151 run produces, through
    the live `apply` reducer, and checks it lands in CLOSED having passed
    through DECIDE (committee route) rather than closing by delegation.
    """
    state = open_run()
    seen_decide = False
    for trigger, ctx in _DEMO_SEQUENCE:
        result = apply(state, trigger, ctx)
        if result.to_state == S.DECIDE or result.from_state == S.DECIDE:
            seen_decide = True
        state = result.to_state

    assert state == S.CLOSED
    assert seen_decide, "the demo case must route through DECIDE (Band 3, committee)"


def test_the_demo_sequence_replays_identically_through_eventstore_rebuild() -> None:
    """The same sequence, folded through eventstore.rebuild (the function
    the real runtime's replay test will call), must reach the same state —
    this is design A §3.5's "replay must equal the snapshot" requirement,
    exercised here with the demo's real sequence rather than a toy one.
    """
    events = [RecordedTransition(trigger=t, context=c) for t, c in _DEMO_SEQUENCE]
    final_state = rebuild(open_run(), events)
    assert final_state == S.CLOSED


def test_no_majority_reopens_a_vote_round_instead_of_closing() -> None:
    """ADR-08: a split vote must not close the case under any outcome —
    it stays in DECIDE for a re-vote.
    """
    result = apply(S.DECIDE, T.VOTE_NO_MAJORITY, TransitionContext())
    assert result.to_state == S.DECIDE


def test_delegated_close_requires_band_one_or_two() -> None:
    """R1 only fires for Band 1-2; a Band 3 case must not be closeable by
    a close_requested trigger (it must go through DECIDE's vote path
    instead, which IllegalTransition enforces here since no guard for
    case_close_requested passes at band 3).
    """
    with pytest.raises(IllegalTransition):
        apply(
            S.DECIDE, T.CASE_CLOSE_REQUESTED, TransitionContext(band=3, acks_done=True)
        )

    # Band 1 does pass.
    result = apply(
        S.DECIDE, T.CASE_CLOSE_REQUESTED, TransitionContext(band=1, acks_done=True)
    )
    assert result.to_state == S.DECIDE


def test_a_number_change_in_a_committee_answer_reopens_review() -> None:
    result = apply(
        S.DECIDE,
        T.COMMITTEE_ANSWERED_CHANGED,
        TransitionContext(committee_numbers_changed=True),
    )
    assert result.to_state == S.REVIEW


def test_unknown_trigger_for_a_state_raises_illegal_transition() -> None:
    with pytest.raises(IllegalTransition):
        apply(S.INTAKE, T.VOTE_CAST, TransitionContext())


def test_agent_failure_after_retry_hands_off_from_any_open_state() -> None:
    for state in (S.INTAKE, S.ASSESS, S.REVIEW, S.DECIDE):
        result = apply(state, T.AGENT_FAILED_AFTER_RETRY, TransitionContext())
        assert result.to_state == S.HANDOFF

    # A closed case cannot fail an agent step.
    with pytest.raises(IllegalTransition):
        apply(S.CLOSED, T.AGENT_FAILED_AFTER_RETRY, TransitionContext())


def test_a_second_agent_failure_while_in_handoff_stays_in_handoff() -> None:
    """A fan-out (one intake job per document) can fail twice: the second
    failure must not be illegal, or its message dead-letters and freezes the run.
    """
    result = apply(S.HANDOFF, T.AGENT_FAILED_AFTER_RETRY, TransitionContext())
    assert result.to_state == S.HANDOFF


class TestRouting:
    @pytest.mark.parametrize(
        ("band", "expected"),
        [
            (1, Route.ANALYST),
            (2, Route.ANALYST),
            (3, Route.COMMITTEE),
            (4, Route.COMMITTEE),
        ],
    )
    def test_route_for_band(self, band: int, expected: Route) -> None:
        assert route_for_band(band) == expected

    def test_the_demo_case_is_not_a_fast_lane_candidate(self) -> None:
        """Three change types (vendor_onboarding, new_geography, new_product)
        — never fast-laned."""
        assert (
            is_fast_lane_candidate(
                ["vendor_onboarding", "new_geography", "new_product"],
                48_000_000,
                new_geography=True,
                new_segment=False,
            )
            is False
        )

    def test_a_vendor_renewal_alone_is_a_fast_lane_candidate(self) -> None:
        """Matches the seed's one fast-lane case, FCRM-2026-0140 'KYC
        vendor renewal'."""
        assert (
            is_fast_lane_candidate(
                ["vendor_onboarding"], 1_000_000, new_geography=False, new_segment=False
            )
            is True
        )

    def test_pick_auto_triage_analyst_matches_the_seed_s_queue_counts(self) -> None:
        """Rahul Iyer has 4 open cases, the other two analysts have 5 each
        (docs/requirements/research/06-walkthrough-seed-extraction.md §5)."""
        assert (
            pick_auto_triage_analyst({"u-riyer": 4, "u-mshah": 5, "u-dbrooks": 5})
            == "u-riyer"
        )

    def test_pick_auto_triage_analyst_breaks_ties_alphabetically(self) -> None:
        assert pick_auto_triage_analyst({"u-zed": 2, "u-abe": 2}) == "u-abe"

    def test_tally_votes_matches_the_demo_s_2_1_outcome(self) -> None:
        outcome, no_majority = tally_votes(
            ["approve_with_conditions", "approve_with_conditions", "defer"]
        )
        assert outcome == "approve_with_conditions"
        assert no_majority is False

    def test_tally_votes_a_three_way_split_is_no_majority(self) -> None:
        outcome, no_majority = tally_votes(["approve", "defer", "reject"])
        assert outcome is None
        assert no_majority is True


def test_run_flags_default_to_a_fresh_run() -> None:
    flags = RunFlags()
    assert flags.fast_lane is False
    assert flags.round_open is False
    assert flags.vote_round == 0

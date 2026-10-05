"""The decision and closing step handlers one at a time, over fake peers: what
each writes, what it refuses, and the refusals S5 and the guards make. The whole
walkthrough through the engine is ``test_decision_scene.py``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import (
    DEMO_CONDITIONS,
    FakeClosingCaseService,
    FakeDecisionVotesService,
    RecordingAudit,
    coded_problem,
    make_run,
)
from fcrm_caseflow.clients.decision_votes_client import DecisionRefusedError
from fcrm_caseflow.cores import closing, decision
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.steps import StepDependencyError, StepDeps
from fcrm_common.errors import (
    ConflictError,
    GateRequiredError,
    RuleViolationError,
    ValidationError,
)
from fcrm_contracts.enums import CaseState, Outcome, StepKind
from fcrm_contracts.events.payloads import HarnessStep

CASE = "FCRM-2026-0151"
PACK = "pack_1"

VOTES = [
    {"member_id": "u-jokafor", "vote": "approve_with_conditions", "rationale": "yes"},
    {"member_id": "u-amendes", "vote": "approve_with_conditions", "rationale": "yes"},
    {"member_id": "u-lchen", "vote": "defer", "rationale": "wait"},
]


def step(kind: StepKind, payload: dict[str, Any]) -> HarnessStep:
    return HarnessStep(
        step_id=f"step_{kind.value}",
        step=kind,
        case_id=CASE,
        run_id="run_1",
        args={"payload": payload},
    )


def tallied(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "pack_id": PACK,
        "round": 1,
        "outcome": "approve_with_conditions",
        "vote_outcome": "approve_with_conditions",
        "counts": {"approve_with_conditions": 2, "defer": 1},
        "votes": VOTES,
    }
    return {**body, **overrides}


# ── vote.record ─────────────────────────────────────────────────────────────


async def test_a_vote_is_recorded_as_a_count_and_never_an_option(
    decision_deps_factory: Callable[..., StepDeps], recording_audit: RecordingAudit
) -> None:
    cast = {
        "pack_id": PACK,
        "round": 1,
        "member_id": "u-lchen",
        "cast_count": 3,
        "quorum": 3,
    }
    await decision.vote_record(
        step(StepKind.VOTE_RECORD, cast), decision_deps_factory()
    )
    [record] = recording_audit.records
    assert (record.type, record.summary, record.actor) == (
        "vote.cast",
        "Vote 3 of 3 cast",
        "u-lchen",
    )
    assert set(record.data) == {
        "run_id",
        "step_id",
        "pack_id",
        "round",
        "member_id",
        "cast_count",
        "quorum",
    }


@pytest.mark.parametrize("leaky_key", ["vote", "option", "choice", "rationale"])
async def test_a_vote_event_that_names_the_option_is_refused_before_anything_is_written(
    decision_deps_factory: Callable[..., StepDeps],
    recording_audit: RecordingAudit,
    leaky_key: str,
) -> None:
    cast = {
        "pack_id": PACK,
        "round": 1,
        "member_id": "u-lchen",
        "cast_count": 1,
        "quorum": 3,
        leaky_key: "defer",
    }
    with pytest.raises(ValidationError):
        await decision.vote_record(
            step(StepKind.VOTE_RECORD, cast), decision_deps_factory()
        )
    assert recording_audit.records == []


# ── decide.outcome ──────────────────────────────────────────────────────────


async def test_the_outcome_is_recorded_in_s5_then_s1_with_the_drafted_conditions(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    closing_case_service: FakeClosingCaseService,
) -> None:
    decision_votes_service.tallied = "approve_with_conditions"
    result = await decision.decide_outcome(
        step(StepKind.DECIDE_OUTCOME, tallied()), decision_deps_factory()
    )
    [recorded] = decision_votes_service.outcomes
    assert [c["id"] for c in recorded["conditions"]] == ["CND-1", "CND-2", "CND-3"]
    assert recorded["conditions"][0]["owner_id"] == "u-pnair"
    assert "owner_id" not in recorded["conditions"][1]  # a team has no user id
    [put] = closing_case_service.outcome_puts
    assert len({c["condition_id"] for c in put["conditions"]}) == 3
    assert put["decided_by"] == "committee"
    assert result.refs_patch == {
        "outcome": "approve_with_conditions",
        "outcome_mode": "committee",
        "outcome_pack_id": PACK,
        "conditions_activated": 3,
    }
    # S5 first, then S1 (the case closes only once S5 holds the decision).
    assert [c.path for c in decision_votes_service.calls][-1] == "/outcomes"
    paths = [c.path for c in closing_case_service.calls]
    assert paths[-1].endswith("/outcome")


@pytest.mark.parametrize(
    ("outcome", "votes", "conditions"),
    [
        ("approve", ["approve", "approve", "defer"], 0),
        ("defer", ["defer", "defer", "approve"], 0),
        ("reject", ["reject", "reject", "approve"], 0),
        (
            "approve_with_conditions",
            ["approve", "approve", "approve_with_conditions"],
            3,
        ),
    ],
)
async def test_only_an_approval_with_conditions_activates_conditions(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    closing_case_service: FakeClosingCaseService,
    outcome: str,
    votes: list[str],
    conditions: int,
) -> None:
    """The four outcomes, each from a ballot the real tally maps to it (ADR-36:
    ``approve, approve, approve_with_conditions`` is approve with conditions).
    """
    decision_votes_service.tallied = outcome
    body = tallied(
        outcome=outcome,
        vote_outcome=outcome,
        counts={},
        votes=[
            {"member_id": f"u-{i}", "vote": vote, "rationale": "reasons"}
            for i, vote in enumerate(votes)
        ],
    )
    result = await decision.decide_outcome(
        step(StepKind.DECIDE_OUTCOME, body), decision_deps_factory()
    )
    assert result.refs_patch["outcome"] == outcome
    assert len(closing_case_service.outcome_puts[0]["conditions"]) == conditions
    assert len(decision_votes_service.outcomes[0]["conditions"]) == conditions


async def test_votes_that_do_not_tally_to_the_reported_outcome_record_nothing(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    closing_case_service: FakeClosingCaseService,
    recording_audit: RecordingAudit,
) -> None:
    decision_votes_service.tallied = "defer"
    body = tallied(outcome="defer", vote_outcome="defer")  # the votes say otherwise
    with pytest.raises(ConflictError, match="do not tally"):
        await decision.decide_outcome(
            step(StepKind.DECIDE_OUTCOME, body), decision_deps_factory()
        )
    assert decision_votes_service.outcomes == []
    assert closing_case_service.outcome_puts == []
    assert recording_audit.records == []


@pytest.mark.parametrize("votes", [VOTES[:2], []])
async def test_an_incomplete_round_never_tallies(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    votes: list[dict[str, str]],
) -> None:
    with pytest.raises(ConflictError):
        await decision.decide_outcome(
            step(StepKind.DECIDE_OUTCOME, tallied(votes=votes)),
            decision_deps_factory(),
        )
    assert decision_votes_service.outcomes == []


async def test_s5_refusing_the_outcome_fails_the_step_with_its_code(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    closing_case_service: FakeClosingCaseService,
) -> None:
    decision_votes_service.tallied = None  # S5 holds no decisive tally for the pack
    with pytest.raises(DecisionRefusedError) as refused:
        await decision.decide_outcome(
            step(StepKind.DECIDE_OUTCOME, tallied()), decision_deps_factory()
        )
    assert refused.value.problem_code == "not_tallied"
    assert closing_case_service.outcome_puts == []  # S1 is never asked to close


async def test_a_step_without_the_s5_client_fails_loudly(
    decision_deps_factory: Callable[..., StepDeps],
) -> None:
    deps = decision_deps_factory(decision_votes=None)
    with pytest.raises(StepDependencyError):
        await decision.decide_outcome(step(StepKind.DECIDE_OUTCOME, tallied()), deps)


def test_the_po_summary_drops_a_business_summary_that_names_a_restricted_term() -> None:
    plain = decision.committee_plain_summary(
        Outcome.APPROVE_WITH_CONDITIONS,
        "The service can launch once the review is on file.",
        3,
    )
    assert plain.startswith("The committee approved this change with conditions.")
    assert "3 conditions now apply" in plain
    assert plain.endswith("the review is on file.")
    guarded = decision.committee_plain_summary(
        Outcome.APPROVE, "Composite score 10.4 puts it in Band 3.", 0
    )
    assert guarded == "The committee approved this change."


# ── decide.no_majority ──────────────────────────────────────────────────────


async def test_a_split_confirms_the_revote_and_decides_nothing(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    recording_audit: RecordingAudit,
    closing_case_service: FakeClosingCaseService,
) -> None:
    body = {
        "pack_id": PACK,
        "round": 1,
        "counts": {"approve": 1, "defer": 1, "reject": 1},
    }
    result = await decision.decide_no_majority(
        step(StepKind.DECIDE_NO_MAJORITY, body), decision_deps_factory()
    )
    assert result.refs_patch == {}
    assert [r.type for r in recording_audit.records] == [
        "vote.no_majority",
        "vote.rerequested",
    ]
    assert decision_votes_service.outcomes == []
    assert closing_case_service.state_patches == []


async def test_a_revote_s5_does_not_expect_is_refused(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
) -> None:
    decision_votes_service.fail(
        "POST", "/revote", coded_problem(409, "no_revote_pending")
    )
    body = {"pack_id": PACK, "round": 1, "counts": {}}
    with pytest.raises(DecisionRefusedError) as refused:
        await decision.decide_no_majority(
            step(StepKind.DECIDE_NO_MAJORITY, body), decision_deps_factory()
        )
    assert refused.value.problem_code == "no_revote_pending"


# ── decide.delegated: Guard 5 ───────────────────────────────────────────────


def close_request(**overrides: Any) -> dict[str, Any]:
    return {
        "case_id": CASE,
        "actor": "u-riyer",
        "outcome": "approve",
        "reason": "Band 2: closed under delegated authority.",
        "plain_summary": "Your change is approved.",
        "band": 2,
        **overrides,
    }


def locked_run(band: int = 2, *, locked: bool = True) -> Any:
    run = make_run(state=CaseState.RECOMMEND)
    snapshot = run.snapshot.model_copy(update={"flags": RunFlags(locked=locked)})
    return dataclasses.replace(run, snapshot=snapshot, band=band)


async def test_a_delegated_close_records_the_analyst_and_their_reason(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    recording_audit: RecordingAudit,
) -> None:
    result = await decision.decide_delegated(
        step(StepKind.DECIDE_DELEGATED, close_request()),
        decision_deps_factory(locked_run()),
    )
    [recorded] = decision_votes_service.outcomes
    assert recorded["mode"] == "delegated"
    assert recorded["decided_by"] == "u-riyer"
    assert recorded["reason"] == "Band 2: closed under delegated authority."
    [record] = recording_audit.records
    assert (record.type, record.actor) == ("outcome.set", "u-riyer")
    assert result.refs_patch["outcome_mode"] == "delegated"


async def test_a_delegated_approval_with_conditions_takes_them_from_the_latest_pack(
    decision_deps_factory: Callable[..., StepDeps],
    closing_case_service: FakeClosingCaseService,
) -> None:
    await decision.decide_delegated(
        step(
            StepKind.DECIDE_DELEGATED, close_request(outcome="approve_with_conditions")
        ),
        decision_deps_factory(locked_run()),
    )
    assert len(closing_case_service.outcome_puts[0]["conditions"]) == len(
        DEMO_CONDITIONS
    )


@pytest.mark.parametrize("band", [3, 4])
async def test_guard_five_refuses_a_delegated_close_for_a_committee_band(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    band: int,
) -> None:
    with pytest.raises(RuleViolationError):
        await decision.decide_delegated(
            step(StepKind.DECIDE_DELEGATED, close_request(band=band)),
            decision_deps_factory(locked_run(band)),
        )
    assert decision_votes_service.outcomes == []


async def test_guard_five_refuses_a_delegated_close_of_an_unlocked_case(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
) -> None:
    with pytest.raises(GateRequiredError):
        await decision.decide_delegated(
            step(StepKind.DECIDE_DELEGATED, close_request()),
            decision_deps_factory(locked_run(locked=False)),
        )
    assert decision_votes_service.outcomes == []


@pytest.mark.parametrize(
    "overrides",
    [{"reason": "short"}, {"plain_summary": "  "}],
    ids=["short-reason", "no-summary"],
)
async def test_a_delegated_close_without_a_reason_or_a_summary_is_refused(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        await decision.decide_delegated(
            step(StepKind.DECIDE_DELEGATED, close_request(**overrides)),
            decision_deps_factory(locked_run()),
        )
    assert decision_votes_service.outcomes == []


# ── closed.finalize ─────────────────────────────────────────────────────────


def finalize_step() -> HarnessStep:
    return step(StepKind.CLOSED_FINALIZE, {})


async def test_the_closing_step_reads_the_outcome_from_s5_when_the_run_has_none(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    recording_audit: RecordingAudit,
) -> None:
    decision_votes_service.case_outcome = {
        "case_id": CASE,
        "outcome": "defer",
        "label": "Deferred",
        "mode": "committee",
        "round": 1,
        "conditions": [],
        "recorded": True,
    }
    result = await closing.closed_finalize(
        finalize_step(), decision_deps_factory(make_run(state=CaseState.CLOSED))
    )
    assert [r.type for r in recording_audit.records] == [
        "outcome.delivered",
        "case.closed",
        "chain.verified",
    ]
    [event] = result.ui_events
    assert event.payload["outcome_label"] == "Deferred"
    assert result.refs_patch["precedent_indexed"] is False  # the S3 route is a seam


async def test_a_case_that_closed_with_no_recorded_outcome_is_not_finalised(
    decision_deps_factory: Callable[..., StepDeps], recording_audit: RecordingAudit
) -> None:
    with pytest.raises(ConflictError, match="no recorded outcome"):
        await closing.closed_finalize(
            finalize_step(), decision_deps_factory(make_run(state=CaseState.CLOSED))
        )
    assert recording_audit.records == []


async def test_the_po_announcement_carries_a_label_and_a_count_and_nothing_else(
    decision_deps_factory: Callable[..., StepDeps],
) -> None:
    run = make_run(
        state=CaseState.CLOSED,
        refs={
            "outcome": "approve_with_conditions",
            "outcome_mode": "committee",
            "conditions_activated": 3,
        },
    )
    result = await closing.closed_finalize(finalize_step(), decision_deps_factory(run))
    [event] = result.ui_events
    assert event.event_type == "outcome.delivered"
    assert event.payload == {
        "outcome": "approve_with_conditions",
        "outcome_label": "Approved with conditions",
        "conditions": 3,
    }


async def test_the_due_dates_count_from_the_decision_date_s5_holds_not_from_today(
    decision_deps_factory: Callable[..., StepDeps],
    decision_votes_service: FakeDecisionVotesService,
    closing_case_service: FakeClosingCaseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step retried after midnight must send S1 the same body, or S1 would
    refuse it as a different outcome for a decided case.
    """
    decision_votes_service.tallied = "approve_with_conditions"
    step_ = step(StepKind.DECIDE_OUTCOME, tallied())
    first_day = datetime(2026, 10, 5, 23, 59, tzinfo=UTC)
    monkeypatch.setattr(decision, "utcnow", lambda: first_day)
    await decision.decide_outcome(step_, decision_deps_factory())
    monkeypatch.setattr(decision, "utcnow", lambda: first_day + timedelta(days=3))
    await decision.decide_outcome(step_, decision_deps_factory())  # replays, no 409
    [put] = closing_case_service.outcome_puts
    assert put["conditions"][2]["due_date"] == "2027-04-05"
    assert len(decision_votes_service.outcomes) == 1

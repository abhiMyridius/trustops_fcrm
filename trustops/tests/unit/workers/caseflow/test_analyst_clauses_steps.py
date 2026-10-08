"""H1's side of a clause the analyst adds from a corpus search (ADR-74):
``clause.record`` writes ``clause.added`` / ``clause.removed`` with the analyst as
the actor and the reason in the record; S1's events start it without moving the
run (review or recommend); the pack quotes the clause with who added it and why;
and an unlock never gives the analyst's clause a gate.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

import pytest
from conftest import (
    FakeCollection,
    FakeScoringService,
    FakeSession,
    RecordingAudit,
    harness_step,
    make_run,
)
from fcrm_caseflow.audit_map import RECORDS_FOR, STEP_FOR
from fcrm_caseflow.cores import analyst_clauses, assessment_pack, default_handlers
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.steps import StepDeps, StepResult
from fcrm_caseflow.store import CaseRun, CaseRunStore
from fcrm_caseflow.transitions import TransitionContext
from fcrm_caseflow.triggers import Trigger
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import ReviewItemInternal
from fcrm_contracts.enums import CaseState, ItemKind, ItemStatus, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep

CASE = "FCRM-2026-0151"
RUN = "run_1"
REASON = "The payout partner runs an agent network the research did not cover."
REMOVE_REASON = "Added by mistake: the agent network is out of this case's scope."
TEXT = "A bank that relies on a third party to pay out funds should know its agents."

ADDED = {
    "case_id": CASE,
    "actor": "u-riyer",
    "item_id": "acl_1",
    "clause_id": "FFIEC-TPR-12",
    "corpus_v": 14,
    "reason": REASON,
    "title": "Third-party payout agents",
    "ref": "FFIEC BSA/AML Manual, p. 12",
}
REMOVED = {
    "case_id": CASE,
    "actor": "u-riyer",
    "item_id": "acl_1",
    "clause_id": "FFIEC-TPR-12",
    "reason": REMOVE_REASON,
}


def _record(audit: RecordingAudit, record_type: str) -> Any:
    [record] = [r for r in audit.records if r.type == record_type]
    return record


# ── the step ────────────────────────────────────────────────────────────────────


def test_both_events_start_the_clause_step_and_it_documents_both_records() -> None:
    assert STEP_FOR[Trigger.CASE_CLAUSE_ADDED] is StepKind.CLAUSE_RECORD
    assert STEP_FOR[Trigger.CASE_CLAUSE_REMOVED] is StepKind.CLAUSE_RECORD
    assert RECORDS_FOR[StepKind.CLAUSE_RECORD] == ("clause.added", "clause.removed")
    assert default_handlers()[StepKind.CLAUSE_RECORD] is analyst_clauses.clause_record


async def test_an_added_clause_is_recorded_with_the_analyst_and_the_reason(
    step_deps_factory: Callable[..., StepDeps],
    recording_audit: RecordingAudit,
    scoring_service: FakeScoringService,
) -> None:
    deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.REVIEW))

    result = await analyst_clauses.clause_record(
        harness_step(
            StepKind.CLAUSE_RECORD, ADDED, trigger=Trigger.CASE_CLAUSE_ADDED.value
        ),
        deps,
    )

    assert result == StepResult()  # no gate, no job, no state
    record = _record(recording_audit, "clause.added")
    assert record.actor == "u-riyer"
    assert record.data["reason"] == REASON
    assert record.data["clause_id"] == "FFIEC-TPR-12"
    assert record.data["corpus_v"] == 14
    assert "quoted verbatim" in record.summary
    assert "not scored" in record.summary
    assert scoring_service.requests == []  # a clause is never a score input


async def test_a_removed_clause_is_recorded_with_its_reason(
    step_deps_factory: Callable[..., StepDeps], recording_audit: RecordingAudit
) -> None:
    deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.REVIEW))

    await analyst_clauses.clause_record(
        harness_step(
            StepKind.CLAUSE_RECORD, REMOVED, trigger=Trigger.CASE_CLAUSE_REMOVED.value
        ),
        deps,
    )

    record = _record(recording_audit, "clause.removed")
    assert (record.actor, record.data["reason"]) == ("u-riyer", REMOVE_REASON)
    assert [r.type for r in recording_audit.records] == ["clause.removed"]


async def test_a_retried_step_chains_one_record(
    step_deps_factory: Callable[..., StepDeps], recording_audit: RecordingAudit
) -> None:
    deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.REVIEW))
    step = harness_step(
        StepKind.CLAUSE_RECORD, ADDED, trigger=Trigger.CASE_CLAUSE_ADDED.value
    )

    first = await analyst_clauses.clause_record(step, deps)
    second = await analyst_clauses.clause_record(step, deps)

    assert first == second
    assert len(set(recording_audit.keys)) == 1  # S4 returns the record it chained


# ── the engine: step-only, the run does not move ────────────────────────────────


def _env(event_type: str, payload: dict[str, Any], run_id: str) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id=CASE,
        run_id=run_id,
        occurred_at=utcnow(),
        payload=payload,
    )


async def _run_in(
    engine: CaseflowEngine,
    run_store: CaseRunStore,
    session: FakeSession,
    state: CaseState,
    flags: Optional[RunFlags] = None,
) -> CaseRun:
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
    for _ in range(2):
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
    return run


def _clause_steps(outbox_collection: FakeCollection) -> list[HarnessStep]:
    envelopes = [
        Envelope.model_validate_json(bytes(doc["body"]))
        for doc in outbox_collection.docs
    ]
    return [
        step
        for step in (
            HarnessStep.model_validate(e.payload)
            for e in envelopes
            if e.type == "harness.step"
        )
        if step.step is StepKind.CLAUSE_RECORD
    ]


@pytest.mark.parametrize(
    ("state", "event_type", "payload"),
    [
        (CaseState.REVIEW, "case.clause_added", ADDED),
        (CaseState.RECOMMEND, "case.clause_added", ADDED),
        (CaseState.REVIEW, "case.clause_removed", REMOVED),
        (CaseState.RECOMMEND, "case.clause_removed", REMOVED),
    ],
)
async def test_the_event_starts_the_step_and_leaves_the_run_where_it_is(
    engine_factory: Callable[..., CaseflowEngine],
    run_store: CaseRunStore,
    session: FakeSession,
    outbox_collection: FakeCollection,
    state: CaseState,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    engine = engine_factory(step_map=STEP_FOR)
    run = await _run_in(engine, run_store, session, state)

    await engine.handle(_env(event_type, payload, run.snapshot.run_id), session)

    after = await run_store.get(run.snapshot.run_id)
    assert after is not None
    assert after.state is state
    assert after.snapshot.version == run.snapshot.version  # no transition
    [step] = _clause_steps(outbox_collection)
    assert step.args["trigger"] == event_type
    assert step.args["payload"]["reason"] == payload["reason"]


async def test_the_same_event_twice_is_one_step(
    engine_factory: Callable[..., CaseflowEngine],
    run_store: CaseRunStore,
    session: FakeSession,
    outbox_collection: FakeCollection,
) -> None:
    engine = engine_factory(step_map=STEP_FOR)
    run = await _run_in(engine, run_store, session, CaseState.REVIEW)
    event = _env("case.clause_added", ADDED, run.snapshot.run_id)

    await engine.handle(event, session)
    await engine.handle(event, session)

    assert len({s.step_id for s in _clause_steps(outbox_collection)}) == 1


# ── the pack and the unlock ─────────────────────────────────────────────────────


def _analyst_clause(status: ItemStatus = ItemStatus.CONFIRMED) -> ReviewItemInternal:
    return ReviewItemInternal(
        item_id="acl_1",
        kind=ItemKind.CLAUSE,
        status=status,
        gate_id=None,
        subject={
            "clause_id": "FFIEC-TPR-12",
            "corpus_v": 14,
            "origin": "analyst",
            "title": "Third-party payout agents",
            "ref": "FFIEC BSA/AML Manual, p. 12",
            "added_by": "u-riyer",
            "added_by_name": "Rahul Iyer",
            "added_reason": REASON,
        },
        proposed={"quote": TEXT},
    )


def _agent_clause() -> ReviewItemInternal:
    return ReviewItemInternal(
        item_id="itm_c1",
        kind=ItemKind.CLAUSE,
        status=ItemStatus.CONFIRMED,
        gate_id="gate_c1",
        subject={"clause_id": "AML-12", "domain": "channel", "corpus_v": 14},
        proposed={"quote": "q", "applicability": "a", "confidence": 0.9},
    )


def test_the_pack_quotes_an_analysts_clause_with_who_added_it_and_why() -> None:
    assert assessment_pack.pack_clause(_analyst_clause()) == {
        "id": "FFIEC-TPR-12",
        "title": "Third-party payout agents",
        "quote": TEXT,
        "ref": "FFIEC BSA/AML Manual, p. 12 · corpus v14",
        "added_by": "Rahul Iyer",
        "added_reason": REASON,
    }


def test_an_agents_clause_keeps_the_shape_it_had_so_its_pack_hash_holds() -> None:
    assert assessment_pack.pack_clause(_agent_clause()) == {
        "id": "AML-12",
        "title": "AML-12",
        "quote": "q",
        "ref": "corpus v14",
    }


def test_the_pack_shape_is_one_s5_accepts() -> None:
    from fcrm_contracts.api.decision import PackClause

    parsed = PackClause.model_validate(assessment_pack.pack_clause(_analyst_clause()))

    assert (parsed.added_by, parsed.added_reason) == ("Rahul Iyer", REASON)


def test_an_unlock_gives_the_analysts_clause_no_gate() -> None:
    step = harness_step(
        StepKind.UNLOCK_SUPERSEDE,
        {"case_id": CASE, "actor": "u-riyer", "unlock_reason": REMOVE_REASON},
    )

    gates, rewrites = assessment_pack.reopen_gates(
        step, [_analyst_clause(), _agent_clause()]
    )

    assert [g.subject["id"] for g in gates] == ["itm_c1"]
    assert [r.item_id for r in rewrites] == ["itm_c1"]


def test_an_analysts_clause_does_not_hold_up_the_release() -> None:
    from fcrm_contracts.api.case_internal_assessment import RecommendationInternal

    checks = assessment_pack.release_checks(
        locked=True,
        recommendation=RecommendationInternal.model_validate(
            {
                "case_id": CASE,
                "run_id": RUN,
                "draft": {"business_summary": {"text": "Ready."}},
                "draft_seq": 1,
            }
        ),
        items=[_analyst_clause()],
        acks_open=0,
    )

    assert checks["all_items_reviewed"] is True

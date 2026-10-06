"""The engine and the per-case token ledger (ADR-14, ADR-27): a job is reserved
in the transaction that publishes it, settled when its result is applied,
refused at the cap with an audit record and a handoff, and a re-run spends from
the same ledger. Every collection is the in-memory fake; the real transaction
and the concurrent race are in ``test_engine_budget_live.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from conftest import (
    FakeCollection,
    FakeSession,
    RecordingAudit,
    agent_done_body,
)
from fcrm_caseflow.budget import (
    CaseBudgetStore,
    JobRequest,
    JobStatus,
    reservation_tokens,
)
from fcrm_caseflow.dispatch import AgentDispatch
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.steps import AgentJobSpec, StepResult
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    AgentResultStore,
    CaseRun,
    CaseRunStore,
    new_case_run_document,
)
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import AgentName, CaseState, GateType
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import JobPayload

CASE_ID = "FCRM-2026-0151"
RUN_ID = "run_budget"
CAP = 120_000

EngineFactory = Callable[..., CaseflowEngine]


def _env(event_type: str, payload: dict[str, Any]) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id=CASE_ID,
        run_id=RUN_ID,
        occurred_at=utcnow(),
        payload=payload,
    )


def _done(job_id: str, agent: str, tokens: int) -> Envelope:
    body = agent_done_body(job_id, RUN_ID, agent=agent)
    body["usage"] = {"input_tokens": tokens - 300, "output_tokens": 300}
    return _env("agent.done", body)


def _failed(job_id: str, agent: str, usage: Optional[dict[str, int]]) -> Envelope:
    body: dict[str, Any] = {
        "job_id": job_id,
        "case_id": CASE_ID,
        "run_id": RUN_ID,
        "agent": agent,
        "attempt": 2,
        "reason": "guard4_rejected: schema mismatch",
    }
    if usage is not None:
        body["usage"] = usage
    return _env("agent.failed", body)


def _jobs(outbox_collection: FakeCollection) -> list[JobPayload]:
    return [
        JobPayload.model_validate(env.payload)
        for env in (
            Envelope.model_validate_json(bytes(doc["body"]))
            for doc in outbox_collection.docs
            if doc["type"] == "agent.run"
        )
    ]


async def _seed_run(
    run_store: CaseRunStore,
    session: FakeSession,
    state: CaseState,
    *,
    pending_agents: tuple[str, ...] = (),
    dispatched: tuple[str, ...] = (),
) -> CaseRun:
    document = new_case_run_document(run_id=RUN_ID, case_id=CASE_ID, pins=DEFAULT_PINS)
    document["state"] = state.value
    document["pending_agents"] = list(pending_agents)
    document["dispatched_in_state"] = list(dispatched)
    return await run_store.create(document, session=session)


async def _hold(
    store: CaseBudgetStore,
    session: FakeSession,
    *held: tuple[str, AgentName],
    spent: int = 0,
) -> None:
    """A ledger as the earlier scenes left it: ``spent`` tokens settled and the
    given jobs reserved and still running.
    """
    if spent:
        await store.settle(
            CASE_ID, RUN_ID, "job_earlier", AgentName.INTAKE, spent, session=session
        )
    jobs = [JobRequest(job_id, a, reservation_tokens(a)) for job_id, a in held]
    if jobs:
        await store.reserve(CASE_ID, RUN_ID, jobs, session=session)


async def _in_assess(
    run_store: CaseRunStore,
    session: FakeSession,
    budget_store: CaseBudgetStore,
    *,
    spent: int,
) -> None:
    """The research and assessment pair is out: reserved, and pending the join."""
    await _seed_run(
        run_store,
        session,
        CaseState.ASSESS,
        pending_agents=("research", "assessment"),
        dispatched=("internal.assess_dispatch",),
    )
    await _hold(
        budget_store,
        session,
        ("job_r", AgentName.RESEARCH),
        ("job_s", AgentName.ASSESSMENT),
        spent=spent,
    )


async def test_the_critic_is_reserved_when_the_pair_settles_and_is_published(
    engine_factory: EngineFactory,
    run_store: CaseRunStore,
    budget_store: CaseBudgetStore,
    session: FakeSession,
    outbox_collection: FakeCollection,
) -> None:
    engine = engine_factory(budgets=budget_store)
    await _in_assess(run_store, session, budget_store, spent=30_000)

    await engine.handle(_done("job_r", "research", 17_000), session)
    midway = await budget_store.get(CASE_ID)
    assert midway is not None
    # The research job's reservation became what it spent; the assessment job's
    # is untouched: two parallel jobs are two independent lines.
    assert (midway.spent, midway.reserved) == (47_000, 24_000)
    assert midway.jobs["job_r"].status is JobStatus.SETTLED
    assert midway.jobs["job_s"].status is JobStatus.RESERVED

    await engine.handle(_done("job_s", "assessment", 19_500), session)

    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    [critic] = _jobs(outbox_collection)
    assert critic.agent is AgentName.CRITIC
    assert ledger.spent == 66_500
    assert ledger.reserved == 20_000
    assert ledger.jobs[critic.job_id].reserved == reservation_tokens(AgentName.CRITIC)
    assert ledger.jobs[critic.job_id].status is JobStatus.RESERVED


async def test_a_redelivered_result_settles_once(
    engine_factory: EngineFactory,
    run_store: CaseRunStore,
    budget_store: CaseBudgetStore,
    session: FakeSession,
) -> None:
    engine = engine_factory(budgets=budget_store)
    await _in_assess(run_store, session, budget_store, spent=0)

    await engine.handle(_done("job_r", "research", 17_000), session)
    await engine.handle(_done("job_r", "research", 17_000), session)

    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert ledger.spent == 17_000
    assert ledger.jobs["job_r"].spent == 17_000


async def test_every_audit_record_the_engine_writes_carries_an_idempotency_key(
    engine_factory: EngineFactory,
    run_store: CaseRunStore,
    budget_store: CaseBudgetStore,
    session: FakeSession,
    recording_audit: RecordingAudit,
) -> None:
    """The engine writes its records from inside the Mongo transaction, which
    ``with_transaction`` re-runs on a transient error: without a key each re-run
    chains the same ``budget.exhausted`` or transition record again.
    """
    results = AgentResultStore(FakeCollection("agent_results"))  # type: ignore[arg-type]
    engine = engine_factory(budgets=budget_store, results=results)
    await _in_assess(run_store, session, budget_store, spent=105_000)

    await engine.handle(_done("job_r", "research", 1_500), session)
    await engine.handle(_done("job_s", "assessment", 1_500), session)

    assert recording_audit.records
    assert None not in recording_audit.keys
    assert len(set(recording_audit.keys)) == len(recording_audit.keys)


async def test_a_dispatch_the_cap_refuses_is_not_published_and_hands_the_case_off(
    engine_factory: EngineFactory,
    run_store: CaseRunStore,
    gate_store: GateStore,
    budget_store: CaseBudgetStore,
    case_budgets_collection: FakeCollection,
    session: FakeSession,
    outbox_collection: FakeCollection,
    recording_audit: RecordingAudit,
) -> None:
    results = AgentResultStore(FakeCollection("agent_results"))  # type: ignore[arg-type]
    engine = engine_factory(budgets=budget_store, results=results)
    await _in_assess(run_store, session, budget_store, spent=105_000)

    await engine.handle(_done("job_r", "research", 1_500), session)
    await engine.handle(_done("job_s", "assessment", 1_500), session)

    # 108,000 spent, nothing held: the critic's 20,000 would make 128,000.
    run = await run_store.get(RUN_ID)
    assert run is not None
    assert run.state is CaseState.HANDOFF
    assert await gate_store.count_open(CASE_ID, GateType.HANDOFF) == 1
    assert _jobs(outbox_collection) == []
    [refusal] = [r for r in recording_audit.records if r.type == "budget.exhausted"]
    assert refusal.actor == "system"
    assert refusal.data["agent"] == "critic"
    assert refusal.data["spent"] == 108_000
    assert refusal.data["reserved"] == 0
    assert refusal.data["cap"] == CAP
    assert refusal.data["requested"] == 20_000
    assert "budget_exhausted" in refusal.summary
    types = [r.type for r in recording_audit.records]
    assert types.index("budget.exhausted") < types.index("agent.failed.after_retry")
    # Never silent, never lossy: the ledger kept the refusal, and both results
    # the case had already computed are still stored.
    assert case_budgets_collection.docs[0]["last_refusal"]["cap"] == CAP
    assert len(await results.list_for(RUN_ID, AgentName.RESEARCH)) == 1
    assert len(await results.list_for(RUN_ID, AgentName.ASSESSMENT)) == 1


async def test_a_failed_job_is_charged_for_every_attempt_it_made(
    engine_factory: EngineFactory,
    run_store: CaseRunStore,
    gate_store: GateStore,
    budget_store: CaseBudgetStore,
    session: FakeSession,
) -> None:
    engine = engine_factory(budgets=budget_store)
    await _in_assess(run_store, session, budget_store, spent=0)

    await engine.handle(
        _failed(
            "job_r",
            "research",
            {"input_tokens": 41_000, "output_tokens": 7_000, "cache_read_tokens": 9},
        ),
        session,
    )

    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    # Two attempts of a 24,000 tier: 48,000, cache reads not counted.
    assert ledger.spent == 48_000
    assert ledger.jobs["job_r"].reserved == 0
    assert ledger.reserved == 24_000  # the assessment job is still out
    run = await run_store.get(RUN_ID)
    assert run is not None
    assert run.state is CaseState.HANDOFF
    assert await gate_store.count_open(CASE_ID, GateType.HANDOFF) == 1


async def test_a_failure_with_no_usage_releases_the_reservation_and_spends_nothing(
    engine_factory: EngineFactory,
    run_store: CaseRunStore,
    budget_store: CaseBudgetStore,
    session: FakeSession,
) -> None:
    engine = engine_factory(budgets=budget_store)
    await _in_assess(run_store, session, budget_store, spent=0)

    await engine.handle(_failed("job_r", "research", None), session)

    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert (ledger.spent, ledger.reserved) == (0, 24_000)


async def test_a_step_dispatch_reserves_and_a_clarification_re_run_continues_the_ledger(
    engine_factory: EngineFactory,
    run_store: CaseRunStore,
    budget_store: CaseBudgetStore,
    case_budgets_collection: FakeCollection,
    session: FakeSession,
    outbox_collection: FakeCollection,
) -> None:
    engine = engine_factory(budgets=budget_store)
    await _seed_run(run_store, session, CaseState.CLARIFICATION)
    await _hold(budget_store, session, spent=62_000)
    result = StepResult(
        agent_jobs_to_dispatch=(
            AgentJobSpec(AgentDispatch(AgentName.RESEARCH, job_id="job_r2")),
            AgentJobSpec(AgentDispatch(AgentName.ASSESSMENT, job_id="job_s2")),
        )
    )

    await engine.complete_step(RUN_ID, "step_rerun", result)

    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert len(case_budgets_collection.docs) == 1  # one ledger for the case
    assert (ledger.spent, ledger.reserved) == (62_000, 48_000)
    assert {j.agent for j in _jobs(outbox_collection)} == {
        AgentName.RESEARCH,
        AgentName.ASSESSMENT,
    }
    run = await run_store.get(RUN_ID)
    assert run is not None
    assert run.pending_agents == ("research", "assessment")


async def test_a_step_dispatch_the_cap_refuses_keeps_the_step_and_hands_off(
    engine_factory: EngineFactory,
    run_store: CaseRunStore,
    gate_store: GateStore,
    budget_store: CaseBudgetStore,
    session: FakeSession,
    outbox_collection: FakeCollection,
    recording_audit: RecordingAudit,
) -> None:
    engine = engine_factory(budgets=budget_store)
    await _seed_run(run_store, session, CaseState.REVIEW)
    await _hold(budget_store, session, spent=90_000)
    result = StepResult(
        agent_jobs_to_dispatch=(
            AgentJobSpec(AgentDispatch(AgentName.RESEARCH)),
            AgentJobSpec(AgentDispatch(AgentName.ASSESSMENT)),
        ),
        refs_patch={"rerun": "requested"},
    )

    await engine.complete_step(RUN_ID, "step_rerun", result)

    run = await run_store.get(RUN_ID)
    assert run is not None
    assert run.state is CaseState.HANDOFF
    assert run.refs == {"rerun": "requested"}  # what the step left is kept
    assert run.pending_agents == ()  # no join waits for jobs never published
    assert _jobs(outbox_collection) == []
    assert await gate_store.count_open(CASE_ID, GateType.HANDOFF) == 1
    [refusal] = [r for r in recording_audit.records if r.type == "budget.exhausted"]
    assert refusal.data["agents"] == ["research", "assessment"]
    assert refusal.data["requested"] == 48_000
    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert ledger.reserved == 0  # the pair was refused whole, nothing was held


async def test_an_oversight_job_is_published_unreserved_and_its_spend_is_settled(
    engine_factory: EngineFactory,
    run_store: CaseRunStore,
    budget_store: CaseBudgetStore,
    session: FakeSession,
    outbox_collection: FakeCollection,
) -> None:
    engine = engine_factory(budgets=budget_store)
    await _seed_run(run_store, session, CaseState.CLARIFICATION)
    await _hold(budget_store, session, spent=119_000)
    result = StepResult(
        agent_jobs_to_dispatch=(
            AgentJobSpec(AgentDispatch(AgentName.POLICY_GAP, job_id="job_gap")),
        )
    )

    await engine.complete_step(RUN_ID, "step_gap", result)

    [gap] = _jobs(outbox_collection)
    assert gap.agent is AgentName.POLICY_GAP
    held = await budget_store.get(CASE_ID)
    assert held is not None
    assert "job_gap" not in held.jobs
    run = await run_store.get(RUN_ID)
    assert run is not None
    assert run.state is CaseState.CLARIFICATION

    await engine.handle(_done("job_gap", "policy_gap", 6_000), session)

    settled = await budget_store.get(CASE_ID)
    assert settled is not None
    assert settled.spent == 125_000  # an unmetered job may pass the cap
    assert settled.jobs["job_gap"].reserved == 0


async def test_without_a_ledger_every_job_is_published_as_before(
    engine_factory: EngineFactory,
    run_store: CaseRunStore,
    case_budgets_collection: FakeCollection,
    session: FakeSession,
    outbox_collection: FakeCollection,
) -> None:
    engine = engine_factory()
    await _seed_run(run_store, session, CaseState.CLARIFICATION)
    result = StepResult(
        agent_jobs_to_dispatch=(
            AgentJobSpec(AgentDispatch(AgentName.RESEARCH)),
            AgentJobSpec(AgentDispatch(AgentName.ASSESSMENT)),
        )
    )

    await engine.complete_step(RUN_ID, "step_rerun", result)

    assert len(_jobs(outbox_collection)) == 2
    assert case_budgets_collection.docs == []

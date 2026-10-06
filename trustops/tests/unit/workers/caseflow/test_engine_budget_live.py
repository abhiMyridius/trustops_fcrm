"""The per-case token ledger against a real MongoDB 8 replica set: what the
in-memory collection of ``test_engine_budget.py`` only imitates.

Covered here: a reservation and its outbox row commit or abort together, a
settled result changes the ledger once however often it is applied, a refusal
leaves the audit record, the handoff gate and no job in a real transaction, and
a burst of concurrent dispatches for one case can never take more than the cap
(the later transaction conflicts on the ledger document and is retried against
the new state).

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:29017/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/unit/workers/caseflow/test_engine_budget_live.py
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from conftest import RecordingAudit, agent_done_body
from fcrm_caseflow.budget import (
    CaseBudgetStore,
    JobRequest,
    JobStatus,
    reservation_tokens,
)
from fcrm_caseflow.engine import CaseflowEngine, mongo_transact
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    AgentResultStore,
    CaseRunStore,
    new_case_run_document,
)
from fcrm_common.ids import new_id
from fcrm_common.mongo import mongo_client, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.enums import AgentName, CaseState, GateType
from fcrm_contracts.events.envelope import Envelope
from pymongo.asynchronous.database import AsyncDatabase

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

CASE_ID = "FCRM-2026-0151"
RUN_ID = "run_live_budget"
STRONG = reservation_tokens(AgentName.RESEARCH)  # 24,000


@dataclass(frozen=True)
class World:
    """The engine and the ledger over one real database."""

    client: Any
    database: AsyncDatabase[Any]
    engine: CaseflowEngine
    runs: CaseRunStore
    gates: GateStore
    budgets: CaseBudgetStore
    audit: RecordingAudit

    async def seed_assess(self) -> None:
        """A run in ASSESS with the research and assessment pair out."""
        document = new_case_run_document(
            run_id=RUN_ID, case_id=CASE_ID, pins=DEFAULT_PINS
        )
        document["state"] = CaseState.ASSESS.value
        document["pending_agents"] = ["research", "assessment"]
        document["dispatched_in_state"] = ["internal.assess_dispatch"]
        await self.runs.create(document)

    async def in_txn(self, work: Any) -> Any:
        return await with_transaction(self.client, work)

    async def reserve_pair(self) -> None:
        await self.in_txn(
            lambda s: self.budgets.reserve(
                CASE_ID,
                RUN_ID,
                [
                    JobRequest("job_r", AgentName.RESEARCH, STRONG),
                    JobRequest("job_s", AgentName.ASSESSMENT, STRONG),
                ],
                session=s,
            )
        )

    async def settle_earlier(self, tokens: int) -> None:
        await self.in_txn(
            lambda s: self.budgets.settle(
                CASE_ID, RUN_ID, "job_earlier", AgentName.INTAKE, tokens, session=s
            )
        )

    async def send_done(self, job_id: str, agent: str, tokens: int) -> None:
        body = agent_done_body(job_id, RUN_ID, agent=agent)
        body["usage"] = {"input_tokens": tokens - 300, "output_tokens": 300}
        envelope = Envelope(
            event_id=new_id("evt"),
            type="agent.done",
            case_id=CASE_ID,
            run_id=RUN_ID,
            occurred_at=utcnow(),
            payload=body,
        )
        await with_transaction(self.client, lambda s: self.engine.handle(envelope, s))

    async def agent_runs(self) -> list[dict[str, Any]]:
        rows = [r async for r in self.database["outbox"].find({"type": "agent.run"})]
        return [Envelope.model_validate_json(bytes(r["body"])).payload for r in rows]

    async def ledger_document(self) -> dict[str, Any]:
        document = await self.database["case_budgets"].find_one({"_id": CASE_ID})
        assert document is not None
        return document


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with mongo_client(URI) as client:
        name = f"fcrm_caseflow_budget_it_{new_id('db')[3:].lower()}"
        database = client[name]
        runs = CaseRunStore(database["case_runs"], database["run_events"])
        gates = GateStore(database["gates"])
        budgets = CaseBudgetStore(database["case_budgets"], cap=120_000)
        audit = RecordingAudit()
        await runs.ensure_indexes()
        await gates.ensure_indexes()
        engine = CaseflowEngine(
            runs,
            gates,
            Outbox(database["outbox"]),
            audit,
            step_map={},  # the steps are not under test: the chain runs on
            transact=mongo_transact(client),
            results=AgentResultStore(database["agent_results"]),
            budgets=budgets,
        )
        yield World(client, database, engine, runs, gates, budgets, audit)
        await client.drop_database(name)


async def test_a_reservation_aborts_with_its_transaction(world: World) -> None:
    async def reserve_then_fail(session: Any) -> None:
        await world.budgets.reserve(
            CASE_ID,
            RUN_ID,
            [JobRequest("job_r", AgentName.RESEARCH, STRONG)],
            session=session,
        )
        raise RuntimeError("the transaction fails after reserving")

    with pytest.raises(RuntimeError, match="fails after reserving"):
        await world.in_txn(reserve_then_fail)

    assert await world.budgets.get(CASE_ID) is None


async def test_settling_in_real_transactions_counts_a_job_once(world: World) -> None:
    await world.reserve_pair()

    for _ in range(3):
        await world.in_txn(
            lambda s: world.budgets.settle(
                CASE_ID, RUN_ID, "job_r", AgentName.RESEARCH, 17_000, session=s
            )
        )

    document = await world.ledger_document()
    assert (document["spent"], document["reserved"]) == (17_000, STRONG)
    assert document["jobs"]["job_r"]["status"] == JobStatus.SETTLED.value
    assert document["jobs"]["job_s"]["status"] == JobStatus.RESERVED.value


async def test_the_critic_is_reserved_with_its_outbox_row_in_one_transaction(
    world: World,
) -> None:
    await world.seed_assess()
    await world.reserve_pair()
    await world.settle_earlier(30_000)

    await world.send_done("job_r", "research", 17_000)
    await world.send_done("job_s", "assessment", 19_500)

    [critic] = await world.agent_runs()
    document = await world.ledger_document()
    assert critic["agent"] == "critic"
    assert (document["spent"], document["reserved"]) == (66_500, 20_000)
    assert document["jobs"][critic["job_id"]]["reserved"] == 20_000


async def test_a_refusal_leaves_the_audit_record_the_gate_and_no_job(
    world: World,
) -> None:
    await world.seed_assess()
    await world.reserve_pair()
    await world.settle_earlier(105_000)

    await world.send_done("job_r", "research", 1_500)
    await world.send_done("job_s", "assessment", 1_500)

    run = await world.runs.get(RUN_ID)
    assert run is not None
    assert run.state is CaseState.HANDOFF
    assert await world.gates.count_open(CASE_ID, GateType.HANDOFF) == 1
    assert await world.agent_runs() == []
    [refusal] = [r for r in world.audit.records if r.type == "budget.exhausted"]
    assert (refusal.data["spent"], refusal.data["cap"]) == (108_000, 120_000)
    assert (await world.ledger_document())["last_refusal"]["asked"] == 20_000
    stored = [d async for d in world.database["agent_results"].find({})]
    assert {d["agent"] for d in stored} == {"research", "assessment"}


async def test_concurrent_dispatches_never_take_more_than_the_cap(
    world: World,
) -> None:
    # Nine jobs of 24,000 race for a 120,000 cap: exactly five fit.
    async def dispatch(index: int) -> bool:
        job = JobRequest(f"job_{index}", AgentName.RESEARCH, STRONG)
        outcome = await world.in_txn(
            lambda s: world.budgets.reserve(CASE_ID, RUN_ID, [job], session=s)
        )
        return bool(outcome.granted)

    granted = await asyncio.gather(*(dispatch(i) for i in range(9)))

    document = await world.ledger_document()
    assert sum(granted) == 5
    assert document["reserved"] == 5 * STRONG <= document["cap"]
    held = [j for j in document["jobs"].values() if j["status"] == "reserved"]
    assert len(held) == 5


async def test_a_settle_racing_reservations_loses_no_update(world: World) -> None:
    await world.reserve_pair()

    async def settle_research() -> None:
        await world.in_txn(
            lambda s: world.budgets.settle(
                CASE_ID, RUN_ID, "job_r", AgentName.RESEARCH, 10_000, session=s
            )
        )

    async def reserve(index: int) -> bool:
        job = JobRequest(f"job_c{index}", AgentName.CRITIC, 20_000)
        outcome = await world.in_txn(
            lambda s: world.budgets.reserve(CASE_ID, RUN_ID, [job], session=s)
        )
        return bool(outcome.granted)

    *granted, _ = await asyncio.gather(
        *(reserve(i) for i in range(6)), settle_research()
    )

    document = await world.ledger_document()
    held = sum(
        j["reserved"] for j in document["jobs"].values() if j["status"] == "reserved"
    )
    spent = sum(j["spent"] for j in document["jobs"].values())
    assert document["reserved"] == held
    assert document["spent"] == spent == 10_000
    assert document["spent"] + document["reserved"] <= document["cap"]
    assert sum(granted) >= 2  # 120,000 - 24,000 held - 10,000 spent leaves room


async def test_two_first_dispatches_create_one_ledger(world: World) -> None:
    async def dispatch(index: int) -> None:
        job = JobRequest(f"job_{index}", AgentName.SCREENING, 15_000)
        await world.in_txn(
            lambda s: world.budgets.reserve(CASE_ID, RUN_ID, [job], session=s)
        )

    await asyncio.gather(*(dispatch(i) for i in range(4)))

    assert await world.database["case_budgets"].count_documents({}) == 1
    document = await world.ledger_document()
    assert document["reserved"] == 4 * 15_000

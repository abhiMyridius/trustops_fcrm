"""The engine's assessment additions against a real MongoDB 8 replica set, with the
real outbox: what the in-memory collections of the scene test only imitate.

Covered here: a step result applied in one real transaction (the ``$set`` of
``refs.<key>``, the ``$inc`` of counters, the band, deterministic gates opened with
``open_if_absent``, a follow-up step, a UI event), a second step that names the same
gates opening nothing twice, :meth:`GateStore.close_if_open` closing only what is
still open, an acknowledgement starting ``review.ack`` and being attributed to the
person who decided it, and ``case.locked`` opening the pack-release gate under the
id its pack version gives.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set, like the intake
counterpart::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27917/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/unit/workers/caseflow/test_engine_assessment_live.py
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional

import pytest
from conftest import RecordingAudit
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.dispatch import pack_release_gate_key
from fcrm_caseflow.engine import CaseflowEngine, mongo_transact
from fcrm_caseflow.gates import SYSTEM_DECIDER, GateStore, deterministic_gate_id
from fcrm_caseflow.steps import (
    FollowUpStep,
    GateCloseSpec,
    GateSpec,
    StepResult,
    UiEvent,
)
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    AgentResultStore,
    CaseRun,
    CaseRunStore,
    new_case_run_document,
)
from fcrm_common.ids import new_id
from fcrm_common.mongo import mongo_client, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.enums import CaseState, GateStatus, GateType, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep
from pymongo.asynchronous.database import AsyncDatabase

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

CASE_ID = "FCRM-2026-0151"
RUN_ID = "run_live_assess"
ANALYST = "u-riyer"


@dataclass(frozen=True)
class World:
    """The engine over a real database, with the stores it was built from."""

    client: Any
    database: AsyncDatabase[Any]
    engine: CaseflowEngine
    runs: CaseRunStore
    gates: GateStore

    async def seed_run(
        self, state: CaseState, refs: Optional[dict[str, Any]] = None
    ) -> CaseRun:
        """A run already in ``state``, as if the earlier scenes had happened."""
        document = new_case_run_document(
            run_id=RUN_ID, case_id=CASE_ID, pins=DEFAULT_PINS
        )
        document["state"] = state.value
        document["refs"] = dict(refs or {})
        return await self.runs.create(document)

    async def send(self, event_type: str, payload: dict[str, Any]) -> None:
        """One wire event to the engine, in a real transaction."""
        envelope = Envelope(
            event_id=new_id("evt"),
            type=event_type,
            case_id=CASE_ID,
            run_id=RUN_ID,
            occurred_at=utcnow(),
            payload=payload,
        )
        await with_transaction(self.client, lambda s: self.engine.handle(envelope, s))

    async def outbox(self, event_type: str) -> list[Envelope]:
        rows = [r async for r in self.database["outbox"].find({"type": event_type})]
        return [Envelope.model_validate_json(bytes(r["body"])) for r in rows]

    async def steps(self) -> list[HarnessStep]:
        return [
            HarnessStep.model_validate(e.payload)
            for e in await self.outbox("harness.step")
        ]

    async def run_document(self) -> dict[str, Any]:
        document = await self.database["case_runs"].find_one({"_id": RUN_ID})
        assert document is not None
        return document


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with mongo_client(URI) as client:
        name = f"fcrm_caseflow_assess_it_{new_id('db')[3:].lower()}"
        database = client[name]
        runs = CaseRunStore(database["case_runs"], database["run_events"])
        gates = GateStore(database["gates"])
        await runs.ensure_indexes()
        await gates.ensure_indexes()
        engine = CaseflowEngine(
            runs,
            gates,
            Outbox(database["outbox"]),
            RecordingAudit(),
            step_map=dict(STEP_FOR),
            transact=mongo_transact(client),
            results=AgentResultStore(database["agent_results"]),
        )
        yield World(client, database, engine, runs, gates)
        await client.drop_database(name)


def _review_gate(item: str) -> GateSpec:
    return GateSpec(GateType.REVIEW_ITEM, item, {"kind": "control", "id": item})


ACK_GATE = GateSpec(
    GateType.ACK, "band_change:step_a", {"kind": "ack", "id": "band_change"}
)


async def test_a_step_result_applies_in_one_transaction_and_opens_nothing_twice(
    world: World,
) -> None:
    await world.seed_run(CaseState.REVIEW, refs={"keep": 1})
    result = StepResult(
        gates_to_open=(_review_gate("itm_x"), _review_gate("itm_y"), ACK_GATE),
        refs_patch={"score_summary": {"composite": 10.4, "band": "3"}},
        follow_up_steps=(FollowUpStep(StepKind.STATE_PUSH, {"why": "after score"}),),
        ui_events=(UiEvent("score.computed", {"composite": 10.4}),),
        band=3,
        counter_deltas={"agents_run": 2, "tokens_used": 150},
    )

    await world.engine.complete_step(
        RUN_ID, "step_a", result, step=StepKind.ASSESS_PUBLISH
    )

    document = await world.run_document()
    assert document["refs"] == {
        "keep": 1,
        "score_summary": {"composite": 10.4, "band": "3"},
    }
    assert document["counters"] == {"agents_run": 2, "tokens_used": 150}
    assert document["band"] == 3
    stored = {g["_id"]: g async for g in world.database["gates"].find({})}
    assert set(stored) == {
        deterministic_gate_id(RUN_ID, GateType.REVIEW_ITEM, "itm_x"),
        deterministic_gate_id(RUN_ID, GateType.REVIEW_ITEM, "itm_y"),
        deterministic_gate_id(RUN_ID, GateType.ACK, "band_change:step_a"),
    }
    assert {g["status"] for g in stored.values()} == {GateStatus.OPEN.value}
    assert len(await world.outbox("gate.opened")) == 3
    assert len(await world.outbox("score.computed")) == 1
    [follow_up] = await world.steps()
    assert follow_up.step is StepKind.STATE_PUSH

    # The same step again, as a redelivery: nothing changes anywhere.
    await world.engine.complete_step(
        RUN_ID, "step_a", result, step=StepKind.ASSESS_PUBLISH
    )
    assert (await world.run_document())["counters"] == {
        "agents_run": 2,
        "tokens_used": 150,
    }
    assert len(await world.outbox("gate.opened")) == 3

    # Another step naming the same gates and one new one: only the new one opens.
    second = StepResult(
        gates_to_open=(_review_gate("itm_x"), _review_gate("itm_z")),
        refs_patch={"overrides": ["CTL-T01 7.6 → 4.5"]},
    )
    await world.engine.complete_step(
        RUN_ID, "step_b", second, step=StepKind.REVIEW_ITEM
    )
    assert await world.database["gates"].count_documents({}) == 4
    assert len(await world.outbox("gate.opened")) == 4
    merged = (await world.run_document())["refs"]
    assert (
        merged["score_summary"]["composite"] == 10.4
    )  # a patch merges, never replaces
    assert merged["overrides"] == ["CTL-T01 7.6 → 4.5"]


async def test_a_step_closes_only_the_gates_that_are_still_open(world: World) -> None:
    await world.seed_run(CaseState.REVIEW)
    opened = StepResult(gates_to_open=(_review_gate("itm_x"), _review_gate("itm_y")))
    await world.engine.complete_step(RUN_ID, "step_a", opened)
    y_id = deterministic_gate_id(RUN_ID, GateType.REVIEW_ITEM, "itm_y")
    await world.database["gates"].update_one(
        {"_id": y_id},
        {
            "$set": {
                "status": GateStatus.DECIDED.value,
                "decision": "confirm",
                "decided_by": ANALYST,
                "decided_at": utcnow(),
            }
        },
    )

    def close(item: str) -> GateCloseSpec:
        return GateCloseSpec(
            GateType.REVIEW_ITEM, item, "superseded", reason="replaced by a re-run"
        )

    await world.engine.complete_step(
        RUN_ID,
        "step_b",
        StepResult(gates_to_close=(close("itm_x"), close("itm_y"), close("itm_none"))),
    )

    x = await world.gates.get(
        deterministic_gate_id(RUN_ID, GateType.REVIEW_ITEM, "itm_x")
    )
    y = await world.gates.get(y_id)
    assert x is not None
    assert (x["status"], x["decision"], x["decided_by"]) == (
        GateStatus.EXPIRED.value,
        "superseded",
        SYSTEM_DECIDER,
    )
    assert x["reason"] == "replaced by a re-run"
    assert y is not None
    assert (y["status"], y["decided_by"]) == (GateStatus.DECIDED.value, ANALYST)
    assert await world.database["gates"].count_documents({}) == 2  # none was made up

    # Closing again, in a later step, leaves the closed gate exactly as it was.
    decided_at = x["decided_at"]
    await world.engine.complete_step(
        RUN_ID, "step_c", StepResult(gates_to_close=(close("itm_x"),))
    )
    again = await world.gates.get(
        deterministic_gate_id(RUN_ID, GateType.REVIEW_ITEM, "itm_x")
    )
    assert again is not None
    assert again["decided_at"] == decided_at
    # The decider of record is the person, never the system that closed the other.
    assert (
        await world.gates.last_decider(CASE_ID, GateType.REVIEW_ITEM, run_id=RUN_ID)
        == ANALYST
    )


async def test_deciding_an_ack_starts_review_ack_and_names_who_decided_it(
    world: World,
) -> None:
    await world.seed_run(CaseState.REVIEW)
    await world.engine.complete_step(
        RUN_ID,
        "step_a",
        StepResult(gates_to_open=(_review_gate("itm_x"), ACK_GATE)),
    )
    ack_id = deterministic_gate_id(RUN_ID, GateType.ACK, "band_change:step_a")

    await world.send(
        "gate.decided",
        {
            "gate_id": ack_id,
            "gate_type": GateType.ACK.value,
            "case_id": CASE_ID,
            "run_id": RUN_ID,
            "decision": "acknowledge",
            "reason": "Band change and boundary flag reviewed.",
            "decided_by": ANALYST,
            "decided_at": utcnow().isoformat(),
            "subject": {"kind": "ack", "id": "band_change"},
        },
    )

    ack = await world.gates.get(ack_id)
    assert ack is not None
    assert (ack["status"], ack["decided_by"]) == (GateStatus.DECIDED.value, ANALYST)
    kinds = [s.step for s in await world.steps()]
    assert kinds == [StepKind.REVIEW_ACK]
    run = await world.runs.get(RUN_ID)
    assert run is not None
    assert run.state is CaseState.REVIEW  # an acknowledgement moves nothing itself
    assert len(run.pending_steps) == 1
    assert (
        await world.gates.last_decider(CASE_ID, GateType.ACK, run_id=RUN_ID) == ANALYST
    )


async def test_locking_opens_the_pack_release_gate_under_its_pack_versions_id(
    world: World,
) -> None:
    await world.seed_run(CaseState.RECOMMEND)

    await world.send("case.locked", {"case_id": CASE_ID, "actor": ANALYST})

    run = await world.runs.get(RUN_ID)
    assert run is not None
    assert run.snapshot.flags.locked is True
    assert run.snapshot.flags.pack_version == 1
    gate_id = deterministic_gate_id(
        RUN_ID, GateType.PACK_RELEASE, pack_release_gate_key(1)
    )
    gate = await world.gates.get(gate_id)
    assert gate is not None
    assert gate["status"] == GateStatus.OPEN.value
    assert await world.database["gates"].count_documents({}) == 1
    assert [s.step for s in await world.steps()] == [StepKind.LOCK_BUILD_PACK]
    [opened] = await world.outbox("gate.opened")
    assert opened.payload["gate_id"] == gate_id

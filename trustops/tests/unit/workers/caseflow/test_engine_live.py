""":mod:`fcrm_caseflow.engine` against a real MongoDB 8 replica set: proves
the outbox-in-the-same-transaction contract for real (every fake-session
test in this directory proves the *API usage*, not actual commit/abort
semantics — only a real replica set can).

Skipped unless ``FCRM_TEST_MONGO_URI`` points at one, exactly like
``tests/unit/packages/common/test_common_mongo_live.py``::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27117/?replicaSet=rs0'
    uv run --project src/backend --package fcrm-caseflow pytest \\
        tests/unit/workers/caseflow/test_engine_live.py
"""

from __future__ import annotations

import os

import pytest
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore, gate_document
from fcrm_caseflow.store import CaseRunStore
from fcrm_common.ids import new_id
from fcrm_common.mongo import mongo_client, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.enums import GateStatus, GateType, Role
from fcrm_contracts.events.envelope import Envelope

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

CASE_ID = "FCRM-2026-0151"


class _RecordingAudit:
    def __init__(self) -> None:
        self.records: list[object] = []

    async def append(self, record: object, **_: object) -> object:
        self.records.append(record)
        return record


@pytest.fixture
async def db():
    async with mongo_client(URI) as client:
        name = f"fcrm_caseflow_it_{new_id('db')[3:].lower()}"
        database = client[name]
        yield client, database
        await client.drop_database(name)


async def test_a_failure_mid_handle_rolls_back_every_write_in_that_call(db) -> None:
    client, database = db
    runs = CaseRunStore(database["case_runs"], database["run_events"])
    gates = GateStore(database["gates"])
    outbox = Outbox(database["outbox"])
    engine = CaseflowEngine(runs, gates, outbox, _RecordingAudit())  # type: ignore[arg-type]

    async def open_run(session: object) -> None:
        await engine.handle(
            Envelope(
                event_id=new_id("evt"),
                type="case.created",
                case_id=CASE_ID,
                run_id=None,
                occurred_at=utcnow(),
                payload={"data": {}},
            ),
            session,  # type: ignore[arg-type]
        )

    await with_transaction(client, open_run)
    run = await runs.get_by_case_id(CASE_ID)
    assert run is not None
    run_id = run.snapshot.run_id

    await gates.open(
        gate_document(
            gate_id="gate_completeness_1",
            case_id=CASE_ID,
            run_id=run_id,
            gate_type=GateType.COMPLETENESS,
            required_role=Role.ANALYST,
        )
    )

    async def bad_decision(session: object) -> None:
        await engine.handle(
            Envelope(
                event_id=new_id("evt"),
                type="gate.decided",
                case_id=CASE_ID,
                run_id=run_id,
                occurred_at=utcnow(),
                payload={
                    "gate_id": "gate_completeness_1",
                    "gate_type": GateType.COMPLETENESS.value,
                    "case_id": CASE_ID,
                    "run_id": run_id,
                    "decision": "not-ask-or-proceed",
                    "decided_by": "u-riyer",
                    "decided_at": utcnow().isoformat(),
                },
            ),
            session,  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="unrecognised decision"):
        await with_transaction(client, bad_decision)

    # The gate's own GateStore.decide() call succeeded *inside* that same
    # handle() call, before gate_decided_trigger raised — if the
    # transaction genuinely rolled back, the gate must still read as open.
    gate_after = await gates.get("gate_completeness_1")
    assert gate_after is not None
    assert gate_after["status"] == GateStatus.OPEN.value

    run_after = await runs.get(run_id)
    assert run_after is not None
    assert run_after.snapshot.version == run.snapshot.version, (
        "no transition should have been persisted for the failed call"
    )

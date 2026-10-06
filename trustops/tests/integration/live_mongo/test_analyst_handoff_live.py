"""The human handoff on a real MongoDB 8 replica set: S1's gate record, completion and
ledger, and the whole path into the real Caseflow engine.

    docker run -d --name c6-mongo -p 28917:28917 mongo:8.0 \\
        mongod --replSet rs0 --port 28917 --bind_ip_all
    docker exec c6-mongo mongosh --quiet --port 28917 --eval \\
        'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:28917"}]}).ok'
    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28917/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/integration/live_mongo/test_analyst_handoff_live.py

What a database shows that the in-memory twin cannot: the output, the ledger row, the
activity row and ``gate.decided`` commit together or not at all; a refusal leaves no
trace; the ledger refuses a different output on a decided gate (the first version of
``get_decision`` read the wrong key and replayed it); and the event S1 writes is what
the real engine resumes a run from.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.consumer import CaseServiceEventsConsumer
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.engine import CaseflowEngine, mongo_transact
from fcrm_caseflow.eventstore import rebuild
from fcrm_caseflow.gates import GateStore, deterministic_gate_id
from fcrm_caseflow.raw_inputs import MongoRawInputsReader
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    AgentResultStore,
    CaseRunStore,
    new_case_run_document,
)
from fcrm_common.events import (
    Disposition,
    build_envelope,
    encode_envelope,
    message_headers,
)
from fcrm_common.mongo import with_transaction
from fcrm_common.outbox import Outbox
from fcrm_contracts.api.case_internal import StatePatch, UiActor
from fcrm_contracts.enums import AgentName, CaseState, GateType, Role
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import GateOpened
from fcrm_contracts.events.raw_inputs import raw_inputs_ref

if TYPE_CHECKING:
    from conftest import LivePo, TopicRecorder

pytestmark = pytest.mark.integration

CASE = "FCRM-2026-0151"
ANALYST = "u-riyer"
RUN = "run_1"
JOB = "job_r1"
REASON = "The research agent failed twice; I read the clause and wrote it up."
SYSTEM = UiActor(id="svc:caseflow", role="fcrm_service", component="caseflow")
TEXT = "## Case\nConsumer remittances US to Mexico\n\n## Clauses\nFFIEC-TPPP-04"
CLAUSE: dict[str, Any] = {
    "source_ref": {"kind": "clause", "clause_id": "FFIEC-TPPP-04", "corpus_v": 14},
    "quote": "A bank must evaluate the processor's customers and volumes.",
    "confidence": 0.9,
    "clause_id": "FFIEC-TPPP-04",
    "domain": "channel",
    "applicability": "payout partner corridor",
}
GATE = deterministic_gate_id(RUN, GateType.HANDOFF, JOB)
URL = f"/cases/{CASE}/handoff/{GATE}/complete"
BODY = {"output": [CLAUSE], "reason": REASON}


def _gate_opened(subject: dict[str, str]) -> Envelope:
    return build_envelope(
        "gate.opened",
        GateOpened(
            gate_id=GATE,
            gate_type=GateType.HANDOFF,
            case_id=CASE,
            run_id=RUN,
            subject=subject,
            required_role=Role.ANALYST.value,
        ).model_dump(mode="json"),
        case_id=CASE,
        run_id=RUN,
    )


SUBJECT = {
    "agent": "research",
    "job_id": JOB,
    "attempt": "2",
    "reason": "guard4_rejected: schema mismatch",
    "step": "assess",
    "raw_inputs_ref": raw_inputs_ref(JOB),
    "raw_inputs": TEXT,
    "raw_inputs_sha256": "ab" * 32,
    "raw_inputs_bytes": str(len(TEXT.encode())),
    "raw_inputs_truncated": "false",
}


class _Audit:
    """S4 as far as the engine uses it: it records the appends and answers none."""

    def __init__(self) -> None:
        self.records: list[Any] = []

    async def append(self, record: Any, **_: Any) -> None:
        self.records.append(record)


async def _outbox(live: LivePo, type_: str) -> list[dict[str, Any]]:
    rows = await live.case.db["outbox"].find({"type": type_}).sort("_id", 1).to_list()
    return [json.loads(bytes(r["body"])) for r in rows]


async def _count(live: LivePo, collection: str, **query: Any) -> int:
    return await live.case.db[collection].count_documents(query)


async def _deliver(
    consumer: CaseServiceEventsConsumer, envelope: Envelope
) -> Disposition:
    async def ack() -> None:
        return None

    async def nack(requeue: bool) -> None:
        return None

    return await consumer.process(
        encode_envelope(envelope), message_headers(envelope), ack, nack
    )


@pytest.fixture
async def handoff(
    live_po: LivePo,
    act_as: Callable[[Role, str], None],
    topic_recorder: TopicRecorder,
) -> LivePo:
    """The demo case held by Rahul in ``handoff``, its handoff gate announced
    through S1's own consumer, called as Rahul.
    """
    app = live_po.case.app
    await app.state.store.assign_analyst(CASE, ANALYST, "Rahul Iyer", SYSTEM)
    await app.state.store.apply_state(
        CASE, StatePatch(stage=CaseState.HANDOFF, run_id=RUN), SYSTEM
    )
    consumer = CaseServiceEventsConsumer(
        live_po.case.db["consumed_messages"],
        topic_recorder,
        activity=app.state.store.activity,
        hub=app.state.hub,
        handoffs=app.state.handoffs,
        client=live_po.case.db.client,
        max_attempts=0,
    )
    await consumer.ensure_indexes()
    assert await _deliver(consumer, _gate_opened(SUBJECT)) is Disposition.PROCESSED
    act_as(Role.ANALYST, ANALYST)
    return live_po


async def test_the_gate_the_consumer_held_is_listed_with_what_the_agent_was_given(
    handoff: LivePo,
) -> None:
    response = await handoff.client.get(f"/cases/{CASE}/handoff/gates")

    assert response.status_code == 200, response.text
    [gate] = response.json()["gates"]
    assert gate["gate_id"] == GATE
    assert (gate["agent"], gate["job_id"], gate["status"]) == ("research", JOB, "open")
    assert gate["raw_inputs"] == TEXT
    assert gate["raw_inputs_sha256"] == "ab" * 32
    assert await _count(handoff, "activity", case_id=CASE, type="handoff.opened") == 1


async def test_the_gate_is_held_once_however_often_it_is_announced(
    handoff: LivePo, topic_recorder: TopicRecorder
) -> None:
    consumer = CaseServiceEventsConsumer(
        handoff.case.db["consumed_messages"],
        topic_recorder,
        activity=handoff.case.app.state.store.activity,
        hub=handoff.case.app.state.hub,
        handoffs=handoff.case.app.state.handoffs,
        client=handoff.case.db.client,
        max_attempts=0,
    )

    await _deliver(consumer, _gate_opened(SUBJECT))  # a new event, the same gate

    assert await _count(handoff, "handoff_gates") == 1
    assert await _count(handoff, "activity", case_id=CASE, type="handoff.opened") == 1


async def test_the_output_the_ledger_the_activity_and_the_event_commit_together(
    handoff: LivePo,
) -> None:
    first = await handoff.client.post(URL, json=BODY)
    again = await handoff.client.post(URL, json=BODY)

    assert first.status_code == 202, first.text
    assert again.json()["replayed"] is True
    stored = await handoff.case.db["handoff_outputs"].find_one({"_id": GATE})
    assert stored is not None
    assert stored["output"] == [CLAUSE]
    assert (stored["decided_by"], stored["reason"]) == (ANALYST, REASON)
    ledger = await handoff.case.db["analyst_decisions"].find_one({"_id": GATE})
    assert ledger is not None
    assert ledger["decision"] == "complete"
    assert (
        await _count(handoff, "activity", case_id=CASE, type="handoff.completed") == 1
    )
    [event] = await _outbox(handoff, "gate.decided")
    assert event["payload"]["gate_type"] == "handoff"
    assert event["payload"]["after"] == {"output": [CLAUSE]}
    listed = (await handoff.client.get(f"/cases/{CASE}/handoff/gates")).json()
    assert [g["status"] for g in listed["gates"]] == ["decided"]


async def test_a_different_output_on_a_decided_gate_is_refused_by_the_ledger(
    handoff: LivePo,
) -> None:
    await handoff.client.post(URL, json=BODY)
    other = {"output": [{**CLAUSE, "confidence": 0.4}], "reason": REASON}

    response = await handoff.client.post(URL, json=other)

    assert response.status_code == 409, response.text
    assert response.json()["type"].endswith("gate_already_decided")
    assert len(await _outbox(handoff, "gate.decided")) == 1


async def test_a_refused_output_leaves_nothing_behind(handoff: LivePo) -> None:
    response = await handoff.client.post(
        URL, json={"output": [{"score": 4.1}], "reason": REASON}
    )

    assert response.status_code == 422, response.text
    assert await _count(handoff, "handoff_outputs") == 0
    assert await _count(handoff, "analyst_decisions") == 0
    assert await _outbox(handoff, "gate.decided") == []
    assert (
        await _count(handoff, "activity", case_id=CASE, type="handoff.completed") == 0
    )


# --- the whole path: S1's event resumes a run in the real engine ----------------


async def test_the_event_s1_writes_resumes_the_run_in_the_real_engine(
    handoff: LivePo,
) -> None:
    """H1 fails a research job in ``assess``, S1 takes the analyst's output, and the
    ``gate.decided`` S1 wrote is what the engine resumes the run from: the same run
    document, gate, outbox and results a deployment has, on the same replica set."""
    client = handoff.case.db.client
    db = client["fcrm_caseflow_handoff_it"]
    try:
        runs = CaseRunStore(db["case_runs"], db["run_events"])
        gates = GateStore(db["gates"])
        await runs.ensure_indexes()
        await gates.ensure_indexes()
        results = AgentResultStore(db["agent_results"])
        await db["agent_raw_inputs"].insert_one(
            {
                "_id": JOB,
                "case_id": CASE,
                "run_id": RUN,
                "agent": "research",
                "job": {"job_id": JOB},
                "text": TEXT,
                "truncated": False,
                "pack_hash": "sha256:pack",
                "sections": ["Case", "Clauses"],
                "error": None,
                "saved_at": datetime(2026, 10, 6, tzinfo=UTC),
            }
        )
        engine = CaseflowEngine(
            runs,
            gates,
            Outbox(db["outbox"]),
            _Audit(),  # type: ignore[arg-type]
            step_map=dict(STEP_FOR),
            transact=mongo_transact(client),
            results=results,
            raw_inputs=MongoRawInputsReader(db["agent_raw_inputs"]),
        )
        document = new_case_run_document(run_id=RUN, case_id=CASE, pins=DEFAULT_PINS)
        document["state"] = CaseState.ASSESS.value
        document["pending_agents"] = ["research", "assessment"]
        await runs.create(document)

        async def send(event_type: str, payload: dict[str, Any]) -> None:
            envelope = Envelope.model_validate(
                build_envelope(
                    event_type, payload, case_id=CASE, run_id=RUN
                ).model_dump(mode="json")
            )
            await with_transaction(client, lambda s: engine.handle(envelope, s))

        await send(
            "agent.failed",
            {
                "job_id": JOB,
                "case_id": CASE,
                "run_id": RUN,
                "agent": "research",
                "attempt": 2,
                "reason": "guard4_rejected: schema mismatch",
                "raw_inputs_ref": raw_inputs_ref(JOB),
            },
        )

        run = await runs.get(RUN)
        assert run is not None
        assert (run.state, run.suspended_from) == (CaseState.HANDOFF, CaseState.ASSESS)
        gate = await gates.get(GATE)
        assert gate is not None
        assert gate["subject"]["raw_inputs"] == TEXT  # read from what H2 kept
        assert gate["subject"]["raw_inputs_ref"] == raw_inputs_ref(JOB)

        # the analyst completes the job through S1; its event is what the engine reads
        response = await handoff.client.post(URL, json=BODY)
        assert response.status_code == 202, response.text
        [written] = await _outbox(handoff, "gate.decided")
        await send("gate.decided", written["payload"])

        resumed = await runs.get(RUN)
        assert resumed is not None
        assert resumed.suspended_from is None
        assert resumed.state is CaseState.ASSESS  # the assessment half is still owed
        assert resumed.pending_agents == ("assessment",)
        stored = await results.list_for(RUN, AgentName.RESEARCH)
        assert [(r.job_id, r.model) for r in stored] == [(JOB, "human")]
        assert stored[0].accepted() == [CLAUSE]
        stored_gate = await gates.get(GATE)
        assert stored_gate is not None
        assert (stored_gate["status"], stored_gate["decided_by"]) == (
            "decided",
            ANALYST,
        )
        replayed = rebuild(CaseState.ASSESS, await runs.load_recorded_transitions(RUN))
        assert replayed is resumed.state
        steps = [
            e["payload"]["step"]
            for e in [
                json.loads(bytes(r["body"]))
                async for r in db["outbox"].find({"type": "harness.step"})
            ]
        ]
        assert steps.count("handoff.open") == 2  # opened, then completed
    finally:
        await client.drop_database("fcrm_caseflow_handoff_it")

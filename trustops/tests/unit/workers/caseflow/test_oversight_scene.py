"""Scene 20 of the walkthrough, driven through the real engine and the real
oversight step handlers with fake peers.

Nothing is stubbed on H1's side. The events S4 and S3 would publish
(``examiner.asked``, ``jurisdiction.list_published``), the results H2 would
publish (``agent.done`` / ``agent.failed``) and S1's ``gate.decided`` go through
:meth:`CaseflowEngine.handle`; every ``harness.step`` that enqueues is run by a
real :class:`StepRunner` against the fakes in ``conftest`` (S1, S3, S4) and each
step's result is applied by :meth:`CaseflowEngine.complete_step`.

Two paths: **a closed case is asked a question** (the examiner answers only from
the chain, and a citation the chain does not hold becomes a refusal), and **a
jurisdiction list is published** (two open cases are re-flagged, an analyst
acknowledges one, and nothing about a score, a stage or a lock changes).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

import pytest
from conftest import (
    FakeClosingCaseService,
    FakeCollection,
    FakeExaminerService,
    FakeJurisdictionPolicyService,
    FakeReflagService,
    FakeSession,
    RecordingAudit,
    agent_done_body,
)
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.clients.case_client import CaseClient
from fcrm_caseflow.clients.oversight_client import (
    ExaminerClient,
    OversightClients,
    ReflagClient,
)
from fcrm_caseflow.clients.policy_client import PolicyClient
from fcrm_caseflow.cores import oversight
from fcrm_caseflow.engine import CaseflowEngine, OrphanEventError
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.oversight_runs import OversightRuns
from fcrm_caseflow.steps import StepRegistry, StepRunner
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    AgentResultStore,
    CaseRun,
    CaseRunStore,
    new_case_run_document,
)
from fcrm_common.ids import deterministic_id, new_id
from fcrm_common.time import utcnow
from fcrm_contracts.api.audit import RecordAppend
from fcrm_contracts.enums import AgentName, CaseState, GateStatus, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep, JobPayload
from pydantic import ValidationError

CASE = "FCRM-2026-0151"
VERSION = "v2026-10-fixture"
HARNESS_RUN = deterministic_id("run", "watchlist", VERSION)
OWNED_KINDS = frozenset(oversight.HANDLERS)
QUESTION = "Why was this case routed to the committee instead of closed?"
CHAIN_RECORDS = 12


def _list_source(version: str = VERSION) -> dict[str, Any]:
    return {"kind": "list", "list_id": "FATF", "version": version}


def reflag_proposal(
    case_id: str, jurisdiction: str, *, rerun: bool = True, **overrides: Any
) -> dict[str, Any]:
    """A ``ReflagProposal`` as the watchlist agent produces it."""
    body: dict[str, Any] = {
        "case_id": case_id,
        "jurisdiction": jurisdiction,
        "list_version": VERSION,
        "explanation": f"{jurisdiction} was re-listed in {VERSION}; research is stale.",
        "list_change": "added",
        "rerun": rerun,
        "source_ref": _list_source(),
        "quote": f"{jurisdiction} · increased monitoring",
        "confidence": 0.9,
    }
    return {**body, **overrides}


def examiner_answer(*sentences: tuple[str, list[int]]) -> dict[str, Any]:
    """An ``ExaminerAnswer`` as the examiner agent produces it."""
    return {
        "question": QUESTION,
        "sentences": [
            {"text": text, "record_refs": refs, "quotes": ["quote"] * len(refs)}
            for text, refs in sentences
        ],
    }


class Scene:
    """Drives oversight events through the engine and runs the steps they enqueue."""

    def __init__(
        self,
        *,
        engine: CaseflowEngine,
        runner: StepRunner,
        run_store: CaseRunStore,
        gates: FakeCollection,
        outbox_collection: FakeCollection,
        session: FakeSession,
        audit: RecordingAudit,
        case_service: FakeClosingCaseService,
        examiner_service: FakeExaminerService,
        reflag_service: FakeReflagService,
    ) -> None:
        self.engine = engine
        self.runner = runner
        self.run_store = run_store
        self.gates = gates
        self.outbox_collection = outbox_collection
        self.session = session
        self.audit = audit
        self.case_service = case_service
        self.examiner_service = examiner_service
        self.reflag_service = reflag_service
        self._ran: set[str] = set()

    async def open_closed_run(
        self, case_id: str = CASE, run_id: str = "run_closed"
    ) -> str:
        """A real run that finished the assessment: closed, its close dispatched."""
        document = new_case_run_document(
            run_id=run_id, case_id=case_id, pins=DEFAULT_PINS
        )
        document["state"] = CaseState.CLOSED.value
        document["closed_at"] = utcnow()
        document["dispatched_in_state"] = ["internal.closed_dispatch"]
        await self.run_store.create(document, session=self.session)  # type: ignore[arg-type]
        return run_id

    async def fill_chain(self, case_id: str = CASE, count: int = CHAIN_RECORDS) -> None:
        """Give the case's chain ``count`` records, so citations 1..count resolve."""
        for number in range(count):
            await self.audit.append(
                RecordAppend(
                    case_id=case_id,
                    type="gate.decided",
                    actor="system",
                    summary=f"record {number + 1}",
                )
            )

    async def send(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        case_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        envelope = Envelope(
            event_id=new_id("evt"),
            type=event_type,
            case_id=case_id,
            run_id=run_id,
            occurred_at=utcnow(),
            payload=dict(payload),
        )
        await self.engine.handle(envelope, self.session)  # type: ignore[arg-type]

    async def ask(self, question_id: str = "exq_1", case_id: str = CASE) -> None:
        await self.send(
            "examiner.asked",
            {
                "case_id": case_id,
                "question_id": question_id,
                "question": QUESTION,
                "asked_by": "u-riyer",
            },
            case_id=case_id,
        )

    async def publish_list(
        self, added: list[str], removed: Optional[list[str]] = None
    ) -> None:
        await self.send(
            "jurisdiction.list_published",
            {
                "version": VERSION,
                "added": added,
                "removed": removed or [],
                "fixture": True,
            },
        )

    def envelopes(self, event_type: str) -> list[Envelope]:
        envelopes = [
            Envelope.model_validate_json(bytes(d["body"]))
            for d in self.outbox_collection.docs
        ]
        return [e for e in envelopes if e.type == event_type]

    def steps(self) -> list[HarnessStep]:
        return [
            HarnessStep.model_validate(e.payload)
            for e in self.envelopes("harness.step")
        ]

    def jobs(self) -> list[JobPayload]:
        return [
            JobPayload.model_validate(e.payload) for e in self.envelopes("agent.run")
        ]

    async def drain(self) -> list[StepKind]:
        ran: list[StepKind] = []
        while True:
            pending = [s for s in self.steps() if s.step_id not in self._ran]
            if not pending:
                return ran
            for step in pending:
                self._ran.add(step.step_id)
                await self.runner.run(step)
                ran.append(step.step)

    async def agent_done(
        self, job: JobPayload, *accepted: dict[str, Any], run_id: str
    ) -> None:
        body = agent_done_body(job.job_id, run_id, *accepted, agent=job.agent.value)
        body["case_id"] = job.case_id
        await self.send("agent.done", body, case_id=job.case_id, run_id=run_id)

    async def agent_failed(self, job: JobPayload, reason: str, run_id: str) -> None:
        await self.send(
            "agent.failed",
            {
                "job_id": job.job_id,
                "case_id": job.case_id,
                "run_id": run_id,
                "agent": job.agent.value,
                "attempt": 2,
                "reason": reason,
            },
            case_id=job.case_id,
            run_id=run_id,
        )

    async def run(self, run_id: str) -> CaseRun:
        run = await self.run_store.get(run_id)
        assert run is not None
        return run

    def records(self, record_type: str) -> list[RecordAppend]:
        return [r for r in self.audit.records if r.type == record_type]


@pytest.fixture
def scene(
    engine_factory: Any,
    run_store: CaseRunStore,
    gate_store: GateStore,
    gates_collection: FakeCollection,
    outbox_collection: FakeCollection,
    session: FakeSession,
    recording_audit: RecordingAudit,
    agent_results: AgentResultStore,
    closing_case_service: FakeClosingCaseService,
    closing_case_client: CaseClient,
    examiner_service: FakeExaminerService,
    reflag_service: FakeReflagService,
    jurisdiction_policy_service: FakeJurisdictionPolicyService,
) -> Scene:
    step_map = {t: k for t, k in STEP_FOR.items() if k in OWNED_KINDS}
    engine: CaseflowEngine = engine_factory(
        step_map, results=agent_results, oversight=OversightRuns(run_store)
    )
    runner = StepRunner(
        run_store,
        StepRegistry(oversight.HANDLERS),
        engine,
        recording_audit,
        case=closing_case_client,
        policy=PolicyClient(jurisdiction_policy_service.client("policy")),
        agent_results=agent_results,
        gates=gate_store,
        oversight=OversightClients(
            examiner=ExaminerClient(examiner_service.client("audit")),
            reflags=ReflagClient(reflag_service.client("case")),
        ),
    )
    return Scene(
        engine=engine,
        runner=runner,
        run_store=run_store,
        gates=gates_collection,
        outbox_collection=outbox_collection,
        session=session,
        audit=recording_audit,
        case_service=closing_case_service,
        examiner_service=examiner_service,
        reflag_service=reflag_service,
    )


# ── the examiner: a closed case is asked a question ──────────────────────────────


async def test_the_examiner_answers_a_closed_case_from_its_chain(scene: Scene) -> None:
    run_id = await scene.open_closed_run()
    await scene.fill_chain()

    await scene.ask()
    assert await scene.drain() == [StepKind.EXAMINER_ANSWER]

    # The question is on the chain, and exactly one examiner job was dispatched
    # on the case's own closed run, carrying only the question.
    [asked] = scene.records("examiner.asked")
    assert asked.actor == "u-riyer"
    [job] = scene.jobs()
    assert (job.agent, job.case_id, job.run_id) == (AgentName.EXAMINER, CASE, run_id)
    assert job.refs == {"question": QUESTION}
    assert job.job_id == deterministic_id("job", run_id, "examiner", "exq_1")
    assert job.pins == DEFAULT_PINS

    await scene.agent_done(
        job,
        examiner_answer(
            ("The score put the case in Band 3.", [3, 5]),
            ("Band 3 goes to the committee.", [5]),
        ),
        run_id=run_id,
    )
    assert await scene.drain() == [StepKind.EXAMINER_ANSWER]

    # The checked answer reached S4 with only the records it cited.
    answer = scene.examiner_service.answers["exq_1"]
    assert answer["refused"] is False
    assert answer["sentences"] == [
        {"t": "The score put the case in Band 3.", "refs": [3, 5]},
        {"t": "Band 3 goes to the committee.", "refs": [5]},
    ]
    assert answer["case_id"] == CASE
    assert answer["run_id"] == run_id
    # the chain now includes the examiner.asked record H1 appended
    assert f"{CHAIN_RECORDS + 1} records" in answer["references"][0]
    [answered] = scene.records("examiner.answered")
    assert answered.data["cited"] == [3, 5]
    assert answered.data["refused"] is False
    shown = [e for e in scene.case_service.events if e["type"] == "examiner.answered"]
    assert [e["visibility"] for e in shown] == ["analyst"]
    # Asking never moved the closed run.
    assert (await scene.run(run_id)).state is CaseState.CLOSED


@pytest.mark.parametrize(
    ("sentences", "reason"),
    [
        ([("It was approved.", [3, 500])], "not in the chain"),
        ([("It was approved.", [0])], "not in the chain"),
        ([("It was approved.", [])], "cited no record"),
        ([], "do not hold the answer"),
        ([("x" * 700, [3])], "longer than"),
    ],
)
async def test_an_answer_the_chain_cannot_back_becomes_a_refusal_with_no_citation(
    scene: Scene, sentences: list[tuple[str, list[int]]], reason: str
) -> None:
    run_id = await scene.open_closed_run()
    await scene.fill_chain()
    await scene.ask()
    await scene.drain()
    [job] = scene.jobs()
    accepted = [examiner_answer(*sentences)] if sentences else []
    await scene.agent_done(job, *accepted, run_id=run_id)
    await scene.drain()

    answer = scene.examiner_service.answers["exq_1"]
    assert answer["refused"] is True
    assert answer["sentences"] == []
    assert reason in answer["refusal_reason"]
    [answered] = scene.records("examiner.answered")
    assert answered.data["refused"] is True
    assert answered.data["cited"] == []


async def test_a_chain_that_does_not_verify_is_never_answered_from(
    scene: Scene,
) -> None:
    run_id = await scene.open_closed_run()
    await scene.fill_chain()
    await scene.ask()
    await scene.drain()
    [job] = scene.jobs()
    scene.audit.verify_ok = False
    scene.audit.first_break_seq = 4
    await scene.agent_done(job, examiner_answer(("Band 3.", [3])), run_id=run_id)
    await scene.drain()
    answer = scene.examiner_service.answers["exq_1"]
    assert answer["refused"] is True
    assert "does not verify" in answer["refusal_reason"]
    assert answer["sentences"] == []


async def test_an_examiner_that_fails_on_a_closed_run_is_a_refusal_not_a_dead_letter(
    scene: Scene,
) -> None:
    run_id = await scene.open_closed_run()
    await scene.fill_chain()
    await scene.ask()
    await scene.drain()
    [job] = scene.jobs()
    await scene.agent_failed(job, "guard4_rejected: no attempt left", run_id)
    assert await scene.drain() == [StepKind.EXAMINER_ANSWER]
    answer = scene.examiner_service.answers["exq_1"]
    assert answer["refused"] is True
    assert "guard4_rejected" in answer["refusal_reason"]
    assert "no attempt left" not in answer["refusal_reason"]
    assert (await scene.run(run_id)).state is CaseState.CLOSED


async def test_a_case_h1_never_ran_gets_an_oversight_run_with_the_pins_s1_holds(
    scene: Scene, run_store: CaseRunStore
) -> None:
    await scene.fill_chain(case_id=CASE)
    await scene.ask()
    assert await scene.drain() == [StepKind.EXAMINER_ANSWER]
    [job] = scene.jobs()
    run = await scene.run(job.run_id)
    assert run.state is CaseState.CLOSED
    assert run.refs["oversight"] == "examiner"
    assert run.snapshot.closed_at is not None
    # It is never mistaken for a case in flight.
    assert await run_store.get_by_case_id(CASE) is None
    assert (await run_store.get_any_by_case_id(CASE)) is not None
    # The job reads the versions S1 holds for the case, not the run's placeholder.
    assert job.pins == DEFAULT_PINS
    assert scene.case_service.calls_to("GET", "/record")
    # The closed-case chain is untouched by anything but the two oversight records.
    assert {r.type for r in scene.audit.records} == {"gate.decided", "examiner.asked"}


async def test_a_step_run_twice_answers_once(scene: Scene) -> None:
    run_id = await scene.open_closed_run()
    await scene.fill_chain()
    await scene.ask()
    await scene.drain()
    [job] = scene.jobs()
    await scene.agent_done(job, examiner_answer(("Band 3.", [3])), run_id=run_id)
    await scene.drain()
    [answer_step] = [
        s for s in scene.steps() if s.args["trigger"] == "agent.done.examiner"
    ]
    await scene.runner.run(answer_step)
    assert len(scene.examiner_service.answers) == 1
    assert len(scene.records("examiner.answered")) == 1


# ── global events: handled, but nothing else without a run is forgiven ───────────


async def test_an_unrelated_event_without_a_run_is_still_an_orphan(
    scene: Scene,
) -> None:
    with pytest.raises(OrphanEventError):
        await scene.send(
            "condition.activated",
            {"case_id": "FCRM-2026-9999"},
            case_id="FCRM-2026-9999",
        )
    with pytest.raises(OrphanEventError):
        await scene.send("pack.released", {"pack_id": "p"}, case_id="FCRM-2026-9999")


async def test_a_global_event_needs_the_engine_to_have_oversight(
    engine_factory: Any, session: FakeSession
) -> None:
    engine: CaseflowEngine = engine_factory()
    envelope = Envelope(
        event_id=new_id("evt"),
        type="jurisdiction.list_published",
        occurred_at=utcnow(),
        payload={"version": VERSION, "added": ["Algeria"], "removed": []},
    )
    with pytest.raises(ValueError, match="oversight"):
        await engine.handle(envelope, session)  # type: ignore[arg-type]


async def test_a_malformed_global_event_is_poison_not_a_silent_skip(
    scene: Scene,
) -> None:
    with pytest.raises(ValidationError):
        await scene.send("examiner.asked", {"case_id": CASE}, case_id=CASE)


# ── the watchlist: a list is published, two cases are re-flagged ─────────────────


def _two_candidates(scene: Scene) -> None:
    scene.reflag_service.candidates = [
        {
            "case_id": "FCRM-2026-0144",
            "title": "North Africa trade finance (Algeria)",
            "stage": "review",
            "jurisdiction": "Algeria",
        },
        {
            "case_id": "FCRM-2026-0147",
            "title": "Payroll partner (Namibia)",
            "stage": "assess",
            "jurisdiction": "Namibia",
        },
    ]


async def test_a_published_list_re_flags_two_open_cases_and_an_analyst_acknowledges(
    scene: Scene,
) -> None:
    _two_candidates(scene)

    # --- the event names no case: it gets a harness-level run of its own --------
    await scene.publish_list(["Algeria", "Namibia"])
    run = await scene.run(HARNESS_RUN)
    assert run.snapshot.case_id == f"WATCHLIST-{VERSION}"
    assert run.state is CaseState.CLOSED
    assert run.refs["oversight"] == "watchlist"
    assert await scene.drain() == [StepKind.WATCHLIST_PUBLISH]

    [published] = scene.records("jurisdiction.published")
    assert published.case_id == f"WATCHLIST-{VERSION}"
    assert published.data["added"] == ["Algeria", "Namibia"]
    assert scene.reflag_service.queries == [["Algeria", "Namibia"]]
    [job] = scene.jobs()
    assert (job.agent, job.run_id) == (AgentName.WATCHLIST, HARNESS_RUN)
    assert job.refs == {"list_from": "v2026-06", "list_to": VERSION}
    assert job.pins.jurisdictions_v == "v2026-06"
    assert job.job_id == deterministic_id("job", HARNESS_RUN, "watchlist", VERSION)

    # --- the agent proposes two re-flags and one for a case code never named -----
    await scene.agent_done(
        job,
        reflag_proposal("FCRM-2026-0144", "Algeria"),
        reflag_proposal("FCRM-2026-0147", "Namibia", rerun=False),
        reflag_proposal("FCRM-2026-0001", "Algeria"),
        run_id=HARNESS_RUN,
    )
    assert await scene.drain() == [StepKind.WATCHLIST_PUBLISH]

    rows = scene.reflag_service.rows
    assert {r["case_id"] for r in rows.values()} == {
        "FCRM-2026-0144",
        "FCRM-2026-0147",
    }
    by_case = {r["case_id"]: r for r in rows.values()}
    algeria = by_case["FCRM-2026-0144"]
    assert algeria["proposed_by"] == "agent"
    assert algeria["rerun"] == ["research", "assessment"]
    assert by_case["FCRM-2026-0147"]["rerun"] == ["research"]
    assert algeria["list_entry"]["list_id"] == "FATF"
    assert algeria["list_entry"]["quote"] == "Algeria · increased monitoring"
    assert algeria["proposal_id"] is not None
    assert (algeria["run_id"], algeria["list_from"]) == (HARNESS_RUN, "v2026-06")
    proposed = scene.records("reflag.proposed")
    assert sorted(r.case_id for r in proposed) == [
        "FCRM-2026-0144",
        "FCRM-2026-0147",
    ]
    assert [p.kind for p in scene.audit.proposals] == ["reflag"]

    # one reflag_ack gate per re-flag, on the harness run, about the real case
    gate_docs = [d for d in scene.gates.docs if d["gate_type"] == "reflag_ack"]
    assert {d["_id"] for d in gate_docs} == {r["gate_id"] for r in rows.values()}
    assert {d["case_id"] for d in gate_docs} == set(by_case)
    assert {d["run_id"] for d in gate_docs} == {HARNESS_RUN}
    assert {d["required_role"] for d in gate_docs} == {"fcrm_analyst"}

    # --- an analyst acknowledges one: S1 emits gate.decided, H1 records it ------
    await scene.send(
        "gate.decided",
        {
            "gate_id": algeria["gate_id"],
            "gate_type": "reflag_ack",
            "case_id": "FCRM-2026-0144",
            "run_id": HARNESS_RUN,
            "decision": "acknowledge",
            "reason": "Checked the corridor evidence; no payout leg clears in Algeria.",
            "decided_by": "u-riyer",
            "decided_at": utcnow().isoformat(),
            "role": "fcrm_analyst",
            "subject": {"kind": "reflag", "id": algeria["reflag_id"]},
            "value": {"rerun": True, "jurisdiction": "Algeria"},
        },
        case_id="FCRM-2026-0144",
        run_id=HARNESS_RUN,
    )
    assert await scene.drain() == [StepKind.REFLAG_ACK]
    [acknowledged] = scene.records("reflag.acknowledged")
    assert acknowledged.case_id == "FCRM-2026-0144"
    assert acknowledged.actor == "u-riyer"
    assert acknowledged.data["rerun_requested"] is True
    assert "no payout leg" in acknowledged.data["reason"]
    decided = next(d for d in scene.gates.docs if d["_id"] == algeria["gate_id"])
    assert decided["status"] == GateStatus.DECIDED.value
    # The other re-flag is still waiting, and nothing moved or was scored.
    waiting = next(d for d in scene.gates.docs if d["_id"] != algeria["gate_id"])
    assert waiting["status"] == GateStatus.OPEN.value
    assert (await scene.run(HARNESS_RUN)).state is CaseState.CLOSED
    assert not scene.envelopes("score.computed")


async def test_an_explanation_the_agent_left_out_is_written_by_code(
    scene: Scene,
) -> None:
    _two_candidates(scene)
    await scene.publish_list(["Algeria", "Namibia"])
    await scene.drain()
    [job] = scene.jobs()
    await scene.agent_done(
        job, reflag_proposal("FCRM-2026-0144", "Algeria"), run_id=HARNESS_RUN
    )
    await scene.drain()
    by_case = {r["case_id"]: r for r in scene.reflag_service.rows.values()}
    assert by_case["FCRM-2026-0144"]["proposed_by"] == "agent"
    code = by_case["FCRM-2026-0147"]
    assert code["proposed_by"] == "code"
    assert code["proposal_id"] is None
    assert "Namibia was added to jurisdiction list" in code["reason"]
    assert "synthetic test list" in code["reason"]


async def test_a_watchlist_agent_that_fails_still_re_flags_every_candidate(
    scene: Scene,
) -> None:
    _two_candidates(scene)
    await scene.publish_list(["Algeria", "Namibia"])
    await scene.drain()
    [job] = scene.jobs()
    await scene.agent_failed(job, "guard4_rejected: unsourced", HARNESS_RUN)
    assert await scene.drain() == [StepKind.WATCHLIST_PUBLISH]
    rows = scene.reflag_service.rows.values()
    assert {r["proposed_by"] for r in rows} == {"code"}
    assert len(scene.reflag_service.rows) == 2
    [failure] = scene.records("agent.failed")
    assert failure.data["reason"] == "guard4_rejected"
    assert len(scene.records("reflag.proposed")) == 2
    assert (await scene.run(HARNESS_RUN)).state is CaseState.CLOSED


@pytest.mark.parametrize(
    ("added", "removed"),
    [([], ["Algeria"]), (["Chad"], [])],
    ids=["a removal never re-flags", "no open case names the country"],
)
async def test_a_list_that_touches_no_open_case_runs_no_agent_and_flags_nothing(
    scene: Scene, added: list[str], removed: list[str]
) -> None:
    _two_candidates(scene)
    await scene.publish_list(added, removed)
    assert await scene.drain() == [StepKind.WATCHLIST_PUBLISH]
    assert scene.jobs() == []
    assert scene.reflag_service.rows == {}
    assert [r.type for r in scene.audit.records] == ["jurisdiction.published"]


async def test_a_redelivered_list_event_finds_the_same_harness_run(
    scene: Scene, run_store: CaseRunStore
) -> None:
    _two_candidates(scene)
    await scene.publish_list(["Algeria", "Namibia"])
    await scene.publish_list(["Algeria", "Namibia"])
    # one run for the version; the second event enqueued its own step against it
    first = await run_store.get(HARNESS_RUN)
    assert first is not None
    assert len({s.run_id for s in scene.steps()}) == 1

"""Scenes 2-7 of the walkthrough, driven through the real engine and the real
step handlers (:mod:`fcrm_caseflow.cores.intake`) with fake peers.

Nothing is stubbed on H1's side: ``case.created`` and the S1/H3/H2 events the
walkthrough implies go through :meth:`CaseflowEngine.handle`, every
``harness.step`` it enqueues is run by a real :class:`StepRunner` against
``conftest.FakeCaseService`` / ``FakePolicyService`` (``httpx.MockTransport``)
and :class:`~conftest.RecordingAudit`, and each step's result is applied by
:meth:`CaseflowEngine.complete_step`. At each checkpoint the test asserts the
steps that ran, the gates that opened or closed, the agent jobs dispatched,
what S1 was told and the audit records written.

What the fakes stand in for, by name: S1 (state it would hold after a PO
action is set on ``FakeCaseService`` before the event it publishes), H3
(``ingest.done`` envelopes), H2 (``agent.done`` envelopes).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from conftest import (
    FakeCaseService,
    FakeCollection,
    FakePolicyService,
    FakeSession,
    RecordingAudit,
    agent_done_body,
    category_proposal,
    doc_source,
    extracted_field,
    extraction_proposal,
    fact_flag,
    gap_question,
)
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.cores import default_handlers
from fcrm_caseflow.cores.intake_merge import category_gate_id, gap_gate_id
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore, deterministic_gate_id
from fcrm_caseflow.steps import StepRegistry, StepRunner
from fcrm_caseflow.store import AgentResultStore, CaseRun, CaseRunStore
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import (
    AgentName,
    CaseState,
    ChangeType,
    GateStatus,
    GateType,
    StepKind,
)
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import GateDecided, HarnessStep, JobPayload

CASE = "FCRM-2026-0151"
PRECEDENT = "FCRM-2026-0118"
BC, CT = "doc_bc", "doc_ct"


class Scene:
    """Drives one case through the engine and its steps."""

    def __init__(
        self,
        *,
        engine: CaseflowEngine,
        runner: StepRunner,
        run_store: CaseRunStore,
        gates_collection: FakeCollection,
        outbox_collection: FakeCollection,
        session: FakeSession,
        audit: RecordingAudit,
        case_service: FakeCaseService,
    ) -> None:
        self.engine = engine
        self.runner = runner
        self.run_store = run_store
        self.gates_collection = gates_collection
        self.outbox_collection = outbox_collection
        self.session = session
        self.audit = audit
        self.case_service = case_service
        self.run_id = ""
        self._ran: set[str] = set()

    async def send(
        self, event_type: str, payload: Mapping[str, Any], *, run: bool = True
    ) -> None:
        """Hand one wire event to the engine, as the consumer would."""
        envelope = Envelope(
            event_id=new_id("evt"),
            type=event_type,
            case_id=CASE,
            run_id=self.run_id if run and self.run_id else None,
            occurred_at=utcnow(),
            payload=dict(payload),
        )
        await self.engine.handle(envelope, self.session)  # type: ignore[arg-type]

    async def drain(self) -> list[StepKind]:
        """Run every ``harness.step`` the outbox holds that has not run, and
        any step those steps enqueue, until none is left. Returns the kinds in
        the order they ran.
        """
        ran: list[StepKind] = []
        while True:
            pending = [s for s in self.steps() if s.step_id not in self._ran]
            if not pending:
                return ran
            for step in pending:
                self._ran.add(step.step_id)
                await self.runner.run(step)
                ran.append(step.step)

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

    def jobs(self, agent: AgentName) -> list[JobPayload]:
        jobs = [
            JobPayload.model_validate(e.payload) for e in self.envelopes("agent.run")
        ]
        return [j for j in jobs if j.agent is agent]

    def gates(
        self, gate_type: GateType, status: GateStatus = GateStatus.OPEN
    ) -> list[dict[str, Any]]:
        return [
            d
            for d in self.gates_collection.docs
            if d["gate_type"] == gate_type.value and d["status"] == status.value
        ]

    def record_types(self) -> list[str]:
        return [r.type for r in self.audit.records]

    async def run(self) -> CaseRun:
        run = await self.run_store.get(self.run_id)
        assert run is not None
        return run

    # -- the walkthrough's events -------------------------------------------------

    async def open_case(self) -> None:
        await self.send(
            "case.created",
            {
                "case_id": CASE,
                "actor": "u-pnair",
                "data": {"title": "Consumer remittances US → Mexico"},
            },
            run=False,
        )
        run = await self.run_store.get_by_case_id(CASE)
        assert run is not None
        self.run_id = run.snapshot.run_id

    async def upload(self, doc_id: str, name: str) -> None:
        self.case_service.add_document(doc_id, name)
        await self.send(
            "case.document_stored",
            {
                "case_id": CASE,
                "actor": "u-pnair",
                "doc_id": doc_id,
                "name": name,
                "bucket": "uploads",
                "key": f"{CASE}/{doc_id}/{name}",
                "sha256": "ab" * 32,
                "size_bytes": 2_000_000,
                "content_type": "application/pdf",
                "keep_terms": ["Priya Nair", "Remesas del Norte S.A."],
            },
        )

    async def extracted(self, doc_id: str, pages: int, masked: int) -> None:
        await self.send(
            "ingest.done",
            {
                "job_id": f"ingest_{doc_id}",
                "doc_id": doc_id,
                "canonical_key": f"canonical/{CASE}/{doc_id}.md",
                "anchored_key": f"canonical/{CASE}/{doc_id}.anchored.md",
                "pages": pages,
                "page_map": {"1": [1, 30]},
                "guard1": {"status": "masked", "pii_total": masked},
            },
        )

    async def agent_done(self, job_id: str, *accepted: dict[str, Any]) -> None:
        await self.send("agent.done", agent_done_body(job_id, self.run_id, *accepted))

    async def decide(
        self,
        gate_id: str,
        gate_type: GateType,
        decision: str,
        subject: Mapping[str, str],
        **extra: Any,
    ) -> None:
        payload = GateDecided(
            gate_id=gate_id,
            gate_type=gate_type,
            case_id=CASE,
            run_id=self.run_id,
            decision=decision,
            decided_by="u-pnair",
            decided_at=utcnow(),
            subject=dict(subject),
            **extra,
        )
        await self.send("gate.decided", payload.model_dump(mode="json"))


@pytest.fixture
def scene(
    engine_factory: Any,
    run_store: CaseRunStore,
    gate_store: GateStore,
    gates_collection: FakeCollection,
    outbox_collection: FakeCollection,
    session: FakeSession,
    recording_audit: RecordingAudit,
    case_service: FakeCaseService,
    policy_service: FakePolicyService,
    agent_results: AgentResultStore,
    case_client: Any,
    policy_client: Any,
) -> Scene:
    engine: CaseflowEngine = engine_factory(dict(STEP_FOR), results=agent_results)
    runner = StepRunner(
        run_store,
        StepRegistry(default_handlers()),
        engine,
        recording_audit,
        case=case_client,
        policy=policy_client,
        agent_results=agent_results,
    )
    case_service.add_precedent(
        PRECEDENT,
        "Payout partner onboarding (Guatemala corridor)",
        {
            "F31": ("Transaction limits and velocity", "$2,999 per transaction"),
            "F33": ("Disclosure text reference", "Standard remittance disclosure v3"),
            "F34": ("Monitoring rule coverage", "Remittance rule set v3"),
        },
    )
    policy_service.hits = [
        {
            "id": PRECEDENT,
            "score": 1.0,
            "text": "Payout partner onboarding (Guatemala corridor)",
            "metadata": {
                "title": "Payout partner onboarding (Guatemala corridor)",
                "closed_date": "2026-06-15",
                "text_score": 0.81,
            },
        }
    ]
    return Scene(
        engine=engine,
        runner=runner,
        run_store=run_store,
        gates_collection=gates_collection,
        outbox_collection=outbox_collection,
        session=session,
        audit=recording_audit,
        case_service=case_service,
    )


def _business_case_output() -> list[dict[str, Any]]:
    return [
        extraction_proposal(
            extracted_field(
                "F01", "Priya Nair, Payments", 0.97, source=doc_source(BC, 1, 2)
            ),
            extracted_field(
                "F02",
                "Consumer remittances US to Mexico",
                0.95,
                source=doc_source(BC, 1, 3),
            ),
            extracted_field("F03", "Q1 2027", 0.9, source=doc_source(BC, 1, 9)),
            extracted_field("F20", "US to Mexico", 0.9, source=doc_source(BC, 4, 12)),
            extracted_field("F05", "$48M year 1", 0.7, source=doc_source(BC, 6, 17)),
        ),
        category_proposal("new_geography", 0.9, doc_source(BC, 4, 12)),
        category_proposal("new_product", 0.85, doc_source(BC, 3, 5)),
        gap_question("q_licence", "F10", blocking=True),
        gap_question("q_volume", "F32", blocking=True),
        gap_question("q_channels", "F21", blocking=True),
        fact_flag("Cash pickup is not in the business case", "F21"),
    ]


def _contract_output() -> list[dict[str, Any]]:
    return [
        extraction_proposal(
            extracted_field(
                "F06", "Remesas del Norte S.A.", 0.99, source=doc_source(CT, 1, 2)
            ),
            extracted_field("F23", "MXN", 0.92, source=doc_source(CT, 3, 4)),
        ),
        category_proposal("vendor_onboarding", 0.97, doc_source(CT, 1, 2)),
    ]


async def test_scenes_two_to_seven_run_from_case_created_to_screening(  # noqa: PLR0915 - one scripted walkthrough, sequential by nature
    scene: Scene,
    case_service: FakeCaseService,
    policy_service: FakePolicyService,
) -> None:
    # --- scene 2: a case is created, the run opens ------------------------------
    await scene.open_case()
    assert await scene.drain() == [StepKind.INTAKE_OPEN]
    assert scene.record_types() == ["case.created", "workflow.started"]
    assert [p["stage"] for p in case_service.state_patches] == ["intake"]
    assert case_service.events[0]["type"] == "case.stage_changed"
    assert (await scene.run()).state is CaseState.INTAKE

    # --- scene 3: two documents are uploaded; H3 reads them; the agents run -----
    await scene.upload(BC, "business_case_remittance_MX.pdf")
    await scene.upload(CT, "payout_partner_contract.pdf")
    ingest_runs = scene.envelopes("ingest.run")
    assert [e.payload["doc_id"] for e in ingest_runs] == [BC, CT]
    assert all(
        e.payload["target"]["key_prefix"] == f"canonical/{CASE}/" for e in ingest_runs
    )
    assert await scene.drain() == [StepKind.INTAKE_DOCUMENT_STORED] * 2
    assert scene.record_types().count("document.stored") == 2

    await scene.extracted(BC, pages=14, masked=3)
    assert await scene.drain() == [StepKind.INTAKE_INGESTED]
    assert scene.jobs(AgentName.INTAKE) == []  # contract.pdf is still being read

    await scene.extracted(CT, pages=9, masked=2)
    assert await scene.drain() == [StepKind.INTAKE_INGESTED]
    job_bc, job_ct = scene.jobs(AgentName.INTAKE)
    assert sorted([job_bc.refs["doc_id"], job_ct.refs["doc_id"]]) == [BC, CT]
    assert (job_bc.scope, job_bc.tier.value) == ("full", "cheap")
    assert (await scene.run()).pending_agents == (
        f"intake:{job_bc.job_id}",
        f"intake:{job_ct.job_id}",
    )
    assert scene.record_types().count("guard.input") == 2
    assert {d["status"] for d in case_service.documents.values()} == {"extracted"}
    po_events = [e for e in case_service.events if e["visibility"] == "po"]
    assert [e["type"] for e in po_events] == ["document.extracted"] * 2

    # --- scene 3: the agents' proposals are merged and reach the PO -------------
    by_doc = {
        job_bc.refs["doc_id"]: job_bc.job_id,
        job_ct.refs["doc_id"]: job_ct.job_id,
    }
    await scene.agent_done(by_doc[BC], *_business_case_output())
    assert await scene.drain() == []  # the join waits for the contract's agent
    assert case_service.intake_pushes == []

    await scene.agent_done(by_doc[CT], *_contract_output())
    assert await scene.drain() == [StepKind.INTAKE_PUBLISH]

    [push] = case_service.intake_pushes
    assert [c["change_type"] for c in push["categories"]] == [
        "vendor_onboarding",
        "new_geography",
        "new_product",
    ]
    assert [v["field_id"] for v in push["values"]] == [
        "F01",
        "F02",
        "F03",
        "F05",
        "F06",
        "F20",
        "F23",
    ]
    assert push["precedent"]["case_id"] == PRECEDENT
    assert [g["blocking"] for g in push["gaps"]] == [True, True, True]
    assert len(policy_service.calls) == 1
    assert len(scene.gates(GateType.PO_CONFIRM_CATEGORY)) == 3
    assert len(scene.gates(GateType.PO_ANSWER_GAPS)) == 3
    assert len(scene.gates(GateType.PO_CONFIRM_VALUE)) == 7
    assert len(scene.gates(GateType.PO_REUSE)) == 1
    assert [e["type"] for e in case_service.events][-1] == "intake.proposals_ready"
    for record_type in (
        "proposal.extraction",
        "proposal.category",
        "proposal.gaps",
        "precedent.matched",
        "fact.flagged",
    ):
        assert scene.record_types().count(record_type) == 1, record_type
    assert (await scene.run()).pending_agents == ()

    # --- scene 4: the PO confirms each change type, one gate each ---------------
    run_id = scene.run_id
    types = [
        ChangeType.VENDOR_ONBOARDING,
        ChangeType.NEW_GEOGRAPHY,
        ChangeType.NEW_PRODUCT,
    ]
    for change_type in types[:2]:
        await scene.decide(
            category_gate_id(run_id, change_type),
            GateType.PO_CONFIRM_CATEGORY,
            "confirm",
            {"kind": "change_type", "id": change_type.value},
        )
        assert await scene.drain() == [StepKind.INTAKE_PO_ACTION]
    assert (await scene.run()).state is CaseState.INTAKE
    assert len(scene.gates(GateType.PO_CONFIRM_CATEGORY)) == 1
    assert len(scene.jobs(AgentName.INTAKE)) == 2  # no new agent run yet

    case_service.change_types = [t.value for t in types]
    case_service.required_field_ids = [f"F{n:02d}" for n in range(1, 19)]
    await scene.decide(
        category_gate_id(run_id, types[2]),
        GateType.PO_CONFIRM_CATEGORY,
        "confirm",
        {"kind": "change_type", "id": types[2].value},
    )
    assert await scene.drain() == [StepKind.INTAKE_FIELDSET_RESOLVED]
    assert len(scene.gates(GateType.PO_CONFIRM_CATEGORY)) == 0
    assert len(scene.gates(GateType.PO_CONFIRM_CATEGORY, GateStatus.DECIDED)) == 3
    assert scene.record_types().count("gate.decided") == 3
    assert "fieldset.resolved" in scene.record_types()
    fieldset = [r for r in scene.audit.records if r.type == "fieldset.resolved"][0]
    assert fieldset.summary == "Required field set = union of 3 types = 18 fields"

    # Blocking questions are still open, so the intake agent is asked for them
    # again, on a document it can read.
    [questions_job] = [
        j for j in scene.jobs(AgentName.INTAKE) if j.scope == "questions_only"
    ]
    assert questions_job.refs == {"doc_id": (await scene.run()).refs["intake_docs"][0]}

    # --- scene 5-6: the questions round returns; nothing is opened twice --------
    gates_before = len(scene.gates_collection.docs)
    await scene.agent_done(questions_job.job_id)
    assert await scene.drain() == [StepKind.INTAKE_PUBLISH]
    assert len(case_service.intake_pushes) == 2
    assert case_service.intake_pushes[1] == push
    assert len(scene.gates_collection.docs) == gates_before
    assert len(scene.gates(GateType.PO_CONFIRM_CATEGORY, GateStatus.DECIDED)) == 3

    # --- scene 5: the PO reuses three values from the precedent -----------------
    await scene.decide(
        deterministic_gate_id(run_id, GateType.PO_REUSE, PRECEDENT),
        GateType.PO_REUSE,
        "reuse",
        {"kind": "precedent", "id": PRECEDENT},
        after={"field_ids": ["F31", "F33", "F34"]},
    )
    assert await scene.drain() == [StepKind.INTAKE_PO_ACTION]

    # --- scene 6: the PO answers the blocking questions --------------------------
    for question in ("q_licence", "q_volume", "q_channels"):
        await scene.decide(
            gap_gate_id(run_id, question),
            GateType.PO_ANSWER_GAPS,
            "answer",
            {"kind": "gap", "id": question},
        )
        assert await scene.drain() == [StepKind.INTAKE_PO_ACTION]
    assert scene.gates(GateType.PO_ANSWER_GAPS) == []

    # --- scene 7: she corrects the go-live date; the original is kept -----------
    await scene.decide(
        deterministic_gate_id(run_id, GateType.PO_CONFIRM_VALUE, "F03"),
        GateType.PO_CONFIRM_VALUE,
        "correct",
        {"kind": "field", "id": "F03"},
        reason="Launch moved after the partner's licence review.",
        before={"value": "Q1 2027"},
        after={"value": "February 2027"},
    )
    assert await scene.drain() == [StepKind.INTAKE_PO_ACTION]

    # --- scene 7: submit -> the four checks, R5 triage, screening ---------------
    case_service.fields = [
        {"field_id": "F01", "label": "Owner", "value": "Priya", "origin": "extracted"},
        {
            "field_id": "F02",
            "label": "Description",
            "value": "x",
            "origin": "extracted",
        },
        {
            "field_id": "F03",
            "label": "Go-live date",
            "value": "February 2027",
            "origin": "owner_declared",
            "original_value": "Q1 2027",
        },
    ]
    await scene.send(
        "case.submitted",
        {
            "case_id": CASE,
            "actor": "u-pnair",
            "submission_version": 1,
            "change_types": [t.value for t in types],
            "filled": 15,
            "required": 18,
            "checks": [
                {"id": c, "ok": True}
                for c in (
                    "completion",
                    "always_required",
                    "types_confirmed",
                    "no_conflicts",
                )
            ],
            "four_submit_checks_passed": True,
        },
    )
    assert await scene.drain() == [StepKind.SUBMIT_RECORD, StepKind.TRIAGE_ASSIGN]

    final = await scene.run()
    assert final.state is CaseState.SCREENING
    assert final.pending_steps == ()
    assert case_service.stage == "screening"
    assert case_service.assignments[0]["analyst_id"] == "u-riyer"  # 4 open: the fewest
    assert case_service.analyst == {"id": "u-riyer", "name": "Rahul Iyer"}
    last_event = case_service.events[-1]
    assert (last_event["type"], last_event["visibility"]) == ("case.assigned", "po")
    assert last_event["detail"] == {"analyst_name": "Rahul Iyer"}
    # The chain went on by itself: screening is dispatched, no human in between.
    assert [j.agent for j in scene.jobs(AgentName.SCREENING)] == [AgentName.SCREENING]

    # --- the audit trail the PO's intake left -----------------------------------
    types_in_order = scene.record_types()
    for required in (
        "case.created",
        "workflow.started",
        "document.stored",
        "guard.input",
        "proposal.extraction",
        "proposal.category",
        "proposal.gaps",
        "precedent.matched",
        "gate.decided",
        "fieldset.resolved",
        "values.reused",
        "owner.answered",
        "fact.flagged",
        "owner.corrected",
        "gate.validation",
        "case.submitted",
        "triage.assigned",
        "eval.recorded",
    ):
        assert required in types_in_order, required
    # The walkthrough's order, wherever its scenes fix one.
    sequence = [
        "case.created",
        "workflow.started",
        "document.stored",
        "guard.input",
        "proposal.extraction",
        "proposal.category",
        "gate.decided",
        "fieldset.resolved",
        "values.reused",
        "owner.answered",
        "owner.corrected",
        "gate.validation",
        "case.submitted",
        "triage.assigned",
        "eval.recorded",
    ]
    positions = [types_in_order.index(t) for t in sequence]
    assert positions == sorted(positions)
    # No type is written twice by two writers: the engine's generic records of
    # these two types would duplicate the steps'.
    assert types_in_order.count("case.created") == 1
    assert types_in_order.count("case.submitted") == 1
    assert scene.audit.records[0].summary == "Case created by Priya Nair"
    # Every step record carries a key derived from its step, so a redelivered
    # step cannot chain a record twice.
    step_keys = [k for k in scene.audit.keys if k is not None]
    assert len(step_keys) == len(set(step_keys))


async def test_a_redelivered_step_changes_nothing_the_second_time(
    scene: Scene, case_service: FakeCaseService
) -> None:
    await scene.open_case()
    [step] = scene.steps()
    await scene.runner.run(step)
    records = len(scene.audit.records)
    patches = len(case_service.state_patches)

    await scene.runner.run(step)  # the broker redelivers; the step is done

    assert len(scene.audit.records) == records
    assert len(case_service.state_patches) == patches
    assert (await scene.run()).pending_steps == ()


# --- ADR-72: the owner chooses the change types herself ----------------------------


def _passed_checks(types: list[str], filled: int, required: int) -> dict[str, Any]:
    """``case.submitted`` as S1 sends it once the four submit checks passed."""
    return {
        "case_id": CASE,
        "actor": "u-pnair",
        "submission_version": 1,
        "change_types": types,
        "filled": filled,
        "required": required,
        "checks": [
            {"id": c, "ok": True}
            for c in (
                "completion",
                "always_required",
                "types_confirmed",
                "no_conflicts",
            )
        ],
        "four_submit_checks_passed": True,
    }


def _types_changed(change_type: str, types: list[str]) -> dict[str, Any]:
    """``case.types_changed`` for a type the owner added."""
    return {
        "case_id": CASE,
        "actor": "u-pnair",
        "change_type": change_type,
        "action": "added",
        "change_types": list(types),
    }


async def test_a_manual_case_runs_to_screening_with_no_document_and_no_proposal(
    scene: Scene, case_service: FakeCaseService
) -> None:
    """The manual path: the owner's types arrive on ``case.created``, she adds
    one more, fills the fields by hand and submits. No document is read, no
    intake agent runs, no PO gate opens, and the trail still says who chose
    which type and the field set each choice resolved.
    """
    case_service.change_types = ["new_product", "new_geography"]
    case_service.required_field_ids = [f"F{n:02d}" for n in range(1, 14)]
    await scene.send(
        "case.created",
        {
            "case_id": CASE,
            "actor": "u-pnair",
            "data": {
                "title": "Consumer remittances US → Mexico",
                "change_types": ["new_product", "new_geography"],
                "intake_mode": "manual",
            },
        },
        run=False,
    )
    run = await scene.run_store.get_by_case_id(CASE)
    assert run is not None
    scene.run_id = run.snapshot.run_id
    assert await scene.drain() == [StepKind.INTAKE_OPEN]
    assert scene.record_types() == [
        "case.created",
        "workflow.started",
        "case.types_changed",
        "fieldset.resolved",
    ]

    # She adds a type the assistant never proposed: recorded, nothing moves.
    case_service.change_types.append("vendor_onboarding")
    case_service.required_field_ids = [f"F{n:02d}" for n in range(1, 19)]
    await scene.send(
        "case.types_changed",
        _types_changed("vendor_onboarding", case_service.change_types),
    )
    assert await scene.drain() == [StepKind.INTAKE_TYPES_CHANGED]
    assert (await scene.run()).state is CaseState.INTAKE
    resolved = [r for r in scene.audit.records if r.type == "fieldset.resolved"]
    assert resolved[-1].summary == "Required field set = union of 3 types = 18 fields"
    assert scene.gates_collection.docs == []
    assert scene.jobs(AgentName.INTAKE) == []

    # She submits with no document: triage and screening follow as usual.
    await scene.send(
        "case.submitted", _passed_checks(case_service.change_types, 18, 18)
    )
    assert await scene.drain() == [StepKind.SUBMIT_RECORD, StepKind.TRIAGE_ASSIGN]
    final = await scene.run()
    assert final.state is CaseState.SCREENING
    assert scene.jobs(AgentName.INTAKE) == []
    assert [j.agent for j in scene.jobs(AgentName.SCREENING)] == [AgentName.SCREENING]
    grade = [r for r in scene.audit.records if r.type == "eval.recorded"][0]
    assert grade.data["types_proposed"] == 0
    types_in_order = scene.record_types()
    assert types_in_order.count("case.types_changed") == 2
    assert "document.stored" not in types_in_order
    assert "proposal.category" not in types_in_order


async def test_a_type_the_owner_added_first_leaves_no_gate_dangling(
    scene: Scene, case_service: FakeCaseService
) -> None:
    """AI-assisted, but the owner added *new geography* before the assistant
    read anything. When it proposes that type too, no gate opens for it, so
    confirming the two other types resolves the field set as usual.
    """
    await scene.open_case()
    await scene.drain()
    case_service.change_types = ["new_geography"]
    await scene.send(
        "case.types_changed", _types_changed("new_geography", ["new_geography"])
    )
    assert await scene.drain() == [StepKind.INTAKE_TYPES_CHANGED]

    await scene.upload(BC, "business_case_remittance_MX.pdf")
    await scene.upload(CT, "payout_partner_contract.pdf")
    await scene.drain()
    await scene.extracted(BC, pages=14, masked=3)
    await scene.extracted(CT, pages=9, masked=2)
    await scene.drain()
    job_bc, job_ct = scene.jobs(AgentName.INTAKE)
    by_doc = {
        job_bc.refs["doc_id"]: job_bc.job_id,
        job_ct.refs["doc_id"]: job_ct.job_id,
    }
    await scene.agent_done(by_doc[BC], *_business_case_output())
    await scene.agent_done(by_doc[CT], *_contract_output())
    assert await scene.drain() == [StepKind.INTAKE_PUBLISH]

    [push] = case_service.intake_pushes
    assert [c["change_type"] for c in push["categories"]] == [
        "vendor_onboarding",
        "new_product",
    ]
    assert len(scene.gates(GateType.PO_CONFIRM_CATEGORY)) == 2

    run_id = scene.run_id
    await scene.decide(
        category_gate_id(run_id, ChangeType.VENDOR_ONBOARDING),
        GateType.PO_CONFIRM_CATEGORY,
        "confirm",
        {"kind": "change_type", "id": "vendor_onboarding"},
    )
    assert await scene.drain() == [StepKind.INTAKE_PO_ACTION]
    case_service.change_types = ["new_geography", "vendor_onboarding", "new_product"]
    case_service.required_field_ids = [f"F{n:02d}" for n in range(1, 19)]
    await scene.decide(
        category_gate_id(run_id, ChangeType.NEW_PRODUCT),
        GateType.PO_CONFIRM_CATEGORY,
        "confirm",
        {"kind": "change_type", "id": "new_product"},
    )
    assert await scene.drain() == [StepKind.INTAKE_FIELDSET_RESOLVED]
    assert scene.gates(GateType.PO_CONFIRM_CATEGORY) == []
    resolved = [r for r in scene.audit.records if r.type == "fieldset.resolved"]
    assert resolved[-1].summary == "Required field set = union of 3 types = 18 fields"

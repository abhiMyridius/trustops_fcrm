"""Scenes 8-16 of the walkthrough, driven through the real engine and the real step
handlers (:mod:`fcrm_caseflow.cores`) with fake peers: from the submitted case to
the pack released to the committee.

Nothing is stubbed on H1's side. Every wire event goes through
:meth:`CaseflowEngine.handle`, every ``harness.step`` it enqueues is run by a real
:class:`StepRunner` against the fake S1, S2 (the real scoring engine), S3, S4 and S5
in ``conftest``, and each step's result is applied by
:meth:`CaseflowEngine.complete_step`. At each checkpoint the test asserts the steps
that ran, the gates that opened or closed, the agent jobs dispatched, what the peers
were asked and the audit records written.

What the fakes stand in for, by name: S1 (the state it would hold after an analyst
or owner action is set on ``FakeCaseService`` before the event it publishes), H2
(``agent.done`` envelopes) and H3 (``ingest.done``).

Numbers are the walkthrough's, because S2 here is the real engine over the seed's
inputs: 9.8 Band 2 with a boundary flag; overriding CTL-T01 7.6 to 4.5 gives 10.4
Band 3.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

import pytest
from conftest import (
    FakeCaseService,
    FakeCollection,
    FakeDecisionService,
    FakePolicyService,
    FakeScoringService,
    FakeSession,
    RecordingAudit,
    agent_done_body,
    business_summary,
    completeness_report,
    control_proposal,
    critic_finding,
    document_stored_payload,
    drafted_condition,
    drafted_question,
    narrative_text,
    screening_proposal,
)
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.cores import default_handlers
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore, deterministic_gate_id
from fcrm_caseflow.steps import StepRegistry, StepRunner
from fcrm_caseflow.store import AgentResultStore, CaseRun, CaseRunStore
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import AgentName, CaseState, GateStatus, GateType, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import GateDecided, HarnessStep, JobPayload
from fcrm_seed.loader import load_raw

CASE = "FCRM-2026-0151"
VOCABULARY = {str(r["type"]) for r in load_raw()["demo"]["audit"]}
DOC = "doc_dd"
REASON = "Audit predates the corridor arrangement and covers domestic scope only."
ANALYST = "u-riyer"


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
        """Run every ``harness.step`` the outbox holds that has not run, and any
        step those steps enqueue, until none is left; the kinds in order.
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
        """The walkthrough's records, in order: the engine's own per-transition
        records (``internal.assess_dispatch``, ``agent.done.screening``...) are
        left out, as the demo's 67 are a different vocabulary from them.
        """
        return [r.type for r in self.audit.records if r.type in VOCABULARY]

    async def run(self) -> CaseRun:
        run = await self.run_store.get(self.run_id)
        assert run is not None
        return run

    # -- the walkthrough's events -------------------------------------------------

    async def open_case(self) -> None:
        await self.send(
            "case.created",
            {"case_id": CASE, "actor": "u-pnair", "data": {"title": "Remittances"}},
            run=False,
        )
        run = await self.run_store.get_by_case_id(CASE)
        assert run is not None
        self.run_id = run.snapshot.run_id

    async def submit(self) -> None:
        await self.send(
            "case.submitted",
            {
                "case_id": CASE,
                "actor": "u-pnair",
                "submission_version": 1,
                "filled": 15,
                "required": 18,
                "four_submit_checks_passed": True,
                "checks": [{"id": n, "ok": True} for n in "abcd"],
            },
        )

    async def agent_done(self, agent: AgentName, *accepted: dict[str, Any]) -> str:
        """The newest job of ``agent`` reports done with ``accepted``."""
        job = self.jobs(agent)[-1]
        await self.send(
            "agent.done",
            agent_done_body(job.job_id, self.run_id, *accepted, agent=agent.value),
        )
        return job.job_id

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
            decided_by=ANALYST,
            decided_at=utcnow(),
            subject=dict(subject),
            **extra,
        )
        await self.send("gate.decided", payload.model_dump(mode="json"))

    async def decide_item(
        self,
        item_id: str,
        decision: str,
        status: str,
        *,
        value: Optional[dict[str, Any]] = None,
        reason: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """The analyst decides one item: S1 stores the decision, then publishes
        ``gate.decided``.
        """
        row = self.case_service.items[item_id]
        self.case_service.decide_item(item_id, status, value=value, reason=reason)
        await self.decide(
            row["gate_id"],
            GateType.REVIEW_ITEM,
            decision,
            {"kind": row["kind"], "id": item_id},
            reason=reason,
            item_kind=row["kind"],
            **extra,
        )

    async def acknowledge_all(self) -> None:
        for gate in self.gates(GateType.ACK):
            await self.decide(
                gate["_id"],
                GateType.ACK,
                "acknowledge",
                gate["subject"],
                reason="Band change and boundary flag reviewed.",
            )
            await self.drain()


@pytest.fixture
def scene(
    engine_factory: Any,
    run_store: CaseRunStore,
    gate_store: GateStore,
    gates_collection: FakeCollection,
    outbox_collection: FakeCollection,
    session: FakeSession,
    recording_audit: RecordingAudit,
    assessed_case: FakeCaseService,
    policy_service: FakePolicyService,
    agent_results: AgentResultStore,
    case_client: Any,
    policy_client: Any,
    scoring_client: Any,
    decision_client: Any,
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
        scoring=scoring_client,
        decision=decision_client,
        gates=gate_store,
    )
    return Scene(
        engine=engine,
        runner=runner,
        run_store=run_store,
        gates_collection=gates_collection,
        outbox_collection=outbox_collection,
        session=session,
        audit=recording_audit,
        case_service=assessed_case,
    )


def _first_round_assessment(output: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The assessment agent's first output has no planned control: the owner's
    answer is what brings the corridor review to light (scene 11).
    """
    return [o for o in output if not o.get("planned")]


async def _to_review(
    scene: Scene,
    demo_research_output: list[dict[str, Any]],
    demo_assessment_output: list[dict[str, Any]],
) -> None:
    """Run the case from submission to the start of review (scenes 7-11)."""
    case_service = scene.case_service
    await scene.open_case()
    await scene.drain()
    await scene.submit()
    assert await scene.drain() == [StepKind.SUBMIT_RECORD, StepKind.TRIAGE_ASSIGN]
    await scene.agent_done(AgentName.SCREENING, screening_proposal(False))
    assert await scene.drain() == [StepKind.SCREENING_PUBLISH, StepKind.ASSESS_OPEN]
    await scene.agent_done(AgentName.RESEARCH, *demo_research_output)
    await scene.agent_done(
        AgentName.ASSESSMENT, *_first_round_assessment(demo_assessment_output)
    )
    assert await scene.drain() == [StepKind.ASSESS_PUBLISH]
    await scene.agent_done(
        AgentName.CRITIC,
        critic_finding("Vendor due-diligence rating 7.6 is 2.7 above the 4.9 average"),
        critic_finding("Only evidence is a 2023 audit", "evidence_recency", "high"),
    )
    assert await scene.drain() == [StepKind.CRITIC_PUBLISH]
    await scene.agent_done(
        AgentName.NARRATIVE,
        completeness_report(),
        drafted_question("q1", "Is a corridor-specific review planned?"),
        drafted_question("q2", "Which agents are onboarded?"),
    )
    assert await scene.drain() == [StepKind.COMPLETENESS_PUBLISH]
    completeness_gate = deterministic_gate_id(
        scene.run_id, GateType.COMPLETENESS, "completeness"
    )
    await scene.decide(
        completeness_gate,
        GateType.COMPLETENESS,
        "ask",
        {},
        after={"round": 1, "questions": 2},
    )
    assert await scene.drain() == [StepKind.CLARIFICATION_RECORD]
    # the owner answers and adds the scope letter
    case_service.add_document(DOC, "vendor_DD_scope_letter_2026.pdf")
    case_service.submission_version = 2
    await scene.send(
        "case.document_stored", document_stored_payload(DOC, raised_in_round=1)
    )
    await scene.send(
        "case.answered",
        {
            "case_id": CASE,
            "actor": "u-pnair",
            "round": 1,
            "submission_version": 2,
            "changed_field_ids": ["F11", "F21"],
            "added_document_ids": [DOC],
            "thread_ids": ["thr_1", "thr_2"],
        },
    )
    await scene.drain()
    case_service.documents[DOC]["status"] = "extracted"
    await scene.send(
        "ingest.done",
        {
            "job_id": "ingest_dd",
            "doc_id": DOC,
            "canonical_key": f"canonical/{CASE}/{DOC}.md",
            "anchored_key": f"canonical/{CASE}/{DOC}.anchored.md",
            "pages": 2,
            "page_map": {"1": [1, 20]},
            "guard1": {"status": "clean", "pii_total": 0},
        },
    )
    await scene.drain()
    await scene.agent_done(AgentName.RESEARCH)
    await scene.agent_done(
        AgentName.ASSESSMENT,
        control_proposal(
            "CTL-T01",
            "channel",
            None,
            planned=True,
            evidence="vendor_DD_scope_letter_2026.pdf",
            evidence_date="",
        ),
    )
    await scene.drain()
    # D-129: the analyst resolves the owner's answers; the round is done
    await scene.send(
        "case.clarification_resolved",
        {
            "case_id": CASE,
            "actor": "u-riyer",
            "round": 1,
            "thread_ids": ["thr_1", "thr_2"],
        },
    )
    await scene.drain()


async def _decide_everything_but_the_ratings(scene: Scene) -> None:
    """The analyst decides every open item gate, one call each (rule 2): clauses
    accepted, factors and controls confirmed, the critic's findings explained;
    CTL-T01 is the one override.
    """
    items = scene.case_service.items
    for item_id, row in list(items.items()):
        if row["kind"] == "gap" or row["status"] != "proposed":
            continue
        if row["supersedes"] is not None:
            continue
        if row["kind"] == "clause":
            await scene.decide_item(item_id, "accept", "confirmed")
        elif row["kind"] == "critic_finding":
            await scene.decide_item(
                item_id,
                "explain",
                "explained",
                reason="The rating reflects the evidence we hold today.",
            )
        elif (
            row["kind"] == "control"
            and row["subject"]["control_id"] == "CTL-T01"
            and not row["subject"]["planned"]
        ):
            await scene.decide_item(
                item_id,
                "override",
                "overridden",
                value={"rating": 4.5},
                reason=REASON,
                before={"rating": 7.6},
                after={"rating": 4.5},
                rating_or_control_changed=True,
            )
        else:
            await scene.decide_item(item_id, "confirm", "confirmed")
        await scene.drain()


# ── the scenes ──────────────────────────────────────────────────────────────


async def test_scenes_eight_to_eleven_assessment_critic_completeness_and_the_rerun(
    scene: Scene,
    assessed_case: FakeCaseService,
    scoring_service: FakeScoringService,
    demo_research_output: list[dict[str, Any]],
    demo_assessment_output: list[dict[str, Any]],
) -> None:
    # --- scene 7 -> 8: submitted, triaged; the screening agent is dispatched ----
    await scene.open_case()
    await scene.drain()
    await scene.submit()
    assert await scene.drain() == [StepKind.SUBMIT_RECORD, StepKind.TRIAGE_ASSIGN]
    run = await scene.run()
    assert run.state is CaseState.SCREENING
    assert run.refs["domains"] == ["channel", "geography", "product"]
    assert run.refs["submission_version"] == 1
    [screening] = scene.jobs(AgentName.SCREENING)
    assert screening.refs == {
        "domains": ["channel", "geography", "product"],
        "submission_version": 1,
    }

    # --- scene 8: screening says no fast lane; research and assessment run ------
    await scene.agent_done(AgentName.SCREENING, screening_proposal(False))
    assert await scene.drain() == [StepKind.SCREENING_PUBLISH, StepKind.ASSESS_OPEN]
    assert (await scene.run()).state is CaseState.ASSESS
    assert scene.gates(GateType.FAST_LANE_CONFIRM) == []
    [research] = scene.jobs(AgentName.RESEARCH)
    [assessed] = scene.jobs(AgentName.ASSESSMENT)
    assert research.refs["domains"] == assessed.refs["domains"]
    assert (await scene.run()).pending_agents == ("research", "assessment")
    assert scene.record_types()[-2:] == ["screening.proposed", "workflow.stage"]
    assert assessed_case.stage == "assess"

    # --- scene 8-9: both done -> items, one gate each, the score, the boundary --
    await scene.agent_done(AgentName.RESEARCH, *demo_research_output)
    assert await scene.drain() == []  # the join waits for the assessment agent
    await scene.agent_done(
        AgentName.ASSESSMENT, *_first_round_assessment(demo_assessment_output)
    )
    assert await scene.drain() == [StepKind.ASSESS_PUBLISH]
    run = await scene.run()
    assert run.state is CaseState.CRITIC
    assert run.band == 2
    assert run.refs["score_summary"]["composite"] == 9.8
    assert (
        len(scene.gates(GateType.REVIEW_ITEM)) == 4 + 3 + 13
    )  # clauses, factors, controls
    [boundary] = scene.gates(GateType.ACK)
    assert boundary["subject"] == {"kind": "ack", "id": "boundary"}
    assert scene.record_types()[-4:] == [
        "proposal.research",
        "proposal.assessment",
        "score.computed",
        "boundary.flagged",
    ]
    [score_request] = scoring_service.requests
    assert sum(len(d["controls"]) for d in score_request["domains"]) == 13
    assert [p for p in assessed_case.state_patches][-1]["stage"] == "critic"
    [critic_job] = scene.jobs(AgentName.CRITIC)
    assert critic_job.refs["score_summary"]["composite"] == 9.8

    # --- scene 9: the critic; the completeness report -------------------------
    await scene.agent_done(
        AgentName.CRITIC,
        critic_finding("Vendor due-diligence rating 7.6 is 2.7 above the 4.9 average"),
        critic_finding("Only evidence is a 2023 audit", "evidence_recency", "high"),
    )
    assert await scene.drain() == [StepKind.CRITIC_PUBLISH]
    assert (await scene.run()).state is CaseState.COMPLETENESS
    assert len(scene.gates(GateType.REVIEW_ITEM)) == 20 + 2
    [narrative] = scene.jobs(AgentName.NARRATIVE)
    assert narrative.scope == "completeness"
    await scene.agent_done(
        AgentName.NARRATIVE,
        completeness_report(),
        drafted_question("q1"),
        drafted_question("q2"),
    )
    assert await scene.drain() == [StepKind.COMPLETENESS_PUBLISH]
    [completeness] = scene.gates(GateType.COMPLETENESS)
    assert completeness["_id"] == assessed_case.views["completeness"][0]["gate_id"]

    # --- scene 10: the analyst asks one round; the clock pauses -----------------
    await scene.decide(
        completeness["_id"],
        GateType.COMPLETENESS,
        "ask",
        {},
        after={"round": 1, "questions": 2},
    )
    assert await scene.drain() == [StepKind.CLARIFICATION_RECORD]
    run = await scene.run()
    assert run.state is CaseState.CLARIFICATION
    assert run.snapshot.flags.round_open
    assert assessed_case.stage == "clarification"
    assert scene.record_types()[-1] == "clarification.sent"

    # --- scene 11: the owner answers and adds a document ------------------------
    assessed_case.add_document(DOC, "vendor_DD_scope_letter_2026.pdf")
    assessed_case.submission_version = 2
    await scene.send(
        "case.document_stored", document_stored_payload(DOC, raised_in_round=1)
    )
    [ingest] = [e for e in scene.envelopes("ingest.run") if e.payload["doc_id"] == DOC]
    assert ingest.payload["purpose"] == "case_document"
    await scene.send(
        "case.answered",
        {
            "case_id": CASE,
            "actor": "u-pnair",
            "round": 1,
            "submission_version": 2,
            "changed_field_ids": ["F11", "F21"],
            "added_document_ids": [DOC],
            "thread_ids": ["thr_1", "thr_2"],
        },
    )
    kinds = await scene.drain()
    assert kinds == [StepKind.INTAKE_DOCUMENT_STORED, StepKind.RERUN_RECORD]
    assert len(scene.jobs(AgentName.RESEARCH)) == 1  # the re-run waits for the document
    assert "owner.answered" in scene.record_types()
    assert "agent.rerun" not in scene.record_types()

    assessed_case.documents[DOC]["status"] = "extracted"
    await scene.send(
        "ingest.done",
        {
            "job_id": "ingest_dd",
            "doc_id": DOC,
            "canonical_key": f"canonical/{CASE}/{DOC}.md",
            "anchored_key": f"canonical/{CASE}/{DOC}.anchored.md",
            "pages": 2,
            "page_map": {"1": [1, 20]},
            "guard1": {"status": "clean", "pii_total": 0},
        },
    )
    assert await scene.drain() == [StepKind.INTAKE_INGESTED, StepKind.RERUN_RECORD]
    # F11 and F21 are not what research's queries are worded from: only assessment
    # re-runs (the join completes with its one member), research's clauses stand.
    rerun_assessment = scene.jobs(AgentName.ASSESSMENT)[-1]
    assert len(scene.jobs(AgentName.RESEARCH)) == 1
    assert rerun_assessment.scope == "changed_fields"
    assert rerun_assessment.changed_fields == ["F11", "F21"]
    assert rerun_assessment.refs["submission_version"] == 2
    assert scene.record_types()[-1] == "agent.rerun"

    # the re-run proposes the planned corridor review; the score does not move
    await scene.agent_done(
        AgentName.ASSESSMENT,
        control_proposal(
            "CTL-T01",
            "channel",
            None,
            planned=True,
            evidence="vendor_DD_scope_letter_2026.pdf",
            evidence_date="",
        ),
    )
    assert await scene.drain() == [StepKind.RERUN_PUBLISH]
    run = await scene.run()
    # D-129: the re-run landed, but the analyst has not resolved the answers
    assert run.state is CaseState.CLARIFICATION
    assert run.snapshot.flags.round_open is True
    await scene.send(
        "case.clarification_resolved",
        {
            "case_id": CASE,
            "actor": "u-riyer",
            "round": 1,
            "thread_ids": ["thr_1", "thr_2"],
        },
    )
    assert await scene.drain() == [StepKind.STATE_PUSH]
    run = await scene.run()
    assert run.state is CaseState.REVIEW
    assert run.snapshot.flags.round_open is False
    assert run.refs["score_summary"]["composite"] == 9.8
    assert len(scene.gates(GateType.REVIEW_ITEM)) == 22 + 1  # the planned control
    assert len(scene.gates(GateType.ACK)) == 1  # no new acknowledgement
    assert len(scoring_service.requests) == 2
    last_request = scoring_service.requests[-1]
    assert sum(len(d["controls"]) for d in last_request["domains"]) == 13
    assert assessed_case.stage == "review"


async def test_scenes_twelve_to_sixteen_review_override_narrative_lock_and_release(
    scene: Scene,
    assessed_case: FakeCaseService,
    scoring_service: FakeScoringService,
    decision_service: FakeDecisionService,
    recording_audit: RecordingAudit,
    demo_research_output: list[dict[str, Any]],
    demo_assessment_output: list[dict[str, Any]],
) -> None:
    await _to_review(scene, demo_research_output, demo_assessment_output)
    prior = len(recording_audit.records)

    # --- scenes 12-14: Gate A, one item at a time; the override; the rescore ----
    await _decide_everything_but_the_ratings(scene)
    run = await scene.run()
    assert run.state is CaseState.REVIEW, "two acknowledgements are still open"
    assert run.band == 3
    assert run.refs["score_summary"]["composite"] == 10.4
    assert scene.gates(GateType.REVIEW_ITEM) == []
    assert sorted(g["subject"]["id"] for g in scene.gates(GateType.ACK)) == [
        "band_change",
        "boundary",
    ]
    [rescore] = [v for v in assessed_case.views["score"] if v["purpose"] == "confirmed"]
    assert rescore["previous"] == {"composite": 9.8, "band": "2"}
    assert rescore["score"]["composite"] == 10.4
    assert rescore["band_changed"]
    overrides = [
        r for r in recording_audit.records[prior:] if r.type == "human.override"
    ]
    assert len(overrides) == 1
    assert overrides[0].data["reason"] == REASON
    types = [r.type for r in recording_audit.records[prior:]]
    assert types.index("human.override") < types.index("score.recomputed")
    assert types.count("gate.decided") == 22  # every other item, one record each

    # --- R3: nothing leaves review until both acknowledgements are done ----------
    boundary_gate = next(
        g for g in scene.gates(GateType.ACK) if g["subject"]["id"] == "boundary"
    )
    await scene.decide(
        boundary_gate["_id"],
        GateType.ACK,
        "acknowledge",
        boundary_gate["subject"],
        reason="Reviewed.",
    )
    assert await scene.drain() == [StepKind.REVIEW_ACK]
    assert (await scene.run()).state is CaseState.REVIEW
    band_gate = next(
        g for g in scene.gates(GateType.ACK) if g["subject"]["id"] == "band_change"
    )
    await scene.decide(
        band_gate["_id"],
        GateType.ACK,
        "acknowledge",
        band_gate["subject"],
        reason="Band 2 to 3 reviewed.",
    )
    assert await scene.drain() == [StepKind.REVIEW_ACK, StepKind.STATE_PUSH]
    run = await scene.run()
    assert run.state is CaseState.RECOMMEND
    [narrative] = [
        j for j in scene.jobs(AgentName.NARRATIVE) if j.scope == "recommendation"
    ]
    assert narrative.refs["score_summary"]["composite"] == 10.4
    assert narrative.refs["score_summary"]["overrides"] == "CTL-T01 7.6 -> 4.5"
    assert assessed_case.stage == "recommend"

    # --- scene 15: the narrative drafts; the analyst edits and locks --------------
    await scene.agent_done(
        AgentName.NARRATIVE,
        narrative_text(
            "Residual risk sits in Band 3, driven by the third-party channel."
        ),
        drafted_condition(
            "CND-1", "Corridor-scope vendor DD review completed and filed"
        ),
        drafted_condition("CND-2", "Enhanced monitoring rules on payout agents live"),
        drafted_condition("CND-3", "Quarterly sample review of agent KYC files"),
        business_summary("The change is ready for committee."),
    )
    assert await scene.drain() == [StepKind.NARRATIVE_PUBLISH]
    assert scene.record_types()[-2:] == ["proposal.narrative", "guard.output"]
    assert recording_audit.records[-1].summary == "PO-summary scan · 0 restricted terms"
    assessed_case.recommendation_edits = {
        "sections": [
            {"key": "recommendation", "text": "Residual risk sits in Band 3."}
        ],
        "edit_count": 2,
    }
    assessed_case.locked = True
    await scene.send("case.locked", {"case_id": CASE, "actor": ANALYST})
    run = await scene.run()
    assert run.snapshot.flags.locked
    assert run.snapshot.flags.pack_version == 1
    [release_gate] = scene.gates(GateType.PACK_RELEASE)
    assert release_gate["_id"] == deterministic_gate_id(
        scene.run_id, GateType.PACK_RELEASE, "pack_v1"
    )
    assert await scene.drain() == [StepKind.LOCK_BUILD_PACK]
    assert scene.record_types()[-3:] == [
        "recommendation.locked",
        "pack.assembled",
        "gate.validation",
    ]

    # --- scene 16: four checks, the pack, the release ---------------------------
    [pack] = decision_service.packs
    assert pack["checks"] == {
        "locked": True,
        "business_summary_present": True,
        "all_items_reviewed": True,
        "boundary_and_band_acknowledged": True,
    }
    snapshot = decision_service.builds[0]["snapshot"]
    assert snapshot["score"]["composite"] == 10.4
    # The pack says who acknowledged by name, not by user id (ADR-89).
    assert snapshot["boundary"]["acknowledged_by"] == "Rahul Iyer"
    assert len(snapshot["conditions"]) == 3
    assert [o["item"] for o in snapshot["overrides"]] == ["CTL-T01"]
    assert [p["description"] for p in snapshot["planned"]] == ["CTL-T01 rationale"]
    [handoff] = assessed_case.views["handoff"]
    assert handoff["pack_id"] == pack["pack_id"]
    assert handoff["gate_id"] == release_gate["_id"]

    await scene.send(
        "pack.released",
        {
            "pack_id": pack["pack_id"],
            "version": 1,
            "released_to": ["u-amendes", "u-jokafor", "u-lchen"],
            "content_hash": pack["content_hash"],
            "release_checks_passed": True,
        },
    )
    assert await scene.drain() == [StepKind.PACK_APPROVED]
    run = await scene.run()
    assert run.state is CaseState.DECIDE
    assert run.refs["route"] == "committee"
    assert scene.gates(GateType.PACK_RELEASE) == []
    assert [
        g["status"]
        for g in scene.gates_collection.docs
        if g["_id"] == release_gate["_id"]
    ] == ["decided"]
    assert scene.record_types()[-1] == "pack.approved"
    assert assessed_case.stage == "decide"

    # --- what the PO could ever have seen: nothing of the harness or a score -----
    for event in assessed_case.events:
        if event["visibility"] != "analyst":
            assert "composite" not in str(event)
            assert "band" not in event["summary"].lower()
    po_events = [
        e["type"] for e in assessed_case.events if e["visibility"] != "analyst"
    ]
    assert "score.computed" not in po_events
    assert "score.recomputed" not in po_events

    # --- the audit vocabulary of the whole assessment, in the order it happened --
    records = scene.record_types()
    assessment_records = records[records.index("eval.recorded") + 1 :]
    # every decision is recorded: 22 items, then the two acknowledgements
    assert assessment_records.count("gate.decided") == 22 + 2
    assert [t for t in assessment_records if t != "gate.decided"] == [
        "screening.proposed",
        "workflow.stage",
        "proposal.research",
        "proposal.assessment",
        "score.computed",
        "boundary.flagged",
        "critic.findings",
        "completeness.report",
        "clarification.sent",
        "document.stored",  # the scope letter the owner added
        "owner.answered",
        "guard.input",  # Guard 1 on it, from the ingest step
        "agent.rerun",
        "proposal.assessment",  # the re-run's planned control
        "score.recomputed",  # unchanged: 9.8
        "human.override",
        "score.recomputed",  # 10.4
        "boundary.flagged",  # the band change asks for an acknowledgement
        "proposal.narrative",
        "guard.output",  # the PO-summary scan
        "recommendation.locked",
        "pack.assembled",
        "gate.validation",
        "pack.approved",
    ]


async def test_an_unlock_after_the_lock_supersedes_the_pack_and_reopens_every_item(
    scene: Scene,
    assessed_case: FakeCaseService,
    decision_service: FakeDecisionService,
    demo_research_output: list[dict[str, Any]],
    demo_assessment_output: list[dict[str, Any]],
) -> None:
    await _to_review(scene, demo_research_output, demo_assessment_output)
    await _decide_everything_but_the_ratings(scene)
    await scene.acknowledge_all()
    assert (await scene.run()).state is CaseState.RECOMMEND
    await scene.agent_done(
        AgentName.NARRATIVE,
        narrative_text("Recommendation."),
        drafted_condition("CND-1", "Corridor review filed"),
        business_summary("Ready."),
    )
    await scene.drain()
    assessed_case.locked = True
    await scene.send("case.locked", {"case_id": CASE, "actor": ANALYST})
    await scene.drain()
    pack = decision_service.packs[0]

    await scene.send(
        "case.unlocked",
        {
            "case_id": CASE,
            "actor": ANALYST,
            "unlock_reason": "The partner sent a corrected audit report.",
        },
    )
    assert await scene.drain() == [StepKind.UNLOCK_SUPERSEDE]

    run = await scene.run()
    assert run.state is CaseState.REVIEW, "reopened items keep the run in review"
    assert run.snapshot.flags.locked is False
    assert decision_service.superseded[0]["pack_id"] == pack["pack_id"]
    assert [
        g["status"]
        for g in scene.gates_collection.docs
        if g["gate_type"] == "pack_release"
    ] == ["expired"]
    gated = [r for r in assessed_case.items.values() if r["kind"] != "gap"]
    assert len(scene.gates(GateType.REVIEW_ITEM)) == len(gated)
    assert assessed_case.stage == "review"
    written = [r.type for r in scene.audit.records]  # not in the demo vocabulary
    assert {"case.unlocked", "pack.superseded"} <= set(written)

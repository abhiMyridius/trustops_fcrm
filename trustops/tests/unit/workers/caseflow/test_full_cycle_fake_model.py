"""One whole case cycle through the real H1 and the real H2 on a fake model
(plan B4; ADR-77, ADR-79).

H1 is the real :class:`CaseflowEngine`, :class:`StepRunner` and every core's step
handlers, against the fake peers of ``conftest`` (S1, S3, S4, S5, and S2 as the
real scoring engine). H2 is the real :class:`AgentJobConsumer`: it claims each
job, builds the context pack (or its parts) with the seed-backed tool registry,
runs every pre-guard, attempt and Guard 4 check of the live runtime, and commits
its terminal event to its own outbox. Only the model is fake: it answers each
request with :func:`fcrm_agents.demo.demo_output` for the pack that request was
built from, so every claim it makes cites what the pack really shows.

A :class:`Bridge` stands in for the two relays and the broker: it runs H1's
pending steps, hands each new ``agent.run`` in H1's outbox to H2, and each new
``agent.done``/``agent.failed`` in H2's outbox back to H1, until nothing new
appears. Nothing calls Bedrock (ADR-59).

The human side (the owner, the analyst, S5's committee events) is scripted as in
the scene tests, but decided from what the agents really proposed: every open
gate is answered by reading the gate store, not a list known in advance.

Two limits of the seed-backed tools H2 runs on here: the examiner's pack lists the
seed's 67-record demo chain, not the chain this run wrote, and the critic's
evidence ledger is the seed's demo proposals keyed to this run. The
``policy_gap`` agent's ``agent.done`` is stored and settled by H1 and nothing
else: no step consumes it (the engine logs it as outside the review's join).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Optional

import pytest
from conftest import (
    COMMITTEE,
    CycleAgentJobs,
    FakeClosingCaseService,
    FakeCollection,
    FakeDecisionService,
    FakeDecisionVotesService,
    FakeExaminerService,
    FakeJurisdictionPolicyService,
    FakeReflagService,
    FakeScoringService,
    FakeSession,
    FakeTransact,
    RecordingAudit,
)
from fcrm_agent_worker import handler as h2_handler
from fcrm_agent_worker.handler import AgentJobConsumer
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_agents.builders import BUILDERS
from fcrm_agents.context_pack import (
    DEFAULT_PACK_TOTALS,
    DEFAULT_SECTION_TOKEN_CAPS,
    ContextPack,
    ContextPackBuilder,
)
from fcrm_agents.demo import demo_output
from fcrm_agents.models import load_tiers_config
from fcrm_agents.runtime import StaticPermitClient
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.budget import CaseBudgetStore
from fcrm_caseflow.clients.case_client import CaseClient
from fcrm_caseflow.clients.decision_client import DecisionClient
from fcrm_caseflow.clients.decision_votes_client import DecisionVotesClient
from fcrm_caseflow.clients.oversight_client import (
    ExaminerClient,
    OversightClients,
    ReflagClient,
)
from fcrm_caseflow.clients.policy_client import PolicyClient
from fcrm_caseflow.clients.scoring_client import ScoringClient
from fcrm_caseflow.cores import default_handlers
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.oversight_runs import OversightRuns
from fcrm_caseflow.steps import StepRegistry, StepRunner
from fcrm_caseflow.store import AgentResultStore, CaseRun, CaseRunStore
from fcrm_common.ids import new_id
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.enums import AgentName, CaseState, GateStatus, GateType, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    AgentDone,
    GateDecided,
    HarnessStep,
    JobPayload,
)
from fcrm_seed.loader import load_raw
from fcrm_tools import DEMO_CASE_ID, build_seed_registry, seed_pins
from fcrm_tools.seed_backed import EvidenceLedgerTool
from fcrm_tools.seed_data import demo_ledger_entries
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

SEED = load_raw()
DEMO = SEED["demo"]
CASE = DEMO_CASE_ID
BUSINESS_CASE, CONTRACT, SCOPE_LETTER = (d["name"] for d in DEMO["documents"])
OWNER, ANALYST = "u-pnair", "u-riyer"
OVERRIDE_REASON = (
    "Audit predates the corridor arrangement and covers domestic scope only."
)
WATCHLIST_VERSION = "v2026-10-fixture"
PRECEDENT = "FCRM-2026-0118"
#: The open cases S1 finds for the published list (the oversight scene's two).
REFLAG_CANDIDATES: list[dict[str, Any]] = [
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
MAX_BRIDGE_ITERATIONS = 40

#: The agents the oversized variant forces to split, and the caps it gives them:
#: each section the agent's input is split along at a fraction of its shipped cap.
SPLIT_AGENTS = (AgentName.RESEARCH, AgentName.CRITIC)
LOWERED_SECTION_CAPS: Mapping[str, Mapping[str, int]] = {
    **DEFAULT_SECTION_TOKEN_CAPS,
    "research": {**DEFAULT_SECTION_TOKEN_CAPS["research"], "clauses": 350},  # 12: ~650
    "critic": {**DEFAULT_SECTION_TOKEN_CAPS["critic"], "proposals": 700},  # 10: ~1,200
}


# --- H2 with a pack-derived fake model -----------------------------------------


class PackCapturingConsumer(AgentJobConsumer):
    """The real consumer, keeping the packs (or parts) of each job it builds,
    so the fake model can answer a request from the pack it was built from."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.packs: dict[str, list[ContextPack]] = {}
        self.current: list[ContextPack] = []

    async def _build_packs(self, job: Any) -> list[ContextPack]:
        packs = await super()._build_packs(job)
        self.packs[job.job_id] = packs
        self.current = packs
        return packs


def _user_text(messages: Sequence[ModelMessage]) -> str:
    return "\n".join(
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )


def pack_answering_factory(
    current: Callable[[], Sequence[ContextPack]],
) -> ModelFactory:
    """A ``ModelFactory`` whose model answers with ``demo_output`` for the pack
    of the request: of the job's packs (``current()``), the only one, or the
    part whose ``Part i of n`` line the request's user text carries."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        packs = current()
        if len(packs) == 1:
            pack = packs[0]
        else:
            text = _user_text(messages)
            matching = [p for p in packs if p.sections.get("part", "\0") in text]
            assert len(matching) == 1, "a request names no single part of its job"
            pack = matching[0]
        payload = [
            item.model_dump(mode="json") for item in demo_output(pack.agent, pack)
        ]
        output_tool = info.output_tools[0]
        properties = output_tool.parameters_json_schema.get("properties", {})
        key = next(iter(properties)) if len(properties) == 1 else "response"
        return ModelResponse(
            parts=[ToolCallPart(tool_name=output_tool.name, args={key: payload})]
        )

    def factory(models: Any, plan: Any) -> FunctionModel:
        return FunctionModel(respond, model_name="fake-pack-demo")

    return factory


def _lowered_builder_for(agent: AgentName) -> ContextPackBuilder:
    """``builder_for`` with the split agents' sharded sections capped low."""
    if agent in SPLIT_AGENTS:
        return BUILDERS[agent](
            section_caps=LOWERED_SECTION_CAPS, pack_totals=DEFAULT_PACK_TOTALS
        )
    return BUILDERS[agent]()


# --- the bridge: H1's outbox -> H2, H2's outbox -> H1 ------------------------


def _envelopes(collection: FakeCollection) -> list[Envelope]:
    return [Envelope.model_validate_json(bytes(d["body"])) for d in collection.docs]


class Bridge:
    """The relays and the broker between H1 and H2, in one process."""

    def __init__(
        self,
        cycle: Cycle,
        consumer: PackCapturingConsumer,
        h2_outbox: FakeCollection,
    ) -> None:
        self.cycle = cycle
        self.consumer = consumer
        self.h2_outbox = h2_outbox
        self.delivered: set[str] = set()
        self.iterations: list[int] = []

    async def settle(self) -> int:
        """Run until neither side has anything new; the loop's iterations."""
        iterations = 0
        while True:
            iterations += 1
            assert iterations < MAX_BRIDGE_ITERATIONS, "the H1/H2 loop never settled"
            ran = await self.cycle.drain()
            jobs = self._new(_envelopes(self.cycle.outbox), {"agent.run"})
            for envelope in jobs:
                await self.consumer.handle(envelope, None)
            results = self._new(
                _envelopes(self.h2_outbox), {"agent.done", "agent.failed"}
            )
            for envelope in results:
                await self.cycle.engine.handle(envelope, self.cycle.session)  # type: ignore[arg-type]
            if not (ran or jobs or results):
                self.iterations.append(iterations)
                return iterations

    def _new(self, envelopes: list[Envelope], types: set[str]) -> list[Envelope]:
        fresh = [
            e for e in envelopes if e.type in types and e.event_id not in self.delivered
        ]
        self.delivered.update(e.event_id for e in fresh)
        return fresh


# --- the case, its peers and the people ------------------------------------------


class Cycle:
    """H1 wired to every fake peer, and the people's side of the walkthrough."""

    def __init__(
        self,
        *,
        engine: CaseflowEngine,
        runner: StepRunner,
        run_store: CaseRunStore,
        runs: FakeCollection,
        gates: FakeCollection,
        outbox: FakeCollection,
        session: FakeSession,
        audit: RecordingAudit,
        case: FakeClosingCaseService,
        decision: FakeDecisionService,
        votes: FakeDecisionVotesService,
        examiner: FakeExaminerService,
        reflags: FakeReflagService,
    ) -> None:
        self.engine = engine
        self.runner = runner
        self.run_store = run_store
        self.runs = runs
        self.gates = gates
        self.outbox = outbox
        self.session = session
        self.audit = audit
        self.case = case
        self.decision = decision
        self.votes = votes
        self.examiner = examiner
        self.reflags = reflags
        self.run_id = ""
        self.ran: list[StepKind] = []
        self._ran_ids: set[str] = set()

    async def send(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        case_id: Optional[str] = CASE,
        run_id: Optional[str] = None,
    ) -> None:
        """Hand one wire event to the engine, as H1's consumer would."""
        envelope = Envelope(
            event_id=new_id("evt"),
            type=event_type,
            case_id=case_id,
            run_id=run_id if run_id is not None else (self.run_id or None),
            occurred_at=utcnow(),
            payload=dict(payload),
        )
        await self.engine.handle(envelope, self.session)  # type: ignore[arg-type]

    async def drain(self) -> list[StepKind]:
        """Run every ``harness.step`` not yet run, and the steps they enqueue."""
        ran: list[StepKind] = []
        while True:
            pending = [s for s in self.steps() if s.step_id not in self._ran_ids]
            if not pending:
                self.ran.extend(ran)
                return ran
            for step in pending:
                self._ran_ids.add(step.step_id)
                await self.runner.run(step)
                ran.append(step.step)

    def every_step_ran(self) -> bool:
        """Whether no ``harness.step`` in H1's outbox is still waiting to run."""
        return {s.step_id for s in self.steps()} <= self._ran_ids

    def envelopes(self, event_type: str) -> list[Envelope]:
        return [e for e in _envelopes(self.outbox) if e.type == event_type]

    def steps(self) -> list[HarnessStep]:
        return [
            HarnessStep.model_validate(e.payload)
            for e in self.envelopes("harness.step")
        ]

    def jobs(self, agent: AgentName) -> list[JobPayload]:
        """The ``agent.run`` jobs H1 dispatched to ``agent``, in order."""
        jobs = [
            JobPayload.model_validate(e.payload) for e in self.envelopes("agent.run")
        ]
        return [j for j in jobs if j.agent is agent]

    def run_documents(self) -> list[dict[str, Any]]:
        """Every run H1 holds (the case's, and the oversight runs)."""
        return list(self.runs.docs)

    def open_gates(self, gate_type: GateType) -> list[dict[str, Any]]:
        return [
            d
            for d in self.gates.docs
            if d["gate_type"] == gate_type.value
            and d["status"] == GateStatus.OPEN.value
        ]

    def record_types(self) -> list[str]:
        return [r.type for r in self.audit.records]

    async def run(self) -> CaseRun:
        run = await self.run_store.get(self.run_id)
        assert run is not None
        return run

    async def decide(
        self,
        gate: Mapping[str, Any],
        decision: str,
        *,
        by: str,
        subject: Optional[Mapping[str, Any]] = None,
        **extra: Any,
    ) -> None:
        """S1 publishes ``gate.decided`` for ``gate``."""
        payload = GateDecided(
            gate_id=gate["_id"],
            gate_type=GateType(gate["gate_type"]),
            case_id=CASE,
            run_id=self.run_id,
            decision=decision,
            decided_by=by,
            decided_at=utcnow(),
            subject=dict(subject if subject is not None else gate["subject"]),
            **extra,
        )
        await self.send("gate.decided", payload.model_dump(mode="json"))


@pytest.fixture
def cycle_parts(
    engine_factory: Any,
    run_store: CaseRunStore,
    case_runs_collection: FakeCollection,
    gate_store: GateStore,
    gates_collection: FakeCollection,
    outbox_collection: FakeCollection,
    session: FakeSession,
    recording_audit: RecordingAudit,
    agent_results: AgentResultStore,
    case_budgets_collection: FakeCollection,
    scoring_service: FakeScoringService,
    decision_service: FakeDecisionService,
    examiner_service: FakeExaminerService,
    reflag_service: FakeReflagService,
    jurisdiction_policy_service: FakeJurisdictionPolicyService,
) -> tuple[Cycle, CaseBudgetStore]:
    """H1 as production wires it (every core, the budget ledger, oversight),
    over the fakes; the S1 fake serves intake, assessment and the outcome."""
    case = FakeClosingCaseService()
    case.analyst = None  # assigned at triage
    case.fields = []
    case.add_precedent(
        PRECEDENT,
        "Payout partner onboarding (Guatemala corridor)",
        {
            "F31": ("Transaction limits and velocity", "$2,999 per transaction"),
            "F33": ("Disclosure text reference", "Standard remittance disclosure v3"),
            "F34": ("Monitoring rule coverage", "Remittance rule set v3"),
        },
    )
    jurisdiction_policy_service.hits = [
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
    budgets = CaseBudgetStore(case_budgets_collection)  # type: ignore[arg-type]
    engine: CaseflowEngine = engine_factory(
        dict(STEP_FOR),
        results=agent_results,
        oversight=OversightRuns(run_store),
        budgets=budgets,
    )
    votes = FakeDecisionVotesService({})
    runner = StepRunner(
        run_store,
        StepRegistry(default_handlers()),
        engine,
        recording_audit,
        case=CaseClient(case.client("case")),
        policy=PolicyClient(jurisdiction_policy_service.client("policy")),
        agent_results=agent_results,
        scoring=ScoringClient(scoring_service.client("scoring")),
        decision=DecisionClient(decision_service.client("decision")),
        gates=gate_store,
        decision_votes=DecisionVotesClient(votes.client("decision")),
        oversight=OversightClients(
            examiner=ExaminerClient(examiner_service.client("audit")),
            reflags=ReflagClient(reflag_service.client("case")),
        ),
    )
    cycle = Cycle(
        engine=engine,
        runner=runner,
        run_store=run_store,
        runs=case_runs_collection,
        gates=gates_collection,
        outbox=outbox_collection,
        session=session,
        audit=recording_audit,
        case=case,
        decision=decision_service,
        votes=votes,
        examiner=examiner_service,
        reflags=reflag_service,
    )
    return cycle, budgets


def _consumer(
    run_id: str,
    audit: RecordingAudit,
    jobs: CycleAgentJobs,
    h2_outbox: FakeCollection,
) -> PackCapturingConsumer:
    """H2 as production wires it, on the seed-backed tools (with the evidence
    ledger keyed to H1's real run) and the shipped tier chains, the model
    replaced by :func:`pack_answering_factory`."""
    ledger = EvidenceLedgerTool({(CASE, run_id): demo_ledger_entries(SEED)})
    consumer = PackCapturingConsumer(
        FakeCollection("consumed_messages"),
        _NoPublisher(),
        registry=build_seed_registry(SEED, ledger=ledger),
        permit_client=StaticPermitClient.from_registry(),
        audit=audit,  # type: ignore[arg-type]
        tiers_config=load_tiers_config(),
        transact=FakeTransact(),
        jobs=jobs,  # type: ignore[arg-type]
        outbox=Outbox(h2_outbox),  # type: ignore[arg-type]
        model_factory=pack_answering_factory(lambda: consumer.current),
    )
    return consumer


class _NoPublisher:
    """H2 publishes nothing itself (its events go through its outbox); the base
    consumer's retry path would, and a test that reaches it has failed."""

    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        raise AssertionError(f"H2 published {routing_key} directly")


# --- the walkthrough ------------------------------------------------------------


async def _open_case(cycle: Cycle) -> None:
    """Scene 2: S1 creates the case with the seed's pinned versions."""
    await cycle.send(
        "case.created",
        {
            "case_id": CASE,
            "actor": OWNER,
            "data": {
                "title": "Consumer remittances US → Mexico",
                "pins": seed_pins(SEED).model_dump(mode="json"),
            },
        },
        run_id="",
    )
    run = await cycle.run_store.get_by_case_id(CASE)
    assert run is not None
    cycle.run_id = run.snapshot.run_id


async def _store_document(cycle: Cycle, name: str, **extra: Any) -> None:
    cycle.case.add_document(name, name)
    await cycle.send(
        "case.document_stored",
        {
            "case_id": CASE,
            "actor": OWNER,
            "doc_id": name,
            "name": name,
            "bucket": "uploads",
            "key": f"{CASE}/{name}/{name}",
            "sha256": "ab" * 32,
            "size_bytes": 2_000_000,
            "content_type": "application/pdf",
            "keep_terms": ["Priya Nair", "Remesas del Norte S.A."],
            **extra,
        },
    )


async def _ingested(cycle: Cycle, name: str) -> None:
    """H3 read the document (its ``ingest.done``)."""
    pages = next(d["pages"] for d in DEMO["documents"] if d["name"] == name)
    cycle.case.documents[name]["status"] = "extracted"
    await cycle.send(
        "ingest.done",
        {
            "job_id": f"ingest_{name}",
            "doc_id": name,
            "canonical_key": f"canonical/{CASE}/{name}.md",
            "anchored_key": f"canonical/{CASE}/{name}.anchored.md",
            "pages": pages,
            "page_map": {"1": [1, 30]},
            "guard1": {"status": "masked", "pii_total": 2},
        },
    )


@pytest.mark.parametrize("oversized", [False, True], ids=["shipped-caps", "oversized"])
async def test_one_case_runs_its_whole_cycle_through_h1_and_h2(  # noqa: PLR0915 - one scripted walkthrough
    oversized: bool,
    cycle_parts: tuple[Cycle, CaseBudgetStore],
    recording_audit: RecordingAudit,
    cycle_agent_jobs: CycleAgentJobs,
    cycle_agent_outbox_collection: FakeCollection,
    agent_results_collection: FakeCollection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cycle, budgets = cycle_parts
    if oversized:
        monkeypatch.setattr(h2_handler, "builder_for", _lowered_builder_for)

    # --- scene 2: the case is created with the seed's pins; the run opens ------
    await _open_case(cycle)
    consumer = _consumer(
        cycle.run_id, recording_audit, cycle_agent_jobs, cycle_agent_outbox_collection
    )
    bridge = Bridge(cycle, consumer, cycle_agent_outbox_collection)
    await bridge.settle()
    assert (await cycle.run()).state is CaseState.INTAKE

    # --- scene 3: two documents; H3 reads them; H2 runs intake on each ----------
    await _store_document(cycle, BUSINESS_CASE)
    await _store_document(cycle, CONTRACT)
    await _ingested(cycle, BUSINESS_CASE)
    await _ingested(cycle, CONTRACT)
    await bridge.settle()
    intake = {job.refs["doc_id"]: job.job_id for job in cycle.jobs(AgentName.INTAKE)}
    assert set(intake) == {BUSINESS_CASE, CONTRACT}
    # The 14-page business case does not fit one cheap pack: H2 split it.
    assert len(consumer.packs[intake[BUSINESS_CASE]]) == 2
    assert len(consumer.packs[intake[CONTRACT]]) == 1
    assert (
        len(_done(cycle_agent_outbox_collection, intake[BUSINESS_CASE]).output["parts"])
        == 2
    )
    [push] = cycle.case.intake_pushes
    assert {c["change_type"] for c in push["categories"]} == set(DEMO["change_types"])
    assert push["precedent"]["case_id"] == PRECEDENT
    assert cycle.open_gates(GateType.PO_ANSWER_GAPS)

    # --- scenes 4-7: the owner confirms, reuses, answers, corrects -------------
    await _owner_finishes_intake(cycle)
    await bridge.settle()
    assert cycle.open_gates(GateType.PO_CONFIRM_CATEGORY) == []
    assert cycle.open_gates(GateType.PO_ANSWER_GAPS) == []
    assert "values.reused" in cycle.record_types()
    assert "owner.corrected" in cycle.record_types()

    # --- scenes 7-9: submit, triage, screening, research and assessment, the ----
    # --- score (real S2), the critic and the completeness report: all H2 -------
    await _submit(cycle)
    await bridge.settle()
    run = await cycle.run()
    assert run.state is CaseState.COMPLETENESS
    assert (run.band, run.refs["score_summary"]["composite"]) == (2, 9.8)
    assert run.refs["score_summary"]["boundary_flag"] is True
    assert [g["subject"]["id"] for g in cycle.open_gates(GateType.ACK)] == ["boundary"]
    assert cycle.case.analyst == {"id": ANALYST, "name": "Rahul Iyer"}
    for kind in ("clause", "factor", "control", "critic_finding"):
        assert any(i["kind"] == kind for i in cycle.case.items.values()), kind

    # --- scene 10: the analyst asks one clarification round -------------------
    [completeness] = cycle.open_gates(GateType.COMPLETENESS)
    await cycle.decide(
        completeness, "ask", by=ANALYST, subject={}, after={"round": 1, "questions": 2}
    )
    await bridge.settle()
    assert (await cycle.run()).state is CaseState.CLARIFICATION

    # --- scene 11: the owner answers and adds the scope letter; the re-run -----
    await _owner_answers(cycle)
    await bridge.settle()
    assert (await cycle.run()).state is CaseState.CLARIFICATION  # reading the letter
    await _ingested(cycle, SCOPE_LETTER)
    await bridge.settle()
    run = await cycle.run()
    assert run.state is CaseState.REVIEW
    assert run.refs["submission_version"] == 2
    rerun = cycle.jobs(AgentName.ASSESSMENT)[-1]
    assert (rerun.scope, rerun.changed_fields) == ("changed_fields", ["F11", "F21"])
    assert run.refs["score_summary"]["composite"] == 9.8

    # --- a policy gap is recorded; the policy_gap agent runs beside the review --
    await cycle.send(
        "case.gap_recorded",
        {
            "case_id": CASE,
            "actor": ANALYST,
            "gap_id": "GAP-0151-1",
            "text": DEMO["clause_gap"],
            "scope": "corpus",
        },
    )
    await bridge.settle()
    [gap_job] = cycle.jobs(AgentName.POLICY_GAP)
    assert _done(cycle_agent_outbox_collection, gap_job.job_id).output["accepted"]
    assert (await cycle.run()).state is CaseState.REVIEW  # never on the decision path
    # H1 keeps the suggestion and settles its tokens, and does nothing else with
    # it: no step consumes ``agent.done`` for policy_gap (the engine logs it as
    # outside the review's join), so nothing reaches the chain or the analyst.

    # --- scenes 12-14: every item decided; CTL-T01 overridden 7.6 -> 4.5 -------
    await _analyst_reviews(cycle)
    await bridge.settle()
    run = await cycle.run()
    assert (run.band, run.refs["score_summary"]["composite"]) == (3, 10.4)
    assert run.refs["score_summary"]["overrides"] == "CTL-T01 7.6 -> 4.5"
    assert cycle.open_gates(GateType.REVIEW_ITEM) == []
    assert sorted(g["subject"]["id"] for g in cycle.open_gates(GateType.ACK)) == [
        "band_change",
        "boundary",
    ]
    for gate in cycle.open_gates(GateType.ACK):
        await cycle.decide(gate, "acknowledge", by=ANALYST, reason="Reviewed.")
        await bridge.settle()

    # --- scene 15: the recommendation (H2); the analyst edits and locks --------
    run = await cycle.run()
    assert run.state is CaseState.RECOMMEND
    [recommendation] = [
        j for j in cycle.jobs(AgentName.NARRATIVE) if j.scope == "recommendation"
    ]
    assert recommendation.refs["score_summary"]["composite"] == 10.4
    assert "proposal.narrative" in cycle.record_types()
    cycle.case.recommendation_edits = {
        "sections": [
            {"key": "recommendation", "text": "Residual risk sits in Band 3."}
        ],
        "edit_count": 2,
    }
    cycle.case.locked = True
    await cycle.send("case.locked", {"case_id": CASE, "actor": ANALYST})
    await bridge.settle()
    [pack] = cycle.decision.packs
    assert set(pack["checks"].values()) == {True}
    snapshot = cycle.decision.builds[-1]["snapshot"]
    assert snapshot["score"]["composite"] == 10.4
    assert [o["item"] for o in snapshot["overrides"]] == ["CTL-T01"]
    assert [c["id"] for c in snapshot["conditions"]] == ["CND-1", "CND-2", "CND-3"]

    # --- scene 16: the pack is released to the committee -----------------------
    cycle.votes.pack = _released(pack)
    await cycle.send(
        "pack.released",
        {
            "pack_id": pack["pack_id"],
            "version": 1,
            "released_to": list(COMMITTEE),
            "content_hash": pack["content_hash"],
            "release_checks_passed": True,
        },
    )
    await bridge.settle()
    assert (await cycle.run()).state is CaseState.DECIDE

    # --- scenes 17-19: three hidden votes, 2-1, the outcome, the close ---------
    await _committee_decides(cycle, pack["pack_id"])
    await bridge.settle()
    run = await cycle.run()
    assert run.state is CaseState.CLOSED
    assert (run.refs["outcome"], run.refs["outcome_mode"]) == (
        "approve_with_conditions",
        "committee",
    )
    assert run.refs["conditions_activated"] == 3
    assert run.refs["chain_verified"] is True
    [recorded] = cycle.votes.outcomes
    assert (recorded["outcome"], recorded["pack_id"]) == (
        "approve_with_conditions",
        pack["pack_id"],
    )
    [put] = cycle.case.outcome_puts
    assert [c["ref"] for c in put["conditions"]] == ["CND-1", "CND-2", "CND-3"]
    assert cycle.case.stage == "closed"
    assert cycle.audit.verified == [CASE]
    [verified] = [r for r in cycle.audit.records if r.type == "chain.verified"]
    assert verified.data["ok"] is True

    # --- scene 20: an examiner asks the closed case a question (H2 answers) ----
    await cycle.send(
        "examiner.asked",
        {
            "case_id": CASE,
            "question_id": "exq_1",
            "question": DEMO["examiner"]["question"],
            "asked_by": ANALYST,
        },
        run_id="",
    )
    await bridge.settle()
    answer = cycle.examiner.answers["exq_1"]
    assert answer["refused"] is False
    assert len(answer["sentences"]) == len(DEMO["examiner"]["sentences"])
    assert (await cycle.run()).state is CaseState.CLOSED  # asking moves nothing

    # --- scene 20: a jurisdiction list is published; two open cases re-flagged --
    cycle.reflags.candidates = REFLAG_CANDIDATES
    await cycle.send(
        "jurisdiction.list_published",
        {
            "version": WATCHLIST_VERSION,
            "added": ["Algeria", "Namibia"],
            "removed": [],
            "fixture": True,
        },
        case_id=None,
        run_id="",
    )
    await bridge.settle()
    reflags = {r["case_id"]: r for r in cycle.reflags.rows.values()}
    assert set(reflags) == {c["case_id"] for c in REFLAG_CANDIDATES}
    assert {r["proposed_by"] for r in reflags.values()} == {"agent"}

    # --- the whole cycle, H1 and H2 together ------------------------------------
    await _assert_one_terminal_event_per_job(cycle, cycle_agent_outbox_collection)
    assert {d["_id"] for d in agent_results_collection.docs} == set(
        cycle_agent_jobs.docs
    )
    assert set(cycle_agent_jobs.docs) == {
        e.payload["job_id"] for e in cycle.envelopes("agent.run")
    }
    assert {d["status"] for d in cycle_agent_jobs.docs.values()} == {"done"}
    for job_id in cycle_agent_jobs.docs:  # Guard 2 and Guard 4, recorded by H2
        records = [
            r.type for r in cycle.audit.records if r.data.get("job_id") == job_id
        ]
        # every part runs the whole guard chain, so a split job has one per part
        assert records.count("guard.permit") == len(consumer.packs[job_id]), job_id
        assert "guard.output" in records, job_id
        assert "agent.failed" not in records, job_id
    await _assert_ledger_matches_usage(budgets, cycle_agent_outbox_collection)
    assert cycle.every_step_ran()
    for run_doc in cycle.run_documents():
        assert run_doc["pending_steps"] == [], run_doc["_id"]
    assert max(bridge.iterations) < MAX_BRIDGE_ITERATIONS
    clause_ids = [
        i["subject"]["clause_id"]
        for i in cycle.case.items.values()
        if i["kind"] == "clause"
    ]
    assert len(clause_ids) == len(set(clause_ids))
    assert set(clause_ids) == {c["clause_id"] for c in DEMO["clauses"]}

    # --- the oversized variant: research and critic really ran in parts --------
    if oversized:
        for agent in SPLIT_AGENTS:
            for job in cycle.jobs(agent):
                assert len(consumer.packs[job.job_id]) >= 2, agent
                parts = _done(cycle_agent_outbox_collection, job.job_id).output["parts"]
                assert len(parts) >= 2, agent


async def _owner_finishes_intake(cycle: Cycle) -> None:
    """Scenes 4-7: the owner confirms each proposed change type (S1 resolves the
    field set before the last), reuses three values from the precedent, answers
    every blocking question and corrects the go-live date."""
    categories = cycle.open_gates(GateType.PO_CONFIRM_CATEGORY)
    cycle.case.change_types = list(DEMO["change_types"])
    cycle.case.required_field_ids = [f"F{n:02d}" for n in range(1, 19)]
    for gate in categories:
        await cycle.decide(gate, "confirm", by=OWNER)
        await cycle.drain()
    for gate in cycle.open_gates(GateType.PO_REUSE):
        await cycle.decide(
            gate, "reuse", by=OWNER, after={"field_ids": ["F31", "F33", "F34"]}
        )
        await cycle.drain()
    for gate in cycle.open_gates(GateType.PO_ANSWER_GAPS):
        await cycle.decide(gate, "answer", by=OWNER)
        await cycle.drain()
    [go_live] = [
        g
        for g in cycle.open_gates(GateType.PO_CONFIRM_VALUE)
        if g["subject"]["id"] == "F03"
    ]
    await cycle.decide(
        go_live,
        "correct",
        by=OWNER,
        reason="Launch moved after the partner's licence review.",
        before={"value": "Q1 2027"},
        after={"value": "February 2027"},
    )
    await cycle.drain()


async def _submit(cycle: Cycle) -> None:
    """Scene 7: S1's four submit checks passed; it publishes ``case.submitted``."""
    cycle.case.domains = list(DEMO["domains"])
    cycle.case.submission_version = 1
    cycle.case.fields = [
        {
            "field_id": "F03",
            "label": "Go-live date",
            "value": "February 2027",
            "origin": "owner_declared",
            "original_value": "Q1 2027",
        }
    ]
    await cycle.send(
        "case.submitted",
        {
            "case_id": CASE,
            "actor": OWNER,
            "submission_version": 1,
            "change_types": list(DEMO["change_types"]),
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


async def _owner_answers(cycle: Cycle) -> None:
    """Scene 11: the owner answers round 1 and adds the scope letter (S1 then
    holds submission v2)."""
    cycle.case.submission_version = 2
    await _store_document(cycle, SCOPE_LETTER, raised_in_round=1)
    await cycle.send(
        "case.answered",
        {
            "case_id": CASE,
            "actor": OWNER,
            "round": 1,
            "submission_version": 2,
            "changed_field_ids": ["F11", "F21"],
            "added_document_ids": [SCOPE_LETTER],
            "thread_ids": ["thr_1", "thr_2"],
        },
    )


async def _decide_item(
    cycle: Cycle,
    item_id: str,
    decision: str,
    status: str,
    *,
    value: Optional[dict[str, Any]] = None,
    reason: Optional[str] = None,
    **extra: Any,
) -> None:
    """The analyst decides one item: S1 stores it, then publishes ``gate.decided``."""
    row = cycle.case.items[item_id]
    cycle.case.decide_item(item_id, status, value=value, reason=reason)
    [gate] = [g for g in cycle.gates.docs if g["_id"] == row["gate_id"]]
    await cycle.decide(
        gate,
        decision,
        by=ANALYST,
        subject={"kind": row["kind"], "id": item_id},
        reason=reason,
        item_kind=row["kind"],
        **extra,
    )


async def _analyst_reviews(cycle: Cycle) -> None:
    """Scenes 12-14: every open item, one decision each: clauses accepted,
    factors and controls confirmed, the critic's findings explained, and the
    rated CTL-T01 overridden 7.6 -> 4.5 with a reason. An item a re-run
    superseded has an expired gate and is not decided (S1 shows only the newer)."""
    open_gates = {g["_id"] for g in cycle.open_gates(GateType.REVIEW_ITEM)}
    for item_id, row in list(cycle.case.items.items()):
        if row["gate_id"] not in open_gates:
            continue  # a gap (no gate), or an item a re-run superseded
        if row["kind"] == "clause":
            await _decide_item(cycle, item_id, "accept", "confirmed")
        elif row["kind"] == "critic_finding":
            await _decide_item(
                cycle,
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
            await _decide_item(
                cycle,
                item_id,
                "override",
                "overridden",
                value={"rating": 4.5},
                reason=OVERRIDE_REASON,
                before={"rating": 7.6},
                after={"rating": 4.5},
                rating_or_control_changed=True,
            )
        else:
            await _decide_item(cycle, item_id, "confirm", "confirmed")
        await cycle.drain()


def _released(pack: Mapping[str, Any]) -> dict[str, Any]:
    """The pack as S5 serves it once released (what the outcome step reads)."""
    view = FakeDecisionService._view(dict(pack))  # noqa: SLF001
    view.update(status="released", released_at="2026-10-05T12:00:00Z")
    return view


async def _committee_decides(cycle: Cycle, pack_id: str) -> None:
    """S5's events for scenes 17-18: three votes, then the 2-1 tally."""
    for count, member in enumerate(("u-jokafor", "u-amendes", "u-lchen"), start=1):
        await cycle.send(
            "vote.cast",
            {
                "pack_id": pack_id,
                "round": 1,
                "member_id": member,
                "cast_count": count,
                "quorum": 3,
            },
        )
    cycle.votes.tallied = "approve_with_conditions"
    await cycle.send(
        "vote.tallied",
        {
            "pack_id": pack_id,
            "round": 1,
            "outcome": "approve_with_conditions",
            "vote_outcome": "approve_with_conditions",
            "counts": {"approve_with_conditions": 2, "defer": 1},
            "votes": [
                {"member_id": member, "vote": vote, "rationale": rationale}
                for member, vote, rationale in (
                    ("u-jokafor", "approve_with_conditions", "Conditions cover it."),
                    ("u-amendes", "approve_with_conditions", "Conditions cover it."),
                    ("u-lchen", "defer", "The corridor review is not on file yet."),
                )
            ],
        },
    )


def _results(h2_outbox: FakeCollection) -> list[Envelope]:
    """The terminal events H2 committed, in order."""
    return [
        e for e in _envelopes(h2_outbox) if e.type in {"agent.done", "agent.failed"}
    ]


def _done(h2_outbox: FakeCollection, job_id: str) -> AgentDone:
    [done] = [
        AgentDone.model_validate(e.payload)
        for e in _results(h2_outbox)
        if e.type == "agent.done" and e.payload["job_id"] == job_id
    ]
    return done


async def _assert_one_terminal_event_per_job(
    cycle: Cycle, h2_outbox: FakeCollection
) -> None:
    """Every job H1 dispatched ended in exactly one ``agent.done``, and H1 kept
    each result once."""
    dispatched = [e.payload["job_id"] for e in cycle.envelopes("agent.run")]
    assert len(dispatched) == len(set(dispatched))
    results = _results(h2_outbox)
    assert sorted(e.payload["job_id"] for e in results) == sorted(dispatched)
    assert {e.type for e in results} == {"agent.done"}


async def _assert_ledger_matches_usage(
    budgets: CaseBudgetStore, h2_outbox: FakeCollection
) -> None:
    """Each case's ledger spent exactly what H2 reported its jobs used (input and
    output tokens, the capped keys), and holds nothing back."""
    spent: dict[str, int] = {}
    jobs: dict[str, set[str]] = {}
    for envelope in _results(h2_outbox):
        done = AgentDone.model_validate(envelope.payload)
        used = done.usage.get("input_tokens", 0) + done.usage.get("output_tokens", 0)
        assert used > 0, done.agent
        spent[done.case_id] = spent.get(done.case_id, 0) + used
        jobs.setdefault(done.case_id, set()).add(done.job_id)
    for case_id, total in spent.items():
        ledger = await budgets.get(case_id)
        assert ledger is not None, case_id
        assert (ledger.spent, ledger.reserved) == (total, 0), case_id
        assert set(ledger.jobs) == jobs[case_id], case_id

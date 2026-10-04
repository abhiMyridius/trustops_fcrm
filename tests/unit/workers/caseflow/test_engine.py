"""Tests for :mod:`fcrm_caseflow.engine`: the real runtime around the pure
state machine. Every Mongo collection is the in-memory
:class:`~conftest.FakeCollection`; every write passes the same
:class:`~conftest.FakeSession`, proving the outbox-in-the-same-transaction
contract at the API-usage level this package owns (real commit/abort
atomicity needs a replica set — see ``test_engine_live.py``, skipped
without one).
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
from conftest import FakeCollection, FakeSession, RecordingAudit
from fcrm_caseflow.engine import (
    CaseflowEngine,
    DuplicateCaseCreatedError,
    OrphanEventError,
)
from fcrm_caseflow.eventstore import rebuild
from fcrm_caseflow.gates import GateStore, gate_document
from fcrm_caseflow.store import CaseRunStore
from fcrm_caseflow.transitions import TransitionContext, open_run
from fcrm_caseflow.triggers import Trigger
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import AgentName, CaseState, GateType, Role
from fcrm_contracts.events.envelope import Envelope

CASE_ID = "FCRM-2026-0151"


def _env(
    event_type: str,
    payload: dict[str, Any],
    *,
    case_id: Optional[str] = CASE_ID,
    run_id: Optional[str] = None,
) -> Envelope:
    """Built directly against ``Envelope`` (not ``build_envelope``): a real
    consumer only ever sees an envelope through ``decode_envelope``, which
    never checks the event-type catalogue — see ``test_resolve.py``'s own
    helper docstring for why that matters for a couple of these types.
    """
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id=case_id,
        run_id=run_id,
        occurred_at=utcnow(),
        payload=payload,
    )


def _gate_decision_payload(
    *, gate_id: str, gate_type: GateType, run_id: str, decision: str, decided_by: str
) -> dict[str, Any]:
    return GateDecision(
        gate_id=gate_id,
        gate_type=gate_type,
        case_id=CASE_ID,
        run_id=run_id,
        decision=decision,
        decided_by=decided_by,
        decided_at=utcnow(),
    ).model_dump(mode="json")


async def _open_run(engine: CaseflowEngine, session: FakeSession) -> str:
    await engine.handle(_env("case.created", {"actor": "u-pnair", "data": {}}), session)


def _published_agent_run_payloads(
    outbox_collection: FakeCollection, agent: AgentName
) -> list[dict[str, Any]]:
    out = []
    for doc in outbox_collection.docs:
        if doc["type"] != "agent.run":
            continue
        env = Envelope.model_validate_json(bytes(doc["body"]))
        if env.payload.get("agent") == agent.value:
            out.append(env.payload)
    return out


class TestOpeningARun:
    async def test_case_created_opens_a_run_in_intake(
        self,
        engine: CaseflowEngine,
        session: FakeSession,
        run_store: CaseRunStore,
        recording_audit: RecordingAudit,
    ) -> None:
        await _open_run(engine, session)
        run = await run_store.get_by_case_id(CASE_ID)
        assert run is not None
        assert run.state is CaseState.INTAKE
        assert any(r.type == "case.created" for r in recording_audit.records)

    async def test_a_second_case_created_for_the_same_case_raises(
        self, engine: CaseflowEngine, session: FakeSession
    ) -> None:
        await _open_run(engine, session)
        with pytest.raises(DuplicateCaseCreatedError):
            await _open_run(engine, session)

    async def test_an_event_for_an_unknown_case_raises_orphan(
        self, engine: CaseflowEngine, session: FakeSession
    ) -> None:
        with pytest.raises(OrphanEventError):
            await engine.handle(
                _env("case.submitted", {}, case_id="FCRM-2026-9999"), session
            )

    async def test_handle_requires_a_transactional_session(
        self, engine: CaseflowEngine
    ) -> None:
        with pytest.raises(ValueError, match="transactional session"):
            await engine.handle(_env("case.created", {"data": {}}), None)


class TestAgentFailedAfterRetry:
    """D-15: every ``agent.failed`` resolves to ``AGENT_FAILED_AFTER_RETRY``
    unconditionally (see ``fcrm_caseflow.resolve``'s module docstring).
    """

    async def test_agent_failed_drives_handoff_and_opens_a_gate(
        self,
        engine: CaseflowEngine,
        session: FakeSession,
        run_store: CaseRunStore,
        gate_store: GateStore,
    ) -> None:
        await _open_run(engine, session)
        run = await run_store.get_by_case_id(CASE_ID)
        assert run is not None

        await engine.handle(
            _env(
                "agent.failed",
                {
                    "job_id": "job_1",
                    "case_id": CASE_ID,
                    "run_id": run.snapshot.run_id,
                    "agent": "intake",
                    "attempt": 2,
                    "reason": "guard4_rejected: schema mismatch",
                },
                run_id=run.snapshot.run_id,
            ),
            session,
        )

        reloaded = await run_store.get(run.snapshot.run_id)
        assert reloaded is not None
        assert reloaded.state is CaseState.HANDOFF
        assert await gate_store.count_open(CASE_ID, GateType.HANDOFF) == 1


class TestResearchAssessmentPairJoin:
    @staticmethod
    async def _run_in_assess_with_pending(
        engine: CaseflowEngine, session: FakeSession, run_store: CaseRunStore
    ) -> str:
        """Fast-forwards straight to ``ASSESS`` with both agents pending,
        exactly as a prior ``SCREENING_DISPATCH -> ASSESS_DISPATCH`` chain
        would have left it. The full-lifecycle test below drives the whole
        INTAKE/TRIAGE/SCREENING prelude the real way, end to end; doing
        that here too for every pair-join scenario would just be noise.
        """
        await _open_run(engine, session)
        run = await run_store.get_by_case_id(CASE_ID)
        assert run is not None
        # Two calls, not one: the first (INTAKE -> ASSESS) is a genuine
        # state *move*, so CaseRunStore correctly resets
        # ``dispatched_in_state`` for the newly-entered state; the second
        # (ASSESS -> ASSESS, the same self-loop the real
        # ``ASSESS_DISPATCH`` row always is) is what marks the dispatch as
        # already done, exactly as a real SCREENING_DISPATCH ->
        # ASSESS_DISPATCH chain would leave it. Skipping straight to one
        # call with an inconsistent (from=INTAKE, trigger=ASSESS_DISPATCH)
        # pair looked like a harmless shortcut but silently left
        # ``dispatched_in_state`` empty, which is not what the real chain
        # ever produces — caught by this test file's own assertions.
        for _ in range(2):
            run = await run_store.apply_transition(
                run,
                to_state=CaseState.ASSESS,
                flags=run.snapshot.flags,
                trigger=Trigger.ASSESS_DISPATCH,
                action="test setup: jump straight to ASSESS",
                message_id=None,
                context=TransitionContext(),
                session=session,
            )
        await run_store.set_pending_agents(
            run.snapshot.run_id, ["research", "assessment"], session=session
        )
        return run.snapshot.run_id

    async def test_a_single_agent_done_does_not_advance(
        self,
        engine: CaseflowEngine,
        session: FakeSession,
        run_store: CaseRunStore,
    ) -> None:
        run_id = await self._run_in_assess_with_pending(engine, session, run_store)
        await engine.handle(
            _env("agent.done", {"agent": "research"}, run_id=run_id), session
        )
        run = await run_store.get(run_id)
        assert run is not None
        assert run.state is CaseState.ASSESS
        assert run.pending_agents == ("assessment",)

    async def test_both_done_advances_to_critic_and_dispatches_it(
        self,
        engine: CaseflowEngine,
        session: FakeSession,
        run_store: CaseRunStore,
        gate_store: GateStore,
        outbox_collection: FakeCollection,
    ) -> None:
        run_id = await self._run_in_assess_with_pending(engine, session, run_store)
        await engine.handle(
            _env("agent.done", {"agent": "research"}, run_id=run_id), session
        )
        await engine.handle(
            _env(
                "agent.done",
                {"agent": "assessment", "band": 2, "boundary_flagged": True},
                run_id=run_id,
            ),
            session,
        )
        run = await run_store.get(run_id)
        assert run is not None
        assert run.state is CaseState.CRITIC
        assert run.pending_agents == ()
        assert run.band == 2
        assert await gate_store.count_open(CASE_ID, GateType.REVIEW_ITEM) == 1
        assert _published_agent_run_payloads(outbox_collection, AgentName.CRITIC)


class TestPoConfirmCategoryAggregation:
    async def test_fires_only_once_every_open_gate_of_that_type_is_decided(
        self,
        engine: CaseflowEngine,
        session: FakeSession,
        run_store: CaseRunStore,
        gate_store: GateStore,
        outbox_collection: FakeCollection,
    ) -> None:
        await _open_run(engine, session)
        run = await run_store.get_by_case_id(CASE_ID)
        assert run is not None
        run_id = run.snapshot.run_id

        for gate_id in ("gate_po_1", "gate_po_2"):
            await gate_store.open(
                gate_document(
                    gate_id=gate_id,
                    case_id=CASE_ID,
                    run_id=run_id,
                    gate_type=GateType.PO_CONFIRM_CATEGORY,
                    required_role=Role.PO,
                ),
                session=session,
            )
        await gate_store.open(
            gate_document(
                gate_id="gate_gap_1",
                case_id=CASE_ID,
                run_id=run_id,
                gate_type=GateType.PO_ANSWER_GAPS,
                required_role=Role.PO,
            ),
            session=session,
        )

        before = run.snapshot.version
        await engine.handle(
            _env(
                "gate.decided",
                _gate_decision_payload(
                    gate_id="gate_po_1",
                    gate_type=GateType.PO_CONFIRM_CATEGORY,
                    run_id=run_id,
                    decision="confirm",
                    decided_by="u-pnair",
                ),
                run_id=run_id,
            ),
            session,
        )
        still_waiting = await run_store.get(run_id)
        assert still_waiting is not None
        assert still_waiting.snapshot.version == before, (
            "one po_confirm_category gate is still open; no transition yet"
        )

        await engine.handle(
            _env(
                "gate.decided",
                _gate_decision_payload(
                    gate_id="gate_po_2",
                    gate_type=GateType.PO_CONFIRM_CATEGORY,
                    run_id=run_id,
                    decision="confirm",
                    decided_by="u-pnair",
                ),
                run_id=run_id,
            ),
            session,
        )
        done = await run_store.get(run_id)
        assert done is not None
        assert done.snapshot.version == before + 1
        assert done.state is CaseState.INTAKE
        # gaps are still open (gate_gap_1), so another intake pass is
        # dispatched, scoped to questions only.
        payloads = _published_agent_run_payloads(outbox_collection, AgentName.INTAKE)
        assert any(p.get("scope") == "questions_only" for p in payloads)


class TestFullDemoLifecycle:
    """One pass of (roughly) FCRM-2026-0151's own trigger sequence
    (``docs/requirements/research/06-walkthrough-seed-extraction.md``;
    mirrors ``test_transitions.py``'s ``_DEMO_SEQUENCE``), but driven as
    real wire envelopes through ``engine.handle()`` rather than direct
    ``apply()`` calls — proving the dispatch/gate/outbox wiring does not
    silently diverge from the already-proven pure logic, end to end.
    """

    async def test_demo_case_reaches_closed_via_committee(  # noqa: PLR0915 - a full lifecycle, sequential by nature
        self,
        engine: CaseflowEngine,
        session: FakeSession,
        run_store: CaseRunStore,
        gate_store: GateStore,
        outbox_collection: FakeCollection,
    ) -> None:
        await _open_run(engine, session)
        run = await run_store.get_by_case_id(CASE_ID)
        assert run is not None
        run_id = run.snapshot.run_id

        # INTAKE: a document, the intake agent, the one PO gate.
        await engine.handle(
            _env("case.document_stored", {"doc_id": "doc_1"}, run_id=run_id), session
        )
        await engine.handle(
            _env("agent.done", {"agent": "intake"}, run_id=run_id), session
        )
        assert await gate_store.count_open(CASE_ID, GateType.PO_CONFIRM_CATEGORY) == 1
        po_gate = next(
            d
            for d in gate_store._collection.docs  # type: ignore[attr-defined]
            if d["gate_type"] == GateType.PO_CONFIRM_CATEGORY.value
        )
        await engine.handle(
            _env(
                "gate.decided",
                _gate_decision_payload(
                    gate_id=po_gate["_id"],
                    gate_type=GateType.PO_CONFIRM_CATEGORY,
                    run_id=run_id,
                    decision="confirm",
                    decided_by="u-pnair",
                ),
                run_id=run_id,
            ),
            session,
        )

        # Submit -> TRIAGE -> SCREENING, with the screening agent dispatched
        # by the internal-chain, no fast lane.
        await engine.handle(_env("case.submitted", {}, run_id=run_id), session)
        after_submit = await run_store.get(run_id)
        assert after_submit is not None
        assert after_submit.state is CaseState.SCREENING
        assert _published_agent_run_payloads(outbox_collection, AgentName.SCREENING)

        await engine.handle(
            _env(
                "agent.done",
                {"agent": "screening", "fast_lane_proposed": False},
                run_id=run_id,
            ),
            session,
        )
        after_screening = await run_store.get(run_id)
        assert after_screening is not None
        assert after_screening.state is CaseState.ASSESS
        assert after_screening.pending_agents == ("research", "assessment")

        # ASSESS (both agents) -> CRITIC, with the boundary-flagged Band 2
        # score this case's demo numbers start at (CLAUDE.md: 9.8, Band 2,
        # boundary flag).
        await engine.handle(
            _env("agent.done", {"agent": "research"}, run_id=run_id), session
        )
        await engine.handle(
            _env(
                "agent.done",
                {"agent": "assessment", "band": 2, "boundary_flagged": True},
                run_id=run_id,
            ),
            session,
        )
        after_assess = await run_store.get(run_id)
        assert after_assess is not None
        assert after_assess.state is CaseState.CRITIC
        assert after_assess.band == 2

        # CRITIC -> COMPLETENESS, narrative dispatched in completeness mode.
        await engine.handle(
            _env("agent.done", {"agent": "critic"}, run_id=run_id), session
        )
        completeness_payloads = _published_agent_run_payloads(
            outbox_collection, AgentName.NARRATIVE
        )
        assert any(p.get("scope") == "completeness" for p in completeness_payloads)

        # The completeness report opens its own gate; the analyst asks a
        # clarification round (D-16).
        await engine.handle(
            _env("agent.done", {"agent": "narrative"}, run_id=run_id), session
        )
        completeness_gate = next(
            d
            for d in gate_store._collection.docs  # type: ignore[attr-defined]
            if d["gate_type"] == GateType.COMPLETENESS.value and d["status"] == "open"
        )
        await engine.handle(
            _env(
                "gate.decided",
                _gate_decision_payload(
                    gate_id=completeness_gate["_id"],
                    gate_type=GateType.COMPLETENESS,
                    run_id=run_id,
                    decision="ask",
                    decided_by="u-riyer",
                ),
                run_id=run_id,
            ),
            session,
        )
        after_ask = await run_store.get(run_id)
        assert after_ask is not None
        assert after_ask.state is CaseState.CLARIFICATION
        assert after_ask.snapshot.flags.round_open is True

        # The owner answers; the rerun completes -> REVIEW, round closed.
        await engine.handle(
            _env(
                "case.answered",
                {"changed_field_ids": ["vendor_due_diligence"]},
                run_id=run_id,
            ),
            session,
        )
        await engine.handle(
            _env("agent.done", {"agent": "research"}, run_id=run_id), session
        )
        await engine.handle(
            _env("agent.done", {"agent": "assessment"}, run_id=run_id), session
        )
        after_rerun = await run_store.get(run_id)
        assert after_rerun is not None
        assert after_rerun.state is CaseState.REVIEW
        assert after_rerun.snapshot.flags.round_open is False

        # The vendor due-diligence override (demo: band 2 -> 3, boundary
        # flag stays on — the real band/boundary numbers come from a
        # re-score call to S2, out of this lane's scope; band_changed/
        # boundary_flagged are instead read straight off whatever this
        # event's own payload says, per fcrm_caseflow.engine's documented
        # convention): deciding the one review_item gate (this package's
        # own simplification of the real per-item fan-out — see
        # dispatch.py) opens the ack gate (R3).
        review_item_gate = next(
            d
            for d in gate_store._collection.docs  # type: ignore[attr-defined]
            if d["gate_type"] == GateType.REVIEW_ITEM.value and d["status"] == "open"
        )
        await engine.handle(
            _env(
                "gate.decided",
                {
                    "gate_id": review_item_gate["_id"],
                    "gate_type": GateType.REVIEW_ITEM.value,
                    "case_id": CASE_ID,
                    "run_id": run_id,
                    "decision": "override",
                    "reason": "vendor due diligence (CTL-T01) overridden 7.6 -> 4.5",
                    "decided_by": "u-riyer",
                    "decided_at": utcnow().isoformat(),
                    "band_changed": True,
                    "boundary_flagged": True,
                },
                run_id=run_id,
            ),
            session,
        )
        assert await gate_store.count_open(CASE_ID, GateType.ACK) == 1
        mid_review = await run_store.get(run_id)
        assert mid_review is not None
        assert mid_review.state is CaseState.REVIEW, (
            "all_items_decided is true but acks_done is not yet "
            "(the ack gate just opened) — ALL_ITEMS_DECIDED must not fire"
        )

        # Deciding the ack gate has no trigger of its own (informational
        # only — fcrm_caseflow.resolve's module docstring), but it is what
        # makes ALL_ITEMS_DECIDED's guard (all_items_decided AND
        # acks_done) newly legal; the engine's fallback internal-chain
        # check (handle()'s "no primary resolution" branch) is what
        # catches this.
        ack_gate = next(
            d
            for d in gate_store._collection.docs  # type: ignore[attr-defined]
            if d["gate_type"] == GateType.ACK.value and d["status"] == "open"
        )
        await engine.handle(
            _env(
                "gate.decided",
                {
                    "gate_id": ack_gate["_id"],
                    "gate_type": GateType.ACK.value,
                    "case_id": CASE_ID,
                    "run_id": run_id,
                    "decision": "acknowledge",
                    "reason": "band change and boundary flag reviewed",
                    "decided_by": "u-riyer",
                    "decided_at": utcnow().isoformat(),
                },
                run_id=run_id,
            ),
            session,
        )
        after_ack = await run_store.get(run_id)
        assert after_ack is not None
        assert after_ack.state is CaseState.RECOMMEND
        recommendation_payloads = _published_agent_run_payloads(
            outbox_collection, AgentName.NARRATIVE
        )
        assert any(p.get("scope") == "recommendation" for p in recommendation_payloads)

        # Lock, pack release, committee route (band 3), a no-change
        # question, and a 2-1 vote tally -> CLOSED (the demo's own outcome).
        await engine.handle(_env("case.locked", {}, run_id=run_id), session)
        assert await gate_store.count_open(CASE_ID, GateType.PACK_RELEASE) == 1
        locked = await run_store.get(run_id)
        assert locked is not None
        assert locked.snapshot.flags.locked is True
        assert locked.snapshot.flags.pack_version == 1

        await engine.handle(
            _env(
                "pack.released",
                {"pack_id": "pack_1", "release_checks_passed": True},
                run_id=run_id,
            ),
            session,
        )
        after_release = await run_store.get(run_id)
        assert after_release is not None
        assert after_release.state is CaseState.DECIDE
        assert after_release.snapshot.flags.vote_round == 1

        await engine.handle(
            _env(
                "committee.question_asked",
                {"thread_id": "thr_1", "member_id": "u-chen", "text": "?"},
                run_id=run_id,
            ),
            session,
        )
        await engine.handle(
            _env(
                "committee.answered.no_change",
                {"thread_id": "thr_1", "committee_numbers_changed": False},
                run_id=run_id,
            ),
            session,
        )
        still_decide = await run_store.get(run_id)
        assert still_decide is not None
        assert still_decide.state is CaseState.DECIDE

        for member in ("u-mendes", "u-okafor", "u-chen"):
            await engine.handle(
                _env("vote.cast", {"member_id": member}, run_id=run_id), session
            )

        await engine.handle(
            _env(
                "vote.tallied",
                {"vote_outcome": "approve_with_conditions"},
                run_id=run_id,
            ),
            session,
        )
        closed = await run_store.get(run_id)
        assert closed is not None
        assert closed.state is CaseState.CLOSED
        assert closed.snapshot.closed_at is not None

        # The whole real run replays through eventstore.rebuild, matching
        # design A §3.5's "replay must equal the snapshot" (already proven
        # for direct (trigger, context) pairs by the 19 pure-logic tests;
        # this proves it for rows this package's own CaseRunStore wrote).
        events = await run_store.load_recorded_transitions(run_id)
        replayed = rebuild(open_run(), events)
        assert replayed is closed.state is CaseState.CLOSED


class TestDispatchedInStateDoesNotRefireAcrossSeparateHandleCalls:
    """Regression test for the ``dispatched_in_state`` bug: an internal
    dispatch trigger (``COMPLETENESS_DISPATCH``) is an unconditional
    ``COMPLETENESS -> COMPLETENESS`` self-loop, and so is the external
    ``AGENT_DONE_NARRATIVE_COMPLETENESS`` trigger that fires once the
    narrative agent reports back. A naive "exclude the trigger that was
    just applied" guard stops ``COMPLETENESS_DISPATCH`` from re-firing
    *within* the same ``engine.handle()`` call, but a later, separate
    ``engine.handle()`` call that re-enters ``COMPLETENESS`` via the
    *different*, external trigger starts a fresh exclusion set — and
    would wrongly re-dispatch narrative a second time without
    ``CaseRun.dispatched_in_state`` persisting across calls.
    """

    async def test_narrative_is_dispatched_exactly_once_not_twice(
        self,
        engine: CaseflowEngine,
        session: FakeSession,
        run_store: CaseRunStore,
        gate_store: GateStore,
        outbox_collection: FakeCollection,
    ) -> None:
        await _open_run(engine, session)
        run = await run_store.get_by_case_id(CASE_ID)
        assert run is not None
        run_id = run.snapshot.run_id

        await engine.handle(
            _env("case.document_stored", {"doc_id": "doc_1"}, run_id=run_id), session
        )
        await engine.handle(
            _env("agent.done", {"agent": "intake"}, run_id=run_id), session
        )
        po_gate = next(
            d
            for d in gate_store._collection.docs  # type: ignore[attr-defined]
            if d["gate_type"] == GateType.PO_CONFIRM_CATEGORY.value
        )
        await engine.handle(
            _env(
                "gate.decided",
                _gate_decision_payload(
                    gate_id=po_gate["_id"],
                    gate_type=GateType.PO_CONFIRM_CATEGORY,
                    run_id=run_id,
                    decision="confirm",
                    decided_by="u-pnair",
                ),
                run_id=run_id,
            ),
            session,
        )
        await engine.handle(_env("case.submitted", {}, run_id=run_id), session)
        await engine.handle(
            _env(
                "agent.done",
                {"agent": "screening", "fast_lane_proposed": False},
                run_id=run_id,
            ),
            session,
        )
        await engine.handle(
            _env("agent.done", {"agent": "research"}, run_id=run_id), session
        )
        await engine.handle(
            _env(
                "agent.done",
                {"agent": "assessment", "band": 2, "boundary_flagged": True},
                run_id=run_id,
            ),
            session,
        )

        # CRITIC -> COMPLETENESS: COMPLETENESS_DISPATCH fires once, inside
        # this same engine.handle() call.
        await engine.handle(
            _env("agent.done", {"agent": "critic"}, run_id=run_id), session
        )
        after_first_dispatch = _published_agent_run_payloads(
            outbox_collection, AgentName.NARRATIVE
        )
        assert len(after_first_dispatch) == 1

        # A later, SEPARATE engine.handle() call: the narrative agent
        # reports back via AGENT_DONE_NARRATIVE_COMPLETENESS, an external
        # COMPLETENESS -> COMPLETENESS self-loop. Without
        # dispatched_in_state surviving across the two calls, this would
        # see a fresh exclusion set and wrongly re-dispatch narrative.
        after_run = await run_store.get(run_id)
        assert after_run is not None
        assert after_run.state is CaseState.COMPLETENESS
        await engine.handle(
            _env("agent.done", {"agent": "narrative"}, run_id=run_id), session
        )
        after_second_call = _published_agent_run_payloads(
            outbox_collection, AgentName.NARRATIVE
        )
        assert len(after_second_call) == 1, (
            "narrative was dispatched more than once across two separate "
            "engine.handle() calls on the same COMPLETENESS self-loop"
        )

"""Engine behaviour for the wire events ADR-28 added: an ingest result, a
planned control, a committee answer that changes the numbers, a delegated
outcome, and the R3 guard now reading H1's own open gates.

The run is moved straight to the state under test the way the engine's other
tests do (``CaseRunStore.apply_transition``): driving the full lifecycle to
each state would be noise, and ``test_engine.py`` already does it once.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
from conftest import FakeSession
from fcrm_caseflow.dispatch import REQUIRED_ROLE
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.gates import GateStore, gate_document
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.store import CaseRun, CaseRunStore
from fcrm_caseflow.transitions import TransitionContext
from fcrm_caseflow.triggers import Trigger
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import CaseState, GateType
from fcrm_contracts.events.envelope import Envelope

CASE_ID = "FCRM-2026-0151"


def _env(event_type: str, payload: dict[str, Any], run_id: str) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id=CASE_ID,
        run_id=run_id,
        occurred_at=utcnow(),
        payload=payload,
    )


async def _run_in(
    engine: CaseflowEngine,
    run_store: CaseRunStore,
    session: FakeSession,
    state: CaseState,
    *,
    flags: Optional[RunFlags] = None,
    band: Optional[int] = None,
) -> CaseRun:
    """Opens a run and moves it to ``state`` (two moves, so the internal
    dispatch bookkeeping looks like a real arrival, as test_engine does).
    """
    await engine.handle(
        Envelope(
            event_id=new_id("evt"),
            type="case.created",
            case_id=CASE_ID,
            occurred_at=utcnow(),
            payload={"actor": "u-pnair", "data": {}},
        ),
        session,
    )
    run = await run_store.get_by_case_id(CASE_ID)
    assert run is not None
    for _ in range(2):
        run = await run_store.apply_transition(
            run,
            to_state=state,
            flags=flags or run.snapshot.flags,
            trigger=Trigger.CASE_DOCUMENT_STORED,
            action="test setup",
            message_id=None,
            context=TransitionContext(),
            session=session,  # type: ignore[arg-type]
        )
    if band is not None:
        await run_store.set_band(run.snapshot.run_id, band)
    reloaded = await run_store.get(run.snapshot.run_id)
    assert reloaded is not None
    return reloaded


@pytest.mark.parametrize("state", [CaseState.INTAKE, CaseState.CLARIFICATION])
@pytest.mark.parametrize("event_type", ["ingest.done", "ingest.failed"])
async def test_an_ingest_result_leaves_intake_and_clarification_where_they_are(
    engine: CaseflowEngine,
    run_store: CaseRunStore,
    session: FakeSession,
    state: CaseState,
    event_type: str,
) -> None:
    run = await _run_in(engine, run_store, session, state)
    version = run.snapshot.version

    await engine.handle(
        _env(event_type, {"job_id": "job_1"}, run.snapshot.run_id), session
    )

    after = await run_store.get(run.snapshot.run_id)
    assert after is not None
    assert after.state is state
    assert after.snapshot.version == version + 1, "recorded as a transition"


async def test_an_ingest_result_where_no_document_is_taken_is_an_illegal_transition(
    engine: CaseflowEngine, run_store: CaseRunStore, session: FakeSession
) -> None:
    # A document reaches H3 at intake, in a clarification round, or with the
    # answer to an ad-hoc question at any stage the analyst holds the case
    # (ADR-73); REVIEW_AND_SUBMIT takes none.
    run = await _run_in(engine, run_store, session, CaseState.REVIEW_AND_SUBMIT)
    with pytest.raises(IllegalTransition):
        await engine.handle(
            _env("ingest.done", {"job_id": "job_1"}, run.snapshot.run_id), session
        )


async def _open_review_item(
    gate_store: GateStore, session: FakeSession, run_id: str
) -> None:
    """One review item the analyst has yet to decide, as the (future)
    ``assess.publish`` step opens them. With none open, entering REVIEW
    chains straight on to RECOMMEND (``all_items_decided`` counts open gates).
    """
    await gate_store.open(
        gate_document(
            gate_id=new_id("gate"),
            case_id=CASE_ID,
            run_id=run_id,
            gate_type=GateType.REVIEW_ITEM,
            required_role=REQUIRED_ROLE[GateType.REVIEW_ITEM],
        ),
        session=session,  # type: ignore[arg-type]
    )


async def test_a_planned_control_in_review_is_recorded_and_changes_no_state(
    engine: CaseflowEngine,
    run_store: CaseRunStore,
    gate_store: GateStore,
    session: FakeSession,
) -> None:
    run = await _run_in(engine, run_store, session, CaseState.REVIEW)
    await _open_review_item(gate_store, session, run.snapshot.run_id)
    await engine.handle(
        _env(
            "case.control_planned",
            {"planned_control": {"name": "Corridor DD review"}},
            run.snapshot.run_id,
        ),
        session,
    )
    after = await run_store.get(run.snapshot.run_id)
    assert after is not None
    assert after.state is CaseState.REVIEW
    assert after.snapshot.version == run.snapshot.version + 1


async def test_changed_committee_numbers_unlock_and_return_the_case_to_review(
    engine: CaseflowEngine,
    run_store: CaseRunStore,
    gate_store: GateStore,
    session: FakeSession,
) -> None:
    run = await _run_in(
        engine,
        run_store,
        session,
        CaseState.DECIDE,
        flags=RunFlags(locked=True, pack_version=1, vote_round=1),
    )
    # The item the analyst must re-decide after the unlock (the I4 unlock step
    # reopens them); without one, REVIEW chains straight back to RECOMMEND.
    await _open_review_item(gate_store, session, run.snapshot.run_id)
    await engine.handle(
        _env(
            "committee.answered",
            {
                "pack_id": "pack_1",
                "thread_id": "thr_1",
                "by": "u-riyer",
                "numbers_changed": True,
                "unlock_reason": "CTL-T01 rating corrected",
            },
            run.snapshot.run_id,
        ),
        session,
    )
    after = await run_store.get(run.snapshot.run_id)
    assert after is not None
    assert after.state is CaseState.REVIEW
    assert after.snapshot.flags.locked is False


async def test_a_committee_answer_with_unchanged_numbers_stays_in_decide(
    engine: CaseflowEngine, run_store: CaseRunStore, session: FakeSession
) -> None:
    run = await _run_in(
        engine,
        run_store,
        session,
        CaseState.DECIDE,
        flags=RunFlags(locked=True, pack_version=1, vote_round=1),
    )
    await engine.handle(
        _env(
            "committee.answered",
            {"pack_id": "pack_1", "thread_id": "thr_1", "numbers_changed": False},
            run.snapshot.run_id,
        ),
        session,
    )
    after = await run_store.get(run.snapshot.run_id)
    assert after is not None
    assert after.state is CaseState.DECIDE
    assert after.snapshot.flags.locked is True


async def test_a_delegated_outcome_closes_the_run(
    engine: CaseflowEngine, run_store: CaseRunStore, session: FakeSession
) -> None:
    run = await _run_in(engine, run_store, session, CaseState.DECIDE, band=2)
    await engine.handle(
        _env(
            "outcome.set",
            {"case_id": CASE_ID, "mode": "delegated", "outcome": "approve"},
            run.snapshot.run_id,
        ),
        session,
    )
    after = await run_store.get(run.snapshot.run_id)
    assert after is not None
    assert after.state is CaseState.CLOSED


async def test_a_committee_outcome_event_is_informational_in_decide(
    engine: CaseflowEngine, run_store: CaseRunStore, session: FakeSession
) -> None:
    run = await _run_in(engine, run_store, session, CaseState.DECIDE, band=3)
    await engine.handle(
        _env(
            "outcome.set",
            {"case_id": CASE_ID, "mode": "committee", "outcome": "approve"},
            run.snapshot.run_id,
        ),
        session,
    )
    after = await run_store.get(run.snapshot.run_id)
    assert after is not None
    assert after.state is CaseState.DECIDE


class TestCloseRequestReadsH1sOwnGates:
    """R3: a delegated close needs every acknowledgement done. The guard used
    to take ``acks_done`` from the payload (default true); now H1 counts its
    own open ``ack`` gates, so no event can assert it.
    """

    @staticmethod
    async def _open_ack_gate(
        gate_store: GateStore, session: FakeSession, run_id: str
    ) -> None:
        await gate_store.open(
            gate_document(
                gate_id=new_id("gate"),
                case_id=CASE_ID,
                run_id=run_id,
                gate_type=GateType.ACK,
                required_role=REQUIRED_ROLE[GateType.ACK],
            ),
            session=session,  # type: ignore[arg-type]
        )

    async def test_an_open_ack_gate_blocks_the_close_even_if_the_payload_says_done(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
    ) -> None:
        run = await _run_in(engine, run_store, session, CaseState.DECIDE, band=2)
        await self._open_ack_gate(gate_store, session, run.snapshot.run_id)

        with pytest.raises(IllegalTransition):
            await engine.handle(
                _env(
                    "case.close_requested",
                    {"outcome": "approve", "band": 2, "acks_done": True},
                    run.snapshot.run_id,
                ),
                session,
            )

    async def test_with_no_open_ack_gate_the_close_proceeds(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        session: FakeSession,
    ) -> None:
        run = await _run_in(engine, run_store, session, CaseState.DECIDE, band=2)
        version = run.snapshot.version
        await engine.handle(
            _env(
                "case.close_requested",
                {"outcome": "approve", "band": 2},
                run.snapshot.run_id,
            ),
            session,
        )
        after = await run_store.get(run.snapshot.run_id)
        assert after is not None
        assert after.snapshot.version == version + 1
        assert after.state is CaseState.DECIDE, "awaits outcome.set"

    async def test_a_band_three_close_is_still_refused(
        self,
        engine: CaseflowEngine,
        run_store: CaseRunStore,
        session: FakeSession,
    ) -> None:
        run = await _run_in(engine, run_store, session, CaseState.DECIDE)
        with pytest.raises(IllegalTransition):
            await engine.handle(
                _env(
                    "case.close_requested",
                    {"outcome": "approve", "band": 3},
                    run.snapshot.run_id,
                ),
                session,
            )

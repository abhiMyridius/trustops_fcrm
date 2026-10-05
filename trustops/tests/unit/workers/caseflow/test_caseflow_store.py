"""Tests for :mod:`fcrm_caseflow.store`: opening a run, persisting
transitions, and folding the persisted ``run_events`` back through
:func:`fcrm_caseflow.eventstore.rebuild` — proving a *real* run (one this
package's own ``CaseRunStore`` wrote) replays exactly like the 19
pure-logic tests already prove for direct ``(trigger, context)`` pairs.
"""

from __future__ import annotations

from conftest import FakeSession
from fcrm_caseflow.eventstore import rebuild
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    CaseRunStore,
    new_case_run_document,
    pins_from_case_event_data,
)
from fcrm_caseflow.transitions import TransitionContext, apply, open_run
from fcrm_caseflow.triggers import Trigger
from fcrm_contracts.enums import CaseState

CASE_ID = "FCRM-2026-0151"


async def test_create_opens_a_run_in_intake(run_store: CaseRunStore) -> None:
    doc = new_case_run_document(run_id="run_1", case_id=CASE_ID, pins=DEFAULT_PINS)
    run = await run_store.create(doc)
    assert run.state is CaseState.INTAKE
    assert run.snapshot.version == 0
    assert run.pins == DEFAULT_PINS


async def test_pins_from_case_event_data_falls_back_to_default() -> None:
    assert pins_from_case_event_data({}) == DEFAULT_PINS


async def test_pins_from_case_event_data_honours_an_explicit_pins_key() -> None:
    explicit = DEFAULT_PINS.model_copy(update={"scoring_v": 99})
    pins = pins_from_case_event_data({"pins": explicit.model_dump(mode="json")})
    assert pins.scoring_v == 99


async def test_get_by_case_id_finds_the_open_run(run_store: CaseRunStore) -> None:
    doc = new_case_run_document(run_id="run_1", case_id=CASE_ID, pins=DEFAULT_PINS)
    await run_store.create(doc)
    found = await run_store.get_by_case_id(CASE_ID)
    assert found is not None
    assert found.snapshot.run_id == "run_1"


async def test_apply_transition_bumps_version_and_moves_state(
    run_store: CaseRunStore, session: FakeSession
) -> None:
    doc = new_case_run_document(run_id="run_1", case_id=CASE_ID, pins=DEFAULT_PINS)
    run = await run_store.create(doc)
    updated = await run_store.apply_transition(
        run,
        to_state=CaseState.INTAKE,
        flags=RunFlags(),
        trigger=Trigger.CASE_DOCUMENT_STORED,
        action="ingest.run",
        message_id="evt_1",
        context=TransitionContext(),
        session=session,
    )
    assert updated.snapshot.version == 1
    assert updated.state is CaseState.INTAKE

    reloaded = await run_store.get("run_1")
    assert reloaded is not None
    assert reloaded.snapshot.version == 1


async def test_apply_transition_stamps_closed_at_on_reaching_closed(
    run_store: CaseRunStore, session: FakeSession
) -> None:
    doc = new_case_run_document(run_id="run_1", case_id=CASE_ID, pins=DEFAULT_PINS)
    run = await run_store.create(doc)
    updated = await run_store.apply_transition(
        run,
        to_state=CaseState.CLOSED,
        flags=RunFlags(),
        trigger=Trigger.VOTE_TALLIED,
        action="close",
        message_id=None,
        context=TransitionContext(),
        session=session,
    )
    assert updated.snapshot.closed_at is not None


async def test_pending_agents_round_trip(run_store: CaseRunStore) -> None:
    doc = new_case_run_document(run_id="run_1", case_id=CASE_ID, pins=DEFAULT_PINS)
    await run_store.create(doc)
    await run_store.set_pending_agents("run_1", ["research", "assessment"])
    remaining = await run_store.remove_pending_agent("run_1", "research")
    assert remaining == ["assessment"]
    remaining = await run_store.remove_pending_agent("run_1", "assessment")
    assert remaining == []


async def test_band_round_trips(run_store: CaseRunStore) -> None:
    doc = new_case_run_document(run_id="run_1", case_id=CASE_ID, pins=DEFAULT_PINS)
    await run_store.create(doc)
    await run_store.set_band("run_1", 3)
    run = await run_store.get("run_1")
    assert run is not None
    assert run.band == 3


async def test_a_real_run_replays_through_eventstore_rebuild(
    run_store: CaseRunStore, session: FakeSession
) -> None:
    """Mirrors ``test_transitions.py``'s own replay test, but against rows
    this package's ``CaseRunStore`` actually wrote, not a hand-built list.
    """
    doc = new_case_run_document(run_id="run_1", case_id=CASE_ID, pins=DEFAULT_PINS)
    run = await run_store.create(doc)

    sequence = [
        (Trigger.CASE_DOCUMENT_STORED, TransitionContext()),
        (Trigger.AGENT_DONE_INTAKE, TransitionContext()),
        (
            Trigger.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL,
            TransitionContext(gaps_open=False),
        ),
        (Trigger.CASE_SUBMITTED, TransitionContext(four_submit_checks_passed=True)),
    ]
    state = run.state
    for trigger, ctx in sequence:
        result = apply(state, trigger, ctx)
        run = await run_store.apply_transition(
            run,
            to_state=result.to_state,
            flags=run.snapshot.flags,
            trigger=trigger,
            action=result.action,
            message_id=None,
            context=ctx,
            session=session,
        )
        state = result.to_state

    events = await run_store.load_recorded_transitions("run_1")
    assert len(events) == len(sequence)
    replayed_state = rebuild(open_run(), events)
    assert replayed_state is state is CaseState.TRIAGE

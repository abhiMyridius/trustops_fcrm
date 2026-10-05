"""What the step mechanism added to :mod:`fcrm_caseflow.store`:
``pending_steps`` / ``refs`` / ``counters`` on :class:`CaseRun` and the atomic
operators behind them, the ``step_done`` run event, and the BSON-safety fix
for the context stored on every transition row.
"""

from __future__ import annotations

import logging
from typing import Any

import bson
import pytest
from conftest import FakeCollection, FakeSession
from fcrm_caseflow.eventstore import rebuild
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    CaseRunStore,
    case_run_from_document,
    context_from_document,
    context_to_document,
    new_case_run_document,
    pins_from_case_event_data,
    recorded_transition_from_document,
    run_event_document,
    step_done_document,
)
from fcrm_caseflow.transitions import TransitionContext, apply, open_run
from fcrm_caseflow.triggers import Trigger
from fcrm_contracts.enums import CaseState
from pymongo.errors import DuplicateKeyError

CASE_ID = "FCRM-2026-0151"
RUN_ID = "run_1"


async def _created(run_store: CaseRunStore) -> None:
    await run_store.create(
        new_case_run_document(run_id=RUN_ID, case_id=CASE_ID, pins=DEFAULT_PINS)
    )


class TestPinsFromCaseCreated:
    def test_the_pins_the_event_carries_are_used(self) -> None:
        pins = DEFAULT_PINS.model_copy(update={"jurisdictions_v": "v2026-06"})
        assert pins_from_case_event_data({"pins": pins.model_dump(mode="json")}) == pins

    def test_missing_pins_fall_back_to_the_placeholder_loudly(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="fcrm_caseflow.store"):
            assert pins_from_case_event_data({}) == DEFAULT_PINS
        assert any("DEFAULT_PINS" in r.getMessage() for r in caplog.records)


class TestNewRunFields:
    async def test_a_new_run_starts_with_no_pending_steps_refs_or_counters(
        self, run_store: CaseRunStore
    ) -> None:
        await _created(run_store)
        run = await run_store.get(RUN_ID)
        assert run is not None
        assert run.pending_steps == ()
        assert run.refs == {}
        assert run.counters == {}

    def test_a_run_stored_before_the_fields_existed_still_loads(self) -> None:
        document = new_case_run_document(
            run_id=RUN_ID, case_id=CASE_ID, pins=DEFAULT_PINS
        )
        for key in ("pending_steps", "refs", "counters"):
            del document[key]
        run = case_run_from_document(document)
        assert (run.pending_steps, run.refs, run.counters) == ((), {}, {})

    def test_two_runs_never_share_one_refs_dict(self) -> None:
        a = case_run_from_document(
            new_case_run_document(run_id="a", case_id=CASE_ID, pins=DEFAULT_PINS)
        )
        b = case_run_from_document(
            new_case_run_document(run_id="b", case_id=CASE_ID, pins=DEFAULT_PINS)
        )
        a.refs["x"] = 1
        assert b.refs == {}


class TestPendingSteps:
    async def test_add_is_atomic_and_idempotent(
        self, run_store: CaseRunStore, session: FakeSession
    ) -> None:
        await _created(run_store)
        await run_store.add_pending_step(RUN_ID, "step_a", session=session)  # type: ignore[arg-type]
        await run_store.add_pending_step(RUN_ID, "step_b", session=session)  # type: ignore[arg-type]
        await run_store.add_pending_step(RUN_ID, "step_a", session=session)  # type: ignore[arg-type]
        run = await run_store.get(RUN_ID)
        assert run is not None
        assert run.pending_steps == ("step_a", "step_b")

    async def test_remove_returns_what_is_still_pending(
        self, run_store: CaseRunStore, session: FakeSession
    ) -> None:
        await _created(run_store)
        for step_id in ("step_a", "step_b"):
            await run_store.add_pending_step(RUN_ID, step_id, session=session)  # type: ignore[arg-type]

        left = await run_store.remove_pending_step(RUN_ID, "step_a", session=session)  # type: ignore[arg-type]
        assert left == ["step_b"]
        assert (
            await run_store.remove_pending_step(RUN_ID, "step_b", session=session) == []
        )  # type: ignore[arg-type]
        assert (
            await run_store.remove_pending_step(RUN_ID, "step_b", session=session) == []
        )  # type: ignore[arg-type]


class TestRefsAndCounters:
    async def test_merge_refs_sets_only_the_given_keys(
        self, run_store: CaseRunStore, session: FakeSession
    ) -> None:
        await _created(run_store)
        await run_store.merge_refs(RUN_ID, {"a": 1, "b": "x"}, session=session)  # type: ignore[arg-type]
        await run_store.merge_refs(RUN_ID, {"b": "y", "c": {"n": 2}}, session=session)  # type: ignore[arg-type]
        run = await run_store.get(RUN_ID)
        assert run is not None
        assert run.refs == {"a": 1, "b": "y", "c": {"n": 2}}

    async def test_an_empty_patch_writes_nothing(
        self, run_store: CaseRunStore, case_runs_collection: FakeCollection
    ) -> None:
        await _created(run_store)
        before = len(case_runs_collection.sessions)
        await run_store.merge_refs(RUN_ID, {}, session=FakeSession())  # type: ignore[arg-type]
        await run_store.bump_counters(RUN_ID, {}, session=FakeSession())  # type: ignore[arg-type]
        assert len(case_runs_collection.sessions) == before

    @pytest.mark.parametrize("key", ["", "a.b", "$set"])
    async def test_a_key_that_would_become_a_nested_path_or_operator_is_refused(
        self, run_store: CaseRunStore, session: FakeSession, key: str
    ) -> None:
        await _created(run_store)
        with pytest.raises(ValueError, match="unusable"):
            await run_store.merge_refs(RUN_ID, {key: 1}, session=session)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="unusable"):
            await run_store.bump_counters(RUN_ID, {key: 1}, session=session)  # type: ignore[arg-type]

    async def test_bump_counters_adds_atomically(
        self, run_store: CaseRunStore, session: FakeSession
    ) -> None:
        await _created(run_store)
        await run_store.bump_counters(
            RUN_ID, {"agents_run": 2, "tokens_used": 5000}, session=session
        )  # type: ignore[arg-type]
        await run_store.bump_counters(RUN_ID, {"agents_run": 1}, session=session)  # type: ignore[arg-type]
        run = await run_store.get(RUN_ID)
        assert run is not None
        assert run.counters == {"agents_run": 3, "tokens_used": 5000}

    async def test_apply_transition_keeps_the_new_fields_on_the_returned_run(
        self, run_store: CaseRunStore, session: FakeSession
    ) -> None:
        await _created(run_store)
        await run_store.add_pending_step(RUN_ID, "step_a", session=session)  # type: ignore[arg-type]
        await run_store.merge_refs(RUN_ID, {"k": "v"}, session=session)  # type: ignore[arg-type]
        await run_store.bump_counters(RUN_ID, {"agents_run": 1}, session=session)  # type: ignore[arg-type]
        run = await run_store.get(RUN_ID)
        assert run is not None

        moved = await run_store.apply_transition(
            run,
            to_state=CaseState.INTAKE,
            flags=run.snapshot.flags,
            trigger=Trigger.CASE_DOCUMENT_STORED,
            action="x",
            message_id="evt_1",
            context=TransitionContext(),
            session=session,  # type: ignore[arg-type]
        )

        assert moved.pending_steps == ("step_a",)
        assert moved.refs == {"k": "v"}
        assert moved.counters == {"agents_run": 1}


class TestStepDoneRows:
    async def test_has_step_done_is_false_until_recorded(
        self, run_store: CaseRunStore, session: FakeSession
    ) -> None:
        await _created(run_store)
        run = await run_store.get(RUN_ID)
        assert run is not None
        assert not await run_store.has_step_done(RUN_ID, "step_a")

        await run_store.record_step_done(
            run, "step_a", "intake.publish", session=session
        )  # type: ignore[arg-type]

        assert await run_store.has_step_done(RUN_ID, "step_a")
        assert not await run_store.has_step_done(RUN_ID, "step_b")
        assert not await run_store.has_step_done("run_other", "step_a")

    async def test_recording_takes_the_next_seq_and_bumps_the_version(
        self, run_store: CaseRunStore, session: FakeSession
    ) -> None:
        await _created(run_store)
        run = await run_store.get(RUN_ID)
        assert run is not None

        after = await run_store.record_step_done(
            run, "step_a", "intake.publish", session=session
        )  # type: ignore[arg-type]

        assert after.snapshot.version == run.snapshot.version + 1
        stored = await run_store.get(RUN_ID)
        assert stored is not None
        assert stored.snapshot.version == after.snapshot.version

    async def test_a_second_writer_with_the_same_seq_or_step_is_refused(
        self,
        run_store: CaseRunStore,
        session: FakeSession,
    ) -> None:
        """The unique ``{run_id, seq}`` and ``step_done`` indexes make a racing
        writer fail (and its transaction retry) instead of double-recording.
        """
        await _created(run_store)
        run = await run_store.get(RUN_ID)
        assert run is not None
        await run_store.record_step_done(
            run, "step_a", "intake.publish", session=session
        )  # type: ignore[arg-type]
        with pytest.raises(DuplicateKeyError):  # same seq stale snapshot
            await run_store.record_step_done(
                run, "step_b", "intake.publish", session=session
            )  # type: ignore[arg-type]
        fresh = await run_store.get(RUN_ID)
        assert fresh is not None
        with pytest.raises(DuplicateKeyError):  # same step id, next seq
            await run_store.record_step_done(
                fresh, "step_a", "intake.publish", session=session
            )  # type: ignore[arg-type]

    async def test_replay_folds_transitions_only(
        self, run_store: CaseRunStore, session: FakeSession
    ) -> None:
        await _created(run_store)
        run = await run_store.get(RUN_ID)
        assert run is not None
        state = open_run()
        for trigger, ctx in [
            (Trigger.CASE_DOCUMENT_STORED, TransitionContext()),
            (Trigger.CASE_SUBMITTED, TransitionContext(four_submit_checks_passed=True)),
        ]:
            result = apply(state, trigger, ctx)
            run = await run_store.apply_transition(
                run,
                to_state=result.to_state,
                flags=run.snapshot.flags,
                trigger=trigger,
                action=result.action,
                message_id=None,
                context=ctx,
                session=session,  # type: ignore[arg-type]
            )
            run = await run_store.record_step_done(
                run, f"step_{trigger.value}", "x", session=session
            )  # type: ignore[arg-type]
            state = result.to_state

        events = await run_store.load_recorded_transitions(RUN_ID)
        assert len(events) == 2
        assert rebuild(open_run(), events) is state is CaseState.TRIAGE


class TestIndexes:
    async def test_every_new_query_path_has_an_index(
        self,
        run_store: CaseRunStore,
        case_runs_collection: FakeCollection,
        run_events_collection: FakeCollection,
    ) -> None:
        names = await run_store.ensure_indexes()

        specs = {
            i.document["name"]: i.document
            for i in [*run_events_collection.indexes, *case_runs_collection.indexes]
        }
        assert (
            set(names)
            == set(specs)
            == {
                "run_id_seq_unique",
                "step_done_unique",
                "case_id_open",
            }
        )
        step_done = specs["step_done_unique"]
        assert list(step_done["key"].keys()) == ["run_id", "kind", "data.step_id"]
        assert step_done["unique"] is True
        assert step_done["partialFilterExpression"] == {"kind": "step_done"}
        assert list(specs["case_id_open"]["key"].keys()) == [
            "case_id",
            "closed_at",
            "opened_at",
        ]


class TestContextIsBsonSafe:
    """``context.__dict__`` held a ``RunFlags`` object, which PyMongo cannot
    encode: the first transition against a real MongoDB raised
    ``InvalidDocument``. Fixed here; ``bson.encode`` is the real check, no
    server needed.
    """

    def test_a_transition_row_encodes_to_bson(self) -> None:
        document = run_event_document(
            run_id=RUN_ID,
            seq=0,
            kind="transition",
            trigger=Trigger.CASE_SUBMITTED,
            state_from=CaseState.INTAKE,
            state_to=CaseState.TRIAGE,
            message_id=None,
            context=TransitionContext(
                flags=RunFlags(locked=True, pack_version=2), band=3
            ),
            action="x",
        )
        assert bson.decode(bson.encode(document))["data"]["context"]["flags"] == {
            "fast_lane": False,
            "round_open": False,
            "locked": True,
            "pack_version": 2,
            "vote_round": 0,
        }

    def test_a_new_run_and_a_step_done_row_encode_to_bson(self) -> None:
        run = new_case_run_document(run_id=RUN_ID, case_id=CASE_ID, pins=DEFAULT_PINS)
        done = step_done_document(
            run_id=RUN_ID,
            seq=1,
            state=CaseState.INTAKE,
            step_id="step_a",
            step="intake.publish",
        )
        assert bson.decode(bson.encode(run))["pending_steps"] == []
        assert bson.decode(bson.encode(done))["data"] == {
            "step_id": "step_a",
            "step": "intake.publish",
        }

    def test_the_context_round_trips_through_its_document(self) -> None:
        context = TransitionContext(
            flags=RunFlags(round_open=True, vote_round=2),
            four_submit_checks_passed=False,
            rerun_reason="vendor contract amended",
            band=3,
            vote_outcome="approve_with_conditions",
        )
        assert context_from_document(context_to_document(context)) == context

    def test_a_row_survives_the_trip_through_bson_and_replays(self) -> None:
        context = TransitionContext(
            flags=RunFlags(locked=True), release_checks_passed=True
        )
        document = run_event_document(
            run_id=RUN_ID,
            seq=3,
            kind="transition",
            trigger=Trigger.PACK_RELEASED,
            state_from=CaseState.RECOMMEND,
            state_to=CaseState.DECIDE,
            message_id="evt_1",
            context=context,
            action="x",
        )
        recorded = recorded_transition_from_document(bson.decode(bson.encode(document)))
        assert recorded.trigger is Trigger.PACK_RELEASED
        assert recorded.context == context

    def test_a_field_a_later_version_dropped_does_not_break_a_replay(self) -> None:
        stored: dict[str, Any] = context_to_document(TransitionContext())
        stored["a_field_that_no_longer_exists"] = True
        assert context_from_document(stored) == TransitionContext()

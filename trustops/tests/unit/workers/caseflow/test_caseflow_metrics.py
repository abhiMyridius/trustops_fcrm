"""H1 Caseflow's metrics: ``fcrm_case_stage_transitions_total``,
``fcrm_case_submit_to_decide_seconds`` and ``fcrm_gate_wait_seconds``.

The engine buffers each observation per transaction attempt and records it
only after the commit (:func:`fcrm_caseflow.engine.flush_pending_metrics`),
so a transaction that re-runs or aborts never counts a transition twice.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import pytest
from conftest import FakeCollection, FakePublisher, FakeSession, FakeTransact
from fcrm_caseflow.consumers import CaseEventsConsumer
from fcrm_caseflow.dispatch import REQUIRED_ROLE
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore, gate_document
from fcrm_caseflow.steps import StepResult
from fcrm_caseflow.store import TRANSITION_KIND, CaseRunStore, run_event_document
from fcrm_caseflow.transitions import TransitionContext
from fcrm_caseflow.triggers import Trigger
from fcrm_common.events import Disposition
from fcrm_common.ids import new_id
from fcrm_common.metrics import get_metrics
from fcrm_common.time import utcnow
from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import CaseState, GateType
from fcrm_contracts.events.envelope import Envelope
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from pymongo.errors import ConnectionFailure

CASE_ID = "FCRM-2026-0151"
TRANSITIONS = "fcrm_case_stage_transitions_total"
SUBMIT_TO_DECIDE = "fcrm_case_submit_to_decide_seconds"
GATE_WAIT = "fcrm_gate_wait_seconds"


def _metered(
    engine_factory: Callable[..., CaseflowEngine], **overrides: Any
) -> tuple[CaseflowEngine, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    engine = engine_factory(
        metrics=get_metrics(MeterProvider(metric_readers=[reader])), **overrides
    )
    return engine, reader


def _points(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    data = reader.get_metrics_data()
    out: dict[str, list[Any]] = {}
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                points = list(metric.data.data_points)
                if points:
                    out[metric.name] = points
    return out


def _transition_counts(reader: InMemoryMetricReader) -> dict[str, float]:
    return {
        str(p.attributes["to"]): p.value for p in _points(reader).get(TRANSITIONS, [])
    }


def _env(event_type: str, payload: dict[str, Any], run_id: Optional[str]) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id=CASE_ID,
        run_id=run_id,
        occurred_at=utcnow(),
        payload=payload,
    )


async def _open_run_in(
    engine: CaseflowEngine,
    session: FakeSession,
    case_runs: FakeCollection,
    state: CaseState,
) -> str:
    """Opens the demo case's run and puts it straight into ``state``."""
    await engine.handle(
        _env("case.created", {"actor": "u-pnair", "data": {}}, None), session
    )
    doc = case_runs.docs[0]
    doc["state"] = state.value
    return str(doc["_id"])


def _vote_tallied(run_id: str) -> Envelope:
    return _env("vote.tallied", {"vote_outcome": "approve_with_conditions"}, run_id)


def _submitted_row(run_id: str, at: datetime) -> dict[str, Any]:
    row = run_event_document(
        run_id=run_id,
        seq=-1,
        kind=TRANSITION_KIND,
        trigger=Trigger.CASE_SUBMITTED,
        state_from=CaseState.INTAKE,
        state_to=CaseState.TRIAGE,
        message_id="evt_submit",
        context=TransitionContext(),
        action="record case.submitted",
    )
    row["at"] = at
    return row


class TestStageTransitions:
    async def test_a_move_is_counted_once_and_only_after_the_flush(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
        run_store: CaseRunStore,
    ) -> None:
        engine, reader = _metered(engine_factory)
        run_id = await _open_run_in(
            engine, session, case_runs_collection, CaseState.DECIDE
        )

        await engine.handle(_vote_tallied(run_id), session)

        run = await run_store.get(run_id)
        assert run is not None
        assert run.state is CaseState.CLOSED
        assert _points(reader) == {}
        engine.flush_metrics()
        # CLOSED_DISPATCH (CLOSED -> CLOSED) also ran; a self-loop is not a move.
        assert _transition_counts(reader) == {"closed": 1}
        engine.flush_metrics()
        assert _transition_counts(reader) == {"closed": 1}

    async def test_a_self_loop_records_nothing(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
        run_store: CaseRunStore,
    ) -> None:
        engine, reader = _metered(engine_factory)
        run_id = await _open_run_in(
            engine, session, case_runs_collection, CaseState.DECIDE
        )

        await engine.handle(
            _env(
                "committee.question_asked",
                {"thread_id": "thr_1", "member_id": "u-chen", "text": "?"},
                run_id,
            ),
            session,
        )
        engine.flush_metrics()

        run = await run_store.get(run_id)
        assert run is not None
        assert run.state is CaseState.DECIDE
        assert TRANSITIONS not in _points(reader)

    async def test_a_new_attempt_starts_with_an_empty_buffer(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
    ) -> None:
        """An attempt that never committed (no flush) leaves nothing behind for
        the next attempt's flush.
        """
        engine, reader = _metered(engine_factory)
        run_id = await _open_run_in(
            engine, session, case_runs_collection, CaseState.DECIDE
        )
        await engine.handle(_vote_tallied(run_id), session)  # aborted: no flush
        assert case_runs_collection.docs[0]["state"] == CaseState.CLOSED.value

        await engine.handle(_env("index.built", {}, None), session)
        engine.flush_metrics()

        assert _points(reader) == {}


class TestSubmitToDecide:
    async def test_closing_records_the_time_since_case_submitted(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
        run_events_collection: FakeCollection,
    ) -> None:
        engine, reader = _metered(engine_factory)
        run_id = await _open_run_in(
            engine, session, case_runs_collection, CaseState.DECIDE
        )
        run_events_collection.docs.append(
            _submitted_row(run_id, utcnow() - timedelta(hours=1))
        )

        await engine.handle(_vote_tallied(run_id), session)
        engine.flush_metrics()

        (point,) = _points(reader)[SUBMIT_TO_DECIDE]
        assert point.count == 1
        assert 3600 <= point.sum < 3660
        assert dict(point.attributes) == {}

    async def test_a_run_with_no_case_submitted_row_records_nothing(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
    ) -> None:
        engine, reader = _metered(engine_factory)
        run_id = await _open_run_in(
            engine, session, case_runs_collection, CaseState.DECIDE
        )

        await engine.handle(_vote_tallied(run_id), session)
        engine.flush_metrics()

        assert SUBMIT_TO_DECIDE not in _points(reader)
        assert _transition_counts(reader) == {"closed": 1}

    async def test_submitted_at_reads_the_first_case_submitted_row(
        self, run_store: CaseRunStore, run_events_collection: FakeCollection
    ) -> None:
        first = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
        later = _submitted_row("run_1", first + timedelta(days=2))
        later["seq"] = 7
        run_events_collection.docs.extend(
            [
                later,
                _submitted_row("run_1", first),
                _submitted_row("run_2", later["at"]),
            ]
        )

        assert await run_store.submitted_at("run_1") == first
        assert await run_store.submitted_at("run_9") is None


class TestGateWait:
    @pytest.mark.parametrize(("waited_s", "recorded_s"), [(90, 90.0), (-30, 0.0)])
    async def test_a_decided_gate_records_its_wait(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
        gate_store: GateStore,
        waited_s: int,
        recorded_s: float,
    ) -> None:
        """The wait is ``decided_at - opened_at``, clamped at zero (clock skew)."""
        engine, reader = _metered(engine_factory)
        run_id = await _open_run_in(
            engine, session, case_runs_collection, CaseState.INTAKE
        )
        gate_type = GateType.PO_CONFIRM_CATEGORY
        gate = gate_document(
            gate_id=new_id("gate"),
            case_id=CASE_ID,
            run_id=run_id,
            gate_type=gate_type,
            required_role=REQUIRED_ROLE[gate_type],
        )
        opened_at = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
        gate["opened_at"] = opened_at
        await gate_store.open(gate, session=session)
        decision = GateDecision(
            gate_id=gate["_id"],
            gate_type=gate_type,
            case_id=CASE_ID,
            run_id=run_id,
            decision="confirm",
            decided_by="u-pnair",
            decided_at=opened_at + timedelta(seconds=waited_s),
        ).model_dump(mode="json")

        await engine.handle(_env("gate.decided", decision, run_id), session)
        assert GATE_WAIT not in _points(reader)
        engine.flush_metrics()

        (point,) = _points(reader)[GATE_WAIT]
        assert point.count == 1
        assert point.sum == recorded_s
        assert dict(point.attributes) == {"gate_type": gate_type.value}


class TestCompleteStep:
    async def _run_with_pending_step(
        self,
        engine: CaseflowEngine,
        session: FakeSession,
        case_runs: FakeCollection,
    ) -> str:
        """A run in TRIAGE waiting on one step; closing it lets the chain fire
        TRIAGE_DISPATCH (TRIAGE -> SCREENING).
        """
        run_id = await _open_run_in(engine, session, case_runs, CaseState.TRIAGE)
        case_runs.docs[0]["pending_steps"] = ["step_1"]
        return run_id

    async def test_the_chain_it_resumes_is_recorded_after_the_commit(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
    ) -> None:
        engine, reader = _metered(engine_factory)
        run_id = await self._run_with_pending_step(
            engine, session, case_runs_collection
        )

        await engine.complete_step(run_id, "step_1", StepResult())

        assert _transition_counts(reader) == {"screening": 1}

    async def test_a_transaction_that_fails_to_commit_records_nothing(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
    ) -> None:
        class CommitFails(FakeTransact):
            async def __call__(self, fn: Callable[[Any], Awaitable[Any]]) -> Any:
                await super().__call__(fn)
                raise ConnectionFailure("commit lost")

        engine, reader = _metered(engine_factory, transact=CommitFails())
        run_id = await self._run_with_pending_step(
            engine, session, case_runs_collection
        )

        with pytest.raises(ConnectionFailure):
            await engine.complete_step(run_id, "step_1", StepResult())

        assert _points(reader) == {}


class TestConsumerFlushesOnlyAfterACommit:
    """The consumer's transaction is faked at ``fcrm_common.events
    .with_transaction``; the fake collections cannot roll back, so a re-run
    restores them by hand, as an aborted transaction would.
    """

    @staticmethod
    def _consumer(
        engine: CaseflowEngine, consumed: Optional[FakeCollection] = None
    ) -> CaseEventsConsumer:
        return CaseEventsConsumer(
            consumed or FakeCollection("consumed_messages"),  # type: ignore[arg-type]
            FakePublisher(),
            engine=engine,
            client=object(),  # type: ignore[arg-type]
        )

    async def test_a_processed_delivery_records_its_move_once(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def commits(_client: object, fn: Callable[[Any], Awaitable[Any]]) -> Any:
            return await fn(FakeSession())

        monkeypatch.setattr("fcrm_common.events.with_transaction", commits)
        engine, reader = _metered(engine_factory)
        run_id = await _open_run_in(
            engine, session, case_runs_collection, CaseState.DECIDE
        )

        disposition = await self._consumer(engine)._apply(_vote_tallied(run_id))

        assert disposition is Disposition.PROCESSED
        assert _transition_counts(reader) == {"closed": 1}

    async def test_a_re_run_transaction_counts_the_move_once(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
        run_events_collection: FakeCollection,
        gates_collection: FakeCollection,
        outbox_collection: FakeCollection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        consumed = FakeCollection("consumed_messages")
        collections = [
            case_runs_collection,
            run_events_collection,
            gates_collection,
            outbox_collection,
            consumed,
        ]

        async def reruns(_client: object, fn: Callable[[Any], Awaitable[Any]]) -> Any:
            saved = [copy.deepcopy(c.docs) for c in collections]
            await fn(FakeSession())  # a TransientTransactionError: aborted
            for collection, docs in zip(collections, saved, strict=True):
                collection.docs[:] = docs
            return await fn(FakeSession())

        monkeypatch.setattr("fcrm_common.events.with_transaction", reruns)
        engine, reader = _metered(engine_factory)
        run_id = await _open_run_in(
            engine, session, case_runs_collection, CaseState.DECIDE
        )

        await self._consumer(engine, consumed)._apply(_vote_tallied(run_id))

        assert _transition_counts(reader) == {"closed": 1}

    async def test_a_failed_commit_records_nothing_then_or_later(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def commit_fails(
            _client: object, fn: Callable[[Any], Awaitable[Any]]
        ) -> Any:
            await fn(FakeSession())
            raise ConnectionFailure("commit lost")

        monkeypatch.setattr("fcrm_common.events.with_transaction", commit_fails)
        engine, reader = _metered(engine_factory)
        consumer = self._consumer(engine)
        run_id = await _open_run_in(
            engine, session, case_runs_collection, CaseState.DECIDE
        )

        with pytest.raises(ConnectionFailure):
            await consumer._apply(_vote_tallied(run_id))
        assert _points(reader) == {}

        async def commits(_client: object, fn: Callable[[Any], Awaitable[Any]]) -> Any:
            return await fn(FakeSession())

        monkeypatch.setattr("fcrm_common.events.with_transaction", commits)
        harness_step = _env("harness.step", {}, run_id)  # never reaches the engine
        assert await consumer._apply(harness_step) is Disposition.PROCESSED
        assert _points(reader) == {}

    async def test_a_duplicate_delivery_records_nothing(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        session: FakeSession,
        case_runs_collection: FakeCollection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def commits(_client: object, fn: Callable[[Any], Awaitable[Any]]) -> Any:
            return await fn(FakeSession())

        monkeypatch.setattr("fcrm_common.events.with_transaction", commits)
        engine, reader = _metered(engine_factory)
        consumer = self._consumer(
            engine,
            FakeCollection("consumed_messages", unique=[("message_id", "consumer")]),
        )
        run_id = await _open_run_in(
            engine, session, case_runs_collection, CaseState.DECIDE
        )
        envelope = _vote_tallied(run_id)
        assert await consumer._apply(envelope) is Disposition.PROCESSED
        assert _transition_counts(reader) == {"closed": 1}

        # A stale, never-committed observation must not ride out on a duplicate.
        case_runs_collection.docs[0]["state"] = CaseState.DECIDE.value
        case_runs_collection.docs[0]["closed_at"] = None
        await engine.handle(_vote_tallied(run_id), session)
        assert case_runs_collection.docs[0]["state"] == CaseState.CLOSED.value

        assert await consumer._apply(envelope) is Disposition.DUPLICATE
        assert _transition_counts(reader) == {"closed": 1}

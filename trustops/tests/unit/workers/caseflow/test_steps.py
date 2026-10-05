"""The step mechanism's runtime half (ADR-28): :class:`StepRegistry` (a kind
with no handler is an error, never a silent pass), :class:`StepRunner`
(idempotent by ``step_id``, serialised per case, only specific exceptions
caught, logged with context and re-raised) and :class:`StepDeps`'s idempotent
audit helper.

Handlers here are plain fakes; what a real handler does (HTTP calls, S4) is
each later lane's own test.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Optional

import httpx
import pytest
from conftest import FakeSession, RecordingAudit
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.steps import (
    STEP_ERRORS,
    StepDeps,
    StepRegistry,
    StepResult,
    StepRunner,
    UnknownStepKindError,
)
from fcrm_caseflow.store import DEFAULT_PINS, CaseRunStore, new_case_run_document
from fcrm_common.errors import FcrmError, NotFoundError, UpstreamUnavailableError
from fcrm_common.ids import deterministic_id
from fcrm_common.logging import get_context
from fcrm_contracts.enums import StepKind
from fcrm_contracts.events.payloads import HarnessStep
from pymongo.errors import ServerSelectionTimeoutError

CASE_ID = "FCRM-2026-0151"


def _step(
    step_id: str = "step_1",
    *,
    kind: StepKind = StepKind.INTAKE_PUBLISH,
    case_id: str = CASE_ID,
    run_id: str = "run_1",
) -> HarnessStep:
    return HarnessStep(step_id=step_id, step=kind, case_id=case_id, run_id=run_id)


async def _create_run(
    run_store: CaseRunStore, run_id: str = "run_1", case_id: str = CASE_ID
) -> None:
    await run_store.create(
        new_case_run_document(run_id=run_id, case_id=case_id, pins=DEFAULT_PINS)
    )


class StubCompleter:
    """Records every ``complete_step`` call; no engine, no Mongo."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, StepResult, Optional[StepKind]]] = []

    async def complete_step(
        self,
        run_id: str,
        step_id: str,
        result: StepResult,
        *,
        step: Optional[StepKind] = None,
    ) -> None:
        self.calls.append((run_id, step_id, result, step))


class CountingHandler:
    """A handler that records its calls and returns ``result``."""

    def __init__(self, result: Optional[StepResult] = None) -> None:
        self.calls: list[tuple[HarnessStep, StepDeps]] = []
        self.result = result if result is not None else StepResult()

    async def __call__(self, step: HarnessStep, deps: StepDeps) -> StepResult:
        self.calls.append((step, deps))
        return self.result


def _runner(
    run_store: CaseRunStore,
    audit: RecordingAudit,
    completer: Any,
    handlers: Optional[dict[StepKind, Any]] = None,
) -> StepRunner:
    return StepRunner(run_store, StepRegistry(handlers), completer, audit)


# --- StepRegistry -----------------------------------------------------------


class TestStepRegistry:
    def test_get_returns_the_registered_handler(self) -> None:
        handler = CountingHandler()
        registry = StepRegistry({StepKind.INTAKE_PUBLISH: handler})
        assert registry.get(StepKind.INTAKE_PUBLISH) is handler
        assert registry.kinds == {StepKind.INTAKE_PUBLISH}

    def test_an_unknown_kind_raises_a_specific_error_never_passes(self) -> None:
        registry = StepRegistry()
        with pytest.raises(UnknownStepKindError, match=r"intake\.publish"):
            registry.get(StepKind.INTAKE_PUBLISH)

    def test_the_error_is_an_fcrm_error_so_the_runner_logs_and_retries_it(
        self,
    ) -> None:
        assert issubclass(UnknownStepKindError, FcrmError)
        assert issubclass(UnknownStepKindError, STEP_ERRORS)

    def test_register_adds_a_handler_and_refuses_to_replace_one(self) -> None:
        registry = StepRegistry()
        registry.register(StepKind.STATE_PUSH, CountingHandler())
        assert StepKind.STATE_PUSH in registry.kinds
        with pytest.raises(ValueError, match="already registered"):
            registry.register(StepKind.STATE_PUSH, CountingHandler())

    def test_the_injected_table_is_copied_not_shared(self) -> None:
        table = {StepKind.STATE_PUSH: CountingHandler()}
        registry = StepRegistry(table)
        table.clear()
        assert StepKind.STATE_PUSH in registry.kinds

    def test_require_passes_when_every_kind_has_a_handler(self) -> None:
        registry = StepRegistry({StepKind.STATE_PUSH: CountingHandler()})
        registry.require([StepKind.STATE_PUSH])
        registry.require([])

    def test_require_names_every_kind_that_has_no_handler(self) -> None:
        registry = StepRegistry({StepKind.STATE_PUSH: CountingHandler()})
        with pytest.raises(UnknownStepKindError) as raised:
            registry.require(
                [StepKind.STATE_PUSH, StepKind.TRIAGE_ASSIGN, StepKind.SUBMIT_RECORD]
            )
        message = str(raised.value)
        assert "submit.record" in message
        assert "triage.assign" in message
        assert "state.push" not in message


# --- StepRunner -------------------------------------------------------------


class TestStepRunner:
    async def test_it_runs_the_handler_then_completes_the_step(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        await _create_run(run_store)
        result = StepResult(refs_patch={"k": "v"})
        handler = CountingHandler(result)
        completer = StubCompleter()
        runner = _runner(
            run_store, recording_audit, completer, {StepKind.INTAKE_PUBLISH: handler}
        )

        await runner.run(_step())

        [(step, deps)] = handler.calls
        assert step.step_id == "step_1"
        assert deps.run.snapshot.run_id == "run_1"
        assert deps.audit is recording_audit
        assert completer.calls == [("run_1", "step_1", result, StepKind.INTAKE_PUBLISH)]

    async def test_a_step_that_is_already_done_is_an_idempotent_no_op(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        await _create_run(run_store)
        run = await run_store.get("run_1")
        assert run is not None
        await run_store.record_step_done(
            run,
            "step_1",
            "intake.publish",
            session=FakeSession(),  # type: ignore[arg-type]
        )
        handler = CountingHandler()
        completer = StubCompleter()
        runner = _runner(
            run_store, recording_audit, completer, {StepKind.INTAKE_PUBLISH: handler}
        )

        await runner.run(_step())

        assert handler.calls == []
        assert completer.calls == []

    async def test_an_unregistered_kind_fails_loudly_and_completes_nothing(
        self,
        run_store: CaseRunStore,
        recording_audit: RecordingAudit,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _create_run(run_store)
        completer = StubCompleter()
        runner = _runner(run_store, recording_audit, completer)

        with (
            caplog.at_level(logging.WARNING, logger="fcrm_caseflow.steps"),
            pytest.raises(UnknownStepKindError),
        ):
            await runner.run(_step())

        assert completer.calls == []
        assert any("step failed" in r.getMessage() for r in caplog.records)

    async def test_a_missing_run_raises_not_found(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        runner = _runner(
            run_store,
            recording_audit,
            StubCompleter(),
            {StepKind.INTAKE_PUBLISH: CountingHandler()},
        )
        with pytest.raises(NotFoundError, match="run_1"):
            await runner.run(_step())

    @pytest.mark.parametrize(
        "error",
        [
            UpstreamUnavailableError("S1 is down"),
            httpx.ReadTimeout("slow"),
            ServerSelectionTimeoutError("mongo down"),
        ],
        ids=["fcrm-error", "httpx-timeout", "pymongo-error"],
    )
    async def test_expected_failures_are_logged_with_context_and_reraised(
        self,
        run_store: CaseRunStore,
        recording_audit: RecordingAudit,
        caplog: pytest.LogCaptureFixture,
        error: Exception,
    ) -> None:
        await _create_run(run_store)
        completer = StubCompleter()

        async def failing(step: HarnessStep, deps: StepDeps) -> StepResult:
            raise error

        runner = _runner(
            run_store, recording_audit, completer, {StepKind.INTAKE_PUBLISH: failing}
        )

        with (
            caplog.at_level(logging.WARNING, logger="fcrm_caseflow.steps"),
            pytest.raises(type(error)) as raised,
        ):
            await runner.run(_step())

        assert raised.value is error, "re-raised unchanged for the broker retry path"
        assert completer.calls == [], "a failed step is never completed"
        [record] = [r for r in caplog.records if "step failed" in r.getMessage()]
        assert record.exc_info is not None

    async def test_the_handler_runs_inside_a_bound_logging_context(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        await _create_run(run_store)
        seen: dict[str, Any] = {}

        async def handler(step: HarnessStep, deps: StepDeps) -> StepResult:
            seen.update(get_context())
            return StepResult()

        runner = _runner(
            run_store,
            recording_audit,
            StubCompleter(),
            {StepKind.INTAKE_PUBLISH: handler},
        )
        await runner.run(_step())

        assert seen == {
            "case_id": CASE_ID,
            "run_id": "run_1",
            "step_id": "step_1",
            "step": "intake.publish",
        }
        assert get_context() == {}, "the binding does not leak out of run()"

    async def test_an_unexpected_exception_is_not_caught_here(
        self,
        run_store: CaseRunStore,
        recording_audit: RecordingAudit,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Only upstream and database failures are the runner's to log; a
        ``KeyError`` is a bug and propagates untouched to the consumer.
        """
        await _create_run(run_store)

        async def buggy(step: HarnessStep, deps: StepDeps) -> StepResult:
            raise KeyError("missing")

        runner = _runner(
            run_store,
            recording_audit,
            StubCompleter(),
            {StepKind.INTAKE_PUBLISH: buggy},
        )
        with (
            caplog.at_level(logging.WARNING, logger="fcrm_caseflow.steps"),
            pytest.raises(KeyError),
        ):
            await runner.run(_step())
        assert not [r for r in caplog.records if "step failed" in r.getMessage()]


class TestPerCaseSerialisation:
    @staticmethod
    def _tracking_handler() -> tuple[Callable[..., Any], dict[str, int]]:
        state = {"active": 0, "peak": 0}

        async def handler(step: HarnessStep, deps: StepDeps) -> StepResult:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            await asyncio.sleep(0.01)
            state["active"] -= 1
            return StepResult()

        return handler, state

    async def test_two_steps_of_one_case_never_run_at_the_same_time(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        await _create_run(run_store)
        handler, state = self._tracking_handler()
        runner = _runner(
            run_store,
            recording_audit,
            StubCompleter(),
            {StepKind.INTAKE_PUBLISH: handler},
        )
        await asyncio.gather(runner.run(_step("step_a")), runner.run(_step("step_b")))
        assert state["peak"] == 1

    async def test_steps_of_different_cases_run_concurrently(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        await _create_run(run_store, "run_1", "FCRM-2026-0151")
        await _create_run(run_store, "run_2", "FCRM-2026-0152")
        handler, state = self._tracking_handler()
        runner = _runner(
            run_store,
            recording_audit,
            StubCompleter(),
            {StepKind.INTAKE_PUBLISH: handler},
        )
        await asyncio.gather(
            runner.run(_step("step_a", run_id="run_1", case_id="FCRM-2026-0151")),
            runner.run(_step("step_b", run_id="run_2", case_id="FCRM-2026-0152")),
        )
        assert state["peak"] == 2

    async def test_a_failed_step_releases_the_case_lock(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        await _create_run(run_store)
        calls: list[str] = []

        async def handler(step: HarnessStep, deps: StepDeps) -> StepResult:
            calls.append(step.step_id)
            if step.step_id == "step_a":
                raise UpstreamUnavailableError("S1 is down")
            return StepResult()

        runner = _runner(
            run_store,
            recording_audit,
            StubCompleter(),
            {StepKind.INTAKE_PUBLISH: handler},
        )
        with pytest.raises(UpstreamUnavailableError):
            await runner.run(_step("step_a"))
        await asyncio.wait_for(runner.run(_step("step_b")), timeout=1)
        assert calls == ["step_a", "step_b"]

    async def test_no_lock_is_kept_for_a_case_that_is_idle(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        await _create_run(run_store)
        handler, _ = self._tracking_handler()
        runner = _runner(
            run_store,
            recording_audit,
            StubCompleter(),
            {StepKind.INTAKE_PUBLISH: handler},
        )
        await asyncio.gather(runner.run(_step("step_a")), runner.run(_step("step_b")))
        assert len(runner._locks) == 0

    async def test_a_duplicate_waiting_on_the_lock_skips_after_the_first_finishes(
        self,
        run_store: CaseRunStore,
        recording_audit: RecordingAudit,
        engine: CaseflowEngine,
    ) -> None:
        """The same ``step_id`` delivered twice at once (a redelivery while the
        first run is still going): the second takes the lock only after the
        first completed, finds the ``step_done`` row and does nothing.
        """
        await _create_run(run_store)
        handler, state = self._tracking_handler()
        calls: list[str] = []

        async def counting(step: HarnessStep, deps: StepDeps) -> StepResult:
            calls.append(step.step_id)
            return await handler(step, deps)

        runner = _runner(
            run_store, recording_audit, engine, {StepKind.INTAKE_PUBLISH: counting}
        )
        await asyncio.gather(runner.run(_step()), runner.run(_step()))

        assert calls == ["step_1"]
        assert state["peak"] == 1
        assert await run_store.has_step_done("run_1", "step_1")


# --- StepDeps.append_record --------------------------------------------------


class TestAppendRecord:
    async def test_a_record_is_written_for_the_case_with_the_step_in_its_data(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        await _create_run(run_store)
        run = await run_store.get("run_1")
        assert run is not None
        deps = StepDeps(run=run, audit=recording_audit)

        await deps.append_record(
            _step(),
            "proposal.extraction",
            "Intake agent · 15 candidate values",
            {"values": 15},
            actor="ai",
        )

        [record] = recording_audit.records
        assert record.case_id == CASE_ID
        assert record.type == "proposal.extraction"
        assert record.actor == "ai"
        assert record.data == {"run_id": "run_1", "step_id": "step_1", "values": 15}

    async def test_a_retried_step_sends_the_same_idempotency_keys(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        await _create_run(run_store)
        run = await run_store.get("run_1")
        assert run is not None
        deps = StepDeps(run=run, audit=recording_audit)

        for _ in range(2):  # the step ran twice
            await deps.append_record(_step(), "guard.input", "file 1", ordinal=0)
            await deps.append_record(_step(), "guard.input", "file 2", ordinal=1)

        first, second, again_first, again_second = recording_audit.keys
        assert first == again_first
        assert second == again_second
        assert first != second, "records of one type in one step differ by ordinal"
        assert first == deterministic_id("idem", "step_1", "guard.input", "0")

    async def test_keys_differ_between_steps_and_between_record_types(
        self, run_store: CaseRunStore, recording_audit: RecordingAudit
    ) -> None:
        await _create_run(run_store)
        run = await run_store.get("run_1")
        assert run is not None
        deps = StepDeps(run=run, audit=recording_audit)

        await deps.append_record(_step("step_1"), "guard.input", "a")
        await deps.append_record(_step("step_2"), "guard.input", "a")
        await deps.append_record(_step("step_1"), "guard.output", "a")

        assert len(set(recording_audit.keys)) == 3

    async def test_an_audit_failure_fails_the_step_instead_of_being_swallowed(
        self, run_store: CaseRunStore
    ) -> None:
        class DownAudit(RecordingAudit):
            async def append(self, record: Any, *, idempotency_key: Any = None) -> Any:
                raise UpstreamUnavailableError("S4 is down")

        await _create_run(run_store)
        run = await run_store.get("run_1")
        assert run is not None
        deps = StepDeps(run=run, audit=DownAudit())
        with pytest.raises(UpstreamUnavailableError):
            await deps.append_record(_step(), "guard.input", "a")

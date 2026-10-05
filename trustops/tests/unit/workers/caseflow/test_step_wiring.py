"""``caseflow.steps`` on the bus (ADR-28): the third subscriber
:func:`fcrm_caseflow.app.wire_subscribers` registers, and what
:class:`~fcrm_caseflow.consumers.HarnessStepConsumer` does with a delivery,
against ``faststream.rabbit.TestRabbitBroker`` (no real broker) and the
consumer's own ``process`` path (dedupe, retry, dead letter).

``harness.step`` is bound to ``caseflow.steps`` with its own routing key and
also matches ``caseflow.events``'s ``#``, so a copy reaches both queues; the
events consumer must drop it before the engine sees it.
"""

from __future__ import annotations

from typing import Any, Optional

from conftest import FakeCollection, FakePublisher
from faststream.rabbit import RabbitBroker, TestRabbitBroker
from fcrm_caseflow.app import wire_subscribers
from fcrm_caseflow.consumers import (
    AgentResultsConsumer,
    CaseEventsConsumer,
    HarnessStepConsumer,
)
from fcrm_caseflow.publisher import FastStreamPublisher
from fcrm_caseflow.topology import (
    CASE_EVENTS_EXCHANGE,
    CASEFLOW_STEPS_QUEUE,
    passive_exchange,
)
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.events import Disposition
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep
from pymongo.asynchronous.client_session import AsyncClientSession

CASE = "FCRM-2026-0151"


class RecordingEngine:
    """Records the envelopes the events consumer hands to the engine."""

    def __init__(self) -> None:
        self.calls: list[Envelope] = []

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        self.calls.append(envelope)


class StubRunner:
    """Stands in for :class:`~fcrm_caseflow.steps.StepRunner`: records each
    step, and raises ``error`` instead when one is set.
    """

    def __init__(self, error: Optional[Exception] = None) -> None:
        self.steps: list[HarnessStep] = []
        self._error = error

    async def run(self, step: HarnessStep) -> None:
        if self._error is not None:
            raise self._error
        self.steps.append(step)


class Acks:
    """Counts the ack/nack calls of one delivery."""

    def __init__(self) -> None:
        self.acked = 0
        self.nacked: list[bool] = []

    async def ack(self) -> None:
        self.acked += 1

    async def nack(self, requeue: bool) -> None:
        self.nacked.append(requeue)


def _step_envelope(step_id: str = "step_1") -> Envelope:
    step = HarnessStep(
        step_id=step_id,
        step=StepKind.INTAKE_PUBLISH,
        case_id=CASE,
        run_id="run_1",
        args={"trigger": "agent.done.intake"},
    )
    return Envelope(
        event_id=new_id("evt"),
        type="harness.step",
        case_id=CASE,
        run_id="run_1",
        occurred_at=utcnow(),
        payload=step.model_dump(mode="json"),
    )


def _consumers(
    publisher: Any, engine: RecordingEngine, runner: StubRunner
) -> tuple[CaseEventsConsumer, AgentResultsConsumer, HarnessStepConsumer]:
    return (
        CaseEventsConsumer(
            FakeCollection("consumed"),
            publisher,
            engine=engine,  # type: ignore[arg-type]
        ),
        AgentResultsConsumer(
            FakeCollection("consumed"),
            publisher,
            engine=engine,  # type: ignore[arg-type]
        ),
        HarnessStepConsumer(
            FakeCollection("consumed"),
            publisher,
            runner=runner,  # type: ignore[arg-type]
        ),
    )


async def test_a_harness_step_reaches_the_step_consumer_not_the_engine() -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    engine, runner = RecordingEngine(), StubRunner()
    wire_subscribers(broker, *_consumers(FastStreamPublisher(broker), engine, runner))

    env = _step_envelope()
    async with TestRabbitBroker(broker) as br:
        await br.publish(
            env.model_dump_json().encode(),
            exchange=passive_exchange(CASE_EVENTS_EXCHANGE),
            routing_key="harness.step",
        )

    assert [s.step_id for s in runner.steps] == ["step_1"]
    assert engine.calls == [], "the events consumer must ignore harness.step"


async def test_the_step_consumer_does_not_see_other_case_events() -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    engine, runner = RecordingEngine(), StubRunner()
    wire_subscribers(broker, *_consumers(FastStreamPublisher(broker), engine, runner))

    other = Envelope(
        event_id=new_id("evt"),
        type="case.submitted",
        case_id=CASE,
        run_id="run_1",
        occurred_at=utcnow(),
        payload={"four_submit_checks_passed": True},
    )
    async with TestRabbitBroker(broker) as br:
        await br.publish(
            other.model_dump_json().encode(),
            exchange=passive_exchange(CASE_EVENTS_EXCHANGE),
            routing_key="case.submitted",
        )

    assert runner.steps == []
    assert [c.type for c in engine.calls] == ["case.submitted"]


def test_three_queues_are_wired_each_with_its_own_prefetch() -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    engine, runner = RecordingEngine(), StubRunner()
    wire_subscribers(
        broker,
        *_consumers(FastStreamPublisher(broker), engine, runner),
        prefetch=16,
        step_prefetch=4,
    )
    by_queue = {sub.queue.name: sub for sub in broker.subscribers}
    assert set(by_queue) == {
        "caseflow.events",
        "caseflow.agent-results",
        "caseflow.steps",
    }
    assert by_queue["caseflow.events"].channel.prefetch_count == 16
    assert by_queue["caseflow.agent-results"].channel.prefetch_count == 16
    assert by_queue["caseflow.steps"].channel.prefetch_count == 4
    assert by_queue["caseflow.steps"].queue.routing_key == "harness.step"


class TestStepConsumerProcess:
    @staticmethod
    def _consumer(runner: StubRunner) -> tuple[HarnessStepConsumer, FakePublisher]:
        publisher = FakePublisher()
        consumer = HarnessStepConsumer(
            FakeCollection("consumed"),
            publisher,
            runner=runner,  # type: ignore[arg-type]
        )
        return consumer, publisher

    async def test_a_valid_step_runs_and_is_acked(self) -> None:
        runner = StubRunner()
        consumer, publisher = self._consumer(runner)
        acks = Acks()
        body = _step_envelope().model_dump_json().encode()

        outcome = await consumer.process(body, {}, acks.ack, acks.nack)

        assert outcome is Disposition.PROCESSED
        assert [s.step_id for s in runner.steps] == ["step_1"]
        assert runner.steps[0].step is StepKind.INTAKE_PUBLISH
        assert acks.acked == 1
        assert publisher.published == []

    async def test_a_failing_step_is_retried_not_acknowledged_as_done(self) -> None:
        runner = StubRunner(error=UpstreamUnavailableError("S1 is down"))
        consumer, publisher = self._consumer(runner)
        acks = Acks()
        body = _step_envelope().model_dump_json().encode()

        outcome = await consumer.process(body, {}, acks.ack, acks.nack)

        assert outcome is Disposition.RETRIED
        assert [(ex, key) for ex, key, _ in publisher.published] == [
            ("retry", CASEFLOW_STEPS_QUEUE)
        ]
        # The original delivery is acked only because the retry was confirmed.
        assert acks.acked == 1

    async def test_a_step_that_keeps_failing_is_dead_lettered(self) -> None:
        runner = StubRunner(error=UpstreamUnavailableError("S1 is down"))
        consumer, publisher = self._consumer(runner)
        acks = Acks()
        body = _step_envelope().model_dump_json().encode()

        outcome = await consumer.process(body, {"x-attempt": 3}, acks.ack, acks.nack)

        assert outcome is Disposition.DEAD_LETTERED
        assert [(ex, key) for ex, key, _ in publisher.published] == [
            ("dlx", CASEFLOW_STEPS_QUEUE)
        ]

    async def test_a_failed_step_leaves_no_consumed_marker_so_the_retry_reruns_it(
        self,
    ) -> None:
        consumed = FakeCollection("consumed")
        runner = StubRunner(error=UpstreamUnavailableError("S1 is down"))
        consumer = HarnessStepConsumer(
            consumed,
            FakePublisher(),
            runner=runner,  # type: ignore[arg-type]
        )
        acks = Acks()
        await consumer.process(
            _step_envelope().model_dump_json().encode(), {}, acks.ack, acks.nack
        )
        assert consumed.docs == []

    async def test_a_redelivered_step_is_deduplicated_by_message_id(self) -> None:
        runner = StubRunner()
        consumer, _ = self._consumer(runner)
        body = _step_envelope().model_dump_json().encode()
        first, second = Acks(), Acks()

        await consumer.process(body, {}, first.ack, first.nack)
        outcome = await consumer.process(body, {}, second.ack, second.nack)

        assert outcome is Disposition.DUPLICATE
        assert len(runner.steps) == 1
        assert second.acked == 1

    async def test_a_payload_that_is_not_a_step_is_retried_then_parked(self) -> None:
        runner = StubRunner()
        consumer, publisher = self._consumer(runner)
        bad = Envelope(
            event_id=new_id("evt"),
            type="harness.step",
            case_id=CASE,
            run_id="run_1",
            occurred_at=utcnow(),
            payload={"step_id": "step_1", "step": "intake.invented"},
        )
        acks = Acks()

        outcome = await consumer.process(
            bad.model_dump_json().encode(), {}, acks.ack, acks.nack
        )

        assert outcome is Disposition.RETRIED
        assert runner.steps == []
        assert publisher.published[0][0] == "retry"

    def test_the_consumer_uses_the_non_transactional_dedupe_path(self) -> None:
        consumer, _ = self._consumer(StubRunner())
        assert consumer.queue == CASEFLOW_STEPS_QUEUE
        assert consumer._client is None

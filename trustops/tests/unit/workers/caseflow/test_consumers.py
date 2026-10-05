"""End-to-end FastStream/RabbitMQ plumbing for H1's two consumers, against
``faststream.rabbit.TestRabbitBroker`` (no real broker) — mirrors
``tests/unit/workers/agent-worker/test_app_wiring.py``'s own pattern.

Proves the topology this package's :mod:`fcrm_caseflow.topology`/``.app``
claim (``ops/rabbitmq/definitions.json``, read verbatim): a ``case.events``
message (any routing key — the real binding is ``#``) reaches
:class:`~fcrm_caseflow.consumers.CaseEventsConsumer`; ``agent.jobs``
messages routed ``agent.done``/``agent.failed`` reach
:class:`~fcrm_caseflow.consumers.AgentResultsConsumer`. Uses a recording
stub in place of :class:`~fcrm_caseflow.engine.CaseflowEngine` —
``engine.handle``'s own behaviour is ``test_engine.py``'s job; this file's
job is only "does the right wire message reach the right consumer".
"""

from __future__ import annotations

from typing import Any, Optional

from conftest import FakeCollection
from faststream.rabbit import RabbitBroker, TestRabbitBroker
from fcrm_caseflow.app import wire_subscribers
from fcrm_caseflow.consumers import (
    AgentResultsConsumer,
    CaseEventsConsumer,
    HarnessStepConsumer,
)
from fcrm_caseflow.publisher import FastStreamPublisher
from fcrm_caseflow.topology import (
    AGENT_JOBS_EXCHANGE,
    CASE_EVENTS_EXCHANGE,
    passive_exchange,
    passive_queue,
)
from fcrm_common.events import Publisher
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep
from pymongo.asynchronous.client_session import AsyncClientSession


class RecordingRunner:
    """Stands in for :class:`~fcrm_caseflow.steps.StepRunner`: records every
    step it is asked to run (its own behaviour is ``test_steps.py``'s job).
    """

    def __init__(self) -> None:
        self.steps: list[HarnessStep] = []

    async def run(self, step: HarnessStep) -> None:
        self.steps.append(step)


def _steps_consumer(
    publisher: Publisher, runner: Optional[RecordingRunner] = None
) -> HarnessStepConsumer:
    return HarnessStepConsumer(
        FakeCollection("consumed_messages"),
        publisher,
        runner=runner or RecordingRunner(),  # type: ignore[arg-type]
    )


class RecordingEngine:
    """Stands in for :class:`~fcrm_caseflow.engine.CaseflowEngine`: records
    every envelope it is asked to handle instead of applying the state
    machine. ``Consumer`` (``client=None``) calls ``handle`` with
    ``session=None`` — correct for this file, since the real transactional
    path (a genuine ``AsyncMongoClient``) is what ``test_engine.py`` and
    ``test_engine_live.py`` already exercise.
    """

    def __init__(self) -> None:
        self.calls: list[Envelope] = []

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        self.calls.append(envelope)


def _env(event_type: str, payload: dict[str, Any]) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id="FCRM-2026-0151",
        run_id="run_1",
        occurred_at=utcnow(),
        payload=payload,
    )


async def test_a_case_events_message_reaches_the_case_events_consumer() -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    publisher = FastStreamPublisher(broker)
    engine = RecordingEngine()
    case_events = CaseEventsConsumer(
        FakeCollection("consumed_messages"),
        publisher,
        engine=engine,  # type: ignore[arg-type]
    )
    agent_results = AgentResultsConsumer(
        FakeCollection("consumed_messages"),
        publisher,
        engine=engine,  # type: ignore[arg-type]
    )
    wire_subscribers(broker, case_events, agent_results, _steps_consumer(publisher))

    case_events_exchange = passive_exchange(CASE_EVENTS_EXCHANGE)
    env = _env("case.created", {"actor": "u-pnair", "data": {}})
    async with TestRabbitBroker(broker) as br:
        await br.publish(
            env.model_dump_json().encode(),
            exchange=case_events_exchange,
            routing_key="case.created",
        )

    assert len(engine.calls) == 1
    assert engine.calls[0].event_id == env.event_id


async def test_a_gate_decided_message_also_reaches_it_via_the_wildcard_binding() -> (
    None
):
    """``caseflow.events`` is bound with routing key ``#`` — every
    case.events routing key reaches it, not just ``case.*``.
    """
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    publisher = FastStreamPublisher(broker)
    engine = RecordingEngine()
    case_events = CaseEventsConsumer(
        FakeCollection("consumed_messages"),
        publisher,
        engine=engine,  # type: ignore[arg-type]
    )
    agent_results = AgentResultsConsumer(
        FakeCollection("consumed_messages"),
        publisher,
        engine=engine,  # type: ignore[arg-type]
    )
    wire_subscribers(broker, case_events, agent_results, _steps_consumer(publisher))

    case_events_exchange = passive_exchange(CASE_EVENTS_EXCHANGE)
    env = _env("gate.decided", {"gate_id": "gate_1"})
    async with TestRabbitBroker(broker) as br:
        await br.publish(
            env.model_dump_json().encode(),
            exchange=case_events_exchange,
            routing_key="gate.decided",
        )

    assert len(engine.calls) == 1
    assert engine.calls[0].type == "gate.decided"


async def test_agent_done_and_agent_failed_both_reach_the_agent_results_consumer() -> (
    None
):
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    publisher = FastStreamPublisher(broker)
    engine = RecordingEngine()
    case_events = CaseEventsConsumer(
        FakeCollection("consumed_messages"),
        publisher,
        engine=engine,  # type: ignore[arg-type]
    )
    agent_results = AgentResultsConsumer(
        FakeCollection("consumed_messages"),
        publisher,
        engine=engine,  # type: ignore[arg-type]
    )
    wire_subscribers(broker, case_events, agent_results, _steps_consumer(publisher))

    agent_jobs_exchange = passive_exchange(AGENT_JOBS_EXCHANGE)
    done = _env("agent.done", {"agent": "research"})
    failed = _env("agent.failed", {"agent": "research", "attempt": 2})
    async with TestRabbitBroker(broker) as br:
        await br.publish(
            done.model_dump_json().encode(),
            exchange=agent_jobs_exchange,
            routing_key="agent.done",
        )
        await br.publish(
            failed.model_dump_json().encode(),
            exchange=agent_jobs_exchange,
            routing_key="agent.failed",
        )

    assert len(engine.calls) == 2
    assert {c.type for c in engine.calls} == {"agent.done", "agent.failed"}


async def test_an_agent_run_message_does_not_reach_either_caseflow_consumer() -> None:
    """``agent.run`` is bound only to ``agent-worker.run`` (H2's queue) —
    Caseflow must never see its own dispatched jobs echoed back.
    """
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    publisher = FastStreamPublisher(broker)
    engine = RecordingEngine()
    case_events = CaseEventsConsumer(
        FakeCollection("consumed_messages"),
        publisher,
        engine=engine,  # type: ignore[arg-type]
    )
    agent_results = AgentResultsConsumer(
        FakeCollection("consumed_messages"),
        publisher,
        engine=engine,  # type: ignore[arg-type]
    )
    wire_subscribers(broker, case_events, agent_results, _steps_consumer(publisher))

    agent_jobs_exchange = passive_exchange(AGENT_JOBS_EXCHANGE)

    @broker.subscriber(
        passive_queue("agent-worker.run", routing_key="agent.run"),
        agent_jobs_exchange,
    )
    async def _h2_stub(msg: object) -> None:
        return None

    run = _env("agent.run", {"agent": "research"})
    async with TestRabbitBroker(broker) as br:
        await br.publish(
            run.model_dump_json().encode(),
            exchange=agent_jobs_exchange,
            routing_key="agent.run",
        )

    assert engine.calls == []

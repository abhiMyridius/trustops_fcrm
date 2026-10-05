"""The H1 process: one ``RabbitBroker`` consuming both
:data:`~fcrm_caseflow.topology.CASEFLOW_EVENTS_QUEUE` and
:data:`~fcrm_caseflow.topology.CASEFLOW_AGENT_RESULTS_QUEUE` through
:class:`~fcrm_caseflow.consumers.CaseEventsConsumer` /
:class:`~fcrm_caseflow.consumers.AgentResultsConsumer`, an
:class:`~fcrm_common.outbox.OutboxRelay` run as a background task for the
run of the process (the same transactional-outbox pattern every other
state-changing unit in this repo uses — see ``fcrm_common.outbox``'s
module docstring), and ``/healthz``/``/readyz`` on the same ASGI app
(mirrors :mod:`fcrm_agent_worker.app`'s documented
``AsgiFastStream``/health wiring exactly).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from faststream import AckPolicy
from faststream.asgi import AsgiFastStream
from faststream.rabbit import RabbitBroker, RabbitMessage
from fcrm_common.events import Consumer as EventConsumer
from fcrm_common.health import (
    ReadinessCheck,
    ReadinessProbe,
    amqp_check,
    build_health_asgi_app,
    mongo_check,
)
from fcrm_common.http import ServiceClient
from fcrm_common.ids import new_id
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import MongoLease, get_client
from fcrm_common.outbox import Outbox, OutboxRelay

from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.consumers import AgentResultsConsumer, CaseEventsConsumer
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.publisher import FastStreamPublisher
from fcrm_caseflow.settings import CaseflowSettings
from fcrm_caseflow.store import CaseRunStore
from fcrm_caseflow.topology import (
    AGENT_JOBS_EXCHANGE,
    CASE_EVENTS_EXCHANGE,
    ROUTING_KEY_AGENT_DONE,
    ROUTING_KEY_AGENT_FAILED,
    ROUTING_KEY_CASE_EVENTS,
    passive_exchange,
    passive_queue,
)

logger = logging.getLogger(__name__)

CONSUMED_MESSAGES_COLLECTION = "consumed_messages"
CASE_RUNS_COLLECTION = "case_runs"
RUN_EVENTS_COLLECTION = "run_events"
GATES_COLLECTION = "gates"
OUTBOX_COLLECTION = "outbox"
LEASES_COLLECTION = "leases"
OUTBOX_RELAY_LEASE_NAME = "outbox-relay:caseflow"


def build_broker(rabbitmq_url: str) -> RabbitBroker:
    """An unwired ``RabbitBroker``; call :func:`wire_subscribers` next."""
    return RabbitBroker(rabbitmq_url)


def wire_subscribers(
    broker: RabbitBroker,
    case_events: EventConsumer,
    agent_results: EventConsumer,
) -> None:
    """Register both manually-acked subscribers, delegating every delivery
    to the matching consumer's ``process`` (dedupe, retry/DLQ — see
    ``fcrm_common.events``). ``ack_policy=AckPolicy.MANUAL`` disables
    FastStream's own ack/nack so ``consumer.process`` is the only thing
    that ever acks or nacks a delivery (mirrors
    :func:`fcrm_agent_worker.app.wire_subscriber`).
    """
    case_events_exchange = passive_exchange(CASE_EVENTS_EXCHANGE)
    agent_jobs_exchange = passive_exchange(AGENT_JOBS_EXCHANGE)

    @broker.subscriber(
        passive_queue(case_events.queue, routing_key=ROUTING_KEY_CASE_EVENTS),
        case_events_exchange,
        ack_policy=AckPolicy.MANUAL,
    )
    async def _consume_case_events(msg: RabbitMessage) -> None:
        await case_events.process(
            msg.body,
            msg.headers,
            ack=msg.ack,
            nack=lambda requeue: msg.nack(requeue=requeue),
        )

    @broker.subscriber(
        passive_queue(agent_results.queue, routing_key=ROUTING_KEY_AGENT_DONE),
        agent_jobs_exchange,
        ack_policy=AckPolicy.MANUAL,
    )
    async def _consume_agent_done(msg: RabbitMessage) -> None:
        await agent_results.process(
            msg.body,
            msg.headers,
            ack=msg.ack,
            nack=lambda requeue: msg.nack(requeue=requeue),
        )

    @broker.subscriber(
        passive_queue(agent_results.queue, routing_key=ROUTING_KEY_AGENT_FAILED),
        agent_jobs_exchange,
        ack_policy=AckPolicy.MANUAL,
    )
    async def _consume_agent_failed(msg: RabbitMessage) -> None:
        await agent_results.process(
            msg.body,
            msg.headers,
            ack=msg.ack,
            nack=lambda requeue: msg.nack(requeue=requeue),
        )


def build_app(settings: Optional[CaseflowSettings] = None) -> AsgiFastStream:
    """The full H1 process: broker + outbox relay + health ASGI app, real
    dependencies.
    """
    configured = settings or CaseflowSettings.load()
    configure_logging(configured.app_name, configured.log_level, env=configured.env)

    assert configured.mongodb_uri is not None
    assert configured.mongodb_db is not None
    assert configured.audit_service_url is not None
    assert configured.rabbitmq_url is not None

    mongo_client = get_client(configured.mongodb_uri)
    db = mongo_client[configured.mongodb_db]
    consumed = db[CONSUMED_MESSAGES_COLLECTION]
    case_runs = db[CASE_RUNS_COLLECTION]
    run_events = db[RUN_EVENTS_COLLECTION]
    gates_collection = db[GATES_COLLECTION]
    outbox_collection = db[OUTBOX_COLLECTION]
    leases = db[LEASES_COLLECTION]

    runs_store = CaseRunStore(case_runs, run_events)
    gate_store = GateStore(gates_collection)
    outbox = Outbox(outbox_collection)

    audit_http = ServiceClient(
        configured.audit_service_url,
        dependency="audit",
        caller=configured.app_name,
        token_secret=(
            configured.service_token_secret.get_secret_value()
            if configured.service_token_secret
            else None
        ),
    )
    audit = AuditClient(audit_http)
    engine = CaseflowEngine(runs_store, gate_store, outbox, audit)

    broker = build_broker(configured.rabbitmq_url)
    publisher = FastStreamPublisher(broker)
    case_events_consumer = CaseEventsConsumer(
        consumed, publisher, engine=engine, client=mongo_client
    )
    agent_results_consumer = AgentResultsConsumer(
        consumed, publisher, engine=engine, client=mongo_client
    )
    wire_subscribers(broker, case_events_consumer, agent_results_consumer)

    lease = MongoLease(leases, OUTBOX_RELAY_LEASE_NAME, new_id("relay"))
    relay = OutboxRelay(outbox_collection, lease, publisher)
    stop_event = asyncio.Event()
    relay_task: list[asyncio.Task[None]] = []

    indexes_ready: list[bool] = []

    async def _ensure_indexes() -> None:
        # FastStream logs an after_startup exception and keeps running, so a
        # failure here must flip readiness itself (D-24: on the first
        # composed boot an IndexOptionsConflict was swallowed while /readyz
        # kept answering 200).
        try:
            await runs_store.ensure_indexes()
            await gate_store.ensure_indexes()
            await outbox.ensure_indexes()
            await case_events_consumer.ensure_indexes()
        except Exception:
            logger.exception("index creation failed; readiness stays false")
            raise
        indexes_ready.append(True)

    async def _indexes_check() -> None:
        if not indexes_ready:
            msg = "indexes not created yet or index creation failed"
            raise RuntimeError(msg)

    async def _start_relay() -> None:
        relay_task.append(asyncio.create_task(relay.run(stop_event)))

    async def _stop_relay() -> None:
        stop_event.set()
        if relay_task:
            await relay_task[0]

    probe = ReadinessProbe(
        [
            mongo_check(mongo_client),
            amqp_check(configured.rabbitmq_url),
            ReadinessCheck("indexes", _indexes_check, critical=True),
        ]
    )
    health_app = build_health_asgi_app(probe)
    return AsgiFastStream(
        broker,
        asgi_routes=[("/healthz", health_app), ("/readyz", health_app)],
        after_startup=[_ensure_indexes, _start_relay],
        on_shutdown=[_stop_relay],
    )

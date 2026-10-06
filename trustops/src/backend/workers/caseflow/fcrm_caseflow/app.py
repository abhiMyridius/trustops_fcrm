"""The H1 consumer: one ``RabbitBroker`` consuming
:data:`~fcrm_caseflow.topology.CASEFLOW_EVENTS_QUEUE`,
:data:`~fcrm_caseflow.topology.CASEFLOW_AGENT_RESULTS_QUEUE` and
:data:`~fcrm_caseflow.topology.CASEFLOW_STEPS_QUEUE` through
:class:`~fcrm_caseflow.consumers.CaseEventsConsumer` /
:class:`~fcrm_caseflow.consumers.AgentResultsConsumer` /
:class:`~fcrm_caseflow.consumers.HarnessStepConsumer`, an
:class:`~fcrm_common.outbox.OutboxRelay` run as a background task for the
run of the process (the same transactional-outbox pattern every other
state-changing unit in this repo uses — see ``fcrm_common.outbox``'s
module docstring).

:func:`build_unit` returns H1 as a :class:`~fcrm_common.worker.WorkerUnit`
for the composite ``fcrm_harness`` process, which owns the broker, the
Mongo client and the health app; :func:`build_app` wraps the same unit in
its own broker and ``/healthz``/``/readyz`` for ``python -m fcrm_caseflow``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Optional

from faststream.asgi import AsgiFastStream
from faststream.rabbit import RabbitBroker
from fcrm_common.broker import FastStreamPublisher, wire_consumer
from fcrm_common.cache import InMemoryTTLCache
from fcrm_common.events import Consumer as EventConsumer
from fcrm_common.events import Publisher
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
from fcrm_common.mongo import Document, MongoLease, close_clients, get_client
from fcrm_common.outbox import Outbox, OutboxRelay, RelayRunner
from fcrm_common.settings import required_value
from fcrm_common.worker import WorkerUnit
from fcrm_contracts.enums import StepKind
from fcrm_contracts.events.raw_inputs import RAW_INPUTS_COLLECTION
from pymongo import AsyncMongoClient

from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.budget import CASE_BUDGETS_COLLECTION, CaseBudgetStore
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
from fcrm_caseflow.consumers import (
    AgentResultsConsumer,
    CaseEventsConsumer,
    HarnessStepConsumer,
)
from fcrm_caseflow.cores import default_handlers
from fcrm_caseflow.engine import CaseflowEngine, mongo_transact
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.harness_view import HarnessReporter
from fcrm_caseflow.oversight_runs import OversightRuns
from fcrm_caseflow.raw_inputs import MongoRawInputsReader
from fcrm_caseflow.settings import CaseflowSettings
from fcrm_caseflow.steps import StepHandler, StepRegistry, StepRunner
from fcrm_caseflow.store import AGENT_RESULTS_COLLECTION, AgentResultStore, CaseRunStore
from fcrm_caseflow.topology import (
    AGENT_JOBS_EXCHANGE,
    CASE_EVENTS_EXCHANGE,
    ROUTING_KEY_AGENT_DONE,
    ROUTING_KEY_AGENT_FAILED,
    ROUTING_KEY_CASE_EVENTS,
    ROUTING_KEY_HARNESS_STEP,
)
from fcrm_caseflow.triggers import Trigger

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
    steps: EventConsumer,
    *,
    prefetch: Optional[int] = None,
    step_prefetch: Optional[int] = None,
) -> None:
    """Register H1's manually-acked subscribers, delegating every delivery to
    the matching consumer's ``process`` (dedupe, retry/DLQ; see
    ``fcrm_common.events``). ``ack_policy=AckPolicy.MANUAL`` disables
    FastStream's own ack/nack so ``consumer.process`` is the only thing that
    ever acks or nacks a delivery (:func:`fcrm_common.broker.wire_consumer`).
    ``prefetch`` bounds how many case and agent-result deliveries H1 works on
    at once; ``step_prefetch`` bounds the ``harness.step`` deliveries, each of
    which makes HTTP calls.
    """
    wire_consumer(
        broker,
        case_events,
        exchange=CASE_EVENTS_EXCHANGE,
        routing_keys=[ROUTING_KEY_CASE_EVENTS],
        prefetch=prefetch,
    )
    wire_consumer(
        broker,
        agent_results,
        exchange=AGENT_JOBS_EXCHANGE,
        routing_keys=[ROUTING_KEY_AGENT_DONE, ROUTING_KEY_AGENT_FAILED],
        prefetch=prefetch,
    )
    wire_consumer(
        broker,
        steps,
        exchange=CASE_EVENTS_EXCHANGE,
        routing_keys=[ROUTING_KEY_HARNESS_STEP],
        prefetch=step_prefetch,
    )


def _service_client(
    settings: CaseflowSettings, url: Optional[str], dependency: str
) -> ServiceClient:
    """A pooled client for one peer, authenticated with the service token.

    Raises:
        fcrm_common.settings.MissingSettingsError: the peer's URL is not set.
    """
    env_name = f"{dependency.upper()}_SERVICE_URL"
    return ServiceClient(
        required_value(url, env_name, settings.app_name),
        dependency=dependency,
        caller=settings.app_name,
        token_secret=(
            settings.service_token_secret.get_secret_value()
            if settings.service_token_secret
            else None
        ),
    )


def build_unit(
    settings: CaseflowSettings,
    *,
    mongo: AsyncMongoClient[Document],
    publisher: Publisher,
    step_handlers: Optional[Mapping[StepKind, StepHandler]] = None,
    step_map: Optional[Mapping[Trigger, StepKind]] = None,
) -> WorkerUnit:
    """H1 as a unit a host can wire onto its own broker.

    Args:
        settings: H1's settings (already ``load()``-ed).
        mongo: The host's Mongo client; never closed here.
        publisher: The host's publisher, used by the consumers and the relay.
        step_handlers: The ``harness.step`` handlers by kind. Defaults to every
            core's table (:func:`fcrm_caseflow.cores.default_handlers`); a
            mapping passed here replaces it entirely.
        step_map: Trigger -> step kind for the engine. Defaults to
            :data:`fcrm_caseflow.audit_map.STEP_FOR`; keep it in step with
            ``step_handlers``, since a trigger mapped to a kind without a
            handler enqueues a step that fails.

    Returns:
        The unit: subscribers to wire, the index and relay startup hooks, the
        relay and HTTP-client shutdown hooks, and the ``indexes`` check.

    Raises:
        fcrm_caseflow.steps.UnknownStepKindError: ``step_map`` names a step
            kind that has no handler in ``step_handlers`` (the unit refuses to
            start rather than enqueue steps nobody can run).
        fcrm_common.settings.MissingSettingsError: a service URL or the
            database name is not set.
    """
    wired_steps = step_map if step_map is not None else STEP_FOR
    handlers = default_handlers() if step_handlers is None else step_handlers
    registry = StepRegistry(handlers)
    registry.require(wired_steps.values())
    db_name = required_value(settings.mongodb_db, "MONGODB_DB", settings.app_name)
    audit_http = _service_client(settings, settings.audit_service_url, "audit")
    case_http = _service_client(settings, settings.case_service_url, "case")
    policy_http = _service_client(settings, settings.policy_service_url, "policy")
    scoring_http = _service_client(settings, settings.scoring_service_url, "scoring")
    decision_http = _service_client(settings, settings.decision_service_url, "decision")
    db = mongo[db_name]
    consumed = db[CONSUMED_MESSAGES_COLLECTION]
    outbox_collection = db[OUTBOX_COLLECTION]

    runs_store = CaseRunStore(db[CASE_RUNS_COLLECTION], db[RUN_EVENTS_COLLECTION])
    gate_store = GateStore(db[GATES_COLLECTION])
    results_store = AgentResultStore(db[AGENT_RESULTS_COLLECTION])
    budget_store = CaseBudgetStore(
        db[CASE_BUDGETS_COLLECTION], cap=settings.case_token_cap
    )
    outbox = Outbox(outbox_collection)

    audit = AuditClient(audit_http)
    case_client = CaseClient(case_http)
    engine = CaseflowEngine(
        runs_store,
        gate_store,
        outbox,
        audit,
        step_map=wired_steps,
        transact=mongo_transact(mongo),
        results=results_store,
        ingest_bucket=settings.s3_bucket_canonical,
        oversight=OversightRuns(runs_store),
        raw_inputs=MongoRawInputsReader(db[RAW_INPUTS_COLLECTION]),
        budgets=budget_store,
    )
    step_runner = StepRunner(
        runs_store,
        registry,
        engine,
        audit,
        timeout_s=settings.step_timeout_s,
        case=case_client,
        policy=PolicyClient(policy_http, cache=InMemoryTTLCache()),
        agent_results=results_store,
        scoring=ScoringClient(scoring_http),
        decision=DecisionClient(decision_http),
        gates=gate_store,
        decision_votes=DecisionVotesClient(decision_http),
        reporter=HarnessReporter(
            runs_store, gate_store, audit, case_client, budgets=budget_store
        ),
        oversight=OversightClients(
            examiner=ExaminerClient(audit_http), reflags=ReflagClient(case_http)
        ),
    )

    case_events_consumer = CaseEventsConsumer(
        consumed, publisher, engine=engine, client=mongo
    )
    agent_results_consumer = AgentResultsConsumer(
        consumed, publisher, engine=engine, client=mongo
    )
    step_consumer = HarnessStepConsumer(consumed, publisher, runner=step_runner)

    lease = MongoLease(db[LEASES_COLLECTION], OUTBOX_RELAY_LEASE_NAME, new_id("relay"))
    relay = OutboxRelay(outbox_collection, lease, publisher)
    # The runner logs a relay that dies as it dies, bounds the stop and backs the
    # "outbox" readiness check, as in S1, S3 and S5 (a bare task lost the error
    # until shutdown, left /readyz green and could abort the other shutdown hooks).
    relay_runner = RelayRunner(relay, name="caseflow")
    indexes_ready: list[bool] = []

    async def _ensure_indexes() -> None:
        # FastStream logs an after_startup exception and keeps running, so a
        # failure here must flip readiness itself (D-24: on the first
        # composed boot an IndexOptionsConflict was swallowed while /readyz
        # kept answering 200).
        try:
            await runs_store.ensure_indexes()
            await gate_store.ensure_indexes()
            await results_store.ensure_indexes()
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
        relay_runner.start()

    return WorkerUnit(
        "H1",
        wire=lambda broker: wire_subscribers(
            broker,
            case_events_consumer,
            agent_results_consumer,
            step_consumer,
            prefetch=settings.prefetch,
            step_prefetch=settings.step_prefetch,
        ),
        after_startup=(_ensure_indexes, _start_relay),
        on_shutdown=(
            relay_runner.stop,
            audit_http.aclose,
            case_http.aclose,
            policy_http.aclose,
            scoring_http.aclose,
            decision_http.aclose,
        ),
        checks=(
            ReadinessCheck("indexes", _indexes_check, critical=True),
            ReadinessCheck("outbox", relay_runner.check),
        ),
    )


def build_app(settings: Optional[CaseflowSettings] = None) -> AsgiFastStream:
    """The standalone H1 process (``python -m fcrm_caseflow``): its own
    broker, the unit, and a health ASGI app, with real dependencies.
    """
    configured = settings or CaseflowSettings.load()
    configure_logging(configured.app_name, configured.log_level, env=configured.env)

    mongodb_uri = required_value(
        configured.mongodb_uri, "MONGODB_URI", configured.app_name
    )
    rabbitmq_url = required_value(
        configured.rabbitmq_url, "RABBITMQ_URL", configured.app_name
    )
    mongo_client = get_client(mongodb_uri)
    broker = build_broker(rabbitmq_url)
    unit = build_unit(
        configured, mongo=mongo_client, publisher=FastStreamPublisher(broker)
    )
    unit.wire(broker)

    probe = ReadinessProbe(
        [mongo_check(mongo_client), amqp_check(rabbitmq_url), *unit.checks]
    )
    health_app = build_health_asgi_app(probe)
    return AsgiFastStream(
        broker,
        asgi_routes=[("/healthz", health_app), ("/readyz", health_app)],
        after_startup=list(unit.after_startup),
        on_shutdown=[*unit.on_shutdown, close_clients],
    )

"""The H2 consumer: :data:`AGENT_WORKER_RUN_QUEUE` consumed through
:class:`~fcrm_agent_worker.handler.AgentJobConsumer`.

:func:`build_unit` returns H2 as a :class:`~fcrm_common.worker.WorkerUnit`
for the composite ``fcrm_harness`` process, which owns the broker, the Mongo
client and the health app. H2 owns its outbox (``agent_outbox``) and runs its
own :class:`~fcrm_common.outbox.OutboxRelay` under the lease
``outbox-relay:agent-worker`` (ADR-79), so ``python -m fcrm_agent_worker``
publishes its ``agent.done``/``agent.failed`` standalone; in the harness it runs
beside H1's relay, which holds a lease of another name. :func:`build_app` wraps
the same unit in its own broker and ``/healthz``/``/readyz``
(``fcrm_common.health``'s documented ``AsgiFastStream`` wiring) for
``python -m fcrm_agent_worker``.

Both wire real dependencies (Mongo, the real OPA client, the S4 audit HTTP
client and, by default, the HTTP-backed tool registry over S1, S3 and the
object store); tests call :func:`build_broker` / :func:`wire_subscriber`
directly with fakes instead, per ``faststream.rabbit.TestRabbitBroker``, and
pass ``registry=`` (the seed-backed one, which ``fcrm_evals`` also uses) to
:func:`build_unit` when they want no services.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Optional

from faststream.asgi import AsgiFastStream
from faststream.rabbit import RabbitBroker
from fcrm_common.authz import OPAClient as RealOPAClient
from fcrm_common.broker import FastStreamPublisher, wire_consumer
from fcrm_common.events import Consumer as EventConsumer
from fcrm_common.events import Publisher
from fcrm_common.health import (
    ReadinessCheck,
    ReadinessProbe,
    amqp_check,
    build_health_asgi_app,
    http_check,
    mongo_check,
)
from fcrm_common.http import ServiceClient
from fcrm_common.ids import new_id
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import Document, MongoLease, close_clients, get_client
from fcrm_common.objectstore import ObjectStore
from fcrm_common.outbox import Outbox, OutboxRelay, RelayRunner
from fcrm_common.settings import MissingSettingsError, required_value
from fcrm_common.worker import WorkerUnit
from fcrm_contracts.enums import AgentName, LlmMode, Tier
from fcrm_contracts.events.raw_inputs import RAW_INPUTS_COLLECTION
from fcrm_contracts.versions import PinnedVersions
from fcrm_tools import ToolClients, ToolRegistry, build_http_registry
from pymongo import AsyncMongoClient

from fcrm_agent_worker.audit_client import AuditClient
from fcrm_agent_worker.handler import AgentJobConsumer, mongo_transact
from fcrm_agent_worker.jobs import (
    AGENT_JOBS_COLLECTION,
    AGENT_OUTBOX_COLLECTION,
    LEASES_COLLECTION,
    OUTBOX_RELAY_LEASE_NAME,
    AgentJobStore,
)
from fcrm_agent_worker.live_runtime import ModelFactory, model_factory_for
from fcrm_agent_worker.raw_inputs import MongoRawInputs
from fcrm_agent_worker.settings import AgentWorkerSettings
from fcrm_agent_worker.topology import (
    AGENT_JOBS_EXCHANGE,
    ROUTING_KEY_AGENT_RUN,
)

logger = logging.getLogger(__name__)

CONSUMED_MESSAGES_COLLECTION = "consumed_messages"

InstructionsFor = Callable[[AgentName, Optional[str], PinnedVersions], Optional[str]]
"""``(agent, mode, pinned versions) -> rendered system prompt``."""


def _instructions_for(
    settings: AgentWorkerSettings, *, reduce: bool = False
) -> InstructionsFor:
    """The agent's rendered system prompt (design B §1.5), or with ``reduce``
    its ``<agent>.reduce`` prompt, the one call that merges a split job's
    partial answers (ADR-78; only the narrative has one).

    The template is rendered by :class:`fcrm_prompts.InstructionRenderer`, the
    same code the evals render with: ``{{ config("routing.R4") }}``, the
    ``schema(...)`` envelopes and the skills are resolved before the model sees
    the text, never sent as placeholders. The renderer caches a rendering per
    ``(agent, mode, pinned versions)``.

    A missing ``ai/`` root yields no instructions (the library logs it once,
    and ``load_tiers_config`` already refuses to start without ``ai/``). A
    template that does not render is **not** swallowed: the job raises and
    takes the broker's retry and DLQ path, because an agent must never run
    without the rules its prompt carries.
    """
    from fcrm_prompts import InstructionRenderer

    renderer = InstructionRenderer(ai_root=settings.prompts_dir)

    def instructions_for(
        agent: AgentName, mode: Optional[str], versions: PinnedVersions
    ) -> Optional[str]:
        if not renderer.library.available:
            return None
        prompt_id = f"{agent.value}.reduce" if reduce else None
        return renderer.render(agent, versions, mode, prompt_id=prompt_id).text

    return instructions_for


def require_live_llm_mode(settings: AgentWorkerSettings) -> None:
    """Refuse to start unless agents are to call live Bedrock (ADR-27).

    ``FCRM_LLM_MODE`` may be ``live`` in any case, or unset or empty (both
    mean live). Anything else (``replay``, ``mock``...) names a mode that no
    longer exists, and silently running live under such a setting would
    spend Bedrock tokens the operator believed were being replayed.

    Raises:
        fcrm_common.settings.MissingSettingsError: the mode is not ``live``;
            ``invalid`` names ``FCRM_LLM_MODE`` and the value.
    """
    mode = settings.fcrm_llm_mode.strip().lower()
    if mode in ("", LlmMode.LIVE.value):
        return
    raise MissingSettingsError(
        settings.app_name,
        [],
        [
            f"FCRM_LLM_MODE: {settings.fcrm_llm_mode!r} is not supported, agents "
            "call live Bedrock only (ADR-27); unset it or set it to 'live'"
        ],
    )


def build_broker(rabbitmq_url: str) -> RabbitBroker:
    """An unwired ``RabbitBroker``; call :func:`wire_subscriber` next."""
    return RabbitBroker(rabbitmq_url)


def wire_subscriber(
    broker: RabbitBroker, consumer: EventConsumer, *, prefetch: Optional[int] = None
) -> None:
    """Register the one manually-acked subscriber on
    :data:`AGENT_WORKER_RUN_QUEUE`, delegating every delivery to
    ``consumer.process`` (dedupe, the guard run, retry/DLQ -- see
    ``fcrm_common.events``). ``ack_policy=AckPolicy.MANUAL`` disables
    FastStream's own ack/nack so ``consumer.process`` is the only thing that
    ever acks or nacks a delivery. ``prefetch`` is how many agent jobs run at
    once: each delivery is its own task and each job is one model call.
    """
    wire_consumer(
        broker,
        consumer,
        exchange=AGENT_JOBS_EXCHANGE,
        routing_keys=[ROUTING_KEY_AGENT_RUN],
        prefetch=prefetch,
    )


def build_unit(
    settings: AgentWorkerSettings,
    *,
    mongo: AsyncMongoClient[Document],
    publisher: Publisher,
    registry: Optional[ToolRegistry] = None,
    model_factory: Optional[ModelFactory] = None,
) -> WorkerUnit:
    """H2 as a unit a host can wire onto its own broker.

    Args:
        settings: H2's settings (already ``load()``-ed).
        mongo: The host's Mongo client; never closed here.
        publisher: The host's publisher: H2's outbox relay publishes
            ``agent.done`` and ``agent.failed`` through it, and the consumer
            its retry and DLQ republishing.
        registry: The tool registry. ``None`` (the default) builds the
            HTTP-backed one: S1 and S3 through service-account clients and the
            canonical bucket through the object store. Pass the seed-backed
            registry (tests, evals) to run with no service.
        model_factory: Builds the model for each attempt (a fake in tests).
            ``None`` builds the live one, ending each chain with a second
            Bedrock region when ``BEDROCK_REGION_FALLBACK`` names one.

    Returns:
        The unit: the ``agent-worker.run`` subscriber; the index and relay
        startup hooks; the relay and HTTP-client shutdown hooks; and the
        ``agent_indexes``, ``agent_outbox`` and ``opa`` readiness checks (named
        apart from H1's ``indexes`` and ``outbox``, since the harness probe
        keeps one check per name).

    Raises:
        fcrm_common.settings.MissingSettingsError: ``FCRM_LLM_MODE`` is set to
            anything but ``live`` (ADR-27), or a required variable is missing.
    """
    require_live_llm_mode(settings)
    db_name = required_value(settings.mongodb_db, "MONGODB_DB", settings.app_name)
    opa_url = required_value(settings.opa_url, "OPA_URL", settings.app_name)
    audit_url = required_value(
        settings.audit_service_url, "AUDIT_SERVICE_URL", settings.app_name
    )
    db = mongo[db_name]
    consumed = db[CONSUMED_MESSAGES_COLLECTION]
    outbox_collection = db[AGENT_OUTBOX_COLLECTION]
    outbox = Outbox(outbox_collection)
    jobs = AgentJobStore(db[AGENT_JOBS_COLLECTION], holder=new_id("h2"))

    opa_client = RealOPAClient(opa_url)
    token_secret = (
        settings.service_token_secret.get_secret_value()
        if settings.service_token_secret
        else None
    )
    audit_http = ServiceClient(
        audit_url,
        dependency="audit",
        caller=settings.app_name,
        token_secret=token_secret,
    )
    tool_clients: Optional[ToolClients] = None
    if registry is None:
        tool_clients = ToolClients.create(
            case_url=required_value(
                settings.case_service_url, "CASE_SERVICE_URL", settings.app_name
            ),
            policy_url=required_value(
                settings.policy_service_url, "POLICY_SERVICE_URL", settings.app_name
            ),
            audit_url=audit_url,
            caller=settings.app_name,
            token_secret=token_secret,
            audit_client=audit_http,
        )
        registry = build_http_registry(
            tool_clients,
            ObjectStore.from_settings(settings),
            canonical_bucket=settings.s3_bucket_canonical,
        )

    from fcrm_agents.models import load_tiers_config

    tiers_config = load_tiers_config(settings.prompts_dir)
    consumer = AgentJobConsumer(
        consumed,
        publisher,
        registry=registry,
        permit_client=opa_client,
        audit=AuditClient(audit_http),
        tiers_config=tiers_config,
        instructions_for=_instructions_for(settings),
        model_factory=model_factory
        or model_factory_for(
            settings.bedrock_region_fallback,
            settings.aws_region,
            max_pool_connections=settings.prefetch,
        ),
        raw_inputs=MongoRawInputs(db[RAW_INPUTS_COLLECTION]),
        tool_call_cap=settings.agent_max_tool_calls,
        transact=mongo_transact(mongo),
        jobs=jobs,
        outbox=outbox,
        max_parts=settings.agent_max_parts,
        reduce_tier=Tier(settings.agent_reduce_tier),
        output_floor_tokens=settings.agent_output_floor_tokens,
        reduce_instructions_for=_instructions_for(settings, reduce=True),
    )

    lease = MongoLease(db[LEASES_COLLECTION], OUTBOX_RELAY_LEASE_NAME, new_id("relay"))
    relay = OutboxRelay(outbox_collection, lease, publisher)
    # As in H1: the runner logs a relay that dies, bounds the stop and backs
    # the readiness check.
    relay_runner = RelayRunner(relay, name="agent-worker")
    indexes_ready: list[bool] = []

    async def _ensure_indexes() -> None:
        # FastStream logs an after_startup exception and keeps running, so a
        # failure here must flip readiness itself (D-24).
        try:
            await jobs.ensure_indexes()
            await outbox.ensure_indexes()
            await consumer.ensure_indexes()
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

    async def _close_clients() -> None:
        await opa_client.aclose()
        # The tool clients include the audit client (one pool per peer).
        await (tool_clients or audit_http).aclose()

    return WorkerUnit(
        "H2",
        wire=lambda broker: wire_subscriber(
            broker, consumer, prefetch=settings.prefetch
        ),
        after_startup=(_ensure_indexes, _start_relay),
        on_shutdown=(relay_runner.stop, _close_clients),
        checks=(
            ReadinessCheck("agent_indexes", _indexes_check, critical=True),
            ReadinessCheck("agent_outbox", relay_runner.check),
            _opa_health_check(opa_url),
        ),
    )


def build_app(
    settings: Optional[AgentWorkerSettings] = None,
    *,
    registry: Optional[ToolRegistry] = None,
    model_factory: Optional[ModelFactory] = None,
) -> AsgiFastStream:
    """The standalone H2 process (``python -m fcrm_agent_worker``): its own
    broker, the unit, and a health ASGI app, with real dependencies.
    """
    configured = settings or AgentWorkerSettings.load()
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
        configured,
        mongo=mongo_client,
        publisher=FastStreamPublisher(broker),
        registry=registry,
        model_factory=model_factory,
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


def _opa_health_check(opa_url: str) -> ReadinessCheck:
    """``GET {OPA_URL}/health`` as a non-critical readiness check."""
    return http_check("opa", f"{opa_url.rstrip('/')}/health", critical=False)

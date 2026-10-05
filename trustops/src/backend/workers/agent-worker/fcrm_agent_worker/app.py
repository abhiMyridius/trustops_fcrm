"""The H2 consumer: :data:`AGENT_WORKER_RUN_QUEUE` consumed through
:class:`~fcrm_agent_worker.handler.AgentJobConsumer`.

:func:`build_unit` returns H2 as a :class:`~fcrm_common.worker.WorkerUnit`
for the composite ``fcrm_harness`` process, which owns the broker, the Mongo
client and the health app. :func:`build_app` wraps the same unit in its own
broker and ``/healthz``/``/readyz`` (``fcrm_common.health``'s documented
``AsgiFastStream`` wiring) for ``python -m fcrm_agent_worker``.

Both wire real dependencies (Mongo, the real OPA client, the S4 audit HTTP
client, the seed-backed tool registry -- see the package report for why that
last one is not yet a live, HTTP-backed registry); tests call
:func:`build_broker` / :func:`wire_subscriber` directly with fakes instead,
per ``faststream.rabbit.TestRabbitBroker``.
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
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import Document, close_clients, get_client
from fcrm_common.settings import MissingSettingsError, required_value
from fcrm_common.worker import WorkerUnit
from fcrm_contracts.enums import AgentName, LlmMode
from fcrm_tools import ToolRegistry, build_seed_registry
from pymongo import AsyncMongoClient

from fcrm_agent_worker.audit_client import AuditClient
from fcrm_agent_worker.handler import AgentJobConsumer
from fcrm_agent_worker.live_runtime import ModelFactory, live_model_for_slot
from fcrm_agent_worker.settings import AgentWorkerSettings
from fcrm_agent_worker.topology import (
    AGENT_JOBS_EXCHANGE,
    ROUTING_KEY_AGENT_RUN,
)

logger = logging.getLogger(__name__)

CONSUMED_MESSAGES_COLLECTION = "consumed_messages"

InstructionsFor = Callable[[AgentName], Optional[str]]


def _instructions_for(settings: AgentWorkerSettings) -> InstructionsFor:
    """The agent's rendered system prompt, if ``ai/`` is mounted.

    Only the prompt's raw body is read (``fcrm_prompts.PromptLibrary``);
    the Jinja-templated variable substitution ``ai/prompts`` expects is a
    separate, not-yet-built rendering concern (see the package report) --
    this is deliberately a conservative "no instructions" fallback, not a
    silent partial render.
    """
    from fcrm_prompts import PromptLibrary
    from fcrm_prompts.errors import PromptNotFoundError

    library = PromptLibrary(settings.prompts_dir)

    def instructions_for(agent: AgentName) -> Optional[str]:
        if not library.available:
            return None
        try:
            return library.prompt(f"{agent.value}.system").body
        except PromptNotFoundError:
            logger.warning("no system prompt for %s; running with none", agent.value)
            return None

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
    model_factory: ModelFactory = live_model_for_slot,
) -> WorkerUnit:
    """H2 as a unit a host can wire onto its own broker.

    Args:
        settings: H2's settings (already ``load()``-ed).
        mongo: The host's Mongo client; never closed here.
        publisher: The host's publisher, used to publish ``agent.done`` and
            ``agent.failed``.
        registry: The tool registry; defaults to the seed-backed one.
        model_factory: Builds the model for each attempt (a fake in tests).

    Returns:
        The unit: the ``agent-worker.run`` subscriber, an OPA readiness check,
        and a shutdown hook closing the OPA and audit HTTP clients.

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
    consumed = mongo[db_name][CONSUMED_MESSAGES_COLLECTION]

    opa_client = RealOPAClient(opa_url)
    audit_http = ServiceClient(
        audit_url,
        dependency="audit",
        caller=settings.app_name,
        token_secret=(
            settings.service_token_secret.get_secret_value()
            if settings.service_token_secret
            else None
        ),
    )

    from fcrm_agents.models import load_tiers_config

    tiers_config = load_tiers_config(settings.prompts_dir)
    consumer = AgentJobConsumer(
        consumed,
        publisher,
        registry=registry if registry is not None else build_seed_registry(),
        permit_client=opa_client,
        audit=AuditClient(audit_http),
        tiers_config=tiers_config,
        instructions_for=_instructions_for(settings),
        model_factory=model_factory,
        tool_call_cap=settings.agent_max_tool_calls,
        client=mongo,
    )

    async def _close_clients() -> None:
        await opa_client.aclose()
        await audit_http.aclose()

    return WorkerUnit(
        "H2",
        wire=lambda broker: wire_subscriber(
            broker, consumer, prefetch=settings.prefetch
        ),
        on_shutdown=(_close_clients,),
        checks=(_opa_health_check(opa_url),),
    )


def build_app(
    settings: Optional[AgentWorkerSettings] = None,
    *,
    registry: Optional[ToolRegistry] = None,
    model_factory: ModelFactory = live_model_for_slot,
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
        on_shutdown=[*unit.on_shutdown, close_clients],
    )


def _opa_health_check(opa_url: str) -> ReadinessCheck:
    """``GET {OPA_URL}/health`` as a non-critical readiness check."""
    return http_check("opa", f"{opa_url.rstrip('/')}/health", critical=False)

"""The H2 process: one ``RabbitBroker`` consuming :data:`AGENT_WORKER_RUN_QUEUE`
through :class:`~fcrm_agent_worker.handler.AgentJobConsumer`, serving
``/healthz``/``/readyz`` on the same ASGI app (``fcrm_common.health``'s
documented ``AsgiFastStream`` wiring: a dependency-free health app mounted
alongside the broker, run under uvicorn -- there is no separate FastAPI
process for this worker).

``build_app`` wires real dependencies (Mongo, the real OPA client, the S4
audit HTTP client, the seed-backed tool registry -- see the package report
for why that last one is not yet a live, HTTP-backed registry) and is what
``fcrm-agent-worker`` (``__main__.py``) runs under uvicorn; tests call
:func:`build_broker` / :func:`wire_subscriber` directly with fakes instead,
per ``faststream.rabbit.TestRabbitBroker``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Optional

from faststream import AckPolicy
from faststream.asgi import AsgiFastStream
from faststream.rabbit import RabbitBroker, RabbitMessage
from fcrm_common.authz import OPAClient as RealOPAClient
from fcrm_common.events import Consumer as EventConsumer
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
from fcrm_common.mongo import get_client
from fcrm_contracts.enums import AgentName
from fcrm_tools import ToolRegistry, build_seed_registry

from fcrm_agent_worker.audit_client import AuditClient
from fcrm_agent_worker.handler import AgentJobConsumer
from fcrm_agent_worker.live_runtime import ModelFactory, live_model_for_slot
from fcrm_agent_worker.publisher import FastStreamPublisher
from fcrm_agent_worker.settings import AgentWorkerSettings
from fcrm_agent_worker.topology import (
    AGENT_JOBS_EXCHANGE,
    AGENT_WORKER_RUN_QUEUE,
    ROUTING_KEY_AGENT_RUN,
    passive_exchange,
    passive_queue,
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


def build_broker(rabbitmq_url: str) -> RabbitBroker:
    """An unwired ``RabbitBroker``; call :func:`wire_subscriber` next."""
    return RabbitBroker(rabbitmq_url)


def wire_subscriber(broker: RabbitBroker, consumer: EventConsumer) -> None:
    """Register the one manually-acked subscriber on
    :data:`AGENT_WORKER_RUN_QUEUE`, delegating every delivery to
    ``consumer.process`` (dedupe, the guard run, retry/DLQ -- see
    ``fcrm_common.events``). ``ack_policy=AckPolicy.MANUAL`` disables
    FastStream's own ack/nack so ``consumer.process`` is the only thing
    that ever acks or nacks a delivery.
    """

    @broker.subscriber(
        passive_queue(AGENT_WORKER_RUN_QUEUE, routing_key=ROUTING_KEY_AGENT_RUN),
        passive_exchange(AGENT_JOBS_EXCHANGE),
        ack_policy=AckPolicy.MANUAL,
    )
    async def _consume(msg: RabbitMessage) -> None:
        await consumer.process(
            msg.body,
            msg.headers,
            ack=msg.ack,
            nack=lambda requeue: msg.nack(requeue=requeue),
        )


def build_app(
    settings: Optional[AgentWorkerSettings] = None,
    *,
    registry: Optional[ToolRegistry] = None,
    model_factory: ModelFactory = live_model_for_slot,
) -> AsgiFastStream:
    """The full H2 process: broker + health ASGI app, real dependencies."""
    configured = settings or AgentWorkerSettings.load()
    configure_logging(configured.app_name, configured.log_level, env=configured.env)

    # fail_fast() in .load() already guarantees these; narrow for mypy.
    assert configured.mongodb_uri is not None
    assert configured.mongodb_db is not None
    assert configured.opa_url is not None
    assert configured.audit_service_url is not None
    assert configured.rabbitmq_url is not None

    mongo_client = get_client(configured.mongodb_uri)
    db = mongo_client[configured.mongodb_db]
    consumed = db[CONSUMED_MESSAGES_COLLECTION]

    opa_client = RealOPAClient(configured.opa_url)
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

    from fcrm_agents.models import load_tiers_config

    tiers_config = load_tiers_config(configured.prompts_dir)

    broker = build_broker(configured.rabbitmq_url)
    publisher = FastStreamPublisher(broker)
    consumer = AgentJobConsumer(
        consumed,
        publisher,
        registry=registry if registry is not None else build_seed_registry(),
        permit_client=opa_client,
        audit=audit,
        tiers_config=tiers_config,
        instructions_for=_instructions_for(configured),
        model_factory=model_factory,
        tool_call_cap=configured.agent_max_tool_calls,
        client=mongo_client,
    )
    wire_subscriber(broker, consumer)

    probe = ReadinessProbe(
        [
            mongo_check(mongo_client),
            amqp_check(configured.rabbitmq_url),
            _opa_health_check(configured.opa_url),
        ]
    )
    health_app = build_health_asgi_app(probe)
    return AsgiFastStream(
        broker, asgi_routes=[("/healthz", health_app), ("/readyz", health_app)]
    )


def _opa_health_check(opa_url: str) -> ReadinessCheck:
    """``GET {OPA_URL}/health`` as a non-critical readiness check."""
    return http_check("opa", f"{opa_url.rstrip('/')}/health", critical=False)

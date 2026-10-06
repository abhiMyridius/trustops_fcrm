"""``fcrm_agent_worker.handler.AgentJobConsumer``: one ``agent.run`` job ->
``agent.done``/``agent.failed`` (+ a best-effort S4 audit write), called
directly (``handle()``, not through a broker -- :mod:`test_app_wiring`
exercises the real FastStream/RabbitMQ wiring)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fcrm_agent_worker.handler import AgentJobConsumer
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_agent_worker.topology import (
    AGENT_JOBS_EXCHANGE,
    ROUTING_KEY_AGENT_DONE,
    ROUTING_KEY_AGENT_FAILED,
)
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import AgentDone, AgentFailed, JobPayload
from fcrm_guardrails.opa_client import OPAClient
from fcrm_tools import ToolRegistry


def _consumer(
    *,
    registry: ToolRegistry,
    permit_client: OPAClient,
    audit: Any,
    publisher: Any,
    tiers_config: dict[str, Any],
    model_factory: ModelFactory,
    consumed: Any,
) -> AgentJobConsumer:
    return AgentJobConsumer(
        consumed,
        publisher,
        registry=registry,
        permit_client=permit_client,
        audit=audit,
        tiers_config=tiers_config,
        model_factory=model_factory,
    )


async def test_handle_publishes_agent_done_and_writes_audit(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    seed_registry: ToolRegistry,
    allow_all_opa_client: OPAClient,
    fake_tiers_config: dict[str, Any],
    recording_publisher: Any,
    recording_audit: Any,
    fake_consumed: Any,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    consumer = _consumer(
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=recording_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
        consumed=fake_consumed,
    )
    await consumer.handle(demo_job_envelope, None)

    assert len(recording_publisher.sent) == 1
    exchange, routing_key, body, _headers = recording_publisher.sent[0]
    assert exchange == AGENT_JOBS_EXCHANGE
    assert routing_key == ROUTING_KEY_AGENT_DONE
    sent_envelope = Envelope.model_validate_json(body)
    done = AgentDone.model_validate(sent_envelope.payload)
    assert done.job_id == "job-research-1"
    assert len(done.output["accepted"]) == len(research_outputs)
    assert done.model == "fake-fixed-output"

    # The walkthrough vocabulary: Guard 2's permit and Guard 4's verdict. No
    # ``agent.done`` record: H1 records what the agent proposed (``proposal.*``).
    assert [r.type for r in recording_audit.records] == [
        "guard.permit",
        "guard.output",
    ]
    assert all(r.case_id == done.case_id for r in recording_audit.records)


async def test_handle_publishes_agent_failed_on_permit_denial(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    seed_registry: ToolRegistry,
    deny_all_opa_client: OPAClient,
    fake_tiers_config: dict[str, Any],
    recording_publisher: Any,
    recording_audit: Any,
    fake_consumed: Any,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    consumer = _consumer(
        registry=seed_registry,
        permit_client=deny_all_opa_client,
        audit=recording_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
        consumed=fake_consumed,
    )
    await consumer.handle(demo_job_envelope, None)

    assert len(recording_publisher.sent) == 1
    exchange, routing_key, body, _headers = recording_publisher.sent[0]
    assert exchange == AGENT_JOBS_EXCHANGE
    assert routing_key == ROUTING_KEY_AGENT_FAILED
    sent_envelope = Envelope.model_validate_json(body)
    failed = AgentFailed.model_validate(sent_envelope.payload)
    assert failed.job_id == "job-research-1"
    assert failed.reason.startswith("permit_denied:")

    assert [r.type for r in recording_audit.records] == [
        "guard.permit",
        "agent.failed",
    ]
    assert recording_audit.records[0].data["allowed"] is False


async def test_handle_reports_agent_failed_on_context_pack_error(
    seed_registry: ToolRegistry,
    allow_all_opa_client: OPAClient,
    fake_tiers_config: dict[str, Any],
    recording_publisher: Any,
    recording_audit: Any,
    fake_consumed: Any,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    """``intake`` needs a ``doc_id`` H1 did not supply in this job (the
    JobPayload -> refs gap the package report flags) -- a deterministic,
    legitimate ``agent.failed``, never a broker-level retry."""
    from fcrm_common.events import build_envelope
    from fcrm_contracts.enums import AgentName, Tier
    from fcrm_seed.loader import load_raw
    from fcrm_tools import seed_pins

    payload = JobPayload(
        job_id="job-intake-1",
        case_id="FCRM-2026-0151",
        run_id="run-intake-1",
        agent=AgentName.INTAKE,
        tier=Tier.CHEAP,
        step_key="intake",
        attempt=1,
        pins=seed_pins(load_raw()),
    )
    envelope = build_envelope(
        "agent.run",
        payload.model_dump(mode="json"),
        case_id=payload.case_id,
        run_id=payload.run_id,
    )
    consumer = _consumer(
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=recording_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning([]),
        consumed=fake_consumed,
    )
    await consumer.handle(envelope, None)

    assert len(recording_publisher.sent) == 1
    _exchange, routing_key, body, _headers = recording_publisher.sent[0]
    assert routing_key == ROUTING_KEY_AGENT_FAILED
    failed = AgentFailed.model_validate(Envelope.model_validate_json(body).payload)
    assert failed.reason.startswith("context_pack_error:")


async def test_audit_write_failure_does_not_crash_handle(
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    seed_registry: ToolRegistry,
    allow_all_opa_client: OPAClient,
    fake_tiers_config: dict[str, Any],
    recording_publisher: Any,
    failing_audit: Any,
    fake_consumed: Any,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    """S4 being unavailable degrades audit completeness, never job
    delivery (handler module docstring)."""
    consumer = _consumer(
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=failing_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
        consumed=fake_consumed,
    )
    await consumer.handle(demo_job_envelope, None)

    assert len(recording_publisher.sent) == 1
    assert failing_audit.records == []


async def test_handle_asks_for_instructions_with_the_packs_mode_and_pins(
    demo_job_envelope: Envelope,
    demo_job_payload: JobPayload,
    research_outputs: list[Any],
    seed_registry: ToolRegistry,
    allow_all_opa_client: OPAClient,
    fake_tiers_config: dict[str, Any],
    recording_publisher: Any,
    recording_audit: Any,
    fake_consumed: Any,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    asked: list[tuple[Any, Any, Any]] = []

    def instructions_for(agent: Any, mode: Any, versions: Any) -> str:
        asked.append((agent, mode, versions))
        return "rendered system prompt"

    consumer = AgentJobConsumer(
        fake_consumed,
        recording_publisher,
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=recording_audit,
        tiers_config=fake_tiers_config,
        instructions_for=instructions_for,
        model_factory=make_model_factory_returning(research_outputs),
    )
    await consumer.handle(demo_job_envelope, None)

    assert asked == [(demo_job_payload.agent, None, demo_job_payload.pins)]

"""What an agent was given when its job failed (flow F-path): H2 keeps it before it
reports ``agent.failed`` and names it in ``raw_inputs_ref``, so the analyst who
finishes the step by hand sees what the agent saw.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from fcrm_agent_worker.handler import AgentJobConsumer
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_agent_worker.raw_inputs import MongoRawInputs, raw_inputs_of
from fcrm_agents.context_pack import ContextPack
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import AgentFailed, JobPayload
from fcrm_contracts.events.raw_inputs import (
    RAW_INPUTS_MAX_CHARS,
    AgentRawInputs,
    raw_inputs_ref,
)
from fcrm_guardrails.opa_client import OPAClient
from fcrm_tools import ToolRegistry
from pymongo.errors import PyMongoError


class _Collection:
    """Just enough of a Mongo collection for ``MongoRawInputs``: an upsert by id
    that keeps what a second write would not change (``$setOnInsert``)."""

    def __init__(self, *, down: bool = False) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.down = down

    async def update_one(
        self, filter_: dict[str, Any], update: dict[str, Any], upsert: bool = False
    ) -> None:
        if self.down:
            raise PyMongoError("mongo is down")
        self.docs.setdefault(
            filter_["_id"], {"_id": filter_["_id"], **update["$setOnInsert"]}
        )


class _Sink:
    """A raw-inputs sink that records what it was given."""

    def __init__(self, ref: Optional[str]) -> None:
        self.ref = ref
        self.saved: list[tuple[JobPayload, Optional[ContextPack], Optional[str]]] = []

    async def save(
        self,
        job: JobPayload,
        pack: Optional[ContextPack],
        *,
        error: Optional[str] = None,
    ) -> Optional[str]:
        self.saved.append((job, pack, error))
        return self.ref


def _failed(recording_publisher: Any) -> AgentFailed:
    _exchange, _key, body, _headers = recording_publisher.sent[0]
    return AgentFailed.model_validate(Envelope.model_validate_json(body).payload)


def test_the_stored_inputs_are_the_job_and_the_pack_as_the_model_received_it(
    demo_job_payload: JobPayload, research_pack: ContextPack
) -> None:
    stored = raw_inputs_of(demo_job_payload, research_pack, None)

    assert stored.job_id == "job-research-1"
    assert stored.agent == "research"
    assert stored.text == research_pack.user_text()
    assert stored.sections == research_pack.section_names()
    assert stored.pack_hash == research_pack.pack_hash
    assert stored.truncated is False
    assert stored.error is None
    assert stored.job == demo_job_payload.model_dump(mode="json")


def test_a_text_over_the_bound_is_cut_and_marked(
    demo_job_payload: JobPayload, research_pack: ContextPack
) -> None:
    big = research_pack.model_copy(
        update={"sections": {"Case": "x" * (RAW_INPUTS_MAX_CHARS + 10)}}
    )

    stored = raw_inputs_of(demo_job_payload, big, None)

    assert len(stored.text) == RAW_INPUTS_MAX_CHARS
    assert stored.truncated is True


def test_a_job_that_built_no_pack_keeps_the_job_and_why(
    demo_job_payload: JobPayload,
) -> None:
    stored = raw_inputs_of(demo_job_payload, None, "a tool refused")

    assert stored.text == ""
    assert stored.pack_hash is None
    assert stored.error == "a tool refused"


async def test_mongo_keeps_one_document_per_job_and_returns_its_reference(
    demo_job_payload: JobPayload, research_pack: ContextPack
) -> None:
    collection = _Collection()
    sink = MongoRawInputs(collection)  # type: ignore[arg-type]

    first = await sink.save(demo_job_payload, research_pack)
    again = await sink.save(demo_job_payload, research_pack, error="later")

    assert first == again == raw_inputs_ref("job-research-1")
    [doc] = collection.docs.values()
    assert doc["_id"] == "job-research-1"
    assert doc["error"] is None  # a redelivered failure keeps the first
    assert (
        AgentRawInputs.model_validate(
            {**{k: v for k, v in doc.items() if k != "_id"}, "job_id": doc["_id"]}
        ).text
        == research_pack.user_text()
    )


async def test_a_store_that_is_down_costs_the_inputs_not_the_handoff(
    demo_job_payload: JobPayload, research_pack: ContextPack
) -> None:
    sink = MongoRawInputs(_Collection(down=True))  # type: ignore[arg-type]

    assert await sink.save(demo_job_payload, research_pack) is None


def _consumer(
    sink: Optional[_Sink],
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
        raw_inputs=sink,
    )


async def test_a_failed_job_names_the_inputs_it_kept(
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
    sink = _Sink(raw_inputs_ref("job-research-1"))
    consumer = _consumer(
        sink,
        registry=seed_registry,
        permit_client=deny_all_opa_client,
        audit=recording_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
        consumed=fake_consumed,
    )

    await consumer.handle(demo_job_envelope, None)

    failed = _failed(recording_publisher)
    assert failed.raw_inputs_ref == raw_inputs_ref("job-research-1")
    [(job, pack, error)] = sink.saved
    assert job.job_id == "job-research-1"
    assert pack is not None  # the pack the agent was shown
    assert error is None


async def test_a_failed_job_without_a_sink_names_no_inputs(
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
        None,
        registry=seed_registry,
        permit_client=deny_all_opa_client,
        audit=recording_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
        consumed=fake_consumed,
    )

    await consumer.handle(demo_job_envelope, None)

    assert _failed(recording_publisher).raw_inputs_ref is None


async def test_a_sink_that_kept_nothing_does_not_stop_the_failure_report(
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
        _Sink(None),
        registry=seed_registry,
        permit_client=deny_all_opa_client,
        audit=recording_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
        consumed=fake_consumed,
    )

    await consumer.handle(demo_job_envelope, None)

    failed = _failed(recording_publisher)
    assert failed.raw_inputs_ref is None
    assert failed.reason.startswith("permit_denied:")


async def test_a_job_whose_pack_could_not_be_built_keeps_the_job_and_the_error(
    seed_registry: ToolRegistry,
    allow_all_opa_client: OPAClient,
    fake_tiers_config: dict[str, Any],
    recording_publisher: Any,
    recording_audit: Any,
    fake_consumed: Any,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
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
    sink = _Sink(raw_inputs_ref("job-intake-1"))
    consumer = _consumer(
        sink,
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=recording_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning([]),
        consumed=fake_consumed,
    )

    await consumer.handle(envelope, None)

    failed = _failed(recording_publisher)
    assert failed.raw_inputs_ref == raw_inputs_ref("job-intake-1")
    [(job, pack, error)] = sink.saved
    assert pack is None
    assert error is not None
    assert failed.reason == f"context_pack_error: {error}"


def test_build_unit_keeps_the_inputs_in_the_harness_database(
    monkeypatch: Any, seed_registry: ToolRegistry
) -> None:
    from pathlib import Path
    from unittest.mock import MagicMock

    from fcrm_agent_worker import app as agent_app
    from fcrm_agent_worker.settings import AgentWorkerSettings

    seen: dict[str, Any] = {}

    class _Recording:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            seen.update(kwargs)

    monkeypatch.setattr(agent_app, "AgentJobConsumer", _Recording)
    mongo = MagicMock()
    settings = AgentWorkerSettings(
        mongodb_uri="mongodb://localhost:27017/?replicaSet=rs0",
        mongodb_db="fcrm_harness",
        service_token_secret="x" * 32,
        opa_url="http://localhost:8181",
        identity_jwks_url="http://localhost:8000/.well-known/jwks.json",
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        case_service_url="http://localhost:8001",
        scoring_service_url="http://localhost:8002",
        policy_service_url="http://localhost:8003",
        audit_service_url="http://localhost:8004",
        decision_service_url="http://localhost:8005",
        prompts_dir=str(Path(__file__).parents[4] / "ai"),
    )

    agent_app.build_unit(
        settings, mongo=mongo, publisher=MagicMock(), registry=seed_registry
    )

    assert isinstance(seen["raw_inputs"], MongoRawInputs)
    mongo.__getitem__.return_value.__getitem__.assert_any_call("agent_raw_inputs")

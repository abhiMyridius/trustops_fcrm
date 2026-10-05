"""Fixtures for H2 (:mod:`fcrm_agent_worker`) tests.

Lives beside this unit's own tests rather than the root ``tests/conftest.py``
-- the same exception the ingestion-worker and context-worker test suites
take (see their own ``conftest.py`` docstrings): these fixtures build a real
demo :class:`~fcrm_agents.context_pack.ContextPack` with the real,
seed-backed tool registry (:mod:`fcrm_agents`/:mod:`fcrm_tools`, already
proven by the offline runtime) and serve only this unit's tests.

No live broker, no live Mongo, no live Bedrock: the model is always a
:class:`~pydantic_ai.models.function.FunctionModel` behind a ``ModelFactory``
fake (see :func:`model_factory_returning` / :func:`model_factory_raising`),
and the Mongo ``consumed_messages`` collection is :class:`FakeConsumed`, the
same minimal double ``tests/unit/packages/common/test_common_events.py``
uses for :class:`~fcrm_common.events.Consumer` itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

import pytest
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_agents.builders import builder_for
from fcrm_agents.context_pack import BuildRequest, ContextPack
from fcrm_agents.demo import research_output
from fcrm_agents.runtime import StaticPermitClient
from fcrm_agents.spec import REGISTRY, AgentSpec
from fcrm_common.events import build_envelope
from fcrm_contracts.enums import AgentName
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import JobPayload
from fcrm_guardrails import PermitGuard
from fcrm_guardrails.opa_client import OPAClient
from fcrm_tools import DEMO_CASE_ID, DEMO_RUN_ID, ToolRegistry, build_seed_registry
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

RESEARCH_SPEC: AgentSpec = REGISTRY[AgentName.RESEARCH]

#: A tiny, fake ``ai/models/tiers.yaml``-shaped mapping. Model ids here are
#: deliberately not Bedrock-shaped (the repo-wide lock test greps for real
#: inference-profile ids, never test fixtures) -- the ``model_factory`` fake
#: ignores them entirely; only ``max_tokens``/``wall_clock_s`` shape the
#: ``UsageLimits``/``ModelSettings`` this module's ``LiveRun`` builds.
FAKE_TIERS_CONFIG: dict[str, Any] = {
    "tiers": {
        "cheap": {
            "primary": "fake:cheap-primary",
            "fallbacks": ["fake:cheap-fallback"],
            "max_tokens": 2000,
            "wall_clock_s": 60,
        },
        "mid": {
            "primary": "fake:mid-primary",
            "fallbacks": ["fake:mid-fallback"],
            "max_tokens": 3500,
            "wall_clock_s": 120,
        },
        "strong": {
            "primary": "fake:strong-primary",
            "fallbacks": ["fake:strong-fallback"],
            "max_tokens": 5000,
            "wall_clock_s": 180,
        },
        "judge": {
            "primary": "fake:judge-primary",
            "fallbacks": ["fake:judge-fallback"],
            "max_tokens": 4000,
            "wall_clock_s": 180,
        },
    },
    "models": {},
}


@pytest.fixture
def seed_registry() -> ToolRegistry:
    """The real, seed-backed tool registry (no network)."""
    return build_seed_registry()


@pytest.fixture
async def research_pack(
    seed_registry: ToolRegistry, demo_job_payload: JobPayload
) -> ContextPack:
    """A real ``research`` context pack for the demo case, built exactly as
    :meth:`fcrm_agent_worker.handler.AgentJobConsumer.handle` builds it:
    from the bare ``JobPayload`` via ``BuildRequest.from_job`` (no H1-side
    ``refs``, unlike ``fcrm_agents.demo.demo_request`` -- see the package
    report's context-pack ``refs`` gap). Keeping this fixture and the
    handler's own pack-build call identical is what makes
    :mod:`test_handler`'s expectations (which outputs Guard 4 accepts)
    match what ``handle()`` actually builds.
    """
    return await builder_for(AgentName.RESEARCH).build(
        BuildRequest.from_job(demo_job_payload), seed_registry
    )


@pytest.fixture
def demo_job_payload() -> JobPayload:
    """The ``agent.run`` job H1 would dispatch for ``research`` on the
    demo case (real pinned versions, from the seed -- so context-pack
    building's version-scope checks pass)."""
    from fcrm_seed.loader import load_raw
    from fcrm_tools import seed_pins

    return JobPayload(
        job_id="job-research-1",
        case_id=DEMO_CASE_ID,
        run_id=DEMO_RUN_ID,
        agent=AgentName.RESEARCH,
        tier=RESEARCH_SPEC.tier,
        step_key="assess",
        attempt=1,
        pins=seed_pins(load_raw()),
    )


@pytest.fixture
def demo_job_envelope(demo_job_payload: JobPayload) -> Envelope:
    """``demo_job_payload`` as the ``agent.run`` envelope a consumer reads."""
    return build_envelope(
        "agent.run",
        demo_job_payload.model_dump(mode="json"),
        case_id=demo_job_payload.case_id,
        run_id=demo_job_payload.run_id,
    )


class DenyAllOPA:
    """An :class:`~fcrm_guardrails.opa_client.OPAClient` that denies
    everything (simulates an OPA policy refusal, not a transport failure).
    """

    async def tool_permit(
        self, *, agent: str, tool: str, case_id: str, role: str, run_id: str
    ) -> bool:
        return False


@pytest.fixture
def allow_all_opa_client() -> OPAClient:
    """The raw :class:`~fcrm_guardrails.opa_client.OPAClient` (not yet
    wrapped in a :class:`~fcrm_guardrails.PermitGuard`) -- what
    :class:`~fcrm_agent_worker.handler.AgentJobConsumer` takes and wraps
    itself."""
    return StaticPermitClient.from_registry()


@pytest.fixture
def deny_all_opa_client() -> OPAClient:
    return DenyAllOPA()


@pytest.fixture
def allow_all_permit(allow_all_opa_client: OPAClient) -> PermitGuard:
    """Guard 2 wired to the registry's own allow-list (every agent's own
    tools permitted; nothing else) -- for tests that call
    :mod:`fcrm_agent_worker.live_runtime` directly, which takes an
    already-built :class:`PermitGuard`.
    """
    return PermitGuard(allow_all_opa_client)


@pytest.fixture
def deny_all_permit(deny_all_opa_client: OPAClient) -> PermitGuard:
    """Guard 2 wired to an OPA client that denies every tool."""
    return PermitGuard(deny_all_opa_client)


def model_factory_returning(
    outputs: Sequence[BaseModel],
    *,
    tool_calls: Sequence[tuple[str, dict[str, Any]]] = (),
) -> ModelFactory:
    """A :data:`~fcrm_agent_worker.live_runtime.ModelFactory` returning a
    fixed-output ``FunctionModel`` regardless of attempt/slot -- the same
    technique ``fcrm_agents.runtime.fixed_output_model`` uses for the
    offline runtime, reimplemented here (that name is private to its
    module) so this unit's tests never make a network call either.
    """
    payload = [o.model_dump(mode="json") for o in outputs]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        from pydantic_ai.messages import ToolCallPart

        responses = sum(1 for m in messages if isinstance(m, ModelResponse))
        if responses < len(tool_calls):
            name, args = tool_calls[responses]
            return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])
        output_tool = info.output_tools[0]
        properties = output_tool.parameters_json_schema.get("properties", {})
        key = next(iter(properties)) if len(properties) == 1 else "response"
        return ModelResponse(
            parts=[ToolCallPart(tool_name=output_tool.name, args={key: payload})]
        )

    def factory(models: Any, plan: Any) -> FunctionModel:
        return FunctionModel(respond, model_name="fake-fixed-output")

    return factory


def model_factory_raising(exc: Exception) -> ModelFactory:
    """A ``ModelFactory`` whose model raises ``exc`` on every call -- for
    exercising :meth:`~fcrm_agent_worker.live_runtime.LiveRun.call_model`'s
    failure-mapping branches deterministically (``UsageLimitExceeded``,
    ``UnexpectedModelBehavior``, ``FallbackExceptionGroup``, a plain
    ``TimeoutError``), without needing a real model or network call to
    fail in just the right way.
    """

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise exc

    def factory(models: Any, plan: Any) -> FunctionModel:
        return FunctionModel(respond, model_name="fake-raising")

    return factory


@pytest.fixture
def research_outputs(research_pack: ContextPack) -> list[BaseModel]:
    """Plausible ``research`` agent outputs for the demo pack (the same
    ones ``fcrm-agent run research --seed`` uses)."""
    return research_output(research_pack)


class FakeConsumed:
    """Just enough of a Mongo collection for the non-transactional dedupe
    path (mirrors ``tests/unit/packages/common/test_common_events.py``'s
    own double for :class:`~fcrm_common.events.Consumer`).
    """

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(
        self, query: dict[str, Any], projection: Optional[dict[str, Any]] = None
    ) -> Optional[dict[str, Any]]:
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def insert_one(
        self, doc: dict[str, Any], session: Optional[Any] = None
    ) -> None:
        from pymongo.errors import DuplicateKeyError

        if await self.find_one(
            {"message_id": doc["message_id"], "consumer": doc["consumer"]}
        ):
            raise DuplicateKeyError("dup")
        self.docs.append(doc)


class RecordingPublisher:
    """A ``fcrm_common.events.Publisher`` that records every publish."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, str, bytes, dict[str, Any]]] = []
        self.fail = fail

    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.sent.append((exchange, routing_key, body, headers))


class Acks:
    """Records ack/nack calls for a single delivery."""

    def __init__(self) -> None:
        self.acked = 0
        self.nacked: list[bool] = []

    async def ack(self) -> None:
        self.acked += 1

    async def nack(self, requeue: bool) -> None:
        self.nacked.append(requeue)


class RecordingAuditClient:
    """A fake :class:`~fcrm_agent_worker.audit_client.AuditClient` (same
    ``append`` method, no HTTP)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[Any] = []
        self.fail = fail

    async def append(self, record: Any) -> Any:
        if self.fail:
            msg = "audit service unavailable"
            raise ConnectionError(msg)
        self.records.append(record)
        return record


@pytest.fixture
def recording_publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def recording_audit() -> RecordingAuditClient:
    return RecordingAuditClient()


@pytest.fixture
def failing_audit() -> RecordingAuditClient:
    """An :class:`AuditClient` double standing in for S4 being down."""
    return RecordingAuditClient(fail=True)


@pytest.fixture
def fake_consumed() -> FakeConsumed:
    return FakeConsumed()


@pytest.fixture
def acks() -> Acks:
    return Acks()


@pytest.fixture
def research_spec() -> AgentSpec:
    return RESEARCH_SPEC


@pytest.fixture
def fake_tiers_config() -> dict[str, Any]:
    return FAKE_TIERS_CONFIG


@pytest.fixture
def make_model_factory_returning() -> Any:
    """Factory-as-fixture: avoids every test file needing ``from conftest
    import ...`` (fragile when two unit's test directories both have a
    plain ``conftest.py`` on ``sys.path`` under pytest's default import
    mode) for a plain helper function, not a fixture.
    """
    return model_factory_returning


@pytest.fixture
def make_model_factory_raising() -> Any:
    return model_factory_raising

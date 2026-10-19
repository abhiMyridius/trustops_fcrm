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

The job ledger, H2's outbox and the terminal transaction (ADR-79) are
in-memory too: :class:`FakeAgentJobs` (with a settable clock),
:class:`FakeAgentOutboxCollection` under a real
:class:`~fcrm_common.outbox.Outbox`, :class:`FakeH2Session` and
:func:`fake_transact`. A test builds a consumer with
``AgentJobConsumer(consumed, publisher, ..., **consumer_deps.kwargs)`` and
reads the terminal events it committed from ``consumer_deps.sent`` (the same
``(exchange, routing_key, body, headers)`` tuples a publisher would have seen)
or ``consumer_deps.events``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pytest
from fcrm_agent_worker.jobs import ClaimOutcome
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_agents.builders import builder_for
from fcrm_agents.context_pack import BuildRequest, ContextPack
from fcrm_agents.demo import research_output
from fcrm_agents.runtime import StaticPermitClient
from fcrm_agents.spec import REGISTRY, AgentSpec
from fcrm_common.events import build_envelope
from fcrm_common.outbox import Outbox
from fcrm_contracts.enums import AgentName
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import JobPayload
from fcrm_guardrails import PermitGuard
from fcrm_guardrails.opa_client import OPAClient
from fcrm_tools import DEMO_CASE_ID, DEMO_RUN_ID, ToolRegistry, build_seed_registry
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)
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
def failing_publisher() -> RecordingPublisher:
    """A publisher whose broker is down: every publish raises."""
    return RecordingPublisher(fail=True)


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


class SeenRequest(BaseModel):
    """What one model request carried: the attempt that made it, the user
    text, the output allowance in its settings and how many responses that
    attempt had already received."""

    attempt: int
    user_text: str
    max_tokens: Optional[int]
    round_trip: int


def model_factory_scripted(
    per_attempt: Sequence[Sequence[Any]], seen: list[SeenRequest]
) -> ModelFactory:
    """A ``ModelFactory`` whose model replies from a script and records every
    request: ``per_attempt[n]`` holds the replies of attempt ``n + 1``, one per
    request it makes (a tool-call round trip is one request; the last reply of
    an attempt repeats if the model is asked again). A reply is a callable
    ``(AgentInfo, messages) -> ModelResponse``.
    """
    calls = {"attempt": 0}

    def factory(models: Any, plan: Any) -> FunctionModel:
        calls["attempt"] += 1
        attempt = calls["attempt"]
        replies = list(per_attempt[min(attempt, len(per_attempt)) - 1])

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            responses = sum(1 for m in messages if isinstance(m, ModelResponse))
            text = "\n".join(
                str(part.content)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            )
            settings = info.model_settings or {}
            seen.append(
                SeenRequest(
                    attempt=attempt,
                    user_text=text,
                    max_tokens=settings.get("max_tokens"),
                    round_trip=responses,
                )
            )
            return replies[min(responses, len(replies) - 1)](info, messages)

        return FunctionModel(respond, model_name=f"scripted-{attempt}")

    return factory


@pytest.fixture
def make_model_factory_scripted() -> Any:
    """``model_factory_scripted`` as a fixture (see
    ``make_model_factory_returning`` for why)."""
    return model_factory_scripted


class FakeH2Session:
    """The bit of ``AsyncClientSession`` :class:`~fcrm_common.outbox.Outbox`
    checks: whether a transaction is open."""

    def __init__(self, in_transaction: bool = True) -> None:
        self.in_transaction = in_transaction


async def fake_transact[R](fn: Callable[[Any], Awaitable[R]]) -> R:
    """A ``Transact`` that runs ``fn`` once with an open fake session."""
    return await fn(FakeH2Session(in_transaction=True))


class _FakeH2Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self) -> list[dict[str, Any]]:
        return self._docs


class FakeAgentOutboxCollection:
    """The operations :class:`~fcrm_common.outbox.Outbox` and
    :class:`~fcrm_common.outbox.OutboxRelay` issue on ``agent_outbox`` (the
    pattern of ``test_common_outbox.py``'s fake, under its own name, D-45),
    plus the unique ``event_id`` index.
    """

    full_name = "fcrm_test.agent_outbox"

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.sessions: list[Any] = []

    async def insert_one(self, doc: dict[str, Any], session: Any = None) -> None:
        from pymongo.errors import DuplicateKeyError

        if any(d["event_id"] == doc["event_id"] for d in self.docs):
            raise DuplicateKeyError("event_id_unique")
        self.sessions.append(session)
        self.docs.append(doc)

    def _unpublished(self) -> list[dict[str, Any]]:
        return sorted(
            (d for d in self.docs if d["published_at"] is None), key=lambda d: d["_id"]
        )

    def find(self, query: Any, sort: Any = None, limit: int = 0) -> _FakeH2Cursor:
        docs = self._unpublished()
        return _FakeH2Cursor(docs[:limit] if limit else docs)

    async def find_one(
        self, query: Any, sort: Any = None, projection: Any = None
    ) -> Optional[dict[str, Any]]:
        docs = self._unpublished()
        return docs[0] if docs else None

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> None:
        for doc in self.docs:
            if doc["_id"] == query["_id"]:
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                for key, value in update.get("$inc", {}).items():
                    doc[key] = doc.get(key, 0) + value

    async def create_indexes(self, indexes: Any) -> list[str]:
        return []


class FakeAgentJobs:
    """An in-memory :class:`~fcrm_agent_worker.jobs.AgentJobStore` with a
    settable clock (``now``; :meth:`advance` moves it), keeping the same
    rules: a job is taken when missing or ``running`` past its lease, each
    claim has its own token, and ``mark_done`` / ``renew`` / ``release`` act
    only while that token still holds the job.
    """

    def __init__(self, holder: str = "h2_test") -> None:
        self.holder = holder
        self.now = datetime(2026, 10, 19, 12, 0, 0)
        self.docs: dict[str, dict[str, Any]] = {}
        self.claims: list[tuple[str, float, ClaimOutcome]] = []
        self.done_sessions: list[Any] = []
        self.released: list[str] = []
        self._tokens: dict[str, str] = {}
        self._issued = 0

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    async def ensure_indexes(self) -> list[str]:
        return []

    async def claim(
        self,
        job_id: str,
        ttl_s: float,
        *,
        case_id: str = "",
        run_id: str = "",
        agent: str = "",
    ) -> ClaimOutcome:
        doc = self.docs.get(job_id)
        if doc is not None and doc["status"] == "done":
            outcome = ClaimOutcome.DONE
        elif doc is not None and doc["lease_until"] >= self.now:
            outcome = ClaimOutcome.RUNNING
        else:
            self._issued += 1
            token = f"claim-{self._issued}"
            self.docs[job_id] = {
                "_id": job_id,
                "case_id": case_id,
                "run_id": run_id,
                "agent": agent,
                "status": "running",
                "holder": self.holder,
                "claim": token,
                "started_at": self.now,
                "lease_until": self.now + timedelta(seconds=ttl_s),
            }
            self._tokens[job_id] = token
            outcome = ClaimOutcome.CLAIMED
        self.claims.append((job_id, ttl_s, outcome))
        return outcome

    def _mine(self, job_id: str) -> Optional[dict[str, Any]]:
        doc = self.docs.get(job_id)
        token = self._tokens.get(job_id)
        if doc is None or token is None or doc["claim"] != token:
            return None
        return doc if doc["status"] == "running" else None

    async def renew(self, job_id: str, ttl_s: float) -> bool:
        doc = self._mine(job_id)
        if doc is None:
            return False
        doc["lease_until"] = self.now + timedelta(seconds=ttl_s)
        return True

    async def mark_done(
        self, job_id: str, *, outcome: str, event_id: str, session: Any
    ) -> bool:
        self.done_sessions.append(session)
        doc = self._mine(job_id)
        if doc is None:
            return False
        doc.update(
            status="done", finished_at=self.now, outcome=outcome, event_id=event_id
        )
        return True

    async def release(self, job_id: str) -> None:
        doc = self._mine(job_id)
        self.forget(job_id)
        if doc is not None:
            self.released.append(job_id)
            del self.docs[job_id]

    def forget(self, job_id: str) -> None:
        self._tokens.pop(job_id, None)


@dataclass
class ConsumerDeps:
    """What :class:`~fcrm_agent_worker.handler.AgentJobConsumer` needs beyond
    its pre-ADR-79 arguments, as fakes, and what they recorded."""

    jobs: FakeAgentJobs = field(default_factory=FakeAgentJobs)
    outbox_collection: FakeAgentOutboxCollection = field(
        default_factory=FakeAgentOutboxCollection
    )
    transact: Any = fake_transact

    @staticmethod
    def new_session() -> FakeH2Session:
        """A session with an open transaction, for a test's own transact."""
        return FakeH2Session(in_transaction=True)

    @property
    def outbox(self) -> Outbox:
        return Outbox(self.outbox_collection)  # type: ignore[arg-type]

    @property
    def kwargs(self) -> dict[str, Any]:
        """``transact``, ``jobs`` and ``outbox`` for the constructor."""
        return {"transact": self.transact, "jobs": self.jobs, "outbox": self.outbox}

    @property
    def sent(self) -> list[tuple[str, str, bytes, dict[str, Any]]]:
        """The committed terminal events, as publisher tuples."""
        return [
            (doc["exchange"], doc["routing_key"], doc["body"], doc["headers"])
            for doc in self.outbox_collection.docs
        ]

    @property
    def events(self) -> list[tuple[str, Envelope]]:
        """``(routing_key, envelope)`` per committed terminal event."""
        return [
            (routing_key, Envelope.model_validate_json(body))
            for _exchange, routing_key, body, _headers in self.sent
        ]


@pytest.fixture
def consumer_deps() -> ConsumerDeps:
    """Fresh job ledger, outbox and transaction fakes for one consumer."""
    return ConsumerDeps()


# --- the nine-agent matrices (test_live_runtime_scenarios, _adversarial) -----

#: The repository's ``ai/`` folder (the tier chains and pack caps the live
#: evals use), found from this file rather than the working directory.
AI_ROOT = Path(__file__).resolve().parents[4] / "ai"

#: The eval row each agent's base case is built from: it passes Guards 4 and 5
#: as it stands, covers the most output types and carries quotes.
BASE_ROWS: dict[AgentName, str] = {
    AgentName.SCREENING: "FCRM-2026-0072",
    AgentName.INTAKE: "FCRM-2026-0151",
    AgentName.RESEARCH: "FCRM-2026-0151",
    AgentName.ASSESSMENT: "FCRM-2026-0072",
    AgentName.CRITIC: "FCRM-2026-0151",
    AgentName.NARRATIVE: "FCRM-2026-0151",
    AgentName.EXAMINER: "FCRM-2026-0151~q1",
    AgentName.WATCHLIST: "list:v2026-10-fixture",
    AgentName.POLICY_GAP: "GAP-FCRM-2026-0151-1",
}

#: Items of output types no reference row covers, added to the base case (each
#: is checked against the pack first; the fixture fails if one stops passing).
EXTRA_ITEMS: dict[AgentName, list[dict[str, Any]]] = {
    AgentName.RESEARCH: [
        {
            "domain": "product",
            "description": "No clause in corpus v14 addresses cash-pickup agents.",
            "searched": {
                "query": "cash pickup agent network",
                "indexes": ["policy"],
                "hits": 0,
            },
        },
        {
            "jurisdiction": "MX",
            "note": "Mexico corridor needs jurisdiction screening at onboarding.",
            "source_ref": {"kind": "clause", "clause_id": "FCRM42-3.1", "corpus_v": 14},
            "quote": "new corridors require jurisdiction screening at onboarding",
            "confidence": 0.8,
        },
    ],
    AgentName.NARRATIVE: [
        {
            "missing": [],
            "stale": [],
            "contradictory": [],
            "questions_drafted": 0,
            "questions_kept": 0,
        },
        {
            "question_id": "Q-1",
            "anchored_to": "CTL-T01",
            "text": "When will the corridor vendor review be filed?",
        },
    ],
}


@dataclass
class AdversarialEnv:
    """What every job of the matrices runs against: the seed registry, the
    real tier chains, Guard 2 on the registry's allow-list, and the packs the
    live evals build (``fcrm_evals.live.RowPackBuilder``)."""

    registry: ToolRegistry
    tiers: dict[str, Any]
    permit: PermitGuard
    packs: Any


@dataclass
class AdversarialCase:
    """One agent's base case: an eval-row pack and items (JSON dicts) that
    pass Guards 4 and 5 against it."""

    agent: AgentName
    pack: ContextPack
    items: list[dict[str, Any]]


def _answering(items: list[dict[str, Any]]) -> ModelFactory:
    """A factory whose model answers ``items`` through the output tool."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        from pydantic_ai.messages import ToolCallPart

        tool = info.output_tools[0]
        properties = tool.parameters_json_schema.get("properties", {})
        key = next(iter(properties)) if len(properties) == 1 else "response"
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={key: items})]
        )

    def factory(models: Any, plan: Any) -> FunctionModel:
        return FunctionModel(respond, model_name="fake-base")

    return factory


async def _passes(env: AdversarialEnv, case: AdversarialCase) -> bool:
    from fcrm_agent_worker.live_runtime import run_agent_live
    from fcrm_agents.runtime import RunVerdict
    from fcrm_guardrails import CaseBudget

    result, _model, _usage = await run_agent_live(
        REGISTRY[case.agent],
        case.pack,
        f"base-{case.agent.value}",
        registry=env.registry,
        permit=env.permit,
        budget=CaseBudget(case.pack.case_id),
        instructions=None,
        tiers_config=env.tiers,
        model_factory=_answering(case.items),
    )
    return result.verdict is RunVerdict.PASS


async def _base_cases(env: AdversarialEnv) -> dict[AgentName, AdversarialCase]:
    from fcrm_evals.datasets import load

    cases: dict[AgentName, AdversarialCase] = {}
    for agent in AgentName:
        row = next(r for r in load(agent) if r.row_id == BASE_ROWS[agent])
        pack = await env.packs.build(row)
        by_type: dict[str, dict[str, Any]] = {}
        for ref in row.expected.reference_outputs:
            by_type.setdefault(ref.type, ref.data)
        case = AdversarialCase(
            agent, pack, [*by_type.values(), *EXTRA_ITEMS.get(agent, [])]
        )
        assert await _passes(env, case), f"{agent.value} base case no longer passes"
        cases[agent] = case
    return cases


@pytest.fixture(scope="session")
def adversarial_env() -> AdversarialEnv:
    """The seed registry and pins, the eval pack builder over the repository's
    ``ai/``, the real ``ai/models/tiers.yaml`` and Guard 2 on the registry's
    allow-list (the models are always fakes)."""
    from fcrm_agents.models import load_tiers_config
    from fcrm_evals.live import RowPackBuilder
    from fcrm_seed.loader import load_raw
    from fcrm_tools import seed_pins

    registry = build_seed_registry()
    return AdversarialEnv(
        registry=registry,
        tiers=load_tiers_config(AI_ROOT),
        permit=PermitGuard(StaticPermitClient.from_registry()),
        packs=RowPackBuilder.from_ai(registry, seed_pins(load_raw()), AI_ROOT),
    )


@pytest.fixture(scope="session")
def base_cases(adversarial_env: AdversarialEnv) -> dict[AgentName, AdversarialCase]:
    """Each agent's base case (:data:`BASE_ROWS` plus :data:`EXTRA_ITEMS`),
    built and checked to pass once per session. Built on a private event loop:
    a session fixture outlives every test's loop."""
    with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
        return runner.run(_base_cases(adversarial_env))

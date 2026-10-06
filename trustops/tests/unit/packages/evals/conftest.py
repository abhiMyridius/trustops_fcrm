"""Fixtures for the live-eval tests (``fcrm_evals.live``, ``live_run``, the
judge, the dashboard and the results files).

Lives beside this unit's tests, like the agent-worker's and caseflow's
(``tests/unit/workers/agent-worker/conftest.py``): the fakes here stand in for
Bedrock only. A ``FunctionModel`` answers an agent's real output tool with a
dataset row's own reference outputs (or with something deliberately wrong), so
the pack builder, the real guard chain, the budgets and the usage metering all
run exactly as they do live; only the network call is replaced. Nothing here
is used by the shipped path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import pytest
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_agents.models import load_tiers_config
from fcrm_contracts.enums import AgentName, Tier
from fcrm_evals.live import LiveProducer
from fcrm_evals.rows import EvalRow
from fcrm_evals.usage import PriceTable
from fcrm_guardrails.budget import TIER_BUDGETS
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

REPO_AI = Path(__file__).parents[4] / "ai"
CHEAP_BUDGET_TOKENS = TIER_BUDGETS[Tier.CHEAP].tokens
STAMP = datetime(2026, 10, 5, 14, 22, tzinfo=UTC)


@pytest.fixture(scope="session")
def repo_ai() -> Path:
    """The repository's ``ai/`` directory."""
    return REPO_AI


@pytest.fixture
def stamp() -> datetime:
    """A fixed result timestamp."""
    return STAMP


def _user_text(messages: Sequence[ModelMessage]) -> str:
    return "\n".join(
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )


def _marker(row: EvalRow) -> str:
    """What identifies ``row`` inside the user text of its pack."""
    if row.agent is AgentName.WATCHLIST:
        return f"Jurisdiction list {row.input['version']}"
    return f"Case {row.case_id} ·"


def _output_args(info: AgentInfo, payload: list[dict[str, Any]]) -> dict[str, Any]:
    properties = info.output_tools[0].parameters_json_schema.get("properties", {})
    key = next(iter(properties)) if len(properties) == 1 else "response"
    return {key: payload}


@pytest.fixture
def reference_factory() -> Callable[..., ModelFactory]:
    """A ``ModelFactory`` whose model answers each row with that row's own
    reference outputs (what ``EchoProducer`` returns), found by the agent's
    output tool and the case id in the pack text. ``tamper`` may rewrite the
    payload (for a blocked run)."""

    def build(
        rows: Sequence[EvalRow],
        *,
        tamper: Optional[Callable[[list[dict[str, Any]]], list[dict[str, Any]]]] = None,
    ) -> ModelFactory:
        payloads = {
            (f"{row.agent.value}_output", _marker(row)): [
                i.data for i in row.expected.reference_outputs
            ]
            for row in rows
        }

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            text = _user_text(messages)
            tool = info.output_tools[0].name
            payload = next(
                p
                for (name, marker), p in payloads.items()
                if name == tool and marker in text
            )
            args = _output_args(info, tamper(payload) if tamper else payload)
            return ModelResponse(
                parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)]
            )

        def factory(models: Any, plan: Any) -> FunctionModel:
            return FunctionModel(respond, model_name="fake-live-model")

        return factory

    return build


@pytest.fixture
def unresolved_source_tamper() -> Callable[
    [list[dict[str, Any]]], list[dict[str, Any]]
]:
    """Points every item's ``source_ref`` at a case the agent was never shown:
    Guard 4 refuses it on both attempts, so the run is blocked."""

    def tamper(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bad = {"kind": "case", "case_id": "FCRM-2026-9999"}
        out = []
        for original in payload:
            item = dict(original)
            if "source_ref" in item:
                item["source_ref"] = bad
            if "reasons" in item:
                item["reasons"] = [{**r, "source_ref": bad} for r in item["reasons"]]
            out.append(item)
        return out

    return tamper


@pytest.fixture(scope="session")
def tiers_config() -> Any:
    """The repository's real ``ai/models/tiers.yaml``, parsed."""
    return load_tiers_config(REPO_AI)


@pytest.fixture(scope="session")
def price_table(tiers_config: Any) -> PriceTable:
    """The price table of the real tiers file."""
    return PriceTable.from_tiers(tiers_config)


@pytest.fixture
def inflate_output() -> Callable[[list[dict[str, Any]]], list[dict[str, Any]]]:
    """Rewrites a screening payload so one reason is longer than the cheap tier's
    token budget a run: schema valid, but more tokens than it may spend."""

    def inflate(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        copy = deepcopy(payload)
        copy[0]["reasons"][0]["text"] = "word " * (CHEAP_BUDGET_TOKENS + 1_000)
        return copy

    return inflate


@pytest.fixture
def producer_for(
    reference_factory: Callable[..., ModelFactory],
) -> Callable[..., LiveProducer]:
    """``LiveProducer.for_agent`` over a fake model answering ``rows``."""

    def make(agent: AgentName, rows: Sequence[EvalRow], **kwargs: Any) -> LiveProducer:
        return LiveProducer.for_agent(
            agent,
            ai_root=REPO_AI,
            model_factory=reference_factory(rows, **kwargs),
            instructions="rendered",
        )

    return make


class FakeConverseClient:
    """The slice of aiobotocore's Bedrock Runtime client the judge uses:
    ``converse``. It records each request and answers from a queue."""

    def __init__(self, replies: Sequence[Any]) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    async def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return {
            "output": {"message": {"content": [{"text": reply}]}},
            "usage": {"inputTokens": 1000, "outputTokens": 200, "totalTokens": 1200},
            "stopReason": "end_turn",
        }


@pytest.fixture
def fake_converse() -> Callable[..., tuple[FakeConverseClient, Callable[..., Any]]]:
    """``(client, client_factory)``: a fake Bedrock client and the factory
    ``BedrockJudgeLLM`` takes in its place."""

    def build(*replies: Any) -> tuple[FakeConverseClient, Callable[..., Any]]:
        client = FakeConverseClient(replies)

        @asynccontextmanager
        async def factory() -> AsyncIterator[FakeConverseClient]:
            yield client

        return client, factory

    return build


@pytest.fixture
def results_root(tmp_path: Path) -> Path:
    """An empty ``evals/results``-shaped folder."""
    root = tmp_path / "results"
    root.mkdir()
    return root

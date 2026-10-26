"""``BEDROCK_REGION_FALLBACK``: a second Bedrock region as the last fallback of
every chain (``fcrm_agent_worker.live_runtime.model_factory_for``,
``fcrm_agents.models.bedrock_chain``).

Nothing here calls Bedrock: boto3 builds its client lazily and the providers
carry dummy keys. Model ids are fixtures; the real ones live in tiers.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fcrm_agent_worker import app as agent_app
from fcrm_agent_worker.live_runtime import (
    model_factory_for,
)
from fcrm_agent_worker.settings import AgentWorkerSettings
from fcrm_agents.model_limits import ModelLimitsModel
from fcrm_agents.models import TierModels, bedrock_chain, build_model, tier_models
from fcrm_contracts.enums import Tier
from fcrm_guardrails.budget import attempt_plan
from fcrm_tools import ToolRegistry
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.providers.bedrock import BedrockProvider

REPO_AI = Path(__file__).parents[4] / "ai"
TIERS: dict[str, Any] = {
    "tiers": {
        "strong": {
            "primary": "fixture-primary",
            "fallbacks": ["fixture-fallback-1", "fixture-fallback-2"],
            "max_tokens": 5000,
            "wall_clock_s": 180,
        },
    },
    "models": {},
}


def _provider(region: str) -> BedrockProvider:
    return BedrockProvider(
        region_name=region, aws_access_key_id="test", aws_secret_access_key="test"
    )


def _children(model: Any) -> list[BedrockConverseModel]:
    """The chain's Bedrock models, read through the limits wrapper (ADR-83)."""
    assert isinstance(model, FallbackModel)
    assert all(isinstance(m, ModelLimitsModel) for m in model.models)
    children = [
        m.wrapped for m in model.models if isinstance(m.wrapped, BedrockConverseModel)
    ]
    assert len(children) == len(model.models)
    return children


@pytest.fixture
def strong() -> TierModels:
    return tier_models(Tier.STRONG, TIERS)


def test_no_second_region_leaves_the_chain_as_it_was(strong: TierModels) -> None:
    chain = build_model(strong, provider=_provider("us-east-1"))

    assert [m.model_name for m in _children(chain)] == list(strong.chain)


def test_a_second_region_adds_the_first_model_last_in_that_region(
    strong: TierModels,
) -> None:
    first, second = _provider("us-east-1"), _provider("us-west-2")

    chain = build_model(strong, provider=first, region_fallback=second)

    children = _children(chain)
    assert [m.model_name for m in children] == [*strong.chain, "fixture-primary"]
    assert all(m.client is first.client for m in children[:-1])
    assert children[-1].client is second.client


def test_the_retry_chain_ends_in_its_own_first_model_never_the_rejected_one(
    strong: TierModels,
) -> None:
    """Attempt 2 runs the fallbacks alone so a Guard 4 retry never returns to the
    model whose output was rejected; the second region must keep that promise."""
    second = _provider("us-west-2")

    retry = bedrock_chain(
        strong,
        strong.fallbacks,
        provider=_provider("us-east-1"),
        region_fallback=second,
    )

    names = [m.model_name for m in _children(retry)]
    assert names == [*strong.fallbacks, "fixture-fallback-1"]
    assert strong.primary not in names


class _FakeShared:
    """A lazy shared provider over a dummy-key provider for ``region``."""

    def __init__(self, region: Any) -> None:
        self.region = region
        self.calls = 0

    def get(self) -> BedrockProvider:
        self.calls += 1
        return _provider(self.region or "us-east-1")


@pytest.mark.parametrize("region", [None, "", "  "])
def test_an_unset_region_adds_no_second_region(region: Any, strong: TierModels) -> None:
    built: list[Any] = []

    def provider_for(name: Any, pool: int) -> Any:
        built.append(name)
        return _FakeShared(name)

    factory = model_factory_for(region, "us-east-1", provider_for=provider_for)
    chain = factory(strong, attempt_plan(Tier.STRONG, 1))

    assert built == ["us-east-1"]
    assert [m.model_name for m in _children(chain)] == list(strong.chain)


def test_a_fallback_equal_to_the_primary_region_adds_nothing(
    strong: TierModels,
) -> None:
    built: list[Any] = []

    def provider_for(name: Any, pool: int) -> Any:
        built.append(name)
        return _FakeShared(name)

    factory = model_factory_for("us-east-1", "us-east-1", provider_for=provider_for)
    chain = factory(strong, attempt_plan(Tier.STRONG, 1))

    assert built == ["us-east-1"]
    assert [m.model_name for m in _children(chain)] == list(strong.chain)


def test_the_factory_builds_each_provider_once_and_adds_the_region_to_both_attempts(
    strong: TierModels,
) -> None:
    built: list[Any] = []

    def provider_for(name: Any, pool: int) -> Any:
        built.append((name, pool))
        return _FakeShared(name)

    factory = model_factory_for(
        "us-west-2", "us-east-1", max_pool_connections=4, provider_for=provider_for
    )
    first = factory(strong, attempt_plan(Tier.STRONG, 1))
    retry = factory(strong, attempt_plan(Tier.STRONG, 2))

    assert built == [("us-east-1", 4), ("us-west-2", 4)]
    assert [m.model_name for m in _children(first)][-1] == "fixture-primary"
    assert [m.model_name for m in _children(retry)][-1] == "fixture-fallback-1"
    assert len(_children(first)) == len(strong.chain) + 1
    assert len(_children(retry)) == len(strong.fallbacks) + 1


def _worker_settings(**extra: Any) -> AgentWorkerSettings:
    return AgentWorkerSettings(
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
        prompts_dir=str(REPO_AI),
        **extra,
    )


class _RecordingConsumer:
    """Stands in for ``AgentJobConsumer`` to capture what ``build_unit`` passes."""

    kwargs: dict[str, Any]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).kwargs = kwargs


def test_build_unit_asks_for_the_second_region_from_its_two_settings(
    monkeypatch: pytest.MonkeyPatch, seed_registry: ToolRegistry
) -> None:
    asked: list[tuple[Any, Any]] = []

    def fake_factory_for(region: Any, primary: Any, **kwargs: Any) -> Any:
        asked.append((region, primary))
        assert kwargs == {"max_pool_connections": 2}  # AGENT_PREFETCH's default
        return "the-factory"

    monkeypatch.setattr(agent_app, "model_factory_for", fake_factory_for)
    monkeypatch.setattr(agent_app, "AgentJobConsumer", _RecordingConsumer)

    agent_app.build_unit(
        _worker_settings(bedrock_region_fallback="us-west-2", aws_region="us-east-1"),
        mongo=MagicMock(),
        publisher=MagicMock(),
        registry=seed_registry,
    )

    assert asked == [("us-west-2", "us-east-1")]
    assert _RecordingConsumer.kwargs["model_factory"] == "the-factory"


def test_build_unit_keeps_a_factory_a_test_passes(
    monkeypatch: pytest.MonkeyPatch, seed_registry: ToolRegistry
) -> None:
    monkeypatch.setattr(agent_app, "AgentJobConsumer", _RecordingConsumer)
    mine = MagicMock()

    agent_app.build_unit(
        _worker_settings(bedrock_region_fallback="us-west-2"),
        mongo=MagicMock(),
        publisher=MagicMock(),
        registry=seed_registry,
        model_factory=mine,
    )

    assert _RecordingConsumer.kwargs["model_factory"] is mine

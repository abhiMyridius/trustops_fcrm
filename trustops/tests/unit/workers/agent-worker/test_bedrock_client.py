"""One Bedrock Runtime client per region, shared by every agent job (D-130).

Nothing here calls Bedrock: boto3 builds a client without a network call, and
the credentials are dummies. The environment is cleared of any AWS profile so
the developer's own config cannot change what is built.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fcrm_agent_worker.bedrock_client import (
    CONNECT_TIMEOUT_S,
    READ_TIMEOUT_S,
    SharedBedrockProvider,
    bedrock_runtime_client,
)
from fcrm_agent_worker.live_runtime import model_factory_for
from fcrm_agents.model_limits import ModelLimitsModel
from fcrm_agents.models import TierModels, tier_models
from fcrm_contracts.enums import Tier
from fcrm_guardrails.budget import attempt_plan
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.fallback import FallbackModel

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


@pytest.fixture(autouse=True)
def clean_aws_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_USE_DUALSTACK_ENDPOINT",
        "AWS_USE_FIPS_ENDPOINT",
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")


@pytest.fixture
def strong() -> TierModels:
    return tier_models(Tier.STRONG, TIERS)


def test_the_client_keeps_the_long_timeouts_and_sizes_its_pool() -> None:
    """boto3's own 60 s read timeout would cut off a 180 s strong-tier call."""
    client = bedrock_runtime_client("us-east-1", max_pool_connections=4)
    config = client.meta.config
    assert (config.read_timeout, config.connect_timeout) == (
        READ_TIMEOUT_S,
        CONNECT_TIMEOUT_S,
    )
    assert config.read_timeout >= 180
    assert config.max_pool_connections == 4
    assert client.meta.region_name == "us-east-1"


def test_a_bedrock_api_key_signs_with_the_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "fixture-token")
    client = bedrock_runtime_client("us-east-1", max_pool_connections=2)
    assert client.meta.config.signature_version == "bearer"
    assert client.meta.config.read_timeout == READ_TIMEOUT_S


def test_no_region_is_the_providers_own_error() -> None:
    with pytest.raises(UserError):
        bedrock_runtime_client(None, max_pool_connections=2)


def test_nothing_is_built_until_a_job_asks_and_then_only_once() -> None:
    built: list[tuple[Any, int]] = []

    def build(region: Any, *, max_pool_connections: int) -> Any:
        built.append((region, max_pool_connections))
        return bedrock_runtime_client(region, max_pool_connections=max_pool_connections)

    shared = SharedBedrockProvider("us-east-1", max_pool_connections=3, build=build)
    assert built == []
    first, second = shared.get(), shared.get()
    assert first is second
    assert built == [("us-east-1", 3)]


def test_a_missing_region_fails_the_job_not_the_startup_and_is_tried_again() -> None:
    shared = SharedBedrockProvider(None, max_pool_connections=2)  # builds nothing
    with pytest.raises(UserError):
        shared.get()
    with pytest.raises(UserError):
        shared.get()


def _clients(model: Any) -> list[Any]:
    assert isinstance(model, FallbackModel)
    assert all(isinstance(m, ModelLimitsModel) for m in model.models)
    return [m.wrapped.client for m in model.models]


def test_every_job_and_attempt_reuses_one_client_with_no_second_region(
    strong: TierModels,
) -> None:
    built: list[Any] = []

    def provider_for(region: Any, pool: int) -> SharedBedrockProvider:
        built.append(region)
        return SharedBedrockProvider(region, max_pool_connections=pool)

    factory = model_factory_for(
        None, "us-east-1", max_pool_connections=4, provider_for=provider_for
    )
    first = factory(strong, attempt_plan(Tier.STRONG, 1))
    again = factory(strong, attempt_plan(Tier.STRONG, 1))
    retry = factory(strong, attempt_plan(Tier.STRONG, 2))

    assert built == ["us-east-1"]
    clients = {id(c) for c in [*_clients(first), *_clients(again), *_clients(retry)]}
    assert len(clients) == 1
    assert _clients(first)[0].meta.config.max_pool_connections == 4


def test_a_second_region_has_its_own_shared_client(strong: TierModels) -> None:
    factory = model_factory_for("us-west-2", "us-east-1", max_pool_connections=2)
    first = factory(strong, attempt_plan(Tier.STRONG, 1))
    again = factory(strong, attempt_plan(Tier.STRONG, 1))

    regions = [c.meta.region_name for c in _clients(first)]
    assert regions == ["us-east-1"] * len(strong.chain) + ["us-west-2"]
    assert _clients(first)[-1] is _clients(again)[-1]
    assert _clients(first)[0] is _clients(again)[0]

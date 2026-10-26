"""Each model of a chain is held to its own token limits (ADR-83,
``fcrm_agents.model_limits``). No network call: the wrapped models are
``FunctionModel``s that record the ``max_tokens`` they were sent."""

from __future__ import annotations

from typing import Any, Optional

import pytest
from fcrm_agents.model_limits import (
    SAFE_OUTPUT_TOKENS,
    ModelLimitError,
    ModelLimits,
    ModelLimitsModel,
    is_token_limit_error,
    limit_named_in,
)
from fcrm_agents.models import bedrock_chain, tier_models
from fcrm_contracts.enums import Tier
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.settings import ModelSettings

MESSAGES: list[ModelMessage] = [ModelRequest.user_text_prompt("rate the controls")]


def _rejection(message: str, status: int = 400) -> ModelHTTPError:
    return ModelHTTPError(
        status,
        "fixture-model",
        body={"Error": {"Code": "ValidationException", "Message": message}},
    )


class Recorder:
    """A ``FunctionModel`` body that records each request's ``max_tokens`` and
    raises the scripted errors first."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.sent: list[Optional[int]] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        settings = info.model_settings or {}
        self.sent.append(settings.get("max_tokens"))
        if self.errors:
            raise self.errors.pop(0)
        return ModelResponse(parts=[TextPart("ok")])


async def _ask(model: Any, max_tokens: Optional[int] = 8000) -> ModelResponse:
    settings = ModelSettings(max_tokens=max_tokens) if max_tokens else None
    response: ModelResponse = await model.request(
        MESSAGES, settings, ModelRequestParameters()
    )
    return response


async def test_a_model_without_limits_is_sent_the_tiers_figure() -> None:
    body = Recorder()
    await _ask(ModelLimitsModel(FunctionModel(body)))
    assert body.sent == [8000]


async def test_max_tokens_is_lowered_to_the_models_output_cap() -> None:
    body = Recorder()
    model = ModelLimitsModel(FunctionModel(body), ModelLimits(max_output_tokens=5000))
    await _ask(model)
    assert body.sent == [5000]


async def test_a_cap_above_the_tiers_figure_changes_nothing() -> None:
    body = Recorder()
    model = ModelLimitsModel(FunctionModel(body), ModelLimits(max_output_tokens=64000))
    await _ask(model)
    assert body.sent == [8000]


async def test_max_tokens_is_lowered_to_what_the_context_window_leaves() -> None:
    body = Recorder()
    model = ModelLimitsModel(FunctionModel(body), ModelLimits(context_window=2000))
    await _ask(model)
    (sent,) = body.sent
    assert sent is not None
    assert 0 < sent < 2000


async def test_an_input_over_the_context_window_falls_to_the_next_model() -> None:
    small, large = Recorder(), Recorder()
    chain = FallbackModel(
        ModelLimitsModel(FunctionModel(small), ModelLimits(context_window=5)),
        ModelLimitsModel(FunctionModel(large)),
    )
    await _ask(chain)
    assert small.sent == []
    assert large.sent == [8000]


async def test_a_rejected_max_tokens_is_resent_once_at_the_limit_the_error_names() -> (
    None
):
    body = Recorder(
        _rejection("max_tokens: 8000 > 6000, which is the maximum allowed number")
    )
    model = ModelLimitsModel(FunctionModel(body))
    await _ask(model)
    assert body.sent == [8000, 6000]
    await _ask(model)
    assert body.sent[-1] == 6000, "the learned cap holds for the attempt"


async def test_a_rejection_that_names_no_limit_is_resent_at_the_safe_figure() -> None:
    body = Recorder(
        _rejection("The maximum tokens you requested exceeds the model limit")
    )
    await _ask(ModelLimitsModel(FunctionModel(body)))
    assert body.sent == [8000, SAFE_OUTPUT_TOKENS]


async def test_a_second_rejection_is_raised_so_the_chain_falls_through() -> None:
    error = _rejection("max_tokens: 8000 > 6000, which is the maximum allowed number")
    body = Recorder(error, _rejection("max_tokens exceeds the model limit"))
    with pytest.raises(ModelLimitError):
        await _ask(ModelLimitsModel(FunctionModel(body)))
    assert body.sent == [8000, 6000]


async def test_an_input_too_long_rejection_is_not_resent() -> None:
    body = Recorder(_rejection("Input is too long for requested model: 210000 tokens"))
    with pytest.raises(ModelLimitError):
        await _ask(ModelLimitsModel(FunctionModel(body)))
    assert body.sent == [8000]


async def test_another_error_passes_through_unchanged() -> None:
    error = _rejection("Too many requests, please wait", status=429)
    body = Recorder(error)
    with pytest.raises(ModelHTTPError) as raised:
        await _ask(ModelLimitsModel(FunctionModel(body)))
    assert raised.value is error
    assert body.sent == [8000]


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    [
        ("max_tokens: 70000 > 64000, which is the maximum allowed", 400, True),
        (
            "The maximum tokens you requested exceeds the model limit of 10000",
            400,
            True,
        ),
        ("Input is too long for requested model", 400, False),
        ("Input is too long: 210000 tokens exceeds the limit", 400, True),
        ("max_tokens: 70000 > 64000", 429, False),
        (
            "Malformed input: #/maxTokens: 12000 is not less or equal to 10000",
            400,
            True,
        ),
        ("Malformed input request: extraneous key", 400, False),
    ],
)
def test_token_limit_errors_are_told_from_others(
    message: str, status: int, expected: bool
) -> None:
    assert is_token_limit_error(_rejection(message, status)) is expected


def test_the_limit_named_is_the_largest_number_below_the_ask() -> None:
    assert limit_named_in("max_tokens: 70,000 > 64,000", 70000) == 64000
    assert limit_named_in("exceeds the model limit", 70000) is None


def test_tier_models_reads_each_models_limits_and_the_chain_is_wrapped() -> None:
    config: dict[str, Any] = {
        "tiers": {
            "judge": {
                "primary": "small-output",
                "fallbacks": ["unknown-limits"],
                "max_tokens": 8000,
                "wall_clock_s": 180,
            }
        },
        "models": {
            "small-output": {"max_output_tokens": 5000, "context_window": 300000},
            "unknown-limits": {},
        },
    }
    judge = tier_models(Tier.JUDGE, config)
    assert judge.model_limits["small-output"] == ModelLimits(
        max_output_tokens=5000, context_window=300000
    )
    assert judge.model_limits["unknown-limits"] == ModelLimits()
    provider = BedrockProvider(
        region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    chain = bedrock_chain(judge, judge.chain, provider=provider)
    assert all(isinstance(m, ModelLimitsModel) for m in chain.models)
    assert [m.limits.max_output_tokens for m in chain.models] == [5000, None]

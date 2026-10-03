"""Tests for token-based embedding input limits (plan §3.3).

Replaces the old char-based ``EMBEDDING_MAX_INPUT_CHARS``/
``resolve_embedding_max_input_chars`` scheme: embedding input is now
pre-checked in *tokens* against ``resolve_embedding_max_input_tokens`` and
never truncated — an oversized input raises ``EmbeddingInputTooLarge``
instead of being silently cut and logged as a warning.
"""

from __future__ import annotations

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.llm.client import (
    EmbeddingInputTooLarge,
    EmbeddingValidationError,
    LLMClient,
    resolve_embedding_max_input_tokens,
)


def test_resolve_known_model_returns_published_token_limit() -> None:
    assert resolve_embedding_max_input_tokens("amazon.titan-embed-text-v2:0") == 8192
    assert resolve_embedding_max_input_tokens("amazon.titan-embed-text-v1") == 8192


def test_resolve_unknown_model_falls_back_to_conservative_default() -> None:
    assert resolve_embedding_max_input_tokens("some-future-embedding-model") == 512


def test_resolve_explicit_override_always_wins() -> None:
    assert (
        resolve_embedding_max_input_tokens("amazon.titan-embed-text-v2:0", 1234) == 1234
    )
    assert resolve_embedding_max_input_tokens("some-future-embedding-model", 999) == 999


def test_llm_client_from_config_carries_the_token_limit_through(
    dummy_config: ContextConfig,
) -> None:
    """Regression guard: LLMClient.from_config(config) is the only path that
    wires embed_max_input_tokens/embed_dimension through — a bare
    LLMClient() falls back to dataclass defaults, discarding whatever
    ContextConfig resolved."""
    client = LLMClient.from_config(dummy_config)
    assert client.embed_model == dummy_config.embedding_model
    assert client.embed_dimension == dummy_config.embedding_dimension
    assert client.embed_max_input_tokens == dummy_config.embedding_max_input_tokens


@pytest.mark.asyncio
async def test_embed_raises_when_input_exceeds_token_limit_no_truncation() -> None:
    """An oversized input is never silently truncated — it raises."""
    client = LLMClient(
        embed_provider="dummy",
        embed_dimension=8,
        embed_max_input_tokens=5,
        embed_input_safety=1.0,
    )
    long_text = "word " * 500  # far more than 5 tokens
    with pytest.raises(EmbeddingInputTooLarge):
        await client.embed([long_text])


@pytest.mark.asyncio
async def test_embed_does_not_raise_when_under_the_token_limit() -> None:
    client = LLMClient(
        embed_provider="dummy",
        embed_dimension=8,
        embed_max_input_tokens=1000,
        embed_input_safety=1.0,
    )
    vectors = await client.embed(["a short text"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 8


@pytest.mark.asyncio
async def test_dummy_provider_returns_deterministic_unit_vectors() -> None:
    client = LLMClient(embed_provider="dummy", embed_dimension=8)
    v1 = await client.embed(["hello world"])
    v2 = await client.embed(["hello world"])
    v3 = await client.embed(["a different text"])

    assert v1 == v2, "dummy vectors must be deterministic for the same text"
    assert v1 != v3
    assert len(v1[0]) == 8
    norm = sum(x * x for x in v1[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-6, "dummy vectors must be unit-norm, never all-zero"


@pytest.mark.asyncio
async def test_embed_validates_dimension_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(
        embed_provider="bedrock",
        embed_model="amazon.titan-embed-text-v2:0",
        embed_dimension=8,
    )

    async def _fake_aembedding(model: str, input: list[str]):
        return {
            "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]
        }  # only 3 dims, not 8

    monkeypatch.setattr("evoq_context.llm.client.aembedding", _fake_aembedding)

    with pytest.raises(EmbeddingValidationError):
        await client.embed(["short text"])


@pytest.mark.asyncio
async def test_embed_validates_non_finite_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(
        embed_provider="bedrock",
        embed_model="amazon.titan-embed-text-v2:0",
        embed_dimension=3,
    )

    async def _fake_aembedding(model: str, input: list[str]):
        return {"data": [{"index": 0, "embedding": [float("nan"), 0.2, 0.3]}]}

    monkeypatch.setattr("evoq_context.llm.client.aembedding", _fake_aembedding)

    with pytest.raises(EmbeddingValidationError):
        await client.embed(["short text"])


@pytest.mark.asyncio
async def test_embed_validates_zero_norm_never_stored_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old zero-vector fallback is gone: a genuinely zero embedding from
    the backend must raise, never be treated as a valid result."""
    client = LLMClient(
        embed_provider="bedrock",
        embed_model="amazon.titan-embed-text-v2:0",
        embed_dimension=3,
    )

    async def _fake_aembedding(model: str, input: list[str]):
        return {"data": [{"index": 0, "embedding": [0.0, 0.0, 0.0]}]}

    monkeypatch.setattr("evoq_context.llm.client.aembedding", _fake_aembedding)

    with pytest.raises(EmbeddingValidationError):
        await client.embed(["short text"])


@pytest.mark.asyncio
async def test_embed_retries_on_throttling_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(
        embed_provider="bedrock",
        embed_model="amazon.titan-embed-text-v2:0",
        embed_dimension=3,
        embed_max_retries=2,
    )
    calls = {"n": 0}

    async def _flaky_aembedding(model: str, input: list[str]):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("ThrottlingException: Rate exceeded")
        return {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}

    monkeypatch.setattr("evoq_context.llm.client.aembedding", _flaky_aembedding)
    monkeypatch.setattr("evoq_context.llm.client.asyncio.sleep", lambda *_: _noop())

    vectors = await client.embed(["short text"])
    assert calls["n"] == 2
    assert vectors == [[0.1, 0.2, 0.3]]


@pytest.mark.asyncio
async def test_embed_does_not_retry_non_retryable_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(
        embed_provider="bedrock",
        embed_model="amazon.titan-embed-text-v2:0",
        embed_dimension=3,
    )
    calls = {"n": 0}

    async def _bad_request(model: str, input: list[str]):
        calls["n"] += 1
        raise RuntimeError("ValidationException: bad input")

    monkeypatch.setattr("evoq_context.llm.client.aembedding", _bad_request)

    with pytest.raises(RuntimeError):
        await client.embed(["short text"])
    assert calls["n"] == 1


async def _noop() -> None:
    return None


@pytest.mark.asyncio
async def test_embed_raises_on_short_batch_never_silently_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE #1 FIX: a provider returning fewer vectors than inputs must raise
    EmbeddingValidationError, not silently drop the tail of the batch. The
    old code did ``zip(structured, embeddings)`` downstream, which just
    stops at the shorter sequence with no error and a manifest that records
    the truncated count as if it were correct."""
    client = LLMClient(
        embed_provider="bedrock",
        embed_model="amazon.titan-embed-text-v2:0",
        embed_dimension=3,
        embed_batch_size=10,
    )

    async def _short_response(model: str, input: list[str]):
        # 3 inputs requested, only 2 vectors returned.
        return {
            "data": [
                {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                {"index": 1, "embedding": [0.4, 0.5, 0.6]},
            ]
        }

    monkeypatch.setattr("evoq_context.llm.client.aembedding", _short_response)

    with pytest.raises(EmbeddingValidationError, match=r"2 vectors for 3 inputs"):
        await client.embed(["a", "b", "c"])


@pytest.mark.asyncio
async def test_embed_short_batch_emits_guardrail_event_via_observer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evoq_context.core.observer import GuardrailEvent

    events: list[GuardrailEvent] = []

    class _RecordingObserver:
        def on_llm_call(self, record):
            return None

        def on_guardrail_event(self, event: GuardrailEvent) -> None:
            events.append(event)

    client = LLMClient(
        embed_provider="bedrock",
        embed_model="amazon.titan-embed-text-v2:0",
        embed_dimension=3,
    )

    async def _short_response(model: str, input: list[str]):
        return {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}

    monkeypatch.setattr("evoq_context.llm.client.aembedding", _short_response)

    with pytest.raises(EmbeddingValidationError):
        await client.embed(
            ["a", "b"], observer=_RecordingObserver(), caller="test-caller"
        )

    assert len(events) == 1
    assert events[0].name == "embedding_length_mismatch"
    assert events[0].outcome == "rejected"
    assert events[0].subject == "test-caller"


@pytest.mark.asyncio
async def test_embed_emits_llm_call_record_with_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evoq_context.core.observer import LlmCallRecord

    records: list[LlmCallRecord] = []

    class _RecordingObserver:
        def on_llm_call(self, record: LlmCallRecord) -> None:
            records.append(record)

        def on_guardrail_event(self, event) -> None:
            return None

    client = LLMClient(
        embed_provider="bedrock",
        embed_model="amazon.titan-embed-text-v2:0",
        embed_dimension=3,
    )

    async def _with_usage(model: str, input: list[str]):
        return {
            "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
            "usage": {"prompt_tokens": 7, "total_tokens": 7},
        }

    monkeypatch.setattr("evoq_context.llm.client.aembedding", _with_usage)

    await client.embed(["hello"], observer=_RecordingObserver(), caller="doc-1")

    assert len(records) == 1
    rec = records[0]
    assert rec.operation == "embed"
    assert rec.success is True
    assert rec.prompt_tokens == 7
    assert rec.completion_tokens == 0
    assert rec.total_tokens == 7
    assert rec.caller == "doc-1"
    assert rec.warning is None


@pytest.mark.asyncio
async def test_embed_missing_usage_records_zeros_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evoq_context.core.observer import LlmCallRecord

    records: list[LlmCallRecord] = []

    class _RecordingObserver:
        def on_llm_call(self, record: LlmCallRecord) -> None:
            records.append(record)

        def on_guardrail_event(self, event) -> None:
            return None

    client = LLMClient(
        embed_provider="bedrock",
        embed_model="amazon.titan-embed-text-v2:0",
        embed_dimension=3,
    )

    async def _no_usage(model: str, input: list[str]):
        return {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}

    monkeypatch.setattr("evoq_context.llm.client.aembedding", _no_usage)

    await client.embed(["hello"], observer=_RecordingObserver(), caller="doc-1")

    assert len(records) == 1
    assert records[0].prompt_tokens == 0
    assert records[0].total_tokens == 0
    # The call itself must NOT fail just because usage is unavailable.
    assert records[0].success is True
    assert records[0].warning == "usage_unavailable"


def test_litellm_model_id_has_no_provider_special_cases() -> None:
    from evoq_context.llm.client import _litellm_model_id

    assert (
        _litellm_model_id("bedrock", "amazon.titan-embed-text-v2:0")
        == "bedrock/amazon.titan-embed-text-v2:0"
    )
    assert (
        _litellm_model_id("openai", "text-embedding-3-small")
        == "openai/text-embedding-3-small"
    )
    assert _litellm_model_id("dummy", "dummy-model") == "dummy/dummy-model"
    # Already-qualified model ids pass through unchanged.
    assert (
        _litellm_model_id("bedrock", "bedrock/amazon.titan-embed-text-v2:0")
        == "bedrock/amazon.titan-embed-text-v2:0"
    )

"""LiteLLM-backed chat completions and embeddings — config-driven, observed.

Scope & functionality
----------------------
``LLMClient.from_config(config)`` builds from ``ContextConfig.embedding()``/
``.synthesis()`` (the typed views) rather than reading the environment —
this module has no import-time ``os.getenv`` of its own.

``LLMClient.embed``: each sub-batch is sent under
``asyncio.wait_for(aembedding(...), timeout=cfg.call_timeout_seconds)`` (an
embedding call could otherwise hang forever — the old code only timed out
``complete``); **``if len(vectors) != len(batch): raise
EmbeddingValidationError(...)``** — the single highest-value fix in the
whole engine: a provider that silently returns fewer vectors than inputs
used to have its stray chunks vanish from the index with the manifest still
recording the pre-truncation count as if nothing were wrong. Dimension/
finite/non-zero-norm validation happens **per batch**, immediately after
that batch returns, not after every batch in the document has round-tripped.

Retries go through ``llm/retry.py::classify`` (exception type first, a
strict word-boundary regex fallback second — never a bare ``"500"``
substring match) with jittered backoff (``retry.backoff_seconds``).

Every call — success or final failure — is reported as one
``LlmCallRecord`` via the optional ``observer``, built from
``response.usage`` when the provider supplies it; embedding responses often
carry ``prompt_tokens``/``total_tokens`` with no ``completion_tokens`` at
all, and a provider that omits ``usage`` entirely gets a record of zeros
tagged ``warning="usage_unavailable"`` — the call itself still succeeds.

The ``dummy`` provider returns deterministic sha256-derived unit vectors —
useful for offline tests and local runs without any credentials, and
structurally indistinguishable from a real embedding (finite, unit-norm,
fixed dimension). ``_litellm_model_id`` is ``f"{provider}/{model}"`` for
every provider — no Bedrock-specific special case.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from litellm import acompletion, aembedding

from evoq_context.chunking.tokens import get_estimator
from evoq_context.core import limits
from evoq_context.core.observer import ContextObserver, GuardrailEvent, LlmCallRecord, LlmOperation
from evoq_context.llm import retry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from evoq_context.core.config import ContextConfig

logger = logging.getLogger(__name__)

#: Published max input tokens per embedding model. Unrecognized models get
#: a conservative flat fallback rather than an assumed-safe large limit.
_EMBEDDING_MODEL_MAX_TOKENS: dict[str, int] = {
    "amazon.titan-embed-text-v2:0": 8192,
    "amazon.titan-embed-text-v1": 8192,
}
_DEFAULT_EMBEDDING_MAX_INPUT_TOKENS = 512


class EmbeddingInputTooLarge(RuntimeError):
    """A text exceeds the resolved max embedding input tokens. Never truncated."""


class EmbeddingValidationError(RuntimeError):
    """A returned embedding batch failed validation: wrong count, wrong
    dimension, non-finite values, or zero norm.
    """


def resolve_embedding_max_input_tokens(model: str, override: int = 0) -> int:
    """Titan v1/v2 -> 8192; unknown model -> 512; explicit override always wins."""
    if override and override > 0:
        return override
    return _EMBEDDING_MODEL_MAX_TOKENS.get(model, _DEFAULT_EMBEDDING_MAX_INPUT_TOKENS)


def _litellm_model_id(provider: str, model: str) -> str:
    """``f"{provider}/{model}"`` — no provider-specific special casing (e.g.
    no Bedrock-only branch). Already-qualified ids pass through unchanged.
    """
    if "/" in model:
        return model
    return f"{provider}/{model}"


def _deterministic_dummy_vector(text: str, dimension: int) -> list[float]:
    """A finite, unit-norm, fixed-dimension vector derived from ``text``'s
    sha256 digest — deterministic, never all-zero.
    """
    dim = dimension or 8
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    raw: list[int] = []
    counter = 0
    while len(raw) < dim:
        raw.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
        counter += 1
    values = [(b / 255.0) * 2 - 1 for b in raw[:dim]]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _validate_embeddings(vectors: list[list[float]], dimension: int) -> None:
    for v in vectors:
        if dimension and len(v) != dimension:
            raise EmbeddingValidationError(
                f"embedding has {len(v)} dimensions, expected {dimension}"
            )
        if not all(math.isfinite(x) for x in v):
            raise EmbeddingValidationError("embedding contains non-finite values")
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0.0:
            raise EmbeddingValidationError(
                "embedding has zero norm — the old zero-vector fallback is "
                "removed; a real embedding failure must raise, not be "
                "silently stored as a valid vector"
            )


def _embedding_vectors_from_response(resp: Any) -> list[list[float]]:
    if resp is None:
        return []
    if isinstance(resp, dict):
        items = resp.get("data", [])
    elif hasattr(resp, "data"):
        items = resp.data
    else:
        raise ValueError("Unexpected embedding response shape")

    def _idx(x: Any) -> int:
        if isinstance(x, dict):
            return int(x.get("index", 0))
        return int(getattr(x, "index", 0) or 0)

    sorted_items = sorted(items, key=_idx)
    out: list[list[float]] = []
    for item in sorted_items:
        if isinstance(item, dict):
            emb = item.get("embedding", [])
        else:
            emb = getattr(item, "embedding", None) or []
        out.append([float(v) for v in emb])
    return out


def _usage_value(usage: Any, key: str) -> int:
    if isinstance(usage, dict):
        return int(usage.get(key, 0) or 0)
    return int(getattr(usage, key, 0) or 0)


def _usage_from_response(resp: Any) -> tuple[int, int, int, bool]:
    """``(prompt_tokens, completion_tokens, total_tokens, usage_present)``.

    Embedding responses commonly carry ``prompt_tokens``/``total_tokens``
    with no ``completion_tokens`` key at all (there is no completion) —
    that's fine, it's read as ``0``. A response with no ``usage`` block at
    all returns ``usage_present=False`` so the caller can record it as a
    warning without failing the call.
    """
    usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
    if usage is None:
        return 0, 0, 0, False
    prompt = _usage_value(usage, "prompt_tokens")
    completion = _usage_value(usage, "completion_tokens")
    total = _usage_value(usage, "total_tokens") or (prompt + completion)
    return prompt, completion, total, True


@dataclass
class LLMClient:
    """Chat + embedding calls via LiteLLM; optional kwargs override defaults."""

    provider: str = "bedrock"
    model: str = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    max_tokens: int = 15000
    temperature: float = 0.2
    llm_call_timeout_seconds: int = limits.DEFAULT_LLM_CALL_TIMEOUT_SECONDS
    max_output_tokens_cap: int = limits.DEFAULT_LLM_MAX_OUTPUT_TOKENS_CAP
    embed_provider: str = field(default="bedrock")
    embed_model: str = field(default="amazon.titan-embed-text-v2:0")
    embed_dimension: int = 0
    embed_max_input_tokens: int = 0  # 0 = resolve dynamically from model
    embed_input_safety: float = 0.85
    embed_batch_size: int = 16
    embed_max_retries: int = 4
    embed_call_timeout_seconds: int = limits.DEFAULT_EMBEDDING_CALL_TIMEOUT_SECONDS

    @classmethod
    def from_config(cls, config: ContextConfig) -> LLMClient:
        """Build from the typed ``EmbeddingConfig``/``SynthesisConfig`` views
        (``ContextConfig.embedding()``/``.synthesis()``) — the engine never
        reads the environment itself.
        """
        emb = config.embedding()
        syn = config.synthesis()
        return cls(
            provider=syn.synthesis_provider,
            model=syn.synthesis_model,
            max_tokens=syn.llm_max_tokens,
            llm_call_timeout_seconds=syn.llm_call_timeout_seconds,
            max_output_tokens_cap=syn.max_output_tokens_cap,
            embed_provider=emb.provider,
            embed_model=emb.model,
            embed_dimension=emb.dimension,
            embed_max_input_tokens=emb.max_input_tokens,
            embed_input_safety=emb.input_safety,
            embed_batch_size=emb.batch_size,
            embed_max_retries=emb.max_retries,
            embed_call_timeout_seconds=emb.call_timeout_seconds,
        )

    async def complete(
        self,
        user_message: str,
        system_message: str | None = None,
        *,
        model: str | None = None,
        provider: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
        observer: ContextObserver | None = None,
        caller: str = "unknown",
    ) -> str:
        """Single chat completion; ``None`` overrides fall back to instance attrs."""
        mid = _litellm_model_id(
            self.provider if provider is None else provider,
            self.model if model is None else model,
        )
        eff_max = self.max_tokens if max_tokens is None else max_tokens
        eff_temp = self.temperature if temperature is None else temperature
        eff_provider = self.provider if provider is None else provider
        eff_timeout = timeout if timeout is not None else self.llm_call_timeout_seconds
        if eff_provider == "bedrock" and eff_max > self.max_output_tokens_cap:
            logger.warning(
                "Capping max_tokens from %s to %s for Bedrock provider",
                eff_max, self.max_output_tokens_cap,
            )
            eff_max = self.max_output_tokens_cap
        messages: list[dict[str, str]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": user_message})

        t0 = time.monotonic()
        logger.debug(
            "llm_call_start model=%s max_tokens=%d input_chars=%d timeout=%ds",
            mid, eff_max, len(user_message), eff_timeout,
        )
        try:
            response = await asyncio.wait_for(
                acompletion(model=mid, messages=messages, max_tokens=eff_max, temperature=eff_temp),
                timeout=eff_timeout,
            )
        except TimeoutError:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.error(
                "llm_call_timeout model=%s elapsed_ms=%d timeout=%ds input_chars=%d",
                mid, elapsed_ms, eff_timeout, len(user_message),
            )
            self._emit_call_record(
                observer, operation="complete", mid=mid, prompt_tokens=0,
                completion_tokens=0, total_tokens=0, latency_ms=elapsed_ms,
                attempt=1, retries=0, success=False, error_code="timeout", caller=caller,
            )
            raise TimeoutError(f"LLM call to {mid} timed out after {eff_timeout}s") from None
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            self._emit_call_record(
                observer, operation="complete", mid=mid, prompt_tokens=0,
                completion_tokens=0, total_tokens=0, latency_ms=elapsed_ms,
                attempt=1, retries=0, success=False, error_code=type(exc).__name__, caller=caller,
            )
            raise

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        prompt_tokens, completion_tokens, total_tokens, usage_present = _usage_from_response(response)
        logger.debug(
            "llm_call_done model=%s elapsed_ms=%d output_chars=%d", mid, elapsed_ms, len(content),
        )
        self._emit_call_record(
            observer, operation="complete", mid=mid, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens, total_tokens=total_tokens,
            latency_ms=elapsed_ms, attempt=1, retries=0, success=True,
            caller=caller, warning=None if usage_present else "usage_unavailable",
        )
        return content

    async def _embed_one_batch(
        self, mid: str, batch: list[str], *, observer: ContextObserver | None, caller: str
    ) -> list[list[float]]:
        """Embed exactly one sub-batch: timeout, length-check, per-batch
        validation, retry-with-jitter, one ``LlmCallRecord`` for the batch.
        """
        attempt = 0
        t0 = time.monotonic()
        while True:
            attempt += 1
            try:
                resp = await asyncio.wait_for(
                    aembedding(model=mid, input=batch), timeout=self.embed_call_timeout_seconds,
                )
            except TimeoutError as exc:
                if attempt > self.embed_max_retries:
                    self._emit_call_record(
                        observer, operation="embed", mid=mid, prompt_tokens=0,
                        completion_tokens=0, total_tokens=0,
                        latency_ms=int((time.monotonic() - t0) * 1000), attempt=attempt,
                        retries=attempt - 1, success=False, error_code="timeout", caller=caller,
                    )
                    raise TimeoutError(
                        f"embedding call to {mid} timed out after "
                        f"{self.embed_call_timeout_seconds}s"
                    ) from exc
                backoff = retry.backoff_seconds(attempt)
                logger.warning(
                    "embed batch timed out (attempt %d/%d) — retrying in %.1fs",
                    attempt, self.embed_max_retries, backoff,
                )
                await asyncio.sleep(backoff)
                continue
            except Exception as exc:
                classification = retry.classify(exc)
                if classification != "retryable" or attempt > self.embed_max_retries:
                    self._emit_call_record(
                        observer, operation="embed", mid=mid, prompt_tokens=0,
                        completion_tokens=0, total_tokens=0,
                        latency_ms=int((time.monotonic() - t0) * 1000), attempt=attempt,
                        retries=attempt - 1, success=False, error_code=type(exc).__name__,
                        caller=caller,
                    )
                    raise
                backoff = retry.backoff_seconds(attempt)
                logger.warning(
                    "embed batch failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt, self.embed_max_retries, exc, backoff,
                )
                await asyncio.sleep(backoff)
                continue

            vectors = _embedding_vectors_from_response(resp)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if len(vectors) != len(batch):
                # THE #1 FIX: never silently truncate. A provider returning
                # fewer vectors than inputs used to leave the tail of the
                # batch missing from the index with no error and a manifest
                # that recorded the truncated count as if it were correct.
                mismatch = EmbeddingValidationError(
                    f"{len(vectors)} vectors for {len(batch)} inputs"
                )
                if observer is not None:
                    observer.on_guardrail_event(
                        GuardrailEvent(
                            name="embedding_length_mismatch",
                            subject=caller,
                            outcome="rejected",
                            detail=str(mismatch),
                        )
                    )
                self._emit_call_record(
                    observer, operation="embed", mid=mid, prompt_tokens=0,
                    completion_tokens=0, total_tokens=0, latency_ms=elapsed_ms,
                    attempt=attempt, retries=attempt - 1, success=False,
                    error_code="length_mismatch", caller=caller,
                )
                raise mismatch

            # Validate THIS batch immediately — fail fast, not after every
            # batch in the document has round-tripped.
            try:
                _validate_embeddings(vectors, self.embed_dimension)
            except EmbeddingValidationError:
                self._emit_call_record(
                    observer, operation="embed", mid=mid, prompt_tokens=0,
                    completion_tokens=0, total_tokens=0, latency_ms=elapsed_ms,
                    attempt=attempt, retries=attempt - 1, success=False,
                    error_code="validation_failed", caller=caller,
                )
                raise

            prompt_tokens, completion_tokens, total_tokens, usage_present = _usage_from_response(resp)
            self._emit_call_record(
                observer, operation="embed", mid=mid, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, total_tokens=total_tokens,
                latency_ms=elapsed_ms, attempt=attempt, retries=attempt - 1, success=True,
                caller=caller, warning=None if usage_present else "usage_unavailable",
            )
            return vectors

    def _emit_call_record(
        self,
        observer: ContextObserver | None,
        *,
        operation: LlmOperation,
        mid: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        latency_ms: int,
        attempt: int,
        retries: int,
        success: bool,
        caller: str,
        error_code: str | None = None,
        warning: str | None = None,
        doc_id: str | None = None,
    ) -> None:
        if observer is None:
            return
        provider, _, model = mid.partition("/")
        if warning == "usage_unavailable":
            logger.warning(
                "llm usage unavailable operation=%s provider=%s model=%s caller=%s",
                operation, provider, model or mid, caller,
            )
        observer.on_llm_call(
            LlmCallRecord(
                operation=operation,
                provider=provider,
                model=model or mid,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                attempt=attempt,
                retries=retries,
                success=success,
                caller=caller,
                error_code=error_code,
                doc_id=doc_id,
                warning=warning,
            )
        )

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        provider: str | None = None,
        observer: ContextObserver | None = None,
        caller: str = "unknown",
    ) -> list[list[float]]:
        """Embed texts; ``None`` overrides fall back to ``embed_*`` instance attrs.

        Raises :class:`EmbeddingInputTooLarge` when any text exceeds the
        resolved max input tokens (no truncation — chunking is responsible
        for keeping every chunk under this limit) and
        :class:`EmbeddingValidationError` when a batch returns the wrong
        vector count or a vector fails validation. Never returns a zero
        vector, and never returns fewer vectors than texts, as a stand-in
        for failure.
        """
        if not texts:
            return []
        eff_provider = self.embed_provider if provider is None else provider
        eff_model = self.embed_model if model is None else model

        max_input_tokens = int(
            resolve_embedding_max_input_tokens(eff_model, self.embed_max_input_tokens)
            * self.embed_input_safety
        )
        estimator = get_estimator("tiktoken")
        oversized = [t for t in texts if estimator.count(t) > max_input_tokens]
        if oversized:
            raise EmbeddingInputTooLarge(
                f"{len(oversized)}/{len(texts)} text(s) exceed the "
                f"{max_input_tokens}-token embedding input limit for "
                f"model={eff_model!r} — chunking must keep every chunk "
                "under this limit; embedding never truncates."
            )

        if eff_provider == "dummy":
            vectors = [_deterministic_dummy_vector(t, self.embed_dimension) for t in texts]
            self._emit_call_record(
                observer, operation="embed", mid=f"dummy/{eff_model}", prompt_tokens=0,
                completion_tokens=0, total_tokens=0, latency_ms=0, attempt=1, retries=0,
                success=True, caller=caller,
            )
            return vectors

        mid = _litellm_model_id(eff_provider, eff_model)
        out: list[list[float]] = []
        for start in range(0, len(texts), max(1, self.embed_batch_size)):
            batch = texts[start : start + max(1, self.embed_batch_size)]
            out.extend(await self._embed_one_batch(mid, batch, observer=observer, caller=caller))

        if len(out) != len(texts):
            # Belt-and-braces: the per-batch check above already guarantees
            # this, but a total mismatch must never slip through silently.
            raise EmbeddingValidationError(f"{len(out)} vectors for {len(texts)} inputs")
        return out

"""``evoq_context/llm/retry.py`` — exception-type classification, the
"1500 dimensions" false-positive regression, and jittered backoff bounds."""

from __future__ import annotations

import random

import pytest
from botocore.exceptions import ClientError
from evoq_context.llm.retry import backoff_seconds, classify
from litellm.exceptions import (
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

# ---------------------------------------------------------------------------
# The #2 fix: no bare "500"/"503" substring check.
# ---------------------------------------------------------------------------


def test_dimension_mismatch_message_is_not_retryable() -> None:
    """A permanent validation error whose message happens to contain a
    3-digit run that looks like an HTTP status must never be retried."""
    exc = RuntimeError("embedding has 1500 dimensions, expected 1024")
    assert classify(exc) == "permanent"


def test_request_id_containing_500_is_not_retryable() -> None:
    exc = RuntimeError("request failed, request-id=abc-1500-xyz")
    assert classify(exc) == "permanent"


def test_line_number_containing_503_is_not_retryable() -> None:
    exc = ValueError("parse error at line 1503 column 12")
    assert classify(exc) == "permanent"


# ---------------------------------------------------------------------------
# Typed exception classification (checked before any string inspection).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        RateLimitError(message="rate limited", llm_provider="bedrock", model="m"),
        ServiceUnavailableError(
            message="unavailable", llm_provider="bedrock", model="m"
        ),
        APIConnectionError(message="conn refused", llm_provider="bedrock", model="m"),
        Timeout(message="timed out", model="m", llm_provider="bedrock"),
        InternalServerError(message="500", llm_provider="bedrock", model="m"),
    ],
)
def test_litellm_typed_exceptions_are_retryable(exc: BaseException) -> None:
    assert classify(exc) == "retryable"


def test_asyncio_timeout_error_is_retryable() -> None:
    assert classify(TimeoutError("timed out")) == "retryable"


@pytest.mark.parametrize(
    "code",
    [
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelTimeoutException",
    ],
)
def test_botocore_client_error_retryable_codes(code: str) -> None:
    exc = ClientError({"Error": {"Code": code, "Message": "x"}}, "PutVectors")
    assert classify(exc) == "retryable"


def test_botocore_client_error_unrecognized_code_is_permanent() -> None:
    """A ClientError WITH a real code that isn't in the known-transient set
    is permanent — the typed code is authoritative, no string fallback."""
    exc = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad input"}}, "PutVectors"
    )
    assert classify(exc) == "permanent"


def test_botocore_client_error_whose_message_contains_500_stays_permanent() -> None:
    exc = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "dimension 1500 invalid"}},
        "PutVectors",
    )
    assert classify(exc) == "permanent"


# ---------------------------------------------------------------------------
# String fallback (untyped exceptions only) — strict word boundaries.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "ThrottlingException: Rate exceeded",
        "TooManyRequests: slow down",
        "TooManyRequestsException occurred",
        "ServiceUnavailable: try again",
        "ServiceUnavailableException",
        "ModelTimeoutException while invoking model",
        "RequestTimeout waiting for upstream",
    ],
)
def test_untyped_exception_word_boundary_fallback_retryable(message: str) -> None:
    assert classify(RuntimeError(message)) == "retryable"


@pytest.mark.parametrize(
    "message",
    [
        "ValidationException: bad input",
        "AccessDeniedException",
        "embedding has 1500 dimensions, expected 1024",
        "a plain runtime error with no known marker",
    ],
)
def test_untyped_exception_no_match_is_permanent(message: str) -> None:
    assert classify(RuntimeError(message)) == "permanent"


def test_fallback_does_not_match_substrings_of_other_words() -> None:
    """The regex is word-bounded — a word that merely CONTAINS one of the
    known tokens as a substring must not match."""
    assert classify(RuntimeError("NotAThrottlingExceptionAtAll")) == "permanent"


# ---------------------------------------------------------------------------
# Jittered backoff bounds.
# ---------------------------------------------------------------------------


def test_backoff_seconds_within_jitter_bounds() -> None:
    rng = random.Random(42)
    for attempt in range(1, 8):
        value = backoff_seconds(attempt, rng=rng)
        base = min(30.0, 2**attempt)
        assert base * 0.5 <= value <= base * 1.5


def test_backoff_seconds_capped_at_30_before_jitter() -> None:
    rng = random.Random(7)
    value = backoff_seconds(10, rng=rng)  # 2**10 = 1024, capped to 30
    assert 15.0 <= value <= 45.0


def test_backoff_seconds_is_jittered_not_identical_across_calls() -> None:
    """Concurrent workers retrying the same attempt number must not all
    sleep for the exact same duration (that's the thundering-herd bug)."""
    values = {backoff_seconds(3) for _ in range(20)}
    assert len(values) > 1

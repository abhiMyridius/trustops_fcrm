r"""Retry classification and jittered backoff for LLM calls.

Scope & functionality
----------------------
``classify(exc)`` decides whether one failed LLM/embedding call is worth
retrying. It checks the exception's **type** first — LiteLLM's own
``RateLimitError``/``ServiceUnavailableError``/``APIConnectionError``/
``Timeout``/``InternalServerError``, a botocore ``ClientError`` whose
``response["Error"]["Code"]`` is a known-transient AWS code, or
``asyncio.TimeoutError`` — and only falls back to inspecting the exception
*text* for an untyped/string exception (e.g. one re-raised across a process
boundary that lost its class). That fallback is a **strict word-boundary
regex**, never a bare substring check: the old ``"500" in str(exc)`` scheme
retried a permanent error like "embedding has 1500 dimensions, expected
1024" for five attempts with a 30s backoff because the digits happened to
contain "500". ``\\b(...)\\b`` cannot match a digit run.

``backoff_seconds`` is ``min(cap, 2**attempt) * uniform(0.5, 1.5)`` — the
jitter matters as much as the exponent: without it, N concurrent workers
that all get throttled at once retry in lockstep and re-throttle each
other on every attempt.
"""

from __future__ import annotations

import asyncio
import random
import re
from typing import Literal

RetryClass = Literal["retryable", "permanent"]

# --- exception-type classification -----------------------------------------

try:  # pragma: no cover - exercised via classify(); import shape is what matters
    from litellm.exceptions import (
        APIConnectionError,
        InternalServerError,
        RateLimitError,
        ServiceUnavailableError,
        Timeout,
    )

    _LITELLM_RETRYABLE: tuple[type[BaseException], ...] = (
        RateLimitError,
        ServiceUnavailableError,
        APIConnectionError,
        Timeout,
        InternalServerError,
    )
except Exception:  # pragma: no cover - litellm is a hard dependency in prod
    _LITELLM_RETRYABLE = ()

try:  # pragma: no cover
    from botocore.exceptions import ClientError as _BotoClientError  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - aioboto3/botocore optional in some envs
    _BotoClientError = None

#: AWS error codes that mean "try again", not "your request is wrong".
BOTO_RETRYABLE_CODES: frozenset[str] = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelTimeoutException",
    }
)

# --- string fallback (untyped/re-raised exceptions ONLY) --------------------

#: Word-boundary match on the known-transient names only. Deliberately NOT a
#: bare "500"/"503" substring check — those false-positive on any message
#: that happens to contain those digits (a dimension count, a request id, a
#: line number). A permanent validation error must never be retried.
_RETRYABLE_WORD_RE = re.compile(
    r"\b(ThrottlingException|TooManyRequests(?:Exception)?|"
    r"ServiceUnavailable(?:Exception)?|ModelTimeoutException|RequestTimeout)\b"
)


def classify(exc: BaseException) -> RetryClass:
    """Classify one failure as ``"retryable"`` or ``"permanent"``.

    Order: (1) LiteLLM exception types, (2) ``asyncio.TimeoutError``,
    (3) botocore ``ClientError`` by ``response["Error"]["Code"]`` (a
    ``ClientError`` with an unrecognized code is treated as permanent —
    it has a real code, so the string fallback is not consulted),
    (4) the strict word-boundary regex fallback for everything else.
    """
    if _LITELLM_RETRYABLE and isinstance(exc, _LITELLM_RETRYABLE):
        return "retryable"
    if isinstance(exc, asyncio.TimeoutError):
        return "retryable"
    if _BotoClientError is not None and isinstance(exc, _BotoClientError):
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str(response.get("Error", {}).get("Code", ""))
        return "retryable" if code in BOTO_RETRYABLE_CODES else "permanent"
    if _RETRYABLE_WORD_RE.search(str(exc)):
        return "retryable"
    return "permanent"


def backoff_seconds(attempt: int, *, cap: float = 30.0, rng: random.Random | None = None) -> float:
    """Jittered exponential backoff: ``min(cap, 2**attempt) * uniform(0.5, 1.5)``.

    ``attempt`` is 1-based (the first retry passes ``1``). The jitter keeps
    concurrent workers that were throttled together from retrying in
    lockstep and re-throttling each other.
    """
    base = min(cap, float(2 ** max(0, attempt)))
    jitter: float = rng.uniform(0.5, 1.5) if rng is not None else random.uniform(0.5, 1.5)
    return base * jitter

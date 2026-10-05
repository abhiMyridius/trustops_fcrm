"""Service-to-service HTTP client (BUILD-PLAN §3.9, design A §1.4, §10.3).

:class:`ServiceClient` wraps one pooled ``httpx.AsyncClient`` per peer
service and adds, on every request:

- timeouts: connect 2 s, read 10 s by default (``/score`` and ``/packs``
  callers pass their own);
- a service-account bearer token (:func:`fcrm_common.auth.mint_service_token`,
  HS256 with ``SERVICE_TOKEN_SECRET``, role ``fcrm_service``, 5-minute
  TTL, re-minted 30 s before expiry), or a custom async token provider;
- W3C ``traceparent``/``tracestate`` from the current OTel context (the
  HTTPX auto-instrumentation, when on, overwrites them with its own client
  span's context, which is the more precise parent);
- ``Idempotency-Key`` when the caller gives one; the same key is reused
  on every retry of that call;
- retries (design A §10.3), written as a small loop rather than a
  dependency on tenacity: up to 2 retries with 0.2 s then 0.6 s backoff,
  on connection failures for any method (the request never reached the
  peer), and on 502/503/504 only for idempotent requests — GET, HEAD,
  OPTIONS, PUT, DELETE, or any method carrying an ``Idempotency-Key``.
  A 500 is not retried (it is usually deterministic) and read timeouts are
  not retried (the peer may have acted; waiting longer multiplies latency).

:meth:`ServiceClient.request` returns the raw response;
:meth:`ServiceClient.request_json` raises the matching
:mod:`fcrm_common.errors` class for a 4xx/5xx so a caller's handler maps
it straight back to problem+json.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from types import TracebackType
from typing import Any, Optional, Self

import httpx
from opentelemetry import propagate
from pydantic import SecretStr

from fcrm_common.auth import mint_service_token
from fcrm_common.errors import (
    ConflictError,
    FcrmError,
    ForbiddenError,
    NotFoundError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    ValidationError,
)

logger = logging.getLogger(__name__)

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
RETRY_STATUSES = frozenset({502, 503, 504})
DEFAULT_BACKOFF_S: tuple[float, ...] = (0.2, 0.6)
TOKEN_RENEW_BEFORE_S = 30.0

TokenProvider = Callable[[], Awaitable[str]]
SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Timeouts:
    """Per-phase timeouts in seconds."""

    connect: float = 2.0
    read: float = 10.0
    write: float = 10.0
    pool: float = 5.0

    def to_httpx(self) -> httpx.Timeout:
        """As an ``httpx.Timeout``."""
        return httpx.Timeout(
            connect=self.connect, read=self.read, write=self.write, pool=self.pool
        )


class _ServiceTokenCache:
    """Mints the HS256 service token and reuses it until close to expiry."""

    def __init__(self, secret: str | SecretStr, service_name: str, ttl_s: int) -> None:
        self._secret = secret
        self._service_name = service_name
        self._ttl_s = ttl_s
        self._token: Optional[str] = None
        self._renew_at = 0.0

    async def __call__(self) -> str:
        """A token valid for at least ~30 more seconds."""
        now = monotonic()
        if self._token is None or now >= self._renew_at:
            self._token = mint_service_token(
                self._secret, self._service_name, ttl_s=self._ttl_s
            )
            self._renew_at = now + max(1.0, self._ttl_s - TOKEN_RENEW_BEFORE_S)
        return self._token


class ServiceClient:
    """HTTP client for one peer service.

    Args:
        base_url: The peer's base URL (``CASE_SERVICE_URL`` ...).
        dependency: Peer name used in errors, logs and metrics (``case``).
        caller: This unit's name, the service token's subject.
        token_secret: ``SERVICE_TOKEN_SECRET``; ``None`` sends no token
            unless ``token_provider`` is given.
        token_provider: Async callable returning a bearer token, overriding
            the HS256 token (e.g. an identity-minted service token).
        timeouts: Default timeouts (connect 2 s, read 10 s).
        retries: Extra attempts (default 2).
        backoff_s: Sleep before retry *n* (the last value repeats).
        transport: Custom ``httpx`` transport (tests use ``MockTransport``).
        sleep: Injected sleep, for tests.
    """

    def __init__(
        self,
        base_url: str,
        *,
        dependency: str,
        caller: str = "fcrm-service",
        token_secret: Optional[str | SecretStr] = None,
        token_provider: Optional[TokenProvider] = None,
        timeouts: Optional[Timeouts] = None,
        retries: int = 2,
        backoff_s: Sequence[float] = DEFAULT_BACKOFF_S,
        token_ttl_s: int = 300,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self.dependency = dependency
        self._retries = retries
        self._backoff_s = tuple(backoff_s) or (0.0,)
        self._sleep = sleep
        self._tokens: Optional[TokenProvider] = token_provider
        if self._tokens is None and token_secret is not None:
            self._tokens = _ServiceTokenCache(token_secret, caller, token_ttl_s)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=(timeouts or Timeouts()).to_httpx(),
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the pooled connections."""
        await self._client.aclose()

    async def _headers(
        self, extra: Optional[Mapping[str, str]], idempotency_key: Optional[str]
    ) -> dict[str, str]:
        """Auth, trace and idempotency headers merged over ``extra``."""
        headers: dict[str, str] = dict(extra or {})
        if self._tokens is not None:
            headers["Authorization"] = f"Bearer {await self._tokens()}"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        propagate.inject(headers)
        return headers

    def _backoff(self, attempt: int) -> float:
        """Sleep before retry number ``attempt`` (1-based)."""
        return self._backoff_s[min(attempt - 1, len(self._backoff_s) - 1)]

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        idempotency_key: Optional[str] = None,
        timeout: Optional[Timeouts] = None,
    ) -> httpx.Response:
        """Send one request with the retry policy; returns the final
        response, whatever its status.

        Raises:
            UpstreamUnavailableError: Could not connect after all retries.
            UpstreamTimeoutError: The peer did not answer in time.
        """
        verb = method.upper()
        idempotent = verb in IDEMPOTENT_METHODS or idempotency_key is not None
        sent_headers = await self._headers(headers, idempotency_key)
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    verb,
                    path,
                    json=json,
                    params=params,
                    headers=sent_headers,
                    timeout=timeout.to_httpx() if timeout else httpx.USE_CLIENT_DEFAULT,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                if attempt < self._retries:
                    attempt += 1
                    self._log_retry(verb, path, attempt, type(exc).__name__)
                    await self._sleep(self._backoff(attempt))
                    continue
                raise UpstreamUnavailableError(
                    f"{self.dependency} is unreachable.", dependency=self.dependency
                ) from exc
            except httpx.TimeoutException as exc:
                raise UpstreamTimeoutError(
                    f"{self.dependency} did not answer in time.",
                    dependency=self.dependency,
                ) from exc
            except httpx.TransportError as exc:
                raise UpstreamUnavailableError(
                    f"{self.dependency} connection failed.", dependency=self.dependency
                ) from exc
            if (
                idempotent
                and response.status_code in RETRY_STATUSES
                and attempt < self._retries
            ):
                attempt += 1
                self._log_retry(verb, path, attempt, str(response.status_code))
                await response.aclose()
                await self._sleep(self._backoff(attempt))
                continue
            return response

    def _log_retry(self, verb: str, path: str, attempt: int, reason: str) -> None:
        """One warning line per retry."""
        logger.warning(
            "retrying %s %s on %s (attempt %d)",
            verb,
            path,
            self.dependency,
            attempt + 1,
            extra={"dependency": self.dependency, "reason": reason},
        )

    async def request_json(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        """:meth:`request`, then the decoded JSON body (``None`` for 204);
        a 4xx/5xx raises via :func:`raise_for_problem`.
        """
        response = await self.request(method, path, **kwargs)
        raise_for_problem(response, self.dependency)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """GET ``path``."""
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """POST ``path``."""
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        """PUT ``path``."""
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        """PATCH ``path``."""
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """DELETE ``path``."""
        return await self.request("DELETE", path, **kwargs)


_STATUS_ERRORS: dict[int, type[FcrmError]] = {
    400: ValidationError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    504: UpstreamTimeoutError,
}


def _problem_detail(response: httpx.Response) -> Optional[str]:
    """``detail`` from a problem+json (or JSON) error body, if any."""
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, str) else None


def raise_for_problem(response: httpx.Response, dependency: str) -> None:
    """Raise the :mod:`fcrm_common.errors` class matching a peer's error.

    404/409/422/400/403 keep their meaning (a missing case at the peer is a
    missing case here). A 401 means *our* service credentials were refused
    — a deployment fault, so it surfaces as 503, not as the end user's
    401; likewise a 429 or any other unexpected status is the peer being
    unable to serve us, a 503 with ``dependency`` set. A 504 stays a 504.
    """
    status = response.status_code
    if status < 400:
        return
    detail = _problem_detail(response) or f"{dependency} answered {status}."
    error_cls = _STATUS_ERRORS.get(status, UpstreamUnavailableError)
    raise error_cls(detail, dependency=dependency, upstream_status=status)

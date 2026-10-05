"""OPA client for route authorization and agent-to-tool permits
(BUILD-PLAN §3.7, design A §8.2-8.3).

Decisions come from the OPA sidecar's data API:

- route authz -> ``POST {OPA_URL}/v1/data/authz/response`` with input
  ``{service, method, path, user: {id, roles}}`` where ``path`` is the
  FastAPI *route template* (``/cases/{id}/items/{item_id}``), so one rule
  covers every id and the cache key stays small;
- tool permits -> ``POST {OPA_URL}/v1/data/tools/response`` with input
  ``{agent, tool, case_id, run_id, role}``.

Design A's policies expose a ``response`` object (``{"allow": bool, ...}``);
the rule name is a constructor argument, so pointing the client at a bare
``allow`` rule (``{"result": true}``) works too. An undefined decision
(OPA returns no ``result``) is a deny.

Authorization fails **closed**: a transport error, timeout or non-200
from OPA raises :class:`fcrm_common.errors.AuthzUnavailableError` (503),
never an allow — this replaces the cloned ``opasecurity.py``, which also
``print``-ed every user it saw. Decisions are cached for 30 s in an
injected :class:`fcrm_common.cache.TTLCache` keyed by
``(roles, service, method, route_template)`` and ``(agent, tool, role,
has_case)``; a cache outage only makes calls slower.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any, Optional

import httpx
from fastapi import Depends, Request
from fastapi.routing import APIRoute
from fcrm_contracts.common import Principal

from fcrm_common.auth import current_user
from fcrm_common.cache import TTLCache, cache_get, cache_set
from fcrm_common.errors import AuthzUnavailableError, ForbiddenError

logger = logging.getLogger(__name__)

DECISION_TTL_S = 30
OPA_TIMEOUT_S = 2.0


def _cache_key(kind: str, parts: Mapping[str, Any]) -> str:
    """Short, stable cache key for a decision input."""
    digest = hashlib.sha256(
        json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return f"opa:{kind}:{digest}"


def _allowed(result: Any) -> bool:
    """Interpret an OPA ``result``: a bool, or an object with ``allow``."""
    if isinstance(result, bool):
        return result
    if isinstance(result, Mapping):
        return result.get("allow") is True
    return False


class OPAClient:
    """Asks OPA for decisions.

    Args:
        base_url: ``OPA_URL``, e.g. ``http://opa:8181``.
        cache: Optional decision cache (Redis in compose).
        ttl_s: Cache TTL (design A: 30 s).
        retries: Extra attempts on a transport error (design A §10.3: 1).
        authz_package: Package of the route policy (``authz``).
        tools_package: Package of the tool-permit policy (``tools``).
        rule: Rule queried in both packages (``response`` or ``allow``).
        client: Shared ``httpx.AsyncClient``; one with a 2 s timeout is
            created otherwise.
    """

    def __init__(
        self,
        base_url: str,
        *,
        cache: Optional[TTLCache] = None,
        ttl_s: int = DECISION_TTL_S,
        retries: int = 1,
        authz_package: str = "authz",
        tools_package: str = "tools",
        rule: str = "response",
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._cache = cache
        self._ttl_s = ttl_s
        self._retries = retries
        self._authz_path = f"/v1/data/{authz_package}/{rule}"
        self._tools_path = f"/v1/data/{tools_package}/{rule}"
        self._client = client or httpx.AsyncClient(timeout=OPA_TIMEOUT_S)

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def authz(
        self, principal: Principal, service: str, method: str, route_template: str
    ) -> bool:
        """Whether ``principal`` may call ``method route_template`` on
        ``service``.

        Raises:
            AuthzUnavailableError: OPA could not be consulted.
        """
        roles = sorted(str(r) for r in principal.roles)
        key = _cache_key(
            "authz",
            {
                "roles": roles,
                "service": service,
                "method": method.upper(),
                "path": route_template,
            },
        )
        payload = {
            "service": service,
            "method": method.upper(),
            "path": route_template,
            "user": {"id": principal.sub, "roles": roles},
        }
        return await self._decide(key, self._authz_path, payload)

    async def tool_permit(
        self,
        agent: str,
        tool: str,
        case_id: Optional[str],
        role: str,
        run_id: Optional[str],
    ) -> bool:
        """Whether ``agent`` may call ``tool`` for ``case_id`` (Guard 2).

        Raises:
            AuthzUnavailableError: OPA could not be consulted (the caller
                refuses the tool call: fail closed, BP §11).
        """
        key = _cache_key(
            "tools",
            {
                "agent": agent,
                "tool": tool,
                "role": role,
                "has_case": case_id is not None,
            },
        )
        payload = {
            "agent": agent,
            "tool": tool,
            "case_id": case_id,
            "run_id": run_id,
            "role": role,
        }
        return await self._decide(key, self._tools_path, payload)

    async def _decide(self, key: str, path: str, payload: Mapping[str, Any]) -> bool:
        """Cached decision for ``payload`` at ``path``."""
        cached = await cache_get(self._cache, key)
        if cached is not None:
            return cached == "1"
        allowed = _allowed(await self._query(path, payload))
        await cache_set(self._cache, key, "1" if allowed else "0", self._ttl_s)
        return allowed

    async def _query(self, path: str, payload: Mapping[str, Any]) -> Any:
        """POST ``{"input": payload}``; returns OPA's ``result`` (``None``
        when the decision is undefined).
        """
        attempt = 0
        while True:
            try:
                response = await self._client.post(
                    self._base_url + path, json={"input": dict(payload)}
                )
            except httpx.TransportError as exc:
                if attempt < self._retries:
                    attempt += 1
                    await asyncio.sleep(0.1)
                    continue
                logger.error(
                    "opa unreachable; failing closed",
                    extra={"dependency": "opa", "error": {"type": type(exc).__name__}},
                )
                raise AuthzUnavailableError(
                    "The authorization service is unavailable.", dependency="opa"
                ) from exc
            break
        if response.status_code != 200:
            logger.error(
                "opa answered %s; failing closed",
                response.status_code,
                extra={"dependency": "opa"},
            )
            raise AuthzUnavailableError(
                "The authorization service returned an error.", dependency="opa"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise AuthzUnavailableError(
                "The authorization service returned an unreadable decision.",
                dependency="opa",
            ) from exc
        return body.get("result") if isinstance(body, Mapping) else None


def route_template(request: Request) -> str:
    """The matched route's path template (``/cases/{id}``), falling back to
    the concrete path when no route matched.
    """
    route = request.scope.get("route")
    if isinstance(route, APIRoute):
        return route.path
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


def install_authz(app_state: Any, client: OPAClient) -> None:
    """Attach ``client`` where :func:`opa_dependency` looks for it
    (``app.state.opa``). Pass ``app.state``.
    """
    app_state.opa = client


def opa_dependency(service: str) -> Callable[..., Awaitable[Principal]]:
    """FastAPI dependency factory: authenticate (``current_user``), then ask
    OPA whether the caller may use this route; 403 on deny, 503 when OPA is
    unavailable or not configured.

    Use as a router-level dependency::

        router = APIRouter(dependencies=[Depends(opa_dependency("case"))])
    """

    async def dependency(
        request: Request,
        principal: Annotated[Principal, Depends(current_user)],
    ) -> Principal:
        client = getattr(request.app.state, "opa", None)
        if not isinstance(client, OPAClient):
            logger.error("opa_dependency used but no OPA client is installed")
            raise AuthzUnavailableError(
                "Authorization is not configured.", dependency="opa"
            )
        template = route_template(request)
        if not await client.authz(principal, service, request.method, template):
            raise ForbiddenError(f"Not permitted: {request.method} {template}")
        return principal

    return dependency

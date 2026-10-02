"""Bearer-token authentication for every service (BUILD-PLAN §3.7, design
A §8.1, §8.3).

Two kinds of token are accepted, told apart by the JOSE ``alg`` header and
verified with *different key material*, so algorithm confusion is not
possible (an RS256 public key is never used as an HMAC secret or vice
versa):

- **RS256 user tokens** from X1 identity, verified locally against its
  JWKS (``IDENTITY_JWKS_URL``, cached 10 minutes, a stale copy kept up to
  1 hour if identity is unreachable, an unknown ``kid`` triggering one
  rate-limited refresh for key rotation). ``typ`` must be ``access`` (or
  ``service`` for an identity-minted service token): a refresh token
  presented as an access token is rejected, fixing the cloned AEO identity
  bug. When no JWKS URL is configured, the token is checked by calling
  ``IDENTITY_VALIDATE_URL`` (``/core/validate``) instead, with results
  cached 60 s.
- **HS256 service tokens** minted by :func:`mint_service_token` with
  ``SERVICE_TOKEN_SECRET`` (role ``fcrm_service``, ``typ`` ``service``,
  5-minute TTL) and attached by :class:`fcrm_common.http.ServiceClient`
  to service-to-service calls.

Library choice: PyJWT (``pyjwt[crypto]``), not python-jose. Design A §8.1
moves identity itself to PyJWT; python-jose is effectively unmaintained;
PyJWT's ``PyJWKSet`` parses a JWKS into ready-to-use keys. JWKS fetching
is done here with ``httpx`` rather than ``PyJWKClient`` because the latter
uses blocking ``urllib`` inside the event loop.

Wiring in a service::

    app.state.token_verifier = TokenVerifier.from_settings(settings)

    @router.get("/cases/{id}")
    async def get_case(
        principal: Annotated[Principal, Depends(require_roles(Role.ANALYST))],
    ) -> ...
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import timedelta
from time import monotonic
from typing import Annotated, Any, Optional

import httpx
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from pydantic import SecretStr
from pydantic import ValidationError as PydanticValidationError

from fcrm_common.cache import TTLCache, cache_get, cache_set
from fcrm_common.errors import (
    ForbiddenError,
    UnauthorizedError,
    UpstreamUnavailableError,
)
from fcrm_common.ids import new_id
from fcrm_common.logging import bind_context
from fcrm_common.settings import BaseServiceSettings
from fcrm_common.time import utcnow

logger = logging.getLogger(__name__)

ACCESS_TYP = "access"
SERVICE_TYP = "service"
IDENTITY_ISSUER = "fcrm-identity"
SERVICE_ISSUER = "fcrm-service"
AUDIENCE = "fcrm"
SERVICE_TOKEN_TTL_S = 300
JWKS_TTL_S = 600
JWKS_STALE_S = 3600
VALIDATE_CACHE_TTL_S = 60
LEEWAY_S = 10


def mint_service_token(
    secret: str | SecretStr,
    service_name: str,
    *,
    ttl_s: int = SERVICE_TOKEN_TTL_S,
) -> str:
    """An HS256 service-account token: ``sub="svc:<service_name>"``,
    ``roles=["fcrm_service"]``, ``typ="service"``, ``iss="fcrm-service"``,
    ``aud="fcrm"``, a fresh ``jti`` and ``exp`` ``ttl_s`` from now.
    """
    key = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
    now = utcnow()
    claims: dict[str, Any] = {
        "sub": f"svc:{service_name}",
        "name": service_name,
        "roles": [Role.SERVICE.value],
        "typ": SERVICE_TYP,
        "iss": SERVICE_ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_s),
        "jti": new_id("jti"),
    }
    return jwt.encode(claims, key, algorithm="HS256")


class JwksCache:
    """Fetches and caches identity's JWKS.

    Args:
        url: ``IDENTITY_JWKS_URL``.
        client: Shared ``httpx.AsyncClient`` (one is created if omitted).
        ttl_s: Refresh interval (10 min, matching identity's cache-control).
        stale_s: How long a stale set is still used when refresh fails.
        retries: Extra attempts per refresh (design A §10.3: 2, 0.2 s apart).
        min_refresh_interval_s: Floor between forced refreshes for an
            unknown ``kid``, so forged ``kid`` values cannot hammer identity.
    """

    def __init__(
        self,
        url: str,
        *,
        client: Optional[httpx.AsyncClient] = None,
        ttl_s: float = JWKS_TTL_S,
        stale_s: float = JWKS_STALE_S,
        retries: int = 2,
        backoff_s: float = 0.2,
        timeout_s: float = 2.0,
        min_refresh_interval_s: float = 30.0,
    ) -> None:
        self._url = url
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._ttl_s = ttl_s
        self._stale_s = stale_s
        self._retries = retries
        self._backoff_s = backoff_s
        self._min_refresh_interval_s = min_refresh_interval_s
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: Optional[float] = None
        self._last_attempt: Optional[float] = None
        self._lock = asyncio.Lock()

    def _age(self) -> Optional[float]:
        """Seconds since the last successful fetch."""
        return None if self._fetched_at is None else monotonic() - self._fetched_at

    async def get_signing_key(self, kid: Optional[str]) -> jwt.PyJWK:
        """The key for ``kid`` (or the only key when the token has none).

        Raises:
            UnauthorizedError: No key matches.
            UpstreamUnavailableError: JWKS unreachable and no usable copy.
        """
        age = self._age()
        if age is None or age >= self._ttl_s:
            await self._refresh(force=False)
        key = self._lookup(kid)
        if key is None:
            await self._refresh(force=True)
            key = self._lookup(kid)
        if key is None:
            raise UnauthorizedError("Token signing key is not recognised.")
        return key

    def _lookup(self, kid: Optional[str]) -> Optional[jwt.PyJWK]:
        """Key by id; a kid-less token is accepted only with a single key."""
        if kid is not None:
            return self._keys.get(kid)
        if len(self._keys) == 1:
            return next(iter(self._keys.values()))
        return None

    async def _refresh(self, *, force: bool) -> None:
        """Re-fetch the JWKS, keeping a stale copy within ``stale_s`` on
        failure.
        """
        async with self._lock:
            now = monotonic()
            age = self._age()
            if not force and age is not None and age < self._ttl_s:
                return  # another task refreshed while we waited
            if (
                force
                and self._last_attempt is not None
                and now - self._last_attempt < self._min_refresh_interval_s
            ):
                return
            self._last_attempt = now
            try:
                self._keys = await self._fetch()
                self._fetched_at = monotonic()
            except (httpx.HTTPError, ValueError, jwt.PyJWKError) as exc:
                if age is not None and age < self._stale_s:
                    logger.warning(
                        "jwks refresh failed; using cached keys",
                        extra={
                            "dependency": "identity",
                            "error": {"type": type(exc).__name__},
                        },
                    )
                    return
                logger.error(
                    "jwks unavailable",
                    extra={
                        "dependency": "identity",
                        "error": {"type": type(exc).__name__},
                    },
                )
                raise UpstreamUnavailableError(
                    "Identity keys are unavailable.", dependency="identity"
                ) from exc

    async def _fetch(self) -> dict[str, jwt.PyJWK]:
        """GET the JWKS with retries; returns RS256 keys by ``kid``."""
        attempt = 0
        while True:
            try:
                response = await self._client.get(self._url)
                response.raise_for_status()
                key_set = jwt.PyJWKSet.from_dict(response.json())
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt >= self._retries:
                    raise
                attempt += 1
                await asyncio.sleep(self._backoff_s)
                continue
            return {
                str(k.key_id) if k.key_id is not None else "": k for k in key_set.keys
            }

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class TokenVerifier:
    """Verifies a bearer token and returns the :class:`Principal`.

    Args:
        jwks: JWKS cache for RS256 tokens (preferred path).
        validate_url: ``/core/validate`` fallback used when ``jwks`` is None.
        service_secret: ``SERVICE_TOKEN_SECRET`` for HS256 service tokens;
            ``None`` disables service tokens.
        issuer: Expected ``iss`` of RS256 tokens (``None`` skips the check).
        audience: Expected ``aud`` (``None`` skips the check).
        client: HTTP client for the validate fallback.
        cache: Optional shared cache for validate results (60 s).
    """

    def __init__(
        self,
        *,
        jwks: Optional[JwksCache] = None,
        validate_url: Optional[str] = None,
        service_secret: Optional[str | SecretStr] = None,
        issuer: Optional[str] = IDENTITY_ISSUER,
        audience: Optional[str] = AUDIENCE,
        client: Optional[httpx.AsyncClient] = None,
        cache: Optional[TTLCache] = None,
        leeway_s: int = LEEWAY_S,
    ) -> None:
        self._jwks = jwks
        self._validate_url = validate_url
        self._service_secret = (
            service_secret.get_secret_value()
            if isinstance(service_secret, SecretStr)
            else service_secret
        )
        self._issuer = issuer
        self._audience = audience
        self._client = client or httpx.AsyncClient(timeout=2.0)
        self._cache = cache
        self._leeway_s = leeway_s

    @classmethod
    def from_settings(
        cls, settings: BaseServiceSettings, *, cache: Optional[TTLCache] = None
    ) -> TokenVerifier:
        """Build from ``IDENTITY_JWKS_URL`` / ``IDENTITY_VALIDATE_URL`` /
        ``SERVICE_TOKEN_SECRET``.
        """
        jwks = (
            JwksCache(settings.identity_jwks_url)
            if settings.identity_jwks_url
            else None
        )
        return cls(
            jwks=jwks,
            validate_url=settings.identity_validate_url,
            service_secret=settings.service_token_secret,
            cache=cache,
        )

    async def verify(self, token: str) -> Principal:
        """Verify ``token`` and return its principal.

        Raises:
            UnauthorizedError: Malformed, expired, wrongly signed, wrong
                ``typ``/``iss``/``aud``, or an unknown role.
            UpstreamUnavailableError: Identity could not be consulted.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Malformed bearer token.") from exc
        alg = header.get("alg")
        if alg == "HS256" and self._service_secret:
            return self._verify_service(token, self._service_secret)
        if alg == "RS256":
            if self._jwks is not None:
                return await self._verify_rs256(token, header.get("kid"), self._jwks)
            if self._validate_url:
                return await self._verify_remote(token, self._validate_url)
            raise UpstreamUnavailableError(
                "No identity verification method is configured.", dependency="identity"
            )
        raise UnauthorizedError("Unsupported token algorithm.")

    def _verify_service(self, token: str, secret: str) -> Principal:
        """HS256 service token signed with ``SERVICE_TOKEN_SECRET``."""
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=AUDIENCE,
                issuer=SERVICE_ISSUER,
                options={"require": ["exp", "iat", "sub", "typ"]},
                leeway=self._leeway_s,
            )
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Invalid service token.") from exc
        if claims.get("typ") != SERVICE_TYP or claims.get("roles") != [
            Role.SERVICE.value
        ]:
            raise UnauthorizedError("Invalid service token claims.")
        return _principal(claims, SERVICE_TYP)

    async def _verify_rs256(
        self, token: str, kid: Optional[str], jwks: JwksCache
    ) -> Principal:
        """RS256 identity token against the JWKS."""
        key = await jwks.get_signing_key(kid)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["exp", "iat", "sub", "typ"],
                    "verify_aud": self._audience is not None,
                    "verify_iss": self._issuer is not None,
                },
                leeway=self._leeway_s,
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("Token expired.") from exc
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Invalid token.") from exc
        typ = claims.get("typ")
        if typ not in (ACCESS_TYP, SERVICE_TYP):
            raise UnauthorizedError("Token is not an access token.")
        return _principal(claims, str(typ))

    async def _verify_remote(self, token: str, validate_url: str) -> Principal:
        """Fallback: ask identity's ``/core/validate`` (cached 60 s)."""
        cache_key = "validate:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
        cached = await cache_get(self._cache, cache_key)
        if cached is not None:
            try:
                return Principal.model_validate_json(cached)
            except PydanticValidationError:
                logger.warning("discarding unparseable cached principal")
        try:
            response = await self._client.post(
                validate_url, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError(
                "Identity is unavailable.", dependency="identity"
            ) from exc
        if response.status_code in (401, 403):
            raise UnauthorizedError("Token rejected by identity.")
        if response.status_code != 200:
            raise UpstreamUnavailableError(
                f"Identity answered {response.status_code}.", dependency="identity"
            )
        principal = _principal_from_validate(response.json())
        await cache_set(
            self._cache, cache_key, principal.model_dump_json(), VALIDATE_CACHE_TTL_S
        )
        return principal

    async def aclose(self) -> None:
        """Close owned HTTP clients."""
        await self._client.aclose()
        if self._jwks is not None:
            await self._jwks.aclose()


def _principal(claims: dict[str, Any], typ: str) -> Principal:
    """Principal from verified claims; unknown roles are a 401."""
    try:
        return Principal(
            sub=str(claims["sub"]),
            roles=list(claims.get("roles") or []),
            name=str(claims.get("name") or claims["sub"]),
            typ=typ,
        )
    except PydanticValidationError as exc:
        raise UnauthorizedError("Token carries an unknown role.") from exc


def _principal_from_validate(data: Any) -> Principal:
    """Principal from a ``/core/validate`` body (``ValidateResponse``
    ``{valid, sub, roles}`` or design A's ``{sub, roles, name, exp, typ}``).
    """
    if not isinstance(data, dict) or data.get("valid") is False or not data.get("sub"):
        raise UnauthorizedError("Token rejected by identity.")
    typ = data.get("typ", ACCESS_TYP)
    if typ not in (ACCESS_TYP, SERVICE_TYP):
        raise UnauthorizedError("Token is not an access token.")
    return _principal(data, str(typ))


bearer_scheme = HTTPBearer(auto_error=False, description="FCRM access token")


def install_auth(app_state: Any, verifier: TokenVerifier) -> None:
    """Attach ``verifier`` where :func:`current_user` looks for it
    (``app.state.token_verifier``). Pass ``app.state``.
    """
    app_state.token_verifier = verifier


async def current_user(
    request: Request,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)
    ],
) -> Principal:
    """FastAPI dependency: the verified caller.

    Also stores the principal on ``request.state.principal`` and binds
    ``user_id`` (the ``sub`` only, never a name or email) into the logging
    context.

    Raises:
        UnauthorizedError: No bearer token, or verification failed.
        UpstreamUnavailableError: The service has no verifier configured or
            identity is unreachable (fails closed).
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Bearer token required.")
    verifier = getattr(request.app.state, "token_verifier", None)
    if not isinstance(verifier, TokenVerifier):
        logger.error("current_user used but no token_verifier is installed")
        raise UpstreamUnavailableError(
            "Authentication is not configured.", dependency="identity"
        )
    principal = await verifier.verify(credentials.credentials)
    request.state.principal = principal
    bind_context(user_id=principal.sub)
    return principal


def has_any_role(principal: Principal, roles: Iterable[Role]) -> bool:
    """Whether ``principal`` holds at least one of ``roles``."""
    wanted = set(roles)
    return any(role in wanted for role in principal.roles)


def require_roles(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: the caller must hold one of ``roles`` (403
    otherwise). No implicit hierarchy: list ``Role.SERVICE`` explicitly on
    routes services call; fine-grained route policy is OPA's job
    (:mod:`fcrm_common.authz`).
    """
    if not roles:
        raise ValueError("require_roles needs at least one role")

    async def dependency(
        principal: Annotated[Principal, Depends(current_user)],
    ) -> Principal:
        if not has_any_role(principal, roles):
            raise ForbiddenError("This route requires one of: " + ", ".join(roles))
        return principal

    return dependency

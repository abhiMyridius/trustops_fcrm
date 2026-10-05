"""X1's HTTP surface (design A §8.1).

Every route here except the login family (``/login``, ``/refresh``,
``/demo-users``, ``/core/validate``, ``/core/userinfo``,
``/.well-known/jwks.json``)
carries ``Depends(current_user)`` and ``Depends(opa_dependency("identity"))``,
matching the convention design A §2 sets for every service in this
workspace, including X1 itself: identity verifies its own issued tokens
the same way every other service verifies them (against
``GET /.well-known/jwks.json`` through :class:`fcrm_common.auth.JwksCache`),
and asks the same OPA for the route's role permit
(``ops/opa/data/permissions.json``'s ``identity`` rules).
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse
from fcrm_common.auth import current_user
from fcrm_common.authz import opa_dependency
from fcrm_common.errors import NotFoundError, UnauthorizedError
from fcrm_common.time import utcnow
from fcrm_contracts.api.identity import (
    DemoUserGroup,
    LoginRequest,
    LoginResponse,
    PrincipalOut,
    RosterUser,
    ValidateResponse,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from pydantic import BaseModel, ConfigDict

from fcrm_identity.directory import demo_groups, principal_out, roster_user
from fcrm_identity.jwks import JWKS_CACHE_CONTROL, build_jwks
from fcrm_identity.models import RefreshTokenRecord, UserRecord
from fcrm_identity.security import DUMMY_HASH, verify_password
from fcrm_identity.settings import IdentitySettings
from fcrm_identity.store import IdentityStore
from fcrm_identity.tokens import REFRESH_TYP, MintedToken, TokenIssuer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["identity"])


class RefreshRequest(BaseModel):
    """Non-browser clients may send the refresh token in the body instead
    of relying on the cookie (design A §8.1: "cookie or {refresh_token}").
    Not a shared contract shape (no other service calls this): kept local
    to this package rather than added to ``fcrm_contracts`` on this pass.
    """

    model_config = ConfigDict(extra="forbid")

    refresh_token: Optional[str] = None


def get_store(request: Request) -> IdentityStore:
    """The store wired onto ``app.state`` at startup (``lifespan.py``)."""
    store: IdentityStore = request.app.state.store
    return store


def get_issuer(request: Request) -> TokenIssuer:
    """The token issuer wired onto ``app.state`` at startup."""
    issuer: TokenIssuer = request.app.state.issuer
    return issuer


def get_settings(request: Request) -> IdentitySettings:
    """The settings this process started with."""
    settings: IdentitySettings = request.app.state.settings
    return settings


def _refresh_token_from(
    request: Request, body: Optional[RefreshRequest], settings: IdentitySettings
) -> Optional[str]:
    """The refresh token from the httpOnly cookie, or the body for a
    non-browser client; the cookie wins when both are present.
    """
    cookie = request.cookies.get(settings.refresh_cookie_name)
    if cookie:
        return cookie
    if body is not None and body.refresh_token:
        return body.refresh_token
    return None


def _set_refresh_cookie(
    response: Response, settings: IdentitySettings, refresh: MintedToken
) -> None:
    response.set_cookie(
        settings.refresh_cookie_name,
        refresh.token,
        max_age=settings.jwt_refresh_ttl_s,
        httponly=True,
        samesite="strict",
        path=settings.refresh_cookie_path,
    )


async def _issue_tokens(
    store: IdentityStore,
    issuer: TokenIssuer,
    settings: IdentitySettings,
    response: Response,
    *,
    user: UserRecord,
) -> LoginResponse:
    """Mint and persist a fresh access+refresh pair for ``user``, set the
    refresh cookie and return the body every login-family route answers
    with (the token plus the web's ``user`` profile).
    """
    roles = [str(r.value) for r in user.roles]
    access = issuer.mint_access(sub=user.id, roles=roles, name=user.name)
    refresh = issuer.mint_refresh(sub=user.id, roles=roles, name=user.name)
    await store.save_refresh(
        RefreshTokenRecord(jti=refresh.jti, sub=user.id, exp=refresh.exp, revoked=False)
    )
    _set_refresh_cookie(response, settings, refresh)
    return LoginResponse(
        access_token=access.token,
        expires_in=settings.jwt_access_ttl_s,
        user=principal_out(user),
    )


@router.post("/login", response_model=LoginResponse, response_model_exclude_none=True)
async def login(
    body: LoginRequest,
    response: Response,
    store: Annotated[IdentityStore, Depends(get_store)],
    issuer: Annotated[TokenIssuer, Depends(get_issuer)],
    settings: Annotated[IdentitySettings, Depends(get_settings)],
) -> LoginResponse:
    """Password login for a seeded user. Rate-limited at the gateway
    (nginx ``limit_req zone=fcrm_login``), not here.

    The account is named by ``username`` or ``email`` (exactly one, see
    :class:`~fcrm_contracts.api.identity.LoginRequest`). When the local demo
    picker is on (:attr:`IdentitySettings.demo_login_active`) an *empty*
    password signs the named, enabled user in without a hash check; in every
    other environment an empty password is checked like any other and fails.

    Raises:
        UnauthorizedError: Unknown login, disabled account or wrong
            password -- one message for all three, so the response never
            reveals which it was.
    """
    user = await store.find_user_by_login(body.login)
    demo_pick = settings.demo_login_active and body.password == ""
    if demo_pick and user is not None and user.enabled:
        logger.info("demo login", extra={"sub": user.id})
        return await _issue_tokens(store, issuer, settings, response, user=user)
    password_hash = user.password_hash if user is not None else DUMMY_HASH
    password_ok = verify_password(body.password, password_hash)
    if user is None or not user.enabled or not password_ok:
        raise UnauthorizedError("Unknown user or wrong password.")
    return await _issue_tokens(store, issuer, settings, response, user=user)


@router.post("/refresh", response_model=LoginResponse, response_model_exclude_none=True)
async def refresh(
    request: Request,
    response: Response,
    store: Annotated[IdentityStore, Depends(get_store)],
    issuer: Annotated[TokenIssuer, Depends(get_issuer)],
    settings: Annotated[IdentitySettings, Depends(get_settings)],
    body: Annotated[Optional[RefreshRequest], Body()] = None,
) -> LoginResponse:
    """Rotate a refresh token for a new access token.

    Raises:
        UnauthorizedError: No refresh token presented, it is not a
            ``refresh``-typed token (rejects an access token here, the
            mirror image of bug 1), it was never issued, is revoked or
            expired, or its owner is no longer enabled.
    """
    token = _refresh_token_from(request, body, settings)
    if token is None:
        raise UnauthorizedError("No refresh token presented.")
    decoded = issuer.decode(token, expected_typ=REFRESH_TYP)
    record = await store.refresh_record(decoded.jti)
    if record is None or record.revoked or record.exp <= utcnow():
        raise UnauthorizedError("Refresh token is no longer valid.")
    user = await store.user_by_id(decoded.sub)
    if user is None or not user.enabled:
        raise UnauthorizedError("Account is no longer active.")
    await store.revoke_refresh(decoded.jti)
    return await _issue_tokens(store, issuer, settings, response, user=user)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_user)],
    _authorized: Annotated[Principal, Depends(opa_dependency("identity"))],
    store: Annotated[IdentityStore, Depends(get_store)],
    issuer: Annotated[TokenIssuer, Depends(get_issuer)],
    settings: Annotated[IdentitySettings, Depends(get_settings)],
    body: Annotated[Optional[RefreshRequest], Body()] = None,
) -> None:
    """Revoke the caller's refresh token (if one is presented and still
    decodes) and clear the cookie. Always 204: logging out an
    already-expired session is not an error.
    """
    token = _refresh_token_from(request, body, settings)
    if token is not None:
        try:
            decoded = issuer.decode(token, expected_typ=REFRESH_TYP)
        except UnauthorizedError:
            logger.info("logout presented an unusable refresh token")
        else:
            await store.revoke_refresh(decoded.jti)
    response.delete_cookie(
        settings.refresh_cookie_name, path=settings.refresh_cookie_path
    )


async def _own_profile(principal: Principal, store: IdentityStore) -> PrincipalOut:
    """The stored profile of the token's subject (the token only carries
    ``sub``, ``name`` and ``roles``; the title and unit live in the store).

    Raises:
        UnauthorizedError: The subject no longer exists or was disabled
            since the access token was minted.
    """
    user = await store.user_by_id(principal.sub)
    if user is None or not user.enabled:
        raise UnauthorizedError("Account is no longer active.")
    return principal_out(user)


@router.get("/me", response_model=PrincipalOut, response_model_exclude_none=True)
async def me(
    principal: Annotated[Principal, Depends(current_user)],
    _authorized: Annotated[Principal, Depends(opa_dependency("identity"))],
    store: Annotated[IdentityStore, Depends(get_store)],
) -> PrincipalOut:
    """The caller's own profile (id, name, roles, title, unit)."""
    return await _own_profile(principal, store)


@router.get("/users/me", response_model=PrincipalOut, response_model_exclude_none=True)
async def users_me(
    principal: Annotated[Principal, Depends(current_user)],
    _authorized: Annotated[Principal, Depends(opa_dependency("identity"))],
    store: Annotated[IdentityStore, Depends(get_store)],
) -> PrincipalOut:
    """Alias of ``GET /me`` (design A §8.1)."""
    return await _own_profile(principal, store)


@router.get("/users", response_model=list[RosterUser], response_model_exclude_none=True)
async def users(
    role: Role,
    _principal: Annotated[Principal, Depends(current_user)],
    _authorized: Annotated[Principal, Depends(opa_dependency("identity"))],
    store: Annotated[IdentityStore, Depends(get_store)],
) -> list[RosterUser]:
    """The enabled people holding ``role``: the roster R5 auto-triage picks
    an analyst from and committee release counts members against. OPA lets
    analysts, committee members and the service account call it
    (``permissions.json``'s ``identity-get-users-...`` rule).
    """
    return [roster_user(u) for u in await store.list_users(role)]


@router.get(
    "/demo-users",
    response_model=list[DemoUserGroup],
    response_model_exclude_none=True,
)
async def demo_users(
    store: Annotated[IdentityStore, Depends(get_store)],
    settings: Annotated[IdentitySettings, Depends(get_settings)],
) -> list[DemoUserGroup]:
    """The seeded people grouped for the web's demo picker. Unauthenticated
    by design (it is shown before anyone has signed in) and therefore
    available only where password-less login is
    (:attr:`IdentitySettings.demo_login_active`).

    Raises:
        NotFoundError: Demo mode is off or ``ENV`` is not local -- the same
            404 an unknown route gets, so the endpoint does not advertise
            itself.
    """
    if not settings.demo_login_active:
        raise NotFoundError("Not found.")
    return demo_groups(await store.list_users())


@router.post("/core/validate", response_model=ValidateResponse)
async def core_validate(
    principal: Annotated[Principal, Depends(current_user)],
) -> ValidateResponse:
    """The AEO contract kept for nginx's ``auth_request`` (``ops/nginx``)
    and for any service falling back to it when JWKS is unreachable.
    ``current_user`` already rejects a non-access, expired or badly
    signed token with 401 before this body ever runs (bug 1's fix, shared
    with every other service via :class:`fcrm_common.auth.TokenVerifier`).
    """
    return ValidateResponse(
        valid=True, sub=principal.sub, roles=[Role(r) for r in principal.roles]
    )


@router.get("/core/userinfo", response_model=ValidateResponse)
async def core_userinfo(
    principal: Annotated[Principal, Depends(current_user)],
) -> ValidateResponse:
    """Alias of ``POST /core/validate`` (design A §8.1)."""
    return ValidateResponse(
        valid=True, sub=principal.sub, roles=[Role(r) for r in principal.roles]
    )


@router.get("/.well-known/jwks.json", include_in_schema=False)
async def jwks(request: Request) -> JSONResponse:
    """The public key set every service's :class:`fcrm_common.auth.JwksCache`
    fetches (no auth: it is how auth becomes possible in the first place).
    """
    return JSONResponse(
        build_jwks(request.app.state.signing_key),
        headers={"Cache-Control": JWKS_CACHE_CONTROL},
    )

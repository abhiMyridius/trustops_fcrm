"""RS256 access/refresh token minting and local decoding for X1 (design A
§8.1).

Claims are minimal (``sub, roles, name, typ, iat, exp, jti, iss, aud``):
no user document is ever copied into the token (the cloned
``evoqidentityservice`` put the *whole* Mongo user document into
``additional_claims``, design A §8.1 fix 4).

Fix carried from the cloned service (CLAUDE.md's AEO-reuse table, bug 1):
its own ``validate_jwt_token`` decoded a token and returned the payload
without ever looking at the ``type`` claim, so a refresh token presented
as an access token passed every check that used it (``routes/core_routes.py``'s
``/validate`` and ``/userinfo``). :meth:`TokenIssuer.decode` takes the
*expected* ``typ`` as a required argument and raises
:class:`fcrm_common.errors.UnauthorizedError` on any mismatch, so a caller
cannot forget the check the way the clone did; every caller in this
package (``routes.py``) passes an explicit ``expected_typ``.

Other TrustOps services never call this module directly: they verify an
X1-issued token against its public key through
:class:`fcrm_common.auth.TokenVerifier`, fetched over
``GET /.well-known/jwks.json`` (``jwks.py``). ``TokenVerifier`` enforces
the same ``typ in {"access", "service"}`` rule independently, so the fix
holds at both ends: here, at the one place that accepts a *refresh* token
on purpose, and there, at every place that must not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import jwt
from fcrm_common.errors import UnauthorizedError
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow

from fcrm_identity.jwtkeys import SigningKey

ALGORITHM = "RS256"
ACCESS_TYP = "access"
REFRESH_TYP = "refresh"
LEEWAY_S = 10


@dataclass(frozen=True)
class MintedToken:
    """A freshly signed token plus the claims a caller needs to persist
    (``routes.py`` stores ``jti``/``exp`` for a refresh token so it can be
    looked up and revoked later; nothing stores an access token's ``jti``).
    """

    token: str
    jti: str
    exp: datetime


@dataclass(frozen=True)
class DecodedToken:
    """The verified claims of a token this service minted."""

    sub: str
    roles: list[str]
    name: str
    typ: str
    jti: str


class TokenIssuer:
    """Mints and locally decodes this service's own RS256 tokens.

    Args:
        signing_key: The loaded RSA keypair (``jwtkeys.py``).
        issuer: ``iss`` claim (``JWT_ISSUER``).
        audience: ``aud`` claim (``JWT_AUDIENCE``).
        access_ttl_s: Access token lifetime (``JWT_ACCESS_TTL_S``).
        refresh_ttl_s: Refresh token lifetime (``JWT_REFRESH_TTL_S``).
    """

    def __init__(
        self,
        signing_key: SigningKey,
        *,
        issuer: str,
        audience: str,
        access_ttl_s: int,
        refresh_ttl_s: int,
    ) -> None:
        self._signing_key = signing_key
        self._issuer = issuer
        self._audience = audience
        self._access_ttl_s = access_ttl_s
        self._refresh_ttl_s = refresh_ttl_s

    def mint_access(self, *, sub: str, roles: list[str], name: str) -> MintedToken:
        """A 15-minute (default) access token for ``sub``."""
        return self._mint(sub, roles, name, ACCESS_TYP, self._access_ttl_s)

    def mint_refresh(self, *, sub: str, roles: list[str], name: str) -> MintedToken:
        """An 8-hour (default) refresh token for ``sub``."""
        return self._mint(sub, roles, name, REFRESH_TYP, self._refresh_ttl_s)

    def _mint(
        self, sub: str, roles: list[str], name: str, typ: str, ttl_s: int
    ) -> MintedToken:
        now = utcnow()
        exp = now + timedelta(seconds=ttl_s)
        jti = new_id("jti")
        claims: dict[str, Any] = {
            "sub": sub,
            "roles": list(roles),
            "name": name,
            "typ": typ,
            "iat": now,
            "exp": exp,
            "jti": jti,
            "iss": self._issuer,
            "aud": self._audience,
        }
        token = jwt.encode(claims, self._signing_key.private_key, algorithm=ALGORITHM)
        return MintedToken(token=token, jti=jti, exp=exp)

    def decode(self, token: str, *, expected_typ: str) -> DecodedToken:
        """Verify ``token`` and require its ``typ`` to equal ``expected_typ``.

        Raises:
            UnauthorizedError: The signature, expiry, issuer or audience
                do not check out, or ``typ`` is not ``expected_typ`` (this
                is the bug-1 fix: a caller cannot decode without stating
                which token type it will accept).
        """
        try:
            claims = jwt.decode(
                token,
                self._signing_key.public_key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "typ", "jti"]},
                leeway=LEEWAY_S,
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("Token expired.") from exc
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Invalid token.") from exc
        typ = claims.get("typ")
        if typ != expected_typ:
            raise UnauthorizedError(f"Expected a {expected_typ!r} token.")
        return DecodedToken(
            sub=str(claims["sub"]),
            roles=list(claims.get("roles") or []),
            name=str(claims.get("name") or claims["sub"]),
            typ=str(typ),
            jti=str(claims["jti"]),
        )

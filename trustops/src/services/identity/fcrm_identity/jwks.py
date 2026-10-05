"""``GET /.well-known/jwks.json`` body (design A §8.1).

The shape must satisfy ``jwt.PyJWKSet.from_dict`` exactly, because that is
what every other service's :class:`fcrm_common.auth.JwksCache` calls on
this response (``tests/unit/services/identity/test_jwks.py`` asserts this
directly: it feeds the built document straight through ``PyJWKSet.from_dict``,
the same call ``JwksCache._fetch`` makes).
"""

from __future__ import annotations

import json
from typing import Any, cast

from jwt.algorithms import RSAAlgorithm

from fcrm_identity.jwtkeys import SigningKey

JWKS_CACHE_CONTROL = "public, max-age=600"


def _public_jwk(signing_key: SigningKey) -> dict[str, Any]:
    """The bare JWK (``kty``, ``n``, ``e``, ...) for ``signing_key``'s
    public key, as a plain ``dict`` regardless of the installed PyJWT
    version's ``to_jwk`` return type (``str`` in some versions, ``dict``
    with others when ``as_dict=True`` is supported).
    """
    try:
        return RSAAlgorithm.to_jwk(signing_key.public_key, as_dict=True)
    except TypeError:
        raw = RSAAlgorithm.to_jwk(signing_key.public_key)
        return cast(dict[str, Any], json.loads(raw))


def build_jwks(signing_key: SigningKey) -> dict[str, Any]:
    """The JWKS document for ``signing_key``: one RS256 signature key."""
    jwk = _public_jwk(signing_key)
    jwk.update({"kid": signing_key.kid, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}

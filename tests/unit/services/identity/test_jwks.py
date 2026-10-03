"""fcrm_identity.jwks: the JWKS body must satisfy ``jwt.PyJWKSet.from_dict``,
because that is exactly what every other service's
:class:`fcrm_common.auth.JwksCache` calls on it (``JwksCache._fetch``).
"""

from __future__ import annotations

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fcrm_identity.jwks import build_jwks
from fcrm_identity.jwtkeys import SigningKey


def _signing_key() -> SigningKey:
    return SigningKey(rsa.generate_private_key(public_exponent=65537, key_size=2048))


def test_build_jwks_shape():
    signing_key = _signing_key()
    jwks = build_jwks(signing_key)
    assert list(jwks.keys()) == ["keys"]
    assert len(jwks["keys"]) == 1
    key = jwks["keys"][0]
    assert key["kid"] == signing_key.kid
    assert key["kty"] == "RSA"
    assert key["use"] == "sig"
    assert key["alg"] == "RS256"


def test_build_jwks_is_parseable_by_jwks_cache_consumers():
    """The exact call ``fcrm_common.auth.JwksCache._fetch`` makes."""
    signing_key = _signing_key()
    key_set = jwt.PyJWKSet.from_dict(build_jwks(signing_key))
    assert len(key_set.keys) == 1
    assert key_set.keys[0].key_id == signing_key.kid


def test_build_jwks_key_can_verify_a_token_signed_by_the_private_key():
    signing_key = _signing_key()
    key_set = jwt.PyJWKSet.from_dict(build_jwks(signing_key))
    pyjwk = key_set.keys[0]
    token = jwt.encode(
        {"sub": "u-riyer"},
        signing_key.private_key,
        algorithm="RS256",
        headers={"kid": signing_key.kid},
    )
    decoded = jwt.decode(token, pyjwk.key, algorithms=["RS256"])
    assert decoded["sub"] == "u-riyer"

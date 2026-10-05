"""fcrm_identity.tokens: RS256 minting and the typ-aware decode (bug 1's
fix at the point this service issues and reads its own tokens).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fcrm_common.errors import UnauthorizedError
from fcrm_identity.jwtkeys import SigningKey
from fcrm_identity.tokens import ACCESS_TYP, REFRESH_TYP, TokenIssuer

ISSUER = "fcrm-identity"
AUDIENCE = "fcrm"


def _generate_signing_key() -> SigningKey:
    return SigningKey(rsa.generate_private_key(public_exponent=65537, key_size=2048))


def _issuer_for(signing_key: SigningKey, **overrides: object) -> TokenIssuer:
    kwargs: dict[str, object] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "access_ttl_s": 900,
        "refresh_ttl_s": 28800,
    }
    kwargs.update(overrides)
    return TokenIssuer(signing_key, **kwargs)  # type: ignore[arg-type]


def _raw_token(signing_key: SigningKey, **claim_overrides: object) -> str:
    """A hand-built JWT signed by ``signing_key``, for scenarios
    ``TokenIssuer.mint_*`` cannot itself produce (an already-expired
    token, a forged audience).
    """
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "u-riyer",
        "roles": ["fcrm_analyst"],
        "name": "Rahul Iyer",
        "typ": ACCESS_TYP,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": "j1",
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, signing_key.private_key, algorithm="RS256")


@pytest.fixture
def signing_key() -> SigningKey:
    return _generate_signing_key()


@pytest.fixture
def issuer(signing_key: SigningKey) -> TokenIssuer:
    return _issuer_for(signing_key)


def test_mint_access_round_trips(issuer: TokenIssuer):
    minted = issuer.mint_access(
        sub="u-riyer", roles=["fcrm_analyst"], name="Rahul Iyer"
    )
    decoded = issuer.decode(minted.token, expected_typ=ACCESS_TYP)
    assert decoded.sub == "u-riyer"
    assert decoded.roles == ["fcrm_analyst"]
    assert decoded.name == "Rahul Iyer"
    assert decoded.typ == ACCESS_TYP
    assert decoded.jti == minted.jti


def test_mint_refresh_round_trips(issuer: TokenIssuer):
    minted = issuer.mint_refresh(
        sub="u-riyer", roles=["fcrm_analyst"], name="Rahul Iyer"
    )
    decoded = issuer.decode(minted.token, expected_typ=REFRESH_TYP)
    assert decoded.typ == REFRESH_TYP


def test_decode_rejects_an_access_token_when_a_refresh_is_expected(issuer: TokenIssuer):
    access = issuer.mint_access(
        sub="u-riyer", roles=["fcrm_analyst"], name="Rahul Iyer"
    )
    with pytest.raises(UnauthorizedError):
        issuer.decode(access.token, expected_typ=REFRESH_TYP)


def test_decode_rejects_a_refresh_token_when_an_access_is_expected(issuer: TokenIssuer):
    """The bug-1 scenario at the point this service mints its own tokens:
    the cloned ``validate_jwt_token`` never checked ``type`` at all, so a
    refresh token decoded fine wherever an access token was expected.
    """
    refresh = issuer.mint_refresh(
        sub="u-riyer", roles=["fcrm_analyst"], name="Rahul Iyer"
    )
    with pytest.raises(UnauthorizedError):
        issuer.decode(refresh.token, expected_typ=ACCESS_TYP)


def test_decode_rejects_expired_token(signing_key: SigningKey, issuer: TokenIssuer):
    now = datetime.now(UTC)
    expired = _raw_token(
        signing_key, iat=now - timedelta(hours=1), exp=now - timedelta(minutes=5)
    )
    with pytest.raises(UnauthorizedError):
        issuer.decode(expired, expected_typ=ACCESS_TYP)


def test_decode_rejects_wrong_audience(signing_key: SigningKey, issuer: TokenIssuer):
    forged = _raw_token(signing_key, aud="someone-else")
    with pytest.raises(UnauthorizedError):
        issuer.decode(forged, expected_typ=ACCESS_TYP)


def test_decode_rejects_wrong_issuer(signing_key: SigningKey, issuer: TokenIssuer):
    forged = _raw_token(signing_key, iss="someone-else")
    with pytest.raises(UnauthorizedError):
        issuer.decode(forged, expected_typ=ACCESS_TYP)


def test_decode_rejects_token_signed_by_a_different_key(issuer: TokenIssuer):
    other_key = _generate_signing_key()
    forged = _raw_token(other_key)
    with pytest.raises(UnauthorizedError):
        issuer.decode(forged, expected_typ=ACCESS_TYP)


def test_decode_rejects_malformed_token(issuer: TokenIssuer):
    with pytest.raises(UnauthorizedError):
        issuer.decode("not-a-jwt", expected_typ=ACCESS_TYP)

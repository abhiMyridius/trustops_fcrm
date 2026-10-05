"""fcrm_identity.jwtkeys: loading the one RS256 signing keypair."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fcrm_identity.jwtkeys import SigningKey, SigningKeyError


def _write_pem(tmp_path: Path, key, name: str = "key.pem") -> str:
    path = tmp_path / name
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(path)


def test_from_file_loads_an_rsa_key(tmp_path: Path):
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signing_key = SigningKey.from_file(_write_pem(tmp_path, rsa_key))
    assert signing_key.private_key.key_size == 2048


def test_kid_is_stable_across_loads(tmp_path: Path):
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = _write_pem(tmp_path, rsa_key)
    first = SigningKey.from_file(path)
    second = SigningKey.from_file(path)
    assert first.kid == second.kid
    assert len(first.kid) == 16


def test_kid_differs_between_keys():
    a = SigningKey(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    b = SigningKey(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    assert a.kid != b.kid


def test_from_file_missing_file_raises(tmp_path: Path):
    with pytest.raises(SigningKeyError, match="not found"):
        SigningKey.from_file(str(tmp_path / "nope.pem"))


def test_from_file_rejects_non_rsa_key(tmp_path: Path):
    ec_key = ec.generate_private_key(ec.SECP256R1())
    path = _write_pem(tmp_path, ec_key)
    with pytest.raises(SigningKeyError, match="RSA"):
        SigningKey.from_file(path)


def test_from_file_rejects_garbage(tmp_path: Path):
    path = tmp_path / "garbage.pem"
    path.write_text("not a pem file at all")
    with pytest.raises(SigningKeyError, match="valid"):
        SigningKey.from_file(str(path))

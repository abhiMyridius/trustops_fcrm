"""fcrm_identity.security: bcrypt password verification."""

from __future__ import annotations

import bcrypt
from fcrm_identity.security import DUMMY_HASH, verify_password


def test_verify_password_accepts_the_matching_password():
    password_hash = bcrypt.hashpw(b"correct horse", bcrypt.gensalt()).decode("utf-8")
    assert verify_password("correct horse", password_hash) is True


def test_verify_password_rejects_the_wrong_password():
    password_hash = bcrypt.hashpw(b"correct horse", bcrypt.gensalt()).decode("utf-8")
    assert verify_password("wrong password", password_hash) is False


def test_verify_password_rejects_against_a_malformed_hash():
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_dummy_hash_never_matches_a_real_looking_password():
    assert verify_password("password123", DUMMY_HASH) is False
    assert verify_password("", DUMMY_HASH) is False

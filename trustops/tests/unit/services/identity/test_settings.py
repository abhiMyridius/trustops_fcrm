"""fcrm_identity.settings: the fail-fast contract for the two secrets the
cloned ``evoqidentityservice`` used to default silently (``JWT_SECRET_KEY``,
bug 3). ``test_missing_secret_fails_fast_at_startup`` in
``test_upstream_fixes.py`` is the named regression test the task asks
for; this file covers the rest of ``IdentitySettings``'s own behaviour.
"""

from __future__ import annotations

import pytest
from fcrm_common.settings import MissingSettingsError
from fcrm_identity.settings import IdentitySettings

ALL_REQUIRED = {
    "MONGODB_URI": "mongodb://mongo:27017/?replicaSet=rs0",
    "MONGODB_DB": "fcrm_identity",
    "SERVICE_TOKEN_SECRET": "x" * 40,
    "OPA_URL": "http://opa:8181",
    "IDENTITY_JWKS_URL": "http://identity:8000/api/fcrm/auth/.well-known/jwks.json",
    "JWT_KEY_FILE": "/keys/jwt.pem",
    "DEMO_USERS_PASSWORD_HASH": "$2b$12$" + "a" * 53,
}
GENERIC_VARS = [
    *ALL_REQUIRED,
    "ENV",
    "DEMO_USERS_ENABLED",
    "IDENTITY_VALIDATE_URL",
    "SEED_PATH",
    "SEED_ON_EMPTY",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in GENERIC_VARS:
        monkeypatch.delenv(name, raising=False)


def test_load_succeeds_with_every_variable_set(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    settings = IdentitySettings.load()
    assert settings.jwt_key_file == "/keys/jwt.pem"
    assert settings.demo_users_password_hash is not None
    assert settings.demo_users_password_hash.get_secret_value().startswith("$2b$")


def test_fail_fast_without_jwt_key_file(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("JWT_KEY_FILE")
    with pytest.raises(MissingSettingsError) as info:
        IdentitySettings.load()
    assert "JWT_KEY_FILE" in info.value.missing


def test_fail_fast_without_demo_users_password_hash(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DEMO_USERS_PASSWORD_HASH")
    with pytest.raises(MissingSettingsError) as info:
        IdentitySettings.load()
    assert "DEMO_USERS_PASSWORD_HASH" in info.value.missing


def test_jwt_defaults_match_docker_compose(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    settings = IdentitySettings.load()
    assert settings.jwt_access_ttl_s == 900
    assert settings.jwt_refresh_ttl_s == 28800
    assert settings.jwt_issuer == "fcrm-identity"
    assert settings.jwt_audience == "fcrm"
    assert settings.refresh_cookie_name == "fcrm_refresh"


def test_demo_users_are_off_by_default(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    settings = IdentitySettings.load()
    assert settings.demo_users_enabled is False
    assert settings.demo_login_active is False


@pytest.mark.parametrize(
    ("flag", "env", "active"),
    [
        ("true", "local", True),
        ("true", "test", True),
        ("true", "ci", True),
        ("true", "prod", False),
        ("true", "staging", False),
        ("false", "local", False),
        ("", "local", False),
    ],
)
def test_demo_login_needs_both_the_flag_and_a_local_env(
    monkeypatch, flag: str, env: str, active: bool
):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("DEMO_USERS_ENABLED", flag)
    settings = IdentitySettings.load()
    assert settings.demo_login_active is active

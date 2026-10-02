"""fcrm_common.settings: env contract and fail-fast (design A §1.4, §11.4)."""

from __future__ import annotations

import pydantic
import pytest
from fcrm_common.settings import BaseServiceSettings, MissingSettingsError

ALL_REQUIRED = {
    "MONGODB_URI": "mongodb://mongo:27017/?replicaSet=rs0",
    "MONGODB_DB": "fcrm_case",
    "SERVICE_TOKEN_SECRET": "x" * 40,
    "OPA_URL": "http://opa:8181",
    "IDENTITY_JWKS_URL": "http://identity:8000/.well-known/jwks.json",
}
GENERIC_VARS = [
    "APP_NAME",
    "APP_ROOT_PATH",
    "ENV",
    "LOG_LEVEL",
    "MONGODB_URI",
    "MONGODB_DB",
    "RABBITMQ_URL",
    "REDIS_URL",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "IDENTITY_JWKS_URL",
    "IDENTITY_VALIDATE_URL",
    "OPA_URL",
    "SERVICE_TOKEN_SECRET",
    "SEED_PATH",
    "SEED_ON_EMPTY",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in GENERIC_VARS:
        monkeypatch.delenv(name, raising=False)


def test_fail_fast_lists_every_missing_variable():
    with pytest.raises(MissingSettingsError) as info:
        BaseServiceSettings.load()
    assert info.value.missing == [
        "MONGODB_URI",
        "MONGODB_DB",
        "SERVICE_TOKEN_SECRET",
        "OPA_URL",
        "IDENTITY_JWKS_URL or IDENTITY_VALIDATE_URL",
    ]
    message = str(info.value)
    for name in ("MONGODB_URI", "SERVICE_TOKEN_SECRET", "OPA_URL"):
        assert name in message


def test_fail_fast_with_one_missing_variable_names_only_it(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SERVICE_TOKEN_SECRET")
    with pytest.raises(MissingSettingsError) as info:
        BaseServiceSettings.load()
    assert info.value.missing == ["SERVICE_TOKEN_SECRET"]


def test_fail_fast_treats_blank_value_as_missing(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OPA_URL", "   ")
    with pytest.raises(MissingSettingsError) as info:
        BaseServiceSettings.load()
    assert info.value.missing == ["OPA_URL"]


def test_load_reads_environment_when_complete(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SEED_ON_EMPTY", "true")
    monkeypatch.setenv("APP_NAME", "case-service")
    settings = BaseServiceSettings.load()
    assert settings.mongodb_db == "fcrm_case"
    assert settings.seed_on_empty is True
    assert settings.app_name == "case-service"
    assert settings.service_token_secret is not None
    assert "x" * 40 not in repr(settings)  # secrets never in repr


def test_validate_url_satisfies_identity_group(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("IDENTITY_JWKS_URL")
    monkeypatch.setenv("IDENTITY_VALIDATE_URL", "http://identity:8000/core/validate")
    BaseServiceSettings.load()


def test_fail_fast_rejects_short_secret_outside_local(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "short")
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(MissingSettingsError) as info:
        BaseServiceSettings.load()
    assert info.value.missing == []
    assert info.value.invalid[0].startswith("SERVICE_TOKEN_SECRET")


def test_short_secret_allowed_locally(monkeypatch):
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "short")
    assert BaseServiceSettings.load().is_local


def test_settings_forbid_unknown_init_fields():
    with pytest.raises(pydantic.ValidationError):
        BaseServiceSettings(not_a_setting="x")  # type: ignore[call-arg]


def test_subclass_extends_required_settings(monkeypatch):
    class ScoringSettings(BaseServiceSettings):
        required_settings = (*BaseServiceSettings.required_settings, "SEED_PATH")

    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(MissingSettingsError) as info:
        ScoringSettings.load()
    assert info.value.missing == ["SEED_PATH"]

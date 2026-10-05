"""fcrm_common.service: the bundle a host hands each co-hosted service."""

from __future__ import annotations

import pytest
from fcrm_common import mongo
from fcrm_common.service import ServiceDeps, standalone_deps
from fcrm_common.settings import BaseServiceSettings, MissingSettingsError

URI = "mongodb://localhost:1/?replicaSet=rs0"


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mongo, "_REGISTRY", mongo._ClientRegistry())
    for name in ("MONGODB_URI", "MONGODB_DB", "OPA_URL", "SERVICE_TOKEN_SECRET"):
        monkeypatch.delenv(name, raising=False)


def _settings(**values: object) -> BaseServiceSettings:
    base: dict[str, object] = {
        "mongodb_uri": URI,
        "mongodb_db": "fcrm_x",
        "opa_url": "http://opa:8181",
        "service_token_secret": "x" * 40,
        "identity_jwks_url": "http://identity:8000/jwks.json",
    }
    return BaseServiceSettings(**{**base, **values})


async def test_standalone_deps_yield_the_process_clients_and_close_them():
    settings = _settings()
    async with standalone_deps(settings) as deps:
        assert isinstance(deps, ServiceDeps)
        assert deps.mongo is mongo.get_client(URI)
        assert URI in mongo._REGISTRY.clients
    assert URI not in mongo._REGISTRY.clients


async def test_standalone_deps_close_everything_when_the_body_raises():
    settings = _settings()
    with pytest.raises(RuntimeError, match="boom"):
        async with standalone_deps(settings):
            raise RuntimeError("boom")
    assert mongo._REGISTRY.clients == {}


@pytest.mark.parametrize(
    ("missing_field", "env_name"),
    [("mongodb_uri", "MONGODB_URI"), ("opa_url", "OPA_URL")],
)
async def test_standalone_deps_name_the_missing_setting(
    missing_field: str, env_name: str
):
    settings = _settings(**{missing_field: None})
    with pytest.raises(MissingSettingsError) as info:
        async with standalone_deps(settings):
            pytest.fail("must not start without " + env_name)
    assert info.value.missing == [env_name]

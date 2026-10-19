"""H2's tool registry wiring (ADR-27): ``build_unit`` builds the HTTP-backed
registry by default (S1 and S3 through service-account clients, the canonical
bucket through the object store), the seed-backed registry stays available
through the injectable ``registry=`` argument, and the unit's shutdown hook
closes the clients it opened."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from faststream.rabbit import RabbitBroker
from fcrm_agent_worker import app as app_module
from fcrm_agent_worker.app import build_unit
from fcrm_agent_worker.settings import AgentWorkerSettings
from fcrm_common.broker import FastStreamPublisher
from fcrm_common.http import ServiceClient
from fcrm_common.mongo import get_client
from fcrm_common.settings import MissingSettingsError
from fcrm_tools import (
    TOOL_NAMES,
    HttpToolRegistry,
    ToolClients,
    ToolRegistry,
    build_http_registry,
)
from fcrm_tools.http_backed import WIRED_TOOLS

MINIMAL_TIERS_YAML = """\
schema_version: 1
provider: bedrock
tiers:
  cheap:
    primary: us.anthropic.claude-haiku-4-5-v1:0
    fallbacks: []
"""


class _RecordingConsumer:
    """Stands in for ``AgentJobConsumer``: keeps what ``build_unit`` gave it."""

    instances: list[_RecordingConsumer] = []  # noqa: RUF012 - test double

    def __init__(self, *_args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.instances.append(self)


@pytest.fixture(autouse=True)
def consumers(monkeypatch: pytest.MonkeyPatch) -> list[_RecordingConsumer]:
    monkeypatch.delenv("FCRM_LLM_MODE", raising=False)
    _RecordingConsumer.instances = []
    monkeypatch.setattr(app_module, "AgentJobConsumer", _RecordingConsumer)
    return _RecordingConsumer.instances


def _settings(ai_root: Path, **overrides: Any) -> AgentWorkerSettings:
    models = ai_root / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "tiers.yaml").write_text(MINIMAL_TIERS_YAML, encoding="utf-8")
    values: dict[str, Any] = {
        "mongodb_uri": "mongodb://localhost:27017/?replicaSet=rs0",
        "mongodb_db": "fcrm_harness",
        "service_token_secret": "x" * 32,
        "opa_url": "http://localhost:8181",
        "identity_jwks_url": "http://localhost:8000/.well-known/jwks.json",
        "rabbitmq_url": "amqp://guest:guest@localhost:5672/",
        "case_service_url": "http://api:8000/api/fcrm",
        "scoring_service_url": "http://api:8000/api/fcrm/scoring",
        "policy_service_url": "http://api:8000/api/fcrm/policy",
        "audit_service_url": "http://api:8000/api/fcrm/audit",
        "decision_service_url": "http://api:8000/api/fcrm/decision",
        "prompts_dir": str(ai_root),
        "s3_endpoint_url": "http://minio:9000",
        "s3_access_key": "minio",
        "s3_secret_key": "minio-secret",
        "s3_bucket_canonical": "canonical-test",
    }
    values.update(overrides)
    return AgentWorkerSettings(**values)


def _unit(settings: AgentWorkerSettings, **kwargs: Any) -> Any:
    return build_unit(
        settings,
        mongo=get_client(settings.mongodb_uri or ""),
        publisher=FastStreamPublisher(RabbitBroker("amqp://guest:guest@localhost/")),
        **kwargs,
    )


def test_the_default_registry_is_the_http_backed_one(
    tmp_path: Path, consumers: list[_RecordingConsumer]
) -> None:
    _unit(_settings(tmp_path))
    registry = consumers[0].kwargs["registry"]
    assert isinstance(registry, HttpToolRegistry)
    assert registry.names() == WIRED_TOOLS
    assert set(registry.names()) == set(TOOL_NAMES)
    assert registry.get("policy_search").name == "policy_search"


def test_the_tool_clients_and_the_object_store_come_from_the_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consumers: list[_RecordingConsumer],
) -> None:
    created: dict[str, Any] = {}
    built: dict[str, Any] = {}
    real_create = ToolClients.create

    def spy_create(**kwargs: Any) -> ToolClients:
        created.update(kwargs)
        return real_create(**kwargs)

    def spy_build(clients: ToolClients, store: Any, **kwargs: Any) -> ToolRegistry:
        built.update(kwargs, clients=clients, store=store)
        return build_http_registry(clients, store, **kwargs)

    monkeypatch.setattr(ToolClients, "create", staticmethod(spy_create))
    monkeypatch.setattr(app_module, "build_http_registry", spy_build)
    _unit(_settings(tmp_path))
    assert created["case_url"] == "http://api:8000/api/fcrm"
    assert created["policy_url"] == "http://api:8000/api/fcrm/policy"
    assert created["audit_url"] == "http://api:8000/api/fcrm/audit"
    assert created["caller"] == "fcrm-agent-worker"
    assert created["token_secret"] == "x" * 32
    assert isinstance(created["audit_client"], ServiceClient)
    assert built["canonical_bucket"] == "canonical-test"
    assert built["clients"].audit is created["audit_client"]


def test_an_injected_registry_is_used_and_no_tool_client_is_built(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_registry: ToolRegistry,
    consumers: list[_RecordingConsumer],
) -> None:
    def forbidden(**_kwargs: Any) -> ToolClients:
        raise AssertionError(
            "ToolClients.create must not run with an injected registry"
        )

    monkeypatch.setattr(ToolClients, "create", staticmethod(forbidden))
    _unit(_settings(tmp_path), registry=seed_registry)
    assert consumers[0].kwargs["registry"] is seed_registry
    assert len(seed_registry) == 13  # the seed registry still has every tool


@pytest.mark.parametrize("missing", ["case_service_url", "policy_service_url"])
def test_the_default_registry_needs_the_s1_and_s3_urls(
    tmp_path: Path, missing: str
) -> None:
    settings = _settings(tmp_path, **{missing: None})
    with pytest.raises(MissingSettingsError) as raised:
        _unit(settings)
    assert missing.upper() in raised.value.missing


def test_an_injected_registry_does_not_need_the_s1_and_s3_urls(
    tmp_path: Path, seed_registry: ToolRegistry
) -> None:
    settings = _settings(tmp_path, case_service_url=None, policy_service_url=None)
    assert _unit(settings, registry=seed_registry).component_id == "H2"


async def test_the_shutdown_hook_closes_the_tool_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str] = []

    async def record_close(self: ServiceClient) -> None:
        closed.append(self.dependency)

    monkeypatch.setattr(ServiceClient, "aclose", record_close)
    unit = _unit(_settings(tmp_path))
    # The relay stops first, then the HTTP clients close.
    assert len(unit.on_shutdown) == 2
    await unit.on_shutdown[-1]()
    assert sorted(closed) == ["audit", "case", "policy"]


async def test_the_shutdown_hook_closes_the_audit_client_with_an_injected_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed_registry: ToolRegistry
) -> None:
    closed: list[str] = []

    async def record_close(self: ServiceClient) -> None:
        closed.append(self.dependency)

    monkeypatch.setattr(ServiceClient, "aclose", record_close)
    unit = _unit(_settings(tmp_path), registry=seed_registry)
    await unit.on_shutdown[-1]()
    assert closed == ["audit"]


def test_the_object_store_settings_have_the_compose_defaults() -> None:
    settings = AgentWorkerSettings()
    assert settings.s3_bucket_canonical == "canonical"
    assert settings.s3_region == "us-east-1"
    assert settings.s3_endpoint_url is None


def test_the_s3_variables_are_part_of_the_required_contract() -> None:
    required = AgentWorkerSettings.required_settings
    assert "S3_BUCKET_CANONICAL" in required
    assert "S3_REGION" in required

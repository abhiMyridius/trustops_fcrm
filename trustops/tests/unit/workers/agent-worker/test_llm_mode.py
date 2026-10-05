"""ADR-27: agents call live Bedrock only. ``FCRM_LLM_MODE`` is read once, in
``fcrm_agent_worker.app.build_unit``; anything but ``live`` (any case; unset or
empty also means live) stops the worker from starting, naming the variable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from faststream.rabbit import RabbitBroker
from fcrm_agent_worker.app import build_app, build_unit, require_live_llm_mode
from fcrm_agent_worker.settings import AgentWorkerSettings
from fcrm_common.broker import FastStreamPublisher
from fcrm_common.mongo import get_client
from fcrm_common.settings import MissingSettingsError

MINIMAL_TIERS_YAML = """\
schema_version: 1
provider: bedrock
tiers:
  cheap:
    primary: us.anthropic.claude-haiku-4-5-v1:0
    fallbacks: []
"""


@pytest.fixture(autouse=True)
def no_llm_mode_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FCRM_LLM_MODE", raising=False)


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
        "case_service_url": "http://localhost:8001",
        "scoring_service_url": "http://localhost:8002",
        "policy_service_url": "http://localhost:8003",
        "audit_service_url": "http://localhost:8004",
        "decision_service_url": "http://localhost:8005",
        "prompts_dir": str(ai_root),
    }
    values.update(overrides)
    return AgentWorkerSettings(**values)


@pytest.mark.parametrize("mode", ["replay", "mock", "REPLAY", "offline", "livee"])
def test_a_mode_other_than_live_stops_the_worker_naming_the_variable(
    tmp_path: Path, mode: str
) -> None:
    with pytest.raises(MissingSettingsError) as raised:
        build_app(_settings(tmp_path, fcrm_llm_mode=mode))

    assert raised.value.missing == []
    [problem] = raised.value.invalid
    assert problem.startswith("FCRM_LLM_MODE:")
    assert repr(mode) in problem
    assert "live Bedrock only" in problem
    assert "FCRM_LLM_MODE" in str(raised.value)


@pytest.mark.parametrize("mode", ["live", "LIVE", "Live", " live ", ""])
def test_live_in_any_case_or_empty_starts_the_worker(tmp_path: Path, mode: str) -> None:
    require_live_llm_mode(_settings(tmp_path, fcrm_llm_mode=mode))
    assert build_app(_settings(tmp_path, fcrm_llm_mode=mode)) is not None


def test_unset_means_live(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.fcrm_llm_mode == "live"
    require_live_llm_mode(settings)


def test_the_value_is_read_from_the_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FCRM_LLM_MODE", "replay")
    settings = _settings(tmp_path)
    assert settings.fcrm_llm_mode == "replay"
    with pytest.raises(MissingSettingsError, match="FCRM_LLM_MODE"):
        require_live_llm_mode(settings)


def test_an_empty_environment_variable_means_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FCRM_LLM_MODE", "")
    require_live_llm_mode(_settings(tmp_path))


def test_build_unit_checks_the_mode_before_anything_else(tmp_path: Path) -> None:
    """The mode is the first thing ``build_unit`` looks at: a replay setting
    is reported even when other configuration would also have failed, and no
    client is built for a worker that will not start.
    """
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    settings = _settings(tmp_path, fcrm_llm_mode="replay", mongodb_db=None)
    with pytest.raises(MissingSettingsError, match="FCRM_LLM_MODE"):
        build_unit(
            settings,
            mongo=get_client("mongodb://localhost:27017/?replicaSet=rs0"),
            publisher=FastStreamPublisher(broker),
        )


def test_build_unit_with_live_mode_still_builds(tmp_path: Path) -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    unit = build_unit(
        _settings(tmp_path),
        mongo=get_client("mongodb://localhost:27017/?replicaSet=rs0"),
        publisher=FastStreamPublisher(broker),
    )
    assert unit.component_id == "H2"

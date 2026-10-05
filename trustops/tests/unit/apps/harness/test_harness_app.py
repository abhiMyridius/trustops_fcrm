"""fcrm_harness: one broker, one Mongo client and one health app host H1 and H2."""

from __future__ import annotations

from pathlib import Path

import pytest
from faststream.asgi import AsgiFastStream
from faststream.rabbit import RabbitBroker
from fcrm_agents.models import ModelConfigError
from fcrm_common.broker import FastStreamPublisher
from fcrm_common.mongo import get_client
from fcrm_common.settings import MissingSettingsError
from fcrm_harness.app import build_app, build_units
from fcrm_harness.settings import HarnessSettings

MINIMAL_TIERS_YAML = """\
schema_version: 1
provider: bedrock
tiers:
  cheap:
    primary: us.anthropic.claude-haiku-4-5-v1:0
    fallbacks: []
"""

ENV_NAMES = (
    "MONGODB_URI",
    "MONGODB_DB",
    "RABBITMQ_URL",
    "SERVICE_TOKEN_SECRET",
    "OPA_URL",
    "IDENTITY_JWKS_URL",
    "IDENTITY_VALIDATE_URL",
    "CASE_SERVICE_URL",
    "SCORING_SERVICE_URL",
    "POLICY_SERVICE_URL",
    "AUDIT_SERVICE_URL",
    "DECISION_SERVICE_URL",
    "CASEFLOW_PREFETCH",
    "AGENT_PREFETCH",
    "S3_ENDPOINT_URL",
    "S3_REGION",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "S3_BUCKET_UPLOADS",
    "S3_BUCKET_CANONICAL",
    "S3_BUCKET_PACKS",
    "S3_BUCKET_VECTORS",
    "S3_BUCKET_JOBS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings(ai_root: Path, **overrides: object) -> HarnessSettings:
    models = ai_root / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "tiers.yaml").write_text(MINIMAL_TIERS_YAML, encoding="utf-8")
    values: dict[str, object] = {
        "mongodb_uri": "mongodb://localhost:27017/?replicaSet=rs0",
        "service_token_secret": "x" * 32,
        "opa_url": "http://localhost:8181",
        "identity_jwks_url": "http://localhost:8000/.well-known/jwks.json",
        "rabbitmq_url": "amqp://guest:guest@localhost:5672/",
        "case_service_url": "http://localhost:8000/api/fcrm",
        "scoring_service_url": "http://localhost:8000/api/fcrm/scoring",
        "policy_service_url": "http://localhost:8000/api/fcrm/policy",
        "audit_service_url": "http://localhost:8000/api/fcrm/audit",
        "decision_service_url": "http://localhost:8000/api/fcrm/decision",
        "prompts_dir": str(ai_root),
        **overrides,
    }
    return HarnessSettings(**values)


def test_unit_settings_are_derived_with_their_own_prefetch(tmp_path: Path):
    settings = _settings(tmp_path, caseflow_prefetch=16, agent_prefetch=4)
    assert settings.caseflow_settings().prefetch == 16
    agent = settings.agent_worker_settings()
    assert agent.prefetch == 4
    assert agent.prompts_dir == str(tmp_path)
    assert agent.decision_service_url == "http://localhost:8000/api/fcrm/decision"


def test_the_object_store_settings_reach_h2(tmp_path: Path):
    """``document_reader`` reads the anchored text H3 wrote, so H2 needs the
    harness's object-store variables: endpoint, region, keys and every bucket."""
    settings = _settings(
        tmp_path,
        s3_endpoint_url="http://minio:9000",
        s3_region="eu-west-1",
        s3_access_key="minio",
        s3_secret_key="minio-secret",
        s3_bucket_canonical="canonical-eu",
        s3_bucket_uploads="uploads-eu",
    )
    agent = settings.agent_worker_settings()
    assert agent.s3_endpoint_url == "http://minio:9000"
    assert agent.s3_region == "eu-west-1"
    assert agent.s3_access_key == "minio"
    assert agent.s3_secret_key is not None
    assert agent.s3_secret_key.get_secret_value() == "minio-secret"
    assert agent.s3_bucket_canonical == "canonical-eu"
    assert agent.s3_bucket_uploads == "uploads-eu"
    assert agent.s3_bucket_packs == "packs"


def test_the_object_store_defaults_are_the_compose_bucket_names(tmp_path: Path):
    agent = _settings(tmp_path).agent_worker_settings()
    assert agent.s3_endpoint_url is None
    assert (
        agent.s3_bucket_uploads,
        agent.s3_bucket_canonical,
        agent.s3_bucket_packs,
        agent.s3_bucket_vectors,
        agent.s3_bucket_jobs,
    ) == ("uploads", "canonical", "packs", "vectors", "jobs")


def test_the_harness_database_is_shared_by_every_unit(tmp_path: Path):
    settings = _settings(tmp_path)
    assert settings.caseflow_settings().mongodb_db == "fcrm_harness"
    assert settings.agent_worker_settings().mongodb_db == "fcrm_harness"


def test_a_missing_service_url_fails_fast_naming_it():
    with pytest.raises(MissingSettingsError) as info:
        HarnessSettings.load(
            mongodb_uri="mongodb://localhost:27017/?replicaSet=rs0",
            service_token_secret="x" * 32,
            opa_url="http://localhost:8181",
            identity_jwks_url="http://localhost:8000/jwks.json",
            rabbitmq_url="amqp://guest:guest@localhost:5672/",
        )
    assert "AUDIT_SERVICE_URL" in info.value.missing


def test_build_app_wires_every_queue_onto_one_broker(tmp_path: Path):
    app = build_app(_settings(tmp_path, caseflow_prefetch=16, agent_prefetch=4))
    assert isinstance(app, AsgiFastStream)
    by_queue = {sub.queue.name: sub for sub in app.broker.subscribers}
    assert set(by_queue) == {
        "caseflow.events",
        "caseflow.agent-results",
        "caseflow.steps",
        "agent-worker.run",
    }
    assert by_queue["caseflow.events"].channel.prefetch_count == 16
    assert by_queue["caseflow.agent-results"].channel.prefetch_count == 16
    assert by_queue["caseflow.steps"].channel.prefetch_count == 4
    assert by_queue["agent-worker.run"].channel.prefetch_count == 4


def test_the_units_contribute_their_startup_shutdown_hooks_and_checks(
    tmp_path: Path,
):
    settings = _settings(tmp_path)
    units = build_units(
        settings,
        mongo=get_client("mongodb://localhost:27017/?replicaSet=rs0"),
        publisher=FastStreamPublisher(RabbitBroker("amqp://guest:guest@localhost/")),
    )
    by_id = {unit.component_id: unit for unit in units}
    assert list(by_id) == ["H1", "H2"]
    # H1: index creation then the outbox relay; its relay and audit client stop.
    assert len(by_id["H1"].after_startup) == 2
    assert len(by_id["H1"].on_shutdown) == 2
    assert [check.name for check in by_id["H1"].checks] == ["indexes"]
    # H2: nothing to start; it closes its OPA and audit clients and probes OPA.
    assert by_id["H2"].after_startup == ()
    assert len(by_id["H2"].on_shutdown) == 1  # OPA, audit and tool clients, together
    assert [check.name for check in by_id["H2"].checks] == ["opa"]


def test_build_app_raises_a_clear_error_when_the_ai_root_has_no_tiers_file(
    tmp_path: Path,
):
    settings = _settings(tmp_path)
    (tmp_path / "models" / "tiers.yaml").unlink()
    with pytest.raises(ModelConfigError, match="tiers.yaml"):
        build_app(settings)

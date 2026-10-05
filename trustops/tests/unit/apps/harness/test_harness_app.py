"""fcrm_harness: one broker, one Mongo client and one health app host H1 to H4."""

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
    "INGESTION_PREFETCH",
    "CONTEXT_PREFETCH",
    "CONTEXT_BUILD_TIMEOUT_SECONDS",
    "VECTOR_BACKEND",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "UPLOADS_PER_FILE_TIMEOUT_SECONDS",
    "PDF_ENGINE",
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
    assert settings.ingestion_settings().mongodb_db == "fcrm_harness"
    assert settings.context_settings().mongodb_db == "fcrm_harness"


def test_h1_gets_the_peer_urls_and_the_canonical_bucket_it_names_in_ingest_runs(
    tmp_path: Path,
):
    settings = _settings(tmp_path, s3_bucket_canonical="canonical-eu")
    caseflow = settings.caseflow_settings()
    assert caseflow.case_service_url == "http://localhost:8000/api/fcrm"
    assert caseflow.policy_service_url == "http://localhost:8000/api/fcrm/policy"
    assert caseflow.s3_bucket_canonical == "canonical-eu"


def test_h3_settings_are_derived_with_the_object_store_and_its_own_knobs(
    tmp_path: Path,
):
    settings = _settings(
        tmp_path,
        ingestion_prefetch=3,
        uploads_per_file_timeout_seconds=45,
        pdf_engine="pypdf",
        pdf_min_chars_per_page=10,
        s3_endpoint_url="http://minio:9000",
        s3_bucket_uploads="uploads-eu",
    )

    ingestion = settings.ingestion_settings()

    assert ingestion.prefetch == 3
    assert ingestion.uploads_per_file_timeout_seconds == 45
    assert (ingestion.pdf_engine, ingestion.pdf_min_chars_per_page) == ("pypdf", 10)
    assert ingestion.s3_endpoint_url == "http://minio:9000"
    assert ingestion.s3_bucket_uploads == "uploads-eu"
    assert ingestion.s3_bucket_canonical == "canonical"


def test_the_ingestion_prefetch_defaults_to_two(tmp_path: Path):
    assert _settings(tmp_path).ingestion_prefetch == 2


def test_h4_settings_are_derived_with_s3_the_object_store_and_its_own_knobs(
    tmp_path: Path,
):
    settings = _settings(
        tmp_path,
        context_prefetch=2,
        context_build_timeout_seconds=90,
        s3_endpoint_url="http://minio:9000",
        s3_bucket_vectors="vectors-eu",
    )

    context = settings.context_settings()

    assert context.context_prefetch == 2
    assert context.context_build_timeout_seconds == 90
    assert context.policy_service_url == "http://localhost:8000/api/fcrm/policy"
    assert context.s3_endpoint_url == "http://minio:9000"
    assert context.s3_bucket_vectors == "vectors-eu"
    assert context.service_token_secret is not None


def test_the_context_prefetch_defaults_to_four(tmp_path: Path):
    assert _settings(tmp_path).context_prefetch == 4


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
        "ingestion-worker.run",
        "context-worker.run",
    }
    assert by_queue["caseflow.events"].channel.prefetch_count == 16
    assert by_queue["caseflow.agent-results"].channel.prefetch_count == 16
    assert by_queue["caseflow.steps"].channel.prefetch_count == 4
    assert by_queue["agent-worker.run"].channel.prefetch_count == 4
    assert by_queue["ingestion-worker.run"].channel.prefetch_count == 2
    assert by_queue["context-worker.run"].channel.prefetch_count == 4


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
    assert list(by_id) == ["H1", "H2", "H3", "H4"]
    # H1: index creation then the outbox relay; its relay stops and it closes
    # its audit, S1, S3, S2 and S5 clients.
    assert len(by_id["H1"].after_startup) == 2
    assert len(by_id["H1"].on_shutdown) == 6
    assert [check.name for check in by_id["H1"].checks] == ["indexes"]
    # H2: nothing to start; it closes its OPA and audit clients and probes OPA.
    assert by_id["H2"].after_startup == ()
    assert len(by_id["H2"].on_shutdown) == 1  # OPA, audit and tool clients, together
    assert [check.name for check in by_id["H2"].checks] == ["opa"]
    # H3: the consumed-message index at startup; it holds no client to close and
    # probes the uploads bucket without making readiness depend on it.
    assert len(by_id["H3"].after_startup) == 1
    assert by_id["H3"].on_shutdown == ()
    [uploads] = by_id["H3"].checks
    assert (uploads.name, uploads.critical) == ("uploads_bucket", False)
    # H4: the consumed-message index at startup; it closes its S3 client and
    # probes the vectors bucket without making readiness depend on it.
    assert len(by_id["H4"].after_startup) == 1
    assert len(by_id["H4"].on_shutdown) == 1
    [vectors] = by_id["H4"].checks
    assert (vectors.name, vectors.critical) == ("vectors_bucket", False)


def test_build_app_raises_a_clear_error_when_the_ai_root_has_no_tiers_file(
    tmp_path: Path,
):
    settings = _settings(tmp_path)
    (tmp_path / "models" / "tiers.yaml").unlink()
    with pytest.raises(ModelConfigError, match="tiers.yaml"):
        build_app(settings)

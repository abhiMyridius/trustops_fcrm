"""fcrm_harness: one broker, one Mongo client and one health app host H1 to H4."""

from __future__ import annotations

from pathlib import Path

import pytest
from faststream.asgi import AsgiFastStream
from faststream.rabbit import RabbitBroker
from fcrm_agents.models import ModelConfigError
from fcrm_caseflow.settings import DEFAULT_TRACE_UI_URL
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


def test_the_case_token_cap_is_250k_and_reaches_h1(tmp_path: Path):
    assert _settings(tmp_path).case_token_cap == 250_000
    assert _settings(tmp_path).caseflow_settings().case_token_cap == 250_000
    lowered = _settings(tmp_path, case_token_cap=30_000)
    assert lowered.caseflow_settings().case_token_cap == 30_000


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
    # its audit, S1, S3, S2 and S5 clients (S5 serves both of its roles).
    assert len(by_id["H1"].after_startup) == 2
    assert len(by_id["H1"].on_shutdown) == 6
    assert [check.name for check in by_id["H1"].checks] == ["indexes", "outbox"]
    # H2 (ADR-79): its job/outbox indexes then its own outbox relay; the relay
    # stops, then its OPA, audit and tool clients close together. Its checks are
    # named apart from H1's, since the probe keeps one check per name.
    assert len(by_id["H2"].after_startup) == 2
    assert len(by_id["H2"].on_shutdown) == 2
    assert [check.name for check in by_id["H2"].checks] == [
        "agent_indexes",
        "agent_outbox",
        "opa",
    ]
    names = [check.name for unit in units for check in unit.checks]
    assert len(names) == len(set(names))
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


def test_both_bedrock_regions_reach_h2(tmp_path: Path):
    """H2 decides whether a second region adds anything by comparing the two, so
    the harness hands it the primary region as well as the fallback."""
    agent = _settings(
        tmp_path, aws_region="us-east-1", bedrock_region_fallback="us-west-2"
    ).agent_worker_settings()
    assert agent.aws_region == "us-east-1"
    assert agent.bedrock_region_fallback == "us-west-2"


def test_the_chunking_settings_reach_h2(tmp_path: Path) -> None:
    """``AGENT_MAX_PARTS``, ``AGENT_REDUCE_TIER`` and
    ``AGENT_OUTPUT_FLOOR_TOKENS`` are threaded like ``AGENT_MAX_TOOL_CALLS``;
    the defaults are 4, cheap and 1024."""
    defaults = _settings(tmp_path).agent_worker_settings()
    assert (
        defaults.agent_max_parts,
        defaults.agent_reduce_tier,
        defaults.agent_output_floor_tokens,
    ) == (4, "cheap", 1024)
    agent = _settings(
        tmp_path,
        agent_max_parts=6,
        agent_reduce_tier="mid",
        agent_output_floor_tokens=2048,
    ).agent_worker_settings()
    assert (
        agent.agent_max_parts,
        agent.agent_reduce_tier,
        agent.agent_output_floor_tokens,
    ) == (6, "mid", 2048)


def test_an_unknown_reduce_tier_is_refused(tmp_path: Path) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="AGENT_REDUCE_TIER"):
        _settings(tmp_path, agent_reduce_tier="opus").agent_worker_settings()


def test_the_trace_link_url_reaches_h1_and_empty_means_the_default(
    tmp_path: Path,
) -> None:
    team = "https://o2.example.com/web/traces?org_identifier=team"
    assert (
        _settings(tmp_path, trace_ui_url=team).caseflow_settings().trace_ui_url == team
    )
    assert (
        _settings(tmp_path, trace_ui_url="").caseflow_settings().trace_ui_url
        == DEFAULT_TRACE_UI_URL
    )


async def test_a_failing_startup_hook_does_not_stop_the_other_units() -> None:
    """Seen live on 19 October: H2's first startup hook raised (an index
    conflict) and the harness skipped every hook after it, so neither outbox
    relay started and no case moved."""
    from fcrm_common.worker import WorkerUnit
    from fcrm_harness.app import _unit_startup

    ran: list[str] = []

    async def fails() -> None:
        ran.append("h2-indexes")
        raise RuntimeError("index conflict")

    async def h2_relay() -> None:
        ran.append("h2-relay")

    async def h1_relay() -> None:
        ran.append("h1-relay")

    h2 = WorkerUnit("H2", wire=lambda broker: None, after_startup=(fails, h2_relay))
    h1 = WorkerUnit("H1", wire=lambda broker: None, after_startup=(h1_relay,))
    for hook in (_unit_startup(h2), _unit_startup(h1)):
        await hook()
    assert ran == ["h2-indexes", "h2-relay", "h1-relay"]

"""Environment mapping: VECTOR_BACKEND (ADR-12) and CONTEXT_SYNTHESIS_ENABLED."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fcrm_context_worker.config import (
    EmbeddingConfigError,
    VectorBackendError,
    context_config_from_env,
    embeddings_from_tiers,
    synthesis_enabled,
    vector_backend,
)

REPO_AI = Path(__file__).resolve().parents[5] / "ai"


def test_vector_backend_defaults_to_memory() -> None:
    assert vector_backend({}) == "memory"


def test_vector_backend_rejects_unknown_name() -> None:
    with pytest.raises(VectorBackendError):
        vector_backend({"VECTOR_BACKEND": "mongo"})


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, False), ("false", False), ("true", True), ("1", True)]
)
def test_synthesis_enabled_defaults_off(raw: str | None, expected: bool) -> None:
    env = {} if raw is None else {"CONTEXT_SYNTHESIS_ENABLED": raw}
    assert synthesis_enabled(env) is expected


def test_context_config_from_env_memory_is_offline(tmp_path: Path) -> None:
    config = context_config_from_env({}, projects_root=tmp_path)
    assert config.vector_backend == "memory"
    assert config.embedding_provider == "dummy"
    assert config.synthesis_enabled is False


def test_context_config_from_env_s3vectors_uses_bedrock(tmp_path: Path) -> None:
    config = context_config_from_env(
        {
            "VECTOR_BACKEND": "s3vectors",
            "S3_VECTORS_BUCKET": "fcrm-vectors",
            "PROMPTS_DIR": str(REPO_AI),
        },
        projects_root=tmp_path,
    )
    assert config.vector_backend == "s3vectors"
    assert config.embedding_provider == "bedrock"
    assert config.s3v_bucket == "fcrm-vectors"


def _tiers() -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(
        (REPO_AI / "models" / "tiers.yaml").read_text(encoding="utf-8")
    )
    return loaded


def test_the_bedrock_embedding_model_and_dimension_come_from_tiers_yaml(
    tmp_path: Path,
) -> None:
    embeddings = _tiers()["embeddings"]
    assert isinstance(embeddings, dict)

    config = context_config_from_env(
        {"EMBEDDING_PROVIDER": "bedrock", "PROMPTS_DIR": str(REPO_AI)},
        projects_root=tmp_path,
    )

    assert config.embedding_provider == "bedrock"
    assert config.embedding_model == embeddings["model"]
    assert config.embedding_dimension == embeddings["dims"]


def test_an_explicit_model_or_dimension_wins_over_tiers_yaml(tmp_path: Path) -> None:
    config = context_config_from_env(
        {
            "EMBEDDING_PROVIDER": "bedrock",
            "EMBEDDING_MODEL": "some-other-embedder",
            "EMBEDDING_DIMENSION": "512",
        },
        projects_root=tmp_path,
    )
    assert config.embedding_model == "some-other-embedder"
    assert config.embedding_dimension == 512


def test_an_empty_model_variable_means_take_it_from_tiers_yaml(tmp_path: Path) -> None:
    """Compose sets ``EMBEDDING_MODEL`` to the empty string when unset."""
    config = context_config_from_env(
        {
            "EMBEDDING_PROVIDER": "bedrock",
            "EMBEDDING_MODEL": "",
            "EMBEDDING_DIMENSION": "1024",
            "PROMPTS_DIR": str(REPO_AI),
        },
        projects_root=tmp_path,
    )
    assert config.embedding_model == embeddings_from_tiers(REPO_AI)[0]


def test_bedrock_without_a_model_or_a_tiers_file_is_an_error_not_a_default(
    tmp_path: Path,
) -> None:
    with pytest.raises(EmbeddingConfigError, match="PROMPTS_DIR"):
        context_config_from_env(
            {"EMBEDDING_PROVIDER": "bedrock", "PROMPTS_DIR": str(tmp_path / "nope")},
            projects_root=tmp_path,
        )


@pytest.mark.parametrize(
    "text",
    ["", ":::not yaml", "embeddings: {}", "embeddings: {model: x}", "[1, 2]"],
)
def test_a_tiers_file_without_an_embedding_model_is_an_error(
    tmp_path: Path, text: str
) -> None:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "tiers.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(EmbeddingConfigError):
        embeddings_from_tiers(tmp_path)


def test_the_dummy_provider_never_reads_tiers_yaml(tmp_path: Path) -> None:
    config = context_config_from_env(
        {"EMBEDDING_PROVIDER": "dummy", "PROMPTS_DIR": str(tmp_path / "nope")},
        projects_root=tmp_path,
    )
    assert config.embedding_provider == "dummy"
    assert config.embedding_dimension == 1024

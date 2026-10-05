"""Environment mapping: VECTOR_BACKEND (ADR-12) and CONTEXT_SYNTHESIS_ENABLED."""

from __future__ import annotations

from pathlib import Path

import pytest
from fcrm_context_worker.config import (
    VectorBackendError,
    context_config_from_env,
    synthesis_enabled,
    vector_backend,
)


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
        {"VECTOR_BACKEND": "s3vectors", "S3_VECTORS_BUCKET": "fcrm-vectors"},
        projects_root=tmp_path,
    )
    assert config.vector_backend == "s3vectors"
    assert config.embedding_provider == "bedrock"
    assert config.s3v_bucket == "fcrm-vectors"

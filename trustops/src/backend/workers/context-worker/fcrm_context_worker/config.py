"""Environment -> ``evoq_context.ContextConfig`` for the H4 worker.

The cloned engine never reads the environment (AST-enforced); this host
module is the one place that does. It is a deliberately small
``os.environ.get`` mapping because ``fcrm_common.settings`` does not exist
yet — migrate these reads to a ``pydantic-settings`` class there when it
lands (tracked in ``UPSTREAM.md``).

Variables (names only; values come from the environment):

* ``VECTOR_BACKEND`` — ``memory`` (default; offline, zero AWS) or
  ``s3vectors`` (ADR-12's switch, the same name across the system).
* ``EMBEDDING_PROVIDER`` — defaults to ``dummy`` (deterministic offline
  vectors) under ``memory`` and ``bedrock`` under ``s3vectors``. Under
  ``bedrock`` the model id and dimension come from ``ai/models/tiers.yaml``
  (``embeddings.model`` and ``embeddings.dims``, found under ``PROMPTS_DIR``,
  default ``ai``) unless ``EMBEDDING_MODEL`` / ``EMBEDDING_DIMENSION`` say
  otherwise: model ids live in that one file, and H4 and S3 read the same one,
  so the vectors one writes are the vectors the other queries. A missing file
  is an error, never a silent fall back to some other model.
* ``CONTEXT_SYNTHESIS_ENABLED`` — ``false`` by default: the AEO
  analysis/synthesis chat calls never run in TrustOps builds.
* ``S3_VECTORS_BUCKET``, ``S3_VECTORS_INDEX_PREFIX``, ``AWS_REGION``,
  ``EMBEDDING_MODEL``, ``EMBEDDING_DIMENSION``, ``PROMPTS_DIR``,
  ``CONTEXT_PROJECTS_ROOT``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

import yaml
from evoq_context.core.config import ContextConfig

logger = logging.getLogger(__name__)

#: The ``project`` every TrustOps vector carries (design A §7.2 step 5).
FCRM_PROJECT = "fcrm"
VECTOR_BACKENDS: frozenset[str] = frozenset({"memory", "s3vectors"})
_TRUE = frozenset({"1", "true", "yes", "on"})


class VectorBackendError(ValueError):
    """``VECTOR_BACKEND`` names a backend H4 does not support."""


class EmbeddingConfigError(ValueError):
    """The embedding model cannot be determined: ``EMBEDDING_PROVIDER=bedrock``
    with no ``EMBEDDING_MODEL`` and no readable ``ai/models/tiers.yaml``.
    """


def embeddings_from_tiers(ai_root: Path) -> tuple[str, int]:
    """``(model id, dimension)`` of the embedding model, from
    ``<ai_root>/models/tiers.yaml`` (the one file that names a model id).

    Raises:
        EmbeddingConfigError: The file is missing, is not valid YAML, or has no
            ``embeddings.model`` and ``embeddings.dims``.
    """
    path = ai_root / "models" / "tiers.yaml"
    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EmbeddingConfigError(
            f"cannot read the embedding model from {path} "
            "(set PROMPTS_DIR to the ai/ root, or EMBEDDING_MODEL)"
        ) from exc
    embeddings = document.get("embeddings") if isinstance(document, dict) else None
    model = embeddings.get("model") if isinstance(embeddings, dict) else None
    dims = embeddings.get("dims") if isinstance(embeddings, dict) else None
    if not isinstance(model, str) or not model or not isinstance(dims, int):
        raise EmbeddingConfigError(
            f"{path} has no embeddings.model and embeddings.dims"
        )
    return model, dims


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    return default if raw is None else raw.strip().lower() in _TRUE


def synthesis_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """``CONTEXT_SYNTHESIS_ENABLED`` (default ``False``)."""
    return _flag(os.environ if env is None else env, "CONTEXT_SYNTHESIS_ENABLED", False)


def vector_backend(env: Optional[Mapping[str, str]] = None) -> str:
    """``VECTOR_BACKEND`` (default ``memory``); anything else is rejected loudly."""
    source = os.environ if env is None else env
    name = source.get("VECTOR_BACKEND", "memory").strip().lower() or "memory"
    if name not in VECTOR_BACKENDS:
        raise VectorBackendError(
            f"VECTOR_BACKEND={name!r}; expected one of {sorted(VECTOR_BACKENDS)}"
        )
    return name


def context_config_from_env(
    env: Optional[Mapping[str, str]] = None, *, projects_root: Optional[Path] = None
) -> ContextConfig:
    """Build the engine config the worker runs with."""
    source = os.environ if env is None else env
    backend = vector_backend(source)
    default_provider = "dummy" if backend == "memory" else "bedrock"
    provider = source.get("EMBEDDING_PROVIDER", default_provider)
    root = projects_root or Path(
        source.get("CONTEXT_PROJECTS_ROOT", "/tmp/fcrm-context")
    )
    model = source.get("EMBEDDING_MODEL", "").strip()
    dimension = int(source.get("EMBEDDING_DIMENSION", "0") or 0)
    if provider == "bedrock" and not model:
        model, tier_dimension = embeddings_from_tiers(
            Path(source.get("PROMPTS_DIR", "ai"))
        )
        dimension = dimension or tier_dimension
    overrides: dict[str, Any] = {"embedding_model": model} if model else {}
    config = ContextConfig(
        projects_root=root,
        vector_backend=backend,
        s3v_bucket=source.get("S3_VECTORS_BUCKET", ""),
        s3v_region=source.get("AWS_REGION", "us-east-1"),
        s3v_index_prefix=source.get("S3_VECTORS_INDEX_PREFIX", "fcrm"),
        embedding_provider=provider,
        embedding_dimension=dimension or 1024,
        synthesis_enabled=synthesis_enabled(source),
        **overrides,
    )
    logger.info(
        "context config: backend=%s embedding_provider=%s synthesis_enabled=%s",
        config.vector_backend,
        config.embedding_provider,
        config.synthesis_enabled,
    )
    return config

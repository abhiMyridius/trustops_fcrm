"""Vector-store backend registry.

Scope & functionality
----------------------
``create_vector_store(config, project)`` is the one place that turns
``ContextConfig.vector_backend`` into a concrete ``VectorStore`` instance.
Backends register themselves with :func:`register_backend` (production
code registers ``"s3vectors"``/``"memory"`` at import time below; a test or
downstream fork can add another without touching this module). Backend
name ``"none"`` disables vector indexing entirely — ``create_vector_store``
returns ``None`` and callers skip the vector-write stage exactly as they
did before this backend seam existed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from evoq_context.vector.interface import VectorStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    from evoq_context.core.config import ContextConfig

_BackendBuilder = Callable[["ContextConfig", str], VectorStore]

_REGISTRY: dict[str, _BackendBuilder] = {}


def register_backend(name: str, builder: _BackendBuilder) -> None:
    """Register a vector-store backend builder under ``name``."""
    _REGISTRY[name.strip().lower()] = builder


def known_backends() -> list[str]:
    return sorted(_REGISTRY) + ["none"]


def create_vector_store(
    config: ContextConfig, project: str
) -> VectorStore | None:
    """Build the configured backend, or ``None`` for ``vector_backend="none"``.

    Raises ``ValueError`` for an unregistered backend name — a typo in
    ``VECTOR_BACKEND`` must fail loudly, not silently disable indexing.
    """
    name = (config.vector_backend or "s3vectors").strip().lower()
    if name == "none":
        return None
    builder = _REGISTRY.get(name)
    if builder is None:
        raise ValueError(
            f"Unknown VECTOR_BACKEND={name!r}; known backends: {known_backends()}"
        )
    return builder(config, project)


def _build_s3vectors(config: ContextConfig, project: str) -> VectorStore:
    from evoq_context.vector.s3_vectors import S3VectorsStore

    return S3VectorsStore(config, project)


def _build_memory(config: ContextConfig, project: str) -> VectorStore:
    from evoq_context.vector.memory_store import InMemoryVectorStore

    return InMemoryVectorStore(config, project)


register_backend("s3vectors", _build_s3vectors)
register_backend("memory", _build_memory)

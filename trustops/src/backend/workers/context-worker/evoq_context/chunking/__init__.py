"""Structure-aware, token-aware, deterministic chunking.

``ChunkPipeline``/``smart_chunk`` (and everything the pipeline façade pulls
in — enrichers, ``topic``, ``parent_child``) are exposed lazily via
module ``__getattr__``: ``core.config.ContextConfig`` imports
``chunking.budget.ChunkBudget`` at module load time, which — being a
submodule import — runs this package's own ``__init__.py`` first; eagerly
importing ``pipeline`` here (which needs ``core.config.ParentChildConfig``/
``TopicConfig``) would close a circular-import loop. Deferring the import
until the name is actually accessed breaks the cycle without changing
either module's public shape.

``chunking/strategies.py`` (the pre-redesign ``smart_chunk`` plus the
``heading_sections``/``paragraph_windows``/``page_windows``/
``slide_units``/``comment_thread`` strategy functions it once re-exported
here) was deleted by lane C-G2: real segmentation goes entirely through
``chunking/segmenters/*.py`` + ``chunking/pipeline.py`` now, and a
repo-wide grep found no caller of those functions left other than this
module's own (now-removed) re-export. ``smart_chunk`` continues to be
exposed here, lazily, from its new home in ``chunking/pipeline.py``.
"""

from __future__ import annotations

from evoq_context.chunking.normalize import normalize_body
from evoq_context.chunking.profiler import DocumentProfile, build_profile
from evoq_context.chunking.selector import StrategyDecision, select_strategy
from evoq_context.chunking.tokens import TokenEstimator, get_estimator
from evoq_context.chunking.types import StructuredChunk

__all__ = [
    "ChunkPipeline",
    "DocumentProfile",
    "StrategyDecision",
    "StructuredChunk",
    "TokenEstimator",
    "build_profile",
    "get_estimator",
    "normalize_body",
    "select_strategy",
    "smart_chunk",
]

_LAZY = {"ChunkPipeline": "ChunkPipeline", "smart_chunk": "smart_chunk"}


def __getattr__(name: str) -> object:  # noqa: ANN401 - PEP 562 lazy module attribute
    """Resolve ``ChunkPipeline``/``smart_chunk`` on first access (see module docstring)."""
    attr = _LAZY.get(name)
    if attr is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from evoq_context.chunking import pipeline  # noqa: PLC0415 - deliberate, breaks the cycle

    return getattr(pipeline, attr)

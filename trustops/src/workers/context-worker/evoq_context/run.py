"""Single entry module for the context pipeline (mirrors logics.ingestion.run).

Write path: ``build_context`` / ``revert_context`` / ``revert_context_to``.
Read path:
``get_context``, ``get_high_level_context``, ``get_deep_context``.

Revert input/output behavior:
- ``revert_context`` switches to the previous snapshot.
- ``revert_context_to`` accepts both numeric and label versions
  (e.g. ``3`` or ``v3``), strictly validates target snapshot existence,
  and returns numeric ``from_version`` / ``to_version`` in ``RevertResult``.

Callers build a ``ContextBuildRequest`` with their own ``ExtractedItem``
list — the module never assumes any particular upstream format.
"""

from __future__ import annotations

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import (
    ContextBuildRequest,
    ContextBuildResult,
    RevertResult,
)
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline import (
    build_context,
    revert_context,
    revert_context_to,
)
from evoq_context.retrieval.context_engine import (
    ContextResult,
    get_context,
    get_deep_context,
    get_high_level_context,
    search,
)

__all__ = [
    "build_context",
    "revert_context",
    "revert_context_to",
    "get_context",
    "get_deep_context",
    "get_high_level_context",
    "search",
    "ContextConfig",
    "ContextBuildRequest",
    "ContextBuildResult",
    "ContextResult",
    "RevertResult",
    "ExtractedItem",
]

"""logics.context — source-agnostic context management.

Public symbols are re-exported for convenience. The canonical entry
module is ``logics.context.run`` (``build_context``).
"""

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
)

__all__ = [
    "ContextConfig",
    "ContextBuildRequest",
    "ContextBuildResult",
    "ContextResult",
    "RevertResult",
    "ExtractedItem",
    "build_context",
    "revert_context",
    "revert_context_to",
    "get_context",
    "get_deep_context",
    "get_high_level_context",
]

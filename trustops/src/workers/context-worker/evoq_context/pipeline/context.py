"""``BuildContext`` — the mutable working state every stage reads/writes.

Scope & functionality
----------------------
One instance is created per :func:`~evoq_context.pipeline.build.build_context`
call and threaded through every :class:`~evoq_context.pipeline.stages.Stage`.
Stages read the immutable :class:`~evoq_context.pipeline.plan.BuildPlan` for
"what kind of run is this" and accumulate results (``context_items``,
``vector_manifest``, ``analysis``, ...) onto this object as they run, so the
final assembly (:mod:`evoq_context.pipeline.result`) only has to read one
object instead of threading a dozen positional returns through the runner.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ContextItem
from evoq_context.core.observer import ContextObserver, NullObserver
from evoq_context.llm.client import LLMClient
from evoq_context.pipeline.plan import BuildPlan
from evoq_context.pipeline.tracker import PipelineTracker
from evoq_context.synthesis.analyzer import AnalysisResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from evoq_context.pipeline.vectors import VectorWriteReport
    from evoq_context.storage.local import LocalContentStore
    from evoq_context.vector.interface import VectorStore


@dataclass
class BuildContext:
    """Everything a :class:`~evoq_context.pipeline.stages.Stage` needs."""

    request: ContextBuildRequest
    config: ContextConfig
    plan: BuildPlan
    tracker: PipelineTracker
    observer: ContextObserver = field(default_factory=NullObserver)
    llm: LLMClient | None = None
    vector_store: VectorStore | None = None
    store: LocalContentStore | None = None

    # -- accumulated across stages ---------------------------------------
    current_hashes: dict[str, str] = field(default_factory=dict)
    context_items: list[ContextItem] = field(default_factory=list)
    all_items: list[ContextItem] = field(default_factory=list)
    analysis: AnalysisResult = field(default_factory=AnalysisResult)
    vector_manifest: dict[str, dict[str, Any]] = field(default_factory=dict)
    index_manifest: dict[str, Any] | None = None
    vector_write_report: VectorWriteReport | None = None
    failed_doc_ids: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    change_details: dict[str, list[str]] = field(default_factory=dict)
    stats: dict[str, int | str] = field(default_factory=dict)
    artifact_statuses: dict[str, str] = field(default_factory=dict)

    t0: float = field(default_factory=time.monotonic)

    def elapsed_ms(self) -> int:
        """Milliseconds elapsed since this context was created."""
        return int((time.monotonic() - self.t0) * 1000)

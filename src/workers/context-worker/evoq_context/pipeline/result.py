"""Assemble ``ContextBuildResult`` from tracker + stats + the build summary.

Scope & functionality
----------------------
Pure assembly: turns the ``PipelineTracker``'s accumulated component
statuses/errors plus the run's stats into the ``ContextBuildResult`` every
caller (the job, the CLI, tests) consumes. Takes any ``VectorStore``
(backend-agnostic — S3 Vectors, in-memory, or none) so it never imports a
concrete backend module; backend-identifying facts (``vector_store``,
``vector_region``, ``vector_index``) come from ``VectorStore.describe()``
— never hardcoded from config fields that may not match what the backend
actually reports (the fixed bug: a bucket and an index are not the same
thing, and describe() is the one place they cannot be confused).

``publishable`` is ``False`` exactly when a document consumer of this
result should NOT trust it as a complete, servable snapshot: the render,
publish or save-state stage failed outright, or every document failed
vector-write. A caller that gets ``publishable=False`` back knows the
active snapshot pointer was intentionally left alone (see
``jobs/status_map.py``, host-side) — the next build's ``resynthesize``
self-repair (``BuildPlan.artifacts_missing``) is what fixes it forward.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildResult
from evoq_context.pipeline.tracker import PipelineTracker

if TYPE_CHECKING:  # pragma: no cover - typing only
    from evoq_context.core.observer import BuildSummary
    from evoq_context.vector.interface import VectorStore

#: Stages whose failure means the snapshot must not be treated as complete.
_MUST_NOT_FAIL_FOR_PUBLISH = frozenset({"render", "publish", "save_state"})


def _is_publishable(tracker: PipelineTracker, stats: dict[str, int | str]) -> bool:
    """Whether this result should be treated as a complete, servable snapshot."""
    failed_critical = {
        c.name
        for c in tracker.components
        if c.status == "error" and c.name in _MUST_NOT_FAIL_FOR_PUBLISH
    }
    if failed_critical:
        return False
    failed_docs = int(stats.get("failed_docs", 0) or 0)
    vectors_upserted = int(stats.get("vectors_upserted", 0) or 0)
    return not (failed_docs and not vectors_upserted and (stats.get("new") or stats.get("updated")))


async def build_result(
    project: str,
    tracker: PipelineTracker,
    goals: list[str],
    stats: dict[str, int | str],
    config: ContextConfig,
    vector_store: VectorStore | None = None,
    *,
    version_label: str = "",
    change_details: dict[str, list[str]] | None = None,
    analysis: dict[str, object] | None = None,
    summary: BuildSummary | None = None,
) -> ContextBuildResult:
    """Assemble the ``ContextBuildResult`` a caller (job, CLI, tests) consumes."""
    parts = [f"{stats[k]} {k}" for k in ("new", "updated", "deleted", "unchanged") if stats.get(k)]
    change_summary = ", ".join(parts) or "no docs"
    status = tracker.overall_status

    if status == "success":
        message = f"Context built successfully. {change_summary}."
    elif status == "partial_success":
        failed = [e.stage for e in tracker.errors]
        message = (
            f"Context partially built ({change_summary}). "
            f"Failed stages: {', '.join(failed) or 'none'}."
        )
    else:
        message = f"Context build failed. {change_summary}. See errors for details."

    loc_version = version_label or config.read_current_version_label(project) or "v1"
    context_location: dict[str, str] = {
        "project_dir": str(config.project_dir(project)),
        "state_file": str(config.state_path(project, version=loc_version)),
        "items_dir": str(config.items_dir(project, version=loc_version)),
        "context_md": str(config.resolve_context_md_path(project, version=loc_version)),
        "document_inventory": str(
            config.resolve_document_inventory_path(project, version=loc_version)
        ),
        "activity_log": str(config.resolve_activity_path(project)),
        "snapshot_version": loc_version,
        "vector_manifest": str(config.vector_manifest_path(project, version=loc_version)),
        "index_manifest": str(config.index_manifest_path(project, version=loc_version)),
    }
    if vector_store is not None:
        store_info: dict[str, Any] = {}
        try:
            store_info = await vector_store.describe()
        except Exception:  # noqa: BLE001 - describe() is diagnostic only, never fatal here
            store_info = {}
        context_location["vector_backend"] = str(store_info.get("backend", config.vector_backend))
        context_location["vector_store"] = str(store_info.get("store", ""))
        context_location["vector_region"] = str(store_info.get("region", ""))
        context_location["vector_index"] = str(
            store_info.get("index", getattr(vector_store, "index_name", ""))
        )

    return ContextBuildResult(
        project=project,
        status=status,
        message=message,
        goals=goals,
        stats=stats,
        errors=tracker.errors,
        component_statuses=tracker.components,
        change_details=change_details or {},
        context_location=context_location,
        analysis=analysis or {},
        version=version_label,
        summary=summary,
        publishable=_is_publishable(tracker, stats),
    )

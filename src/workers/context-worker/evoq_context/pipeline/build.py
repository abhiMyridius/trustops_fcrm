"""Build or update context for a project — the stage-based orchestrator.

Scope & functionality
----------------------
``build_context`` is now ~60 lines by design: ``BuildPlanner.plan()``
decides what kind of run this is (see ``pipeline/plan.py``), a
``BuildContext`` is constructed to carry the mutable working state, the
stage list for ``plan.mode`` is assembled from ``pipeline/stages.py``, and
``StageRunner`` (``pipeline/runner.py``) drives it. Per-document failures
are isolated inside ``VectorWriteStage`` (removed from ``current_hashes``
so they retry on the next run); a stage's own failure degrades the overall
status to ``partial_success``/``error`` without aborting the rest of the
run unless that stage is ``fatal``. One ``BuildSummary`` is assembled and
emitted exactly once at the end (``pipeline/summary.py``).

Kept here for backward compatibility with pre-existing callers/tests:
``CONFIG_DRIFT_REINDEX_REQUIRED`` (the error code for ``mode="drift_error"``),
and re-exports of ``ContextItem``/``AnalysisResult``/``analyze_corpus``/
``LocalContentStore`` that a few tests import via ``build_module.<name>``.
"""

from __future__ import annotations

import asyncio
import logging

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest, ContextBuildResult
from evoq_context.core.errors import StateCorrupt
from evoq_context.core.models import ContextItem  # noqa: F401 - re-exported for tests
from evoq_context.core.observer import ContextObserver, NullObserver
from evoq_context.llm.client import LLMClient
from evoq_context.pipeline.context import BuildContext
from evoq_context.pipeline.plan import CONFIG_DRIFT_REINDEX_REQUIRED, BuildPlan, BuildPlanner
from evoq_context.pipeline.result import build_result
from evoq_context.pipeline.runner import StageRunner
from evoq_context.pipeline.stages import (
    ActivityStage,
    AnalysisStage,
    ManifestStage,
    PublishStage,
    ReconcileStage,
    RenderStage,
    SaveStateStage,
    SeedSnapshotStage,
    Stage,
    StoreWriteStage,
    VectorWriteStage,
    _fallback_contextual_goals,
)
from evoq_context.pipeline.summary import emit_build_summary
from evoq_context.pipeline.tracker import PipelineTracker
from evoq_context.storage.local import LocalContentStore  # noqa: F401 - re-exported for tests
from evoq_context.synthesis.analyzer import (  # noqa: F401 - re-exported
    AnalysisResult,
    analyze_corpus,
)
from evoq_context.vector.factory import create_vector_store

logger = logging.getLogger(__name__)

__all__ = ["CONFIG_DRIFT_REINDEX_REQUIRED", "build_context"]

_STAGES_BY_MODE: dict[str, list[type[Stage]]] = {
    "full": [
        ReconcileStage, SeedSnapshotStage, StoreWriteStage, VectorWriteStage,
        AnalysisStage, ManifestStage, RenderStage, SaveStateStage,
        PublishStage, ActivityStage,
    ],
    "resynthesize": [
        ReconcileStage, AnalysisStage, ManifestStage, RenderStage,
        SaveStateStage, ActivityStage,
    ],
    "noop": [ReconcileStage, ActivityStage],
}

#: Stages that call the chat model (AEO analysis/synthesis prompts, the
#: context.md render and the LLM document inventory). Dropped from every
#: mode when ``ContextConfig.synthesis_enabled`` is False (TrustOps default).
_SYNTHESIS_STAGES: tuple[type[Stage], ...] = (AnalysisStage, RenderStage)
_SYNTHESIS_OFF_REASON = "CONTEXT_SYNTHESIS_ENABLED=false"

_NOOP_SKIPPED_STAGES = (
    "seed_snapshot", "store_write", "vector_write", "analysis",
    "manifest", "render", "save_state", "publish",
)


def _stages_for_mode(mode: str, *, synthesis_enabled: bool = True) -> list[Stage]:
    try:
        classes = _STAGES_BY_MODE[mode]
    except KeyError:
        raise ValueError(f"no stage list for build mode {mode!r}") from None
    if not synthesis_enabled:
        classes = [cls for cls in classes if cls not in _SYNTHESIS_STAGES]
    return [cls() for cls in classes]


def _skip_synthesis(ctx: BuildContext) -> None:
    """Record the synthesis stages as skipped and give the result fallback goals."""
    ctx.goals = _fallback_contextual_goals(ctx.plan.normalized_goal_prompt)
    ctx.analysis.goals = ctx.goals
    ctx.analysis.intent_alignment.setdefault("user_prompt", ctx.plan.normalized_goal_prompt)
    ctx.artifact_statuses["context_md"] = "skipped"
    ctx.artifact_statuses["document_inventory"] = "skipped"
    for name in ("analysis", "render"):
        ctx.tracker.skipped(name, _SYNTHESIS_OFF_REASON)


async def _empty_result(
    project: str, tracker: PipelineTracker, config: ContextConfig, version_label: str,
) -> ContextBuildResult:
    return await build_result(
        project, tracker, [], {"documents_received": 0, "duration_ms": 0}, config,
        vector_store=None, version_label=version_label, change_details={},
    )


async def build_context(
    request: ContextBuildRequest,
    config: ContextConfig,
    *,
    observer: ContextObserver | None = None,
) -> ContextBuildResult:
    """Build or update context for a project."""
    raw_observer = observer or NullObserver()
    tracker = PipelineTracker(raw_observer)
    planner = BuildPlanner(config)

    plan: BuildPlan
    try:
        plan = await asyncio.to_thread(planner.plan, request)
    except StateCorrupt as exc:
        # Loud, not silently treated as "first run": a present-but-
        # unparseable state.json must never be mistaken for "nothing
        # existed before" — that would either skip real work or trigger a
        # needless full re-embed under the illusion of a fresh project.
        logger.exception("Corrupt state.json — refusing to plan a build")
        tracker.error("reconcile", "STATE_CORRUPT", exc)
        return await _empty_result(request.project, tracker, config, "")
    except Exception as exc:
        logger.exception("Reconciliation/planning failed")
        tracker.error("reconcile", "RECONCILE_ERROR", exc)
        return await _empty_result(request.project, tracker, config, "")

    if plan.mode == "drift_error":
        drift_exc = RuntimeError(plan.drift_message)
        tracker.error("config_drift", CONFIG_DRIFT_REINDEX_REQUIRED, drift_exc)
        logger.error(
            "stage_done stage=build_context project=%s status=error reason=%s",
            request.project, CONFIG_DRIFT_REINDEX_REQUIRED,
        )
        return await _empty_result(request.project, tracker, config, plan.active_label)

    llm = LLMClient.from_config(config)
    needs_vector_store = plan.mode in ("full", "resynthesize")
    vector_store = create_vector_store(config, request.project) if needs_vector_store else None
    ctx = BuildContext(
        request=request, config=config, plan=plan, tracker=tracker,
        observer=raw_observer, llm=llm, vector_store=vector_store,
    )

    if plan.mode == "noop":
        logger.info("NO_CHANGES: skipping synthesis/write stages project=%s", request.project)
        ctx.goals = _fallback_contextual_goals(plan.normalized_goal_prompt)
        ctx.analysis.intent_alignment.setdefault("user_prompt", plan.normalized_goal_prompt)
        for name in _NOOP_SKIPPED_STAGES:
            tracker.skipped(name, "No document/config changes")

    logger.info(
        "stage_start stage=build_context project=%s mode=%s documents_received=%d",
        request.project, plan.mode, len(request.extracted_items),
    )

    if plan.mode != "noop" and not config.synthesis_enabled:
        logger.info("synthesis disabled: skipping analysis/render project=%s", request.project)
        _skip_synthesis(ctx)

    stages = _stages_for_mode(plan.mode, synthesis_enabled=config.synthesis_enabled)
    await StageRunner(tracker).run(stages, ctx)

    summary = emit_build_summary(ctx)
    logger.info(
        "stage_done stage=build_context project=%s status=%s duration_ms=%d",
        request.project, tracker.overall_status, ctx.stats.get("duration_ms", 0),
    )

    return await build_result(
        request.project, tracker, ctx.goals, ctx.stats, config, vector_store,
        version_label=plan.to_label or plan.active_label,
        change_details=ctx.change_details,
        analysis=_analysis_payload(ctx.analysis),
        summary=summary,
    )


def _analysis_payload(analysis: AnalysisResult) -> dict[str, object]:
    return {
        "input_evidence": analysis.input_evidence,
        "consolidated_findings": analysis.consolidated_findings,
        "inferences": analysis.inferences,
        "conflicts": analysis.conflicts,
        "gaps": analysis.gaps,
        "intent_alignment": analysis.intent_alignment,
        "recommended_actions": analysis.recommended_actions,
        "themes": analysis.themes,
        "goals": analysis.goals,
        "glossary": getattr(analysis, "glossary", []),
        "retrieval_hints": getattr(analysis, "retrieval_hints", []),
    }

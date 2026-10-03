"""Assemble and emit the one :class:`BuildSummary` for a build (plan §3.1a).

Scope & functionality
----------------------
``BuildSummary`` itself (with its ``llm_calls``/``llm_tokens_total``/
``llm_calls_by_caller``/``guardrail_events_by_outcome`` fields and the
``record_llm_call``/``record_guardrail`` roll-up methods) lives in
``core/observer.py`` — it is a shared shape the whole engine's observer
seam depends on, not something this module redefines. What this module
does is the *pipeline-specific* half: fold the doc-count/chunk/vector
stats a build accumulated on its :class:`~evoq_context.pipeline.context.BuildContext`
into the same ``BuildSummary`` the :class:`~evoq_context.pipeline.tracker.PipelineTracker`
already rolled every ``LlmCallRecord``/``GuardrailEvent`` into (via its
observe-and-forward ``on_llm_call``/``on_guardrail_event``), then emit it
exactly once via ``observer.build_summary(summary)``.
"""

from __future__ import annotations

from evoq_context.chunking.types import percentile
from evoq_context.core.observer import BuildSummary
from evoq_context.pipeline.context import BuildContext


def build_summary_from_context(ctx: BuildContext) -> BuildSummary:
    """Finalize the tracker's running ``BuildSummary`` with this build's stats.

    Layers doc/stage/chunk stats on top; does not emit it — see
    :func:`emit_build_summary` for that.
    """
    summary = ctx.tracker.summary_counters
    summary.project = ctx.request.project
    summary.version = ctx.plan.to_label or ctx.plan.active_label
    summary.mode = ctx.plan.mode
    summary.status = ctx.tracker.overall_status
    summary.duration_ms = ctx.elapsed_ms()
    summary.documents_received = len(ctx.request.extracted_items)
    summary.new = int(ctx.stats.get("new", 0) or 0)
    summary.updated = int(ctx.stats.get("updated", 0) or 0)
    summary.deleted = int(ctx.stats.get("deleted", 0) or 0)
    summary.unchanged = int(ctx.stats.get("unchanged", 0) or 0)
    summary.chunks_created = int(ctx.stats.get("chunks_created", 0) or 0)
    summary.parents_stored = int(ctx.stats.get("parents_stored", 0) or 0)
    summary.vectors_upserted = int(ctx.stats.get("vectors_upserted", 0) or 0)
    summary.errors = list(ctx.tracker.errors)
    summary.stage_durations_ms = {
        c.name: c.duration_ms for c in ctx.tracker.components if c.duration_ms
    }

    report = ctx.vector_write_report
    if report is not None:
        summary.failed_docs = len(report.failed)
        failed_codes: dict[str, int] = {}
        for failure in report.failed:
            failed_codes[failure.code] = failed_codes.get(failure.code, 0) + 1
        summary.failed_codes = failed_codes
        summary.strategy_histogram = dict(report.strategy_histogram)
        samples = report.token_samples
        summary.token_p50 = percentile(samples, 0.5)
        summary.token_p95 = percentile(samples, 0.95)
        summary.token_max = max(samples) if samples else 0

    return summary


def emit_build_summary(ctx: BuildContext) -> BuildSummary:
    """Finalize and emit (via ``ctx.observer.build_summary``) exactly once."""
    summary = build_summary_from_context(ctx)
    ctx.observer.build_summary(summary)
    return summary

"""The ten pipeline stages — one focused class per concern (plan §2.5).

Scope & functionality
----------------------
Each :class:`Stage` does exactly one thing to a :class:`~evoq_context.pipeline.context.BuildContext`
and records its own outcome on ``ctx.tracker`` when it has something
special to say (a partial failure, a degraded result, an explicit skip);
:class:`~evoq_context.pipeline.runner.StageRunner` supplies the generic
timing/telemetry/exception-handling wrapper (``tracker.astage``) around
every stage, so a stage that raises and has *nothing* special to say still
gets a correctly-coded ``ComponentStatus`` for free (see
``pipeline/tracker.py``'s module docstring).

``ReconcileStage``'s actual content-hash diffing already happened during
planning (:mod:`evoq_context.pipeline.plan` needs the changeset to decide
the build's *mode* before any stage can run) — this stage's ``run`` copies
that already-computed result onto the mutable ``BuildContext`` so every
later stage reads it from one place, and so a fatal failure at this exact
point in the stage list is what the fatal=True flag models.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from evoq_context.chunking.pipeline import ChunkPipeline
from evoq_context.core.models import ContextItem
from evoq_context.core.observer import GuardrailEvent
from evoq_context.pipeline.context import BuildContext
from evoq_context.pipeline.vectors import VectorWriter
from evoq_context.state.activity import append_build_activity
from evoq_context.state.reconciler import (
    compute_build_version_key,
    compute_state_version_key,
    save_state,
    write_current_pointer,
)
from evoq_context.storage import snapshot as _snapshot
from evoq_context.storage.local import LocalContentStore
from evoq_context.synthesis.analyzer import analyze_corpus
from evoq_context.synthesis.context_md import generate_context_md
from evoq_context.synthesis.document_inventory import generate_document_inventory
from evoq_context.vector.embedder import DocumentPreparer

logger = logging.getLogger(__name__)


@runtime_checkable
class Stage(Protocol):
    """One pipeline stage.

    ``fatal=True`` means an unhandled exception ends the whole run;
    otherwise it is recorded and the remaining stages still run (see
    ``pipeline/runner.py::StageRunner``).
    """

    name: str
    fatal: bool

    async def run(self, ctx: BuildContext) -> None:
        """Do this stage's work against ``ctx``.

        Records its own outcome on ``ctx.tracker`` when it has something
        beyond a plain success/exception to report.
        """
        ...


def _fallback_contextual_goals(normalized_goal_prompt: str) -> list[str]:
    goals = [
        "Synthesize source documents into implementation-ready goals.",
        "Identify requirement gaps and resolve ambiguities early.",
        "Create traceable outcomes for delivery and validation.",
    ]
    if normalized_goal_prompt:
        goals.insert(0, "Align synthesized goals with the user's stated intent and scope.")
    return goals


def _rename_tmp_to_final(tmp_dir: Path, final_dir: Path) -> None:
    """Atomic-ish publish: drop any stale partial final dir, then rename."""
    if final_dir.exists():
        shutil.rmtree(final_dir)
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir.rename(final_dir)


class ReconcileStage:
    """Copies the already-computed plan changeset/hashes onto the context."""

    name = "reconcile"
    fatal = True

    async def run(self, ctx: BuildContext) -> None:
        """Copy ``ctx.plan``'s changeset/hashes onto the mutable context."""
        plan = ctx.plan
        ctx.current_hashes = dict(plan.current_hashes)
        ctx.stats.update(
            documents_received=len(ctx.request.extracted_items),
            new=len(plan.changeset.new_ids),
            updated=len(plan.changeset.updated_ids),
            deleted=len(plan.changeset.deleted_ids),
            unchanged=plan.changeset.unchanged_count,
        )
        if plan.reindex_reason:
            ctx.stats["reindex_reason"] = plan.reindex_reason
        ctx.change_details = {
            "new_ids": plan.changeset.new_ids,
            "updated_ids": plan.changeset.updated_ids,
            "deleted_ids": plan.changeset.deleted_ids,
        }


class SeedSnapshotStage:
    """Hardlink-forward unchanged docs' ``items/``/``chunks/`` into the new tmp snapshot."""

    name = "seed_snapshot"
    fatal = False

    async def run(self, ctx: BuildContext) -> None:
        """Hardlink-forward unchanged docs from the active snapshot into the new tmp one."""
        plan = ctx.plan
        if not plan.active_label:
            ctx.tracker.skipped(self.name, "no previous snapshot to seed from")
            return
        skip_ids = {
            doc_id.split(":", 1)[-1]
            for doc_id in (
                *plan.changeset.new_ids,
                *plan.changeset.updated_ids,
                *plan.changeset.deleted_ids,
            )
        }
        report = await asyncio.to_thread(
            _snapshot.seed_snapshot,
            ctx.config.layout,
            ctx.request.project,
            plan.active_label,
            plan.tmp_label,
            skip_source_ids=skip_ids,
        )
        ctx.stats["seeded_hardlinked"] = report.hardlinked
        ctx.stats["seeded_copied"] = report.copied
        ctx.tracker.success(self.name)


class StoreWriteStage:
    """Writes canonical items (new/updated) and deletes removed ones to ``items/``."""

    name = "store_write"
    fatal = False

    async def run(self, ctx: BuildContext) -> None:
        """Write new/updated items, delete removed ones, under the tmp snapshot dir."""
        plan = ctx.plan
        # This stage runs only in "full" mode — write to the {vN}.tmp/
        # staging dir, never directly to the live {vN}/ (PublishStage
        # renames it into place atomically once every stage has succeeded).
        store = LocalContentStore(ctx.config, plan.tmp_label)
        ctx.store = store
        items_by_id = {item.doc_id: item for item in ctx.request.extracted_items}
        for doc_id in plan.changeset.deleted_ids:
            await asyncio.to_thread(store.delete, doc_id, ctx.request.project)
        for doc_id in (*plan.changeset.new_ids, *plan.changeset.updated_ids):
            ctx_item = ContextItem.from_extracted(items_by_id[doc_id], ctx.request.project)
            await asyncio.to_thread(store.add, ctx_item)
            ctx.context_items.append(ctx_item)
        ctx.tracker.success(self.name)


class VectorWriteStage:
    """Reindex/keyed-delete then ``VectorWriter``-stream the changed documents.

    Per-document failures never abort the batch: they are recorded via
    ``tracker.error_doc`` and removed from ``ctx.current_hashes`` so they
    are retried on the next build; the stage-level status is
    ``VECTOR_WRITE_PARTIAL``/``VECTOR_WRITE_ALL_FAILED`` when any document
    failed, ``success`` otherwise.
    """

    name = "vector_write"
    fatal = False

    async def run(self, ctx: BuildContext) -> None:
        """Delete/reindex as needed, then stream-embed the changed documents."""
        plan = ctx.plan
        if ctx.vector_store is None:
            ctx.tracker.skipped(self.name, "VECTOR_BACKEND=none")
            return
        assert ctx.llm is not None

        vector_manifest = dict(plan.previous_vector_manifest)
        if plan.reindex_reason:
            await ctx.vector_store.delete_index()
            vector_manifest = {}
        else:
            for doc_id in (*plan.changeset.deleted_ids, *plan.changeset.updated_ids):
                known_keys = plan.previous_vector_manifest.get(doc_id, {}).get("keys")
                await ctx.vector_store.delete_by_doc_id(doc_id, known_keys=known_keys)
                vector_manifest.pop(doc_id, None)
                await asyncio.to_thread(
                    _snapshot.delete_chunks_file,
                    ctx.config.layout,
                    ctx.request.project,
                    doc_id.split(":", 1)[-1],
                    version=plan.tmp_label,
                )

        if ctx.context_items:
            chunk_pipeline = ChunkPipeline(ctx.config.chunking())
            preparer = DocumentPreparer(chunk_pipeline, ctx.llm, ctx.config, observer=ctx.tracker)
            writer = VectorWriter(
                ctx.vector_store, preparer, ctx.config.layout, ctx.config,
                tracker=ctx.tracker, observer=ctx.tracker,
            )
            report = await writer.write(
                ctx.context_items, snapshot_version=plan.to_label, chunks_version=plan.tmp_label,
            )
            ctx.vector_write_report = report
            ctx.stats["chunks_created"] = report.chunks_created
            ctx.stats["parents_stored"] = report.parents_stored
            ctx.stats["vectors_upserted"] = report.vectors_upserted
            vector_manifest.update(report.manifest_entries)

            if report.failed:
                ctx.failed_doc_ids = [f.doc_id for f in report.failed]
                for doc_id in ctx.failed_doc_ids:
                    ctx.current_hashes.pop(doc_id, None)
                ctx.stats["failed_docs"] = len(ctx.failed_doc_ids)
                for failure in report.failed:
                    ctx.tracker.error_doc(self.name, failure.code, failure.doc_id, failure.error)
                if report.all_failed:
                    ctx.tracker.error(
                        self.name, "VECTOR_WRITE_ALL_FAILED",
                        RuntimeError(f"All {len(ctx.failed_doc_ids)} document(s) failed embedding"),
                    )
                else:
                    ctx.tracker.error(
                        self.name, "VECTOR_WRITE_PARTIAL",
                        RuntimeError(
                            f"{len(ctx.failed_doc_ids)} document(s) failed "
                            f"embedding: {ctx.failed_doc_ids}"
                        ),
                    )
            else:
                ctx.tracker.success(self.name)
        else:
            ctx.tracker.success(self.name)

        ctx.vector_manifest = vector_manifest
        await asyncio.to_thread(
            _snapshot.write_vector_manifest,
            ctx.config.vector_manifest_path(ctx.request.project, version=plan.tmp_label),
            vector_manifest,
        )


class AnalysisStage:
    """Runs corpus analysis/synthesis; falls back to generic goals on total failure."""

    name = "analysis"
    fatal = False

    async def run(self, ctx: BuildContext) -> None:
        """Run corpus analysis/synthesis; degrade or fall back rather than abort."""
        plan = ctx.plan
        store = ctx.store or LocalContentStore(ctx.config, plan.to_label)
        ctx.all_items = await asyncio.to_thread(store.list_items, ctx.request.project)
        assert ctx.llm is not None
        try:
            analysis = await analyze_corpus(
                items=ctx.all_items,
                goal_prompt=plan.normalized_goal_prompt,
                llm=ctx.llm,
                model=ctx.config.analysis_model,
                provider=ctx.config.analysis_provider,
                per_doc_max_tokens=ctx.config.per_doc_max_tokens,
                # Passed on BOTH the extraction and merge paths — the audit
                # found the old full-build branch omitted this, unlike the
                # resynthesize branch; unifying into one stage fixes that
                # structurally (there is only one call site now).
                synthesis_max_tokens=ctx.config.synthesis_max_tokens,
                analysis_concurrency=ctx.config.analysis_concurrency,
                per_doc_input_chars=ctx.config.per_doc_input_chars,
                merge_batch_size=ctx.config.merge_batch_size,
                corpus_budget_chars=ctx.config.corpus_budget_chars,
                observer=ctx.tracker,
            )
            ctx.analysis = analysis
            ctx.goals = analysis.goals
            if analysis.degraded:
                reason = (
                    f"docs_failed={analysis.docs_failed}/"
                    f"{analysis.docs_extracted + analysis.docs_failed} "
                    f"merge_batches_failed={analysis.merge_batches_failed} "
                    f"proofread_failed={analysis.proofread_failed}"
                )
                ctx.tracker.degraded(self.name, "ANALYSIS_DEGRADED", reason)
                ctx.tracker.on_guardrail_event(
                    GuardrailEvent(
                        name="analysis_degraded", subject=ctx.request.project,
                        outcome="degraded", detail=reason,
                    )
                )
            else:
                ctx.tracker.success(self.name)
        except Exception as exc:
            logger.exception("Corpus analysis failed")
            ctx.goals = _fallback_contextual_goals(plan.normalized_goal_prompt)
            ctx.analysis.goals = ctx.goals
            ctx.analysis.intent_alignment.setdefault("user_prompt", plan.normalized_goal_prompt)
            ctx.tracker.error(self.name, "ANALYSIS_ERROR", exc)


class ManifestStage:
    """Builds/writes ``index_manifest.json`` (full) or reuses the active one (resynthesize)."""

    name = "manifest"
    fatal = False

    async def run(self, ctx: BuildContext) -> None:
        """Build (full) or reuse (resynthesize) this snapshot's ``index_manifest.json``."""
        plan = ctx.plan
        if ctx.vector_store is None:
            ctx.tracker.skipped(self.name, "VECTOR_BACKEND=none")
            return

        if plan.mode == "resynthesize":
            ctx.index_manifest = await asyncio.to_thread(
                _snapshot.read_index_manifest,
                ctx.config.index_manifest_path(ctx.request.project, version=plan.to_label),
            )
            ctx.artifact_statuses["index_manifest"] = "ok"
            ctx.tracker.success(self.name)
            return

        store_info = await ctx.vector_store.describe()
        parent_chunk_count = (
            ctx.vector_write_report.parents_stored if ctx.vector_write_report is not None else 0
        )
        manifest = _snapshot.build_index_manifest(
            project_id=ctx.request.project,
            snapshot_version=plan.to_label,
            context_version=ctx.config.version_label_to_number(plan.to_label),
            backend=ctx.config.vector_backend,
            # ``store`` — never the index name — is the fixed bug: a
            # backend's *store* (S3 Vectors bucket, "memory", a Postgres
            # DSN, ...) and its *index* are frequently different concepts.
            store=str(store_info.get("store", "")),
            index=str(store_info.get("index", "")),
            region=str(store_info.get("region", ctx.config.s3v_region)),
            distance_metric=str(store_info.get("distance_metric", "cosine")),
            embedding_model=f"{ctx.config.embedding_provider}/{ctx.config.embedding_model}",
            embedding_dimension=ctx.config.embedding_dimension,
            vector_manifest=ctx.vector_manifest,
            generated_at=datetime.now(UTC).isoformat(),
            parent_chunk_count=parent_chunk_count,
        )
        ctx.index_manifest = manifest
        await asyncio.to_thread(
            _snapshot.write_index_manifest,
            ctx.config.index_manifest_path(ctx.request.project, version=plan.tmp_label),
            manifest,
        )
        ctx.artifact_statuses["index_manifest"] = "ok"
        ctx.tracker.success(self.name)


class RenderStage:
    """Renders ``context.md`` + ``document_inventory.md`` for this snapshot version."""

    name = "render"
    fatal = False

    async def run(self, ctx: BuildContext) -> None:
        """Render ``context.md`` and ``document_inventory.md`` for this snapshot."""
        plan = ctx.plan
        version = plan.tmp_label if plan.mode == "full" else plan.to_label
        md_path = ctx.config.resolve_context_md_path(ctx.request.project, version=version)
        inv_path = ctx.config.resolve_document_inventory_path(ctx.request.project, version=version)

        def _read_chunk_records() -> list[dict[str, Any]]:
            layout, project = ctx.config.layout, ctx.request.project
            return list(_snapshot.iter_chunks_snapshot(layout, project, version))

        chunk_records = await asyncio.to_thread(_read_chunk_records)
        assert ctx.llm is not None
        md_result, inv_result = await asyncio.gather(
            generate_context_md(
                project=ctx.request.project,
                items=ctx.all_items,
                analysis=ctx.analysis,
                context_md_path=md_path,
                vector_manifest=ctx.vector_manifest,
                chunk_records=chunk_records,
                index_manifest=ctx.index_manifest,
            ),
            generate_document_inventory(
                project=ctx.request.project,
                items=ctx.all_items,
                analysis=ctx.analysis,
                inventory_path=inv_path,
                llm=ctx.llm,
                vector_manifest=ctx.vector_manifest,
            ),
        )
        ctx.artifact_statuses["context_md"] = "ok" if md_result.get("status") == "ok" else "failed"
        ctx.artifact_statuses["document_inventory"] = (
            "ok" if inv_result.get("status") == "ok" else "failed"
        )
        failed = [
            name for name, result in (("context_md", md_result), ("document_inventory", inv_result))
            if result.get("status") != "ok"
        ]
        if failed:
            ctx.tracker.error(
                self.name, "RENDER_ERROR",
                RuntimeError(f"{', '.join(failed)} failed to render"),
            )
        else:
            ctx.tracker.success(self.name)


class SaveStateStage:
    """Persists ``state.json`` (full snapshot's own state, or the active one's artifacts)."""

    name = "save_state"
    fatal = False

    async def run(self, ctx: BuildContext) -> None:
        """Write ``state.json`` (doc hashes + fingerprints + per-artifact status)."""
        plan = ctx.plan
        version = plan.tmp_label if plan.mode == "full" else plan.to_label
        state_version = compute_state_version_key(ctx.current_hashes)
        build_key = compute_build_version_key(state_version, ctx.config.fingerprint())
        await asyncio.to_thread(
            save_state,
            ctx.config.state_path(ctx.request.project, version=version),
            project=ctx.request.project,
            run_id=ctx.request.run_id,
            doc_hash_index=ctx.current_hashes,
            state_version_key=state_version,
            build_version_key=build_key,
            goal_prompt_hash=plan.prompt_hash,
            config_fingerprint=ctx.config.fingerprint(),
            index_fingerprint=plan.index_fingerprint_now,
            analysis_fingerprint=plan.analysis_fingerprint_now,
            artifacts=dict(ctx.artifact_statuses),
        )
        ctx.tracker.success(self.name)


class PublishStage:
    """Atomically renames ``{vN}.tmp/`` -> ``{vN}/`` then advances ``current.txt``."""

    name = "publish"
    fatal = False

    async def run(self, ctx: BuildContext) -> None:
        """Rename the tmp snapshot into place and advance ``current.txt`` (full mode only)."""
        plan = ctx.plan
        if plan.mode != "full":
            ctx.tracker.skipped(self.name, "no new snapshot version to publish")
            return
        await asyncio.to_thread(
            _rename_tmp_to_final,
            ctx.config.tmp_version_dir(ctx.request.project, plan.to_label),
            ctx.config.version_dir(ctx.request.project, plan.to_label),
        )
        pointer_path = ctx.config.current_pointer_path(ctx.request.project)
        await asyncio.to_thread(write_current_pointer, pointer_path, plan.to_label)
        ctx.tracker.success(self.name)


class ActivityStage:
    """Writes the ``context_generations``/activity-log entry (last, always runs)."""

    name = "activity"
    fatal = False

    async def run(self, ctx: BuildContext) -> None:
        """Append one build-activity log entry — always the last stage to run."""
        ctx.stats["duration_ms"] = ctx.elapsed_ms()
        version = ctx.plan.to_label or ctx.plan.active_label
        await asyncio.to_thread(
            append_build_activity, ctx.config, ctx.request, ctx.tracker, ctx.stats, version,
        )
        ctx.tracker.success(self.name)

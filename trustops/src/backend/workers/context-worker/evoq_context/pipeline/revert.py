"""Revert the active snapshot and reconcile the vector index — as stages.

Scope & functionality
----------------------
``revert_context_to`` moves ``current.txt`` to a target snapshot version
and reconciles the vector index to match it, via four small, deterministic
stages run in a fixed order (a double-revert against the same two
versions produces byte-identical ``vector_manifest.json``/
``index_manifest.json`` output — see ``test_revert_by_version.py``):

1. ``RevertDeleteStage`` — key-addressed deletes (via each snapshot's
   ``vector_manifest.json`` — no full-index scan) for every document the
   current snapshot has that the target does not.
2. ``RevertReembedStage`` — re-embeds every document whose content differs
   between the current and target snapshot (new in target, or same doc_id
   with a different content hash), via ``VectorWriter``/``DocumentPreparer``
   (migrated off the legacy ``embed_and_upsert`` path). Unlike a normal
   build, which tolerates partial embedding failure and retries the failed
   documents on the next run, **a partial failure here fails the whole
   revert** (``REVERT_PARTIAL_REEMBED``) — a revert is a point-in-time
   promise ("go back to exactly what v-N looked like"), and a half-reverted
   index breaks that promise silently if allowed to report "success".
3. ``RevertManifestStage`` — republishes the target's own
   ``vector_manifest.json``/``index_manifest.json`` with the deletes/
   upserts applied, refreshes the ``## Index manifest`` fenced block
   already embedded in the target's ``context.md`` (surgical text
   replacement — the narrative sections were correct when that version was
   first built and are not re-synthesized here), and deletes now-orphaned
   ``chunks/{source_id}.jsonl`` files for removed documents.
4. ``RevertPointerStage`` — flips ``current.txt`` — **only** after every
   earlier stage has succeeded, so a failed revert never leaves the active
   pointer pointing at a half-reconciled index.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from evoq_context.chunking.pipeline import ChunkPipeline
from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ErrorDetail, RevertResult
from evoq_context.core.errors import StateCorrupt
from evoq_context.llm.client import LLMClient
from evoq_context.pipeline.tracker import PipelineTracker
from evoq_context.pipeline.vectors import VectorWriter, VectorWriteReport
from evoq_context.state.activity import append_raw_activity
from evoq_context.state.reconciler import load_previous_state, write_current_pointer
from evoq_context.storage import snapshot as _snapshot
from evoq_context.storage.local import LocalContentStore
from evoq_context.vector.embedder import DocumentPreparer
from evoq_context.vector.factory import create_vector_store
from evoq_context.vector.interface import VectorStore

logger = logging.getLogger(__name__)

_INDEX_MANIFEST_BLOCK_RE = re.compile(r"## Index manifest\s*\n+```json\n.*?\n```\n?", re.DOTALL)


def _ms_since(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


@dataclass
class RevertContext:
    """Mutable working state one ``revert_context_to`` call threads through its stages."""

    project: str
    config: ContextConfig
    from_version: str
    to_version: str
    tracker: PipelineTracker
    llm: LLMClient
    vector_store: VectorStore | None
    cur_hashes: dict[str, str] = field(default_factory=dict)
    target_hashes: dict[str, str] = field(default_factory=dict)
    cur_vector_manifest: dict[str, dict[str, Any]] = field(default_factory=dict)
    target_vector_manifest: dict[str, dict[str, Any]] = field(default_factory=dict)
    remove_doc_ids: set[str] = field(default_factory=set)
    reembed_doc_ids: set[str] = field(default_factory=set)
    vectors_deleted: int = 0
    vectors_upserted: int = 0
    reembed_report: VectorWriteReport | None = None


@runtime_checkable
class RevertStage(Protocol):
    """One revert stage — see the module docstring for the fixed, four-stage order."""

    name: str

    async def run(self, rctx: RevertContext) -> None:
        """Do this stage's work against ``rctx``, recording its outcome on ``rctx.tracker``."""
        ...


class RevertDeleteStage:
    """Key-addressed delete of every vector belonging to a now-removed document."""

    name = "revert_delete"

    async def run(self, rctx: RevertContext) -> None:
        """Delete every vector for a document the target snapshot no longer has."""
        if rctx.vector_store is None:
            rctx.tracker.skipped(self.name, "VECTOR_BACKEND=none")
            return
        for doc_id in sorted(rctx.remove_doc_ids):
            known_keys = rctx.cur_vector_manifest.get(doc_id, {}).get("keys")
            deleted = await rctx.vector_store.delete_by_doc_id(doc_id, known_keys=known_keys)
            rctx.vectors_deleted += deleted
            rctx.target_vector_manifest.pop(doc_id, None)
        rctx.tracker.success(self.name)


class RevertReembedStage:
    """Re-embeds documents whose content differs between current and target.

    A partial failure here fails the WHOLE revert (``REVERT_PARTIAL_REEMBED``)
    — see the module docstring for why this deliberately does not tolerate
    partial success the way a normal build does.
    """

    name = "revert_reembed"

    async def run(self, rctx: RevertContext) -> None:
        """Re-embed every document whose content differs, failing the revert on any failure."""
        if rctx.vector_store is None or not rctx.reembed_doc_ids:
            rctx.tracker.skipped(self.name, "nothing to re-embed")
            return

        for doc_id in sorted(rctx.reembed_doc_ids):
            known_keys = (
                rctx.cur_vector_manifest.get(doc_id, {}).get("keys")
                or rctx.target_vector_manifest.get(doc_id, {}).get("keys")
            )
            if known_keys:
                rctx.vectors_deleted += await rctx.vector_store.delete_keys(known_keys)

        store = LocalContentStore(rctx.config, rctx.to_version)
        all_items = await asyncio.to_thread(store.list_items, rctx.project)
        by_id = {item.id: item for item in all_items}
        items_to_embed = [by_id[d] for d in sorted(rctx.reembed_doc_ids) if d in by_id]
        if not items_to_embed:
            rctx.tracker.success(self.name)
            return

        chunk_pipeline = ChunkPipeline(rctx.config.chunking())
        preparer = DocumentPreparer(chunk_pipeline, rctx.llm, rctx.config, observer=rctx.tracker)
        writer = VectorWriter(
            rctx.vector_store, preparer, rctx.config.layout, rctx.config,
            tracker=rctx.tracker, observer=rctx.tracker,
        )
        report = await writer.write(items_to_embed, snapshot_version=rctx.to_version)
        rctx.reembed_report = report
        rctx.vectors_upserted += report.vectors_upserted
        rctx.target_vector_manifest.update(report.manifest_entries)

        if report.failed:
            failed_ids = [f.doc_id for f in report.failed]
            exc = RuntimeError(
                f"{len(failed_ids)} document(s) failed re-embedding during revert: {failed_ids}"
            )
            rctx.tracker.error(self.name, "REVERT_PARTIAL_REEMBED", exc)
            raise exc

        rctx.tracker.success(self.name)


def _render_index_manifest_block(manifest: dict[str, Any]) -> str:
    return "## Index manifest\n\n```json\n" + json.dumps(manifest, indent=2) + "\n```\n"


def _republish_index_manifest_block(context_md_path: Path, manifest: dict[str, Any]) -> None:
    """Surgically refresh the ``## Index manifest`` fenced block in-place.

    The rest of ``context.md`` (narrative sections) was correct when this
    snapshot version was first built and is not re-synthesized on revert —
    only the manifest block, which must reflect the post-revert vector
    state, is touched.
    """
    if not context_md_path.is_file():
        return
    text = context_md_path.read_text(encoding="utf-8")
    block = _render_index_manifest_block(manifest)
    if _INDEX_MANIFEST_BLOCK_RE.search(text):
        new_text = _INDEX_MANIFEST_BLOCK_RE.sub(block, text, count=1)
    else:
        sep = "" if text.endswith("\n") else "\n"
        new_text = f"{text}{sep}\n{block}"
    context_md_path.write_text(new_text, encoding="utf-8")


class RevertManifestStage:
    """Republishes both manifests and the ``context.md`` manifest block.

    Also deletes orphaned ``chunks/*.jsonl`` for removed documents.
    """

    name = "revert_manifest"

    async def run(self, rctx: RevertContext) -> None:
        """Republish manifests/context.md block; delete orphaned chunk files."""
        if rctx.vector_store is None:
            rctx.tracker.skipped(self.name, "VECTOR_BACKEND=none")
            return

        for doc_id in sorted(rctx.remove_doc_ids):
            await asyncio.to_thread(
                _snapshot.delete_chunks_file, rctx.config.layout, rctx.project,
                doc_id.split(":", 1)[-1], version=rctx.to_version,
            )

        await asyncio.to_thread(
            _snapshot.write_vector_manifest,
            rctx.config.vector_manifest_path(rctx.project, version=rctx.to_version),
            rctx.target_vector_manifest,
        )

        store_info = await rctx.vector_store.describe()
        manifest = _snapshot.build_index_manifest(
            project_id=rctx.project,
            snapshot_version=rctx.to_version,
            context_version=rctx.config.version_label_to_number(rctx.to_version),
            backend=rctx.config.vector_backend,
            store=str(store_info.get("store", "")),
            index=str(store_info.get("index", "")),
            region=str(store_info.get("region", rctx.config.s3v_region)),
            distance_metric=str(store_info.get("distance_metric", "cosine")),
            embedding_model=f"{rctx.config.embedding_provider}/{rctx.config.embedding_model}",
            embedding_dimension=rctx.config.embedding_dimension,
            vector_manifest=rctx.target_vector_manifest,
            generated_at=datetime.now(UTC).isoformat(),
        )
        await asyncio.to_thread(
            _snapshot.write_index_manifest,
            rctx.config.index_manifest_path(rctx.project, version=rctx.to_version),
            manifest,
        )
        await asyncio.to_thread(
            _republish_index_manifest_block,
            rctx.config.resolve_context_md_path(rctx.project, version=rctx.to_version),
            manifest,
        )
        rctx.tracker.success(self.name)


class RevertPointerStage:
    """Flips ``current.txt`` — only reached once every earlier stage has succeeded."""

    name = "revert_pointer"

    async def run(self, rctx: RevertContext) -> None:
        """Flip ``current.txt`` to the target version."""
        await asyncio.to_thread(
            write_current_pointer, rctx.config.current_pointer_path(rctx.project), rctx.to_version,
        )
        rctx.tracker.success(self.name)


_REVERT_STAGES: tuple[type[RevertStage], ...] = (
    RevertDeleteStage, RevertReembedStage, RevertManifestStage, RevertPointerStage,
)


async def _run_revert_stages(rctx: RevertContext) -> None:
    """Deterministic, fixed-order execution; any stage failing aborts the rest."""
    for stage_cls in _REVERT_STAGES:
        stage = stage_cls()
        async with rctx.tracker.astage(stage.name, project=rctx.project, version=rctx.to_version):
            await stage.run(rctx)


async def revert_context_to(
    project: str,
    config: ContextConfig,
    to_version: str | int,
) -> RevertResult:
    """Point ``current.txt`` at ``to_version`` and reconcile the vector index.

    Hash diff is computed between the active snapshot and the target
    snapshot. If already on ``to_version``, returns success without
    mutating vectors or the pointer.
    """
    t0 = time.monotonic()
    logger.info("stage_start stage=revert_context_to project=%s target=%s", project, to_version)
    errors: list[ErrorDetail] = []
    reverted_at = datetime.now(UTC).isoformat()
    target = config.normalize_version_label(to_version)
    llm = LLMClient.from_config(config)

    config.migrate_flat_layout_to_v1(project)
    cur = config.read_current_version_label(project)
    if not cur:
        return RevertResult(
            project=project, status="error", message="No snapshot to revert (no current.txt).",
            reverted_at=reverted_at, errors=errors,
        )

    target_state_path = config.state_path(project, version=target)
    if not target_state_path.is_file():
        return RevertResult(
            project=project, status="error",
            message=f"Target snapshot {target!r} has no state.json.",
            from_version=config.version_label_to_number(cur),
            to_version=config.version_label_to_number(target),
            reverted_at=reverted_at, errors=errors,
        )

    if cur == target:
        return RevertResult(
            project=project, status="success", message=f"Already at snapshot {target}; no changes.",
            from_version=config.version_label_to_number(cur),
            to_version=config.version_label_to_number(target),
            reverted_at=reverted_at, vectors_deleted=0, vectors_upserted=0, errors=errors,
        )

    try:
        cur_hashes = load_previous_state(config.state_path(project, version=cur))
        target_hashes = load_previous_state(target_state_path)
    except StateCorrupt as exc:
        logger.exception("revert_context_to: corrupt state.json")
        errors.append(ErrorDetail(stage="revert", code="STATE_CORRUPT", message=str(exc)))
        return RevertResult(
            project=project, status="error", message=str(exc),
            from_version=config.version_label_to_number(cur),
            to_version=config.version_label_to_number(target),
            reverted_at=reverted_at, errors=errors,
        )

    remove_doc_ids = set(cur_hashes) - set(target_hashes)
    reembed_doc_ids = (set(target_hashes) - set(cur_hashes)) | {
        d for d in set(cur_hashes) & set(target_hashes) if cur_hashes[d] != target_hashes[d]
    }

    vector_store = create_vector_store(config, project)
    cur_vector_manifest: dict[str, dict[str, Any]] = {}
    target_vector_manifest: dict[str, dict[str, Any]] = {}
    if vector_store is not None:
        cur_vector_manifest = _snapshot.read_vector_manifest(
            config.vector_manifest_path(project, version=cur)
        )
        target_vector_manifest = dict(
            _snapshot.read_vector_manifest(config.vector_manifest_path(project, version=target))
        )

    tracker = PipelineTracker()
    rctx = RevertContext(
        project=project, config=config, from_version=cur, to_version=target,
        tracker=tracker, llm=llm, vector_store=vector_store,
        cur_hashes=cur_hashes, target_hashes=target_hashes,
        cur_vector_manifest=cur_vector_manifest, target_vector_manifest=target_vector_manifest,
        remove_doc_ids=remove_doc_ids, reembed_doc_ids=reembed_doc_ids,
    )

    try:
        await _run_revert_stages(rctx)
    except Exception as exc:
        logger.exception("revert_context_to failed")
        # Prefer the specific code a stage already recorded for itself
        # (e.g. REVERT_PARTIAL_REEMBED) over the generic fallback.
        code = tracker.errors[-1].code if tracker.errors else "REVERT_ERROR"
        errors.append(ErrorDetail(stage="revert", code=code, message=str(exc)))
        await asyncio.to_thread(
            append_raw_activity, config, project,
            {
                "run_id": uuid.uuid4().hex, "timestamp": reverted_at, "action": "revert",
                "status": "error", "project": project, "from_version": cur, "to_version": target,
                "vectors_deleted": rctx.vectors_deleted, "vectors_upserted": rctx.vectors_upserted,
                "duration_ms": _ms_since(t0), "error": str(exc),
            },
        )
        logger.info(
            "stage_done stage=revert_context_to project=%s status=error duration_ms=%d",
            project, _ms_since(t0),
        )
        return RevertResult(
            project=project, status="error", message=str(exc),
            from_version=config.version_label_to_number(cur),
            to_version=config.version_label_to_number(target),
            reverted_at=reverted_at, vectors_deleted=rctx.vectors_deleted,
            vectors_upserted=rctx.vectors_upserted, errors=errors,
        )

    await asyncio.to_thread(
        append_raw_activity, config, project,
        {
            "run_id": uuid.uuid4().hex, "timestamp": reverted_at, "action": "revert",
            "status": "success", "project": project, "from_version": cur, "to_version": target,
            "vectors_deleted": rctx.vectors_deleted, "vectors_upserted": rctx.vectors_upserted,
            "duration_ms": _ms_since(t0),
        },
    )
    logger.info(
        "stage_done stage=revert_context_to project=%s status=success duration_ms=%d",
        project, _ms_since(t0),
    )
    return RevertResult(
        project=project, status="success",
        message=f"Reverted active snapshot from {cur} to {target}.",
        from_version=config.version_label_to_number(cur),
        to_version=config.version_label_to_number(target),
        reverted_at=reverted_at, vectors_deleted=rctx.vectors_deleted,
        vectors_upserted=rctx.vectors_upserted, errors=errors,
    )


async def revert_context(project: str, config: ContextConfig) -> RevertResult:
    """Point active snapshot at previous version and reconcile vectors."""
    logger.info("stage_start stage=revert_context project=%s", project)
    reverted_at = datetime.now(UTC).isoformat()
    errors: list[ErrorDetail] = []

    config.migrate_flat_layout_to_v1(project)
    cur = config.read_current_version_label(project)
    if not cur:
        return RevertResult(
            project=project, status="error", message="No snapshot to revert (no current.txt).",
            reverted_at=reverted_at, errors=errors,
        )
    prev = config.previous_version_label(cur)
    if prev is None:
        return RevertResult(
            project=project, status="error", message="Already at earliest snapshot; cannot revert.",
            from_version=config.version_label_to_number(cur),
            reverted_at=reverted_at, errors=errors,
        )

    prev_state_path = config.state_path(project, version=prev)
    if not prev_state_path.is_file():
        return RevertResult(
            project=project, status="error", message=f"Previous snapshot {prev} has no state.json.",
            from_version=config.version_label_to_number(cur),
            to_version=config.version_label_to_number(prev),
            reverted_at=reverted_at, errors=errors,
        )

    result = await revert_context_to(project, config, prev)
    logger.info(
        "stage_done stage=revert_context project=%s status=%s", project, result.status,
    )
    return result

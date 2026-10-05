"""Streaming chunk + embed + upsert for the vector index, one document at a
time, with per-document failure isolation.

Scope & functionality
----------------------
``VectorWriter.write`` is the memory fix (audit §5.12): a bounded number of
producer coroutines (``Semaphore(config.vector_concurrency)``) call
``DocumentPreparer.prepare`` and push each finished ``PreparedDocument``
into a small bounded ``asyncio.Queue`` (``maxsize=2*concurrency``); exactly
ONE consumer coroutine drains the queue, and for each document does
``upsert -> write_chunks_file (temp file + os.replace) -> manifest entry ->
observer.document_done -> release`` before moving to the next one. No flat
``list[VectorChunk]`` of every chunk of every document is ever held at
once — the deleted legacy ``embed_and_upsert`` free function built exactly
that list (``report.chunks``), which was ~1.1GB resident for a
1,000-document project before the first upsert. Lane C-F migrated its last
callers (``pipeline/build.py``, ``pipeline/revert.py``) onto
:class:`VectorWriter` and removed it.

Per-document isolation is strict: a ``MetadataTooLarge``/embedding/upsert
error for ONE document is caught right there, recorded as a
:class:`DocVectorFailure`, and the writer makes a best-effort
``delete_keys()`` call for any partial writes that document may have left
behind — the rest of the batch is untouched and keeps going.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from evoq_context.core.models import ContextItem
from evoq_context.core.observer import ContextObserver, DocumentRecord, NullObserver
from evoq_context.storage import snapshot as _snapshot
from evoq_context.vector.embedder import DocumentPreparer, PreparedDocument
from evoq_context.vector.interface import VectorChunk, VectorStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    from evoq_context.core.config import ContextConfig
    from evoq_context.storage.layout import SnapshotLayout

logger = logging.getLogger(__name__)

#: Per-document failure codes (plan §2.4 / design §4).
_KNOWN_FAILURE_CODES = frozenset(
    {
        "METADATA_TOO_LARGE",
        "EMBEDDING_INPUT_TOO_LARGE",
        "EMBEDDING_ERROR",
        "CHUNK_ERROR",
        "SCANNED_PDF_UNSUPPORTED",
        "CHUNK_LIMIT_EXCEEDED",
    }
)


def _classify_failure(exc: Exception) -> str:
    name = type(exc).__name__
    if name == "MetadataTooLarge":
        return "METADATA_TOO_LARGE"
    if name == "EmbeddingInputTooLarge":
        return "EMBEDDING_INPUT_TOO_LARGE"
    if name == "EmbeddingValidationError":
        return "EMBEDDING_ERROR"
    if name == "ChunkLimitExceeded":
        return "CHUNK_LIMIT_EXCEEDED"
    return "CHUNK_ERROR"


@dataclass(slots=True)
class DocVectorFailure:
    """One document that failed chunking/embedding/upserting, isolated
    from the rest of the batch.
    """

    doc_id: str
    code: str
    error: str


@dataclass(slots=True)
class VectorWriteReport:
    """Outcome of one :meth:`VectorWriter.write` call.

    Deliberately holds no retained chunk list (the memory fix) — every
    chunk's full text is already on disk in ``chunks/{source_id}.jsonl`` by
    the time this report is returned.
    """

    chunks_created: int = 0
    parents_stored: int = 0
    vectors_upserted: int = 0
    failed: list[DocVectorFailure] = field(default_factory=list)
    manifest_entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_doc: list[DocumentRecord] = field(default_factory=list)
    strategy_histogram: dict[str, int] = field(default_factory=dict)
    #: A representative sample of per-chunk token estimates (bounded size —
    #: never every chunk of every document), for percentile reporting.
    token_samples: list[int] = field(default_factory=list)

    @property
    def partial_success(self) -> bool:
        """Some documents failed, but at least one vector was written."""
        return bool(self.failed) and self.vectors_upserted > 0

    @property
    def all_failed(self) -> bool:
        return bool(self.failed) and self.vectors_upserted == 0


_TOKEN_SAMPLE_CAP = 2000


class VectorWriter:
    """Stream ``ContextItem``s through ``DocumentPreparer`` -> ``upsert`` ->
    ``chunks/*.jsonl`` -> manifest, one document at a time, bounded memory.
    """

    def __init__(
        self,
        store: VectorStore,
        preparer: DocumentPreparer,
        layout: SnapshotLayout,
        config: ContextConfig,
        tracker: Any = None,
        observer: ContextObserver | None = None,
    ) -> None:
        self._store = store
        self._preparer = preparer
        self._layout = layout
        self._config = config
        self._tracker = tracker
        self._observer = observer or NullObserver()

    async def write(
        self,
        items: list[ContextItem],
        *,
        snapshot_version: str,
        chunks_version: str | None = None,
    ) -> VectorWriteReport:
        """Chunk, embed, upsert and persist every item — one document in
        flight per queue slot, never the whole batch's chunks at once.

        ``chunks_version`` is the snapshot version ``chunks/*.jsonl`` is
        written under; it defaults to ``snapshot_version`` (they differ
        only for a staged ``{vN}.tmp`` write).
        """
        report = VectorWriteReport()
        if not items:
            return report

        version_for_chunks = chunks_version or snapshot_version
        concurrency = max(1, self._config.vector_concurrency)
        sem = asyncio.Semaphore(concurrency)
        # Bounded so producers block (backpressure) once 2x concurrency
        # documents are prepared-but-not-yet-consumed — the queue, not a
        # flat accumulator list, is what keeps memory bounded regardless of
        # how many documents are in this batch.
        queue: asyncio.Queue[tuple[ContextItem, PreparedDocument | None, Exception | None]] = (
            asyncio.Queue(maxsize=max(1, 2 * concurrency))
        )

        async def _produce(item: ContextItem) -> None:
            async with sem:
                try:
                    prepared = await self._preparer.prepare(item, snapshot_version=snapshot_version)
                    await queue.put((item, prepared, None))
                except Exception as exc:  # noqa: BLE001 - isolated per document below
                    await queue.put((item, None, exc))

        # Every producer puts exactly one tuple regardless of outcome, so
        # the consumer knows in advance exactly how many gets it needs —
        # no sentinel value or completion-event juggling required, and the
        # producer gather and the consumer drain run concurrently on the
        # same event loop (the queue provides the backpressure between them).
        producer_task = asyncio.ensure_future(
            asyncio.gather(*[_produce(item) for item in items])
        )

        try:
            for _ in range(len(items)):
                item, prepared, error = await queue.get()
                await self._consume_one(item, prepared, error, report, version_for_chunks)
        finally:
            await producer_task
        return report

    async def _consume_one(
        self,
        item: ContextItem,
        prepared: PreparedDocument | None,
        error: Exception | None,
        report: VectorWriteReport,
        chunks_version: str,
    ) -> None:
        doc_id = item.id
        if error is not None:
            code = _classify_failure(error)
            report.failed.append(DocVectorFailure(doc_id=doc_id, code=code, error=str(error)))
            logger.exception("Chunk/embed failed for doc_id=%s", doc_id, exc_info=error)
            self._observer.document_done(
                DocumentRecord(
                    doc_id=doc_id, strategy="", chunk_count=0, parent_count=0,
                    status="error", failure_code=code,
                )
            )
            return

        assert prepared is not None
        written_keys: list[str] = []
        try:
            if prepared.vector_chunks:
                try:
                    upsert_result = await self._store.upsert(
                        prepared.vector_chunks, observer=self._observer,  # type: ignore[call-arg]
                    )
                except TypeError:
                    # A VectorStore implementation outside this lane's two
                    # concrete backends that doesn't accept `observer` yet.
                    upsert_result = await self._store.upsert(prepared.vector_chunks)
                written_keys = list(upsert_result.keys)
                report.vectors_upserted += upsert_result.upserted_count

            all_records: list[VectorChunk | Any] = [*prepared.vector_chunks, *prepared.stored_chunks]
            if all_records:
                await asyncio.to_thread(
                    _snapshot.write_chunks_file,
                    self._layout, item.project, item.source_id, all_records,
                    version=chunks_version,
                )
        except Exception as exc:  # noqa: BLE001 - isolate this document only
            code = _classify_failure(exc)
            report.failed.append(DocVectorFailure(doc_id=doc_id, code=code, error=str(exc)))
            logger.exception("Upsert/write failed for doc_id=%s", doc_id)
            if written_keys:
                try:
                    await self._store.delete_keys(written_keys)
                except Exception:
                    logger.exception(
                        "Best-effort cleanup of %d partially-written key(s) for "
                        "doc_id=%s also failed", len(written_keys), doc_id,
                    )
            self._observer.document_done(
                DocumentRecord(
                    doc_id=doc_id, strategy=prepared.strategy,
                    chunk_count=len(prepared.vector_chunks),
                    parent_count=len(prepared.stored_chunks),
                    status="error", failure_code=code,
                )
            )
            return

        report.chunks_created += len(prepared.vector_chunks)
        report.parents_stored += len(prepared.stored_chunks)
        if prepared.manifest_entry:
            report.manifest_entries[doc_id] = prepared.manifest_entry
        strategy = prepared.strategy or "unknown"
        report.strategy_histogram[strategy] = (
            report.strategy_histogram.get(strategy, 0) + len(prepared.vector_chunks)
        )
        if len(report.token_samples) < _TOKEN_SAMPLE_CAP and prepared.stats is not None:
            for field_name in ("token_p50", "token_p95", "token_max"):
                value = getattr(prepared.stats, field_name, None)
                if isinstance(value, int):
                    report.token_samples.append(value)

        stats = prepared.stats
        self._observer.document_done(
            DocumentRecord(
                doc_id=doc_id, strategy=strategy,
                chunk_count=len(prepared.vector_chunks),
                parent_count=len(prepared.stored_chunks),
                token_p50=getattr(stats, "token_p50", 0) or 0,
                token_p95=getattr(stats, "token_p95", 0) or 0,
                status="success",
            )
        )


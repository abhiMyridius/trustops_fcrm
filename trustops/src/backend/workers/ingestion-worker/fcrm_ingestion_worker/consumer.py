"""H3's consumer: one ``ingest.run`` in, ``ingest.done`` or ``ingest.failed`` out
(design A §7.1).

For each job: read the upload from the object store into a temporary
directory, run the real extraction (:func:`fcrm_ingestion_worker.jobs
.ingest_document.handle`: the cloned engine, then Guard 1) under a deadline,
and write what came out back to the object store, under the job's target
prefix, as ``<doc_id>.md`` (the masked canonical Markdown) and
``<doc_id>.anchored.md`` (the page:line copy agents cite, see
:func:`fcrm_ingestion_worker.anchors.render_anchored`). Then publish the
result on ``case.events``.

**What is a failure and what is a retry.** A document the engine cannot read
(a scan, an unsupported type), one Guard 1 quarantines, one that is missing
from the store, one that outruns the deadline: each is an answer, published as
``ingest.failed`` with a ``code`` so H1 can tell the PO. An outage (the object
store or the broker unreachable) is not an answer about the document: it is
raised, and :class:`fcrm_common.events.Consumer` retries the message and then
dead-letters it, so nothing is lost.

**Safe to run twice.** The consumer is built without a Mongo client, so the
dedupe marker is written after the handler succeeds and a crash in between
re-runs it. That is harmless: the writes are to fixed keys, and the result
events carry an id derived from the job (``deterministic_id("evt", job_id,
type)``), so a second ``ingest.done`` is a duplicate H1's own consumer drops.

**Never log document text.** Logs carry ids, sizes, page counts, status and
failure codes only.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final, Optional

from evoq_ingestion.core.config import PdfEngine
from fcrm_common.errors import NotFoundError
from fcrm_common.events import Consumer, Publisher, build_envelope, default_exchange
from fcrm_common.events import publish as publish_event
from fcrm_common.ids import deterministic_id
from fcrm_common.mongo import Document
from fcrm_common.objectstore import ObjectStore
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import IngestDone, IngestFailed, IngestRun
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_ingestion_worker.anchors import render_anchored
from fcrm_ingestion_worker.jobs import ingest_document
from fcrm_ingestion_worker.jobs.ingest_document import (
    IngestDocumentPayload,
    IngestResult,
)
from fcrm_ingestion_worker.topology import (
    INGESTION_RUN_QUEUE,
    ROUTING_KEY_INGEST_DONE,
    ROUTING_KEY_INGEST_FAILED,
    ROUTING_KEY_INGEST_RUN,
)

logger = logging.getLogger(__name__)

MARKDOWN_CONTENT_TYPE: Final = "text/markdown; charset=utf-8"

#: What a document id may look like inside an object key: no separators, no
#: leading dot, so it can never climb out of the job's target prefix.
_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

#: The extraction call: ``handle`` of the ingest job, injectable for tests.
IngestFn = Callable[..., Awaitable[IngestResult]]

#: PDF libraries that log the text they parse at ``DEBUG`` (pdfminer writes
#: every token). A deployment that turns the root logger to ``DEBUG`` would put
#: document text in the logs, so they are held at ``WARNING``.
_CONTENT_LOGGERS: Final = ("pdfminer", "pdfplumber", "pypdf")


def hold_content_loggers_at_warning() -> None:
    """Keep the PDF libraries from logging document text, whatever the root
    level is. Idempotent; called when the consumer is built.
    """
    for name in _CONTENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _safe_file_name(key: str, doc_id: str) -> str:
    """The name the upload is written under in the temporary directory: the
    last segment of its object key (the engine picks the format from the
    extension), or ``doc_id`` when that segment is not a plain file name.
    """
    name = PurePosixPath(key).name
    return name if name not in ("", ".", "..") else doc_id


class IngestionConsumer(Consumer):
    """Consumes :data:`~fcrm_ingestion_worker.topology.INGESTION_RUN_QUEUE`.

    Args:
        consumed: The ``consumed_messages`` collection (dedupe).
        publisher: Publishes the result events and the retry/DLQ copies.
        store: Reads uploads, writes canonical Markdown.
        timeout_s: The deadline for extracting one document.
        pdf_engine: Passed to the extraction (``pdfplumber`` or ``pypdf``).
        pdf_min_chars_per_page: Passed to the extraction (engine default if
            ``None``).
        extra_keep_terms: Terms Guard 1 never masks, on top of each job's own
            (``GUARD1_ALLOWLIST_FILE``).
        ingest: The extraction call; the real one by default.
        max_attempts: Retries before the dead-letter queue.
    """

    def __init__(
        self,
        consumed: AsyncCollection[Document],
        publisher: Publisher,
        *,
        store: ObjectStore,
        timeout_s: float,
        pdf_engine: PdfEngine = "pdfplumber",
        pdf_min_chars_per_page: Optional[int] = None,
        extra_keep_terms: Sequence[str] = (),
        ingest: IngestFn = ingest_document.handle,
        max_attempts: int = 3,
    ) -> None:
        """Wire the consumer. It is built without a Mongo client on purpose:
        extraction and object-store writes must not sit inside a transaction.
        """
        super().__init__(
            INGESTION_RUN_QUEUE,
            consumed,
            publisher,
            client=None,
            max_attempts=max_attempts,
        )
        hold_content_loggers_at_warning()
        self._store = store
        self._timeout_s = timeout_s
        self._pdf_engine = pdf_engine
        self._pdf_min_chars = pdf_min_chars_per_page
        self._extra_keep_terms = tuple(extra_keep_terms)
        self._ingest = ingest

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        """Run one ``ingest.run`` job and publish its result.

        Raises:
            ValueError: the message is not an ``ingest.run`` (a misrouted
                message cannot be processed; it is retried, then parked).
            pydantic.ValidationError: the payload is not an
                :class:`~fcrm_contracts.events.payloads.IngestRun`.
            fcrm_common.errors.UpstreamUnavailableError: the object store is
                unreachable.
        """
        if envelope.type != ROUTING_KEY_INGEST_RUN:
            raise ValueError(f"{INGESTION_RUN_QUEUE} cannot process {envelope.type!r}")
        job = IngestRun.model_validate(envelope.payload)
        invalid = self._invalid_reason(job, envelope)
        if invalid is not None:
            await self._fail(job, envelope, "invalid_job", invalid)
            return
        try:
            data = await self._store.get_bytes(job.source.bucket, job.source.key)
        except NotFoundError:
            await self._fail(job, envelope, "source_not_found", "upload not found")
            return
        doc_id, case_id = str(job.doc_id), str(envelope.case_id)
        result = await self._extract(job, doc_id, case_id, data)
        if result is None:
            await self._fail(job, envelope, "timeout", "extraction timed out")
        elif result.status == "COMPLETE":
            await self._complete(job, envelope, doc_id, result)
        else:
            await self._fail(
                job,
                envelope,
                result.failure_code or result.status.lower(),
                result.status.lower(),
                quarantined=result.status == "QUARANTINED",
                guard1=result.guard1,
            )

    @staticmethod
    def _invalid_reason(job: IngestRun, envelope: Envelope) -> Optional[str]:
        """Why ``job`` cannot be processed, or ``None``. Only a case document
        with a plain id and a target prefix inside its bucket is.
        """
        if job.purpose != "case_document":
            return f"unsupported purpose {job.purpose}"
        if not job.doc_id or not _SAFE_ID.match(job.doc_id):
            return "the job names no usable document id"
        if not envelope.case_id:
            return "the job names no case"
        if ".." in PurePosixPath(job.target.key_prefix).parts:
            return "the target prefix climbs out of its bucket"
        return None

    async def _extract(
        self, job: IngestRun, doc_id: str, case_id: str, data: bytes
    ) -> Optional[IngestResult]:
        """The extraction result, or ``None`` when it ran past the deadline."""
        with tempfile.TemporaryDirectory(prefix="ingest-") as tmp:
            path = Path(tmp) / _safe_file_name(job.source.key, doc_id)
            await asyncio.to_thread(path.write_bytes, data)
            payload = IngestDocumentPayload(
                case_id=case_id,
                doc_id=doc_id,
                path=str(path),
                keep_terms=[*job.keep_terms, *self._extra_keep_terms],
            )
            try:
                return await asyncio.wait_for(
                    self._ingest(
                        payload,
                        pdf_engine=self._pdf_engine,
                        pdf_min_chars_per_page=self._pdf_min_chars,
                    ),
                    timeout=self._timeout_s,
                )
            except TimeoutError:
                logger.warning(
                    "extraction ran past its deadline",
                    extra={"doc_id": doc_id, "timeout_s": self._timeout_s},
                )
                return None

    async def _complete(
        self, job: IngestRun, envelope: Envelope, doc_id: str, result: IngestResult
    ) -> None:
        """Write the canonical and anchored Markdown, then announce them."""
        prefix = job.target.key_prefix.rstrip("/") + "/"
        canonical_key = f"{prefix}{doc_id}.md"
        anchored_key = f"{prefix}{doc_id}.anchored.md"
        anchored = render_anchored(result.markdown, doc_id=doc_id)
        bucket = job.target.bucket
        await self._store.put_bytes(
            bucket,
            canonical_key,
            result.markdown.encode("utf-8"),
            MARKDOWN_CONTENT_TYPE,
        )
        await self._store.put_bytes(
            bucket, anchored_key, anchored.encode("utf-8"), MARKDOWN_CONTENT_TYPE
        )
        done = IngestDone(
            job_id=job.job_id,
            doc_id=doc_id,
            canonical_key=canonical_key,
            anchored_key=anchored_key,
            pages=result.page_count,
            page_map={str(n): span for n, span in sorted(result.pages.items())},
            guard1=dict(result.guard1 or {}),
            warnings=list(result.warnings),
        )
        logger.info(
            "document extracted",
            extra={
                "doc_id": doc_id,
                "pages": result.page_count,
                "bytes": len(result.markdown),
            },
        )
        await self._publish(
            ROUTING_KEY_INGEST_DONE, done.model_dump(mode="json"), job, envelope
        )

    async def _fail(
        self,
        job: IngestRun,
        envelope: Envelope,
        code: str,
        detail: str,
        *,
        quarantined: bool = False,
        guard1: Optional[dict[str, object]] = None,
    ) -> None:
        """Announce that the document cannot be used, with a ``code``."""
        failed = IngestFailed(
            job_id=job.job_id,
            doc_id=job.doc_id,
            code=code,
            detail=detail,
            quarantined=quarantined,
            guard1=dict(guard1 or {}),
        )
        logger.warning(
            "document could not be ingested",
            extra={"doc_id": job.doc_id, "code": code, "quarantined": quarantined},
        )
        await self._publish(
            ROUTING_KEY_INGEST_FAILED, failed.model_dump(mode="json"), job, envelope
        )

    async def _publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        job: IngestRun,
        envelope: Envelope,
    ) -> None:
        """Publish ``payload`` on ``case.events`` with an id derived from the
        job, so a rerun of the job announces the same event.
        """
        built = build_envelope(
            event_type,
            payload,
            case_id=envelope.case_id,
            run_id=envelope.run_id,
            correlation_id=envelope.correlation_id,
        )
        event = built.model_copy(
            update={"event_id": deterministic_id("evt", job.job_id, event_type)}
        )
        await publish_event(
            self._publisher, default_exchange(event_type), event_type, event
        )

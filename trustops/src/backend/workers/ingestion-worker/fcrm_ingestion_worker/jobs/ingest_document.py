"""``ingest.run`` job: one uploaded document -> clean, anchored Markdown.

Pipeline (design A §7.1): extract with the cloned ``evoq_ingestion`` engine
(in-process engines only; OCR is out of scope, so a scanned PDF fails with
``scanned_pdf_no_ocr``) -> Guard 1 over the extracted Markdown -> page:line
anchors computed on the *masked* text (the text agents will cite).

``handle`` is deliberately transport-free: the FastStream consumer that
wraps it (``ingest.run`` envelope in, ``ingest.done`` out, lease, DLQ,
MinIO writes to ``canonical/{case_id}/{doc_id}.md``) lands with
``fcrm_common``. Until then this is the real entry point tests and the
future consumer both call.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

from evoq_ingestion import (
    DEFAULT_UPLOAD_ALLOWED_EXTENSIONS,
    ExtractionError,
    ExtractionOptions,
    UnsupportedFormat,
    extract_file,
    failure_code_of,
)
from pydantic import BaseModel, ConfigDict, Field

from fcrm_ingestion_worker.anchors import compute_page_line_offsets
from fcrm_ingestion_worker.guard1 import Guard1Outcome, scan_document

logger = logging.getLogger(__name__)

IngestStatus = Literal["COMPLETE", "FAILED", "UNSUPPORTED", "QUARANTINED"]


class IngestDocumentPayload(BaseModel):
    """The job input (the ``ingest.run`` payload a consumer unwraps)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    doc_id: str
    path: str
    #: Values that must survive PII masking (seeded users, partner entity).
    keep_terms: list[str] = Field(default_factory=list)


class IngestResult(BaseModel):
    """The job output (becomes the ``ingest.done`` payload)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    doc_id: str
    status: IngestStatus
    failure_code: Optional[str] = None
    #: Masked canonical Markdown; empty unless ``status == "COMPLETE"``.
    markdown: str = ""
    page_count: int = 0
    #: page -> (line_start, line_end), absolute 1-based lines in ``markdown``.
    pages: dict[int, tuple[int, int]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    #: Audit-safe Guard 1 facts (counts and pattern ids, never PII values).
    guard1: Optional[dict[str, object]] = None


def _extraction_options() -> ExtractionOptions:
    """Production options: in-process engines, the upload allow-list, pdfplumber."""
    return ExtractionOptions(
        engine_mode="inprocess",
        allowed_extensions=DEFAULT_UPLOAD_ALLOWED_EXTENSIONS,
        pdf_engine="pdfplumber",
    )


def _failed(payload: IngestDocumentPayload, error: ExtractionError) -> IngestResult:
    status: IngestStatus = (
        "UNSUPPORTED" if isinstance(error, UnsupportedFormat) else "FAILED"
    )
    logger.info(
        "ingest %s/%s %s: %s", payload.case_id, payload.doc_id, status, error.warning
    )
    return IngestResult(
        case_id=payload.case_id,
        doc_id=payload.doc_id,
        status=status,
        failure_code=str(failure_code_of(error)),
        warnings=[error.warning],
    )


def _from_guard(
    payload: IngestDocumentPayload, outcome: Guard1Outcome, warnings: list[str]
) -> IngestResult:
    summary = outcome.audit_summary()
    if outcome.masked_markdown is None:
        return IngestResult(
            case_id=payload.case_id,
            doc_id=payload.doc_id,
            status="QUARANTINED",
            failure_code="guard1_injection_blocked",
            warnings=warnings,
            guard1=summary,
        )
    pages = compute_page_line_offsets(outcome.masked_markdown)
    return IngestResult(
        case_id=payload.case_id,
        doc_id=payload.doc_id,
        status="COMPLETE",
        markdown=outcome.masked_markdown,
        page_count=len(pages),
        pages=pages,
        warnings=warnings,
        guard1=summary,
    )


async def handle(payload: IngestDocumentPayload) -> IngestResult:
    """Extract, Guard-1-scan and anchor one document. Never raises on bad input.

    Engine refusals become ``FAILED``/``UNSUPPORTED`` results carrying the
    engine's ``lower_snake`` failure code; a Guard 1 block becomes
    ``QUARANTINED`` with no Markdown at all.
    """
    try:
        doc = await extract_file(Path(payload.path), options=_extraction_options())
    except ExtractionError as error:
        return _failed(payload, error)
    warnings = [str(w) for w in doc.extraction.get("warnings", [])]
    outcome = scan_document(
        payload.doc_id, doc.body_markdown, keep_terms=payload.keep_terms
    )
    return _from_guard(payload, outcome, warnings)

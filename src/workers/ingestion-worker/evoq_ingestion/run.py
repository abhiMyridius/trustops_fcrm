"""Public API of ``evoq_ingestion`` — the one module reusers import.

Scope & functionality
---------------------
``run_ingestion(request) -> IngestionResult`` (uploads source; Jira and
Confluence were pruned on clone);
``extract_file(path, engine="auto") -> CanonicalDocument`` and
``extract_folder(path, ...) -> list[CanonicalDocument]`` (ad-hoc single-
file/folder extraction, independent of the multi-source request shape);
``build_request(...)`` assembles an ``IngestionRequest``. Every function
here takes plain dataclasses / paths — nothing reads the environment.
"""

from __future__ import annotations

from pathlib import Path

from evoq_ingestion.core.config import (
    EngineMode,
    ExtractionOptions,
    IngestionRequest,
    IngestionResult,
    UploadsIngestionConfig,
)
from evoq_ingestion.core.documents import CanonicalDocument
from evoq_ingestion.core.profile import compute_profile
from evoq_ingestion.core.service import IngestionService
from evoq_ingestion.extractors.registry import run_extraction
from evoq_ingestion.observability import IngestionObserver, NullObserver
from evoq_ingestion.sources.uploads_source import (
    UploadsIngestionSource,
    build_file_canonical_document,
)

# The default allow-list for ad-hoc extraction (extract_file/extract_folder).
# Upstream unioned in the MCP document-loader's extra extensions; with the
# MCP loader pruned it equals the in-process engines' set.
_ADHOC_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".docx", ".xlsx", ".csv", ".pptx", ".md", ".txt"}
)


async def run_ingestion(request: IngestionRequest) -> IngestionResult:
    """Run the ingestion pipeline (uploads) and return the result."""
    return await IngestionService.run(request)


def build_request(
    uploads: UploadsIngestionConfig | None = None,
    observer: IngestionObserver | None = None,
) -> IngestionRequest:
    """Helper to assemble an ``IngestionRequest`` with defaults."""
    return IngestionRequest(uploads=uploads, observer=observer or NullObserver())


def adhoc_extraction_options(engine: EngineMode) -> ExtractionOptions:
    """The permissive default ``ExtractionOptions`` used by ``extract_file``/``extract_folder``.

    Public so other ad-hoc entry points (e.g. the CLI) that need the same
    allow-list but different failure handling can build on it without
    reaching into a private module attribute.
    """
    return ExtractionOptions(engine_mode=engine, allowed_extensions=_ADHOC_ALLOWED_EXTENSIONS)


async def extract_file(
    path: str | Path, *, engine: EngineMode = "auto", options: ExtractionOptions | None = None,
) -> CanonicalDocument:
    """Extract one file into a ``CanonicalDocument``.

    No folder scan. Raises the terminal engine
    exception (``UnsupportedFormat``/``ExtractionFailed``) on failure rather
    than returning a FAILED/UNSUPPORTED document, since a single explicit
    file request has no "silently skip and keep going" use case.
    """
    p = Path(path)
    opts = options or adhoc_extraction_options(engine)
    results = await run_extraction([p], opts)
    outcome = results[p]
    if isinstance(outcome, Exception):
        raise outcome
    doc = build_file_canonical_document(p, outcome)
    doc.profile_hints = compute_profile(
        doc.body_markdown, doc.structure, doc.raw_format,
        is_scanned_pdf=doc.extraction.get("is_scanned_pdf"),
    )
    return doc


async def extract_folder(
    path: str | Path, *, engine: EngineMode = "auto", options: ExtractionOptions | None = None,
) -> list[CanonicalDocument]:
    """Extract every file in a folder into ``CanonicalDocument``s.

    Includes UNSUPPORTED/FAILED rows for files that couldn't be processed —
    the same "never silently vanish" behavior as the uploads source.
    """
    folder = Path(path)
    opts = options or adhoc_extraction_options(engine)
    source = UploadsIngestionSource(UploadsIngestionConfig(folder_path=folder, extraction=opts))
    result = await source.fetch()
    documents: list[CanonicalDocument] = result["documents"]
    for doc in documents:
        if not doc.profile_hints and doc.extraction_status == "COMPLETE":
            doc.profile_hints = compute_profile(
                doc.body_markdown, doc.structure, doc.raw_format,
                is_scanned_pdf=doc.extraction.get("is_scanned_pdf"),
            )
    return documents

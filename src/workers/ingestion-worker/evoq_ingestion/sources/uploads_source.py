"""Uploads source: a folder of files (or an exact file list) -> ``CanonicalDocument`` list.

Scope & functionality
---------------------
Two ways to select the files this call processes, mirroring
``UploadsIngestionConfig``:

* ``config.files`` given — process **exactly** those paths, in one call.
  This is what a job's own batch of staged uploads should pass so a run
  never re-extracts files another job already staged into the same
  permanent directory (the "stops re-extracting the whole folder per job"
  fix, plan §1.3/§1.7 I8).
* ``config.files`` absent — fall back to scanning ``config.folder_path``
  via ``sources.scanner.FolderScanner`` (non-recursive, symlinks rejected,
  subdirectories reported rather than silently skipped).

Either way, every discovered/given path becomes a row: an unsupported
extension gets an ``UNSUPPORTED`` document, a symlink/subdirectory the
scanner rejected becomes a ``FAILED`` row carrying that rejection's own
code (``symlink_rejected`` / ``subdirectory_skipped``), and a real
extraction failure becomes a ``FAILED`` row with the engine's
``failure_code`` — nothing silently vanishes. Extraction itself runs under
``Semaphore(UPLOADS_MAX_CONCURRENCY)`` with a per-file
``asyncio.wait_for(..., UPLOADS_PER_FILE_TIMEOUT_SECONDS)`` inside
``extractors.registry.run_extraction``; the optional AI summary pass runs
**after**, driven by the job (not here) once rows exist in Mongo.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from os import stat_result
from pathlib import Path
from typing import Any

from evoq_ingestion.core.codes import WarningCode, with_detail
from evoq_ingestion.core.config import UploadsIngestionConfig
from evoq_ingestion.core.documents import CanonicalDocument
from evoq_ingestion.core.hashing import build_source_key
from evoq_ingestion.extractors.base import (
    ExtractedDocument,
    ExtractionError,
    UnsupportedFormat,
    failure_code_of,
)
from evoq_ingestion.extractors.record import ExtractionRecord
from evoq_ingestion.extractors.registry import run_extraction
from evoq_ingestion.observability import IngestionObserver, NullObserver
from evoq_ingestion.sources.base import IngestionSource
from evoq_ingestion.sources.scanner import FolderScanner, ScanEntry

logger = logging.getLogger(__name__)


def build_file_canonical_document(
    path: Path, outcome: ExtractedDocument | Exception,
) -> CanonicalDocument:
    """Build the ``CanonicalDocument`` for one file's extraction outcome.

    Module-level (not just a method) so ``evoq_ingestion.run.extract_file``
    can reuse it for ad-hoc single-file extraction without going through
    the folder-scanning ``UploadsIngestionSource``.
    """
    file_name = path.name
    stat = path.stat() if path.exists() else None
    file_size = stat.st_size if stat else None
    mtime_iso = _mtime_iso(stat)

    if isinstance(outcome, UnsupportedFormat):
        return CanonicalDocument(
            source_type="file",
            source_key=build_source_key("file", file_name=file_name),
            origin_id="",
            body_markdown="",
            raw_format=path.suffix.lstrip(".").lower() or "bin",
            title=file_name,
            source_updated_at=mtime_iso,
            source_version=mtime_iso,
            structure={},
            extraction=_failed_extraction_record(outcome),
            extraction_status="UNSUPPORTED",
            original_upload_path=str(path),
            file_name=file_name,
            file_size=file_size,
        )

    if isinstance(outcome, Exception):
        return CanonicalDocument(
            source_type="file",
            source_key=build_source_key("file", file_name=file_name),
            origin_id="",
            body_markdown="",
            raw_format=path.suffix.lstrip(".").lower() or "bin",
            title=file_name,
            source_updated_at=mtime_iso,
            source_version=mtime_iso,
            structure={},
            extraction=_failed_extraction_record(outcome),
            extraction_status="FAILED",
            original_upload_path=str(path),
            file_name=file_name,
            file_size=file_size,
        )

    doc: ExtractedDocument = outcome
    extraction = dict(doc.extraction)
    return CanonicalDocument(
        source_type="file",
        source_key=build_source_key("file", file_name=file_name),
        origin_id="",
        body_markdown=doc.body_markdown,
        raw_format=doc.raw_format,
        title=doc.title or file_name,
        source_updated_at=mtime_iso,
        source_version=mtime_iso,
        structure=doc.structure,
        extraction=extraction,
        extraction_status="COMPLETE",
        attachments=doc.attachments,
        original_upload_path=str(path),
        file_name=file_name,
        file_size=file_size,
    )


def _mtime_iso(stat: stat_result | None) -> str | None:
    if stat is None:
        return None
    return datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()


def _failed_extraction_record(outcome: Exception) -> dict[str, Any]:
    """A typed ``ExtractionRecord.to_dict()`` for a file never successfully extracted.

    Covers an unsupported extension, a gate refusal, or an engine crash —
    the row carries a real ``failure_code`` instead of a bare Python repr
    buried in ``warnings``.
    """
    engine = "none" if isinstance(outcome, UnsupportedFormat) else "unknown"
    record = ExtractionRecord(engine=engine, engine_version="0")
    if isinstance(outcome, ExtractionError):
        record.failure_code = str(failure_code_of(outcome))
        record.failure_message = str(outcome)
        record.add_warning(outcome.warning)
    else:
        record.failure_code = str(WarningCode.extraction_failed)
        record.failure_message = str(outcome)
        detail = f"{type(outcome).__name__}: {outcome}"
        record.add_warning(with_detail(WarningCode.extraction_failed, detail))
    return record.to_dict()


class UploadsIngestionSource(IngestionSource):
    """Process an exact file list (``config.files``) or scan a folder.

    Extracts every file and returns normalized documents.
    """

    name = "uploads"

    def __init__(
        self, config: UploadsIngestionConfig, observer: IngestionObserver | None = None,
    ) -> None:
        """Hold *config* and the observer per-file dispatch reports through."""
        self._config = config
        self._observer = observer or NullObserver()

    async def fetch(self) -> dict[str, Any]:
        """``config.files`` if given, else a scan of ``config.folder_path``."""
        if self._config.files is not None:
            return await self._fetch_exact_files(list(self._config.files))
        return await self._fetch_folder()

    # ------------------------------------------------------- files= path
    async def _fetch_exact_files(self, files: list[Path]) -> dict[str, Any]:
        """Process exactly ``config.files`` — one call, no folder scan.

        Unlike the folder-scan path there is no root to validate
        containment against (the caller already staged/selected these
        exact paths); a path that no longer exists is still reported as a
        row (``FAILED``) rather than silently skipped.
        """
        existing = [f for f in files if f.exists() and f.is_file()]
        missing = [f for f in files if f not in existing]

        options = self._config.extraction
        results: dict[Path, ExtractedDocument | Exception] = {}
        if existing:
            results.update(
                await run_extraction(
                    existing, options,
                    max_concurrency=options.max_concurrency,
                    per_file_timeout=options.limits.per_file_timeout_seconds,
                    observer=self._observer,
                )
            )
        for f in missing:
            results[f] = FileNotFoundError(f"staged file not found: {f}")

        documents = [self._to_canonical_document(f, results[f]) for f in files]
        logger.info("Uploads (files=) parsed: %d documents", len(documents))
        return {
            "folder_path": None,
            "file_count": len(documents),
            "documents": documents,
            "warnings": [],
        }

    # ------------------------------------------------------ folder-scan path
    async def _fetch_folder(self) -> dict[str, Any]:
        if self._config.folder_path is None:
            raise ValueError("UploadsIngestionConfig.folder_path is required")
        folder = Path(self._config.folder_path).resolve()
        if not folder.exists() or not folder.is_dir():
            logger.warning("Uploads folder missing or not a directory: %s", folder)
            return {
                "folder_path": str(folder), "file_count": 0, "documents": [],
                "warnings": self._build_warnings(folder, 0),
            }

        entries = await asyncio.to_thread(FolderScanner(recursive=False).scan, folder)
        logger.info("Uploads scan: %d entries in %s", len(entries), folder)

        if not entries:
            return {
                "folder_path": str(folder), "file_count": 0, "documents": [],
                "warnings": self._build_warnings(folder, 0),
            }

        rejected = [e for e in entries if not e.ok]
        files = [e.path for e in entries if e.ok]

        options = self._config.extraction
        allowed = options.allowed_extensions
        supported_files = [f for f in files if f.suffix.lower() in allowed]
        unsupported_files = [f for f in files if f.suffix.lower() not in allowed]

        results: dict[Path, ExtractedDocument | Exception] = {}
        if supported_files:
            results.update(
                await run_extraction(
                    supported_files, options,
                    max_concurrency=options.max_concurrency,
                    per_file_timeout=options.limits.per_file_timeout_seconds,
                    observer=self._observer,
                )
            )
        for f in unsupported_files:
            results[f] = UnsupportedFormat(f.suffix)

        documents = [self._to_canonical_document(f, results[f]) for f in files]
        documents.extend(self._rejected_document(e) for e in rejected)

        logger.info("Uploads parsed: %d documents from %s", len(documents), folder)
        return {
            "folder_path": str(folder),
            "file_count": len(documents),
            "documents": documents,
            "warnings": self._build_warnings(folder, len(documents)),
        }

    def _to_canonical_document(
        self, path: Path, outcome: ExtractedDocument | Exception,
    ) -> CanonicalDocument:
        return build_file_canonical_document(path, outcome)

    @staticmethod
    def _rejected_document(entry: ScanEntry) -> CanonicalDocument:
        """A row for a path the scanner refused to touch (symlink / subdirectory).

        Never routed through the extractor registry at all, so it's built
        directly rather than as a fake engine outcome.
        """
        path = entry.path
        record = ExtractionRecord(engine="none", engine_version="0")
        record.mark_failed(entry.status, f"{entry.status}: {path.name}")
        return CanonicalDocument(
            source_type="file",
            source_key=build_source_key("file", file_name=path.name),
            origin_id="",
            body_markdown="",
            raw_format=path.suffix.lstrip(".").lower() or "bin",
            title=path.name,
            structure={},
            extraction=record.to_dict(),
            extraction_status="FAILED",
            original_upload_path=str(path),
            file_name=path.name,
            file_size=None,
        )

    @staticmethod
    def _build_warnings(folder: Path, doc_count: int) -> list[str]:
        warnings: list[str] = []
        if not folder.exists():
            warnings.append(f"Uploads folder does not exist: {folder}")
        elif doc_count == 0:
            warnings.append(f"No supported files found in {folder}")
        return warnings

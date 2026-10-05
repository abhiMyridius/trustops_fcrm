"""``ExtractorRegistry`` — dispatches each file to a typed engine and drives it.

Scope & functionality
---------------------
Builds **one instance of every real engine** (``PdfplumberExtractor``/
``PypdfExtractor``, ``DocxExtractor``, ``XlsxExtractor``, ``CsvExtractor``,
``PptxExtractor``, ``TextExtractor``) over a single ``ExtractionOptions``,
keyed by each engine's ``.supports`` extensions — no more zero-arg
construction, no more ``getattr(options, ...)`` probing anywhere in this
module. (TrustOps prune: the upstream MCP document-loader engine and its
batched phases were removed on clone — ``UPSTREAM.md``; ``engine_mode``
``"auto"`` and ``"inprocess"`` now behave identically.)

Per-file dispatch (``run_extraction`` -> ``_dispatch_one``):

1. ``FileGate.inspect(path)`` — stat/hash/sniff. A refusal here
   (``SymlinkRejected``, ``FileTooLarge``, ``EmptyFile``, ``CorruptFile``,
   ``ExtensionMismatch``, ``BinaryContent``) *is* the file's outcome; no
   engine is ever invoked.
2. The extension not being on ``options.allowed_extensions`` (or not having
   any registered in-process engine) is ``UnsupportedFormat`` —
   ``unsupported_extension`` — again without attempting extraction.
3. Otherwise the matching engine's ``extract(path, facts, deadline)`` runs
   inside ``asyncio.wait_for(..., timeout=per_file_timeout)``; a timeout
   becomes ``ExtractionTimeout`` (warning ``extraction_timeout``), any
   ``ExtractionError`` the engine raised is returned as-is (it already
   carries the right ``FailureCode``).
4. On success, the ``Document`` goes through the **Finalizer**
   (``GfmRenderer.render`` -> ``StructureBuilder.from_model`` ->
   ``ExtractionRecord.to_dict()``) and comes back as an ``ExtractedDocument``
   — the shape ``sources/uploads_source.py::build_file_canonical_document``
   already knows how to turn into a ``CanonicalDocument``.

**PDF engine selection** (``options.pdf_engine``, default ``"pdfplumber"``):
the primary engine is tried first; on any ``ExtractionError`` that is *not*
one of ``EncryptedFile``/``CorruptFile``/``LimitExceeded`` (i.e. a genuine
engine-level failure, not a real property of the file the other engine
would only rediscover) the fallback engine (``PypdfExtractor``) is tried
and the record's ``fallback_used`` is set. ``options.pdf_engine == "pypdf"``
uses ``PypdfExtractor`` only — never falls back.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from evoq_ingestion.core.codes import code_of
from evoq_ingestion.extractors.base import (
    BaseExtractor,
    CorruptFile,
    EncryptedFile,
    ExtractedDocument,
    ExtractionError,
    ExtractionFailed,
    ExtractionTimeout,
    LimitExceeded,
    UnsupportedFormat,
    failure_code_of,
)
from evoq_ingestion.extractors.csv_extractor import CsvExtractor
from evoq_ingestion.extractors.docx_extractor import DocxExtractor
from evoq_ingestion.extractors.gate import FileFacts, FileGate
from evoq_ingestion.extractors.pdf_extractor import (
    PdfplumberExtractor,
    PypdfExtractor,
    ScannedPdfFailure,
)
from evoq_ingestion.extractors.pptx_extractor import PptxExtractor
from evoq_ingestion.extractors.record import ExtractionRecord
from evoq_ingestion.extractors.text_extractor import TextExtractor
from evoq_ingestion.extractors.xlsx_extractor import XlsxExtractor
from evoq_ingestion.model.renderer import GfmRenderer
from evoq_ingestion.model.structure import StructureBuilder
from evoq_ingestion.observability import FileEvent, IngestionObserver, NullObserver

if TYPE_CHECKING:
    from evoq_ingestion.core.config import ExtractionOptions
    from evoq_ingestion.model.document import Document

logger = logging.getLogger(__name__)

# A genuine property of the file (not an engine bug) — retrying with the
# other PDF engine would only rediscover the same fact, so these never
# trigger a fallback.
_PDF_NO_FALLBACK: tuple[type[ExtractionError], ...] = (
    EncryptedFile, CorruptFile, LimitExceeded, ScannedPdfFailure,
)

class ExtractorRegistry:
    """One instance of every in-process engine, keyed by extension.

    The PDF pair (primary + optional fallback) is dispatched specially.
    """

    def __init__(
        self,
        *,
        options: ExtractionOptions,
        by_ext: dict[str, BaseExtractor],
        pdf_primary: BaseExtractor,
        pdf_fallback: BaseExtractor | None,
    ) -> None:
        """Construct directly from already-built engines.

        Tests use this to substitute fakes; prefer ``from_options`` in
        production code.
        """
        self.options = options
        self.allowed_extensions = options.allowed_extensions
        self.pdf_primary = pdf_primary
        self.pdf_fallback = pdf_fallback
        self._by_ext = by_ext

    @classmethod
    def from_options(cls, options: ExtractionOptions) -> ExtractorRegistry:
        """Build one instance of every engine over *options* (§1.2/§1.3)."""
        non_pdf: list[BaseExtractor] = [
            TextExtractor(options),
            CsvExtractor(options),
            DocxExtractor(options),
            XlsxExtractor(options),
            PptxExtractor(options),
        ]
        by_ext: dict[str, BaseExtractor] = {}
        for extractor in non_pdf:
            for ext in extractor.supports:
                by_ext[ext] = extractor

        pdfplumber_engine = PdfplumberExtractor(options)
        pypdf_engine = PypdfExtractor(options)
        if options.pdf_engine == "pypdf":
            pdf_primary: BaseExtractor = pypdf_engine
            pdf_fallback: BaseExtractor | None = None
        else:
            pdf_primary = pdfplumber_engine
            pdf_fallback = pypdf_engine
        by_ext[".pdf"] = pdf_primary

        return cls(
            options=options, by_ext=by_ext,
            pdf_primary=pdf_primary, pdf_fallback=pdf_fallback,
        )

    def extractor_for(self, ext: str) -> BaseExtractor | None:
        """The in-process engine registered for *ext* (``.pdf`` -> the primary PDF engine)."""
        return self._by_ext.get(ext)

    def for_path(self, path: Path) -> list[BaseExtractor]:
        """The ordered engine chain that *would* be tried for *path*.

        A cheap, read-only summary (no gate, no I/O) used by allow-list
        diagnostics/tests; actual dispatch (``run_extraction``) makes its
        own gate-informed decision per file.
        """
        ext = path.suffix.lower()
        if ext not in self.allowed_extensions:
            return []
        chain: list[BaseExtractor] = []
        if ext == ".pdf":
            chain.append(self.pdf_primary)
            if self.pdf_fallback is not None:
                chain.append(self.pdf_fallback)
        else:
            extractor = self._by_ext.get(ext)
            if extractor is not None:
                chain.append(extractor)
        return chain


# ---------------------------------------------------------------- finalizer
def _finalize(
    doc: Document,
    *,
    extractor: BaseExtractor,
    facts: FileFacts,
    duration_ms: int,
    options: ExtractionOptions,
    fallback_from: str | None,
) -> ExtractedDocument:
    """Render + structure + record a successfully-parsed ``Document``.

    The one place a ``model.Document`` becomes the ``ExtractedDocument``
    shape the rest of the pipeline (``build_file_canonical_document``)
    already understands: ``GfmRenderer`` is the only writer of the body,
    ``StructureBuilder`` derives every count from the model (never by
    re-scanning the rendered text), and ``ExtractionRecord`` emits every
    contract key for every engine.
    """
    rendered = GfmRenderer().render(doc)
    structure = StructureBuilder(
        requirement_id_pattern=options.requirement_id_pattern,
        max_headings=options.structure_max_headings,
        max_requirement_ids=options.structure_max_requirement_ids,
    ).from_model(doc, rendered)

    record = ExtractionRecord(
        engine=extractor.name,
        engine_version=extractor.version(),
        raw_hash=facts.raw_hash,
        duration_ms=duration_ms,
        fallback_used=fallback_from is not None,
    )
    record.extend_warnings(doc.warnings)
    record.extend_warnings(rendered.warnings)
    if fallback_from is not None:
        record.mark_fallback(fallback_from)
    record.unit_failures = list(doc.units.failed)
    record.scanned_page_fraction = doc.metadata.get("scanned_page_fraction")
    record.is_scanned_pdf = bool(doc.metadata.get("is_scanned_pdf", False))
    front_matter = doc.metadata.get("front_matter")
    if front_matter:
        record.front_matter = dict(front_matter)

    return ExtractedDocument(
        body_markdown=rendered.markdown,
        raw_format=facts.ext.lstrip(".").lower() or "bin",
        structure=structure.to_dict(),
        extraction=record.to_dict(),
        attachments=[a.to_dict() for a in doc.attachments],
        title=doc.title,
    )


# ----------------------------------------------------------------- dispatch
async def _extract_pdf(
    path: Path,
    facts: FileFacts,
    registry: ExtractorRegistry,
    timeout: float | None,
) -> tuple[Document, BaseExtractor, str | None]:
    """Try the primary PDF engine; fall back only on a genuine engine-level failure.

    Returns ``(document, engine_used, fallback_from_engine_name)``.
    """
    primary = registry.pdf_primary
    deadline = registry.options.limits.deadline(timeout)
    try:
        doc = await primary.extract(path, facts, deadline)
        return doc, primary, None
    except ExtractionError as exc:
        fallback = registry.pdf_fallback
        if fallback is None or isinstance(exc, _PDF_NO_FALLBACK):
            raise
        logger.warning(
            "PDF engine %s failed for %s, falling back to %s: %s",
            primary.name, path.name, fallback.name, exc,
        )
        fallback_deadline = registry.options.limits.deadline(timeout)
        doc = await fallback.extract(path, facts, fallback_deadline)
        return doc, fallback, primary.name


async def _dispatch_one(
    path: Path,
    registry: ExtractorRegistry,
    gate: FileGate,
    timeout: float | None,
    observer: IngestionObserver,
) -> ExtractedDocument | Exception:
    """Gate -> engine(s) -> Finalizer for exactly one file."""
    try:
        facts = await asyncio.to_thread(gate.inspect, path)
    except ExtractionError as exc:
        return exc
    except Exception as exc:  # noqa: BLE001 - the gate itself must never crash the batch
        return ExtractionFailed(f"gate failed for {path.name}: {exc}")

    ext = facts.ext
    if ext not in registry.allowed_extensions:
        detail = ext or path.suffix
        return UnsupportedFormat(f"{detail} is not on the allow-list", detail=detail)

    engine_hint = registry.pdf_primary if ext == ".pdf" else registry.extractor_for(ext)
    if engine_hint is None:
        return UnsupportedFormat(f"no in-process engine for {ext}", detail=ext)
    engine_name_hint = engine_hint.name

    t0 = time.monotonic()
    observer.on_file_start(str(path), engine_name_hint)
    outcome: ExtractedDocument | Exception
    with observer.span("ingest.file", {"path": str(path), "engine": engine_name_hint}):
        try:
            if ext == ".pdf":
                doc, engine_used, fallback_from = await asyncio.wait_for(
                    _extract_pdf(path, facts, registry, timeout), timeout=timeout,
                )
            else:
                deadline = registry.options.limits.deadline(timeout)
                doc = await asyncio.wait_for(
                    engine_hint.extract(path, facts, deadline), timeout=timeout,
                )
                engine_used, fallback_from = engine_hint, None
        except TimeoutError:
            outcome = ExtractionTimeout(f"extraction_timeout after {timeout}s for {path.name}")
        except ExtractionError as exc:
            outcome = exc
        except Exception as exc:  # noqa: BLE001 - engine boundary: never crash the whole batch
            outcome = ExtractionFailed(f"{engine_name_hint} failed for {path.name}: {exc}")
        else:
            duration_ms = int((time.monotonic() - t0) * 1000)
            outcome = _finalize(
                doc, extractor=engine_used, facts=facts, duration_ms=duration_ms,
                options=registry.options, fallback_from=fallback_from,
            )

        duration_ms = int((time.monotonic() - t0) * 1000)
        _notify_file_end(observer, path, facts, outcome, duration_ms)
    return outcome


def _notify_file_end(
    observer: IngestionObserver,
    path: Path,
    facts: FileFacts,
    outcome: ExtractedDocument | Exception,
    duration_ms: int,
) -> None:
    if isinstance(outcome, ExtractedDocument):
        warnings = tuple(outcome.extraction.get("warnings", []))
        event = FileEvent(
            path=str(path),
            engine=str(outcome.extraction.get("engine", "unknown")),
            engine_version=str(outcome.extraction.get("engine_version", "0")),
            raw_format=outcome.raw_format,
            status="COMPLETE",
            duration_ms=duration_ms,
            bytes=facts.size,
            pages=outcome.structure.get("page_count"),
            warnings=warnings,
            fallback_used=bool(outcome.extraction.get("fallback_used", False)),
        )
    else:
        status = "UNSUPPORTED" if isinstance(outcome, UnsupportedFormat) else "FAILED"
        failure_code = failure_code_of(outcome) if isinstance(outcome, ExtractionError) else None
        warnings = (outcome.warning,) if isinstance(outcome, ExtractionError) else (str(outcome),)
        event = FileEvent(
            path=str(path),
            engine="unknown",
            engine_version="0",
            raw_format=(facts.ext.lstrip(".").lower() or "bin"),
            status=status,
            duration_ms=duration_ms,
            bytes=facts.size,
            warnings=warnings,
            failure_code=str(failure_code) if failure_code else None,
        )
    observer.on_file_end(event)
    for warning in event.warnings:
        code, _, detail = warning.partition(":")
        observer.on_warning(str(path), code_of(code), detail)


async def run_extraction(
    files: list[Path],
    options: ExtractionOptions,
    *,
    registry: ExtractorRegistry | None = None,
    max_concurrency: int | None = None,
    per_file_timeout: float | None = None,
    observer: IngestionObserver | None = None,
) -> dict[Path, ExtractedDocument | Exception]:
    """Extract every file in ``files``.

    In-process extraction is bounded by ``Semaphore(max_concurrency)`` and a
    per-file ``per_file_timeout`` (both default to ``options.max_concurrency``
    / ``options.limits.per_file_timeout_seconds`` when omitted).

    Returns a dict keyed by the input ``Path`` -> ``ExtractedDocument`` on
    success or the terminal ``Exception`` on failure (``UnsupportedFormat``,
    a typed ``ExtractionError``, or whatever the last engine raised).
    """
    registry = registry or ExtractorRegistry.from_options(options)
    observer = observer or NullObserver()
    max_concurrency = max_concurrency if max_concurrency is not None else options.max_concurrency
    default_timeout = options.limits.per_file_timeout_seconds
    per_file_timeout = per_file_timeout if per_file_timeout is not None else default_timeout
    gate = FileGate(options.limits)
    sem = asyncio.Semaphore(max(max_concurrency, 1))

    results: dict[Path, ExtractedDocument | Exception] = {}
    inprocess_files = _partition_files(files, registry, results)

    # In-process dispatch, bounded by concurrency + per-file timeout.
    pending = [f for f in inprocess_files if f not in results]

    async def _one(f: Path) -> None:
        async with sem:
            results[f] = await _dispatch_one(f, registry, gate, per_file_timeout, observer)

    await asyncio.gather(*[_one(f) for f in pending])
    return results


def _partition_files(
    files: list[Path],
    registry: ExtractorRegistry,
    results: dict[Path, ExtractedDocument | Exception],
) -> list[Path]:
    """Return the files to dispatch in-process.

    An extension not on the allow-list is recorded straight into *results*
    and excluded from the returned list.
    """
    inprocess_files: list[Path] = []
    for f in files:
        ext = f.suffix.lower()
        if ext not in registry.allowed_extensions:
            results[f] = UnsupportedFormat(f"{ext} is not on the allow-list", detail=ext)
            continue
        inprocess_files.append(f)
    return inprocess_files

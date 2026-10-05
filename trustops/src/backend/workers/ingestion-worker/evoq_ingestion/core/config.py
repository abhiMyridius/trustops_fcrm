"""Input configuration and result types for ``evoq_ingestion``.

Scope & functionality
---------------------
Every option the engine needs is a plain dataclass here. This module never
reads the environment (no ``os.getenv``) — a host process (the job worker,
a script, another service) builds these from its own configuration and
passes them in. In TrustOps the host is ``fcrm_ingestion_worker`` (H3); the
Jira/Confluence/MCP/summary configuration dataclasses were pruned on clone
(see ``src/backend/workers/ingestion-worker/UPSTREAM.md``).

``DEFAULT_REQUIREMENT_ID_PATTERN`` defined here is the **only** copy of the
hash-affecting requirement-id regex (the host's ``Settings`` imports it).
``ExtractionOptions`` is fully typed — extractors read fields, never
``getattr`` — and carries the platform ``Limits``; ``IngestionRequest``
carries the ``IngestionObserver`` the host binds for telemetry.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from evoq_ingestion.core.limits import Limits
from evoq_ingestion.observability import IngestionObserver, NullObserver

DEFAULT_REQUIREMENT_ID_PATTERN = r"\b([A-Z][A-Z0-9]{1,12}(?:-[A-Z0-9]{1,12})*-\d{1,5})\b"
DEFAULT_UPLOAD_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".docx", ".xlsx", ".csv", ".pptx", ".md", ".txt"}
)

EngineMode = Literal["auto", "inprocess"]
PdfEngine = Literal["pdfplumber", "pypdf"]
HiddenSheetPolicy = Literal["include", "skip"]
RowspanPolicyName = Literal["repeat", "blank", "marker"]


@dataclass
class ExtractionOptions:
    """Typed options shared by every in-process extractor and the registry.

    Row/column/byte/page caps live on ``limits`` (the one ``Limits`` object);
    the ``xlsx_max_rows_per_sheet`` / ``csv_max_rows`` /
    ``per_file_timeout_seconds`` properties are read-through aliases kept
    for the pre-model extractors and are removed with them.
    """

    engine_mode: EngineMode = "auto"
    allowed_extensions: frozenset[str] = DEFAULT_UPLOAD_ALLOWED_EXTENSIONS
    max_concurrency: int = 4
    limits: Limits = field(default_factory=Limits)
    pdf_engine: PdfEngine = "pdfplumber"
    pdf_min_chars_per_page: int = 40
    pdf_scanned_fail_fraction: float = 0.9
    docx_infer_headings: bool = True
    docx_include_deletions: bool = False
    xlsx_hidden_sheets: HiddenSheetPolicy = "include"
    pptx_include_notes: bool = True
    table_rowspan_policy: RowspanPolicyName = "repeat"
    requirement_id_pattern: str = DEFAULT_REQUIREMENT_ID_PATTERN
    structure_max_headings: int = 500
    structure_max_requirement_ids: int = 500

    def __post_init__(self) -> None:
        if not 0.0 < self.pdf_scanned_fail_fraction <= 1.0:
            raise ValueError("pdf_scanned_fail_fraction must be in (0, 1]")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        re.compile(self.requirement_id_pattern)

    # -- read-through aliases (pre-model extractors; removed with them) ----
    @property
    def per_file_timeout_seconds(self) -> float:
        """Alias of ``limits.per_file_timeout_seconds``."""
        return self.limits.per_file_timeout_seconds

    @per_file_timeout_seconds.setter
    def per_file_timeout_seconds(self, value: float) -> None:
        self.limits = dataclasses.replace(self.limits, per_file_timeout_seconds=value)

    @property
    def xlsx_max_rows_per_sheet(self) -> int:
        """Alias of ``limits.max_rows_per_sheet``."""
        return self.limits.max_rows_per_sheet

    @xlsx_max_rows_per_sheet.setter
    def xlsx_max_rows_per_sheet(self, value: int) -> None:
        self.limits = dataclasses.replace(self.limits, max_rows_per_sheet=value)

    @property
    def csv_max_rows(self) -> int:
        """Alias of ``limits.max_csv_rows``."""
        return self.limits.max_csv_rows

    @csv_max_rows.setter
    def csv_max_rows(self, value: int) -> None:
        self.limits = dataclasses.replace(self.limits, max_csv_rows=value)


@dataclass
class UploadsIngestionConfig:
    """Settings for uploaded-documents folder ingestion.

    ``files`` — when given, exactly these paths are extracted (the job passes
    its own upload set); otherwise ``folder_path`` is scanned.
    """

    enabled: bool = True
    folder_path: Path | None = None
    files: tuple[Path, ...] | None = None
    extraction: ExtractionOptions = field(default_factory=ExtractionOptions)
    timeout_seconds: float = 600.0


@dataclass
class IngestionRequest:
    """Single run: the uploads config + the host's observer."""

    uploads: UploadsIngestionConfig | None = None
    observer: IngestionObserver = field(default_factory=NullObserver)


@dataclass
class IngestionResult:
    """Normalized result envelope."""

    meta: dict[str, Any]
    uploads: dict[str, Any] | None
    errors: list[dict[str, Any]]

"""Internal engine core: typed config, codes, limits, encoding, scanning, profiling, hashing.

Scope & functionality
---------------------
Everything here is I/O-free and framework-free. ``config`` holds the plain
dataclasses a host fills in; ``codes``/``limits`` are the shared vocabulary
for graceful failure; ``encoding`` and ``markdown_scan`` are the single
readers of bytes and of canonical Markdown; ``profile`` produces
``profile_hints``; ``hashing`` pins the identity algorithms;
``documents`` is the ``CanonicalDocument`` every source emits.
"""

from __future__ import annotations

from evoq_ingestion.core.codes import FailureCode, WarningCode, code_of, with_detail
from evoq_ingestion.core.config import (
    DEFAULT_REQUIREMENT_ID_PATTERN,
    DEFAULT_UPLOAD_ALLOWED_EXTENSIONS,
    ExtractionOptions,
    IngestionRequest,
    IngestionResult,
    UploadsIngestionConfig,
)
from evoq_ingestion.core.documents import CanonicalDocument
from evoq_ingestion.core.encoding import DecodedText, EncodingDetector, EncodingResult
from evoq_ingestion.core.hashing import (
    build_source_key,
    canonicalize,
    compute_content_hash,
    compute_metadata_hash,
    compute_source_id,
)
from evoq_ingestion.core.limits import Deadline, DeadlineExceeded, Limits
from evoq_ingestion.core.markdown_scan import (
    MarkdownScan,
    MarkdownScanner,
    PageLines,
    scan_markdown,
    scan_page_lines,
)
from evoq_ingestion.core.profile import (
    PROFILE_VERSION,
    ProfileHints,
    ProfileScanner,
    compute_profile,
    extract_requirement_ids,
)
from evoq_ingestion.core.utils import utc_now_iso

__all__ = [
    "DEFAULT_REQUIREMENT_ID_PATTERN",
    "DEFAULT_UPLOAD_ALLOWED_EXTENSIONS",
    "PROFILE_VERSION",
    "CanonicalDocument",
    "Deadline",
    "DeadlineExceeded",
    "DecodedText",
    "EncodingDetector",
    "EncodingResult",
    "ExtractionOptions",
    "FailureCode",
    "IngestionRequest",
    "IngestionResult",
    "Limits",
    "MarkdownScan",
    "MarkdownScanner",
    "PageLines",
    "ProfileHints",
    "ProfileScanner",
    "UploadsIngestionConfig",
    "WarningCode",
    "build_source_key",
    "canonicalize",
    "code_of",
    "compute_content_hash",
    "compute_metadata_hash",
    "compute_profile",
    "compute_source_id",
    "extract_requirement_ids",
    "scan_markdown",
    "scan_page_lines",
    "utc_now_iso",
    "with_detail",
]

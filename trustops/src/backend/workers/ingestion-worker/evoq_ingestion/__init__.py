"""evoq_ingestion — standalone, reusable ingestion engine.

Scope & functionality
---------------------
Uploaded-file ingestion into one ``CanonicalDocument`` shape
(``canonical_document.md``, copied from ``AEO/contracts/`` into the test
fixtures). TrustOps prune: the Jira and Confluence sources, their HTML/ADF
renderers, the MCP document loader and the LiteLLM summariser were removed
on clone (``src/backend/workers/ingestion-worker/UPSTREAM.md``). Extractors build a typed
``Document`` model; one ``GfmRenderer`` writes the Markdown body; one
``StructureBuilder`` derives ``structure``; ``compute_profile`` derives
``profile_hints``. Failures are typed (``ExtractionError`` +
``FailureCode``), limits are explicit (``Limits``), and telemetry flows
through the ``IngestionObserver`` Protocol a host binds.

This module re-exports the **entire host surface** — a host imports from
``evoq_ingestion`` only, never from sub-modules (AST-enforced in the job's
tests). Zero imports of ``jobs/``, ``drivers/``, ``config/``, FastStream,
Mongo or OpenTelemetry — enforced by ``test_no_host_imports.py`` (now under
``tests/unit/workers/ingestion-worker/evoq_ingestion/``);
every configuration knob is a plain dataclass in ``core/config.py`` and the
package never reads the environment.
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
from evoq_ingestion.core.markdown_scan import PageLines, scan_page_lines
from evoq_ingestion.core.profile import ProfileHints, ProfileScanner, compute_profile
from evoq_ingestion.core.utils import utc_now_iso
from evoq_ingestion.core.writer import write_bytes_atomic, write_text_atomic
from evoq_ingestion.extractors.base import (
    BaseExtractor,
    BinaryContent,
    CorruptFile,
    EmptyFile,
    EncryptedFile,
    EngineInfo,
    ExtensionMismatch,
    ExtractionError,
    ExtractionFailed,
    ExtractionTimeout,
    FileTooLarge,
    LimitExceeded,
    SymlinkRejected,
    UnsupportedFormat,
    failure_code_of,
)
from evoq_ingestion.extractors.gate import FileFacts, FileGate, FileKind
from evoq_ingestion.extractors.record import ExtractionRecord
from evoq_ingestion.model import (
    AttachmentRef,
    Block,
    Cell,
    CodeBlock,
    Document,
    GfmRenderer,
    Heading,
    HorizontalRule,
    ImageRef,
    ListBlock,
    ListItem,
    Note,
    NoteKind,
    PageBreak,
    Paragraph,
    Quote,
    Raw,
    Rendered,
    Row,
    RowspanPolicy,
    SheetBreak,
    SheetInfo,
    SlideBreak,
    Structure,
    StructureBuilder,
    Table,
    UnitFailure,
    Units,
)
from evoq_ingestion.observability import (
    FileEvent,
    GuardrailEvent,
    IngestionObserver,
    LlmCallRecord,
    NullObserver,
)
from evoq_ingestion.run import build_request, extract_file, extract_folder, run_ingestion

__all__ = [
    "DEFAULT_REQUIREMENT_ID_PATTERN",
    "DEFAULT_UPLOAD_ALLOWED_EXTENSIONS",
    "AttachmentRef",
    "BaseExtractor",
    "BinaryContent",
    "Block",
    "CanonicalDocument",
    "Cell",
    "CodeBlock",
    "CorruptFile",
    "Deadline",
    "DeadlineExceeded",
    "DecodedText",
    "Document",
    "EmptyFile",
    "EncodingDetector",
    "EncodingResult",
    "EncryptedFile",
    "EngineInfo",
    "ExtensionMismatch",
    "ExtractionError",
    "ExtractionFailed",
    "ExtractionOptions",
    "ExtractionRecord",
    "ExtractionTimeout",
    "FailureCode",
    "FileEvent",
    "FileFacts",
    "FileGate",
    "FileKind",
    "FileTooLarge",
    "GfmRenderer",
    "GuardrailEvent",
    "Heading",
    "HorizontalRule",
    "ImageRef",
    "IngestionObserver",
    "IngestionRequest",
    "IngestionResult",
    "LimitExceeded",
    "Limits",
    "ListBlock",
    "ListItem",
    "LlmCallRecord",
    "Note",
    "NoteKind",
    "NullObserver",
    "PageBreak",
    "PageLines",
    "Paragraph",
    "ProfileHints",
    "ProfileScanner",
    "Quote",
    "Raw",
    "Rendered",
    "Row",
    "RowspanPolicy",
    "SheetBreak",
    "SheetInfo",
    "SlideBreak",
    "Structure",
    "StructureBuilder",
    "SymlinkRejected",
    "Table",
    "UnitFailure",
    "Units",
    "UnsupportedFormat",
    "UploadsIngestionConfig",
    "WarningCode",
    "build_request",
    "build_source_key",
    "canonicalize",
    "code_of",
    "compute_content_hash",
    "compute_metadata_hash",
    "compute_profile",
    "compute_source_id",
    "extract_file",
    "extract_folder",
    "failure_code_of",
    "run_ingestion",
    "scan_page_lines",
    "utc_now_iso",
    "with_detail",
    "write_bytes_atomic",
    "write_text_atomic",
]

__version__ = "2.0.0"

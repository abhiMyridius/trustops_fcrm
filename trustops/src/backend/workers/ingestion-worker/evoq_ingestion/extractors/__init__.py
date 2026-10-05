"""Upload extractor engines: gate -> typed engine -> ``Document`` -> record.

Scope & functionality
---------------------
``FileGate`` inspects a file (size, hash, magic bytes) before any engine
runs; engines subclass ``BaseExtractor`` over typed ``ExtractionOptions``
and return a ``model.Document``; ``ExtractionRecord`` is the typed
``extraction`` block written onto the canonical row. Failures are the
typed ``ExtractionError`` hierarchy carrying a ``FailureCode``. The
registry/chain that drives engines lives in ``registry.py`` (imported
explicitly by callers — it pulls in every engine and is not re-exported
here so importing the base types stays cheap).
"""

from __future__ import annotations

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
from evoq_ingestion.extractors.record import (
    ExtractionRecord,
    raw_hash_bytes,
    raw_hash_json,
    raw_hash_text,
)

__all__ = [
    "BaseExtractor",
    "BinaryContent",
    "CorruptFile",
    "EmptyFile",
    "EncryptedFile",
    "EngineInfo",
    "ExtensionMismatch",
    "ExtractionError",
    "ExtractionFailed",
    "ExtractionRecord",
    "ExtractionTimeout",
    "FileFacts",
    "FileGate",
    "FileKind",
    "FileTooLarge",
    "LimitExceeded",
    "SymlinkRejected",
    "UnsupportedFormat",
    "failure_code_of",
    "raw_hash_bytes",
    "raw_hash_json",
    "raw_hash_text",
]

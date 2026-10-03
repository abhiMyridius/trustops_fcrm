"""Extractor base class, engine identity and the typed failure hierarchy.

Scope & functionality
---------------------
``BaseExtractor(options: ExtractionOptions)`` is the one shape every upload
engine (pdfplumber/pypdf, python-docx, openpyxl, csv, python-pptx, text)
takes: ``async extract(path, facts, deadline) -> Document`` runs the
synchronous ``_parse`` in a worker thread, translates a spent ``Deadline``
into ``ExtractionTimeout`` and any unclassified library exception into
``ExtractionFailed`` (engines override ``classify_error`` to map their
library's errors onto ``EncryptedFile`` / ``CorruptFile`` / …). Options are
typed — no ``getattr(options, …)`` probing anywhere.

``ExtractionError(code, message)`` and its subclasses carry the
``FailureCode`` the registry writes onto the ``FAILED`` row, so a failure is
diagnosable (``encrypted_pdf`` vs ``corrupt_file`` vs ``extension_mismatch``)
instead of a Python repr in ``warnings``.

The pre-model ``Extractor`` protocol and ``ExtractedDocument`` stay here
only until the extractor lanes (I2–I7) and the registry (I8) move to
``BaseExtractor``/``Document``; new code must not use them.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from evoq_ingestion.core.codes import FailureCode, with_detail
from evoq_ingestion.core.limits import Deadline, DeadlineExceeded
from evoq_ingestion.model.document import Document

if TYPE_CHECKING:
    from evoq_ingestion.core.config import ExtractionOptions
    from evoq_ingestion.extractors.gate import FileFacts


# ------------------------------------------------------------------ errors
class ExtractionError(Exception):
    """Base of every typed extraction failure; ``code`` is the row's ``failure_code``."""

    code: ClassVar[FailureCode] = FailureCode.extraction_failed

    def __init__(self, message: str = "", *, detail: str | None = None) -> None:
        """Build the error; ``detail`` becomes the ``code:detail`` warning suffix."""
        super().__init__(message or str(self.code))
        self.message = message or str(self.code)
        self.detail = detail

    @property
    def warning(self) -> str:
        """``code`` or ``code:detail`` — what goes into ``extraction.warnings``."""
        return with_detail(self.code, self.detail)

    def __str__(self) -> str:
        return self.message


class UnsupportedFormat(ExtractionError):
    """The file's extension is not one this engine (or the allow-list) handles."""

    code = FailureCode.unsupported_extension


class ExtractionFailed(ExtractionError):
    """An engine attempted extraction and failed for an unclassified reason."""

    code = FailureCode.extraction_failed


class FileTooLarge(ExtractionError):
    """``size > Limits.max_file_bytes`` — refused before any read."""

    code = FailureCode.file_too_large


class EncryptedFile(ExtractionError):
    """Password-protected input (no password available)."""

    code = FailureCode.encrypted_pdf


class CorruptFile(ExtractionError):
    """The parser could not read the container/syntax at all."""

    code = FailureCode.corrupt_file


class LimitExceeded(ExtractionError):
    """A whole-file cap (pages / slides) was exceeded; ``code`` is passed per instance."""

    code = FailureCode.page_limit_exceeded

    def __init__(
        self,
        message: str = "",
        *,
        code: FailureCode = FailureCode.page_limit_exceeded,
        detail: str | None = None,
    ) -> None:
        """Build the error with an instance-level ``code`` (page vs slide cap, …)."""
        super().__init__(message, detail=detail)
        self._code = code

    @property
    def warning(self) -> str:
        """Instance-level code (page vs slide) with optional detail."""
        return with_detail(self._code, self.detail)

    @property
    def failure_code(self) -> FailureCode:
        """The instance-level cap that was hit."""
        return self._code


class ExtractionTimeout(ExtractionError):
    """The per-file ``Deadline`` was spent."""

    code = FailureCode.extraction_timeout


class BinaryContent(ExtractionError):
    """A text extension whose bytes are binary."""

    code = FailureCode.binary_content


class ExtensionMismatch(ExtractionError):
    """Magic bytes disagree with the extension (PDF named ``.docx``, …)."""

    code = FailureCode.extension_mismatch


class EmptyFile(ExtractionError):
    """Zero bytes."""

    code = FailureCode.empty_file


class SymlinkRejected(ExtractionError):
    """Symbolic links are never followed."""

    code = FailureCode.symlink_rejected


def failure_code_of(error: ExtractionError) -> FailureCode:
    """The ``FailureCode`` to write for *error* (honours ``LimitExceeded``'s instance code)."""
    if isinstance(error, LimitExceeded):
        return error.failure_code
    return error.code


# ------------------------------------------------------------------ engine
@dataclass(frozen=True, slots=True)
class EngineInfo:
    """Engine identity — the contract's ``extraction.engine`` / ``engine_version``."""

    name: str
    version: str


class BaseExtractor(ABC):
    """Plain class over typed options; subclasses implement synchronous ``_parse``."""

    info: ClassVar[EngineInfo]
    supports: ClassVar[frozenset[str]]

    def __init__(self, options: ExtractionOptions) -> None:
        """Construct the engine over its typed options (no I/O, no environment)."""
        self.options = options

    @property
    def name(self) -> str:
        """Engine name (``info.name``)."""
        return self.info.name

    def version(self) -> str:
        """Engine version (``info.version``) — hash-affecting, frozen per release."""
        return self.info.version

    def accepts(self, path: Path) -> bool:
        """True when ``path``'s extension is in ``supports``."""
        return path.suffix.lower() in self.supports

    async def extract(self, path: Path, facts: FileFacts, deadline: Deadline) -> Document:
        """Parse *path* in a worker thread into a ``Document``.

        Raises ``UnsupportedFormat`` when the extension is not supported,
        ``ExtractionTimeout`` when *deadline* is spent, any typed
        ``ExtractionError`` the engine classified, or ``ExtractionFailed``
        for anything else.
        """
        ext = facts.ext or path.suffix.lower()
        if ext not in self.supports:
            raise UnsupportedFormat(f"{self.name} does not handle {ext!r}", detail=ext)
        deadline.check()
        try:
            return await asyncio.to_thread(self._parse, path, facts, deadline)
        except ExtractionError:
            raise
        except DeadlineExceeded as exc:
            raise ExtractionTimeout(
                f"{self.name} exceeded {deadline.seconds}s on {path.name}"
            ) from exc
        except Exception as exc:
            raise self.classify_error(exc, path) from exc

    def classify_error(self, exc: Exception, path: Path) -> ExtractionError:
        """Map a library exception to a typed error; the default is ``ExtractionFailed``."""
        return ExtractionFailed(f"{self.name} failed for {path.name}: {exc}")

    @abstractmethod
    def _parse(self, path: Path, facts: FileFacts, deadline: Deadline) -> Document:
        """Synchronous parse; call ``deadline.check()`` between units."""


# ----------------------------------------------------- legacy (pre-model)
@dataclass
class ExtractedDocument:
    """Legacy normalised output — superseded by ``model.Document`` + ``ExtractionRecord``."""

    body_markdown: str
    raw_format: str
    structure: dict[str, Any] = field(default_factory=dict)
    extraction: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    title: str | None = None


@runtime_checkable
class Extractor(Protocol):
    """Legacy protocol (``extract(path, options)``); replaced by ``BaseExtractor``."""

    name: str

    def version(self) -> str:
        """Engine implementation version."""
        ...

    @property
    def supports(self) -> frozenset[str]:
        """Lower-case, dot-prefixed extensions this engine can handle."""
        ...

    async def extract(self, path: Path, options: Any) -> ExtractedDocument:  # noqa: ANN401
        """Extract ``path`` into an ``ExtractedDocument``.

        ``options`` is intentionally untyped here — this is the legacy,
        pre-model protocol every pdf/docx/xlsx/csv/pptx/text engine still
        implements via ``getattr(options, ...)`` probing; ``BaseExtractor``
        above is its typed replacement.
        """
        ...

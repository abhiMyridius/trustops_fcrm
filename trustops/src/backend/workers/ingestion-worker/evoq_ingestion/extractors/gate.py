r"""``FileGate`` — stat, hash and sniff a file before any engine touches it.

Scope & functionality
---------------------
``FileGate.inspect(path) -> FileFacts`` is the first step of every upload
extraction. In order: ``lstat`` (symlinks are rejected, never followed);
size against ``Limits.max_file_bytes`` (**before any read** — an oversize
file is refused without opening it); zero bytes (``empty_file`` for text
extensions, ``corrupt_file`` for binary formats); then exactly one
streaming pass that computes the SHA-256 and sniffs the head — ``%PDF-``,
``PK\\x03\\x04`` (an OOXML container is identified from its part names:
``word/`` / ``xl/`` / ``ppt/``), or text vs binary via ``EncodingDetector``.

The sniffed ``FileKind`` is compared with the extension: a PDF named
``.docx`` is ``extension_mismatch``; an executable named ``.txt`` is
``binary_content``. Unknown extensions are described, not judged — the
registry decides ``unsupported_extension``.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from evoq_ingestion.core.encoding import EncodingDetector
from evoq_ingestion.core.limits import Deadline, Limits
from evoq_ingestion.extractors.base import (
    BinaryContent,
    CorruptFile,
    EmptyFile,
    ExtensionMismatch,
    FileTooLarge,
    SymlinkRejected,
)

__all__ = ["Deadline", "FileFacts", "FileGate", "FileKind"]

TEXT_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md", ".csv"})
OOXML_EXTENSIONS: dict[str, str] = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx"}

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_OOXML_PARTS: tuple[tuple[str, str], ...] = (
    ("word/document.xml", "docx"),
    ("xl/workbook.xml", "xlsx"),
    ("ppt/presentation.xml", "pptx"),
)


class FileKind(StrEnum):
    """What the bytes say the file is."""

    pdf = "pdf"
    docx = "docx"
    xlsx = "xlsx"
    pptx = "pptx"
    zip = "zip"
    text = "text"
    binary = "binary"
    empty = "empty"


@dataclass(frozen=True, slots=True)
class FileFacts:
    """Everything the gate learned; passed to the engine and the record."""

    path: Path
    size: int
    sha256: str
    ext: str
    kind: FileKind
    is_empty: bool = False
    encoding: str | None = None

    @property
    def raw_hash(self) -> str:
        """``"sha256:<hex>"`` for ``ExtractionRecord.raw_hash``."""
        return f"sha256:{self.sha256}"


class FileGate:
    """Pre-flight inspection: never reads an oversize file, never follows symlinks."""

    def __init__(
        self,
        limits: Limits | None = None,
        *,
        sniff_bytes: int = 8192,
        chunk_size: int = 1024 * 1024,
        detector: EncodingDetector | None = None,
    ) -> None:
        """Construct a gate; defaults to platform ``Limits()`` and a fresh detector."""
        self._limits = limits or Limits()
        self._sniff_bytes = sniff_bytes
        self._chunk_size = chunk_size
        self._detector = detector or EncodingDetector()

    @property
    def limits(self) -> Limits:
        """The caps this gate enforces."""
        return self._limits

    def inspect(self, path: Path) -> FileFacts:
        """Return ``FileFacts`` or raise the typed ``ExtractionError`` for a refusal."""
        ext = path.suffix.lower()
        stat = os.lstat(path)
        if os.path.islink(path):
            raise SymlinkRejected(f"symlink rejected: {path.name}", detail=path.name)
        size = stat.st_size
        if size > self._limits.max_file_bytes:
            raise FileTooLarge(
                f"{path.name} is {size} bytes (limit {self._limits.max_file_bytes})",
                detail=str(size),
            )
        if size == 0:
            if ext in TEXT_EXTENSIONS:
                raise EmptyFile(f"{path.name} is empty")
            raise CorruptFile(f"{path.name} is empty", detail="empty")

        digest, head = self._hash_and_head(path)
        kind, encoding = self._sniff(path, ext, head)
        self._check_mismatch(path, ext, kind)
        return FileFacts(
            path=path,
            size=size,
            sha256=digest,
            ext=ext,
            kind=kind,
            is_empty=False,
            encoding=encoding,
        )

    # ------------------------------------------------------------- internals
    def _hash_and_head(self, path: Path) -> tuple[str, bytes]:
        hasher = hashlib.sha256()
        head = b""
        with open(path, "rb") as handle:  # noqa: PTH123 - streaming read
            while True:
                chunk = handle.read(self._chunk_size)
                if not chunk:
                    break
                if len(head) < self._sniff_bytes:
                    head += chunk[: self._sniff_bytes - len(head)]
                hasher.update(chunk)
        return hasher.hexdigest(), head

    def _sniff(self, path: Path, ext: str, head: bytes) -> tuple[FileKind, str | None]:
        if head.lstrip()[:5] == _PDF_MAGIC or _PDF_MAGIC in head[:1024]:
            return FileKind.pdf, None
        if head.startswith(_ZIP_MAGIC):
            return self._sniff_zip(path, ext), None
        result = self._detector.detect(head)
        if result.is_binary:
            return FileKind.binary, None
        return FileKind.text, result.encoding

    @staticmethod
    def _sniff_zip(path: Path, ext: str) -> FileKind:
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
        except (zipfile.BadZipFile, OSError) as exc:
            if ext in OOXML_EXTENSIONS:
                raise CorruptFile(
                    f"{path.name}: unreadable OOXML container", detail=str(exc)
                ) from exc
            return FileKind.zip
        for part, kind in _OOXML_PARTS:
            if part in names:
                return FileKind(kind)
        return FileKind.zip

    @staticmethod
    def _check_mismatch(path: Path, ext: str, kind: FileKind) -> None:
        if ext == ".pdf" and kind is not FileKind.pdf:
            raise ExtensionMismatch(f"{path.name} is not a PDF ({kind})", detail=f"pdf!={kind}")
        expected = OOXML_EXTENSIONS.get(ext)
        if expected is not None and kind.value != expected:
            raise ExtensionMismatch(
                f"{path.name} is not a {expected} ({kind})", detail=f"{expected}!={kind}"
            )
        if ext in TEXT_EXTENSIONS:
            if kind is FileKind.binary:
                raise BinaryContent(f"{path.name} contains binary data")
            if kind is not FileKind.text:
                raise ExtensionMismatch(
                    f"{path.name} is a {kind} container, not text", detail=f"text!={kind}"
                )

"""Tests for ``extractors/gate.py`` — ``FileGate`` never reads past the head."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from evoq_ingestion.core.codes import FailureCode
from evoq_ingestion.core.limits import Limits
from evoq_ingestion.extractors.base import (
    BinaryContent,
    CorruptFile,
    EmptyFile,
    ExtensionMismatch,
    FileTooLarge,
    SymlinkRejected,
)
from evoq_ingestion.extractors.gate import FileGate, FileKind


def _write_ooxml(path: Path, part_name: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(part_name, b"<xml/>")
        zf.writestr("[Content_Types].xml", b"<Types/>")


class TestZeroByteFiles:
    def test_zero_byte_text_extension_is_empty_file(self, tmp_path: Path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"")
        with pytest.raises(EmptyFile):
            FileGate().inspect(f)

    def test_zero_byte_pdf_is_corrupt_file(self, tmp_path: Path):
        f = tmp_path / "a.pdf"
        f.write_bytes(b"")
        with pytest.raises(CorruptFile):
            FileGate().inspect(f)


class TestOversize:
    def test_oversize_via_sparse_file_never_reads_full_content(self, tmp_path: Path):
        f = tmp_path / "huge.txt"
        # A sparse file: seek far out and write one byte — no real disk usage,
        # but stat() reports a huge size. The gate must refuse on stat alone.
        with open(f, "wb") as handle:  # noqa: PTH123 - direct fd control for sparse write
            handle.seek(50 * 1024 * 1024 - 1)
            handle.write(b"\x00")
        gate = FileGate(Limits(max_file_bytes=1024))
        with pytest.raises(FileTooLarge) as exc_info:
            gate.inspect(f)
        assert exc_info.value.code is FailureCode.file_too_large
        assert exc_info.value.warning.startswith("file_too_large")

    def test_file_at_the_limit_is_accepted(self, tmp_path: Path):
        f = tmp_path / "ok.txt"
        f.write_bytes(b"a" * 1024)
        gate = FileGate(Limits(max_file_bytes=1024))
        facts = gate.inspect(f)
        assert facts.size == 1024


class TestExtensionMismatch:
    def test_pdf_named_docx_is_extension_mismatch(self, tmp_path: Path):
        f = tmp_path / "fake.docx"
        f.write_bytes(b"%PDF-1.4\n%mock pdf content\n")
        with pytest.raises(ExtensionMismatch):
            FileGate().inspect(f)

    def test_docx_container_named_pdf_is_extension_mismatch(self, tmp_path: Path):
        f = tmp_path / "fake.pdf"
        _write_ooxml(f, "word/document.xml")
        with pytest.raises(ExtensionMismatch):
            FileGate().inspect(f)

    def test_real_pdf_named_pdf_is_accepted(self, tmp_path: Path):
        f = tmp_path / "real.pdf"
        f.write_bytes(b"%PDF-1.7\n1 0 obj\n<< >>\nendobj\n%%EOF")
        facts = FileGate().inspect(f)
        assert facts.kind is FileKind.pdf

    def test_real_docx_container_is_accepted(self, tmp_path: Path):
        f = tmp_path / "real.docx"
        _write_ooxml(f, "word/document.xml")
        facts = FileGate().inspect(f)
        assert facts.kind is FileKind.docx

    def test_xlsx_named_docx_is_mismatch(self, tmp_path: Path):
        f = tmp_path / "fake.docx"
        _write_ooxml(f, "xl/workbook.xml")
        with pytest.raises(ExtensionMismatch):
            FileGate().inspect(f)


class TestBinaryAsTxt:
    def test_binary_content_named_txt_is_rejected(self, tmp_path: Path):
        f = tmp_path / "binary.txt"
        f.write_bytes(bytes([i % 256 for i in range(2000)]))
        with pytest.raises(BinaryContent):
            FileGate().inspect(f)

    def test_plain_text_named_txt_is_accepted(self, tmp_path: Path):
        f = tmp_path / "plain.txt"
        f.write_text("Hello, this is plain prose.\nSecond line.\n")
        facts = FileGate().inspect(f)
        assert facts.kind is FileKind.text


class TestSymlinks:
    def test_symlink_is_rejected(self, tmp_path: Path):
        target = tmp_path / "real.txt"
        target.write_text("hello")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        with pytest.raises(SymlinkRejected) as exc_info:
            FileGate().inspect(link)
        assert exc_info.value.code is FailureCode.symlink_rejected


class TestHashAndFacts:
    def test_sha256_and_raw_hash_prefix(self, tmp_path: Path):
        f = tmp_path / "a.txt"
        f.write_text("hello world")
        facts = FileGate().inspect(f)
        import hashlib

        assert facts.sha256 == hashlib.sha256(b"hello world").hexdigest()
        assert facts.raw_hash == f"sha256:{facts.sha256}"

    def test_never_reads_past_head_for_hashing_uses_streaming(self, tmp_path: Path):
        # A file larger than the sniff window still hashes correctly end to end.
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * (200 * 1024))
        gate = FileGate(sniff_bytes=64)
        facts = gate.inspect(f)
        import hashlib

        assert facts.sha256 == hashlib.sha256(b"x" * (200 * 1024)).hexdigest()


class TestCorruptZip:
    def test_corrupt_ooxml_container_is_corrupt_file(self, tmp_path: Path):
        f = tmp_path / "broken.docx"
        f.write_bytes(b"PK\x03\x04" + b"not a real zip" * 10)
        with pytest.raises(CorruptFile):
            FileGate().inspect(f)

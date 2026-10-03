"""Tests for core/writer.py — atomic text/bytes body writes."""

from __future__ import annotations

from pathlib import Path

from evoq_ingestion.core.writer import write_bytes_atomic, write_text_atomic


class TestWriteTextAtomic:
    def test_writes_content(self, tmp_path: Path) -> None:
        out = tmp_path / "body.md"
        result_path = write_text_atomic(out, "# Title\n\nBody")
        assert Path(result_path).read_text() == "# Title\n\nBody"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        out = tmp_path / "a" / "b" / "v1.md"
        write_text_atomic(out, "content")
        assert out.exists()

    def test_overwrite(self, tmp_path: Path) -> None:
        out = tmp_path / "v1.md"
        write_text_atomic(out, "v1")
        write_text_atomic(out, "v2")
        assert out.read_text() == "v2"

    def test_no_leftover_temp_files(self, tmp_path: Path) -> None:
        out = tmp_path / "v1.md"
        write_text_atomic(out, "content")
        siblings = list(tmp_path.iterdir())
        assert siblings == [out]


class TestWriteBytesAtomic:
    def test_writes_bytes(self, tmp_path: Path) -> None:
        out = tmp_path / "raw.bin"
        write_bytes_atomic(out, b"\x00\x01\x02")
        assert out.read_bytes() == b"\x00\x01\x02"

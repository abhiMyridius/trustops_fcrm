"""Tests for sources/uploads_source.py — folder scan, ``config.files``-exact
extraction, and UNSUPPORTED/FAILED rows for anything off the allow-list or
rejected by the scanner.

Adapted from AEO/ingestion/tests/unit/logics/ingestion/test_uploads_source.py:
the pre-revamp "unrecognized extensions are silently skipped, zero warning"
behavior is **inverted** by design (§2.3/§2.7) — an unsupported file now
produces a row with ``extraction_status=UNSUPPORTED`` instead of vanishing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from evoq_ingestion.core.config import ExtractionOptions, UploadsIngestionConfig
from evoq_ingestion.sources.scanner import FolderScanner
from evoq_ingestion.sources.uploads_source import UploadsIngestionSource


class TestFolderScannerViaUploadsSource:
    """The scanner itself is covered by ``test_scanner.py``; these confirm
    ``UploadsIngestionSource`` actually uses it (not a private duplicate)."""

    def test_lists_every_regular_file(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.pdf").write_bytes(b"%PDF")
        (tmp_path / "c.xyz").write_text("skip-by-allowlist-not-by-scan")
        names = {e.path.name for e in FolderScanner().scan(tmp_path)}
        assert {"a.txt", "b.pdf", "c.xyz"} <= names

    def test_nonexistent_folder(self, tmp_path: Path) -> None:
        assert FolderScanner().scan(tmp_path / "nope") == []

    def test_empty_folder(self, tmp_path: Path) -> None:
        assert FolderScanner().scan(tmp_path) == []

    def test_sorted_output(self, tmp_path: Path) -> None:
        (tmp_path / "z.txt").write_text("z")
        (tmp_path / "a.txt").write_text("a")
        entries = FolderScanner().scan(tmp_path)
        assert [e.path.name for e in entries] == ["a.txt", "z.txt"]


@pytest.mark.asyncio
class TestUploadsIngestionSourceFolderScan:
    async def test_folder_path_none_raises(self) -> None:
        source = UploadsIngestionSource(UploadsIngestionConfig(folder_path=None))
        with pytest.raises(ValueError, match="folder_path"):
            await source.fetch()

    async def test_empty_folder(self, tmp_path: Path) -> None:
        source = UploadsIngestionSource(UploadsIngestionConfig(folder_path=tmp_path))
        result = await source.fetch()
        assert result["file_count"] == 0
        assert result["documents"] == []
        assert len(result["warnings"]) > 0

    async def test_text_files_parsed(self, uploads_folder: Path) -> None:
        source = UploadsIngestionSource(
            UploadsIngestionConfig(folder_path=uploads_folder)
        )
        result = await source.fetch()
        titles = {
            doc.title
            for doc in result["documents"]
            if doc.extraction_status == "COMPLETE"
        }
        assert "notes.txt" in titles
        assert "readme.md" in titles

    async def test_unrecognized_extension_produces_unsupported_row_not_silent_skip(
        self, uploads_folder: Path
    ) -> None:
        """Inverted from the pre-revamp behavior (§2.7): diagram.png must
        appear as a row with extraction_status=UNSUPPORTED, never vanish."""
        source = UploadsIngestionSource(
            UploadsIngestionConfig(folder_path=uploads_folder)
        )
        result = await source.fetch()
        png_docs = [d for d in result["documents"] if d.file_name == "diagram.png"]
        assert len(png_docs) == 1
        assert png_docs[0].extraction_status == "UNSUPPORTED"
        assert any(
            "unsupported_extension" in w for w in png_docs[0].extraction["warnings"]
        )
        assert png_docs[0].extraction["failure_code"] == "unsupported_extension"

    async def test_all_files_including_unsupported_counted(
        self, uploads_folder: Path
    ) -> None:
        source = UploadsIngestionSource(
            UploadsIngestionConfig(folder_path=uploads_folder)
        )
        result = await source.fetch()
        assert (
            result["file_count"] == 3
        )  # notes.txt, readme.md, diagram.png (as UNSUPPORTED)

    async def test_nonexistent_folder_warning(self, tmp_path: Path) -> None:
        source = UploadsIngestionSource(
            UploadsIngestionConfig(folder_path=tmp_path / "nope")
        )
        result = await source.fetch()
        assert result["file_count"] == 0
        assert any("does not exist" in w for w in result["warnings"])

    async def test_extraction_failure_produces_failed_row(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        f = tmp_path / "broken.txt"
        f.write_text("x")

        from evoq_ingestion.extractors.text_extractor import TextExtractor

        def boom(self, path, facts, deadline):
            raise RuntimeError("simulated extractor crash")

        monkeypatch.setattr(TextExtractor, "_parse", boom)
        source = UploadsIngestionSource(UploadsIngestionConfig(folder_path=tmp_path))
        result = await source.fetch()
        doc = result["documents"][0]
        assert doc.extraction_status == "FAILED"
        assert any("extraction_failed" in w for w in doc.extraction["warnings"])
        assert "simulated extractor crash" in doc.extraction["failure_message"]

    async def test_original_upload_path_preserved(self, uploads_folder: Path) -> None:
        """Legacy file_path semantics: for uploads it's always the original
        file, never the rendered body — see to_source_row()."""
        source = UploadsIngestionSource(
            UploadsIngestionConfig(folder_path=uploads_folder)
        )
        result = await source.fetch()
        notes_doc = next(d for d in result["documents"] if d.file_name == "notes.txt")
        assert notes_doc.original_upload_path == str(uploads_folder / "notes.txt")

    async def test_subdirectory_produces_failed_row_not_silent_skip(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "notes.txt").write_text("hello")
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "inner.txt").write_text("invisible unless recursive")

        source = UploadsIngestionSource(UploadsIngestionConfig(folder_path=tmp_path))
        result = await source.fetch()
        names = {d.file_name for d in result["documents"]}
        assert "notes.txt" in names
        assert "nested" in names
        nested_doc = next(d for d in result["documents"] if d.file_name == "nested")
        assert nested_doc.extraction_status == "FAILED"
        assert any(
            "subdirectory_skipped" in w for w in nested_doc.extraction["warnings"]
        )

    async def test_symlink_produces_failed_row(self, tmp_path: Path) -> None:
        target = tmp_path / "real.txt"
        target.write_text("hello")
        link = tmp_path / "link.txt"
        link.symlink_to(target)

        source = UploadsIngestionSource(UploadsIngestionConfig(folder_path=tmp_path))
        result = await source.fetch()
        link_doc = next(d for d in result["documents"] if d.file_name == "link.txt")
        assert link_doc.extraction_status == "FAILED"
        assert any("symlink_rejected" in w for w in link_doc.extraction["warnings"])


@pytest.mark.asyncio
class TestUploadsIngestionSourceExactFiles:
    """``UploadsIngestionConfig.files`` — process exactly this batch, not
    the whole folder (plan §1.3/§1.7 I8: the job's own staged files)."""

    async def test_processes_only_the_given_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        (tmp_path / "c.txt").write_text("not requested")

        config = UploadsIngestionConfig(files=(tmp_path / "a.txt", tmp_path / "b.txt"))
        source = UploadsIngestionSource(config)
        result = await source.fetch()

        assert result["file_count"] == 2
        names = {d.file_name for d in result["documents"]}
        assert names == {"a.txt", "b.txt"}

    async def test_missing_staged_file_produces_failed_row(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.txt").write_text("hello")
        config = UploadsIngestionConfig(
            files=(tmp_path / "a.txt", tmp_path / "gone.txt")
        )
        source = UploadsIngestionSource(config)
        result = await source.fetch()

        assert result["file_count"] == 2
        gone = next(d for d in result["documents"] if d.file_name == "gone.txt")
        assert gone.extraction_status == "FAILED"

    async def test_does_not_rescan_the_shared_folder(self, tmp_path: Path) -> None:
        """The whole point of ``files=``: a sibling file in the same
        permanent directory (from an earlier job) is never touched."""
        (tmp_path / "mine.txt").write_text("this job's file")
        (tmp_path / "earlier_job.txt").write_text("someone else's file")

        config = UploadsIngestionConfig(files=(tmp_path / "mine.txt",))
        source = UploadsIngestionSource(config)
        result = await source.fetch()

        assert result["file_count"] == 1
        assert result["documents"][0].file_name == "mine.txt"

    async def test_files_takes_precedence_over_folder_path(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        config = UploadsIngestionConfig(
            folder_path=tmp_path, files=(tmp_path / "a.txt",)
        )
        source = UploadsIngestionSource(config)
        result = await source.fetch()
        assert result["file_count"] == 1
        assert result["documents"][0].file_name == "a.txt"

    async def test_custom_options_respected_for_exact_files(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "data.csv"
        f.write_text("a,b\n" + "\n".join(f"{i},{i}" for i in range(10)))
        opts = ExtractionOptions()
        opts.csv_max_rows = 3
        config = UploadsIngestionConfig(files=(f,), extraction=opts)
        source = UploadsIngestionSource(config)
        result = await source.fetch()
        doc = result["documents"][0]
        assert doc.extraction_status == "COMPLETE"
        assert any(w.startswith("rows_truncated:") for w in doc.extraction["warnings"])

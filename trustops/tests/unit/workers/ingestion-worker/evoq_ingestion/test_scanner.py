"""Tests for ``sources/scanner.py::FolderScanner`` — containment, symlink
rejection, and non-recursive subdirectories reported rather than vanished.
"""

from __future__ import annotations

from pathlib import Path

from evoq_ingestion.sources.scanner import FolderScanner, scan_files


class TestBasicListing:
    def test_lists_regular_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.pdf").write_bytes(b"%PDF")
        entries = FolderScanner().scan(tmp_path)
        assert {e.path.name for e in entries} == {"a.txt", "b.pdf"}
        assert all(e.ok for e in entries)

    def test_deterministic_sorted_order(self, tmp_path: Path) -> None:
        for name in ("z.txt", "m.txt", "a.txt"):
            (tmp_path / name).write_text("x")
        entries = FolderScanner().scan(tmp_path)
        assert [e.path.name for e in entries] == ["a.txt", "m.txt", "z.txt"]

    def test_nonexistent_root_returns_empty(self, tmp_path: Path) -> None:
        assert FolderScanner().scan(tmp_path / "nope") == []

    def test_empty_root_returns_empty(self, tmp_path: Path) -> None:
        assert FolderScanner().scan(tmp_path) == []

    def test_scan_files_convenience_wrapper(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a")
        assert scan_files(tmp_path) == [tmp_path / "a.txt"]


class TestSubdirectories:
    def test_non_recursive_subdirectory_is_reported_not_vanished(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("b")

        entries = FolderScanner(recursive=False).scan(tmp_path)
        names = {e.path.name: e for e in entries}
        assert names["a.txt"].ok
        assert not names["sub"].ok
        assert names["sub"].status == "subdirectory_skipped"
        # The file *inside* the subdirectory never appears at all — it was
        # never descended into, which is exactly what the "sub" entry
        # reports to the caller.
        assert "b.txt" not in names

    def test_recursive_descends_into_subdirectories(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("b")

        entries = FolderScanner(recursive=True).scan(tmp_path)
        names = {e.path.name for e in entries if e.ok}
        assert names == {"a.txt", "b.txt"}
        # No "sub" entry at all when recursive — it was descended into, not reported.
        assert "sub" not in {e.path.name for e in entries}

    def test_deeply_nested_recursive(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "leaf.txt").write_text("leaf")
        entries = FolderScanner(recursive=True).scan(tmp_path)
        assert [e.path for e in entries if e.ok] == [deep / "leaf.txt"]


class TestSymlinks:
    def test_symlink_to_file_is_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "real.txt"
        target.write_text("hello")
        link = tmp_path / "link.txt"
        link.symlink_to(target)

        entries = FolderScanner().scan(tmp_path)
        by_name = {e.path.name: e for e in entries}
        assert by_name["real.txt"].ok
        assert not by_name["link.txt"].ok
        assert by_name["link.txt"].status == "symlink_rejected"

    def test_symlink_is_never_followed_even_recursive(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / f"outside-{tmp_path.name}"
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").write_text("should never be read")
        root = tmp_path / "root"
        root.mkdir()
        link = root / "escape"
        link.symlink_to(outside)

        try:
            entries = FolderScanner(recursive=True).scan(root)
            assert all(e.path.name != "secret.txt" for e in entries)
            assert any(e.status == "symlink_rejected" for e in entries)
        finally:
            for child in outside.iterdir():
                child.unlink()
            outside.rmdir()

    def test_symlink_to_directory_escaping_root_is_rejected(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / f"escape-target-{tmp_path.name}"
        outside.mkdir(exist_ok=True)
        root = tmp_path / "root"
        root.mkdir()
        link = root / "escape_dir"
        link.symlink_to(outside)

        try:
            entries = FolderScanner(recursive=False).scan(root)
            assert len(entries) == 1
            assert entries[0].status == "symlink_rejected"
        finally:
            outside.rmdir()


class TestContainment:
    def test_is_contained_true_for_direct_child(self, tmp_path: Path) -> None:
        scanner = FolderScanner()
        child = tmp_path / "a.txt"
        child.write_text("x")
        assert scanner._is_contained(child, tmp_path.resolve()) is True

    def test_is_contained_false_for_path_outside_root(self, tmp_path: Path) -> None:
        scanner = FolderScanner()
        outside = tmp_path.parent
        assert scanner._is_contained(outside, tmp_path.resolve()) is False

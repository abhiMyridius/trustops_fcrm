"""``storage/snapshot.py::seed_snapshot`` — forward-copy via hardlink, not a
full ``shutil.copytree`` of the whole previous snapshot on every build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from evoq_context.storage.layout import SnapshotLayout
from evoq_context.storage.snapshot import seed_snapshot


def _layout(tmp_path: Path) -> SnapshotLayout:
    return SnapshotLayout(tmp_path)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_unchanged_doc_is_hardlinked_not_copied(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    src_item = layout.items_dir("proj", version="v1") / "DOC1.json"
    _write(src_item, '{"id": "file:DOC1"}')

    report = seed_snapshot(layout, "proj", "v1", "v2", skip_source_ids=[])

    dst_item = layout.items_dir("proj", version="v2") / "DOC1.json"
    assert dst_item.exists()
    assert report.hardlinked == 1
    assert report.copied == 0
    # Same inode -> a real hardlink, not a copy.
    assert src_item.stat().st_ino == dst_item.stat().st_ino
    assert src_item.stat().st_nlink >= 2


def test_unchanged_chunks_file_is_hardlinked(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    src_chunks = layout.chunks_path("proj", "DOC1", version="v1")
    _write(src_chunks, '{"key": "file:DOC1:aaaa:0000", "text": "hi"}\n')

    seed_snapshot(layout, "proj", "v1", "v2", skip_source_ids=[])

    dst_chunks = layout.chunks_path("proj", "DOC1", version="v2")
    assert dst_chunks.exists()
    assert src_chunks.stat().st_ino == dst_chunks.stat().st_ino


def test_skip_source_ids_are_not_seeded(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout.items_dir("proj", version="v1") / "DOC1.json", '{"id": "file:DOC1"}')
    _write(layout.items_dir("proj", version="v1") / "DOC2.json", '{"id": "file:DOC2"}')

    report = seed_snapshot(layout, "proj", "v1", "v2", skip_source_ids=["DOC2"])

    dst = layout.items_dir("proj", version="v2")
    assert (dst / "DOC1.json").exists()
    assert not (dst / "DOC2.json").exists()
    assert report.hardlinked == 1
    assert report.skipped == 1
    assert "DOC2" in report.skipped_ids


def test_mutating_the_new_version_does_not_affect_the_old_one(tmp_path: Path) -> None:
    """Every writer in this module writes temp-then-os.replace, so a later
    build that rewrites DOC1 in v2 must never mutate the v1 file the
    hardlink shares an inode with."""
    layout = _layout(tmp_path)
    src_item = layout.items_dir("proj", version="v1") / "DOC1.json"
    _write(src_item, "original")

    seed_snapshot(layout, "proj", "v1", "v2", skip_source_ids=[])
    dst_item = layout.items_dir("proj", version="v2") / "DOC1.json"

    # Simulate a rewrite of v2's copy via the same temp+replace pattern
    # storage.snapshot's own writers use.
    tmp = dst_item.with_name(dst_item.name + ".tmp")
    tmp.write_text("rewritten", encoding="utf-8")
    os.replace(tmp, dst_item)

    assert src_item.read_text(encoding="utf-8") == "original"
    assert dst_item.read_text(encoding="utf-8") == "rewritten"


def test_falls_back_to_copy_when_hardlink_raises_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    src_item = layout.items_dir("proj", version="v1") / "DOC1.json"
    _write(src_item, '{"id": "file:DOC1"}')

    def _raise_exdev(*args, **kwargs):
        raise OSError(18, "Invalid cross-device link")  # EXDEV

    monkeypatch.setattr("evoq_context.storage.snapshot.os.link", _raise_exdev)

    report = seed_snapshot(layout, "proj", "v1", "v2", skip_source_ids=[])

    dst_item = layout.items_dir("proj", version="v2") / "DOC1.json"
    assert dst_item.exists()
    assert dst_item.read_text(encoding="utf-8") == '{"id": "file:DOC1"}'
    assert report.copied == 1
    assert report.hardlinked == 0


def test_missing_source_dirs_are_a_noop(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    report = seed_snapshot(layout, "proj", "v1", "v2", skip_source_ids=[])
    assert report.hardlinked == 0
    assert report.copied == 0
    assert report.skipped == 0

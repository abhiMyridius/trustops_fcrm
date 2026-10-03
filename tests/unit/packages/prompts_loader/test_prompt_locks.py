"""Registry lock lines: compute, render, parse, diff."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fcrm_prompts import (
    LockEntry,
    LockFormatError,
    compute_lock,
    diff_locks,
    parse_lock,
    render_lock,
    write_lock,
)

FIXTURE_AI = Path(__file__).parent / "fixtures" / "ai"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _prompt(version: int, body: str) -> str:
    return (
        f"---\nid: a.system\nversion: {version}\nmodel_tier: cheap\n"
        f"changelog:\n  - {{version: {version}, reason: r}}\n---\n{body}\n"
    )


def test_compute_lock_reads_versions_and_hashes_bytes() -> None:
    entries = {
        e.name: e for e in compute_lock(FIXTURE_AI / "prompts", "**/*.v[0-9]*.md")
    }
    assert sorted(entries) == [
        "research/research.system.v1.md",
        "research/research.system.v2.md",
        "research/research.user.v1.md",
    ]
    v2 = entries["research/research.system.v2.md"]
    assert v2.version == "2"
    raw = (FIXTURE_AI / "prompts" / "research" / "research.system.v2.md").read_bytes()
    assert v2.sha256 == hashlib.sha256(raw).hexdigest()


def test_compute_lock_yaml_and_unversioned_files(tmp_path: Path) -> None:
    _write(tmp_path / "a.agent.yaml", "name: a\nversion: 3\n")
    _write(tmp_path / "notes.txt", "plain")
    _write(tmp_path / ".hidden", "skip me")
    entries = {e.name: e.version for e in compute_lock(tmp_path)}
    assert entries == {"a.agent.yaml": "3", "notes.txt": "-"}


def test_compute_lock_absent_directory_is_empty(tmp_path: Path) -> None:
    assert compute_lock(tmp_path / "missing") == []


def test_render_lock_is_sorted_tab_separated_lines() -> None:
    entries = [
        LockEntry(name="b", version="1", sha256="22"),
        LockEntry(name="a", version="2", sha256="11"),
    ]
    assert render_lock(entries) == "a\t2\t11\nb\t1\t22\n"


def test_parse_lock_round_trips_render_lock() -> None:
    entries = compute_lock(FIXTURE_AI / "skills")
    assert parse_lock(render_lock(entries)) == entries


@pytest.mark.parametrize("text", ["a\t1\n", "a 1 ff\n", "a\t1\tff\na\t2\tee\n"])
def test_parse_lock_malformed_or_duplicate_raises(text: str) -> None:
    with pytest.raises(LockFormatError):
        parse_lock(text)


def test_diff_locks_edit_without_version_bump_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path / "p.v1.md", _prompt(1, "original"))
    committed = compute_lock(tmp_path)
    _write(tmp_path / "p.v1.md", _prompt(1, "edited in place"))
    assert diff_locks(committed, compute_lock(tmp_path)) == [
        "changed without a version bump: p.v1.md (v1)"
    ]


def test_diff_locks_added_removed_and_bumped(tmp_path: Path) -> None:
    _write(tmp_path / "keep.md", _prompt(1, "k"))
    _write(tmp_path / "gone.md", _prompt(1, "g"))
    committed = compute_lock(tmp_path)
    (tmp_path / "gone.md").unlink()
    _write(tmp_path / "keep.md", _prompt(2, "k2"))
    _write(tmp_path / "new.md", _prompt(1, "n"))
    assert diff_locks(committed, compute_lock(tmp_path)) == [
        "added: new.md",
        "removed: gone.md",
        "version 1 -> 2: keep.md",
    ]


def test_diff_locks_identical_is_empty() -> None:
    entries = compute_lock(FIXTURE_AI)
    assert diff_locks(entries, compute_lock(FIXTURE_AI)) == []


def test_write_lock_writes_rendered_text(tmp_path: Path) -> None:
    lock_path = tmp_path / "registry.lock" / "skills.lock"
    text = write_lock(FIXTURE_AI / "skills", lock_path)
    assert lock_path.read_text(encoding="utf-8") == text
    assert text.count("\n") == 2

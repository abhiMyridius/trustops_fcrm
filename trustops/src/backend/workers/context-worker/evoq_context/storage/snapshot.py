"""Snapshot-side artifacts under ``{vN}/`` — streamed, strict on corruption.

Scope & functionality
----------------------
Everything that reads or writes the on-disk shape of one snapshot version:
``chunks/{source_id}.jsonl`` (write via temp-file + ``os.replace`` so a
concurrent reader never observes a half-written file; read as a
**generator**, never a list — a 1,000-document snapshot's chunks are never
all resident in memory at once), ``vector_manifest.json``/
``index_manifest.json`` (missing = a legitimate first-run/no-backend case;
present-but-unparseable = :class:`~evoq_context.core.errors.ManifestCorrupt`,
loud rather than silently treated as empty), and ``seed_snapshot`` (forward-
copies unchanged documents from one version to the next via ``os.link``
hardlinks, falling back to ``shutil.copy`` only on ``OSError`` — this
replaces a full ``shutil.copytree`` of the *entire* previous snapshot on
every single build).

This module takes a :class:`~evoq_context.storage.layout.SnapshotLayout`,
never a host ``Settings``/``ContextConfig`` — the layout is the one thing
every caller (engine build stages, CLI, tests) already has or can build
from a bare ``projects_root``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evoq_context.core.errors import ManifestCorrupt, SnapshotCorrupt
from evoq_context.storage.layout import SnapshotLayout
from evoq_context.vector.contract import INDEX_CONTRACT_REVISION, INDEX_CONTRACT_VERSION
from evoq_context.vector.interface import StoredChunk, VectorChunk

logger = logging.getLogger(__name__)

ChunkRecordLike = VectorChunk | StoredChunk

__all__ = [
    "ChunkRecordLike",
    "SeedReport",
    "build_index_manifest",
    "delete_chunks_file",
    "iter_chunks_snapshot",
    "read_index_manifest",
    "read_vector_manifest",
    "seed_snapshot",
    "write_chunks_file",
    "write_index_manifest",
    "write_vector_manifest",
]


# ---------------------------------------------------------------------------
# chunks/{source_id}.jsonl — one file per document, temp + os.replace
# ---------------------------------------------------------------------------


def _chunk_record(chunk: ChunkRecordLike) -> dict[str, Any]:
    if isinstance(chunk, StoredChunk):
        return {
            "key": chunk.key,
            "chunk_index": chunk.chunk_index,
            "text": chunk.text,
            "metadata": dict(chunk.metadata),
        }
    return {
        "key": chunk.chunk_id,
        "chunk_index": chunk.metadata.get("chunk_index"),
        "text": chunk.text,
        "metadata": dict(chunk.metadata),
    }


def write_chunks_file(
    layout: SnapshotLayout,
    project: str,
    source_id: str,
    records: Sequence[ChunkRecordLike],
    *,
    version: str,
) -> Path:
    """Write one document's ``chunks/{source_id}.jsonl`` atomically.

    ``records`` is normally a document's ``vector_chunks + stored_chunks``
    (children then parents) from one ``PreparedDocument`` — the only place
    a chunk's full, untruncated text survives after indexing. Writes to a
    ``.tmp`` sibling then ``os.replace``s it into place so a reader never
    sees a partially-written file, even under concurrent per-document
    writers (:class:`~evoq_context.pipeline.vectors.VectorWriter`).
    """
    path = layout.chunks_path(project, source_id, version=version)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(_chunk_record(c), ensure_ascii=False) for c in records]
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp_path, path)
    return path


def delete_chunks_file(
    layout: SnapshotLayout, project: str, source_id: str, *, version: str
) -> None:
    """Delete one document's ``chunks/{source_id}.jsonl``, if it exists."""
    path = layout.chunks_path(project, source_id, version=version)
    if path.exists():
        path.unlink()


def iter_chunks_snapshot(
    layout: SnapshotLayout, project: str, version: str
) -> Iterator[dict[str, Any]]:
    """Stream every chunk record (children and parents) under one snapshot.

    A generator — never a list-returning function — so rendering
    ``context.md``/``document_inventory.md`` over a large project never
    holds every chunk of every document in memory at once. A malformed
    line raises :class:`SnapshotCorrupt` immediately; it is never silently
    skipped, since a silently-skipped requirement chunk is exactly the kind
    of defect this contract exists to prevent.
    """
    chunks_dir = layout.chunks_dir(project, version=version)
    if not chunks_dir.exists():
        return
    for path in sorted(chunks_dir.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception as exc:
                    raise SnapshotCorrupt(
                        f"{path}:{lineno} is not valid JSON: {exc}"
                    ) from exc
                yield record


# ---------------------------------------------------------------------------
# vector_manifest.json / index_manifest.json — missing != corrupt
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    """Missing -> ``None`` (a legitimate first-run case).

    Present-but-unparseable raises :class:`ManifestCorrupt` — the caller
    decides what "missing" defaults to.
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ManifestCorrupt(f"{path} exists but could not be parsed: {exc}") from exc


def _atomic_write_json(path: Path, payload: Any, *, indent: int, sort_keys: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=indent, sort_keys=sort_keys), encoding="utf-8")
    os.replace(tmp_path, path)


def read_vector_manifest(path: Path) -> dict[str, dict[str, Any]]:
    """``{doc_id: {...}}``.

    Missing file -> ``{}`` (first run). A present but unparseable file
    raises :class:`ManifestCorrupt`.
    """
    data = _read_json(path)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ManifestCorrupt(f"{path} does not contain a JSON object")
    return data


def write_vector_manifest(path: Path, manifest: dict[str, dict[str, Any]]) -> None:
    """Write ``vector_manifest.json`` atomically (temp file + ``os.replace``)."""
    _atomic_write_json(path, manifest, indent=2, sort_keys=True)


def read_index_manifest(path: Path) -> dict[str, Any] | None:
    """Missing file -> ``None`` (no vector backend configured, or first run).

    A legitimate case, not corruption. A present but unparseable file
    raises :class:`ManifestCorrupt`.
    """
    data = _read_json(path)
    if data is not None and not isinstance(data, dict):
        raise ManifestCorrupt(f"{path} does not contain a JSON object")
    return data


def write_index_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write ``index_manifest.json`` atomically (temp file + ``os.replace``)."""
    _atomic_write_json(path, manifest, indent=2)


def build_index_manifest(
    *,
    project_id: str,
    snapshot_version: str,
    context_version: int,
    backend: str,
    store: str,
    index: str,
    region: str,
    distance_metric: str,
    embedding_model: str,
    embedding_dimension: int,
    vector_manifest: dict[str, dict[str, Any]],
    generated_at: str,
    contract_revision: str = INDEX_CONTRACT_REVISION,
    parent_chunk_count: int = 0,
) -> dict[str, Any]:
    """Assemble ``index_manifest.json`` from the snapshot's ``vector_manifest.json``.

    ``store`` must be the backend-appropriate *store* concept (an S3
    Vectors **bucket**, a Postgres DSN, a collection name — from
    ``VectorStore.describe()["store"]``), never the index/table name; the
    two are frequently different and a manifest that conflates them makes
    the MCP warn on every single build. ``strategy_histogram`` tallies
    *chunks* per strategy (one strategy per document, so this sums each
    contributing document's ``chunk_count``). ``parent_chunk_count``
    defaults to the sum of each document's ``parent_count`` when the
    caller does not supply one explicitly.
    """
    chunk_count = sum(int(v.get("chunk_count", 0)) for v in vector_manifest.values())
    doc_count = len(vector_manifest)
    histogram: dict[str, int] = {}
    for v in vector_manifest.values():
        strategy = v.get("chunk_strategy") or "unknown"
        histogram[strategy] = histogram.get(strategy, 0) + int(v.get("chunk_count", 0))
    if not parent_chunk_count:
        parent_chunk_count = sum(int(v.get("parent_count", 0)) for v in vector_manifest.values())
    return {
        "manifest_version": 1,
        "project_id": project_id,
        "snapshot_version": snapshot_version,
        "context_version": context_version,
        "backend": backend,
        "store": store,
        "index": index,
        "region": region,
        "distance_metric": distance_metric,
        "embedding_model": embedding_model,
        "embedding_dimension": embedding_dimension,
        "index_contract": INDEX_CONTRACT_VERSION,
        "contract_revision": contract_revision,
        "chunk_count": chunk_count,
        "doc_count": doc_count,
        "parent_chunk_count": parent_chunk_count,
        "strategy_histogram": histogram,
        "generated_at": generated_at,
    }


# ---------------------------------------------------------------------------
# seed_snapshot — hardlink-forward unchanged docs (replaces shutil.copytree)
# ---------------------------------------------------------------------------


@dataclass
class SeedReport:
    """Outcome of one :func:`seed_snapshot` call."""

    hardlinked: int = 0
    copied: int = 0
    skipped: int = 0
    skipped_ids: list[str] = field(default_factory=list)


def _link_or_copy(src: Path, dst: Path) -> bool:
    """Hardlink ``src`` -> ``dst``, falling back to a real copy on ``OSError``.

    A cross-device ``EXDEV`` or permission ``EPERM`` triggers the fallback.
    Returns ``True`` when a hardlink was made, ``False`` when it fell back
    to a copy.
    """
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
        return True
    except OSError:
        shutil.copy2(src, dst)
        return False


def seed_snapshot(
    layout: SnapshotLayout,
    project: str,
    from_version: str,
    to_version: str,
    *,
    skip_source_ids: Iterable[str] = (),
) -> SeedReport:
    """Copy-forward unchanged ``items/*.json`` and ``chunks/*.jsonl`` via hardlink.

    Only documents that are NOT about to change are seeded. This replaces a
    full ``shutil.copytree`` of the entire previous snapshot on every build
    (memory/disk fix, audit §5.12 point 6): a hardlink costs one
    directory-entry write, not a byte-for-byte copy, and
    because every writer in this module writes temp-then-``os.replace``,
    the hardlinked file in the *older* snapshot is never mutated in place.
    ``skip_source_ids`` (documents about to be rewritten, updated or
    deleted in ``to_version``) are left for the caller to write instead.
    """
    skip = {str(s) for s in skip_source_ids}
    report = SeedReport()

    src_items = layout.items_dir(project, version=from_version)
    dst_items = layout.items_dir(project, version=to_version)
    if src_items.exists():
        dst_items.mkdir(parents=True, exist_ok=True)
        for src_file in sorted(src_items.glob("*.json")):
            source_id = src_file.stem
            if source_id in skip:
                report.skipped += 1
                report.skipped_ids.append(source_id)
                continue
            if _link_or_copy(src_file, dst_items / src_file.name):
                report.hardlinked += 1
            else:
                report.copied += 1

    src_chunks = layout.chunks_dir(project, version=from_version)
    dst_chunks = layout.chunks_dir(project, version=to_version)
    if src_chunks.exists():
        dst_chunks.mkdir(parents=True, exist_ok=True)
        for src_file in sorted(src_chunks.glob("*.jsonl")):
            source_id = src_file.stem
            if source_id in skip:
                report.skipped += 1
                continue
            if _link_or_copy(src_file, dst_chunks / src_file.name):
                report.hardlinked += 1
            else:
                report.copied += 1

    logger.info(
        "seed_snapshot %s %s->%s: hardlinked=%d copied=%d skipped=%d",
        project, from_version, to_version, report.hardlinked, report.copied, report.skipped,
    )
    return report

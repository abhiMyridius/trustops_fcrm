"""Local filesystem content store (``items/``) — canonical document persistence.

Scope & functionality
----------------------
``LocalContentStore`` is the one thing this module owns going forward: one
JSON file per item under ``items/{source_id}.json``.

The snapshot-side artifacts (``chunks/{source_id}.jsonl``,
``vector_manifest.json``, ``index_manifest.json``, forward-seeding) now live
in ``storage/snapshot.py``, which is layout-based, streams instead of
loading a whole snapshot into memory, and is strict about corruption
(raises ``SnapshotCorrupt``/``ManifestCorrupt`` rather than silently
treating a broken file as empty). The functions below this line are thin,
**deprecated** ``ContextConfig``-based shims that adapt the new, strict
functions back to the old, lenient contract (corrupt -> logged and treated
as empty/partial) — they exist only so pre-existing callers
(``pipeline/build.py``, ``pipeline/revert.py``, ``run_vectors_only.py``,
still on the old signatures as of this lane) keep working without this
lane reaching into files it does not own. New code should call
``storage/snapshot.py`` directly with a ``SnapshotLayout``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from evoq_context.core.config import ContextConfig
from evoq_context.core.errors import ManifestCorrupt, SnapshotCorrupt
from evoq_context.core.models import ContextItem
from evoq_context.storage import snapshot as _snapshot
from evoq_context.storage.interface import ContentStore
from evoq_context.vector.interface import VectorChunk

logger = logging.getLogger(__name__)


class LocalContentStore(ContentStore):
    """Content store backed by local JSON files."""

    def __init__(self, config: ContextConfig, version: str) -> None:
        self._config = config
        self._version = version

    def _path_for_id(self, doc_id: str, project: str) -> Path | None:
        parts = doc_id.split(":", 1)
        if len(parts) != 2:
            return None
        return self._config.items_dir(
            project, version=self._version,
        ) / f"{parts[1]}.json"

    def add(self, item: ContextItem) -> None:
        path = (
            self._config.items_dir(item.project, version=self._version)
            / f"{item.source_id}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(item.to_dict(), indent=2), encoding="utf-8",
        )
        logger.info("Stored %s -> %s", item.id, path)

    def list_items(self, project: str) -> list[ContextItem]:
        d = self._config.items_dir(project, version=self._version)
        if not d.exists():
            return []
        items: list[ContextItem] = []
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                items.append(ContextItem.from_dict(data))
            except Exception:
                logger.exception("Corrupt content file %s", f)
        return items

    def delete(self, doc_id: str, project: str) -> bool:
        path = self._path_for_id(doc_id, project)
        if path is None or not path.exists():
            return False
        path.unlink()
        logger.info("Deleted %s", path)
        return True


# ---------------------------------------------------------------------------
# DEPRECATED: ContextConfig-based shims over storage/snapshot.py.
# See the module docstring — these adapt the new strict API back to the old
# lenient one for callers not yet migrated to SnapshotLayout.
# ---------------------------------------------------------------------------


def write_chunks_snapshot(
    config: ContextConfig,
    project: str,
    version: str,
    chunks: list[VectorChunk],
) -> None:
    """DEPRECATED: groups a flat chunk list by ``source_id`` and writes one
    ``chunks/{source_id}.jsonl`` per group via ``storage.snapshot``. New
    code should call ``snapshot.write_chunks_file`` once per document as it
    finishes, instead of batching every document's chunks into one flat
    list first (that flat list is exactly the memory problem
    ``pipeline/vectors.py::VectorWriter`` fixes).
    """
    by_source: dict[str, list[VectorChunk]] = {}
    for chunk in chunks:
        source_id = str(chunk.metadata.get("source_id") or "unknown")
        by_source.setdefault(source_id, []).append(chunk)
    for source_id, doc_chunks in by_source.items():
        _snapshot.write_chunks_file(
            config.layout, project, source_id, doc_chunks, version=version,
        )


def delete_chunks_file(config: ContextConfig, project: str, source_id: str, *, version: str) -> None:
    _snapshot.delete_chunks_file(config.layout, project, source_id, version=version)


def read_chunks_snapshot(
    config: ContextConfig, project: str, version: str
) -> list[dict[str, Any]]:
    """DEPRECATED lenient wrapper over ``snapshot.iter_chunks_snapshot``:
    a corrupt line is logged and the rest of that snapshot is still
    returned, matching the old best-effort behaviour. New code should call
    ``iter_chunks_snapshot`` directly and let ``SnapshotCorrupt`` propagate.
    """
    try:
        return list(_snapshot.iter_chunks_snapshot(config.layout, project, version))
    except SnapshotCorrupt:
        logger.exception(
            "Corrupt chunk snapshot for %s/%s — returning what could be read "
            "(legacy lenient path; new callers should handle SnapshotCorrupt)",
            project, version,
        )
        return []


def read_vector_manifest(path: Path) -> dict[str, dict[str, Any]]:
    """DEPRECATED lenient wrapper: corrupt -> logged, treated as ``{}``."""
    try:
        return _snapshot.read_vector_manifest(path)
    except ManifestCorrupt:
        logger.exception("Corrupt vector_manifest.json at %s — treating as empty", path)
        return {}


def write_vector_manifest(path: Path, manifest: dict[str, dict[str, Any]]) -> None:
    _snapshot.write_vector_manifest(path, manifest)


def read_index_manifest(path: Path) -> dict[str, Any] | None:
    """DEPRECATED lenient wrapper: corrupt -> logged, treated as ``None``."""
    try:
        return _snapshot.read_index_manifest(path)
    except ManifestCorrupt:
        logger.exception("Corrupt index_manifest.json at %s", path)
        return None


def write_index_manifest(path: Path, manifest: dict[str, Any]) -> None:
    _snapshot.write_index_manifest(path, manifest)


def build_index_manifest(**kwargs: Any) -> dict[str, Any]:
    return _snapshot.build_index_manifest(**kwargs)

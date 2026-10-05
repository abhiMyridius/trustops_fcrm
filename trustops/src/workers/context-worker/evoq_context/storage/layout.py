"""``SnapshotLayout`` — every path under ``{projects_root}/{project}/{vN}/``.

Scope & functionality
---------------------
The one place that knows the on-disk snapshot layout: the project dir, the
``current.txt`` pointer, ``vN``/``vN.tmp`` version dirs, ``items/``,
``chunks/{source_id}.jsonl``, ``state.json``, ``vector_manifest.json``,
``index_manifest.json``, ``context.md``, ``document_inventory.md`` and the
project-level ``activity.jsonl`` — plus the version-label arithmetic
(``normalize_version_label``, ``bump_version_label``, …) and the legacy
flat-layout migration. ``ContextConfig.layout`` returns one of these, and
``ContextConfig``'s path helpers delegate here so existing callers keep
working unchanged.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

_CURRENT_POINTER = "current.txt"
_VERSION_LABEL_RE = re.compile(r"^v(\d+)$", re.IGNORECASE)


class SnapshotLayout:
    """Path arithmetic for one ``projects_root`` (no I/O beyond reading ``current.txt``)."""

    def __init__(
        self,
        projects_root: Path,
        *,
        activity_log_path: Path | None = None,
        context_md_path: Path | None = None,
        document_inventory_path: Path | None = None,
    ) -> None:
        self.projects_root = projects_root
        self.activity_log_path = activity_log_path
        self.context_md_path = context_md_path
        self.document_inventory_path = document_inventory_path

    # -- project / pointer ---------------------------------------------------

    def project_dir(self, project: str) -> Path:
        return self.projects_root / project

    def current_pointer_path(self, project: str) -> Path:
        """Path to the file storing the active version label (e.g. ``v2``)."""
        return self.project_dir(project) / _CURRENT_POINTER

    def read_current_version_label(self, project: str) -> str | None:
        """Return the active version label from ``current.txt``, or ``None``."""
        path = self.current_pointer_path(project)
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8").strip()
        return raw or None

    def _resolve_version(self, project: str, version: str | None) -> str:
        if version is not None:
            return version
        return self.read_current_version_label(project) or "v1"

    # -- version dirs ----------------------------------------------------------

    def version_dir(self, project: str, version: str) -> Path:
        """Root directory for one snapshot, e.g. ``projects/foo/v1/``."""
        return self.project_dir(project) / version

    def tmp_version_dir(self, project: str, version: str) -> Path:
        """Staging directory for one snapshot before the atomic rename."""
        return self.project_dir(project) / f"{version}.tmp"

    def items_dir(self, project: str, *, version: str | None = None) -> Path:
        return self.version_dir(project, self._resolve_version(project, version)) / "items"

    def chunks_dir(self, project: str, *, version: str) -> Path:
        """``chunks/`` directory holding one ``{source_id}.jsonl`` per document."""
        return self.version_dir(project, version) / "chunks"

    def chunks_path(self, project: str, source_id: str, *, version: str) -> Path:
        return self.chunks_dir(project, version=version) / f"{source_id}.jsonl"

    def state_path(self, project: str, *, version: str | None = None) -> Path:
        return self.version_dir(project, self._resolve_version(project, version)) / "state.json"

    def vector_manifest_path(self, project: str, *, version: str) -> Path:
        return self.version_dir(project, version) / "vector_manifest.json"

    def index_manifest_path(self, project: str, *, version: str) -> Path:
        return self.version_dir(project, version) / "index_manifest.json"

    def resolve_activity_path(self, project: str) -> Path:
        if self.activity_log_path:
            return self.activity_log_path
        return self.project_dir(project) / "activity.jsonl"

    def resolve_context_md_path(self, project: str, *, version: str | None = None) -> Path:
        if self.context_md_path:
            return self.context_md_path
        return self.version_dir(project, self._resolve_version(project, version)) / "context.md"

    def resolve_document_inventory_path(
        self, project: str, *, version: str | None = None
    ) -> Path:
        if self.document_inventory_path:
            return self.document_inventory_path
        return (
            self.version_dir(project, self._resolve_version(project, version))
            / "document_inventory.md"
        )

    # -- version labels ----------------------------------------------------------

    @staticmethod
    def normalize_version_label(version: str | int) -> str:
        """Normalize numeric or label input to the canonical ``vN`` form."""
        if isinstance(version, int):
            if version <= 0:
                raise ValueError("Version number must be >= 1.")
            return f"v{version}"
        raw = str(version).strip()
        if not raw:
            raise ValueError("Version cannot be empty.")
        if raw.lower().startswith("v"):
            raw = raw[1:].strip()
        if not raw.isdigit():
            raise ValueError(f"Invalid version format: {version!r}")
        number = int(raw)
        if number <= 0:
            raise ValueError("Version number must be >= 1.")
        return f"v{number}"

    @staticmethod
    def version_label_to_number(label: str) -> int:
        """Convert a canonical or loose version label into numeric form."""
        return int(SnapshotLayout.normalize_version_label(label)[1:])

    @staticmethod
    def bump_version_label(current: str | None) -> str:
        """Next write version: ``None -> v1``, ``v1 -> v2``, ..."""
        if not current:
            return "v1"
        match = _VERSION_LABEL_RE.match(current.strip())
        if not match:
            return "v1"
        return f"v{int(match.group(1)) + 1}"

    @staticmethod
    def previous_version_label(current: str) -> str | None:
        """Previous label: ``v2 -> v1``, ``v1 -> None``."""
        match = _VERSION_LABEL_RE.match(current.strip())
        if not match:
            return None
        number = int(match.group(1))
        return f"v{number - 1}" if number > 1 else None

    # -- migration / seeding (copy semantics; the streaming seeder is storage/snapshot.py's) ---

    def migrate_flat_layout_to_v1(self, project: str) -> None:
        """One-time move of the legacy flat layout into ``v1/`` and write ``current.txt``."""
        root = self.project_dir(project)
        pointer = self.current_pointer_path(project)
        if pointer.exists():
            return
        state_flat = root / "state.json"
        ctx_flat = root / "context.md"
        items_flat = root / "items"
        has_flat = (
            state_flat.is_file()
            or ctx_flat.is_file()
            or (items_flat.is_dir() and any(items_flat.glob("*.json")))
        )
        if not has_flat:
            return
        v1 = self.version_dir(project, "v1")
        v1.mkdir(parents=True, exist_ok=True)
        if state_flat.exists():
            state_flat.rename(v1 / "state.json")
        if ctx_flat.exists():
            ctx_flat.rename(v1 / "context.md")
        if items_flat.exists():
            dest_items = v1 / "items"
            if dest_items.exists():
                shutil.rmtree(dest_items)
            shutil.move(str(items_flat), str(dest_items))
        pointer.write_text("v1\n", encoding="utf-8")

    def _seed_dir(self, src: Path, dst: Path) -> None:
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)

    def seed_items_from_version(self, project: str, from_version: str, to_version: str) -> None:
        """Copy ``items/`` from ``from_version`` into ``to_version``."""
        self._seed_dir(
            self.items_dir(project, version=from_version),
            self.items_dir(project, version=to_version),
        )

    def seed_chunks_from_version(self, project: str, from_version: str, to_version: str) -> None:
        """Copy ``chunks/`` from ``from_version`` into ``to_version`` (copy-forward)."""
        self._seed_dir(
            self.chunks_dir(project, version=from_version),
            self.chunks_dir(project, version=to_version),
        )

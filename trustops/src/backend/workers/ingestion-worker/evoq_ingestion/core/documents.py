"""``CanonicalDocument`` — the one shape emitted for every ``source_type``.

Scope & functionality
---------------------
Implements the Canonical Document contract (``AEO/contracts/canonical_document.md``):
one dataclass produced by every source (Confluence/Jira/file) and every
extractor, carrying the rendered Markdown body plus the additive metadata
fields (``structure``, ``extraction``, ``profile_hints``, ``hierarchy``,
legacy per-type ``metadata``). ``structure`` and ``extraction`` accept the
typed ``model.Structure`` / ``extractors.ExtractionRecord`` objects and
store their ``to_dict()`` form, so the row shape is produced in exactly one
place per type. ``to_source_row()`` is the only place that knows how this
maps onto the Mongo ``sources`` document shape ``jobs/source_store.py``
persists — callers never build that dict by hand.

This module has no I/O; ``body_path``/``raw_path`` are plain strings decided
by the caller (``jobs/source_store.py`` writes the actual files).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from evoq_ingestion.core.hashing import compute_content_hash, compute_metadata_hash
from evoq_ingestion.core.utils import utc_now_iso
from evoq_ingestion.extractors.record import ExtractionRecord
from evoq_ingestion.model.structure import Structure

__all__ = ["CanonicalDocument", "utc_now_iso"]


def _as_dict(value: Mapping[str, Any] | Structure | ExtractionRecord) -> dict[str, Any]:
    if isinstance(value, Structure | ExtractionRecord):
        return value.to_dict()
    return dict(value)


@dataclass
class CanonicalDocument:
    """One normalized document, independent of its source type.

    ``source_id``/``version``/``is_active`` are filled in by the storage
    layer (``jobs/source_store.py``), which alone knows the document's sync
    history; everything else is produced by the source/extractor.
    """

    # Identity
    source_type: str  # "confluence" | "jira" | "file"
    source_key: str
    origin_id: str
    scope_key: str | None = None

    # Body
    body_markdown: str = ""
    raw_format: str | None = None
    raw_content: str | bytes | None = None  # transient; writer persists then drops

    # Legacy path fields (source-type specific meaning, see contract)
    legacy_file_path: str | None = None
    legacy_extracted_text_path: str | None = None
    original_upload_path: str | None = None

    # Top-level metadata
    title: str = ""
    url: str | None = None
    domain_url: str | None = None
    labels: list[str] = field(default_factory=list)
    authors: list[dict[str, Any]] = field(default_factory=list)
    source_created_at: str | None = None
    source_updated_at: str | None = None
    source_version: Any = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    attachments_truncated: bool = False

    hierarchy: dict[str, Any] = field(default_factory=dict)
    structure: dict[str, Any] = field(default_factory=dict)
    extraction: dict[str, Any] = field(default_factory=dict)
    profile_hints: dict[str, Any] = field(default_factory=dict)
    extraction_status: str = "COMPLETE"  # PENDING | COMPLETE | FAILED | UNSUPPORTED

    # Legacy per-type metadata sub-dict (verbatim keys, §1.1)
    metadata: dict[str, Any] = field(default_factory=dict)

    # File-specific bookkeeping the job needs at insert time (not persisted
    # verbatim — mapped into the row by the store).
    file_name: str | None = None
    file_size: int | None = None
    content_type: str | None = None

    def __post_init__(self) -> None:
        # Typed Structure / ExtractionRecord instances are accepted and
        # normalised to their contract dict form exactly once, here.
        self.structure = _as_dict(self.structure)
        self.extraction = _as_dict(self.extraction)

    def content_hash(self) -> str:
        """``sha256:`` of the canonicalised body (contract §Identity)."""
        return compute_content_hash(self.body_markdown)

    def metadata_hash(self) -> str:
        """Hash over the fields whose change refreshes a row in place."""
        return compute_metadata_hash(
            labels=self.labels,
            hierarchy=self.hierarchy,
            authors=self.authors,
            source_version=self.source_version,
            metadata=self.metadata,
        )

    def extraction_record(self) -> ExtractionRecord:
        """The typed view of ``extraction`` (rebuilt from the stored dict)."""
        return ExtractionRecord.from_dict(self.extraction)

    def to_source_row(
        self,
        *,
        source_id: str,
        version: int,
        is_active: bool,
        job_id: str | None,
        created_by: str | None,
        body_path: str | None,
        raw_path: str | None,
        body_inline_max_bytes: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Build the additive Mongo ``sources`` row for this document.

        ``body_markdown`` is inlined only when it fits under
        ``body_inline_max_bytes``; ``body_path`` is always set (the caller
        has already written the file atomically before calling this).
        """
        now = now or datetime.now(UTC)
        body_bytes = len(self.body_markdown.encode("utf-8"))
        body_inline = body_bytes <= body_inline_max_bytes
        content_hash = self.content_hash()
        metadata_hash = self.metadata_hash()

        row: dict[str, Any] = {
            "source_id": source_id,
            "job_id": job_id,
            "origin_id": self.origin_id,
            "source_type": self.source_type,
            "source_key": self.source_key,
            "scope_key": self.scope_key,
            "source_display_name": self.title,
            "file_name": self.file_name or self.title,
            "url": self.url,
            "domain_url": self.domain_url,
            "is_active": is_active,
            "version": version,
            "created_by": created_by,
            "created_on": now,
            # Identity & change detection
            "content_hash": content_hash,
            "metadata_hash": metadata_hash,
            # Body
            "body_format": "markdown",
            "body_markdown": self.body_markdown if body_inline else None,
            "body_inline": body_inline,
            "body_chars": len(self.body_markdown),
            "body_bytes": body_bytes,
            "body_path": body_path,
            "raw_format": self.raw_format,
            "raw_path": raw_path,
            # Top-level metadata
            "title": self.title,
            "labels": self.labels,
            "authors": self.authors,
            "source_created_at": self.source_created_at,
            "source_updated_at": self.source_updated_at,
            "source_version": self.source_version,
            "attachments": self.attachments,
            "attachments_truncated": self.attachments_truncated,
            "hierarchy": self.hierarchy,
            "structure": self.structure,
            "extraction": self.extraction,
            "profile_hints": self.profile_hints,
            "extraction_status": self.extraction_status,
            # Legacy sub-dict, verbatim
            "metadata": self.metadata,
            # Sync bookkeeping
            "first_seen_at": now,
            "last_seen_at": now,
            "last_seen_job_id": job_id,
        }

        # Legacy path fields — exact semantics per contract.
        if self.source_type == "confluence":
            row["file_path"] = body_path
        elif self.source_type == "file":
            row["file_path"] = self.original_upload_path or self.legacy_file_path
            row["extracted_text_path"] = body_path
        else:  # jira
            row["file_path"] = None

        if self.file_size is not None:
            row["file_size"] = self.file_size
        if self.content_type is not None:
            row["content_type"] = self.content_type

        return row

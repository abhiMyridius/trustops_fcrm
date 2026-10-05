"""Plain data models: extracted input, stored items, change sets, JSON typing.

Scope & functionality
---------------------
``ExtractedItem`` is what any upstream hands the engine (it never knows
what produced it); ``ContextItem`` is the canonical unit persisted under a
snapshot's ``items/``; ``ChangeSet`` is the reconciler's diff. ``JsonValue``
is the recursive alias used wherever the engine carries JSON-shaped data
of unknown structure (item metadata, profile hints) so that shape stays
honest without resorting to ``Any``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypeAlias

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
"""Any JSON-serialisable value (recursive)."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ExtractedItem:
    """Generic input from any upstream source.

    The context module receives these — it never knows or cares what
    produced them (ingestion pipeline, API, manual script, etc.).
    """

    source_type: str
    source_id: str
    title: str
    body: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    content_hash: str = ""

    def ensure_hash(self) -> None:
        """Compute content_hash if the caller did not provide one."""
        if not self.content_hash:
            self.content_hash = compute_content_hash(self.body.encode("utf-8"))

    @property
    def doc_id(self) -> str:
        return make_doc_id(self.source_type, self.source_id)


@dataclass
class ContextItem:
    """Canonical unit stored in the local content store (one per doc)."""

    id: str
    source_type: str
    source_id: str
    title: str
    body: str
    content_hash: str
    project: str
    ingested_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = ""
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize to a plain dict for JSON storage."""
        return {
            "id": self.id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "body": self.body,
            "content_hash": self.content_hash,
            "project": self.project,
            "ingested_at": self.ingested_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> ContextItem:
        """Deserialize from a plain dict."""
        metadata = data.get("metadata", {})
        return cls(
            id=str(data["id"]),
            source_type=str(data["source_type"]),
            source_id=str(data["source_id"]),
            title=str(data["title"]),
            body=str(data["body"]),
            content_hash=str(data["content_hash"]),
            project=str(data["project"]),
            ingested_at=str(data.get("ingested_at", "")),
            updated_at=str(data.get("updated_at", "")),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    @classmethod
    def from_extracted(cls, item: ExtractedItem, project: str) -> ContextItem:
        """Create a ContextItem from an ExtractedItem."""
        item.ensure_hash()
        return cls(
            id=item.doc_id,
            source_type=item.source_type,
            source_id=item.source_id,
            title=item.title,
            body=item.body,
            content_hash=item.content_hash,
            project=project,
            metadata=item.metadata,
        )


@dataclass
class ChangeSet:
    """Result of reconciling current input against previous state."""

    new_ids: list[str] = field(default_factory=list)
    updated_ids: list[str] = field(default_factory=list)
    deleted_ids: list[str] = field(default_factory=list)
    unchanged_count: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.new_ids or self.updated_ids or self.deleted_ids)

    @property
    def total_received(self) -> int:
        return len(self.new_ids) + len(self.updated_ids) + self.unchanged_count


def compute_content_hash(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def make_doc_id(source_type: str, source_id: str) -> str:
    """Build a stable document id from source type and source id."""
    return f"{source_type}:{source_id}"

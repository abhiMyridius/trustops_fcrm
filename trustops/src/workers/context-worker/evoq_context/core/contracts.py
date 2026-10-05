"""Public input / output contracts for the context module."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from evoq_context.core.models import ExtractedItem

if TYPE_CHECKING:  # pragma: no cover - typing only; avoids a contracts<->observer import cycle
    from evoq_context.core.observer import BuildSummary


def _new_run_id() -> str:
    return uuid.uuid4().hex


@dataclass
class ContextBuildRequest:
    """Everything the caller provides to build / update context."""

    project: str
    goal_prompt: str
    extracted_items: list[ExtractedItem]
    run_id: str = field(default_factory=_new_run_id)


@dataclass
class ComponentStatus:
    """Execution status of a single pipeline stage."""

    name: str
    status: str
    message: str = ""
    error_code: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
        }
        if self.message:
            d["message"] = self.message
        if self.error_code:
            d["error_code"] = self.error_code
        if self.duration_ms:
            d["duration_ms"] = self.duration_ms
        return d


@dataclass
class ErrorDetail:
    """One error captured during the pipeline."""

    stage: str
    code: str
    message: str
    doc_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
        }
        if self.doc_id:
            d["doc_id"] = self.doc_id
        return d


@dataclass
class ContextBuildResult:
    """What the context module returns to the caller."""

    project: str
    status: str
    message: str
    goals: list[str] = field(default_factory=list)
    #: ``int | str`` (plan §2.5): most values are counts, but ``reindex_reason``
    #: is a string flag carried in the same dict.
    stats: dict[str, int | str] = field(default_factory=dict)
    errors: list[ErrorDetail] = field(default_factory=list)
    component_statuses: list[ComponentStatus] = field(
        default_factory=list
    )
    change_details: dict[str, list[str]] = field(default_factory=dict)
    context_location: dict[str, str] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    #: Additive (plan §3.1a): the one ``BuildSummary`` emitted for this run
    #: (``None`` only for a pre-stage short-circuit, e.g. ``drift_error``,
    #: where no summary was ever assembled).
    summary: BuildSummary | None = None
    #: ``False`` when this snapshot must not be treated as a complete,
    #: servable result (render/publish/save-state failed outright, or every
    #: document failed vector-write) — see ``pipeline/result.py``.
    publishable: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the output JSON contract."""
        d: dict[str, Any] = {
            "project": self.project,
            "status": self.status,
            "message": self.message,
            "goals": self.goals,
            "stats": self.stats,
            "errors": [e.to_dict() for e in self.errors],
            "publishable": self.publishable,
        }
        if self.component_statuses:
            d["component_statuses"] = [
                c.to_dict() for c in self.component_statuses
            ]
        if self.change_details:
            d["change_details"] = self.change_details
        if self.context_location:
            d["context_location"] = self.context_location
        if self.analysis:
            d["analysis"] = self.analysis
        if self.version:
            d["version"] = self.version
        if self.summary is not None:
            d["summary"] = self.summary.to_record()
        return d


@dataclass
class RevertResult:
    """Outcome of moving the active snapshot (one step or to a named version)."""

    project: str
    status: str
    message: str
    from_version: int | None = None
    to_version: int | None = None
    reverted_at: str = ""
    vectors_deleted: int = 0
    vectors_upserted: int = 0
    errors: list[ErrorDetail] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logging or API responses."""
        d: dict[str, Any] = {
            "project": self.project,
            "status": self.status,
            "message": self.message,
            "reverted_at": self.reverted_at,
            "vectors_deleted": self.vectors_deleted,
            "vectors_upserted": self.vectors_upserted,
            "errors": [e.to_dict() for e in self.errors],
        }
        if self.from_version is not None:
            d["from_version"] = self.from_version
        if self.to_version is not None:
            d["to_version"] = self.to_version
        return d

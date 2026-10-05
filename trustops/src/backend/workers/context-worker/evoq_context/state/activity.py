"""Append structured entries to activity.jsonl."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import (
    ComponentStatus,
    ContextBuildRequest,
    ErrorDetail,
)

logger = logging.getLogger(__name__)


class BuildActivityTracker(Protocol):
    """Subset of pipeline tracker used for activity logging."""

    components: list[ComponentStatus]
    errors: list[ErrorDetail]

    @property
    def overall_status(self) -> str:
        """``"success"``/``"partial_success"``/``"error"`` for the run so far."""
        ...


def append_build_activity(
    config: ContextConfig,
    request: ContextBuildRequest,
    tracker: BuildActivityTracker,
    stats: dict[str, int | str],
    version: str,
) -> None:
    """Append one JSON line for a completed build run."""
    entry: dict[str, Any] = {
        "run_id": request.run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "action": "build",
        "status": tracker.overall_status,
        "goal_prompt": request.goal_prompt,
        "stats": stats,
        "component_statuses": {
            c.name: c.status for c in tracker.components
        },
        "errors": [e.to_dict() for e in tracker.errors],
        "duration_ms": stats["duration_ms"],
    }
    if version:
        entry["version"] = version
    append_raw_activity(config, request.project, entry)


def append_raw_activity(
    config: ContextConfig,
    project: str,
    entry: dict[str, Any],
) -> None:
    """Append one JSON line to the project's activity log."""
    try:
        path = config.resolve_activity_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        logger.exception("Failed to append activity log")

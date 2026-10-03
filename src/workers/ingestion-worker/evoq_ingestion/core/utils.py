"""Shared helpers for ingestion (no source-specific business logic).

Scope & functionality
---------------------
A UTC timestamp helper and a small nested-dict walker. The Atlassian
Basic-Auth / REST header helpers, the Jira field helpers and the ADF
(Atlassian Document Format) tree cleaner were pruned on clone with the
Jira and Confluence sources (``UPSTREAM.md``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """Current UTC time as ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def get_nested(
    data: dict[str, Any], *keys: str, default: Any = None,  # noqa: ANN401 - value is genuinely arbitrary
) -> Any:  # noqa: ANN401 - generic nested-dict walker, the value is genuinely arbitrary
    """Walk nested dicts; return default if any key is missing."""
    cur: Any = data
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

"""Abstract ingestion source — one external source (uploads in TrustOps)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IngestionSource(ABC):
    """One external source (uploads; Jira/Confluence were pruned on clone).

    ``fetch()`` returns a normalized dict section for the
    ``IngestionResult`` envelope (kept for back-compat / diagnostics) plus,
    on ``documents``, the ``CanonicalDocument`` list the store consumes.
    """

    name: str

    @abstractmethod
    async def fetch(self) -> dict[str, Any]:
        """Return a normalized dict section for the output envelope."""

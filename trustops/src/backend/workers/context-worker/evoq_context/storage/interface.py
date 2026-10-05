"""Abstract interface for content stores."""

from __future__ import annotations

from abc import ABC, abstractmethod

from evoq_context.core.models import ContextItem


class ContentStore(ABC):
    """Store and retrieve canonical context items."""

    @abstractmethod
    def add(self, item: ContextItem) -> None:
        """Add or overwrite an item (upsert by id)."""

    @abstractmethod
    def list_items(self, project: str) -> list[ContextItem]:
        """List all items for a project."""

    @abstractmethod
    def delete(self, doc_id: str, project: str) -> bool:
        """Delete an item by id. Return True if it existed."""

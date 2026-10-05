"""Canonical document persistence."""

from evoq_context.storage.interface import ContentStore
from evoq_context.storage.local import LocalContentStore

__all__ = ["ContentStore", "LocalContentStore"]

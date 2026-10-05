"""Exception hierarchy of the ``evoq_context`` engine.

Scope & functionality
---------------------
Every error the engine raises on purpose derives from ``ContextError`` so a
host can catch the whole family in one clause while still telling a
configuration mistake (``ConfigError``) from corrupt on-disk state
(``SnapshotCorrupt``/``ManifestCorrupt``/``StateCorrupt``), a per-document
ceiling (``ChunkLimitExceeded``), an Index Contract breach
(``MetadataContractViolation``/``MetadataTooLarge``) or a stage failure
with a named code (``StageError``). Contract violations also subclass
``ValueError`` so pre-existing ``except ValueError`` call sites keep
working.
"""

from __future__ import annotations


class ContextError(Exception):
    """Base class for every error raised deliberately by the engine."""


class ConfigError(ContextError, ValueError):
    """A ``ContextConfig`` (or a piece of it) is invalid — caught at startup."""


class TokenizerUnavailable(ContextError, RuntimeError):
    """The configured tokenizer cannot be loaded and no fallback is allowed."""


class ChunkLimitExceeded(ContextError):
    """A document would need more records than ``chunk_key`` can address."""


class MetadataContractViolation(ContextError, ValueError):
    """A metadata dict does not conform to the Index Contract shape."""


class MetadataTooLarge(ContextError, ValueError):
    """Metadata still exceeds the backend's byte cap after every trim."""


class SnapshotCorrupt(ContextError):
    """A ``chunks/*.jsonl`` line (or file) in a snapshot cannot be parsed."""


class ManifestCorrupt(ContextError):
    """``vector_manifest.json`` / ``index_manifest.json`` exists but is unreadable."""


class StateCorrupt(ContextError):
    """``state.json`` exists but cannot be parsed or has the wrong shape."""


class StageError(ContextError):
    """A pipeline stage failed with a named, machine-readable code."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code

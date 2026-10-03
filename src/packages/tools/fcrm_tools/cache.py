"""The retrieval cache for re-runs (design B §3.5).

On a clarification re-run, a builder re-queries only the retrieval-plan
entries whose query depends on a changed field and reuses the rest; this
cache is what "reuses" means. Keys are ``(tool_name, input_hash)`` where
``input_hash`` is the sha256 of the request's canonical JSON, so two
requests that differ only in key order share an entry and any real
difference (a changed query, another pinned version in the filters) misses.

TTLs (design B §3.5): 24 h for policy, precedent, case-search, control and
field-set lookups; **no expiry** for ``document_reader`` (a document id's
pages are immutable); mutable or cheap reads (``case_record``,
``evidence_ledger``, ``audit_log``, ``rules_check``, ``submission_diff``,
``evidence_store``) are not cached at all.

Only an in-memory implementation lives here (no Redis dependency); the
:class:`Cache` protocol is what a shared backend would implement.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Final, Optional, Protocol

from pydantic import BaseModel

from fcrm_tools.base import Tool, ToolRegistry

logger = logging.getLogger(__name__)

CacheKey = tuple[str, str]
"""``(tool_name, input_hash)``."""

DAY_S: Final[float] = 86_400.0

RETRIEVAL_CACHE_TTL_S: Final[Mapping[str, Optional[float]]] = {
    "policy_search": DAY_S,
    "precedent_search": DAY_S,
    "case_search": DAY_S,
    "control_inventory": DAY_S,
    "field_rules": DAY_S,
    "jurisdiction_list": DAY_S,
    "document_reader": None,
}
"""Cached tools and their TTL in seconds (``None`` = never expires). A tool
absent from this mapping is never cached."""


def input_hash(request: BaseModel) -> str:
    """sha256 of the request's canonical JSON (sorted keys, no whitespace)."""
    canonical = json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_key(tool_name: str, request: BaseModel) -> CacheKey:
    """The cache key of one tool call."""
    return tool_name, input_hash(request)


class Cache(Protocol):
    """A key-value store for tool outputs."""

    def get(self, key: CacheKey) -> Optional[BaseModel]:
        """The cached value, or ``None`` when absent or expired."""
        ...

    def set(self, key: CacheKey, value: BaseModel, ttl_s: Optional[float]) -> None:
        """Store ``value``; ``ttl_s=None`` means it never expires."""
        ...


class InMemoryTTLCache:
    """A bounded, per-process TTL cache. Values are deep-copied in and out
    so a caller mutating a returned model cannot corrupt the cache."""

    def __init__(
        self,
        *,
        max_entries: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Hold at most ``max_entries`` (oldest evicted first)."""
        if max_entries < 1:
            msg = f"max_entries must be >= 1, got {max_entries}"
            raise ValueError(msg)
        self._max = max_entries
        self._clock = clock
        self._entries: OrderedDict[CacheKey, tuple[Optional[float], BaseModel]] = (
            OrderedDict()
        )
        self.hits = 0
        self.misses = 0

    def get(self, key: CacheKey) -> Optional[BaseModel]:
        """The cached value, or ``None`` (counted as a miss)."""
        entry = self._entries.get(key)
        if entry is not None:
            expires_at, value = entry
            if expires_at is None or self._clock() < expires_at:
                self.hits += 1
                return value.model_copy(deep=True)
            del self._entries[key]
        self.misses += 1
        return None

    def set(self, key: CacheKey, value: BaseModel, ttl_s: Optional[float]) -> None:
        """Store ``value`` under ``key`` for ``ttl_s`` seconds."""
        expires_at = None if ttl_s is None else self._clock() + ttl_s
        self._entries[key] = (expires_at, value.model_copy(deep=True))
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        """Number of stored entries (expired ones included until touched)."""
        return len(self._entries)


class CachedTool:
    """A :class:`~fcrm_tools.base.Tool` wrapper that serves repeat calls
    from a :class:`Cache`."""

    def __init__(self, inner: Tool, cache: Cache, ttl_s: Optional[float]) -> None:
        """Wrap ``inner``; entries live ``ttl_s`` seconds (``None``: forever)."""
        self._inner = inner
        self._cache = cache
        self._ttl_s = ttl_s

    @property
    def name(self) -> str:
        """The wrapped tool's name."""
        return self._inner.name

    @property
    def permit_key(self) -> str:
        """The wrapped tool's permit key."""
        return self._inner.permit_key

    @property
    def input_model(self) -> type[BaseModel]:
        """The wrapped tool's input model."""
        return self._inner.input_model

    @property
    def output_model(self) -> type[BaseModel]:
        """The wrapped tool's output model."""
        return self._inner.output_model

    async def call(self, input: BaseModel) -> BaseModel:
        """Serve from the cache, or call through and store the result."""
        key = cache_key(self.name, input)
        cached = self._cache.get(key)
        if cached is not None:
            logger.debug("retrieval cache hit", extra={"tool": self.name})
            return cached
        result = await self._inner.call(input)
        self._cache.set(key, result, self._ttl_s)
        return result


def with_retrieval_cache(
    registry: ToolRegistry,
    cache: Cache,
    ttls: Mapping[str, Optional[float]] = RETRIEVAL_CACHE_TTL_S,
) -> ToolRegistry:
    """A new registry in which every tool named in ``ttls`` is wrapped in a
    :class:`CachedTool`; the others are shared unwrapped."""
    wrapped = ToolRegistry()
    for tool in registry:
        if tool.name in ttls:
            wrapped.register(CachedTool(tool, cache, ttls[tool.name]))
        else:
            wrapped.register(tool)
    return wrapped

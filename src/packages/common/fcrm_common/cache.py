"""A minimal async TTL cache interface for authorization decisions and
identity-validation results (design A §8.3: OPA decisions cached 30 s,
``/core/validate`` results 60 s, in Redis).

Only :class:`InMemoryTTLCache` exists today (a Redis-backed
implementation was removed as dead code: nothing constructed it and no
Redis client was ever a dependency; add one behind :class:`TTLCache` when
a multi-replica deployment needs a shared cache). A cache failure is never an
authorization failure: callers treat a cache error as a miss.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class TTLCache(Protocol):
    """String-to-string cache with per-entry expiry."""

    async def get(self, key: str) -> Optional[str]:
        """The cached value, or ``None`` when absent or expired."""
        ...

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        """Store ``value`` for ``ttl_s`` seconds."""
        ...


@dataclass
class _Entry:
    value: str
    expires_at: float


class InMemoryTTLCache:
    """Process-local :class:`TTLCache` with a size bound (oldest entry
    evicted first when full).
    """

    def __init__(self, max_entries: int = 10_000) -> None:
        self._entries: dict[str, _Entry] = {}
        self._max_entries = max_entries

    async def get(self, key: str) -> Optional[str]:
        """The live value for ``key``; expired entries are dropped."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if monotonic() >= entry.expires_at:
            self._entries.pop(key, None)
            return None
        return entry.value

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        """Store ``value``; evicts the oldest insertion when full."""
        if key not in self._entries and len(self._entries) >= self._max_entries:
            oldest = next(iter(self._entries))
            self._entries.pop(oldest, None)
        self._entries[key] = _Entry(value, monotonic() + ttl_s)


async def cache_get(cache: Optional[TTLCache], key: str) -> Optional[str]:
    """``cache.get(key)``, treating a missing cache or any cache error as a
    miss (logged): Redis being down must not change an authorization
    answer, only make it slower.
    """
    if cache is None:
        return None
    try:
        return await cache.get(key)
    except Exception:  # noqa: BLE001 - any client error is a cache miss
        logger.warning("cache get failed; treating as a miss", exc_info=True)
        return None


async def cache_set(
    cache: Optional[TTLCache], key: str, value: str, ttl_s: int
) -> None:
    """``cache.set(...)``, logging and ignoring cache errors."""
    if cache is None:
        return
    try:
        await cache.set(key, value, ttl_s)
    except Exception:  # noqa: BLE001 - a failed cache write only costs a re-query
        logger.warning("cache set failed; continuing uncached", exc_info=True)

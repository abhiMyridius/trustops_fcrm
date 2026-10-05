"""Who the analysts are, and how many open cases each holds.

S1 stores user ids on a case; names come from X1 (``GET /auth/users?role=
fcrm_analyst``) through a :class:`~fcrm_common.http.ServiceClient` carrying the
service token, and are cached: the roster changes rarely and every ``assign``
and every R5 load read needs it.

:class:`AnalystLoadService` is H1's R5 input (``GET /internal/analysts/load``):
the roster, each analyst with their open-case count from ONE ``$group``
aggregation (:meth:`fcrm_case.store.CaseStore.open_case_counts`), the whole
answer cached for :data:`LOAD_TTL_S` seconds. Assigning a case drops the cache,
so two cases triaged back to back are not both handed to the analyst who *was*
the least loaded.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from time import monotonic
from typing import Any, Optional, Protocol

from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.case_internal import AnalystLoad, AnalystLoadResponse
from fcrm_contracts.api.identity import RosterUser
from fcrm_contracts.enums import Role
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from fcrm_case.store import CaseStore

logger = logging.getLogger(__name__)

#: How long the roster is trusted, and how long a load answer is.
ROSTER_TTL_S = 60.0
LOAD_TTL_S = 10.0

_ROSTER = TypeAdapter(list[RosterUser])
Clock = Callable[[], float]


class IdentityDirectory(Protocol):
    """What S1 asks X1 for."""

    async def analysts(self) -> list[RosterUser]:
        """The enabled people holding ``fcrm_analyst``."""
        ...

    async def analyst_name(self, user_id: str) -> Optional[str]:
        """The display name of analyst ``user_id``, or ``None`` if there is no
        such analyst.
        """
        ...


class HttpIdentityDirectory:
    """The directory over X1's HTTP roster, cached for ``ttl_s``.

    Args:
        client: A client whose base URL is X1's prefix (``IDENTITY_SERVICE_URL``).
        ttl_s: How long a roster is reused.
        clock: Monotonic seconds (injected for tests).
    """

    def __init__(
        self,
        client: ServiceClient,
        *,
        ttl_s: float = ROSTER_TTL_S,
        clock: Clock = monotonic,
    ) -> None:
        self._client = client
        self._ttl_s = ttl_s
        self._clock = clock
        self._roster: list[RosterUser] = []
        self._fetched_at: Optional[float] = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        """Close the client's connection pool."""
        await self._client.aclose()

    def _fresh(self) -> bool:
        return (
            self._fetched_at is not None
            and self._clock() - self._fetched_at < self._ttl_s
        )

    async def analysts(self) -> list[RosterUser]:
        """The roster, from cache or one ``GET /users?role=fcrm_analyst``.

        Raises:
            fcrm_common.errors.UpstreamUnavailableError: X1 is unreachable,
                refused our service token, or answered something that is not a
                roster.
        """
        if self._fresh():
            return list(self._roster)
        async with self._lock:
            if not self._fresh():
                self._roster = await self._fetch()
                self._fetched_at = self._clock()
            return list(self._roster)

    async def _fetch(self) -> list[RosterUser]:
        body: Any = await self._client.request_json(
            "GET", "/users", params={"role": Role.ANALYST.value}
        )
        try:
            return _ROSTER.validate_python(body)
        except PydanticValidationError as exc:
            logger.error(
                "identity roster has an unexpected shape",
                extra={"dependency": "identity", "errors": exc.error_count()},
            )
            raise UpstreamUnavailableError(
                "Identity answered with an unreadable roster.", dependency="identity"
            ) from exc

    async def analyst_name(self, user_id: str) -> Optional[str]:
        for user in await self.analysts():
            if user.id == user_id:
                return user.name
        return None


class StaticIdentityDirectory:
    """A fixed roster (tests, and any host that has no X1)."""

    def __init__(self, analysts: Sequence[RosterUser]) -> None:
        self._analysts = list(analysts)

    async def analysts(self) -> list[RosterUser]:
        return list(self._analysts)

    async def analyst_name(self, user_id: str) -> Optional[str]:
        for user in self._analysts:
            if user.id == user_id:
                return user.name
        return None


class UnconfiguredIdentityDirectory:
    """The directory of a service that was never told where X1 is: every read
    fails closed with a 503 rather than answering from nothing.
    """

    async def analysts(self) -> list[RosterUser]:
        raise UpstreamUnavailableError(
            "No identity service URL is configured.", dependency="identity"
        )

    async def analyst_name(self, user_id: str) -> Optional[str]:
        raise UpstreamUnavailableError(
            "No identity service URL is configured.", dependency="identity"
        )


class AnalystLoadService:
    """The R5 load read, cached for ``ttl_s`` and dropped on every assignment."""

    def __init__(
        self,
        store: CaseStore,
        directory: IdentityDirectory,
        *,
        ttl_s: float = LOAD_TTL_S,
        clock: Clock = monotonic,
    ) -> None:
        self._store = store
        self._directory = directory
        self._ttl_s = ttl_s
        self._clock = clock
        self._cached: Optional[AnalystLoadResponse] = None
        self._cached_at = 0.0

    def invalidate(self) -> None:
        """Forget the cached answer (a case was just assigned)."""
        self._cached = None

    async def loads(self) -> AnalystLoadResponse:
        """Every analyst on the roster with their open-case count, fewest
        first (ties by id). One roster read and one aggregation when the cache
        is cold; none when it is warm.
        """
        cached = self._cached
        if cached is not None and self._clock() - self._cached_at < self._ttl_s:
            return cached
        roster, counts = await asyncio.gather(
            self._directory.analysts(), self._store.open_case_counts()
        )
        rows = sorted(
            (
                AnalystLoad(analyst_id=u.id, open_case_count=counts.get(u.id, 0))
                for u in roster
            ),
            key=lambda load: (load.open_case_count, load.analyst_id),
        )
        self._cached = AnalystLoadResponse(loads=rows)
        self._cached_at = self._clock()
        return self._cached

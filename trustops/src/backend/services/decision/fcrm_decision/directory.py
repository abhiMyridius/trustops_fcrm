"""Who sits on the committee.

S5 does not own people: X1 does. The roster (``GET /users?role=fcrm_committee``
through a :class:`~fcrm_common.http.ServiceClient` carrying the service token,
every request on a timeout) says who a pack is released to, and the names the
votes screen shows next to each seat. It changes rarely, so it is cached for
:data:`ROSTER_TTL_S` seconds; a vote never waits on it.

With no X1 URL configured the directory refuses on use (a 503), it does not
invent three members.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from time import monotonic
from typing import Any, Optional, Protocol

from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.identity import RosterUser
from fcrm_contracts.enums import Role
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

logger = logging.getLogger(__name__)

#: How long a roster is trusted.
ROSTER_TTL_S = 60.0

_ROSTER = TypeAdapter(list[RosterUser])
Clock = Callable[[], float]


class CommitteeDirectory(Protocol):
    """What S5 asks X1 for."""

    async def members(self) -> list[RosterUser]:
        """The enabled people holding ``fcrm_committee``, in a stable order."""
        ...


class HttpCommitteeDirectory:
    """The roster over X1's HTTP API, cached for ``ttl_s``.

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

    async def members(self) -> list[RosterUser]:
        """The roster, from cache or one ``GET /users?role=fcrm_committee``.

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
            "GET", "/users", params={"role": Role.COMMITTEE.value}
        )
        try:
            roster = _ROSTER.validate_python(body)
        except PydanticValidationError as exc:
            logger.error(
                "identity roster has an unexpected shape",
                extra={"dependency": "identity", "errors": exc.error_count()},
            )
            raise UpstreamUnavailableError(
                "Identity answered with an unreadable roster.", dependency="identity"
            ) from exc
        return sorted(roster, key=lambda user: user.id)


class StaticCommitteeDirectory:
    """A fixed roster (tests, and any host that has no X1)."""

    def __init__(self, members: Sequence[RosterUser]) -> None:
        self._members = sorted(members, key=lambda user: user.id)

    async def members(self) -> list[RosterUser]:
        return list(self._members)


class UnconfiguredCommitteeDirectory:
    """The directory of a service that was never told where X1 is: every read
    fails closed with a 503 rather than answering from nothing.
    """

    async def members(self) -> list[RosterUser]:
        raise UpstreamUnavailableError(
            "No identity service URL is configured.", dependency="identity"
        )

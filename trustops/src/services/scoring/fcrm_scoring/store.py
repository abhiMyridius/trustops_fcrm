"""The active-configuration store S2's routes read from.

A small Protocol, not a concrete Mongo dependency, so the route layer
(``routes.py``) and its unit tests never need a database: they take
whatever implements :class:`ConfigStore`, and the real app wires
:class:`MongoConfigStore` at startup while tests wire
:class:`InMemoryConfigStore`. This is the same shape ``fcrm_guardrails``
used for its ``OPAClient`` Protocol, applied here for the same reason —
keep the thing every test needs trivial to fake, and the thing production
needs real.
"""

from __future__ import annotations

from typing import Optional, Protocol

from fcrm_common.errors import NotFoundError
from fcrm_common.mongo import Repository
from fcrm_contracts.api.scoring import RoutingRulesVersion, ScoringModelVersion


class ConfigStore(Protocol):
    async def active_model(self) -> ScoringModelVersion: ...

    async def model(self, version: int) -> Optional[ScoringModelVersion]: ...

    async def active_routing(self) -> RoutingRulesVersion: ...


class InMemoryConfigStore:
    """Used by unit tests and by any caller that already has the active
    versions in hand (e.g. the startup recompute, before Mongo has even
    been asked).
    """

    def __init__(
        self, models: list[ScoringModelVersion], routing: list[RoutingRulesVersion]
    ) -> None:
        self._models = {m.version: m for m in models}
        self._routing = routing

    async def active_model(self) -> ScoringModelVersion:
        for model in self._models.values():
            if model.active:
                return model
        raise NotFoundError("no active scoring model version")

    async def model(self, version: int) -> Optional[ScoringModelVersion]:
        return self._models.get(version)

    async def active_routing(self) -> RoutingRulesVersion:
        for routing in self._routing:
            if routing.active:
                return routing
        raise NotFoundError("no active routing rules version")


class MongoConfigStore:
    """The real store: ``fcrm_scoring.scoring_model_versions`` and
    ``fcrm_scoring.routing_rules_versions``, each document keyed by its own
    ``version`` as ``_id`` (see ``lifespan.py`` for how the repositories
    are built and seeded).
    """

    def __init__(
        self,
        models: Repository[ScoringModelVersion],
        routing: Repository[RoutingRulesVersion],
    ) -> None:
        self._models = models
        self._routing = routing

    async def active_model(self) -> ScoringModelVersion:
        model = await self._models.find_one({"active": True})
        if model is None:
            raise NotFoundError("no active scoring model version")
        return model

    async def model(self, version: int) -> Optional[ScoringModelVersion]:
        return await self._models.find_one({"version": version})

    async def active_routing(self) -> RoutingRulesVersion:
        routing = await self._routing.find_one({"active": True})
        if routing is None:
            raise NotFoundError("no active routing rules version")
        return routing

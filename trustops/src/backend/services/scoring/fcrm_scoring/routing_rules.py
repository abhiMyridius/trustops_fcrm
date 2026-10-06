"""S2's routing rules as the web reads them (``GET scoring/routing-rules/{id}``).

The rules are stored as data in ``routing_rules_versions`` (routing v4 seeded
from P7, D-19), pinned per case as ``routing_v`` and evaluated by code in H1;
this module only reads them. A version is addressed the way a case pins it
(``v4`` or ``4``), or as ``active``/``latest``.

:class:`RoutingStore` is the read side the route depends on, with a Mongo
implementation over the repository the lifespan already builds and an
in-memory twin for unit tests. The publish side is
:mod:`fcrm_scoring.config_publish`.
"""

from __future__ import annotations

import logging
import re
from typing import Final, Optional, Protocol

from fcrm_common.errors import ValidationError
from fcrm_common.mongo import Repository
from fcrm_contracts.api.policy_config import RoutingRuleRow
from fcrm_contracts.api.scoring import RoutingRulesVersion
from fcrm_contracts.api.scoring_routing import RoutingRulesOut

logger = logging.getLogger(__name__)

#: Version spellings that mean "the active one".
ACTIVE_ALIASES: Final = frozenset({"active", "latest", "current"})
_VERSION: Final = re.compile(r"^v?(\d+)$", re.IGNORECASE)


def parse_version(raw: str) -> Optional[int]:
    """``v4`` / ``4`` as ``4``; ``active`` (or ``latest``) as ``None``.

    Raises:
        ValidationError: ``raw`` is neither a version nor an alias.
    """
    text = raw.strip()
    if text.lower() in ACTIVE_ALIASES:
        return None
    match = _VERSION.match(text)
    if match is None:
        raise ValidationError(
            f"{raw!r} is not a routing rules version like v4.",
            errors=[{"loc": "path.version", "msg": "expected v<number> or active"}],
        )
    return int(match.group(1))


def routing_out(routing: RoutingRulesVersion) -> RoutingRulesOut:
    """A stored routing rules version as the web lists it: ``{id, rule}`` rows
    (the rule's text is its description) under the label cases pin.
    """
    return RoutingRulesOut(
        version=f"v{routing.version}",
        rules=[RoutingRuleRow(id=r.rule_id, rule=r.description) for r in routing.rules],
        parameters=dict(routing.parameters),
        committee_quorum=routing.committee_quorum,
        active=routing.active,
    )


class RoutingStore(Protocol):
    """What the routing-rules route reads."""

    async def routing_rules(
        self, version: Optional[int]
    ) -> Optional[RoutingRulesVersion]:
        """One version, or the active one for ``None``; ``None`` when absent."""
        ...


class InMemoryRoutingStore:
    """Used by unit tests."""

    def __init__(self, versions: list[RoutingRulesVersion]) -> None:
        """Read ``versions`` as it stands (the list is held, not copied, so a
        test's publish store and this read see the same versions).
        """
        self._versions = versions

    async def routing_rules(
        self, version: Optional[int]
    ) -> Optional[RoutingRulesVersion]:
        """The version asked for, or the active one."""
        for routing in self._versions:
            if routing.active if version is None else routing.version == version:
                return routing
        return None


class MongoRoutingStore:
    """``routing_rules_versions`` through the repository the lifespan built."""

    def __init__(self, repo: Repository[RoutingRulesVersion]) -> None:
        self._repo = repo

    async def routing_rules(
        self, version: Optional[int]
    ) -> Optional[RoutingRulesVersion]:
        """The version asked for, or the active one."""
        flt = {"active": True} if version is None else {"version": version}
        return await self._repo.find_one(flt)

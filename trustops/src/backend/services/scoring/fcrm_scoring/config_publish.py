"""Putting a published scoring model or routing rules version into force.

The configuration screens (S3) hold the draft and the history; S2 owns the
version a case is scored against and pins. S3 calls the two service-only routes
of :mod:`fcrm_scoring.routes_routing` with the model or rules it built, and this
module is what they do:

* ``base_version`` guards the call: it must be the version S2 has active, or
  the answer is a 409, so two publishes cannot both build on the same base;
* the new version must be ``base_version + 1`` and valid (the checks of design
  A §5.2: weights that sum to 1, contiguous bands that cover 0 to 25, a
  retention floor between 0.3 and 0.6; routing rules R1 to R6 with a quorum
  from 1 to 9);
* one transaction deactivates the old version and inserts the new one active (a
  partial unique index keeps at most one active), and an existing version is
  never changed;
* a repeat of the same publish (the version exists with the same content) is
  answered with that version and writes nothing, which is what lets S3 retry
  after a failed commit.

Who published and why is S3's change record; S2's version document is exactly
the configuration a case pins.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Optional, Protocol

from fcrm_common.errors import ConflictError, ValidationError
from fcrm_common.mongo import Repository, with_transaction
from fcrm_contracts.api.scoring import RoutingRulesVersion, ScoringModelVersion
from fcrm_contracts.enums import Domain
from pydantic import BaseModel
from pymongo import AsyncMongoClient, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession

logger = logging.getLogger(__name__)

WEIGHT_TOLERANCE: Final = 0.001
FLOOR_RANGE: Final = (0.3, 0.6)
BAND_STEP: Final = 0.1
MAX_COMPOSITE: Final = 25.0
RULE_IDS: Final = ("R1", "R2", "R3", "R4", "R5", "R6")
QUORUM_RANGE: Final = (1, 9)


def _problems(problems: list[tuple[str, str]], what: str) -> None:
    """Raise one 422 naming every ``(field, message)`` in ``problems``."""
    if problems:
        raise ValidationError(
            f"{what} is not valid: " + "; ".join(m for _, m in problems),
            errors=[{"loc": loc, "msg": msg} for loc, msg in problems],
        )


def validate_model(model: ScoringModelVersion) -> None:
    """The checks of design A §5.2 on a scoring model before it is published.

    Raises:
        ValidationError: weights do not sum to 1, a domain is missing, the
            bands are not contiguous over 0 to 25, the retention floor or
            factor is out of range, or planned controls are scored.
    """
    problems: list[tuple[str, str]] = []
    weights = model.domain_weights
    total = sum(weights.values())
    if set(weights) != set(Domain):
        problems.append(("domain_weights", "all four domains need a weight"))
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        problems.append(("domain_weights", f"weights sum to {total:.3f}, not 1.000"))
    if any(w <= 0 for w in weights.values()):
        problems.append(("domain_weights", "every weight must be above zero"))
    low, high = FLOOR_RANGE
    if not low <= model.retention_floor_pct <= high:
        problems.append(
            ("retention_floor_pct", f"the floor must be between {low} and {high}")
        )
    if not 0 < model.retention_factor_pct <= 1:
        problems.append(("retention_factor_pct", "the factor must be in (0, 1]"))
    if model.boundary_flag_distance < 0:
        problems.append(("boundary_flag_distance", "the distance cannot be negative"))
    if model.planned_controls_scored:
        problems.append(
            ("planned_controls_scored", "planned controls are never scored")
        )
    problems.extend(_band_problems(model))
    _problems(problems, f"scoring model v{model.version}")


def _band_problems(model: ScoringModelVersion) -> list[tuple[str, str]]:
    bands = sorted(model.bands, key=lambda b: b.band.value)
    if not bands:
        return [("bands", "at least one band is needed")]
    problems: list[tuple[str, str]] = []
    if bands[0].lower != 0:
        problems.append(("bands", "the first band must start at 0"))
    if bands[-1].upper != MAX_COMPOSITE:
        problems.append(("bands", f"the last band must end at {MAX_COMPOSITE:g}"))
    for previous, band in zip(bands, bands[1:], strict=False):
        if abs(band.lower - previous.upper - BAND_STEP) > WEIGHT_TOLERANCE:
            problems.append(
                (
                    "bands",
                    f"band {band.band.value} must start 0.1 above band "
                    f"{previous.band.value}'s upper edge",
                )
            )
    return problems


def validate_routing(routing: RoutingRulesVersion) -> None:
    """Routing rules R1 to R6, each with text, and a quorum from 1 to 9.

    Raises:
        ValidationError: a rule is missing, repeated or blank, or the quorum is
            out of range.
    """
    problems: list[tuple[str, str]] = []
    ids = [r.rule_id for r in routing.rules]
    if sorted(ids) != sorted(RULE_IDS):
        problems.append(("rules", f"expected exactly {', '.join(RULE_IDS)}, got {ids}"))
    if any(not r.description.strip() for r in routing.rules):
        problems.append(("rules", "every rule needs its text"))
    low, high = QUORUM_RANGE
    if not low <= routing.committee_quorum <= high:
        problems.append(("committee_quorum", f"the quorum must be {low} to {high}"))
    _problems(problems, f"routing rules v{routing.version}")


def same_content(a: BaseModel, b: BaseModel) -> bool:
    """Whether two versions say the same, ignoring whether they are active."""
    return a.model_dump(exclude={"active"}) == b.model_dump(exclude={"active"})


def check_base[V: (ScoringModelVersion, RoutingRulesVersion)](
    active: Optional[V], base_version: int, new: V, what: str
) -> None:
    """Refuse a publish that does not build on the active version.

    Raises:
        ConflictError: ``base_version`` is not the active version.
        ValidationError: ``new`` is not ``base_version + 1``.
    """
    if active is None or active.version != base_version:
        now = f"v{active.version}" if active is not None else "none"
        raise ConflictError(
            f"The active {what} is {now}, not v{base_version}: another publish "
            "moved it. Reload and publish again."
        )
    if new.version != base_version + 1:
        raise ValidationError(
            f"A publish after v{base_version} must be v{base_version + 1}.",
            errors=[{"loc": "version", "msg": f"expected {base_version + 1}"}],
        )


class PublishStore(Protocol):
    """What the two internal publish routes call."""

    async def publish_model(
        self, base_version: int, model: ScoringModelVersion
    ) -> tuple[int, bool]:
        """Make ``model`` the active scoring model; returns ``(version,
        created)``, ``created`` being ``False`` for a repeat of the same publish.
        """
        ...

    async def publish_routing(
        self, base_version: int, routing: RoutingRulesVersion
    ) -> tuple[int, bool]:
        """Make ``routing`` the active routing rules; returns ``(version,
        created)``.
        """
        ...


class InMemoryPublishStore:
    """Used by unit tests: the same decisions over two lists."""

    def __init__(
        self,
        models: list[ScoringModelVersion],
        routing: list[RoutingRulesVersion],
    ) -> None:
        self.models = models
        self.routing = routing

    async def publish_model(
        self, base_version: int, model: ScoringModelVersion
    ) -> tuple[int, bool]:
        """Publish like the Mongo store."""
        return _publish_list(self.models, base_version, model, "scoring model")

    async def publish_routing(
        self, base_version: int, routing: RoutingRulesVersion
    ) -> tuple[int, bool]:
        """Publish like the Mongo store."""
        return _publish_list(self.routing, base_version, routing, "routing rules")


def _validate(new: BaseModel) -> None:
    if isinstance(new, ScoringModelVersion):
        validate_model(new)
    elif isinstance(new, RoutingRulesVersion):
        validate_routing(new)


def _publish_list[V: (ScoringModelVersion, RoutingRulesVersion)](
    items: list[V], base_version: int, new: V, what: str
) -> tuple[int, bool]:
    for existing in items:
        if existing.version == new.version and same_content(existing, new):
            return new.version, False
    _validate(new)
    active = next((i for i in items if i.active), None)
    check_base(active, base_version, new, what)
    if any(i.version == new.version for i in items):
        raise ConflictError(f"{what} v{new.version} exists with different content.")
    for index, item in enumerate(items):
        if item.active:
            items[index] = item.model_copy(update={"active": False})
    items.append(new.model_copy(update={"active": True}))
    return new.version, True


class MongoPublishStore:
    """The real store over the two version collections (the repositories the
    lifespan builds), one transaction per publish.
    """

    def __init__(
        self,
        client: AsyncMongoClient[Any],
        models: Repository[ScoringModelVersion],
        routing: Repository[RoutingRulesVersion],
    ) -> None:
        self._client = client
        self._models = models
        self._routing = routing

    async def publish_model(
        self, base_version: int, model: ScoringModelVersion
    ) -> tuple[int, bool]:
        """Publish ``model`` in one transaction."""
        return await self._publish(self._models, base_version, model, "scoring model")

    async def publish_routing(
        self, base_version: int, routing: RoutingRulesVersion
    ) -> tuple[int, bool]:
        """Publish ``routing`` in one transaction."""
        return await self._publish(
            self._routing, base_version, routing, "routing rules"
        )

    async def _publish[V: (ScoringModelVersion, RoutingRulesVersion)](
        self, repo: Repository[V], base_version: int, new: V, what: str
    ) -> tuple[int, bool]:
        async def txn(session: AsyncClientSession) -> tuple[int, bool]:
            existing = await repo.find_one({"version": new.version}, session=session)
            if existing is not None and same_content(existing, new):
                return new.version, False
            _validate(new)
            active = await repo.find_one({"active": True}, session=session)
            check_base(active, base_version, new, what)
            if existing is not None:
                raise ConflictError(
                    f"{what} v{new.version} exists with different content."
                )
            await repo.update_one(
                {"active": True}, {"$set": {"active": False}}, session=session
            )
            await repo.insert_one(
                new.model_copy(update={"active": True}), session=session
            )
            return new.version, True

        version, created = await with_transaction(self._client, txn)
        logger.info(
            "version published",
            extra={"what": what, "config_version": version, "newly_written": created},
        )
        return version, created


async def ensure_publish_indexes(
    models: Repository[ScoringModelVersion], routing: Repository[RoutingRulesVersion]
) -> None:
    """At most one active version in each collection: a partial unique index,
    so a publish that races another cannot leave two scoring models in force.
    """
    guard = IndexModel(
        [("active", 1)],
        unique=True,
        partialFilterExpression={"active": True},
        name="single_active",
    )
    await models.ensure_indexes([guard])
    await routing.ensure_indexes([guard])

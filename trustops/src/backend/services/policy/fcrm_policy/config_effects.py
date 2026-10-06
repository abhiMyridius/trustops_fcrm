"""What publishing a section puts into force beyond its snapshot.

The snapshot log is the history the configuration screens read; the version a
case actually pins and is scored against lives in the service that owns it:

========================  ==================================================
``field-sets``            S3 ``field_set_versions``: a new active document
``control-inventory``     S3 ``control_inventory_versions``: a new active one
``jurisdiction-risk``     S3's own activation (:meth:`~fcrm_policy.reference.
                          MongoReferenceStore.activate_jurisdiction_in`): the
                          stored list becomes active and
                          ``jurisdiction.list_published`` goes out through the
                          outbox, in the publish transaction
``scoring-model``         S2 ``scoring_model_versions`` through
``routing-rules``         ``POST /internal/models`` and ``/internal/routing-
                          rules`` (:class:`HttpScoringPublisher`)
``categories``            the snapshot alone: no service reads the table yet
========================  ==================================================

The first three are :class:`~fcrm_policy.config_store.PublishEffect` objects
that run inside the transaction. The S2 call cannot (it is HTTP), so the
service makes it *before* the transaction, with ``base_version`` as the guard
and the version number as the idempotency key: S2 answers a repeat of the
same publish with the version it already wrote, so a retry after a failed
commit converges instead of writing a second version.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from typing import Any, Final, Optional, Protocol

from fcrm_common.errors import ConflictError, UpstreamUnavailableError
from fcrm_common.http import ServiceClient, Timeouts
from fcrm_contracts.api.policy import JurisdictionActivateOut
from fcrm_contracts.api.scoring import (
    RoutingRule,
    RoutingRulesVersion,
    ScoringModelVersion,
)
from fcrm_contracts.api.scoring_routing import (
    PublishedVersionOut,
    RoutingRulesPublishIn,
    ScoringModelPublishIn,
)
from fcrm_scoring.seedmodel import scoring_model_from_seed
from pydantic import SecretStr, ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

logger = logging.getLogger(__name__)

#: One write to S2; a publish is rare and the analyst is waiting on it.
SCORING_TIMEOUTS: Final = Timeouts(connect=2.0, read=15.0, write=15.0, pool=5.0)
MODELS_PATH: Final = "/internal/models"
ROUTING_PATH: Final = "/internal/routing-rules"
DEFAULT_QUORUM: Final = 3


class ReferenceWriter(Protocol):
    """The writes a publish needs from the reference store (the Mongo store
    and its in-memory twin both have them).
    """

    async def add_field_set_version(
        self, session: Optional[AsyncClientSession], doc: dict[str, Any]
    ) -> None:
        """Insert ``doc`` as the active field set."""
        ...

    async def add_control_inventory_version(
        self, session: Optional[AsyncClientSession], doc: dict[str, Any]
    ) -> None:
        """Insert ``doc`` as the active control inventory."""
        ...

    async def activate_jurisdiction_in(
        self, session: Any, version: str
    ) -> Optional[JurisdictionActivateOut]:
        """Activate the stored list ``version`` and announce it, in the
        transaction ``session`` belongs to.
        """
        ...


class FieldSetEffect:
    """The published field set becomes the active ``field_set_versions`` row."""

    def __init__(self, writer: ReferenceWriter, version: str, body: Mapping[str, Any]):
        self._writer = writer
        self._doc: dict[str, Any] = {"version": version, **copy.deepcopy(dict(body))}

    async def apply(self, session: Optional[AsyncClientSession]) -> None:
        """Add the version, deactivating the one in force."""
        await self._writer.add_field_set_version(session, self._doc)


class ControlInventoryEffect:
    """The published inventory becomes the active ``control_inventory_versions``
    row.
    """

    def __init__(self, writer: ReferenceWriter, version: str, body: Mapping[str, Any]):
        self._writer = writer
        self._doc: dict[str, Any] = {"version": version, **copy.deepcopy(dict(body))}

    async def apply(self, session: Optional[AsyncClientSession]) -> None:
        """Add the version, deactivating the one in force."""
        await self._writer.add_control_inventory_version(session, self._doc)


class JurisdictionEffect:
    """Publishing the jurisdiction section activates a stored list: the same
    transaction body, and so the same ``jurisdiction.list_published`` event
    through the outbox, as S3's activate route.
    """

    def __init__(self, writer: ReferenceWriter, version: str) -> None:
        self._writer = writer
        self._version = version
        self.result: Optional[JurisdictionActivateOut] = None

    async def apply(self, session: Optional[AsyncClientSession]) -> None:
        """Activate the list.

        Raises:
            ConflictError: The list is gone, or is already the active one (the
                publish has nothing to put into force).
        """
        result = await self._writer.activate_jurisdiction_in(session, self._version)
        if result is None:
            raise ConflictError(f"No jurisdiction list {self._version!r} to activate.")
        if result.already_active:
            raise ConflictError(f"{self._version} is already the active list.")
        self.result = result
        logger.info(
            "jurisdiction list activated by a configuration publish",
            extra={
                "list_version": result.version,
                "previous": result.previous_version,
            },
        )


# ── S2: the models and routing rules it owns ────────────────────────────────


def scoring_model_for(body: Mapping[str, Any], version: str) -> ScoringModelVersion:
    """The S2 model a scoring-model snapshot body publishes as ``version``,
    built by S2's own adapter so the two cannot read the body differently.
    """
    model = scoring_model_from_seed({**body, "version": version})
    return model.model_copy(update={"active": True})


def routing_for(body: Mapping[str, Any], version: str) -> RoutingRulesVersion:
    """The S2 routing rules a routing-rules snapshot body publishes as
    ``version``: the rules as text, the parameter table, and the quorum S5
    and H1 read from it.
    """
    parameters = {p["key"]: str(p["value"]) for p in body["parameters"]}
    return RoutingRulesVersion(
        version=int(version.lstrip("v")),
        rules=[
            RoutingRule(rule_id=r["id"], label=r["id"], description=r["rule"])
            for r in body["rules"]
        ],
        committee_quorum=int(parameters.get("quorum", DEFAULT_QUORUM)),
        parameters=parameters,
        active=True,
    )


class ScoringPublisher(Protocol):
    """How S3 puts a published model or routing version into force in S2."""

    async def publish_model(
        self, *, base_version: int, published_by: str, model: ScoringModelVersion
    ) -> int:
        """Make ``model`` the active scoring model; returns its version."""
        ...

    async def publish_routing(
        self, *, base_version: int, published_by: str, routing: RoutingRulesVersion
    ) -> int:
        """Make ``routing`` the active routing rules; returns its version."""
        ...


class HttpScoringPublisher:
    """S2 over HTTP with S3's service token (the only role S2's ``/internal``
    routes answer).
    """

    def __init__(self, client: ServiceClient) -> None:
        """Wrap ``client``; whoever built it closes it."""
        self._client = client

    async def publish_model(
        self, *, base_version: int, published_by: str, model: ScoringModelVersion
    ) -> int:
        """``POST /internal/models``.

        Raises:
            ConflictError: S2's active model is not ``base_version``.
            ValidationError: S2 refused the model (weights, bands, floor).
            UpstreamUnavailableError: S2 is unreachable or answered wrongly.
        """
        body = ScoringModelPublishIn(
            base_version=base_version, published_by=published_by, model=model
        )
        return await self._post(MODELS_PATH, body.model_dump(mode="json"))

    async def publish_routing(
        self, *, base_version: int, published_by: str, routing: RoutingRulesVersion
    ) -> int:
        """``POST /internal/routing-rules``.

        Raises:
            ConflictError: S2's active routing rules are not ``base_version``.
            ValidationError: S2 refused the rules.
            UpstreamUnavailableError: S2 is unreachable or answered wrongly.
        """
        body = RoutingRulesPublishIn(
            base_version=base_version, published_by=published_by, routing=routing
        )
        return await self._post(ROUTING_PATH, body.model_dump(mode="json"))

    async def _post(self, path: str, payload: dict[str, Any]) -> int:
        answer = await self._client.request_json(
            "POST", path, json=payload, timeout=SCORING_TIMEOUTS
        )
        try:
            return PublishedVersionOut.model_validate(answer).version
        except ValidationError as exc:
            logger.error(
                "S2 answered a publish with a body that breaks its contract",
                extra={"path": path, "errors_count": exc.error_count()},
            )
            raise UpstreamUnavailableError(
                "scoring answered a publish with an unexpected body",
                dependency="scoring",
            ) from exc


class UnconfiguredScoringPublisher:
    """What S3 holds when nothing says where S2 is: a publish of a section S2
    owns is a 503 naming the missing setting, never a snapshot S2 does not
    know about.
    """

    async def publish_model(
        self, *, base_version: int, published_by: str, model: ScoringModelVersion
    ) -> int:
        """Always refuses.

        Raises:
            UpstreamUnavailableError: always.
        """
        raise _unconfigured()

    async def publish_routing(
        self, *, base_version: int, published_by: str, routing: RoutingRulesVersion
    ) -> int:
        """Always refuses.

        Raises:
            UpstreamUnavailableError: always.
        """
        raise _unconfigured()


def _unconfigured() -> UpstreamUnavailableError:
    logger.error("a publish needs SCORING_SERVICE_URL, which is not set")
    return UpstreamUnavailableError(
        "Publishing this section needs the scoring service, and "
        "SCORING_SERVICE_URL is not set.",
        dependency="scoring",
    )


def scoring_publisher_for(
    url: Optional[str], *, caller: str, secret: Optional[str | SecretStr]
) -> tuple[ScoringPublisher, Optional[ServiceClient]]:
    """The publisher S3 should use and the client to close at shutdown.

    Args:
        url: S2's base URL including its mount prefix, or ``None``.
        caller: This service's name, the subject of its service token.
        secret: ``SERVICE_TOKEN_SECRET``.
    """
    if not url:
        return UnconfiguredScoringPublisher(), None
    client = ServiceClient(
        url.rstrip("/"), dependency="scoring", caller=caller, token_secret=secret
    )
    return HttpScoringPublisher(client), client


class InMemoryScoringPublisher:
    """Used by unit tests: S2's publish rules without S2 (the base-version
    guard and an idempotent repeat), recording what it was asked.
    """

    def __init__(self, *, model_version: int = 3, routing_version: int = 4) -> None:
        self.models: dict[int, ScoringModelVersion] = {}
        self.routings: dict[int, RoutingRulesVersion] = {}
        self._model_active = model_version
        self._routing_active = routing_version
        self.fail_next: Optional[Exception] = None
        self.calls = 0

    async def publish_model(
        self, *, base_version: int, published_by: str, model: ScoringModelVersion
    ) -> int:
        """Accept the model if ``base_version`` is active (or it repeats)."""
        self._maybe_fail()
        if self.models.get(model.version) == model:
            return model.version
        if base_version != self._model_active:
            raise ConflictError(f"The active model is v{self._model_active}.")
        self.models[model.version] = model
        self._model_active = model.version
        return model.version

    async def publish_routing(
        self, *, base_version: int, published_by: str, routing: RoutingRulesVersion
    ) -> int:
        """Accept the rules if ``base_version`` is active (or it repeats)."""
        self._maybe_fail()
        if self.routings.get(routing.version) == routing:
            return routing.version
        if base_version != self._routing_active:
            raise ConflictError(
                f"The active routing rules are v{self._routing_active}."
            )
        self.routings[routing.version] = routing
        self._routing_active = routing.version
        return routing.version

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.fail_next is not None:
            failure, self.fail_next = self.fail_next, None
            raise failure

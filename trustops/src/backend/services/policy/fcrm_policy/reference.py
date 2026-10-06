"""S3's versioned reference data: jurisdiction lists, field sets and control
inventories (design A §2.3), the data behind the agent tools
``jurisdiction_list``, ``field_rules`` and (next iteration) ``control_inventory``.

Three collections, one document per published version, each with a unique
``version`` and at most one ``active`` document (a partial unique index, so
"the active version" is never ambiguous):

================================  ========================================
``jurisdiction_list_versions``    ``_id`` = ``version`` (``v2026-06``),
                                  ``published``, ``source``,
                                  ``call_for_action[]``, ``increased_monitoring[]``,
                                  ``changes{added,removed}``, ``fixture``, ``active``
``field_set_versions``            ``_id`` = ``version`` (``v6``), ``max_required``,
                                  ``completion_threshold``, ``always_required[]``,
                                  ``per_type{}``, ``active``
``control_inventory_versions``    ``_id`` = ``version`` (``v9``), ``controls[]``,
                                  ``active``
================================  ========================================

:class:`ReferenceStore` is what the routes depend on (Mongo in production,
:class:`InMemoryReferenceStore` in the unit tests, built from the same seed
documents). :func:`resolve_fields` is the one place a field set is resolved
into the fields a case must fill; it must agree with S1's
``fcrm_case.domain.fieldset.resolve_required_fields`` for every combination of
change types (a test pins that), but S3 does not import S1.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Optional, Protocol

from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.events import build_envelope
from fcrm_common.mongo import with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.api.policy import (
    ControlInventoryOut,
    ControlOut,
    ControlVersionOut,
    FieldDef,
    FieldSetOut,
    JurisdictionActivateOut,
    JurisdictionListOut,
    JurisdictionVersionOut,
    ResolvedFieldSetOut,
)
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import JurisdictionListPublished
from pymongo import AsyncMongoClient, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from fcrm_policy.seedmodel import (
    control_inventory_documents,
    field_set_documents,
    jurisdiction_documents,
)

logger = logging.getLogger(__name__)

JURISDICTION_LISTS: Final = "jurisdiction_list_versions"
FIELD_SETS: Final = "field_set_versions"
CONTROL_INVENTORIES: Final = "control_inventory_versions"

#: ``version`` values that mean "the active one".
ACTIVE_ALIASES: Final = frozenset({"latest", "current", "active"})
#: ``version`` alias for the synthetic jurisdiction fixture list.
FIXTURE_ALIAS: Final = "fixture"
MAX_VERSIONS: Final = 200
"""Upper bound on a versions listing (a handful exist; this is a guard)."""

Document = dict[str, Any]
_NUMERIC_VERSION: Final = re.compile(r"^v?(\d+)$", re.IGNORECASE)


def normalise_numeric_version(raw: str) -> str:
    """``"v9"`` and ``"9"`` -> ``"v9"``: the label field sets and control
    inventories are stored under.

    Raises:
        ValidationError: ``raw`` is neither ``v<digits>`` nor ``<digits>``.
    """
    match = _NUMERIC_VERSION.match(raw.strip())
    if match is None:
        raise FcrmValidationError(
            f"{raw!r} is not a version like v6.",
            errors=[{"loc": "version", "msg": "expected v<number>"}],
        )
    return f"v{int(match.group(1))}"


def version_filter(version: Optional[str], *, numeric: bool) -> Document:
    """The Mongo filter that selects one version document.

    ``None`` and the aliases in :data:`ACTIVE_ALIASES` select the active one;
    ``fixture`` (jurisdiction lists only) the synthetic fixture; anything else
    the version of that name (normalised for numeric versions).
    """
    if version is None or version.lower() in ACTIVE_ALIASES:
        return {"active": True}
    if not numeric and version.lower() == FIXTURE_ALIAS:
        return {"fixture": True}
    return {"_id": normalise_numeric_version(version) if numeric else version}


def _matches(doc: Mapping[str, Any], flt: Mapping[str, Any]) -> bool:
    """In-memory twin of a :func:`version_filter` filter."""
    return all(doc.get(key) == value for key, value in flt.items())


def _version_order(version: str) -> tuple[int, str]:
    """Sort key: numeric versions by number (v10 after v9), then by text."""
    match = _NUMERIC_VERSION.match(version)
    return (int(match.group(1)), version) if match else (0, version)


# ── document -> contract model (pure) ────────────────────────────────────


def jurisdiction_out(doc: Mapping[str, Any]) -> JurisdictionListOut:
    """A stored jurisdiction list as served."""
    return JurisdictionListOut(
        version=doc["version"],
        published=doc["published"],
        source=doc["source"],
        call_for_action=list(doc["call_for_action"]),
        increased_monitoring=list(doc["increased_monitoring"]),
        active=bool(doc["active"]),
    )


def jurisdiction_version_out(doc: Mapping[str, Any]) -> JurisdictionVersionOut:
    """A stored jurisdiction list as one row of the versions listing."""
    return JurisdictionVersionOut(
        version=doc["version"],
        published=doc["published"],
        fixture=bool(doc["fixture"]),
        active=bool(doc["active"]),
    )


def field_set_out(doc: Mapping[str, Any]) -> FieldSetOut:
    """A stored field set as served: always-required fields flagged, each
    per-type field carrying the change type that adds it.
    """
    return FieldSetOut(
        version=doc["version"],
        active=bool(doc["active"]),
        max_required=doc["max_required"],
        completion_threshold=doc["completion_threshold"],
        always_required=[
            FieldDef(field_id=f["id"], label=f["label"], always_required=True)
            for f in doc["always_required"]
        ],
        per_type={
            change_type: [
                FieldDef(
                    field_id=f["id"],
                    label=f["label"],
                    always_required=False,
                    change_type=change_type,
                )
                for f in fields
            ]
            for change_type, fields in doc["per_type"].items()
        },
    )


def control_out(row: Mapping[str, Any]) -> ControlOut:
    """One stored control row as served."""
    return ControlOut(
        control_id=row["control_id"],
        domain=row["domain"],
        name=row["name"],
        catalogue_weight=row["catalogue_weight"],
        evidence_artifact=row["evidence_artifact"],
        last_tested=row["last_tested"],
    )


def control_inventory_out(doc: Mapping[str, Any]) -> ControlInventoryOut:
    """A stored control inventory (already narrowed by the store) as served."""
    return ControlInventoryOut(
        version=doc["version"],
        active=bool(doc["active"]),
        controls=[control_out(row) for row in doc["controls"]],
    )


EVENT_JURISDICTION_PUBLISHED: Final = "jurisdiction.list_published"


def activation_result(
    target: Mapping[str, Any],
    previous: Optional[Mapping[str, Any]],
    *,
    already_active: bool,
) -> JurisdictionActivateOut:
    """What activating ``target`` (over ``previous``) amounts to: the version
    now active, the one it replaced and the change it announces.
    """
    changes = target.get("changes", {})
    return JurisdictionActivateOut(
        version=target["version"],
        previous_version=previous["version"] if previous is not None else None,
        fixture=bool(target.get("fixture", False)),
        added=list(changes.get("added", [])),
        removed=list(changes.get("removed", [])),
        already_active=already_active,
    )


def published_envelope(result: JurisdictionActivateOut) -> Envelope:
    """The ``jurisdiction.list_published`` event for an activation (the
    watchlist agent re-flags open cases against ``result.version``).
    """
    payload = JurisdictionListPublished(
        version=result.version,
        added=result.added,
        removed=result.removed,
        fixture=result.fixture,
    )
    return build_envelope(EVENT_JURISDICTION_PUBLISHED, payload.model_dump(mode="json"))


def parse_change_types(raw: Optional[str]) -> list[str]:
    """``"new_product, vendor_onboarding"`` -> the types, in order, trimmed,
    without empties or repeats.
    """
    seen: dict[str, None] = {}
    for part in (raw or "").split(","):
        name = part.strip()
        if name:
            seen.setdefault(name, None)
    return list(seen)


def resolve_fields(
    field_set: FieldSetOut, change_types: Iterable[str]
) -> ResolvedFieldSetOut:
    """The fields a case with ``change_types`` must fill: the always-required
    set first (in its defined order), then each change type's own fields in the
    order the types were given, de-duplicated by field id.

    This is the same arithmetic as S1's ``resolve_required_fields``; a test
    compares the two for every combination of the seed's change types.

    Raises:
        ValidationError: a change type is not in the field set.
    """
    requested = list(dict.fromkeys(change_types))
    unknown = [t for t in requested if t not in field_set.per_type]
    if unknown:
        raise FcrmValidationError(
            f"Unknown change types for field set {field_set.version}: {unknown}.",
            errors=[
                {"loc": "query.change_types", "msg": f"unknown change type {t}"}
                for t in unknown
            ],
        )
    seen: set[str] = set()
    fields: list[FieldDef] = []
    pools: list[Sequence[FieldDef]] = [
        field_set.always_required,
        *(field_set.per_type[t] for t in requested),
    ]
    for pool in pools:
        for field in pool:
            if field.field_id not in seen:
                seen.add(field.field_id)
                fields.append(field)
    return ResolvedFieldSetOut(
        version=field_set.version,
        change_types=requested,
        fields=fields,
        max_required=field_set.max_required,
        completion_threshold=field_set.completion_threshold,
    )


class ReferenceStore(Protocol):
    """What the reference-data routes depend on. Each lookup returns ``None``
    when no such version exists (the route turns that into a 404).
    """

    async def jurisdiction_versions(self) -> list[JurisdictionVersionOut]: ...

    async def jurisdiction_list(
        self, version: Optional[str]
    ) -> Optional[JurisdictionListOut]: ...

    async def field_set(self, version: Optional[str]) -> Optional[FieldSetOut]: ...

    async def control_versions(self) -> list[ControlVersionOut]: ...

    async def control_inventory(
        self, version: Optional[str], domain: Optional[str]
    ) -> Optional[ControlInventoryOut]: ...

    async def activate_jurisdiction(
        self, version: str
    ) -> Optional[JurisdictionActivateOut]:
        """Make ``version`` the active jurisdiction list and announce it, in one
        step; ``None`` when there is no such version.
        """
        ...


class InMemoryReferenceStore:
    """Used by unit tests: the same seed documents the Mongo store is loaded
    with, filtered by the same :func:`version_filter` rules.
    """

    def __init__(
        self,
        *,
        jurisdictions: Sequence[Document] = (),
        field_sets: Sequence[Document] = (),
        control_inventories: Sequence[Document] = (),
    ) -> None:
        self._jurisdictions = [dict(d) for d in jurisdictions]
        self._field_sets = [dict(d) for d in field_sets]
        self._controls = [dict(d) for d in control_inventories]
        #: The ``jurisdiction.list_published`` events an activation announced
        #: (what the Mongo store writes to its outbox).
        self.events: list[Envelope] = []

    @classmethod
    def from_seed(cls, raw: Mapping[str, Any]) -> InMemoryReferenceStore:
        """A store holding the seed's lists, field set and inventory."""
        return cls(
            jurisdictions=jurisdiction_documents(raw),
            field_sets=field_set_documents(raw),
            control_inventories=control_inventory_documents(raw),
        )

    @staticmethod
    def _pick(
        docs: Sequence[Document], version: Optional[str], *, numeric: bool
    ) -> Optional[Document]:
        flt = version_filter(version, numeric=numeric)
        return next((d for d in docs if _matches(d, flt)), None)

    async def jurisdiction_versions(self) -> list[JurisdictionVersionOut]:
        return [
            jurisdiction_version_out(d)
            for d in sorted(self._jurisdictions, key=lambda d: d["version"])
        ]

    async def jurisdiction_list(
        self, version: Optional[str]
    ) -> Optional[JurisdictionListOut]:
        doc = self._pick(self._jurisdictions, version, numeric=False)
        return jurisdiction_out(doc) if doc is not None else None

    async def field_set(self, version: Optional[str]) -> Optional[FieldSetOut]:
        doc = self._pick(self._field_sets, version, numeric=True)
        return field_set_out(doc) if doc is not None else None

    async def control_versions(self) -> list[ControlVersionOut]:
        return [
            ControlVersionOut(
                version=d["version"],
                active=bool(d["active"]),
                control_count=len(d["controls"]),
            )
            for d in sorted(self._controls, key=lambda d: _version_order(d["version"]))
        ]

    async def control_inventory(
        self, version: Optional[str], domain: Optional[str]
    ) -> Optional[ControlInventoryOut]:
        doc = self._pick(self._controls, version, numeric=True)
        if doc is None:
            return None
        rows = [c for c in doc["controls"] if domain is None or c["domain"] == domain]
        return control_inventory_out({**doc, "controls": rows})

    async def activate_jurisdiction(
        self, version: str
    ) -> Optional[JurisdictionActivateOut]:
        """Activate like the Mongo store: the previous list off, this one on,
        one event; a repeat of the active list changes and announces nothing.
        """
        target = self._pick(self._jurisdictions, version, numeric=False)
        if target is None:
            return None
        current = next((d for d in self._jurisdictions if d["active"]), None)
        if current is target:
            return activation_result(target, current, already_active=True)
        if current is not None:
            current["active"] = False
        target["active"] = True
        result = activation_result(target, current, already_active=False)
        self.events.append(published_envelope(result))
        return result

    async def activate_jurisdiction_in(
        self, session: object, version: str
    ) -> Optional[JurisdictionActivateOut]:
        """:meth:`activate_jurisdiction`, for a caller that passes a session
        (ignored here: the twin has no transaction to share).
        """
        return await self.activate_jurisdiction(version)

    async def add_field_set_version(self, session: object, doc: Document) -> None:
        """Add ``doc`` as the active field set (the one in force goes inactive)."""
        for existing in self._field_sets:
            existing["active"] = False
        self._field_sets.append({**doc, "_id": doc["version"], "active": True})

    async def add_control_inventory_version(
        self, session: object, doc: Document
    ) -> None:
        """Add ``doc`` as the active control inventory."""
        for existing in self._controls:
            existing["active"] = False
        self._controls.append({**doc, "_id": doc["version"], "active": True})


class MongoReferenceStore:
    """The real store over the three reference collections (read-only here;
    versions are published by the analyst's configuration routes and the seed).
    """

    def __init__(
        self,
        db: AsyncDatabase[Document],
        *,
        client: Optional[AsyncMongoClient[Document]] = None,
        outbox: Optional[Outbox] = None,
    ) -> None:
        """Read the three collections of ``db``. ``client`` and ``outbox`` are
        what :meth:`activate_jurisdiction` needs to change a list and announce
        it in one transaction; without them it refuses.
        """
        self._client = client
        self._outbox = outbox
        self._jurisdictions: AsyncCollection[Document] = db[JURISDICTION_LISTS]
        self._field_sets: AsyncCollection[Document] = db[FIELD_SETS]
        self._controls: AsyncCollection[Document] = db[CONTROL_INVENTORIES]

    @property
    def collections(self) -> dict[str, AsyncCollection[Document]]:
        """``{collection name: collection}``, for seeding."""
        return {
            JURISDICTION_LISTS: self._jurisdictions,
            FIELD_SETS: self._field_sets,
            CONTROL_INVENTORIES: self._controls,
        }

    async def ensure_indexes(self) -> None:
        """Create each collection's unique ``version`` index and its
        one-active-version guard (idempotent).
        """
        for collection in self.collections.values():
            await collection.create_indexes(
                [
                    IndexModel([("version", 1)], unique=True, name="version_unique"),
                    IndexModel(
                        [("active", 1)],
                        unique=True,
                        partialFilterExpression={"active": True},
                        name="single_active",
                    ),
                ]
            )

    async def jurisdiction_versions(self) -> list[JurisdictionVersionOut]:
        docs = await (
            self._jurisdictions.find(
                {},
                projection={"version": 1, "published": 1, "fixture": 1, "active": 1},
            )
            .sort("version", 1)
            .limit(MAX_VERSIONS)
            .to_list()
        )
        return [jurisdiction_version_out(d) for d in docs]

    async def jurisdiction_list(
        self, version: Optional[str]
    ) -> Optional[JurisdictionListOut]:
        doc = await self._jurisdictions.find_one(
            version_filter(version, numeric=False),
            projection={
                "version": 1,
                "published": 1,
                "source": 1,
                "call_for_action": 1,
                "increased_monitoring": 1,
                "active": 1,
            },
        )
        return jurisdiction_out(doc) if doc is not None else None

    async def field_set(self, version: Optional[str]) -> Optional[FieldSetOut]:
        doc = await self._field_sets.find_one(version_filter(version, numeric=True))
        return field_set_out(doc) if doc is not None else None

    async def control_versions(self) -> list[ControlVersionOut]:
        rows = await self._controls.aggregate(
            [
                {
                    "$project": {
                        "version": 1,
                        "active": 1,
                        "control_count": {"$size": "$controls"},
                    }
                },
                {"$limit": MAX_VERSIONS},
            ]
        )
        versions = [
            ControlVersionOut(
                version=row["version"],
                active=bool(row["active"]),
                control_count=row["control_count"],
            )
            for row in await rows.to_list()
        ]
        return sorted(versions, key=lambda v: _version_order(v.version))

    async def control_inventory(
        self, version: Optional[str], domain: Optional[str]
    ) -> Optional[ControlInventoryOut]:
        controls: Any = "$controls"
        if domain is not None:
            controls = {
                "$filter": {
                    "input": "$controls",
                    "cond": {"$eq": ["$$this.domain", domain]},
                }
            }
        cursor = await self._controls.aggregate(
            [
                {"$match": version_filter(version, numeric=True)},
                {"$limit": 1},
                {
                    "$project": {
                        "version": 1,
                        "active": 1,
                        "controls": controls,
                    }
                },
            ]
        )
        docs = await cursor.to_list()
        return control_inventory_out(docs[0]) if docs else None

    async def activate_jurisdiction(
        self, version: str
    ) -> Optional[JurisdictionActivateOut]:
        """One transaction: deactivate the active list, activate ``version``,
        enqueue ``jurisdiction.list_published``. Either all three happen or
        none (the partial unique index keeps at most one list active).

        Raises:
            UpstreamUnavailableError: The store was built without a Mongo
                client and outbox (it cannot announce what it changes).
        """
        client = self._client
        if client is None or self._outbox is None:
            raise UpstreamUnavailableError(
                "Jurisdiction activation needs the outbox and a replica set.",
                dependency="mongo",
            )

        async def txn(session: AsyncClientSession) -> Optional[JurisdictionActivateOut]:
            return await self.activate_jurisdiction_in(session, version)

        return await with_transaction(client, txn)

    async def activate_jurisdiction_in(
        self, session: AsyncClientSession, version: str
    ) -> Optional[JurisdictionActivateOut]:
        """The body of :meth:`activate_jurisdiction`, run inside the caller's
        transaction: deactivate the active list, activate ``version`` and
        enqueue ``jurisdiction.list_published`` with the same ``session``. A
        caller that has its own writes to commit with the activation (the
        configuration screens' publish) shares the transaction through this.

        Raises:
            UpstreamUnavailableError: The store was built without an outbox.
        """
        outbox = self._outbox
        if outbox is None:
            raise UpstreamUnavailableError(
                "Jurisdiction activation needs the outbox.", dependency="mongo"
            )
        target = await self._jurisdictions.find_one(
            version_filter(version, numeric=False), session=session
        )
        if target is None:
            return None
        current = await self._jurisdictions.find_one({"active": True}, session=session)
        if current is not None and current["_id"] == target["_id"]:
            return activation_result(target, current, already_active=True)
        if current is not None:
            await self._jurisdictions.update_one(
                {"_id": current["_id"]},
                {"$set": {"active": False}},
                session=session,
            )
        await self._jurisdictions.update_one(
            {"_id": target["_id"]},
            {"$set": {"active": True, "activated_at": utcnow()}},
            session=session,
        )
        result = activation_result(target, current, already_active=False)
        await outbox.enqueue(session, published_envelope(result))
        return result

    async def add_field_set_version(
        self, session: Optional[AsyncClientSession], doc: Document
    ) -> None:
        """Insert ``doc`` as the active field set, deactivating the one in
        force, with the caller's ``session`` (a configuration publish).
        """
        await self._add_version(self._field_sets, session, doc)

    async def add_control_inventory_version(
        self, session: Optional[AsyncClientSession], doc: Document
    ) -> None:
        """Insert ``doc`` as the active control inventory, deactivating the
        one in force, with the caller's ``session``.
        """
        await self._add_version(self._controls, session, doc)

    @staticmethod
    async def _add_version(
        collection: AsyncCollection[Document],
        session: Optional[AsyncClientSession],
        doc: Document,
    ) -> None:
        await collection.update_many(
            {"active": True}, {"$set": {"active": False}}, session=session
        )
        await collection.insert_one(
            {**doc, "_id": doc["version"], "active": True}, session=session
        )

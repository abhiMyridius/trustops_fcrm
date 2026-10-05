"""S1's view store: the collections around the case document.

Same split as :mod:`fcrm_case.store`: a small :class:`ViewStore` ``Protocol``
the routes depend on, a Mongo implementation the lifespan wires, and an
in-memory twin for unit tests. The collections (design A §2.1.1; Annex F of the
build plan for the indexes):

=================  ==========================================================
``field_values``   one row per case field; ``_id = "<case>:<field>"``; value,
                   origin, source and ``history[]`` (a PO's correction keeps
                   the extracted original)
``documents``      uploaded files and what H3 made of them
``review_items``   what a gate acts on: intake categories and gaps now, clause,
                   factor, control and critic items later
``intake_views``   ``_id = case_id``; the precedent, candidates found, fact flags
=================  ==========================================================

``activity`` lives in :mod:`fcrm_case.activity_log`; ``score_snapshots`` and
``conditions`` are written only by the seed loader today and read by the
analyst and PO routes the later lanes add.

**Created now:** exactly the collections and indexes the V1 internal routes, the
stream and the seed need. **Left to later lanes** (each adds its collection, its
indexes in :meth:`MongoViewStore.ensure_indexes` and its methods here):
``submission_versions`` (unique ``{case_id, version}``), ``clarification_threads``,
``planned_controls``, ``gaps``, ``integrations``, ``critic_views``,
``completeness_views``, ``recommendations``, ``outcomes``, ``reflags``.

Rules every method follows:

* a write that must announce itself takes ``events`` and enqueues them through
  the :class:`~fcrm_common.outbox.Outbox` in the *same* transaction
  (:func:`fcrm_common.mongo.with_transaction`), exactly like
  ``MongoCaseStore.create``/``submit``;
* every read names its projection and has an index behind it; lists are one
  query, never one per row; item upserts are one ``bulk_write``;
* a value or decision a *person* made is never overwritten by an agent's
  proposal (:data:`~fcrm_case.domain.views.PROTECTED_ORIGINS`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Protocol

from fcrm_common.errors import ConflictError
from fcrm_common.mongo import Repository, to_bson, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import (
    DocumentPatch,
    IntakePush,
    ReviewItemUpsert,
    ValueProposal,
)
from fcrm_contracts.enums import ItemKind, ItemStatus
from fcrm_contracts.events.envelope import Envelope
from pymongo import ASCENDING, AsyncMongoClient, IndexModel, UpdateOne
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import BulkWriteError

from fcrm_case.domain.intake import (
    intake_view_from_push,
    items_from_push,
    value_set_fields,
)
from fcrm_case.domain.views import (
    PROTECTED_ORIGINS,
    ConditionRow,
    DecisionRow,
    DocumentRow,
    FieldHistoryEntry,
    FieldOrigin,
    FieldSource,
    FieldValueRow,
    IntakeViewRow,
    ReviewItemRow,
    ScoreSnapshotRow,
    field_value_id,
)

logger = logging.getLogger(__name__)

_DUPLICATE_KEY = 11000
#: A case has tens of items; this bound only stops a bug from reading a million.
MAX_ITEMS_PER_CASE = 2000
MAX_FIELDS_PER_CASE = 500
MAX_DOCUMENTS_PER_CASE = 200


@dataclass(frozen=True)
class IntakeApplied:
    """What :meth:`ViewStore.apply_intake_push` did, for the caller's log and
    the route's response.
    """

    values_written: int
    values_kept: int
    categories: int
    gaps: int


class ViewStore(Protocol):
    """What the internal routes (and, later, the PO and analyst routes) need."""

    async def ensure_indexes(self) -> None:
        """Create every index the queries below rely on (idempotent)."""
        ...

    async def get_document(self, doc_id: str) -> Optional[DocumentRow]:
        """One document, or ``None``."""
        ...

    async def list_documents(self, case_id: str) -> list[DocumentRow]:
        """Every document of the case, oldest upload first."""
        ...

    async def insert_documents(
        self, rows: Sequence[DocumentRow], events: Sequence[Envelope] = ()
    ) -> None:
        """Insert ``rows`` and enqueue ``events`` (``case.document_stored``)
        atomically.
        """
        ...

    async def patch_document(
        self, doc_id: str, patch: DocumentPatch
    ) -> Optional[DocumentRow]:
        """Record what H3 produced: ``status`` always, the other fields only
        when set. ``None`` if no such document. Replaying a patch changes nothing.
        """
        ...

    async def list_field_values(
        self, case_id: str, *, full_history: bool = False
    ) -> list[FieldValueRow]:
        """The case's stored field values. ``history`` holds only the oldest
        entry (the original) unless ``full_history``.
        """
        ...

    async def write_field_value(
        self,
        case_id: str,
        field_id: str,
        *,
        label: str,
        value: Optional[str],
        origin: FieldOrigin,
        source: Optional[FieldSource],
        by: str,
        events: Sequence[Envelope] = (),
    ) -> FieldValueRow:
        """Set a field's value as a person (``owner_declared``, ``reused``,
        ``answered``). The value it replaces, if different, moves to ``history``
        first, so a correction keeps the original. ``events`` are enqueued in
        the same transaction.
        """
        ...

    async def list_items(
        self, case_id: str, kind: Optional[ItemKind] = None
    ) -> list[ReviewItemRow]:
        """The case's review items, optionally of one ``kind``."""
        ...

    async def upsert_items(
        self, case_id: str, run_id: str, items: Sequence[ReviewItemUpsert]
    ) -> list[str]:
        """Insert or update ``items`` by ``item_id`` in one bulk write; returns
        their ids. An update rewrites an item's content but never its
        ``status``, ``decided`` or ``created_at``, so a replayed push cannot
        undo a decision.

        Raises:
            fcrm_common.errors.ConflictError: A ``proposal_id`` is already used
                by another item of the case.
        """
        ...

    async def decide_item(
        self,
        case_id: str,
        item_id: str,
        *,
        status: ItemStatus,
        decided: DecisionRow,
        events: Sequence[Envelope] = (),
    ) -> Optional[ReviewItemRow]:
        """Record a decision on one item and enqueue ``events`` atomically;
        ``None`` if the case has no such item.
        """
        ...

    async def apply_intake_push(self, case_id: str, push: IntakePush) -> IntakeApplied:
        """Materialise an :class:`IntakePush` in one transaction, idempotently:
        extracted values become ``field_values`` rows unless a person already
        set that field, categories and gaps become gated review items, and the
        precedent, candidates and flags go to ``intake_views``. The same push
        twice leaves the same state.
        """
        ...

    async def get_intake_view(self, case_id: str) -> Optional[IntakeViewRow]:
        """The case's intake view, or ``None`` before the first push."""
        ...


@dataclass(frozen=True)
class ViewRepos:
    """The typed repositories over S1's view collections."""

    field_values: Repository[FieldValueRow]
    documents: Repository[DocumentRow]
    review_items: Repository[ReviewItemRow]
    intake_views: Repository[IntakeViewRow]
    score_snapshots: Repository[ScoreSnapshotRow]
    conditions: Repository[ConditionRow]


def build_view_repos(db: Any) -> ViewRepos:
    """The repositories over ``db``'s view collections, keyed as documented in
    the module docstring.
    """
    return ViewRepos(
        field_values=Repository(
            db["field_values"],
            FieldValueRow,
            id_of=lambda r: field_value_id(r.case_id, r.field_id),
        ),
        documents=Repository(db["documents"], DocumentRow, id_of=lambda r: r.doc_id),
        review_items=Repository(
            db["review_items"], ReviewItemRow, id_of=lambda r: r.item_id
        ),
        intake_views=Repository(
            db["intake_views"], IntakeViewRow, id_of=lambda r: r.case_id
        ),
        score_snapshots=Repository(
            db["score_snapshots"], ScoreSnapshotRow, id_of=lambda r: r.snapshot_id
        ),
        conditions=Repository(
            db["conditions"], ConditionRow, id_of=lambda r: r.condition_id
        ),
    )


def item_upsert_op(
    case_id: str, run_id: str, item: ReviewItemUpsert, now: datetime
) -> UpdateOne:
    """The upsert of one review item: content is ``$set`` (a replay rewrites
    the same values), ``status``/``decided``/``created_at`` are ``$setOnInsert``
    (a replay never reverts a decision). ``case_id`` is in the filter, so an id
    that belongs to another case cannot be taken over.
    """
    return UpdateOne(
        {"_id": item.item_id, "case_id": case_id},
        {
            "$set": to_bson(
                {
                    "item_id": item.item_id,
                    "kind": item.kind,
                    "proposal_id": item.proposal_id,
                    "gate_id": item.gate_id,
                    "subject": item.subject,
                    "proposed": item.proposed,
                    "source_refs": [
                        r.model_dump(mode="json") for r in item.source_refs
                    ],
                    "input_fields": item.input_fields,
                    "supersedes": item.supersedes,
                    "run_id": run_id,
                }
            ),
            "$setOnInsert": to_bson(
                {"status": item.status, "decided": None, "created_at": now}
            ),
        },
        upsert=True,
    )


def value_upsert_op(case_id: str, value: ValueProposal, now: datetime) -> UpdateOne:
    """The upsert of one extracted value: the fields a re-extraction may
    rewrite are ``$set``, the history and timestamps are ``$setOnInsert``.
    """
    return UpdateOne(
        {"_id": field_value_id(case_id, value.field_id), "case_id": case_id},
        {
            "$set": to_bson(value_set_fields(value)),
            "$setOnInsert": {
                "history": [],
                "created_at": now,
                "updated_at": None,
                "updated_by": None,
            },
        },
        upsert=True,
    )


def _raise_if_duplicate(exc: BulkWriteError, message: str) -> None:
    """Map a bulk write that only failed on duplicate keys to a 409 with
    ``message``; any other write error is not ours to interpret and propagates.
    """
    errors = exc.details.get("writeErrors", [])
    if errors and all(err.get("code") == _DUPLICATE_KEY for err in errors):
        raise ConflictError(message) from exc
    raise exc


class MongoViewStore:
    """The real store over ``fcrm_case``'s view collections.

    Args:
        client: The Mongo client (transactions need the ``rs0`` replica set).
        db: The ``fcrm_case`` database.
        outbox: The case service's outbox, so an event commits with its write.
    """

    def __init__(self, client: AsyncMongoClient[Any], db: Any, outbox: Outbox) -> None:
        self._client = client
        self._outbox = outbox
        self._repos = build_view_repos(db)

    @property
    def repos(self) -> ViewRepos:
        """The repositories (the seed loader writes through them)."""
        return self._repos

    async def ensure_indexes(self) -> None:
        """Annex F, for the collections that exist today. ``review_items``'
        proposal index is *partial* on ``proposal_id`` being a string, so items
        without a proposal (seeded or intake items) never collide on ``null``.
        """
        repos = self._repos
        await repos.field_values.ensure_indexes(
            [IndexModel([("case_id", ASCENDING)], name="case_idx")]
        )
        await repos.documents.ensure_indexes(
            [IndexModel([("case_id", ASCENDING)], name="case_idx")]
        )
        await repos.review_items.ensure_indexes(
            [
                IndexModel(
                    [("case_id", ASCENDING), ("proposal_id", ASCENDING)],
                    unique=True,
                    name="case_proposal_unique",
                    partialFilterExpression={"proposal_id": {"$type": "string"}},
                ),
                IndexModel(
                    [
                        ("case_id", ASCENDING),
                        ("kind", ASCENDING),
                        ("status", ASCENDING),
                    ],
                    name="case_kind_status",
                ),
                IndexModel(
                    [("case_id", ASCENDING), ("gate_id", ASCENDING)], name="case_gate"
                ),
            ]
        )
        await repos.score_snapshots.ensure_indexes(
            [
                IndexModel(
                    [("case_id", ASCENDING), ("computed_at", -1)], name="case_computed"
                )
            ]
        )
        await repos.conditions.ensure_indexes(
            [
                IndexModel([("case_id", ASCENDING)], name="case_idx"),
                IndexModel(
                    [("due_date", ASCENDING), ("status", ASCENDING)], name="due_status"
                ),
            ]
        )

    # ── documents ─────────────────────────────────────────────────────────

    async def get_document(self, doc_id: str) -> Optional[DocumentRow]:
        repo = self._repos.documents
        doc = await repo.collection.find_one({"_id": doc_id})
        return repo.from_document(doc) if doc is not None else None

    async def list_documents(self, case_id: str) -> list[DocumentRow]:
        repo = self._repos.documents
        docs = await repo.collection.find(
            {"case_id": case_id},
            sort=[("uploaded_at", ASCENDING), ("_id", ASCENDING)],
            limit=MAX_DOCUMENTS_PER_CASE,
        ).to_list()
        return [repo.from_document(d) for d in docs]

    async def insert_documents(
        self, rows: Sequence[DocumentRow], events: Sequence[Envelope] = ()
    ) -> None:
        async def _do(session: AsyncClientSession) -> None:
            await self._repos.documents.insert_many(rows, session=session)
            for event in events:
                await self._outbox.enqueue(session, event)

        if not rows:
            return
        try:
            await with_transaction(self._client, _do)
        except BulkWriteError as exc:
            _raise_if_duplicate(exc, "a document with that id already exists")

    async def patch_document(
        self, doc_id: str, patch: DocumentPatch
    ) -> Optional[DocumentRow]:
        changes = patch.model_dump(mode="python", exclude_none=True)
        return await self._repos.documents.find_one_and_update({"_id": doc_id}, changes)

    # ── field values ──────────────────────────────────────────────────────

    async def list_field_values(
        self, case_id: str, *, full_history: bool = False
    ) -> list[FieldValueRow]:
        repo = self._repos.field_values
        projection = None if full_history else {"history": {"$slice": 1}}
        docs = await repo.collection.find(
            {"case_id": case_id},
            projection=projection,
            sort=[("_id", ASCENDING)],
            limit=MAX_FIELDS_PER_CASE,
        ).to_list()
        return [repo.from_document(d) for d in docs]

    async def write_field_value(
        self,
        case_id: str,
        field_id: str,
        *,
        label: str,
        value: Optional[str],
        origin: FieldOrigin,
        source: Optional[FieldSource],
        by: str,
        events: Sequence[Envelope] = (),
    ) -> FieldValueRow:
        repo = self._repos.field_values
        row_id = field_value_id(case_id, field_id)

        async def _do(session: AsyncClientSession) -> FieldValueRow:
            now = utcnow()
            existing = await repo.collection.find_one({"_id": row_id}, session=session)
            previous = repo.from_document(existing) if existing is not None else None
            row = next_field_row(
                previous, case_id, field_id, label, value, origin, source, by, now
            )
            await repo.collection.replace_one(
                {"_id": row_id}, repo.to_document(row), upsert=True, session=session
            )
            for event in events:
                await self._outbox.enqueue(session, event)
            return row

        return await with_transaction(self._client, _do)

    # ── review items ──────────────────────────────────────────────────────

    async def list_items(
        self, case_id: str, kind: Optional[ItemKind] = None
    ) -> list[ReviewItemRow]:
        repo = self._repos.review_items
        query: dict[str, Any] = {"case_id": case_id}
        if kind is not None:
            query["kind"] = kind.value
        docs = await repo.collection.find(
            query,
            sort=[("created_at", ASCENDING), ("_id", ASCENDING)],
            limit=MAX_ITEMS_PER_CASE,
        ).to_list()
        return [repo.from_document(d) for d in docs]

    async def upsert_items(
        self, case_id: str, run_id: str, items: Sequence[ReviewItemUpsert]
    ) -> list[str]:
        if not items:
            return []
        now = utcnow()
        ops = [item_upsert_op(case_id, run_id, item, now) for item in items]
        try:
            await self._repos.review_items.collection.bulk_write(ops, ordered=False)
        except BulkWriteError as exc:
            _raise_if_duplicate(
                exc,
                "a review item conflicts with an existing one: the same proposal "
                "id on this case, or an item id that belongs to another case",
            )
        return [item.item_id for item in items]

    async def decide_item(
        self,
        case_id: str,
        item_id: str,
        *,
        status: ItemStatus,
        decided: DecisionRow,
        events: Sequence[Envelope] = (),
    ) -> Optional[ReviewItemRow]:
        async def _do(session: AsyncClientSession) -> Optional[ReviewItemRow]:
            updated = await self._repos.review_items.find_one_and_update(
                {"_id": item_id, "case_id": case_id},
                {"status": status.value, "decided": decided.model_dump(mode="python")},
                session=session,
            )
            if updated is not None:
                for event in events:
                    await self._outbox.enqueue(session, event)
            return updated

        return await with_transaction(self._client, _do)

    # ── intake ────────────────────────────────────────────────────────────

    async def apply_intake_push(self, case_id: str, push: IntakePush) -> IntakeApplied:
        items = items_from_push(case_id, push)
        now = utcnow()
        view = self._repos.intake_views

        async def _do(session: AsyncClientSession) -> IntakeApplied:
            protected = await self._protected_field_ids(
                case_id, [v.field_id for v in push.values], session
            )
            writable = [v for v in push.values if v.field_id not in protected]
            if writable:
                await self._repos.field_values.collection.bulk_write(
                    [value_upsert_op(case_id, v, now) for v in writable],
                    ordered=False,
                    session=session,
                )
            if items:
                await self._repos.review_items.collection.bulk_write(
                    [
                        item_upsert_op(case_id, push.run_id, item, at)
                        for item, at in _in_push_order(items, now)
                    ],
                    ordered=False,
                    session=session,
                )
            await view.collection.replace_one(
                {"_id": case_id},
                view.to_document(intake_view_from_push(case_id, push)),
                upsert=True,
                session=session,
            )
            return IntakeApplied(
                values_written=len(writable),
                values_kept=len(push.values) - len(writable),
                categories=len(push.categories),
                gaps=len(push.gaps),
            )

        return await with_transaction(self._client, _do)

    async def _protected_field_ids(
        self, case_id: str, field_ids: Sequence[str], session: AsyncClientSession
    ) -> set[str]:
        """Which of ``field_ids`` already hold a value a person set (one query)."""
        if not field_ids:
            return set()
        docs = await self._repos.field_values.collection.find(
            {
                "_id": {"$in": [field_value_id(case_id, f) for f in field_ids]},
                "origin": {"$in": list(PROTECTED_ORIGINS)},
            },
            projection={"field_id": 1},
            session=session,
        ).to_list()
        return {str(d["field_id"]) for d in docs}

    async def get_intake_view(self, case_id: str) -> Optional[IntakeViewRow]:
        repo = self._repos.intake_views
        doc = await repo.collection.find_one({"_id": case_id})
        return repo.from_document(doc) if doc is not None else None


def next_field_row(
    previous: Optional[FieldValueRow],
    case_id: str,
    field_id: str,
    label: str,
    value: Optional[str],
    origin: FieldOrigin,
    source: Optional[FieldSource],
    by: str,
    now: datetime,
) -> FieldValueRow:
    """The row after a person set ``field_id``: the replaced value joins
    ``history`` when it differs (a correction keeps the original), and an
    identical write leaves the row as it was.
    """
    if previous is None:
        return FieldValueRow(
            case_id=case_id,
            field_id=field_id,
            label=label,
            value=value,
            origin=origin,
            source=source,
            created_at=now,
            updated_at=now,
            updated_by=by,
        )
    if previous.value == value and previous.origin == origin:
        return previous
    kept = FieldHistoryEntry(
        value=previous.value,
        origin=previous.origin,
        source=previous.source,
        at=previous.updated_at or previous.created_at,
        by=previous.updated_by,
    )
    return previous.model_copy(
        update={
            "label": label,
            "value": value,
            "origin": origin,
            "source": source,
            "confidence": None,
            "history": [*previous.history, kept],
            "updated_at": now,
            "updated_by": by,
        }
    )


def _in_push_order(
    items: Sequence[ReviewItemUpsert], now: datetime
) -> list[tuple[ReviewItemUpsert, datetime]]:
    """Each item of a push with the ``created_at`` it is inserted with: ``now`` plus
    one millisecond per position (BSON dates have millisecond resolution), so
    listing items by ``created_at`` reads them back in the order H1 merged them
    (the owner sees the change types and questions in that order). An item that
    already exists keeps its first ``created_at``, so a re-push never reorders.
    """
    return [(item, now + timedelta(milliseconds=n)) for n, item in enumerate(items)]


class InMemoryViewStore:
    """The test twin: dicts, no transactions (an in-process update is atomic
    with respect to one event loop), the same rules as :class:`MongoViewStore`.
    ``events`` collects what the Mongo store would enqueue in the outbox.
    """

    def __init__(self) -> None:
        self.field_values: dict[str, FieldValueRow] = {}
        self.documents: dict[str, DocumentRow] = {}
        self.items: dict[str, ReviewItemRow] = {}
        self.intake: dict[str, IntakeViewRow] = {}
        self.events: list[Envelope] = []

    async def ensure_indexes(self) -> None:
        """Nothing to create."""

    async def get_document(self, doc_id: str) -> Optional[DocumentRow]:
        return self.documents.get(doc_id)

    async def list_documents(self, case_id: str) -> list[DocumentRow]:
        rows = [d for d in self.documents.values() if d.case_id == case_id]
        return sorted(rows, key=lambda d: (d.uploaded_at, d.doc_id))

    async def insert_documents(
        self, rows: Sequence[DocumentRow], events: Sequence[Envelope] = ()
    ) -> None:
        for row in rows:
            if row.doc_id in self.documents:
                raise ConflictError(f"document {row.doc_id} already exists")
        for row in rows:
            self.documents[row.doc_id] = row
        self.events.extend(events)

    async def patch_document(
        self, doc_id: str, patch: DocumentPatch
    ) -> Optional[DocumentRow]:
        row = self.documents.get(doc_id)
        if row is None:
            return None
        updated = row.model_copy(update=patch.model_dump(exclude_none=True))
        self.documents[doc_id] = updated
        return updated

    async def list_field_values(
        self, case_id: str, *, full_history: bool = False
    ) -> list[FieldValueRow]:
        rows = [r for r in self.field_values.values() if r.case_id == case_id]
        if not full_history:
            rows = [r.model_copy(update={"history": r.history[:1]}) for r in rows]
        return sorted(rows, key=lambda r: field_value_id(r.case_id, r.field_id))

    async def write_field_value(
        self,
        case_id: str,
        field_id: str,
        *,
        label: str,
        value: Optional[str],
        origin: FieldOrigin,
        source: Optional[FieldSource],
        by: str,
        events: Sequence[Envelope] = (),
    ) -> FieldValueRow:
        row_id = field_value_id(case_id, field_id)
        row = next_field_row(
            self.field_values.get(row_id),
            case_id,
            field_id,
            label,
            value,
            origin,
            source,
            by,
            utcnow(),
        )
        self.field_values[row_id] = row
        self.events.extend(events)
        return row

    async def list_items(
        self, case_id: str, kind: Optional[ItemKind] = None
    ) -> list[ReviewItemRow]:
        rows = [
            i
            for i in self.items.values()
            if i.case_id == case_id and (kind is None or i.kind == kind)
        ]
        return sorted(rows, key=lambda i: (i.created_at, i.item_id))

    async def upsert_items(
        self, case_id: str, run_id: str, items: Sequence[ReviewItemUpsert]
    ) -> list[str]:
        self._check_proposals(case_id, items)
        now = utcnow()
        for item in items:
            self._upsert_item(case_id, run_id, item, now)
        return [item.item_id for item in items]

    def _check_proposals(self, case_id: str, items: Sequence[ReviewItemUpsert]) -> None:
        """The partial unique index ``{case_id, proposal_id}``, by hand."""
        owner = {
            i.proposal_id: i.item_id
            for i in self.items.values()
            if i.case_id == case_id and i.proposal_id is not None
        }
        for item in items:
            if item.proposal_id is None:
                continue
            if owner.get(item.proposal_id, item.item_id) != item.item_id:
                raise ConflictError(
                    "a review item with that proposal id already exists on this case"
                )
            owner[item.proposal_id] = item.item_id

    def _upsert_item(
        self, case_id: str, run_id: str, item: ReviewItemUpsert, now: datetime
    ) -> None:
        existing = self.items.get(item.item_id)
        if existing is not None and existing.case_id != case_id:
            raise ConflictError(f"item {item.item_id} belongs to another case")
        self.items[item.item_id] = ReviewItemRow(
            item_id=item.item_id,
            case_id=case_id,
            kind=item.kind,
            status=existing.status if existing else item.status,
            proposal_id=item.proposal_id,
            gate_id=item.gate_id,
            subject=item.subject,
            proposed=item.proposed,
            source_refs=[r.model_dump(mode="json") for r in item.source_refs],
            input_fields=item.input_fields,
            supersedes=item.supersedes,
            run_id=run_id,
            decided=existing.decided if existing else None,
            created_at=existing.created_at if existing else now,
        )

    async def decide_item(
        self,
        case_id: str,
        item_id: str,
        *,
        status: ItemStatus,
        decided: DecisionRow,
        events: Sequence[Envelope] = (),
    ) -> Optional[ReviewItemRow]:
        row = self.items.get(item_id)
        if row is None or row.case_id != case_id:
            return None
        updated = row.model_copy(update={"status": status, "decided": decided})
        self.items[item_id] = updated
        self.events.extend(events)
        return updated

    async def apply_intake_push(self, case_id: str, push: IntakePush) -> IntakeApplied:
        items = items_from_push(case_id, push)
        now = utcnow()
        written = 0
        for value in push.values:
            row_id = field_value_id(case_id, value.field_id)
            existing = self.field_values.get(row_id)
            if existing is not None and existing.origin in PROTECTED_ORIGINS:
                continue
            self.field_values[row_id] = FieldValueRow.model_validate(
                {
                    **value_set_fields(value),
                    "case_id": case_id,
                    "history": existing.history if existing else [],
                    "created_at": existing.created_at if existing else now,
                    "updated_at": existing.updated_at if existing else None,
                    "updated_by": existing.updated_by if existing else None,
                }
            )
            written += 1
        self._check_proposals(case_id, items)
        for item, at in _in_push_order(items, now):
            self._upsert_item(case_id, push.run_id, item, at)
        self.intake[case_id] = intake_view_from_push(case_id, push)
        return IntakeApplied(
            values_written=written,
            values_kept=len(push.values) - written,
            categories=len(push.categories),
            gaps=len(push.gaps),
        )

    async def get_intake_view(self, case_id: str) -> Optional[IntakeViewRow]:
        return self.intake.get(case_id)

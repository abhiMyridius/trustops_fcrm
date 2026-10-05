"""S3's precedents: closed cases other cases are compared against (design A
§2.3, §6.1), and the closed-case feedback loop that adds to them.

Sixty-five precedents come from the seed. Every case that closes after that
adds one: H1 posts a :class:`~fcrm_contracts.api.policy.PrecedentRecordIn` to
``POST /internal/precedents`` on ``case.closed``, and :meth:`PrecedentStore.record`
writes the row **and** an ``index.build`` request for ``precedent_cases`` in one
Mongo transaction, so a precedent that exists is a precedent H4 will embed, and
one that does not exist is never announced.

Posting the same case again is idempotent: an identical repost changes nothing
and enqueues nothing; a repost with different content replaces the row and
asks for a rebuild once.

:class:`PrecedentStore` is what the routes depend on: :class:`MongoPrecedentStore`
in production, :class:`InMemoryPrecedentStore` (which records the events it
would have enqueued) in the unit tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Final, Optional, Protocol

from fcrm_common.mongo import Document, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_contracts.api.policy import (
    PrecedentIndexRecord,
    PrecedentRecordIn,
    PrecedentRecordOut,
)
from fcrm_contracts.common import Page
from fcrm_contracts.enums import Outcome
from fcrm_contracts.events.envelope import Envelope
from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_policy.indexing import index_build_envelope
from fcrm_policy.seedmodel import precedent_document
from fcrm_policy.store import PRECEDENT_CASES

logger = logging.getLogger(__name__)

MAX_PRECEDENT_PAGE: Final = 200
DEFAULT_PRECEDENT_PAGE: Final = 100

#: The contract's outcome values -> the seed's vocabulary, which every stored
#: precedent uses (so a closed case and a seeded one match the same filter).
OUTCOME_STORED: Final[dict[Outcome, str]] = {
    Outcome.APPROVE: "approved",
    Outcome.APPROVE_WITH_CONDITIONS: "approve_with_conditions",
    Outcome.DEFER: "deferred",
    Outcome.REJECT: "rejected",
}


def precedent_row(record: PrecedentRecordIn) -> dict[str, Any]:
    """The document ``record`` is stored as (the seed's precedent shape)."""
    return precedent_document(
        case_id=record.case_id,
        title=record.title,
        unit=record.unit or "",
        change_types=record.change_types,
        domains=record.domains,
        geographies=record.geographies,
        closed_date=record.closed_date,
        outcome=OUTCOME_STORED[record.outcome],
        band=record.band,
        control_ratings=record.control_ratings,
    )


def content_hash(row: Mapping[str, Any]) -> str:
    """A digest of what a precedent says (its ``_id`` and the hash itself
    excluded), so an identical repost is recognised and a changed one is not.
    """
    content = {k: v for k, v in row.items() if k not in ("_id", "content_hash")}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def index_record(doc: Mapping[str, Any]) -> PrecedentIndexRecord:
    """One stored precedent as H4 embeds it."""
    return PrecedentIndexRecord(
        case_id=doc["case_id"],
        title=doc["title"],
        change_types=list(doc.get("change_types", [])),
        domains=list(doc.get("domains", [])),
        geographies=list(doc.get("geographies", [])),
        closed_date=str(doc.get("closed_date", "")),
        outcome=doc.get("outcome"),
        band=doc.get("band"),
        search_text=str(doc.get("search_text", "")),
    )


class PrecedentStore(Protocol):
    """What the precedent routes depend on."""

    async def page(
        self, *, cursor: Optional[str], limit: int
    ) -> Page[PrecedentIndexRecord]:
        """One page of precedents in ``case_id`` order."""
        ...

    async def record(self, record: PrecedentRecordIn) -> PrecedentRecordOut:
        """Store ``record`` and, if it is new or changed, request a rebuild of
        the ``precedent_cases`` index, atomically.
        """
        ...

    async def count(self) -> int:
        """How many precedents are stored (the precedent index version)."""
        ...


class InMemoryPrecedentStore:
    """The unit-test stand-in: rows in a dict and the events that would have
    been enqueued in ``events``.
    """

    def __init__(self, docs: Sequence[Mapping[str, Any]] = ()) -> None:
        """Hold ``docs`` (``fcrm_policy.seedmodel.precedent_documents``)."""
        self._rows: dict[str, dict[str, Any]] = {str(d["_id"]): dict(d) for d in docs}
        self.events: list[Envelope] = []

    async def count(self) -> int:
        """Number of rows held."""
        return len(self._rows)

    async def page(
        self, *, cursor: Optional[str], limit: int
    ) -> Page[PrecedentIndexRecord]:
        """Seek pagination by case id, like the Mongo store."""
        after = decode_cursor(cursor) if cursor is not None else None
        ids = sorted(i for i in self._rows if after is None or i > after)
        window = ids[:limit]
        return Page(
            items=[index_record(self._rows[i]) for i in window],
            next_cursor=encode_cursor(window[-1]) if len(ids) > limit else None,
            total=len(self._rows),
        )

    async def record(self, record: PrecedentRecordIn) -> PrecedentRecordOut:
        """Upsert like the Mongo store; enqueue only on a new or changed row."""
        row = precedent_row(record)
        digest = content_hash(row)
        existing = self._rows.get(record.case_id)
        created = existing is None
        changed = not created and (existing or {}).get("content_hash") != digest
        requested = created or changed
        if requested:
            self._rows[record.case_id] = {**row, "content_hash": digest}
            self.events.append(
                index_build_envelope(
                    "precedent_cases",
                    len(self._rows),
                    discriminator=digest,
                    case_id=record.case_id,
                    mode="incremental",
                )
            )
        return PrecedentRecordOut(
            case_id=record.case_id,
            created=created,
            changed=changed,
            index_requested=requested,
            index_version=len(self._rows),
        )


class MongoPrecedentStore:
    """The real store over ``precedents``, writing its outbox rows in the same
    transaction as the precedent.

    Args:
        client: The Mongo client (transactions need a replica set).
        collection: The ``precedents`` collection.
        outbox: S3's outbox.
    """

    def __init__(
        self,
        client: AsyncMongoClient[Document],
        collection: AsyncCollection[Document],
        outbox: Outbox,
    ) -> None:
        self._client = client
        self._collection = collection
        self._outbox = outbox

    async def count(self) -> int:
        """Number of precedents stored."""
        return await self._collection.count_documents({})

    async def page(
        self, *, cursor: Optional[str], limit: int
    ) -> Page[PrecedentIndexRecord]:
        """Seek pagination by ``_id`` (the case id)."""
        query: dict[str, Any] = {}
        if cursor is not None:
            query = {"_id": {"$gt": decode_cursor(cursor)}}
        docs = await self._collection.find(
            query, sort=[("_id", ASCENDING)], limit=limit + 1
        ).to_list()
        window = docs[:limit]
        next_cursor = encode_cursor(window[-1]["_id"]) if len(docs) > limit else None
        return Page(
            items=[index_record(d) for d in window],
            next_cursor=next_cursor,
            total=await self._collection.count_documents({}),
        )

    async def record(self, record: PrecedentRecordIn) -> PrecedentRecordOut:
        """One transaction: read the row, replace it if it is new or changed,
        and enqueue the ``index.build`` for it.
        """
        row = precedent_row(record)
        digest = content_hash(row)

        async def txn(session: AsyncClientSession) -> PrecedentRecordOut:
            existing = await self._collection.find_one(
                {"_id": record.case_id}, session=session
            )
            created = existing is None
            changed = existing is not None and existing.get("content_hash") != digest
            requested = created or changed
            if requested:
                await self._collection.replace_one(
                    {"_id": record.case_id},
                    {**row, "content_hash": digest},
                    upsert=True,
                    session=session,
                )
            version = await self._collection.count_documents({}, session=session)
            if requested:
                await self._outbox.enqueue(
                    session,
                    index_build_envelope(
                        PRECEDENT_CASES,
                        version,
                        discriminator=digest,
                        case_id=record.case_id,
                        mode="incremental",
                    ),
                )
            return PrecedentRecordOut(
                case_id=record.case_id,
                created=created,
                changed=changed,
                index_requested=requested,
                index_version=version,
            )

        stored = await with_transaction(self._client, txn)
        logger.info(
            "precedent recorded",
            extra={
                "case_id": record.case_id,
                "precedent_created": stored.created,
                "precedent_changed": stored.changed,
            },
        )
        return stored


__all__ = [
    "DEFAULT_PRECEDENT_PAGE",
    "MAX_PRECEDENT_PAGE",
    "OUTCOME_STORED",
    "InMemoryPrecedentStore",
    "MongoPrecedentStore",
    "PrecedentStore",
    "content_hash",
    "index_record",
    "precedent_row",
]

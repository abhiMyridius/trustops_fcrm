"""The ``activity`` collection: S1's per-case, replayable event log.

Every row has a ``seq`` that is monotonic and gap-free *per case*: it is the
value of ``cases.activity_seq`` after an ``$inc`` that runs in the same
transaction as the insert, so two writers to one case serialise on the case
document (the loser's transaction is retried by
:func:`fcrm_common.mongo.with_transaction`), a row is never visible before
its predecessor, and a client that remembers the last ``seq`` it saw can ask
for everything after it. Events are persisted before they are published to the
in-process hub (:mod:`fcrm_case.sse`), so a stream that missed one replays it
from here.

Two shapes of write:

* :meth:`MongoActivityLog.append` is a standalone write (``POST
  /internal/cases/{id}/events``): one transaction, ``$inc`` then insert.
* :meth:`MongoActivityLog.stage` is the insert half, for a store method that
  already ``$inc``-ed ``activity_seq`` in its own update (``PATCH state``,
  ``assign``), so the case write, the row and any outbox events commit
  together.

:class:`InMemoryActivityLog` is the unit-test twin.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Optional, Protocol

from fcrm_common.ids import new_id
from fcrm_common.mongo import Document, from_bson, to_bson, with_transaction
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import UiEventIn
from pymongo import ASCENDING, AsyncMongoClient, IndexModel, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_case.domain.activity import VISIBLE_TO, ActivityEntry, Audience

logger = logging.getLogger(__name__)

#: Rows per replay page: a stream pages until it gets fewer than this.
REPLAY_PAGE = 500


class ActivityLog(Protocol):
    """What the stream and ``POST /internal/cases/{id}/events`` need."""

    async def ensure_indexes(self) -> None:
        """Create the indexes the replay queries use."""
        ...

    async def append(self, case_id: str, event: UiEventIn) -> Optional[ActivityEntry]:
        """Allocate the next ``seq`` and persist ``event``; ``None`` when the
        case does not exist. The caller publishes the returned entry.
        """
        ...

    async def since(
        self,
        case_id: str,
        after_seq: int,
        *,
        audience: Optional[Audience] = None,
        limit: int = REPLAY_PAGE,
    ) -> list[ActivityEntry]:
        """Up to ``limit`` entries with ``seq > after_seq``, oldest first. With
        an ``audience`` only the visibilities that audience may read; the
        type and payload fence (``fcrm_case.domain.activity.visible_to``) is
        the caller's.
        """
        ...


def entry_document(entry: ActivityEntry) -> Document:
    """The BSON document stored for ``entry``."""
    doc: Document = to_bson(entry.model_dump(mode="python"))
    doc["_id"] = new_id("act")
    return doc


def entry_from_document(doc: Document) -> ActivityEntry:
    """The entry stored in ``doc``."""
    data = from_bson(dict(doc))
    data.pop("_id", None)
    return ActivityEntry.model_validate(data)


def build_entry(case_id: str, seq: int, event: UiEventIn) -> ActivityEntry:
    """``event`` placed at ``seq`` of ``case_id``, stamped now."""
    return ActivityEntry(case_id=case_id, seq=seq, at=utcnow(), **event.model_dump())


class MongoActivityLog:
    """The Mongo-backed log over ``fcrm_case.activity``."""

    def __init__(
        self,
        client: AsyncMongoClient[Any],
        cases: AsyncCollection[Document],
        activity: AsyncCollection[Document],
    ) -> None:
        self._client = client
        self._cases = cases
        self._activity = activity

    async def ensure_indexes(self) -> None:
        """``{case_id, seq}`` unique (replay and the seq invariant) and
        ``{case_id, visibility, seq}`` (the PO-filtered feed).
        """
        await self._activity.create_indexes(
            [
                IndexModel(
                    [("case_id", ASCENDING), ("seq", ASCENDING)],
                    unique=True,
                    name="case_seq_unique",
                ),
                IndexModel(
                    [
                        ("case_id", ASCENDING),
                        ("visibility", ASCENDING),
                        ("seq", ASCENDING),
                    ],
                    name="case_visibility_seq",
                ),
            ]
        )

    async def stage(
        self, session: AsyncClientSession, case_id: str, seq: int, event: UiEventIn
    ) -> ActivityEntry:
        """Insert ``event`` at ``seq`` inside the caller's transaction (the
        caller has already ``$inc``-ed ``cases.activity_seq`` to ``seq``).
        """
        entry = build_entry(case_id, seq, event)
        await self._activity.insert_one(entry_document(entry), session=session)
        return entry

    async def append_in(
        self,
        session: Optional[AsyncClientSession],
        case_id: str,
        event: UiEventIn,
    ) -> Optional[ActivityEntry]:
        """``$inc`` the case's ``activity_seq`` and insert the row, inside the
        caller's transaction (``session``), or in a transaction of its own when
        ``session`` is ``None``. ``None`` when the case does not exist.
        """
        if session is None:
            return await self.append(case_id, event)
        doc = await self._cases.find_one_and_update(
            {"_id": case_id},
            {"$inc": {"activity_seq": 1}},
            projection={"activity_seq": 1},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if doc is None:
            return None
        return await self.stage(session, case_id, int(doc["activity_seq"]), event)

    async def append(self, case_id: str, event: UiEventIn) -> Optional[ActivityEntry]:
        """One transaction: ``$inc`` the case's ``activity_seq``, insert the row."""

        async def _do(session: AsyncClientSession) -> Optional[ActivityEntry]:
            return await self.append_in(session, case_id, event)

        return await with_transaction(self._client, _do)

    async def since(
        self,
        case_id: str,
        after_seq: int,
        *,
        audience: Optional[Audience] = None,
        limit: int = REPLAY_PAGE,
    ) -> list[ActivityEntry]:
        """See :meth:`ActivityLog.since`."""
        query: dict[str, Any] = {"case_id": case_id, "seq": {"$gt": after_seq}}
        if audience is not None:
            query["visibility"] = {"$in": list(VISIBLE_TO[audience])}
        cursor = self._activity.find(
            query, projection={"_id": 0}, sort=[("seq", ASCENDING)], limit=limit
        )
        return [entry_from_document(doc) for doc in await cursor.to_list()]


class InMemoryActivityLog:
    """The test twin. ``bump`` allocates the next ``seq`` for a case (the
    in-memory case store increments the record's ``activity_seq``) and
    returns ``None`` when the case does not exist.
    """

    def __init__(self, bump: Callable[[str], Optional[int]]) -> None:
        self._bump = bump
        self.entries: list[ActivityEntry] = []

    async def ensure_indexes(self) -> None:
        """Nothing to create."""

    def stage(self, case_id: str, seq: int, event: UiEventIn) -> ActivityEntry:
        """Record ``event`` at ``seq`` (the caller already bumped it)."""
        entry = build_entry(case_id, seq, event)
        self.entries.append(entry)
        return entry

    async def append(self, case_id: str, event: UiEventIn) -> Optional[ActivityEntry]:
        """See :meth:`ActivityLog.append`."""
        seq = self._bump(case_id)
        return None if seq is None else self.stage(case_id, seq, event)

    async def append_in(
        self,
        session: Optional[AsyncClientSession],
        case_id: str,
        event: UiEventIn,
    ) -> Optional[ActivityEntry]:
        """:meth:`append`; there is no transaction to join in memory."""
        return await self.append(case_id, event)

    async def since(
        self,
        case_id: str,
        after_seq: int,
        *,
        audience: Optional[Audience] = None,
        limit: int = REPLAY_PAGE,
    ) -> list[ActivityEntry]:
        """See :meth:`ActivityLog.since`."""
        allowed = VISIBLE_TO[audience] if audience is not None else None
        rows = [
            e
            for e in self.entries
            if e.case_id == case_id
            and e.seq > after_seq
            and (allowed is None or e.visibility in allowed)
        ]
        return sorted(rows, key=lambda e: e.seq)[:limit]

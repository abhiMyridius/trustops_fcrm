"""S1's watchlist re-flag store.

Same split as :mod:`fcrm_case.outcome_store`: a small :class:`ReflagStore`
``Protocol`` the routes depend on, a Mongo implementation the lifespan wires and
an in-memory twin for unit tests. One collection in ``fcrm_case``:

=============  ==============================================================
``reflags``    ``_id = reflag_id`` (derived by H1 from the list version, the
               case and the jurisdiction); the proposal, the list entry it
               cites, the harness run and gate that decide it, and, once an
               analyst acknowledged it, who and why. ``{created_at, _id}``
               serves the paged list; ``{case_id}`` serves a case's own.
=============  ==============================================================

**Idempotent create.** H1 posts a list version's re-flags and may post them
again (a redelivered step): each row is an upsert on its deterministic id with
``$setOnInsert``, so a second post creates nothing and a row an analyst already
acknowledged is never touched.

**Acknowledge: store, then emit, in one transaction.** The row's change and the
``gate.decided`` event (through the outbox) commit together or not at all, so H1
never hears of an acknowledgement that was not stored and a stored one is never
silent. Acknowledging does not look at the case's stage: a re-flag on a closed
case is exactly the point. Nothing here changes a score or reopens a case.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Optional, Protocol

from fcrm_common.errors import ConflictError
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.mongo import to_bson, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_reflags import ReflagAcknowledge, ReflagOut
from fcrm_contracts.api.case_internal_reflags import (
    ReflagCandidate,
    ReflagIn,
    ReflagsPosted,
)
from fcrm_contracts.common import Page
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.tools.case_search import CaseSearchHit
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.reflags import (
    CORRIDOR_LABEL,
    MAX_CANDIDATES,
    CaseExposure,
    ReflagRow,
    acknowledged_envelope,
    corridor_regex,
    countries_matched,
    reflag_row,
)

logger = logging.getLogger(__name__)

DEFAULT_PAGE_LIMIT: Final = 50
MAX_PAGE_LIMIT: Final = 200


class ReflagAlreadyAcknowledgedError(ConflictError):
    """The re-flag was acknowledged already, with a different reason."""

    code = "reflag_already_acknowledged"
    title = "Re-flag already acknowledged"


@dataclass(frozen=True)
class AckWrite:
    """What an acknowledgement did: the row afterwards and whether it replayed an
    earlier identical acknowledgement (nothing written, nothing emitted).
    """

    row: ReflagRow
    replayed: bool = False


class ReflagStore(Protocol):
    """What the re-flag routes need."""

    async def ensure_indexes(self) -> None:
        """Create the indexes the queries below rely on (idempotent)."""
        ...

    async def candidates(self, countries: Sequence[str]) -> list[ReflagCandidate]:
        """Open cases exposed to any of ``countries``, one entry per case and
        country, ordered by case id then country (at most
        :data:`~fcrm_case.domain.reflags.MAX_CANDIDATES`).
        """
        ...

    async def search_cases(
        self,
        query: str,
        *,
        geography: Optional[str],
        change_types: Sequence[str],
        closed_only: bool,
        top_k: int,
    ) -> list[CaseSearchHit]:
        """Cases for the agents' ``case_search`` tool: those exposed to
        ``geography`` (code match), else those whose title holds ``query``;
        narrowed to ``change_types``; closed ones only when ``closed_only``.
        Best match first, at most ``top_k``; ``outcome`` is ``None`` exactly for
        an open case.
        """
        ...

    async def create_many(self, proposed: Sequence[ReflagIn]) -> ReflagsPosted:
        """Hold ``proposed``, once each (a repeat creates nothing).

        Raises:
            fcrm_common.errors.ValidationError: A re-flag names a case S1 does
                not hold.
        """
        ...

    async def list_page(self, *, cursor: Optional[str], limit: int) -> Page[ReflagOut]:
        """One page of re-flags, newest first."""
        ...

    async def get(self, reflag_id: str) -> Optional[ReflagRow]:
        """One re-flag, or ``None``."""
        ...

    async def acknowledge(
        self, reflag_id: str, body: ReflagAcknowledge, *, by: str
    ) -> Optional[AckWrite]:
        """Store the acknowledgement and enqueue ``gate.decided`` atomically;
        ``None`` when no such re-flag.

        Raises:
            ReflagAlreadyAcknowledgedError: It was acknowledged with another
                reason.
        """
        ...


def _page_bounds(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_LIMIT))


def _sort_key(row: ReflagRow) -> tuple[float, str]:
    """Newest first, then id: the order of both stores' pages."""
    return (-row.created_at.timestamp(), row.reflag_id)


def _cursor_of(row: ReflagRow) -> str:
    return encode_cursor([row.created_at, row.reflag_id])


def _after_cursor(cursor: str) -> tuple[datetime, str]:
    """The ``(created_at, id)`` a page continues after.

    Raises:
        ValidationError: ``cursor`` is not one this store produced.
    """
    decoded = decode_cursor(cursor)
    if (
        not isinstance(decoded, list)
        or len(decoded) != 2
        or not isinstance(decoded[0], datetime)
        or not isinstance(decoded[1], str)
    ):
        raise FcrmValidationError(
            "Invalid pagination cursor.",
            errors=[{"loc": "query.cursor", "msg": "malformed cursor"}],
        )
    return decoded[0], decoded[1]


def _candidates_of(
    cases: Sequence[CaseExposure],
    corridors: dict[str, list[str]],
    countries: Sequence[str],
) -> list[ReflagCandidate]:
    found = [
        ReflagCandidate(
            case_id=case.case_id,
            title=case.title,
            stage=case.stage,
            jurisdiction=country,
        )
        for case in cases
        for country in countries_matched(
            case.geographies, corridors.get(case.case_id, []), countries
        )
    ]
    found.sort(key=lambda c: (c.case_id, c.jurisdiction))
    return found[:MAX_CANDIDATES]


class InMemoryReflagStore:
    """The unit tests' store: cases and rows in dicts, ``events`` standing in
    for the outbox. Every method is atomic under asyncio (no ``await`` between
    a read and its write).

    Args:
        cases: The cases S1 holds, with their geographies.
        corridors: Corridor field values by case id.
    """

    def __init__(
        self,
        cases: Sequence[CaseRecord] = (),
        corridors: Optional[dict[str, list[str]]] = None,
    ) -> None:
        self.cases: dict[str, CaseRecord] = {c.case_id: c for c in cases}
        self.corridors: dict[str, list[str]] = dict(corridors or {})
        self.rows: dict[str, ReflagRow] = {}
        self.events: list[Envelope] = []

    async def ensure_indexes(self) -> None:
        return None

    def _open_cases(self) -> list[CaseExposure]:
        return [
            CaseExposure(c.case_id, c.title, c.state.value, tuple(c.geographies))
            for c in self.cases.values()
            if c.state.value != "closed" and c.discarded_at is None
        ]

    async def candidates(self, countries: Sequence[str]) -> list[ReflagCandidate]:
        return _candidates_of(self._open_cases(), self.corridors, countries)

    async def search_cases(
        self,
        query: str,
        *,
        geography: Optional[str],
        change_types: Sequence[str],
        closed_only: bool,
        top_k: int,
    ) -> list[CaseSearchHit]:
        hits: list[CaseSearchHit] = []
        for case in sorted(self.cases.values(), key=lambda c: c.case_id):
            stage = case.state.value
            if case.discarded_at is not None or (closed_only and stage != "closed"):
                continue
            if change_types and not set(change_types) & {
                t.value for t in case.change_types
            }:
                continue
            if geography is not None:
                matched = countries_matched(
                    case.geographies, self.corridors.get(case.case_id, []), [geography]
                )
            else:
                matched = (
                    [query] if query.strip().casefold() in case.title.casefold() else []
                )
            if matched:
                hits.append(
                    CaseSearchHit(
                        case_id=case.case_id,
                        title=case.title,
                        similarity=1.0,
                        outcome=_outcome_of(
                            stage, case.outcome_ref, case.outcome_summary
                        ),
                    )
                )
        return hits[:top_k]

    async def create_many(self, proposed: Sequence[ReflagIn]) -> ReflagsPosted:
        unknown = sorted({p.case_id for p in proposed if p.case_id not in self.cases})
        if unknown:
            raise _unknown_cases(unknown)
        now = utcnow()
        created = 0
        for item in proposed:
            if item.reflag_id in self.rows:
                continue
            title = self.cases[item.case_id].title
            self.rows[item.reflag_id] = reflag_row(item, title, now)
            created += 1
        return ReflagsPosted(
            created=created,
            replayed=len(proposed) - created,
            reflag_ids=[p.reflag_id for p in proposed],
        )

    async def list_page(self, *, cursor: Optional[str], limit: int) -> Page[ReflagOut]:
        size = _page_bounds(limit)
        ordered = sorted(self.rows.values(), key=_sort_key)
        if cursor is not None:
            after_at, after_id = _after_cursor(cursor)
            marker = (-after_at.timestamp(), after_id)
            ordered = [r for r in ordered if _sort_key(r) > marker]
        window = ordered[: size + 1]
        items = window[:size]
        more = len(window) > size
        return Page[ReflagOut](
            items=[r.out() for r in items],
            next_cursor=_cursor_of(items[-1]) if more and items else None,
            total=len(self.rows),
        )

    async def get(self, reflag_id: str) -> Optional[ReflagRow]:
        return self.rows.get(reflag_id)

    async def acknowledge(
        self, reflag_id: str, body: ReflagAcknowledge, *, by: str
    ) -> Optional[AckWrite]:
        row = self.rows.get(reflag_id)
        if row is None:
            return None
        reason = body.reason or ""
        if row.acknowledged:
            return _replay_or_refuse(row, reason)
        now = utcnow()
        updated = row.model_copy(
            update={
                "acknowledged": True,
                "acknowledged_by": by,
                "acknowledged_at": now,
                "ack_reason": reason,
                "ack_rerun": body.rerun,
            }
        )
        self.rows[reflag_id] = updated
        self.events.append(acknowledged_envelope(updated, by, reason, body.rerun, now))
        return AckWrite(updated)


def _outcome_of(
    stage: str, ref: Optional[str], summary: Optional[str]
) -> Optional[str]:
    """What a search hit says the case's outcome is: ``None`` for an open case,
    otherwise the recorded outcome (or that it is closed).
    """
    if stage != "closed":
        return None
    return ref or summary or "closed"


def _unknown_cases(case_ids: Sequence[str]) -> FcrmValidationError:
    return FcrmValidationError(
        "A re-flag names a case this service does not hold.",
        errors=[{"loc": "body.reflags", "msg": f"no case {c}"} for c in case_ids],
    )


def _replay_or_refuse(row: ReflagRow, reason: str) -> AckWrite:
    """An acknowledgement of an acknowledged re-flag: the same reason is a
    replay, another is a conflict.

    Raises:
        ReflagAlreadyAcknowledgedError: The stored reason differs.
    """
    if row.ack_reason == reason:
        return AckWrite(row, replayed=True)
    raise ReflagAlreadyAcknowledgedError(
        "This re-flag was already acknowledged.", reflag_id=row.reflag_id
    )


class MongoReflagStore:
    """The real store over ``reflags`` (and a read of ``cases`` and
    ``field_values`` for the candidate match).

    Args:
        client: The Mongo client (the row and the outbox event commit in one
            transaction).
        db: The ``fcrm_case`` database.
        outbox: S1's outbox.
    """

    def __init__(self, client: AsyncMongoClient[Any], db: Any, outbox: Outbox) -> None:
        self._client = client
        self._outbox = outbox
        self._reflags: AsyncCollection[Any] = db["reflags"]
        self._cases: AsyncCollection[Any] = db["cases"]
        self._fields: AsyncCollection[Any] = db["field_values"]

    async def ensure_indexes(self) -> None:
        """``{created_at, _id}`` (the paged list) and ``{case_id}`` (a case's
        re-flags).
        """
        await self._reflags.create_indexes(
            [
                IndexModel(
                    [("created_at", DESCENDING), ("_id", ASCENDING)],
                    name="created_id",
                ),
                IndexModel([("case_id", ASCENDING)], name="case_idx"),
            ]
        )

    async def candidates(self, countries: Sequence[str]) -> list[ReflagCandidate]:
        names = [c.strip() for c in countries if c.strip()]
        if not names:
            return []
        corridors = await self._corridor_values(names)
        query: dict[str, Any] = {
            "state": {"$ne": "closed"},
            "discarded_at": None,
            "$or": [
                {"geographies": {"$in": names}},
                {"case_id": {"$in": list(corridors)}},
            ],
        }
        # ``geographies`` is matched ignoring case in code; the prefilter keeps
        # the exact spelling the seed and the list share and the corridor ids.
        cursor = self._cases.find(
            query,
            projection={"case_id": 1, "title": 1, "state": 1, "geographies": 1},
            limit=MAX_CANDIDATES * 2,
        )
        exposures = [
            CaseExposure(
                doc["case_id"],
                doc["title"],
                str(doc["state"]),
                tuple(doc.get("geographies") or ()),
            )
            async for doc in cursor
        ]
        return _candidates_of(exposures, corridors, names)

    async def search_cases(
        self,
        query: str,
        *,
        geography: Optional[str],
        change_types: Sequence[str],
        closed_only: bool,
        top_k: int,
    ) -> list[CaseSearchHit]:
        base: dict[str, Any] = {"discarded_at": None}
        if closed_only:
            base["state"] = "closed"
        if change_types:
            base["change_types"] = {"$in": list(change_types)}
        corridors: dict[str, list[str]] = {}
        if geography is not None:
            corridors = await self._corridor_values([geography])
            base["$or"] = [
                {"geographies": geography},
                {"case_id": {"$in": list(corridors)}},
            ]
        else:
            base["title"] = {"$regex": re.escape(query.strip()), "$options": "i"}
        cursor = self._cases.find(
            base,
            projection={
                "case_id": 1,
                "title": 1,
                "state": 1,
                "geographies": 1,
                "outcome_ref": 1,
                "outcome_summary": 1,
            },
            sort=[("case_id", ASCENDING)],
            limit=MAX_CANDIDATES,
        )
        hits: list[CaseSearchHit] = []
        async for doc in cursor:
            if geography is not None and not countries_matched(
                doc.get("geographies") or (),
                corridors.get(doc["case_id"], []),
                [geography],
            ):
                continue
            hits.append(
                CaseSearchHit(
                    case_id=doc["case_id"],
                    title=doc["title"],
                    similarity=1.0,
                    outcome=_outcome_of(
                        str(doc["state"]),
                        doc.get("outcome_ref"),
                        doc.get("outcome_summary"),
                    ),
                )
            )
        return hits[:top_k]

    async def _corridor_values(self, names: Sequence[str]) -> dict[str, list[str]]:
        """Corridor field text mentioning any of ``names``, by case id (one
        query on ``field_values``; the exact match is :func:`countries_matched`).
        """
        cursor = self._fields.find(
            {
                "label": {"$regex": CORRIDOR_LABEL, "$options": "i"},
                "value": {"$regex": corridor_regex(names), "$options": "i"},
            },
            projection={"case_id": 1, "value": 1},
            limit=MAX_CANDIDATES * 2,
        )
        found: dict[str, list[str]] = {}
        async for doc in cursor:
            found.setdefault(doc["case_id"], []).append(doc["value"])
        return found

    async def create_many(self, proposed: Sequence[ReflagIn]) -> ReflagsPosted:
        ids = sorted({p.case_id for p in proposed})
        titles = {
            doc["case_id"]: doc["title"]
            async for doc in self._cases.find(
                {"case_id": {"$in": ids}}, projection={"case_id": 1, "title": 1}
            )
        }
        unknown = [c for c in ids if c not in titles]
        if unknown:
            raise _unknown_cases(unknown)

        async def _do(session: AsyncClientSession) -> int:
            now = utcnow()
            created = 0
            for item in proposed:
                row = reflag_row(item, titles[item.case_id], now)
                body = to_bson(row.model_dump(mode="python"))
                result = await self._reflags.update_one(
                    {"_id": item.reflag_id},
                    {"$setOnInsert": body},
                    upsert=True,
                    session=session,
                )
                created += 1 if result.upserted_id is not None else 0
            return created

        created = await with_transaction(self._client, _do)
        logger.info(
            "re-flags held",
            extra={
                "reflags_created": created,
                "reflags_replayed": len(proposed) - created,
            },
        )
        return ReflagsPosted(
            created=created,
            replayed=len(proposed) - created,
            reflag_ids=[p.reflag_id for p in proposed],
        )

    async def list_page(self, *, cursor: Optional[str], limit: int) -> Page[ReflagOut]:
        size = _page_bounds(limit)
        query: dict[str, Any] = {}
        if cursor is not None:
            after_at, after_id = _after_cursor(cursor)
            query = {
                "$or": [
                    {"created_at": {"$lt": after_at}},
                    {"created_at": after_at, "_id": {"$gt": after_id}},
                ]
            }
        docs = [
            doc
            async for doc in self._reflags.find(
                query,
                sort=[("created_at", DESCENDING), ("_id", ASCENDING)],
                limit=size + 1,
            )
        ]
        rows = [self._row(doc) for doc in docs[:size]]
        more = len(docs) > size
        return Page[ReflagOut](
            items=[r.out() for r in rows],
            next_cursor=_cursor_of(rows[-1]) if more and rows else None,
            total=await self._reflags.count_documents({}),
        )

    @staticmethod
    def _row(doc: dict[str, Any]) -> ReflagRow:
        body = {k: v for k, v in doc.items() if k != "_id"}
        return ReflagRow.model_validate(body)

    async def get(self, reflag_id: str) -> Optional[ReflagRow]:
        doc = await self._reflags.find_one({"_id": reflag_id})
        return self._row(doc) if doc is not None else None

    async def acknowledge(
        self, reflag_id: str, body: ReflagAcknowledge, *, by: str
    ) -> Optional[AckWrite]:
        reason = body.reason or ""

        async def _do(session: AsyncClientSession) -> Optional[AckWrite]:
            doc = await self._reflags.find_one({"_id": reflag_id}, session=session)
            if doc is None:
                return None
            row = self._row(doc)
            if row.acknowledged:
                return _replay_or_refuse(row, reason)
            now = utcnow()
            updated_doc = await self._reflags.find_one_and_update(
                {"_id": reflag_id, "acknowledged": False},
                {
                    "$set": {
                        "acknowledged": True,
                        "acknowledged_by": by,
                        "acknowledged_at": now,
                        "ack_reason": reason,
                        "ack_rerun": body.rerun,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated_doc is None:
                raise ConflictError(
                    "The re-flag changed while it was being acknowledged.",
                    reflag_id=reflag_id,
                )
            updated = self._row(updated_doc)
            await self._outbox.enqueue(
                session, acknowledged_envelope(updated, by, reason, body.rerun, now)
            )
            return AckWrite(updated)

        return await with_transaction(self._client, _do)

"""The audit-chain store S4's routes read and write through.

Same shape as S2's ``ConfigStore`` (``fcrm_scoring/store.py``): a small
``Protocol``, not a concrete Mongo dependency, so the route layer
(``routes.py``) and its unit tests never need a database — they take
whatever implements :class:`AuditStore`, and the real app wires
:class:`MongoAuditStore` at startup while tests wire
:class:`InMemoryAuditStore`.

Append-only by construction, not just by convention: neither
implementation below exposes an update or delete method for a record
once it is built, and :class:`MongoAuditStore` connects through the
``fcrm_audit_writer`` credential, which MongoDB itself restricts to
``find``/``insert`` on ``audit_records`` (``ops/mongo/init/02-users.js``;
the chain-core docstring in ``chain.py`` and ``docs/governance/
audit-chain.md`` call this "insert-only at the database layer").
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from fcrm_common.mongo import Document, Repository, with_transaction
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_common.time import utcnow
from fcrm_contracts.api.audit import AuditRecord, RecordAppend, VerifyResult
from fcrm_contracts.common import Page
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession

from fcrm_audit.chain import GENESIS_HASH, build_record, verify_chain

DEFAULT_PAGE_LIMIT = 50
_MICROS_PER_MS = 1000


def _mongo_safe_now() -> datetime:
    """UTC now, truncated to millisecond precision.

    BSON stores a datetime as milliseconds-since-epoch (int64), not
    microseconds, so MongoDB silently drops the last three digits of a
    Python datetime's microsecond field on every write. A record hashed
    with the full microsecond-precision ``created_at`` would then never
    reproduce its own hash once read back from Mongo -- caught live, not
    in a fake: the transactional-append test in ``tests/unit/services/
    audit/test_audit_mongo_live.py`` failed ``verify_chain`` at ``seq=1`` with
    exactly this symptom before every ``created_at`` used in a hash went
    through this function instead of :func:`fcrm_common.time.utcnow`
    directly. Used by both stores below (not just the Mongo-backed one)
    so the two implementations stay behaviourally identical.
    """
    now = utcnow()
    return now.replace(microsecond=(now.microsecond // _MICROS_PER_MS) * _MICROS_PER_MS)


class AuditStore(Protocol):
    """What ``routes.py`` depends on; see the module docstring."""

    async def append(self, record: RecordAppend) -> AuditRecord: ...

    async def records_for_case(
        self,
        case_id: str,
        *,
        cursor: Optional[str] = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> Page[AuditRecord]: ...

    async def verify(self, case_id: str) -> VerifyResult: ...


class InMemoryAuditStore:
    """Used by unit tests (``test_audit_routes.py``): one in-process chain per
    case id, built through the same :func:`fcrm_audit.chain.build_record`
    the real store uses, so route-layer tests exercise real chain
    arithmetic, not a fake shortcut.
    """

    def __init__(self, records: Optional[list[AuditRecord]] = None) -> None:
        self._chains: dict[str, list[AuditRecord]] = {}
        for record in sorted(records or [], key=lambda r: (r.case_id, r.seq)):
            self._chains.setdefault(record.case_id, []).append(record)

    async def append(self, record: RecordAppend) -> AuditRecord:
        chain = self._chains.setdefault(record.case_id, [])
        last = chain[-1] if chain else None
        seq = last.seq + 1 if last is not None else 1
        prev_hash = last.hash if last is not None else GENESIS_HASH
        built = build_record(
            record, seq=seq, prev_hash=prev_hash, created_at=_mongo_safe_now()
        )
        chain.append(built)
        return built

    async def records_for_case(
        self,
        case_id: str,
        *,
        cursor: Optional[str] = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> Page[AuditRecord]:
        chain = self._chains.get(case_id, [])
        start = 0
        if cursor is not None:
            after_seq = decode_cursor(cursor)
            start = len(chain)
            for i, record in enumerate(chain):
                if record.seq == after_seq:
                    start = i + 1
                    break
        page = chain[start : start + limit]
        has_more = start + limit < len(chain)
        next_cursor = encode_cursor(page[-1].seq) if has_more and page else None
        return Page(items=page, next_cursor=next_cursor, total=len(chain))

    async def verify(self, case_id: str) -> VerifyResult:
        return verify_chain(case_id, self._chains.get(case_id, []))


class MongoAuditStore:
    """The real store: ``fcrm_audit.audit_records``, keyed by MongoDB's own
    ``ObjectId`` (not a composite ``{case_id}:{seq}`` key — see the
    ``records_for_case`` docstring below for why that choice matters for
    pagination), with uniqueness on ``{case_id, seq}`` enforced by the
    index ``ops/mongo/init/04-indexes.js`` creates (this service's own
    Mongo credential cannot create indexes itself; see ``lifespan.py``).
    """

    def __init__(
        self, records: Repository[AuditRecord], client: AsyncMongoClient[Document]
    ) -> None:
        self._records = records
        self._client = client

    async def append(self, record: RecordAppend) -> AuditRecord:
        """Read the case's current chain head and insert the next record
        inside one transaction (design A §2.4, ``docs/governance/
        audit-chain.md``): two concurrent appends for the same case both
        read the same head under snapshot isolation, compute the same
        next ``seq``, and race on the unique ``{case_id, seq}`` index --
        one insert wins, the other raises ``DuplicateKeyError`` (mapped to
        409 by ``fcrm_common.errors``), never a silent corruption.
        """

        async def _do(session: AsyncClientSession) -> AuditRecord:
            last = await self._records.find_one(
                {"case_id": record.case_id}, sort=[("seq", -1)], session=session
            )
            seq = last.seq + 1 if last is not None else 1
            prev_hash = last.hash if last is not None else GENESIS_HASH
            built = build_record(
                record, seq=seq, prev_hash=prev_hash, created_at=_mongo_safe_now()
            )
            await self._records.insert_one(built, session=session)
            return built

        return await with_transaction(self._client, _do)

    async def records_for_case(
        self,
        case_id: str,
        *,
        cursor: Optional[str] = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> Page[AuditRecord]:
        """One page of ``case_id``'s chain in ``seq`` order.

        Reuses :meth:`fcrm_common.mongo.Repository.find_paginated` as-is
        (seek pagination on ``_id``) rather than inventing a ``seq``-based
        cursor: records for one case are only ever appended here, one at a
        time, in strictly increasing ``seq`` order, so MongoDB's own
        monotonically non-decreasing ``ObjectId`` already orders a single
        case's chain identically to ``seq`` order -- the same reasoning
        that lets every other Mongo-backed collection in this repo use
        the shared cursor helper unmodified.
        """
        return await self._records.find_paginated(
            {"case_id": case_id}, limit=limit, cursor=cursor, descending=False
        )

    async def verify(self, case_id: str) -> VerifyResult:
        """Fetch the whole chain (seq ascending) and recompute it for
        real. Not paginated: a verify run needs every record to catch a
        reorder or a tampered middle record, not just one page of them.
        """
        records = await self._records.find({"case_id": case_id}, sort=[("seq", 1)])
        return verify_chain(case_id, records)

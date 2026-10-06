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
``find``/``insert`` on ``audit_records``, ``proposals``, ``verify_runs``
and ``idempotency_keys`` (``ops/mongo/init/02-users.js``; the chain-core
docstring in ``chain.py`` and ``docs/governance/audit-chain.md`` call this
"insert-only at the database layer").

Idempotency
-----------
``append`` and ``append_proposal`` take an optional ``Idempotency-Key``.
The key is stored in ``idempotency_keys`` (``_id`` = the key) in the *same*
transaction as the record or proposal it produced, carrying the case id, the
fingerprint of the request body and the resulting ``seq`` / hash. A repeat of
the key with the same body returns the stored result and writes nothing
(:attr:`Stored.replayed`); a repeat with a different body, or one made to the
other write route, is a 409. Because the role cannot update, the key is
inserted once, with its result: there is never a "pending" key to finish.
Two simultaneous requests with one key race on the key's unique ``_id`` (and
on the ``{case_id, seq}`` index): one transaction commits, the other either
sees the key on its retry or catches the duplicate-key error and replays, so
exactly one record is ever written.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Optional, Protocol, TypeVar

from fcrm_common.errors import ChainBrokenError, ConflictError
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.ids import new_id
from fcrm_common.mongo import Document, Repository, to_bson, with_transaction
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_common.time import utcnow
from fcrm_contracts.api.audit import (
    AuditRecord,
    ChainHead,
    Proposal,
    ProposalAppend,
    RecordAppend,
    VerifyResult,
)
from fcrm_contracts.common import Page
from pydantic import BaseModel
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError

from fcrm_audit.chain import (
    GENESIS_HASH,
    build_record,
    canonical_json,
    sha256_hex,
    verify_chain,
)

logger = logging.getLogger(__name__)

DEFAULT_PAGE_LIMIT = 50
MAX_APPEND_ATTEMPTS = 5
"""How many times an append retries after losing a ``{case_id, seq}`` race
(design A §2.4: "retries (max 5)")."""
_MICROS_PER_MS = 1000
_KIND_RECORD = "record"
_KIND_PROPOSAL = "proposal"

T = TypeVar("T")


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


mongo_safe_now = _mongo_safe_now
"""The public name other S4 modules write timestamps with."""


@dataclass(frozen=True)
class Stored(Generic[T]):
    """What a write returns: the stored value, and whether it was already
    there (an ``Idempotency-Key`` replay, which wrote nothing).
    """

    value: T
    replayed: bool = False


def fingerprint(body: BaseModel) -> str:
    """sha256 of the request body's canonical JSON: the identity an
    ``Idempotency-Key`` is bound to, so a key reused with another body is
    caught.
    """
    return sha256_hex(canonical_json(body.model_dump(mode="json")))


def _key_document(
    key: str, kind: str, case_id: str, body_fingerprint: str, seq: int, result: str
) -> Document:
    """The ``idempotency_keys`` row: the request's identity and its result."""
    return {
        "_id": key,
        "kind": kind,
        "case_id": case_id,
        "fingerprint": body_fingerprint,
        "seq": seq,
        "result_id": result,
        "at": _mongo_safe_now(),
    }


def check_replay(stored: Document, kind: str, body_fingerprint: str) -> None:
    """Raise unless ``stored`` (an ``idempotency_keys`` row) belongs to a
    request of the same kind with the same body.

    Raises:
        ConflictError: The key was used for a different request. The message
            names neither the other request nor its result.
    """
    if stored.get("kind") != kind or stored.get("fingerprint") != body_fingerprint:
        raise ConflictError(
            "This Idempotency-Key was already used for a different request.",
            idempotency_key_reused=True,
        )


class _CaseLocks:
    """One ``asyncio.Lock`` per case id, dropped once nobody holds it.

    Serialises same-case appends inside one process (design A §2.4) so they
    do not burn transactions racing each other; across processes the unique
    indexes still decide.
    """

    def __init__(self) -> None:
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def lock_for(self, case_id: str) -> asyncio.Lock:
        """The lock for ``case_id`` (a live reference keeps it registered)."""
        lock = self._locks.get(case_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[case_id] = lock
        return lock


class AuditStore(Protocol):
    """What ``routes.py`` depends on; see the module docstring."""

    async def append(
        self, record: RecordAppend, *, idempotency_key: Optional[str] = None
    ) -> Stored[AuditRecord]: ...

    async def append_proposal(
        self, proposal: ProposalAppend, *, idempotency_key: Optional[str] = None
    ) -> Stored[Proposal]: ...

    async def proposals(
        self,
        case_id: str,
        *,
        run_id: Optional[str] = None,
        agent: Optional[str] = None,
        kind: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> Page[Proposal]: ...

    async def head(self, case_id: str) -> ChainHead: ...

    async def records_for_case(
        self,
        case_id: str,
        *,
        cursor: Optional[str] = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> Page[AuditRecord]: ...

    async def verify(self, case_id: str) -> VerifyResult: ...


def _unknown_supersedes(proposal_id: str) -> FcrmValidationError:
    return FcrmValidationError(
        "supersedes names no proposal of this case.",
        errors=[{"loc": "body.supersedes", "msg": f"no proposal {proposal_id}"}],
    )


def _already_superseded(proposal_id: str) -> ConflictError:
    return ConflictError(f"Proposal {proposal_id} already has a successor.")


def _proposal_from(
    append: ProposalAppend, *, proposal_id: str, seq: int, created_at: datetime
) -> Proposal:
    """The stored shape of ``append``."""
    return Proposal(
        **append.model_dump(),
        proposal_id=proposal_id,
        seq=seq,
        created_at=created_at,
    )


class InMemoryAuditStore:
    """Used by unit tests (``test_audit_routes.py``): one in-process chain per
    case id, built through the same :func:`fcrm_audit.chain.build_record`
    the real store uses, so route-layer tests exercise real chain
    arithmetic, not a fake shortcut. Every method that writes has no ``await``
    between reading and writing, so each is atomic under asyncio.
    """

    def __init__(self, records: Optional[list[AuditRecord]] = None) -> None:
        self._chains: dict[str, list[AuditRecord]] = {}
        for record in sorted(records or [], key=lambda r: (r.case_id, r.seq)):
            self._chains.setdefault(record.case_id, []).append(record)
        self._proposals: dict[str, list[Proposal]] = {}
        self._keys: dict[str, Document] = {}

    def _replay(
        self, key: Optional[str], kind: str, body_fingerprint: str
    ) -> Optional[Document]:
        """The stored key row for a valid replay, ``None`` for a new key."""
        stored = self._keys.get(key) if key is not None else None
        if stored is not None:
            check_replay(stored, kind, body_fingerprint)
        return stored

    async def append(
        self, record: RecordAppend, *, idempotency_key: Optional[str] = None
    ) -> Stored[AuditRecord]:
        body_fingerprint = fingerprint(record)
        chain = self._chains.setdefault(record.case_id, [])
        replayed = self._replay(idempotency_key, _KIND_RECORD, body_fingerprint)
        if replayed is not None:
            return Stored(chain[replayed["seq"] - 1], replayed=True)
        last = chain[-1] if chain else None
        seq = last.seq + 1 if last is not None else 1
        prev_hash = last.hash if last is not None else GENESIS_HASH
        built = build_record(
            record, seq=seq, prev_hash=prev_hash, created_at=_mongo_safe_now()
        )
        chain.append(built)
        if idempotency_key is not None:
            self._keys[idempotency_key] = _key_document(
                idempotency_key,
                _KIND_RECORD,
                record.case_id,
                body_fingerprint,
                seq,
                built.hash,
            )
        return Stored(built)

    async def append_proposal(
        self, proposal: ProposalAppend, *, idempotency_key: Optional[str] = None
    ) -> Stored[Proposal]:
        body_fingerprint = fingerprint(proposal)
        ledger = self._proposals.setdefault(proposal.case_id, [])
        replayed = self._replay(idempotency_key, _KIND_PROPOSAL, body_fingerprint)
        if replayed is not None:
            return Stored(ledger[replayed["seq"] - 1], replayed=True)
        if proposal.supersedes is not None:
            if proposal.supersedes not in {p.proposal_id for p in ledger}:
                raise _unknown_supersedes(proposal.supersedes)
            if any(p.supersedes == proposal.supersedes for p in ledger):
                raise _already_superseded(proposal.supersedes)
        stored = _proposal_from(
            proposal,
            proposal_id=new_id("prp"),
            seq=len(ledger) + 1,
            created_at=_mongo_safe_now(),
        )
        ledger.append(stored)
        if idempotency_key is not None:
            self._keys[idempotency_key] = _key_document(
                idempotency_key,
                _KIND_PROPOSAL,
                proposal.case_id,
                body_fingerprint,
                stored.seq,
                stored.proposal_id,
            )
        return Stored(stored)

    async def proposals(
        self,
        case_id: str,
        *,
        run_id: Optional[str] = None,
        agent: Optional[str] = None,
        kind: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> Page[Proposal]:
        ledger = self._proposals.get(case_id, [])
        after = decode_cursor(cursor) if cursor is not None else 0
        wanted = [
            p
            for p in ledger
            if p.seq > after
            and (run_id is None or p.run_id == run_id)
            and (agent is None or p.agent == agent)
            and (kind is None or p.kind == kind)
        ]
        page = wanted[:limit]
        successors = {p.supersedes: p.proposal_id for p in ledger if p.supersedes}
        items = [
            p.model_copy(update={"superseded_by": successors.get(p.proposal_id)})
            for p in page
        ]
        next_cursor = encode_cursor(page[-1].seq) if len(wanted) > limit else None
        return Page(items=items, next_cursor=next_cursor, total=None)

    async def head(self, case_id: str) -> ChainHead:
        chain = self._chains.get(case_id, [])
        if not chain:
            return ChainHead(seq=0, hash=GENESIS_HASH)
        return ChainHead(seq=chain[-1].seq, hash=chain[-1].hash)

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


def _proposal_document(proposal: Proposal) -> Document:
    """The BSON document stored for ``proposal``: ``_id`` is the proposal id,
    ``superseded_by`` is never stored (derived on read) and ``supersedes`` is
    omitted when unset so the sparse ``{case_id, supersedes}`` index stays
    small.
    """
    doc: Document = proposal.model_dump(mode="python", exclude={"proposal_id"})
    doc.pop("superseded_by", None)
    if doc.get("supersedes") is None:
        doc.pop("supersedes", None)
    converted: Document = to_bson(doc)
    converted["_id"] = proposal.proposal_id
    return converted


def _proposal_from_document(
    doc: Document, superseded_by: Optional[str] = None
) -> Proposal:
    """Validate a stored proposal document back into a :class:`Proposal`."""
    data = dict(doc)
    data["proposal_id"] = data.pop("_id")
    data["superseded_by"] = superseded_by
    return Proposal.model_validate(data)


class MongoAuditStore:
    """The real store: ``fcrm_audit.audit_records``, keyed by MongoDB's own
    ``ObjectId`` (not a composite ``{case_id}:{seq}`` key — see the
    ``records_for_case`` docstring below for why that choice matters for
    pagination), with uniqueness on ``{case_id, seq}`` enforced by the
    index ``ops/mongo/init/04-indexes.js`` creates (this service's own
    Mongo credential cannot create indexes itself; see ``lifespan.py``).
    The proposals ledger and the idempotency keys live beside it, written
    under the same insert-only credential.
    """

    def __init__(
        self,
        records: Repository[AuditRecord],
        client: AsyncMongoClient[Document],
        *,
        proposals: AsyncCollection[Document],
        idempotency_keys: AsyncCollection[Document],
    ) -> None:
        self._records = records
        self._client = client
        self._proposals = proposals
        self._keys = idempotency_keys
        self._locks = _CaseLocks()

    async def _retrying(
        self,
        case_id: str,
        key: Optional[str],
        kind: str,
        body_fingerprint: str,
        attempt: Callable[[], Awaitable[Stored[T]]],
        replay: Callable[[Document], Awaitable[Stored[T]]],
    ) -> Stored[T]:
        """Run ``attempt`` (one transaction) for ``case_id``; on a duplicate
        key, replay the stored result when ``key`` already exists, else
        retry (another writer took the ``seq``), at most
        :data:`MAX_APPEND_ATTEMPTS` times.
        """
        async with self._locks.lock_for(case_id):
            for number in range(1, MAX_APPEND_ATTEMPTS + 1):
                try:
                    return await attempt()
                except DuplicateKeyError:
                    logger.info(
                        "append lost a duplicate-key race",
                        extra={"case_id": case_id, "attempt": number, "kind": kind},
                    )
                    stored = (
                        await self._keys.find_one({"_id": key})
                        if key is not None
                        else None
                    )
                    if stored is not None:
                        check_replay(stored, kind, body_fingerprint)
                        return await replay(stored)
        raise ConflictError(
            "Could not allocate the next sequence number; retry the request.",
            case_id=case_id,
            attempts=MAX_APPEND_ATTEMPTS,
        )

    async def append(
        self, record: RecordAppend, *, idempotency_key: Optional[str] = None
    ) -> Stored[AuditRecord]:
        """Read the case's current chain head and insert the next record
        inside one transaction (design A §2.4, ``docs/governance/
        audit-chain.md``): two concurrent appends for the same case both
        read the same head under snapshot isolation, compute the same
        next ``seq``, and race on the unique ``{case_id, seq}`` index --
        one insert wins, the other raises ``DuplicateKeyError`` and retries
        on the new head, never a silent corruption.

        With an ``idempotency_key`` the key row is inserted first in the same
        transaction; a known key replays its record instead (module docstring).

        Raises:
            ConflictError: The key was used for a different request, or the
                ``seq`` could not be allocated in :data:`MAX_APPEND_ATTEMPTS`.
        """
        body_fingerprint = fingerprint(record)

        async def _do(session: AsyncClientSession) -> Stored[AuditRecord]:
            if idempotency_key is not None:
                known = await self._keys.find_one(
                    {"_id": idempotency_key}, session=session
                )
                if known is not None:
                    check_replay(known, _KIND_RECORD, body_fingerprint)
                    return Stored(await self._stored_record(known, session), True)
            last = await self._records.find_one(
                {"case_id": record.case_id}, sort=[("seq", -1)], session=session
            )
            seq = last.seq + 1 if last is not None else 1
            prev_hash = last.hash if last is not None else GENESIS_HASH
            built = build_record(
                record, seq=seq, prev_hash=prev_hash, created_at=_mongo_safe_now()
            )
            if idempotency_key is not None:
                await self._keys.insert_one(
                    _key_document(
                        idempotency_key,
                        _KIND_RECORD,
                        record.case_id,
                        body_fingerprint,
                        seq,
                        built.hash,
                    ),
                    session=session,
                )
            await self._records.insert_one(built, session=session)
            return Stored(built)

        async def _replay(known: Document) -> Stored[AuditRecord]:
            return Stored(await self._stored_record(known, None), True)

        return await self._retrying(
            record.case_id,
            idempotency_key,
            _KIND_RECORD,
            body_fingerprint,
            lambda: with_transaction(self._client, _do),
            _replay,
        )

    async def _stored_record(
        self, known: Document, session: Optional[AsyncClientSession]
    ) -> AuditRecord:
        """The record an ``idempotency_keys`` row points at.

        Raises:
            ChainBrokenError: The row exists but its record does not (or has
                another hash): the row and the record are written in one
                transaction and neither can be deleted, so this is tampering
                or corruption, never a normal state.
        """
        stored = await self._records.find_one(
            {"case_id": known["case_id"], "seq": known["seq"]}, session=session
        )
        if stored is None or stored.hash != known["result_id"]:
            raise ChainBrokenError(
                "An idempotency key points at a record that is missing or altered.",
                case_id=known["case_id"],
                first_broken_seq=known["seq"],
            )
        return stored

    async def append_proposal(
        self, proposal: ProposalAppend, *, idempotency_key: Optional[str] = None
    ) -> Stored[Proposal]:
        """Insert ``proposal`` as the case's next ledger entry (``seq`` is
        one per case), with the same idempotency and retry behaviour as
        :meth:`append`.

        Raises:
            ValidationError: ``supersedes`` names no proposal of this case.
            ConflictError: ``supersedes`` already has a successor; the key
                was used for a different request; or no ``seq`` could be
                allocated.
        """
        body_fingerprint = fingerprint(proposal)

        async def _check_supersedes(session: AsyncClientSession) -> None:
            target = proposal.supersedes
            if target is None:
                return
            earlier = await self._proposals.find_one(
                {"_id": target, "case_id": proposal.case_id},
                projection={"_id": 1},
                session=session,
            )
            if earlier is None:
                raise _unknown_supersedes(target)
            successor = await self._proposals.find_one(
                {"case_id": proposal.case_id, "supersedes": target},
                projection={"_id": 1},
                session=session,
            )
            if successor is not None:
                raise _already_superseded(target)

        async def _do(session: AsyncClientSession) -> Stored[Proposal]:
            if idempotency_key is not None:
                known = await self._keys.find_one(
                    {"_id": idempotency_key}, session=session
                )
                if known is not None:
                    check_replay(known, _KIND_PROPOSAL, body_fingerprint)
                    return Stored(await self._stored_proposal(known, session), True)
            await _check_supersedes(session)
            last = await self._proposals.find_one(
                {"case_id": proposal.case_id},
                projection={"seq": 1},
                sort=[("seq", -1)],
                session=session,
            )
            stored = _proposal_from(
                proposal,
                proposal_id=new_id("prp"),
                seq=(last["seq"] + 1) if last is not None else 1,
                created_at=_mongo_safe_now(),
            )
            if idempotency_key is not None:
                await self._keys.insert_one(
                    _key_document(
                        idempotency_key,
                        _KIND_PROPOSAL,
                        proposal.case_id,
                        body_fingerprint,
                        stored.seq,
                        stored.proposal_id,
                    ),
                    session=session,
                )
            await self._proposals.insert_one(
                _proposal_document(stored), session=session
            )
            return Stored(stored)

        async def _replay(known: Document) -> Stored[Proposal]:
            return Stored(await self._stored_proposal(known, None), True)

        return await self._retrying(
            proposal.case_id,
            idempotency_key,
            _KIND_PROPOSAL,
            body_fingerprint,
            lambda: with_transaction(self._client, _do),
            _replay,
        )

    async def _stored_proposal(
        self, known: Document, session: Optional[AsyncClientSession]
    ) -> Proposal:
        """The proposal an ``idempotency_keys`` row points at (see
        :meth:`_stored_record` for the missing-row case).
        """
        doc = await self._proposals.find_one(
            {"_id": known["result_id"]}, session=session
        )
        if doc is None:
            raise ChainBrokenError(
                "An idempotency key points at a proposal that is missing.",
                case_id=known["case_id"],
            )
        return _proposal_from_document(doc)

    async def proposals(
        self,
        case_id: str,
        *,
        run_id: Optional[str] = None,
        agent: Optional[str] = None,
        kind: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> Page[Proposal]:
        """One page of ``case_id``'s proposals in ``seq`` order, optionally
        narrowed to a run, an agent and a kind. ``superseded_by`` comes from
        one extra ``$in`` query over the page, not one query per proposal.
        """
        query: Document = {"case_id": case_id}
        for field, value in (("run_id", run_id), ("agent", agent), ("kind", kind)):
            if value is not None:
                query[field] = value
        if cursor is not None:
            query["seq"] = {"$gt": decode_cursor(cursor)}
        docs = await (
            self._proposals.find(query).sort("seq", 1).limit(limit + 1).to_list()
        )
        page_docs = docs[:limit]
        successors = await self._successors(case_id, [d["_id"] for d in page_docs])
        next_cursor = encode_cursor(page_docs[-1]["seq"]) if len(docs) > limit else None
        return Page(
            items=[
                _proposal_from_document(d, successors.get(d["_id"])) for d in page_docs
            ],
            next_cursor=next_cursor,
            total=None,
        )

    async def _successors(self, case_id: str, ids: list[str]) -> dict[str, str]:
        """``{proposal id: id of the proposal that supersedes it}`` among ``ids``."""
        if not ids:
            return {}
        rows = await self._proposals.find(
            {"case_id": case_id, "supersedes": {"$in": ids}},
            projection={"supersedes": 1},
        ).to_list()
        return {row["supersedes"]: row["_id"] for row in rows}

    async def head(self, case_id: str) -> ChainHead:
        """The case chain's last ``seq`` and hash; ``seq=0`` and the genesis
        hash when the case has no records yet.
        """
        last = await self._records.find_one({"case_id": case_id}, sort=[("seq", -1)])
        if last is None:
            return ChainHead(seq=0, hash=GENESIS_HASH)
        return ChainHead(seq=last.seq, hash=last.hash)

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

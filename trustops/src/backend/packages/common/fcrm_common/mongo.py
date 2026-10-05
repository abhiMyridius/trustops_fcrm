"""MongoDB access on PyMongo's native async API (``pymongo.AsyncMongoClient``;
Motor is deprecated in its favour, ADR-11 / design A §1.3).

- :func:`get_client` hands out one client per URI per process with the
  design A §10.3 timeouts; :func:`mongo_client` wraps it as an
  ``async with`` lifespan and :func:`close_clients` closes them all.
- :class:`Repository` is the port of AEO's ``aeoingestionservice``
  ``mongo_repo`` with snake_case names, no mutable defaults, and typed
  Pydantic models in and out.
- :func:`with_transaction` runs a callback in a transaction (requires the
  ``rs0`` replica set from compose); the outbox, audit and consumer dedupe
  all rely on it.
- :class:`MongoLease` is a ``leases``-collection lock with a TTL, used for
  outbox-relay leadership and per-case serialisation (design A §4.5).

BSON cannot store ``Decimal``, ``Enum``, ``date`` or ``tuple``/``set``
values as-is; :func:`to_bson` converts them on the way in (``Decimal`` to
``Decimal128`` so precision survives) and :func:`from_bson` reverses the
lossy ones on the way out, so a model read back validates to the model
that was written.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from time import monotonic
from typing import Any, Generic, Optional, TypeVar
from urllib.parse import parse_qsl, urlsplit

from bson import ObjectId
from bson.decimal128 import Decimal128
from fcrm_contracts.common import Page
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError, OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.results import UpdateResult
from pymongo.write_concern import WriteConcern

from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.pagination import decode_cursor, encode_cursor

logger = logging.getLogger(__name__)

Document = dict[str, Any]
M = TypeVar("M", bound=BaseModel)

SERVER_SELECTION_TIMEOUT_MS = 5_000
SOCKET_TIMEOUT_MS = 10_000
_ILLEGAL_OPERATION = 20  # "Transaction numbers are only allowed on a replica set"


@dataclass
class _ClientRegistry:
    """Per-process cache of clients keyed by URI (a client owns a pool;
    creating one per request would exhaust connections).
    """

    clients: dict[str, AsyncMongoClient[Document]] = field(default_factory=dict)
    #: Open :func:`mongo_client` contexts per URI; the client closes only
    #: when the last one exits, so nested or concurrent users of one URI
    #: never close each other's pool.
    refs: dict[str, int] = field(default_factory=dict)


_REGISTRY = _ClientRegistry()


def get_client(uri: str, **overrides: Any) -> AsyncMongoClient[Document]:
    """The process-wide client for ``uri``.

    Defaults: ``serverSelectionTimeoutMS=5000``, ``socketTimeoutMS=10000``,
    ``uuidRepresentation="standard"``, ``tz_aware=True`` (datetimes come
    back as aware UTC, matching :func:`fcrm_common.time.utcnow`), retryable
    reads and writes on. An option spelled in the URI's query string wins
    over these defaults (PyMongo would otherwise let the keyword argument
    silently override the operator's URI); ``overrides`` win over both.
    Options apply only when the client for this URI is first created.
    """
    client = _REGISTRY.clients.get(uri)
    if client is None:
        in_uri = {key.lower() for key, _ in parse_qsl(urlsplit(uri).query)}
        defaults: dict[str, Any] = {
            "serverSelectionTimeoutMS": SERVER_SELECTION_TIMEOUT_MS,
            "socketTimeoutMS": SOCKET_TIMEOUT_MS,
            "uuidRepresentation": "standard",
            "retryWrites": True,
            "retryReads": True,
        }
        options: dict[str, Any] = {
            **{k: v for k, v in defaults.items() if k.lower() not in in_uri},
            "tz_aware": True,  # not a URI option; always on
            **overrides,
        }
        client = AsyncMongoClient(uri, **options)
        _REGISTRY.clients[uri] = client
    return client


async def close_clients() -> None:
    """Close every cached client (graceful shutdown, design A §10.4)."""
    clients = list(_REGISTRY.clients.values())
    _REGISTRY.clients.clear()
    _REGISTRY.refs.clear()
    for client in clients:
        await client.close()


@asynccontextmanager
async def mongo_client(uri: str) -> AsyncIterator[AsyncMongoClient[Document]]:
    """Lifespan form of :func:`get_client`, closing the client when the
    last open context for ``uri`` exits.

    Usage: ``async with mongo_client(uri) as client: ...``.
    """
    client = get_client(uri)
    _REGISTRY.refs[uri] = _REGISTRY.refs.get(uri, 0) + 1
    try:
        yield client
    finally:
        remaining = _REGISTRY.refs.get(uri, 1) - 1
        if remaining > 0:
            _REGISTRY.refs[uri] = remaining
        else:
            _REGISTRY.refs.pop(uri, None)
            _REGISTRY.clients.pop(uri, None)
            await client.close()


def to_bson(value: Any) -> Any:
    """Convert a Python value (typically a ``model_dump(mode="python")``) to
    BSON-encodable types: ``Decimal`` -> ``Decimal128``, ``Enum`` -> value,
    ``date`` -> ISO string (BSON has no date-only type), tuples and sets ->
    lists. Datetimes, ObjectIds, str/int/float/bool/None pass through.
    """
    if isinstance(value, Enum):
        return to_bson(value.value)
    if isinstance(value, Decimal):
        return Decimal128(value)
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): to_bson(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_bson(v) for v in value]
    return value


def from_bson(value: Any) -> Any:
    """Reverse the lossy parts of :func:`to_bson` for model validation:
    ``Decimal128`` -> ``Decimal`` and ``ObjectId`` -> ``str``.
    """
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Mapping):
        return {k: from_bson(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_bson(v) for v in value]
    return value


def as_update(update: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise an update document.

    An operator document (every key starts with ``$``) passes through; a
    plain field mapping is wrapped as ``{"$set": fields}``; a mix of the two
    is almost certainly a bug and raises ``ValueError``.
    """
    keys = list(update.keys())
    operators = [k for k in keys if k.startswith("$")]
    if operators and len(operators) != len(keys):
        raise ValueError(f"update mixes operators and fields: {keys}")
    if not keys:
        raise ValueError("empty update document")
    converted = to_bson(dict(update))
    return converted if operators else {"$set": converted}


class Repository(Generic[M]):
    """Typed access to one collection of Pydantic models.

    Args:
        collection: The PyMongo async collection.
        model: The Pydantic model stored in it (``extra="forbid"`` models
            are fine: ``_id`` is stripped on read unless the model declares
            a field aliased ``_id``).
        id_of: Computes ``_id`` from a model on insert (e.g.
            ``lambda r: f"{r.case_id}:{r.seq}"``). ``None`` lets MongoDB
            assign an ``ObjectId``.

    Every method takes an optional ``session`` so it can run inside
    :func:`with_transaction`.
    """

    def __init__(
        self,
        collection: AsyncCollection[Document],
        model: type[M],
        id_of: Optional[Callable[[M], Any]] = None,
    ) -> None:
        self._collection = collection
        self._model = model
        self._id_of = id_of
        self._keeps_id = any(
            info.alias == "_id" for info in model.model_fields.values()
        )

    @property
    def collection(self) -> AsyncCollection[Document]:
        """The underlying collection, for operations this class does not
        wrap.
        """
        return self._collection

    @property
    def model(self) -> type[M]:
        """The model class documents are validated into."""
        return self._model

    def to_document(self, item: M) -> Document:
        """The BSON document stored for ``item``."""
        doc: Document = to_bson(item.model_dump(mode="python", by_alias=True))
        if self._id_of is not None:
            doc["_id"] = to_bson(self._id_of(item))
        return doc

    def from_document(self, doc: Mapping[str, Any]) -> M:
        """Validate a stored document back into the model."""
        data = from_bson(dict(doc))
        if not self._keeps_id:
            data.pop("_id", None)
        return self._model.model_validate(data)

    async def insert_one(
        self, item: M, *, session: Optional[AsyncClientSession] = None
    ) -> Any:
        """Insert ``item``; returns its ``_id``. A duplicate ``_id`` or
        unique-index hit raises ``DuplicateKeyError`` (mapped to 409 by
        :func:`fcrm_common.errors.install_error_handlers`).
        """
        result = await self._collection.insert_one(
            self.to_document(item), session=session
        )
        return result.inserted_id

    async def insert_many(
        self,
        items: Sequence[M],
        *,
        ordered: bool = True,
        session: Optional[AsyncClientSession] = None,
    ) -> list[Any]:
        """Insert ``items`` in one round trip; returns their ``_id`` values."""
        if not items:
            return []
        result = await self._collection.insert_many(
            [self.to_document(i) for i in items], ordered=ordered, session=session
        )
        return list(result.inserted_ids)

    async def find_one(
        self,
        filter: Optional[Mapping[str, Any]] = None,
        *,
        sort: Optional[Sequence[tuple[str, int]]] = None,
        session: Optional[AsyncClientSession] = None,
    ) -> Optional[M]:
        """The first matching model, or ``None``."""
        doc = await self._collection.find_one(
            to_bson(dict(filter or {})),
            sort=list(sort) if sort else None,
            session=session,
        )
        return self.from_document(doc) if doc is not None else None

    async def find(
        self,
        filter: Optional[Mapping[str, Any]] = None,
        *,
        sort: Optional[Sequence[tuple[str, int]]] = None,
        limit: int = 0,
        skip: int = 0,
        session: Optional[AsyncClientSession] = None,
    ) -> list[M]:
        """All matching models (``limit=0`` means no limit)."""
        return [
            m
            async for m in self.iterate(
                filter, sort=sort, limit=limit, skip=skip, session=session
            )
        ]

    async def iterate(
        self,
        filter: Optional[Mapping[str, Any]] = None,
        *,
        sort: Optional[Sequence[tuple[str, int]]] = None,
        limit: int = 0,
        skip: int = 0,
        session: Optional[AsyncClientSession] = None,
    ) -> AsyncIterator[M]:
        """Stream matching models without materialising the whole result."""
        cursor = self._collection.find(
            to_bson(dict(filter or {})),
            sort=list(sort) if sort else None,
            limit=limit,
            skip=skip,
            session=session,
        )
        async for doc in cursor:
            yield self.from_document(doc)

    async def find_paginated(
        self,
        filter: Optional[Mapping[str, Any]] = None,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
        descending: bool = False,
        with_total: bool = False,
        session: Optional[AsyncClientSession] = None,
    ) -> Page[M]:
        """One page in ``_id`` order (seek pagination, stable under
        concurrent inserts). ``next_cursor`` is ``None`` on the last page;
        ``total`` is counted only when ``with_total`` (it costs a scan).

        Raises:
            ValueError: ``limit`` is not positive.
            fcrm_common.errors.ValidationError: ``cursor`` is malformed.
        """
        if limit < 1:
            raise ValueError("limit must be >= 1")
        base = to_bson(dict(filter or {}))
        query = base
        if cursor is not None:
            seek = {"_id": {"$lt" if descending else "$gt": decode_cursor(cursor)}}
            query = {"$and": [base, seek]} if base else seek
        docs = await self._collection.find(
            query,
            sort=[("_id", DESCENDING if descending else ASCENDING)],
            limit=limit + 1,
            session=session,
        ).to_list()
        has_more = len(docs) > limit
        page_docs = docs[:limit]
        next_cursor = encode_cursor(page_docs[-1]["_id"]) if has_more else None
        total = (
            await self._collection.count_documents(base, session=session)
            if with_total
            else None
        )
        return Page(
            items=[self.from_document(d) for d in page_docs],
            next_cursor=next_cursor,
            total=total,
        )

    async def update_one(
        self,
        filter: Mapping[str, Any],
        update: Mapping[str, Any],
        upsert: bool = False,
        *,
        session: Optional[AsyncClientSession] = None,
    ) -> UpdateResult:
        """Update one document. ``update`` is an operator document or a
        plain field mapping applied with ``$set`` (see :func:`as_update`).
        """
        return await self._collection.update_one(
            to_bson(dict(filter)), as_update(update), upsert=upsert, session=session
        )

    async def find_one_and_update(
        self,
        filter: Mapping[str, Any],
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
        return_new: bool = True,
        sort: Optional[Sequence[tuple[str, int]]] = None,
        session: Optional[AsyncClientSession] = None,
    ) -> Optional[M]:
        """Atomically update one document and return it (after the update by
        default), or ``None`` if nothing matched and ``upsert`` is off.
        """
        doc = await self._collection.find_one_and_update(
            to_bson(dict(filter)),
            as_update(update),
            sort=list(sort) if sort else None,
            upsert=upsert,
            return_document=ReturnDocument.AFTER
            if return_new
            else ReturnDocument.BEFORE,
            session=session,
        )
        return self.from_document(doc) if doc is not None else None

    async def count(
        self,
        filter: Optional[Mapping[str, Any]] = None,
        *,
        session: Optional[AsyncClientSession] = None,
    ) -> int:
        """Number of matching documents."""
        return await self._collection.count_documents(
            to_bson(dict(filter or {})), session=session
        )

    async def aggregate(
        self,
        pipeline: Sequence[Mapping[str, Any]],
        *,
        session: Optional[AsyncClientSession] = None,
    ) -> list[Document]:
        """Run an aggregation; returns raw documents (pipeline output rarely
        has the model's shape).
        """
        cursor = await self._collection.aggregate(
            [to_bson(dict(stage)) for stage in pipeline], session=session
        )
        return [from_bson(doc) for doc in await cursor.to_list()]

    async def ensure_indexes(self, indexes: Sequence[IndexModel]) -> list[str]:
        """Create ``indexes`` if missing (idempotent); returns their names."""
        if not indexes:
            return []
        return await self._collection.create_indexes(list(indexes))


async def with_transaction[R](
    client: AsyncMongoClient[Document],
    fn: Callable[[AsyncClientSession], Awaitable[R]],
    *,
    max_commit_time_ms: Optional[int] = None,
) -> R:
    """Run ``fn(session)`` inside a transaction and return its result.

    PyMongo's ``with_transaction`` commits, and retries the whole callback
    on ``TransientTransactionError`` and the commit on
    ``UnknownTransactionCommitResult`` (bounded at 120 s), so ``fn`` must be
    safe to re-run: do every write through ``session``, keep side effects
    other than Mongo writes (publishing, HTTP) out of it — that is what the
    outbox is for. Read concern ``snapshot``, write concern ``majority``.

    Raises:
        UpstreamUnavailableError: The server is standalone (transactions
            need the ``rs0`` replica set) — a deployment error, reported as
            such rather than as an opaque ``OperationFailure``.
    """
    async with client.start_session() as session:
        try:
            return await session.with_transaction(
                fn,
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"),
                max_commit_time_ms=max_commit_time_ms,
            )
        except OperationFailure as exc:
            if exc.code == _ILLEGAL_OPERATION:
                logger.error(
                    "mongo transactions unavailable: not a replica set",
                    extra={"dependency": "mongo"},
                )
                raise UpstreamUnavailableError(
                    "MongoDB transactions require a replica set (rs0).",
                    dependency="mongo",
                ) from exc
            raise


class MongoLease:
    """A named, expiring lock held in a ``leases`` collection document
    ``{_id: name, holder, lease_until}``.

    ``lease_until`` is computed with the *server's* clock (``$$NOW``), and
    contenders compare against the server's clock too, so replicas with
    skewed clocks cannot both believe they hold the lease. The holder's own
    view of validity runs on its local monotonic clock from just *before*
    the acquiring round trip, minus a safety margin, so it always expires
    locally no later than on the server.

    Args:
        collection: The ``leases`` collection.
        name: Lease id, e.g. ``"outbox-relay:case"`` or ``"lease:case:<id>"``.
        holder: This process's identity (``new_id("relay")``).
        ttl_s: Lease lifetime per acquisition/renewal (design A: 10 s).
        margin_s: How much earlier than ``ttl_s`` to stop trusting it.
    """

    def __init__(
        self,
        collection: AsyncCollection[Document],
        name: str,
        holder: str,
        ttl_s: float = 10.0,
        margin_s: float = 1.0,
    ) -> None:
        if margin_s >= ttl_s:
            raise ValueError("margin_s must be smaller than ttl_s")
        self._collection = collection
        self.name = name
        self.holder = holder
        self._ttl_ms = int(ttl_s * 1000)
        self._margin_s = margin_s
        self._ttl_s = ttl_s
        self._valid_until: Optional[float] = None

    @property
    def held(self) -> bool:
        """Whether this process may act as the lease holder right now."""
        return self._valid_until is not None and monotonic() < self._valid_until

    async def acquire(self) -> bool:
        """Take the lease if free or expired, or renew it if already ours.

        Returns whether this process holds the lease afterwards.
        """
        started = monotonic()
        # One atomic upsert keyed on _id alone ($expr is not allowed in an
        # upsert's query): the pipeline takes the lease when it is ours, has
        # expired, or does not exist yet (a missing lease_until compares
        # below any date), and otherwise writes the current values back
        # unchanged. Both $cond expressions see the pre-update document.
        mine_or_free = {
            "$or": [
                {"$eq": ["$holder", self.holder]},
                {"$lt": ["$lease_until", "$$NOW"]},
            ]
        }
        try:
            doc = await self._collection.find_one_and_update(
                {"_id": self.name},
                [
                    {
                        "$set": {
                            "holder": {"$cond": [mine_or_free, self.holder, "$holder"]},
                            "lease_until": {
                                "$cond": [
                                    mine_or_free,
                                    {"$add": ["$$NOW", self._ttl_ms]},
                                    "$lease_until",
                                ]
                            },
                        }
                    }
                ],
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # Two first-ever upserts raced; the other one created the lease.
            self._valid_until = None
            return False
        if doc is None or doc.get("holder") != self.holder:
            self._valid_until = None
            return False
        self._valid_until = started + self._ttl_s - self._margin_s
        return True

    async def release(self) -> None:
        """Give the lease up early (graceful shutdown) if we hold it."""
        self._valid_until = None
        await self._collection.delete_one({"_id": self.name, "holder": self.holder})

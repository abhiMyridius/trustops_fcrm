"""How S3 asks H4 to (re)build a vector index, and how it records that it did.

S3 owns the data of the three indexes (``policy_clauses``, ``precedent_cases``,
``controls``) and H4 owns the vectors. The two meet on the bus: S3 writes an
``index.build`` request through its transactional outbox, H4 reads the records
back over S3's internal routes, embeds them and answers with ``index.built``
(or ``index.failed``), which S3's own consumer records in ``index_status``.

Two things request a build:

* **the first seed** (:class:`IndexRequester`): when S3 starts on an empty
  database it has just loaded the corpus, the precedents and the control
  inventory, so it asks for all three indexes once. Each request is guarded by
  an ``index_status`` row written in the same transaction as the outbox row,
  so a restart never asks twice and a crash between the two never leaves one
  without the other;
* **a closed case** (:mod:`fcrm_policy.precedents`): a new precedent row asks
  for a rebuild of ``precedent_cases`` in the same transaction as the row.

Request and job ids are derived from what is being built
(:func:`index_build_envelope`), so a replay of the same request is the same
message and H4's consumer drops it as a duplicate.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal, Optional

from fcrm_common.events import build_envelope
from fcrm_common.ids import deterministic_id
from fcrm_common.mongo import Document, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import IndexBuild, IndexBuilt, IndexFailed
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_policy.store import CONTROLS, POLICY_CLAUSES, PRECEDENT_CASES

logger = logging.getLogger(__name__)

IndexName = Literal["policy_clauses", "precedent_cases", "controls"]
INDEX_NAMES: Final[tuple[IndexName, ...]] = (
    POLICY_CLAUSES,
    PRECEDENT_CASES,
    CONTROLS,
)
EVENT_INDEX_BUILD: Final = "index.build"


def index_build_envelope(
    index: IndexName,
    version: int,
    *,
    discriminator: str,
    case_id: Optional[str] = None,
    mode: Literal["full", "incremental"] = "full",
) -> Envelope:
    """An ``index.build`` request whose ids derive from what is being built.

    Args:
        index: Which index to build.
        version: The version of the data (corpus version, controls version,
            precedent count).
        discriminator: What distinguishes two builds of the same version
            (``"seed"``, a content hash): the same one gives the same job and
            event id.
        case_id: The case whose closing asked for the build, when one did.
        mode: ``full`` rebuilds the index, ``incremental`` upserts.
    """
    job_id = deterministic_id("job", index, str(version), discriminator)
    payload = IndexBuild(job_id=job_id, index=index, version=version, mode=mode)
    envelope = build_envelope(
        EVENT_INDEX_BUILD, payload.model_dump(mode="json"), case_id=case_id
    )
    return envelope.model_copy(
        update={"event_id": deterministic_id("evt", job_id, EVENT_INDEX_BUILD)}
    )


def status_id(index: str, version: int) -> str:
    """The ``index_status`` ``_id`` of one index at one version."""
    return f"{index}:{version}"


class IndexStatusStore:
    """``index_status``: one row per (index, version) S3 asked H4 to build,
    and what H4 answered. The row is the first-seed guard and, once H4 has
    answered, the record of the build.

    Args:
        collection: The ``index_status`` collection.
    """

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def request(
        self, session: AsyncClientSession, index: str, version: int
    ) -> bool:
        """Record a build request inside the caller's transaction.

        Returns:
            ``True`` when this is the first request for ``(index, version)``;
            ``False`` when one is already recorded (the caller must not
            enqueue another).
        """
        # An upsert, not an insert: a duplicate-key error would abort the
        # whole transaction, whereas ``$setOnInsert`` on an existing row is
        # simply a no-op (``upserted_id`` is ``None``).
        result = await self._collection.update_one(
            {"_id": status_id(index, version)},
            {
                "$setOnInsert": {
                    "index": index,
                    "version": version,
                    "status": "requested",
                    "requested_at": utcnow(),
                }
            },
            upsert=True,
            session=session,
        )
        return result.upserted_id is not None

    async def mark_built(self, event: IndexBuilt, at: datetime) -> None:
        """Record H4's answer: the vector count and where the fixture is.
        Upserts, so an answer that outruns (or replaces) its request row still
        leaves a truthful record.
        """
        await self._collection.update_one(
            {"_id": status_id(event.index, event.version)},
            {
                "$set": {
                    "index": event.index,
                    "version": event.version,
                    "status": "built",
                    "built_at": at,
                    "vector_count": event.vector_count,
                    "fixture_key": event.fixture_key,
                    "job_id": event.job_id,
                }
            },
            upsert=True,
        )

    async def mark_failed(self, event: IndexFailed, at: datetime) -> None:
        """Record that H4 could not build the index, and why."""
        await self._collection.update_many(
            {"index": event.index, "status": "requested"},
            {
                "$set": {
                    "status": "failed",
                    "failed_at": at,
                    "failure_code": event.code,
                    "job_id": event.job_id,
                }
            },
        )

    async def get(self, index: str, version: int) -> Optional[Document]:
        """The status row of one index at one version, or ``None``."""
        return await self._collection.find_one({"_id": status_id(index, version)})


class IndexRequester:
    """Asks H4 for the indexes S3 has just seeded, once each.

    Args:
        client: The Mongo client (the request row and the outbox row commit in
            one transaction on it).
        status: The ``index_status`` store.
        outbox: S3's outbox.
    """

    def __init__(
        self,
        client: AsyncMongoClient[Document],
        status: IndexStatusStore,
        outbox: Outbox,
    ) -> None:
        self._client = client
        self._status = status
        self._outbox = outbox

    async def request_missing(self, versions: Mapping[IndexName, int]) -> list[str]:
        """Enqueue an ``index.build`` for every ``(index, version)`` in
        ``versions`` that has not been requested yet.

        Returns:
            The indexes a request was enqueued for (empty on a restart).
        """
        requested: list[str] = []
        for index in INDEX_NAMES:
            version = versions.get(index)
            if version is None:
                continue
            if await self._request_one(index, version):
                requested.append(index)
        if requested:
            logger.info("index builds requested", extra={"indexes": requested})
        return requested

    async def _request_one(self, index: IndexName, version: int) -> bool:
        """One transaction: the status row and, only if it is new, the event."""
        envelope = index_build_envelope(index, version, discriminator="seed")

        async def txn(session: AsyncClientSession) -> bool:
            if not await self._status.request(session, index, version):
                return False
            await self._outbox.enqueue(session, envelope)
            return True

        return await with_transaction(self._client, txn)


__all__ = [
    "EVENT_INDEX_BUILD",
    "INDEX_NAMES",
    "IndexName",
    "IndexRequester",
    "IndexStatusStore",
    "index_build_envelope",
    "status_id",
]

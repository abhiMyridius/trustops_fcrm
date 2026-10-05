"""S3 as a consumer: what it does when H4 reports a finished index build.

H4 answers every ``index.build`` S3 asked for with ``index.built`` (or
``index.failed``) on ``case.events``; ``policy-service.events`` (bound to
``index.*``) delivers them here. Two things follow:

* S3 records the answer in ``index_status`` (:mod:`fcrm_policy.indexing`);
* for ``policy_clauses`` under the ``memory`` backend S3 loads the fixture H4
  stored in the vectors bucket into a fresh in-memory store and swaps it in
  (:class:`VectorFixtureReloader`), so the semantic leg of a search ranks
  against what H4 built, not only against what S3 seeded for itself.

Only the **ranking** comes from the fixture. A hit's text always comes from
Mongo, the system of record, so a stale or odd fixture can reorder results but
can never change a word of a clause.

A fixture that cannot be used is logged and the previous store keeps serving
(fail closed on the data, never on the search); an object store outage raises,
and :class:`~fcrm_common.events.Consumer` retries the message and then
dead-letters it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from evoq_context.core.config import ContextConfig
from evoq_context.vector.factory import create_vector_store
from fcrm_common.events import Consumer, Publisher
from fcrm_common.mongo import Document
from fcrm_common.objectstore import ObjectStore
from fcrm_common.time import utcnow
from fcrm_context_worker.config import FCRM_PROJECT
from fcrm_context_worker.fixture import FixtureError, decode_fixture
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import IndexBuilt, IndexFailed
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_policy.indexing import IndexStatusStore
from fcrm_policy.search.vector import VectorClauseSource
from fcrm_policy.store import POLICY_CLAUSES

logger = logging.getLogger(__name__)

POLICY_EVENTS_QUEUE = "policy-service.events"
POLICY_EVENTS_EXCHANGE = "case.events"
POLICY_EVENTS_ROUTING_KEY = "index.*"
EVENT_INDEX_BUILT = "index.built"
EVENT_INDEX_FAILED = "index.failed"


class VectorFixtureReloader:
    """Replaces the ``policy_clauses`` vectors S3 searches with H4's build.

    Args:
        store: Reads the fixture from the vectors bucket.
        bucket: The vectors bucket.
        source: The semantic ranking source whose store is swapped.
        config: The engine config the swapped-in store is created from (the
            same one the one it replaces was).
    """

    def __init__(
        self,
        *,
        store: ObjectStore,
        bucket: str,
        source: VectorClauseSource,
        config: ContextConfig,
    ) -> None:
        self._objects = store
        self._bucket = bucket
        self._source = source
        self._config = config

    async def reload(self, event: IndexBuilt) -> Optional[int]:
        """Load ``event``'s fixture and start serving from it.

        Returns:
            The number of vectors now served, or ``None`` when the event names
            no fixture (S3 Vectors: H4 wrote to the shared index itself) or the
            fixture was unusable (logged; the previous store keeps serving).

        Raises:
            fcrm_common.errors.UpstreamUnavailableError: The object store could
                not be reached.
            fcrm_common.errors.NotFoundError: The fixture named by the event is
                not in the bucket.
        """
        if event.fixture_key is None:
            logger.info(
                "index built without a fixture; nothing to reload",
                extra={"index": event.index, "version": event.version},
            )
            return None
        data = await self._objects.get_bytes(self._bucket, event.fixture_key)
        try:
            chunks = await asyncio.to_thread(
                decode_fixture, data, dimension=self._config.embedding_dimension
            )
        except FixtureError:
            logger.error(
                "vector fixture rejected; the previous index keeps serving",
                extra={"index": event.index, "fixture_key": event.fixture_key},
                exc_info=True,
            )
            return None
        fresh = create_vector_store(self._config, FCRM_PROJECT)
        if fresh is None:
            return None
        await fresh.ensure_index()
        await fresh.upsert(chunks)
        self._source.replace_store(fresh)
        logger.info(
            "vector index reloaded",
            extra={
                "index": event.index,
                "version": event.version,
                "vectors": len(chunks),
            },
        )
        return len(chunks)


class PolicyEventsConsumer(Consumer):
    """Consumes :data:`POLICY_EVENTS_QUEUE`: records H4's answers and reloads
    the clause vectors after a build.

    Args:
        consumed: S3's ``consumed_messages`` collection (dedupe).
        publisher: For the retry and dead-letter copies.
        status: The ``index_status`` store.
        reloader: Reloads the clause vectors; ``None`` when this S3 has no
            object store to read a fixture from.
        max_attempts: Retries before the dead-letter queue.
    """

    def __init__(
        self,
        consumed: AsyncCollection[Document],
        publisher: Publisher,
        *,
        status: IndexStatusStore,
        reloader: Optional[VectorFixtureReloader],
        max_attempts: int = 3,
    ) -> None:
        super().__init__(
            POLICY_EVENTS_QUEUE,
            consumed,
            publisher,
            client=None,
            max_attempts=max_attempts,
        )
        self._status = status
        self._reloader = reloader

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        """Apply one ``index.built`` or ``index.failed``; anything else on the
        queue is not S3's and is ignored.

        Raises:
            pydantic.ValidationError: The payload is not the event's contract
                (retried, then dead-lettered).
            fcrm_common.errors.UpstreamUnavailableError: The object store could
                not be reached while reloading.
        """
        if envelope.type == EVENT_INDEX_BUILT:
            await self._on_built(IndexBuilt.model_validate(envelope.payload))
        elif envelope.type == EVENT_INDEX_FAILED:
            failed = IndexFailed.model_validate(envelope.payload)
            logger.warning(
                "index build failed",
                extra={
                    "index": failed.index,
                    "code": failed.code,
                    "job_id": failed.job_id,
                },
            )
            await self._status.mark_failed(failed, utcnow())
        else:
            logger.debug("ignored event", extra={"type": envelope.type})

    async def _on_built(self, built: IndexBuilt) -> None:
        """Record the build, then reload the clause vectors if it was theirs."""
        await self._status.mark_built(built, utcnow())
        if built.index == POLICY_CLAUSES and self._reloader is not None:
            await self._reloader.reload(built)

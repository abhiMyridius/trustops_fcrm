"""H4's consumer: one ``index.build`` in, ``index.built`` or ``index.failed`` out
(design A §7.2).

For each job: read the index's rows from S3, embed them and write the vectors
(:class:`~fcrm_context_worker.builder.IndexBuilder`), under a deadline; under the
``memory`` backend store the fixture S3 will load in the vectors bucket at
``{index}/{version}.json``; then publish the result on ``case.events``.

**What is a failure and what is a retry.** A build that cannot succeed however
often it runs is an answer, published as ``index.failed`` with a ``code`` so S3
can record it: an index or version S3 has no rows for, a row too long to embed,
a deadline overrun, a request that names no index. An outage (S3, the object
store, the embedding provider or the broker unreachable) is not an answer about
the index: it is raised, and :class:`fcrm_common.events.Consumer` retries the
message and then dead-letters it, so nothing is lost.

**Safe to run twice.** The consumer is built without a Mongo client, so the
dedupe marker is written after the handler succeeds and a crash in between
re-runs it. That is harmless: the fixture is written to a fixed key, and the
result events carry an id derived from the job (``deterministic_id("evt", job_id,
type)``), so a second ``index.built`` is a duplicate S3's own consumer drops.

**Never log row text.** Logs carry the index, its version, counts and codes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final, Optional

from evoq_context.llm.client import EmbeddingInputTooLarge
from fcrm_common.events import Consumer, Publisher, build_envelope, default_exchange
from fcrm_common.events import publish as publish_event
from fcrm_common.ids import deterministic_id
from fcrm_common.mongo import Document
from fcrm_common.objectstore import ObjectStore
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import IndexBuild, IndexBuilt, IndexFailed
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_context_worker.builder import (
    BuildResult,
    IndexBuilder,
    NoVectorBackendError,
    UnknownIndexError,
)
from fcrm_context_worker.fixture import FIXTURE_CONTENT_TYPE, fixture_key
from fcrm_context_worker.sources import RecordLimitError, RecordsNotFoundError
from fcrm_context_worker.topology import (
    CONTEXT_RUN_QUEUE,
    ROUTING_KEY_INDEX_BUILD,
    ROUTING_KEY_INDEX_BUILT,
    ROUTING_KEY_INDEX_FAILED,
)

logger = logging.getLogger(__name__)

#: Failure codes a job can end with (``IndexFailed.code``).
CODE_NO_RECORDS: Final = "no_records"
CODE_RECORD_LIMIT: Final = "record_limit"
CODE_INPUT_TOO_LARGE: Final = "embedding_input_too_large"
CODE_NO_BACKEND: Final = "no_vector_backend"
CODE_UNKNOWN_INDEX: Final = "unknown_index"
CODE_TIMEOUT: Final = "timeout"

#: What a build raises that is an answer about the job, not an outage, and the
#: code each is reported under.
_ANSWERS: Final[tuple[tuple[type[Exception], str], ...]] = (
    (RecordsNotFoundError, CODE_NO_RECORDS),
    (RecordLimitError, CODE_RECORD_LIMIT),
    (EmbeddingInputTooLarge, CODE_INPUT_TOO_LARGE),
    (NoVectorBackendError, CODE_NO_BACKEND),
    (UnknownIndexError, CODE_UNKNOWN_INDEX),
)
_ANSWER_ERRORS: Final[tuple[type[Exception], ...]] = tuple(k for k, _ in _ANSWERS)


def failure_code(error: Exception) -> str:
    """The ``index.failed`` code of a build error that is an answer (one of
    :data:`_ANSWER_ERRORS`).
    """
    for kind, code in _ANSWERS:
        if isinstance(error, kind):
            return code
    raise TypeError(f"{type(error).__name__} is not an answer about a job")


class ContextConsumer(Consumer):
    """Consumes :data:`~fcrm_context_worker.topology.CONTEXT_RUN_QUEUE`.

    Args:
        consumed: The ``consumed_messages`` collection (dedupe).
        publisher: Publishes the result events and the retry/DLQ copies.
        builder: Builds one index.
        store: Writes the fixture.
        bucket: The vectors bucket the fixture goes to.
        timeout_s: The deadline for one build.
        max_attempts: Retries before the dead-letter queue.
    """

    def __init__(
        self,
        consumed: AsyncCollection[Document],
        publisher: Publisher,
        *,
        builder: IndexBuilder,
        store: ObjectStore,
        bucket: str,
        timeout_s: float,
        max_attempts: int = 3,
    ) -> None:
        """Wire the consumer. It is built without a Mongo client on purpose:
        embedding calls and object-store writes must not sit inside a
        transaction.
        """
        super().__init__(
            CONTEXT_RUN_QUEUE,
            consumed,
            publisher,
            client=None,
            max_attempts=max_attempts,
        )
        self._builder = builder
        self._store = store
        self._bucket = bucket
        self._timeout_s = timeout_s

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        """Run one ``index.build`` job and publish its result.

        Raises:
            ValueError: the message is not an ``index.build`` (a misrouted
                message cannot be processed; it is retried, then parked).
            pydantic.ValidationError: the payload is not an
                :class:`~fcrm_contracts.events.payloads.IndexBuild`.
            fcrm_common.errors.UpstreamUnavailableError: S3 or the object store
                is unreachable.
        """
        if envelope.type != ROUTING_KEY_INDEX_BUILD:
            raise ValueError(f"{CONTEXT_RUN_QUEUE} cannot process {envelope.type!r}")
        job = IndexBuild.model_validate(envelope.payload)
        logger.info(
            "index build started",
            extra={"index": job.index, "version": job.version, "mode": job.mode},
        )
        try:
            async with asyncio.timeout(self._timeout_s):
                result = await self._builder.build(job.index, job.version)
                key = await self._write_fixture(job, result)
        except TimeoutError:
            logger.warning(
                "index build ran past its deadline",
                extra={"index": job.index, "timeout_s": self._timeout_s},
            )
            await self._fail(job, envelope, CODE_TIMEOUT, "the build timed out")
            return
        except _ANSWER_ERRORS as exc:
            code = failure_code(exc)
            logger.warning(
                "index build cannot succeed",
                extra={"index": job.index, "version": job.version, "code": code},
            )
            await self._fail(job, envelope, code, str(exc))
            return
        built = IndexBuilt(
            job_id=job.job_id,
            index=job.index,
            version=job.version,
            vector_count=result.vector_count,
            fixture_key=key,
        )
        await self._publish(
            ROUTING_KEY_INDEX_BUILT, built.model_dump(mode="json"), job, envelope
        )

    async def _write_fixture(
        self, job: IndexBuild, result: BuildResult
    ) -> Optional[str]:
        """Store the fixture (when the backend produced one); returns its key.

        Raises:
            fcrm_common.errors.UpstreamUnavailableError: The object store is
                unreachable.
        """
        if result.fixture is None:
            return None
        key = fixture_key(job.index, job.version)
        await self._store.put_bytes(
            self._bucket, key, result.fixture, FIXTURE_CONTENT_TYPE
        )
        return key

    async def _fail(
        self, job: IndexBuild, envelope: Envelope, code: str, detail: str
    ) -> None:
        """Announce that the index cannot be built, with a ``code``."""
        failed = IndexFailed(
            job_id=job.job_id, index=job.index, code=code, detail=detail[:300]
        )
        await self._publish(
            ROUTING_KEY_INDEX_FAILED, failed.model_dump(mode="json"), job, envelope
        )

    async def _publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        job: IndexBuild,
        envelope: Envelope,
    ) -> None:
        """Publish ``payload`` on ``case.events`` with an id derived from the
        job, so a rerun of the job announces the same event.
        """
        built = build_envelope(
            event_type,
            payload,
            case_id=envelope.case_id,
            run_id=envelope.run_id,
            correlation_id=envelope.correlation_id,
        )
        event = built.model_copy(
            update={"event_id": deterministic_id("evt", job.job_id, event_type)}
        )
        await publish_event(
            self._publisher, default_exchange(event_type), event_type, event
        )

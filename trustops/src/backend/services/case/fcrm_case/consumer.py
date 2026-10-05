"""S1's bus consumer: ``case-service.events``, bound by ops to ``outcome.*``,
``vote.rerequested``, ``pack.released`` and ``index.built`` (``ops/rabbitmq
/definitions.json``).

It mirrors what S5 and H1 announce into S1's own ``activity`` log and the case
stream, with the visibility :mod:`fcrm_case.domain.mirror` decides: the analyst
sees the committee's process, the PO sees one plain ``outcome.delivered`` and
never a ``vote.*`` or ``pack.*`` event.

**Exactly once.** The consumer is transactional (``client=``): the dedupe marker
and the activity row commit together, so a redelivery after a crash neither
loses the row nor writes it twice, and the per-case ``seq`` stays gap-free
(:meth:`fcrm_case.activity_log.MongoActivityLog.append_in`). The entry is
published to the in-process hub only after the commit, from :meth:`_apply`: the
transaction body may run more than once, and a rolled-back row must never reach a
browser. An event for a case S1 does not have is logged and acknowledged (it can
never succeed); a payload that is not what its type promises raises, so the
message is retried and then parked in the dead-letter queue.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any, Final, Optional, Protocol

from faststream.rabbit import RabbitBroker
from fcrm_common.broker import wire_consumer
from fcrm_common.events import Consumer, Disposition, Publisher
from fcrm_common.mongo import Document
from fcrm_common.service import ServiceDeps
from fcrm_contracts.api.case_internal import UiEventIn
from fcrm_contracts.events.envelope import Envelope
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.mirror import mirror_for
from fcrm_case.sse import EventHub

logger = logging.getLogger(__name__)

#: The queue ops declared for S1 and the exchange it is bound to.
QUEUE: Final = "case-service.events"
EXCHANGE: Final = "case.events"
#: The routing keys ops bound to it (``definitions.json``); one subscriber each.
ROUTING_KEYS: Final = ("outcome.*", "vote.rerequested", "pack.released", "index.built")


class StagingActivityLog(Protocol):
    """The activity log as the consumer uses it."""

    async def append_in(
        self,
        session: Optional[AsyncClientSession],
        case_id: str,
        event: UiEventIn,
    ) -> Optional[ActivityEntry]:
        """Allocate the next ``seq`` and insert ``event`` in ``session``'s
        transaction (standalone when ``session`` is ``None``); ``None`` when the
        case does not exist.
        """
        ...


class CaseServiceEventsConsumer(Consumer):
    """Consumes :data:`QUEUE` and mirrors its events onto the case stream.

    Args:
        consumed: S1's ``consumed_messages`` collection.
        publisher: For retry and dead-letter republishing.
        activity: The activity log.
        hub: The in-process stream hub the committed entries are published to.
        client: Enables the transactional dedupe (the replica set); without it the
            marker is written after the handler and the row can repeat after a
            crash between the two.
        max_attempts: Retries before the dead-letter queue.
    """

    def __init__(
        self,
        consumed: AsyncCollection[Document],
        publisher: Publisher,
        *,
        activity: StagingActivityLog,
        hub: EventHub,
        client: Optional[AsyncMongoClient[Document]] = None,
        max_attempts: int = 3,
    ) -> None:
        super().__init__(
            QUEUE, consumed, publisher, client=client, max_attempts=max_attempts
        )
        self._activity = activity
        self._hub = hub
        self._staged: dict[str, ActivityEntry] = {}

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        """Stage the activity row for ``envelope`` (inside ``session`` when the
        consumer is transactional).

        Raises:
            pydantic.ValidationError: The payload is not what its type promises.
        """
        event = mirror_for(envelope)
        if event is None:
            logger.debug(
                "event is not mirrored onto the case stream",
                extra={"type": envelope.type, "event_id": envelope.event_id},
            )
            return
        if envelope.case_id is None:
            logger.warning(
                "event names no case; dropped",
                extra={"type": envelope.type, "event_id": envelope.event_id},
            )
            return
        entry = await self._activity.append_in(session, envelope.case_id, event)
        if entry is None:
            logger.warning(
                "event for a case S1 does not have; dropped",
                extra={
                    "type": envelope.type,
                    "event_id": envelope.event_id,
                    "case_id": envelope.case_id,
                },
            )
            return
        self._staged[envelope.event_id] = entry

    async def _apply(self, envelope: Envelope) -> Disposition:
        """Dedupe and handle (the base class), then, only if the transaction
        committed, publish the staged entry to the open streams.
        """
        try:
            disposition = await super()._apply(envelope)
            entry = self._staged.get(envelope.event_id)
            if disposition is Disposition.PROCESSED and entry is not None:
                self._hub.publish(entry)
            return disposition
        finally:
            self._staged.pop(envelope.event_id, None)


def subscribe(
    broker: RabbitBroker, consumer: CaseServiceEventsConsumer, *, prefetch: int = 8
) -> list[Any]:
    """Register the consumer's subscribers on ``broker`` and return the new ones,
    for a host whose broker is already started (the composite ``api`` process
    starts it before it registers S1): the caller starts each and stops it on
    shutdown.
    """
    before = {id(sub) for sub in broker.subscribers}
    wire_consumer(
        broker,
        consumer,
        exchange=EXCHANGE,
        routing_keys=ROUTING_KEYS,
        prefetch=prefetch,
    )
    return [sub for sub in broker.subscribers if id(sub) not in before]


async def start_consumer(
    stack: AsyncExitStack,
    deps: ServiceDeps,
    *,
    consumed: AsyncCollection[Document],
    activity: StagingActivityLog,
    hub: EventHub,
) -> Optional[CaseServiceEventsConsumer]:
    """Start the consumer on the host's broker, stopping its subscribers when
    ``stack`` closes; with no broker (a standalone ``uvicorn``) log it and start
    nothing.

    Returns:
        The consumer, or ``None`` when there is no broker to consume from.
    """
    if deps.broker is None or deps.publisher is None:
        logger.warning(
            "case service has no broker; %s is not consumed",
            QUEUE,
            extra={"queue": QUEUE},
        )
        return None
    consumer = CaseServiceEventsConsumer(
        consumed, deps.publisher, activity=activity, hub=hub, client=deps.mongo
    )
    await consumer.ensure_indexes()
    for subscriber in subscribe(deps.broker, consumer):
        await subscriber.start()
        stack.push_async_callback(subscriber.stop)
    logger.info("consuming %s", QUEUE, extra={"queue": QUEUE})
    return consumer

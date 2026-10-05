"""The case event stream: ``GET /cases/{case_id}/stream`` (design A §2.1.5,
ADR-30).

How an event reaches a browser:

1. A writer appends an :class:`~fcrm_case.domain.activity.ActivityEntry`
   through :class:`ActivityWriter` (or a store method that stages one in its own
   transaction). The row is persisted first, with a per-case, gap-free ``seq``
   (:mod:`fcrm_case.activity_log`).
2. After the commit the entry is published to the in-process :class:`EventHub`:
   one bounded queue per open stream. A consumer that cannot keep up is *dropped*
   (its queue filled), never waited for, so a slow browser cannot stall a writer;
   it reconnects with its last id and replays from the log.
3. :func:`case_event_stream` turns the queue into SSE frames. Frames are
   **unnamed** (``id:`` and ``data:`` only), so a browser's
   ``EventSource.onmessage`` fires::

       id: 17
       data: {"type":"case.stage_changed","caseId":"FCRM-2026-0151","seq":17,
              "at":"2026-10-05T10:00:00Z","payload":{...}}

The stream subscribes to the hub *before* it replays, then reconciles by
``seq``: an entry at or below the last one sent is a duplicate and is skipped,
the next one is sent, and a jump (a later entry published before an earlier one
reached the hub) is filled from the log, so no event is missed or reordered.

``Last-Event-ID`` (the header a browser sends on its own reconnect) or
``?last_event_id=`` replays everything after that ``seq``; a fresh connection
starts at the case's current head. A heartbeat comment goes out every
``SSE_HEARTBEAT_S`` and the stream ends after ``SSE_MAX_LIFETIME_S`` (the
browser reconnects with its last id).

**Who sees what** is :mod:`fcrm_case.domain.activity`: the PO audience gets
``po``/``all`` rows only, and never a ``score.*``, ``harness.*``, ``audit.*`` (or
other analyst-only) type or a payload with a restricted key, even when a row was
written with the wrong visibility.

EXTENSION POINT (``REDIS_URL``): the hub is in-process, which is right for one
``api`` container. To serve several replicas, :meth:`EventHub.publish` is where
an entry would also be published to a Redis channel ``sse:case:{id}``, with a
listener task calling :meth:`EventHub.deliver` for entries other replicas
persisted. Not implemented: with ``REDIS_URL`` unset (the default) nothing
changes, and the log replay already makes a missed delivery harmless.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Optional, Protocol

from fcrm_contracts.api.case_internal import UiEventIn
from pymongo.errors import PyMongoError

from fcrm_case.activity_log import REPLAY_PAGE, ActivityLog
from fcrm_case.domain.activity import (
    VISIBLE_TO,
    ActivityEntry,
    Audience,
    frame_payload,
    visible_to,
)

logger = logging.getLogger(__name__)

OPEN_COMMENT = ": open\n\n"
HEARTBEAT_COMMENT = ": heartbeat\n\n"
DEFAULT_QUEUE_SIZE = 256


def _iso(moment: datetime) -> str:
    """``moment`` as ``2026-10-05T10:00:00.123456Z`` (UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def format_frame(entry: ActivityEntry, audience: Audience) -> Optional[str]:
    """The unnamed SSE frame for ``entry``, or ``None`` when ``audience`` may
    not be shown it (a PO-visible row the second fence refuses is logged: its
    writer chose the wrong visibility).
    """
    if not visible_to(entry, audience):
        if audience == "po" and entry.visibility in VISIBLE_TO["po"]:
            logger.error(
                "po stream withheld an entry written as po-visible",
                extra={"case_id": entry.case_id, "seq": entry.seq, "type": entry.type},
            )
        return None
    body = {
        "type": entry.type,
        "caseId": entry.case_id,
        "seq": entry.seq,
        "at": _iso(entry.at),
        "payload": frame_payload(entry, audience),
    }
    data = json.dumps(body, separators=(",", ":"), default=str)
    return f"id: {entry.seq}\ndata: {data}\n\n"


class Subscription:
    """One open stream's queue."""

    def __init__(self, case_id: str, max_queue: int) -> None:
        self.case_id = case_id
        self.queue: asyncio.Queue[ActivityEntry] = asyncio.Queue(maxsize=max_queue)
        #: Set by the hub when this consumer's queue filled and it was removed.
        self.dropped = False


class EventHub:
    """In-process fan-out of committed entries to the open streams of a case.

    Args:
        max_queue: Entries a stream may have waiting before it is dropped.
    """

    def __init__(self, max_queue: int = DEFAULT_QUEUE_SIZE) -> None:
        self._max_queue = max_queue
        self._subs: dict[str, set[Subscription]] = {}

    @contextmanager
    def subscription(self, case_id: str) -> Iterator[Subscription]:
        """A queue that receives every entry published for ``case_id`` until
        the block exits.
        """
        sub = Subscription(case_id, self._max_queue)
        self._subs.setdefault(case_id, set()).add(sub)
        try:
            yield sub
        finally:
            self._discard(sub)

    def _discard(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.case_id)
        if subs is None:
            return
        subs.discard(sub)
        if not subs:
            del self._subs[sub.case_id]

    def subscriber_count(self, case_id: Optional[str] = None) -> int:
        """Open streams for ``case_id``, or in all when it is ``None``."""
        if case_id is not None:
            return len(self._subs.get(case_id, ()))
        return sum(len(subs) for subs in self._subs.values())

    def publish(self, entry: ActivityEntry) -> int:
        """Hand ``entry`` to every open stream of its case; returns how many
        took it. Never blocks: a stream whose queue is full is dropped.
        """
        # EXTENSION POINT (REDIS_URL): publish to ``sse:case:{id}`` here too.
        return self.deliver(entry)

    def deliver(self, entry: ActivityEntry) -> int:
        """Deliver ``entry`` to this process's streams (what a Redis listener
        would call for an entry another replica persisted).
        """
        delivered = 0
        for sub in list(self._subs.get(entry.case_id, ())):
            try:
                sub.queue.put_nowait(entry)
            except asyncio.QueueFull:
                sub.dropped = True
                self._discard(sub)
                logger.warning(
                    "slow stream consumer dropped",
                    extra={"case_id": entry.case_id, "seq": entry.seq},
                )
            else:
                delivered += 1
        return delivered


class ActivityWriter:
    """Persist an event, then publish it: the only way a standalone event
    reaches the stream (a state change or assignment is staged inside its own
    transaction by the store, and published with :meth:`publish`).
    """

    def __init__(self, log: ActivityLog, hub: EventHub) -> None:
        self._log = log
        self._hub = hub

    async def append(self, case_id: str, event: UiEventIn) -> Optional[ActivityEntry]:
        """Append ``event`` to ``case_id``'s log and publish it; ``None`` when
        the case does not exist (nothing is published).
        """
        entry = await self._log.append(case_id, event)
        if entry is not None:
            self._hub.publish(entry)
        return entry

    def publish(self, entry: Optional[ActivityEntry]) -> None:
        """Publish an entry a store already persisted (``None`` is a no-op)."""
        if entry is not None:
            self._hub.publish(entry)


class Disconnectable(Protocol):
    """The one thing the stream asks of the request."""

    async def is_disconnected(self) -> bool:
        """Whether the client has gone away."""
        ...


@dataclass(frozen=True)
class StreamParams:
    """What one stream is for."""

    case_id: str
    audience: Audience
    #: Send entries with ``seq`` above this: the client's ``Last-Event-ID``, or
    #: the case's head for a fresh connection.
    start_seq: int
    heartbeat_s: float
    max_lifetime_s: float
    replay_page: int = REPLAY_PAGE


class _Reconciler:
    """Keeps a stream's position in the case's ``seq`` and turns entries into
    frames exactly once, in order.
    """

    def __init__(self, log: ActivityLog, params: StreamParams) -> None:
        self._log = log
        self._params = params
        self.last = params.start_seq

    def _frame(self, entry: ActivityEntry) -> Optional[str]:
        return format_frame(entry, self._params.audience)

    async def catch_up(self) -> AsyncIterator[str]:
        """Frames for every entry after ``last`` in the log, a page at a time."""
        page = self._params.replay_page
        while True:
            rows = await self._log.since(self._params.case_id, self.last, limit=page)
            for row in rows:
                self.last = row.seq
                frame = self._frame(row)
                if frame is not None:
                    yield frame
            if len(rows) < page:
                return

    async def accept(self, entry: ActivityEntry) -> AsyncIterator[str]:
        """Frames for one entry from the hub: nothing for a duplicate, the
        entry itself when it is next, and a fill from the log when it jumped
        ahead of an entry that has not arrived yet.
        """
        if entry.seq <= self.last:
            return
        if entry.seq > self.last + 1:
            async for frame in self.catch_up():
                yield frame
        if entry.seq > self.last:
            self.last = entry.seq
            own = self._frame(entry)
            if own is not None:
                yield own


async def _next_entry(sub: Subscription, wait_s: float) -> Optional[ActivityEntry]:
    """The next published entry, or ``None`` after ``wait_s`` of silence."""
    try:
        async with asyncio.timeout(wait_s):
            return await sub.queue.get()
    except TimeoutError:
        return None


async def case_event_stream(
    request: Disconnectable,
    hub: EventHub,
    log: ActivityLog,
    params: StreamParams,
) -> AsyncIterator[str]:
    """SSE frames for one case until the client leaves, the consumer is
    dropped, the lifetime is up or the log cannot be read (the browser then
    reconnects and replays).
    """
    deadline = monotonic() + params.max_lifetime_s
    reconciler = _Reconciler(log, params)
    with hub.subscription(params.case_id) as sub:
        try:
            yield OPEN_COMMENT
            async for frame in reconciler.catch_up():
                yield frame
            while monotonic() < deadline and not sub.dropped:
                if await request.is_disconnected():
                    return
                entry = await _next_entry(sub, params.heartbeat_s)
                if entry is None:
                    yield HEARTBEAT_COMMENT
                    continue
                async for frame in reconciler.accept(entry):
                    yield frame
        except PyMongoError:
            logger.warning(
                "stream ended: the activity log could not be read",
                extra={"case_id": params.case_id},
                exc_info=True,
            )

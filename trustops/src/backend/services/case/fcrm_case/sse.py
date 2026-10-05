"""A minimal Server-Sent Events generator for ``GET /cases/{case_id}/stream``.

No real event source feeds this yet: H1 caseflow publishes its state
changes over RabbitMQ (``fcrm_caseflow.publisher``), which this HTTP
service has no broker connection to consume, and wiring one in just for a
stream nobody downstream reads yet would be the over-engineering the task
explicitly warns against. Polling this service's own store for its own
case document is self-contained, store-agnostic (identical whether
``fetch_snapshot`` reads ``InMemoryCaseStore`` or live Mongo, which is what
keeps the route unit-testable without a broker or without Mongo), and
upgradeable later: swap :func:`case_update_stream`'s poll loop for a Mongo
change-stream watch on ``cases`` (already a replica set, per
``fcrm_common.mongo.with_transaction``'s own requirement) or a RabbitMQ
consumer, and ``routes.py`` does not change at all.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Optional

from starlette.requests import Request

logger = logging.getLogger(__name__)

_HEARTBEAT_COMMENT = ": heartbeat\n\n"


def format_event(event: str, data: dict[str, Any]) -> str:
    """One SSE frame: an ``event:``/``data:`` pair, terminated by a blank
    line as the spec requires.
    """
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def case_update_stream(
    request: Request,
    fetch_snapshot: Callable[[], Awaitable[Optional[dict[str, Any]]]],
    heartbeat_s: float,
    max_ticks: Optional[int] = None,
) -> AsyncIterator[str]:
    """Emit an initial ``snapshot`` event, then poll ``fetch_snapshot``
    every ``heartbeat_s`` seconds: an ``update`` event when its JSON
    representation changed since the last tick, otherwise a bare SSE
    comment (a valid, invisible-to-listeners keep-alive). Stops when the
    client disconnects, ``fetch_snapshot`` returns ``None`` (the case no
    longer exists) or ``max_ticks`` polls have run. The tick cap is a
    safety valve, not a feature: a browser ``EventSource`` reconnects on
    its own, so bounding one stream's lifetime costs nothing and
    guarantees a generator can never outlive a proxy that fails to
    deliver a disconnect.
    """
    snapshot = await fetch_snapshot()
    if snapshot is None:
        return
    last_text = json.dumps(snapshot, sort_keys=True)
    yield format_event("snapshot", snapshot)
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        if await request.is_disconnected():
            return
        await asyncio.sleep(heartbeat_s)
        snapshot = await fetch_snapshot()
        if snapshot is None:
            return
        text = json.dumps(snapshot, sort_keys=True)
        if text != last_text:
            last_text = text
            yield format_event("update", snapshot)
        else:
            yield _HEARTBEAT_COMMENT

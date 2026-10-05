"""``fcrm_case.sse.case_update_stream``, driven directly as an async
generator (no HTTP transport, no threads): snapshot first, ``update`` only
when the snapshot changed, keep-alive comments otherwise, and it stops on
disconnect, on a vanished case and at ``max_ticks``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fcrm_case.sse import case_update_stream, format_event


class FakeRequest:
    """Just the one method the generator uses."""

    def __init__(self, disconnect_after: Optional[int] = None) -> None:
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return (
            self._disconnect_after is not None and self._calls > self._disconnect_after
        )


def _snapshots(*values: Optional[dict[str, Any]]):  # type: ignore[no-untyped-def]
    queue = list(values)

    async def fetch() -> Optional[dict[str, Any]]:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return fetch


async def _collect(stream: Any) -> list[str]:
    return [frame async for frame in stream]


def test_format_event_is_a_terminated_sse_frame() -> None:
    frame = format_event("update", {"a": 1})
    assert frame == 'event: update\ndata: {"a": 1}\n\n'


async def test_snapshot_first_then_update_only_when_it_changes() -> None:
    fetch = _snapshots({"state": "intake"}, {"state": "intake"}, {"state": "triage"})
    frames = await _collect(
        case_update_stream(FakeRequest(), fetch, heartbeat_s=0, max_ticks=3)
    )
    assert frames[0].startswith("event: snapshot")
    assert frames[1] == ": heartbeat\n\n"  # unchanged
    assert frames[2].startswith("event: update")
    assert json.loads(frames[2].splitlines()[1][len("data: ") :]) == {"state": "triage"}


async def test_stops_when_the_client_disconnects() -> None:
    fetch = _snapshots({"state": "intake"})
    frames = await _collect(
        case_update_stream(FakeRequest(disconnect_after=1), fetch, heartbeat_s=0)
    )
    assert len(frames) == 2  # snapshot, one keep-alive, then disconnect seen


async def test_stops_when_the_case_vanishes() -> None:
    fetch = _snapshots({"state": "intake"}, None)
    frames = await _collect(
        case_update_stream(FakeRequest(), fetch, heartbeat_s=0, max_ticks=10)
    )
    assert len(frames) == 1


async def test_a_missing_case_yields_nothing_at_all() -> None:
    fetch = _snapshots(None)
    assert await _collect(case_update_stream(FakeRequest(), fetch, heartbeat_s=0)) == []

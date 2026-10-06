"""Reading a case's server-sent event stream (``GET /cases/{id}/stream``)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SseFrame:
    """One data frame of the stream.

    Attributes:
        seq: The frame's ``id:`` (the case's activity ``seq``).
        event: The decoded ``data:`` JSON (``type``, ``caseId``, ``seq``,
            ``at``, ``payload``).
    """

    seq: int
    event: dict[str, Any]


def parse_frames(lines: Iterable[str]) -> Iterator[SseFrame]:
    """Frames from the text lines of a stream.

    A frame is the ``id:`` and ``data:`` lines up to the blank line that ends
    it. Comment lines (``: open``, ``: heartbeat``) and frames without an id or
    with undecodable data are skipped (the last with a warning), since the
    drills only count real events.
    """
    event_id: Optional[str] = None
    data_parts: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            frame = _frame(event_id, data_parts)
            if frame is not None:
                yield frame
            event_id, data_parts = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("id:"):
            event_id = line[3:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].lstrip())


def _frame(event_id: Optional[str], data_parts: list[str]) -> Optional[SseFrame]:
    """The frame these parts make, or ``None`` when they are not a real event."""
    if event_id is None or not data_parts:
        return None
    try:
        seq = int(event_id)
        event = json.loads("\n".join(data_parts))
    except ValueError:
        logger.warning("skipping an unreadable stream frame (id %r)", event_id)
        return None
    if not isinstance(event, dict):
        return None
    return SseFrame(seq, event)

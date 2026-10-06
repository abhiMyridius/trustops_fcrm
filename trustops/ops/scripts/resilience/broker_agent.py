r"""Broker operations, run inside a container that has ``aio_pika``.

The container needs ``RABBITMQ_URL`` (the harness has both). The host sends this
file on standard input::

    docker exec -i trustops-harness-1 python - <command> '<json params>'

so the broker credential is read from the container's own environment and
never reaches the host process, a command line or a log. The last line of
standard output is one JSON object: ``{"ok": true, ...}`` or ``{"ok": false,
"error": "..."}`` (exit status 1).

Commands (params are one JSON object):

``publish``
    ``{exchange, routing_key, body_b64, headers, message_id, type,
    content_type}``: one persistent message, confirmed by the broker and
    mandatory (an unroutable message is an error, not a silent drop).
``peek``
    ``{queue, limit}``: up to ``limit`` messages described, then returned to
    the queue. Each peek counts as one delivery of the message (quorum
    queues keep a delivery count), so look at a dead-letter queue a few times,
    not in a loop.
``replay``
    ``{queue, limit, message_id}``: move messages from ``<queue>.dlq`` back
    onto ``<queue>`` with the retry budget reset (``x-attempt`` 0, the
    last error dropped, ``x-replayed-*`` added). A message is acknowledged on
    the dead-letter queue only after the broker confirmed its republish, so a
    failure never loses one. ``message_id`` limits it to one message (named by
    its AMQP id or its envelope's ``event_id``).
``purge``
    ``{queue, message_id}``: delete messages of a ``*.dlq`` queue (all, or the
    one named like that). Refuses any other queue.

This file imports nothing from the repository: it must run in a bare
container. It is also imported by the unit tests, with a fake connection.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Optional

#: Headers a dead-lettered message carries that a replay must not keep.
STALE_HEADERS = frozenset({"x-attempt", "x-last-error", "x-death"})
STALE_PREFIXES = ("x-first-death-", "x-last-death-")
DLQ_SUFFIX = ".dlq"
#: The envelope's own id, readable even from a body that is cut off or is not JSON.
EVENT_ID = re.compile(rb'"event_id"\s*:\s*"([^"]{1,128})"')
CONNECT_TIMEOUT_S = 10
GET_TIMEOUT_S = 10
PREVIEW_CHARS = 300


class AgentError(Exception):
    """A command could not be carried out; the text goes to the operator."""


def reset_headers(
    headers: Optional[Mapping[str, Any]], *, source_queue: str, now: datetime
) -> dict[str, Any]:
    """Headers for a replayed message: everything except the retry
    bookkeeping, ``x-attempt`` back to 0, and where and when it was replayed.
    """
    kept = {
        key: value
        for key, value in (headers or {}).items()
        if key not in STALE_HEADERS and not key.startswith(STALE_PREFIXES)
    }
    kept["x-attempt"] = 0
    kept["x-replayed-from"] = source_queue
    kept["x-replayed-at"] = now.astimezone(UTC).isoformat()
    return kept


def _plain(value: Any) -> Any:
    """``value`` as something ``json.dumps`` takes (broker long strings and
    bytes become text; nested tables and lists are walked).
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def event_id_of(body: bytes) -> Optional[str]:
    """The ``event_id`` an envelope body names, or ``None``. Read with a pattern,
    not by parsing: a poison message is often cut off or not JSON at all.
    """
    found = EVENT_ID.search(body[:2000])
    return found.group(1).decode("utf-8", errors="replace") if found else None


def matches(message: Any, wanted: str) -> bool:
    """Whether ``message`` is the one named ``wanted``: by its AMQP message id or
    by the envelope's ``event_id``. Both, because a message that went through a
    retry hop on a consumer that did not carry the AMQP properties over has a
    made-up message id, and the event id is the one in the logs and the audit
    trail.
    """
    return message.message_id == wanted or event_id_of(message.body) == wanted


def describe_message(message: Any) -> dict[str, Any]:
    """What an operator needs to recognise a message: its ids, retry
    headers and the start of its body.
    """
    headers = _plain(dict(message.headers or {}))
    return {
        "message_id": message.message_id,
        "event_id": event_id_of(message.body),
        "type": message.type,
        "correlation_id": message.correlation_id,
        "redelivered": bool(message.redelivered),
        "attempt": headers.get("x-attempt"),
        "last_error": headers.get("x-last-error"),
        "body_bytes": len(message.body),
        "body_preview": message.body[:PREVIEW_CHARS].decode("utf-8", errors="replace"),
    }


def dead_letter_queue(queue: str) -> str:
    """The ``.dlq`` of a work queue."""
    return queue if queue.endswith(DLQ_SUFFIX) else f"{queue}{DLQ_SUFFIX}"


def work_queue(queue: str) -> str:
    """The work queue a ``.dlq`` belongs to."""
    return queue[: -len(DLQ_SUFFIX)] if queue.endswith(DLQ_SUFFIX) else queue


async def _drain(
    queue: Any,
    *,
    limit: Optional[int],
    handle: Callable[[Any], Awaitable[bool]],
    stop: Callable[[], bool] = lambda: False,
) -> tuple[int, list[Any]]:
    """Fetch messages one at a time and offer each to ``handle``.

    ``handle`` returns ``True`` when it consumed the message (the message is
    then its job to acknowledge). A message it does not take is held and
    returned to the queue at the end, so it is never seen twice in one pass.
    ``stop`` ends the pass early (a failure: going on would only meet the same
    one again).

    Returns:
        ``(taken, untaken)``: how many ``handle`` consumed, and the messages
        still to be returned with ``nack(requeue=True)``.
    """
    taken = 0
    untaken: list[Any] = []
    while (limit is None or taken < limit) and not stop():
        message = await queue.get(no_ack=False, fail=False, timeout=GET_TIMEOUT_S)
        if message is None:
            break
        if await handle(message):
            taken += 1
        else:
            untaken.append(message)
    return taken, untaken


async def _give_back(messages: list[Any]) -> None:
    """Return held messages to their queue."""
    for message in messages:
        await message.nack(requeue=True)


async def publish(
    channel: Any, params: Mapping[str, Any], *, aio_pika: Any
) -> dict[str, Any]:
    """The ``publish`` command."""
    try:
        body = base64.b64decode(params["body_b64"], validate=True)
    except (KeyError, binascii.Error) as exc:
        raise AgentError("publish needs body_b64 (base64 of the body)") from exc
    message = aio_pika.Message(
        body=body,
        headers=dict(params.get("headers") or {}),
        message_id=params.get("message_id"),
        type=params.get("type"),
        content_type=params.get("content_type", "application/json"),
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    exchange = (
        channel.default_exchange
        if not params.get("exchange")
        else await channel.get_exchange(params["exchange"], ensure=True)
    )
    await publish_confirmed(exchange, message, params["routing_key"])
    return {"published": 1}


async def publish_confirmed(exchange: Any, message: Any, routing_key: str) -> None:
    """Publish ``message`` mandatory and wait for the broker's confirm.

    aio_pika answers a message the broker *returned* (no queue matched the
    routing key) with the returned message instead of raising, so that case is
    checked for here: it is an error, never a silent drop.

    Raises:
        AgentError: the broker returned the message as unroutable.
    """
    outcome = await exchange.publish(message, routing_key=routing_key, mandatory=True)
    if getattr(outcome, "delivery", None) is not None:
        raise AgentError(f"unroutable: no queue takes routing key {routing_key!r}")


async def peek(channel: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    """The ``peek`` command."""
    queue = await channel.get_queue(params["queue"], ensure=True)
    limit = int(params.get("limit", 20))
    held: list[Any] = []
    while len(held) < limit:
        message = await queue.get(no_ack=False, fail=False, timeout=GET_TIMEOUT_S)
        if message is None:
            break
        held.append(message)
    described = [describe_message(message) for message in held]
    await _give_back(held)
    return {"messages": described}


async def replay(
    channel: Any, params: Mapping[str, Any], *, aio_pika: Any, now: datetime
) -> dict[str, Any]:
    """The ``replay`` command."""
    target = work_queue(params["queue"])
    source = dead_letter_queue(target)
    wanted = params.get("message_id")
    dlq = await channel.get_queue(source, ensure=True)
    failures: list[str] = []

    async def move(message: Any) -> bool:
        if wanted is not None and not matches(message, wanted):
            return False
        headers = reset_headers(message.headers, source_queue=source, now=now)
        copy = aio_pika.Message(
            body=message.body,
            headers=headers,
            message_id=message.message_id,
            type=message.type,
            correlation_id=message.correlation_id,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        try:
            await publish_confirmed(channel.default_exchange, copy, target)
        except Exception as exc:  # noqa: BLE001 - report, keep the message parked
            failures.append(f"{message.message_id}: {type(exc).__name__}: {exc}")
            return False
        await message.ack()
        return True

    limit = params.get("limit")
    moved, held = await _drain(
        dlq,
        limit=int(limit) if limit is not None else None,
        handle=move,
        stop=lambda: bool(failures),
    )
    await _give_back(held)
    if failures:
        raise AgentError(f"replayed {moved}, then failed: {failures[0]}")
    return {"replayed": moved, "left": len(held), "to": target}


async def purge(channel: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    """The ``purge`` command."""
    name = params["queue"]
    if not name.endswith(DLQ_SUFFIX):
        raise AgentError(f"refusing to purge {name!r}: only *.dlq queues")
    queue = await channel.get_queue(name, ensure=True)
    wanted = params.get("message_id")
    if wanted is None:
        result = await queue.purge()
        return {"purged": int(getattr(result, "message_count", result) or 0)}

    async def drop(message: Any) -> bool:
        if not matches(message, wanted):
            return False
        await message.ack()
        return True

    dropped, held = await _drain(queue, limit=None, handle=drop)
    await _give_back(held)
    return {"purged": dropped}


async def run_command(
    command: str,
    params: Mapping[str, Any],
    *,
    url: str,
    aio_pika: Any,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Connect, run ``command`` and close; the connection is the seam the
    tests replace through ``aio_pika.connect``.

    Raises:
        AgentError: unknown command, or the command could not be carried out.
    """
    stamp = now or datetime.now(UTC)
    connection = await aio_pika.connect(url, timeout=CONNECT_TIMEOUT_S)
    try:
        channel = await connection.channel()
        if command == "publish":
            return await publish(channel, params, aio_pika=aio_pika)
        if command == "peek":
            return await peek(channel, params)
        if command == "replay":
            return await replay(channel, params, aio_pika=aio_pika, now=stamp)
        if command == "purge":
            return await purge(channel, params)
        raise AgentError(f"unknown command {command!r}")
    finally:
        await connection.close()


def main(argv: list[str]) -> int:
    """Entry point: ``python - <command> '<json params>'``."""
    import aio_pika  # imported here: the unit tests pass a fake instead

    try:
        if len(argv) != 3:
            raise AgentError("usage: python - <command> '<json params>'")
        params = json.loads(argv[2])
        url = os.environ.get("RABBITMQ_URL")
        if not url:
            raise AgentError("RABBITMQ_URL is not set in this container")
        outcome = asyncio.run(run_command(argv[1], params, url=url, aio_pika=aio_pika))
    except (AgentError, ValueError, KeyError, OSError, aio_pika.AMQPException) as exc:
        sys.stdout.write(
            json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n"
        )
        return 1
    sys.stdout.write(json.dumps({"ok": True, **outcome}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
r"""Dead-letter queues: list, inspect, replay one or all, purge (``make dlq``,
``make dlq-replay``, ``make dlq-purge``).

A message lands in ``<queue>.dlq`` when its consumer failed it three times
(``fcrm_common.events.deliver_with_retry``) or it could not even be parsed
(a poison message goes there at once). Nothing is ever dropped; this tool is
how an operator deals with what is parked.

* ``list`` shows every dead-letter queue with its depth.
* ``inspect <queue>`` shows the parked messages (id, type, attempts, last
  error, start of the body) and puts them back. Each look counts as one
  delivery of the message, and a dead-letter queue keeps a delivery limit
  (policy ``fcrm-quorum-safety``), so inspect a queue a few times, not in a
  loop.
* ``replay <queue>`` moves the parked messages back onto the work queue with
  the retry budget reset, so each gets its three attempts again. A message is
  removed from the dead-letter queue only after the broker confirmed the
  republish. ``--message-id`` replays one message, ``--limit`` the first n.
  Replay only after the cause is fixed: a message that is poison will fail
  again and park again.
* ``purge <queue> --confirm`` deletes the parked messages (``--message-id``
  deletes one). Without ``--confirm`` it only says what it would delete.

``<queue>`` is the work queue (``caseflow.steps``) or its ``.dlq``. The tool
needs only the ``docker`` CLI and the stack's container names
(``trustops-<service>-1``); it never reads ``.env`` and never prints a
credential, because the broker URL is read inside the harness container.

Usage::

    python ops/scripts/dlq.py list
    python ops/scripts/dlq.py inspect caseflow.steps [--limit 20]
    python ops/scripts/dlq.py replay caseflow.steps [--message-id evt_...]
    python ops/scripts/dlq.py purge caseflow.steps --confirm

Exit status: 0 done, 1 the broker or docker failed, 2 refused (unknown queue,
or a purge without ``--confirm``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from typing import Optional

from resilience.broker import DLQ_SUFFIX, Broker
from resilience.docker import DEFAULT_PROJECT, DockerCli
from resilience.errors import DrillError
from resilience.shell import SubprocessRunner

logger = logging.getLogger("dlq")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2

BrokerFactory = Callable[[str], Broker]


def default_broker(project: str) -> Broker:
    """A :class:`Broker` over the local ``docker`` CLI."""
    return Broker(DockerCli(SubprocessRunner(), project=project))


def dead_letter_name(queue: str) -> str:
    """``queue`` as a ``.dlq`` name (a work queue name gets the suffix)."""
    return queue if queue.endswith(DLQ_SUFFIX) else f"{queue}{DLQ_SUFFIX}"


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="compose project")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="every dead-letter queue and its depth")
    inspect = commands.add_parser("inspect", help="show the parked messages")
    inspect.add_argument("queue")
    inspect.add_argument("--limit", type=int, default=20)
    replay = commands.add_parser("replay", help="move messages back to the work queue")
    replay.add_argument("queue")
    replay.add_argument("--message-id")
    replay.add_argument("--limit", type=int)
    purge = commands.add_parser("purge", help="delete parked messages")
    purge.add_argument("queue")
    purge.add_argument("--message-id")
    purge.add_argument("--confirm", action="store_true", help="really delete")
    return parser


def _say(text: str) -> None:
    """One line on standard output (the tool's result, for a person or a pipe)."""
    sys.stdout.write(f"{text}\n")


def _depths(broker: Broker) -> dict[str, int]:
    return broker.dead_letter_depths()


def command_list(broker: Broker) -> int:
    """Print each dead-letter queue and its depth."""
    for name, depth in sorted(_depths(broker).items()):
        _say(f"{name:<40} {depth}")
    return EXIT_OK


def _known(broker: Broker, dlq: str) -> bool:
    """Whether ``dlq`` is an existing dead-letter queue (else say so)."""
    if dlq in _depths(broker):
        return True
    logger.error("dlq: no dead-letter queue named %s", dlq)
    return False


def command_inspect(broker: Broker, queue: str, limit: int) -> int:
    """Print the parked messages of ``queue``'s dead-letter queue."""
    dlq = dead_letter_name(queue)
    if not _known(broker, dlq):
        return EXIT_REFUSED
    messages = broker.peek(dlq, limit=limit)
    _say(f"{dlq}: {len(messages)} shown, {_depths(broker)[dlq]} in the queue")
    for message in messages:
        _say(json.dumps(message, ensure_ascii=False))
    return EXIT_OK


def command_replay(
    broker: Broker, queue: str, message_id: Optional[str], limit: Optional[int]
) -> int:
    """Replay parked messages onto the work queue."""
    dlq = dead_letter_name(queue)
    if not _known(broker, dlq):
        return EXIT_REFUSED
    work = dlq[: -len(DLQ_SUFFIX)]
    moved = broker.replay(work, limit=limit, message_id=message_id)
    _say(f"dlq-replay: moved {moved} message(s) from {dlq} to {work}")
    _say(f"dlq-replay: {dlq} now holds {_depths(broker)[dlq]}")
    return EXIT_OK


def command_purge(
    broker: Broker, queue: str, message_id: Optional[str], confirm: bool
) -> int:
    """Delete parked messages, only with ``confirm``."""
    dlq = dead_letter_name(queue)
    if not _known(broker, dlq):
        return EXIT_REFUSED
    if not confirm:
        what = f"message {message_id}" if message_id else "every message"
        logger.error(
            "dlq-purge: would delete %s of %s (%d in the queue); repeat with --confirm",
            what,
            dlq,
            _depths(broker)[dlq],
        )
        return EXIT_REFUSED
    purged = broker.purge(dlq, message_id=message_id)
    _say(f"dlq-purge: deleted {purged} message(s) from {dlq}")
    return EXIT_OK


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    broker_factory: BrokerFactory = default_broker,
) -> int:
    """Run one command; returns the exit status."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    args = build_parser().parse_args(argv)
    broker = broker_factory(args.project)
    try:
        if args.command == "list":
            return command_list(broker)
        if args.command == "inspect":
            return command_inspect(broker, args.queue, args.limit)
        if args.command == "replay":
            return command_replay(broker, args.queue, args.message_id, args.limit)
        return command_purge(broker, args.queue, args.message_id, args.confirm)
    except DrillError as exc:
        logger.error("dlq: %s", exc)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())

"""RabbitMQ as the drills and the DLQ tool see it.

Queue statistics come from ``rabbitmqctl`` inside the broker container; every
message operation (publish, peek, replay, purge) is the in-container script
:mod:`resilience.broker_agent`, run in the harness container, which already
holds the broker URL.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Optional

from resilience.docker import DockerCli
from resilience.errors import BrokerAgentError, CommandError

logger = logging.getLogger(__name__)

DLQ_SUFFIX: Final = ".dlq"
RETRY_SUFFIX: Final = ".retry"
AGENT_SOURCE_PATH: Final = Path(__file__).with_name("broker_agent.py")

#: The queues a healthy stack always has a consumer on (design A §4.1,
#: ``ops/rabbitmq/definitions.json``): H1's three, H2, H3 and H4.
CONSUMED_QUEUES: Final[tuple[str, ...]] = (
    "caseflow.events",
    "caseflow.agent-results",
    "caseflow.steps",
    "agent-worker.run",
    "ingestion-worker.run",
    "context-worker.run",
    "case-service.events",
    "policy-service.events",
)

_AGENT_TIMEOUT_S: Final = 90.0
_LIST_TIMEOUT_S: Final = 30.0


@dataclass(frozen=True)
class QueueStat:
    """One row of ``rabbitmqctl list_queues``.

    Attributes:
        name: The queue name.
        ready: Messages waiting to be delivered.
        unacked: Messages delivered and not yet acknowledged (in flight).
        consumers: Attached consumers.
    """

    name: str
    ready: int
    unacked: int
    consumers: int

    @property
    def depth(self) -> int:
        """Everything the queue still holds: ready plus in flight."""
        return self.ready + self.unacked


@dataclass(frozen=True)
class ReplayResult:
    """What a replay did.

    Attributes:
        moved: Messages put back on the work queue.
        left: Messages the script held and did not move (another id, or past
            ``limit``) and returned to the dead-letter queue.
    """

    moved: int
    left: int


def parse_list_queues(text: str) -> dict[str, QueueStat]:
    """Parse ``rabbitmqctl -q list_queues name messages_ready
    messages_unacknowledged consumers`` output (tab separated, header first
    unless ``-q`` dropped it).

    Raises:
        BrokerAgentError: a row does not have four columns of the right kind.
    """
    stats: dict[str, QueueStat] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        columns = line.split()
        if (
            len(columns) == 4
            and columns[0] == "name"
            and columns[1] == "messages_ready"
        ):
            continue
        if len(columns) != 4:
            raise BrokerAgentError(f"unexpected list_queues row: {line!r}")
        try:
            numbers = [int(column) for column in columns[1:]]
        except ValueError as exc:
            raise BrokerAgentError(f"unexpected list_queues row: {line!r}") from exc
        stats[columns[0]] = QueueStat(columns[0], *numbers)
    return stats


def encode_body(body: bytes) -> str:
    """``body`` as the base64 text the agent's ``publish`` takes."""
    return base64.b64encode(body).decode("ascii")


class Broker:
    """The broker, through docker.

    Args:
        docker: The docker CLI wrapper.
        rabbit_service: The broker's compose service name.
        agent_service: The service whose container runs the broker script
            (it needs ``aio_pika`` and ``RABBITMQ_URL``).
    """

    def __init__(
        self,
        docker: DockerCli,
        *,
        rabbit_service: str = "rabbitmq",
        agent_service: str = "harness",
    ) -> None:
        self._docker = docker
        self._rabbit = rabbit_service
        self._agent_service = agent_service

    # -- statistics -------------------------------------------------------

    def queues(self) -> dict[str, QueueStat]:
        """Every queue's depth and consumer count.

        Raises:
            CommandError: the broker container is down.
        """
        result = self._docker.exec(
            self._rabbit,
            [
                "rabbitmqctl",
                "-q",
                "list_queues",
                "name",
                "messages_ready",
                "messages_unacknowledged",
                "consumers",
            ],
            timeout_s=_LIST_TIMEOUT_S,
        )
        return parse_list_queues(result.stdout)

    def depth(self, queue: str) -> int:
        """What ``queue`` still holds (ready plus in flight).

        Raises:
            BrokerAgentError: there is no such queue.
        """
        stats = self.queues()
        if queue not in stats:
            raise BrokerAgentError(f"no queue named {queue!r}")
        return stats[queue].depth

    def dead_letter_depths(self) -> dict[str, int]:
        """``{queue: depth}`` of every ``*.dlq``."""
        return {
            name: stat.depth
            for name, stat in self.queues().items()
            if name.endswith(DLQ_SUFFIX)
        }

    def consumerless(self) -> list[str]:
        """The work queues that have no consumer attached right now."""
        stats = self.queues()
        return [q for q in CONSUMED_QUEUES if q not in stats or stats[q].consumers < 1]

    # -- messages ---------------------------------------------------------

    def publish(
        self,
        routing_key: str,
        body: bytes,
        *,
        exchange: str = "",
        headers: Optional[Mapping[str, Any]] = None,
        message_id: Optional[str] = None,
        message_type: Optional[str] = None,
        content_type: str = "application/json",
    ) -> None:
        """Publish one persistent message and wait for the broker's confirm.

        ``exchange=""`` is the default exchange: ``routing_key`` is then a
        queue name, so only that queue gets the message (publishing to the
        topic exchange would also reach every queue bound with ``#``).

        Raises:
            BrokerAgentError: unroutable, refused, or the broker is down.
        """
        self._agent(
            "publish",
            {
                "exchange": exchange,
                "routing_key": routing_key,
                "body_b64": encode_body(body),
                "headers": dict(headers or {}),
                "message_id": message_id,
                "type": message_type,
                "content_type": content_type,
            },
        )

    def peek(self, queue: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Describe up to ``limit`` messages of ``queue`` and put them back."""
        outcome = self._agent("peek", {"queue": queue, "limit": limit})
        messages = outcome.get("messages")
        return messages if isinstance(messages, list) else []

    def replay(
        self,
        queue: str,
        *,
        limit: Optional[int] = None,
        message_id: Optional[str] = None,
    ) -> ReplayResult:
        """Move messages of ``<queue>.dlq`` back onto ``<queue>`` with a fresh
        retry budget; says how many moved and how many were left parked.

        The counts are the script's own, taken while it held the messages. The
        queue statistics of ``rabbitmqctl`` lag by up to five seconds, so they
        are no way to confirm a replay a moment later.
        """
        outcome = self._agent(
            "replay", {"queue": queue, "limit": limit, "message_id": message_id}
        )
        return ReplayResult(
            moved=int(outcome.get("replayed", 0)), left=int(outcome.get("left", 0))
        )

    def purge(self, dlq: str, *, message_id: Optional[str] = None) -> int:
        """Delete messages of a ``*.dlq`` queue; returns how many went.

        Raises:
            BrokerAgentError: ``dlq`` is not a dead-letter queue.
        """
        if not dlq.endswith(DLQ_SUFFIX):
            raise BrokerAgentError(f"refusing to purge {dlq!r}: only *.dlq queues")
        outcome = self._agent("purge", {"queue": dlq, "message_id": message_id})
        return int(outcome.get("purged", 0))

    # -- the in-container script -----------------------------------------

    def _agent(self, command: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Run one broker-script command and return its JSON result."""
        source = AGENT_SOURCE_PATH.read_text(encoding="utf-8")
        try:
            result = self._docker.exec(
                self._agent_service,
                ["python", "-", command, json.dumps(params)],
                input_text=source,
                timeout_s=_AGENT_TIMEOUT_S,
            )
        except CommandError as exc:
            raise BrokerAgentError(f"broker {command} failed: {exc}") from exc
        return _last_json(result.stdout, command)


def _last_json(stdout: str, command: str) -> dict[str, Any]:
    """The JSON object on the last line of ``stdout``.

    Raises:
        BrokerAgentError: there is none, or it reports ``ok: false``.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise BrokerAgentError(f"broker {command} printed nothing")
    try:
        outcome = json.loads(lines[-1])
    except ValueError as exc:
        raise BrokerAgentError(f"broker {command}: unreadable output") from exc
    if not isinstance(outcome, dict) or not outcome.get("ok"):
        raise BrokerAgentError(f"broker {command} failed: {outcome}")
    return outcome

"""Fakes for the drill helpers' unit tests (``ops/scripts/resilience``).

Nothing here touches docker, a broker or the network: a runner that replays
scripted command results, a gateway that answers from a route table, and a
minimal ``aio_pika`` for the in-container broker script.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional

import pytest
from resilience.docker import DockerCli
from resilience.errors import CommandError
from resilience.gateway import Reply
from resilience.shell import CommandResult

FROZEN_NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)


class FakeRunner:
    """A :class:`resilience.shell.CommandRunner` that replays scripted results
    and records every call.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._script: list[Any] = []
        #: Called with each argv before the result is returned (a test uses it
        #: to make a command change the world it fakes).
        self.on_run: Optional[Callable[[list[str]], None]] = None

    def reply(self, stdout: str = "", *, returncode: int = 0, stderr: str = "") -> None:
        """Queue the next command's result."""
        self._script.append((stdout, returncode, stderr))

    def fail(self, exc: Exception) -> None:
        """Queue an exception for the next command."""
        self._script.append(exc)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        input_text: Optional[str] = None,
        check: bool = True,
    ) -> CommandResult:
        """Record the call and return the next scripted result."""
        self.calls.append(
            {
                "argv": list(argv),
                "timeout_s": timeout_s,
                "input_text": input_text,
                "check": check,
            }
        )
        if self.on_run is not None:
            self.on_run(list(argv))
        if not self._script:
            raise AssertionError(f"unscripted command: {list(argv)}")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        stdout, returncode, stderr = item
        if check and returncode != 0:
            raise CommandError(f"exit {returncode}: {stderr or stdout}")
        return CommandResult(tuple(argv), returncode, stdout, stderr)


@pytest.fixture
def runner() -> FakeRunner:
    """A fresh scripted command runner."""
    return FakeRunner()


@pytest.fixture
def docker(runner: FakeRunner) -> DockerCli:
    """The docker wrapper over the scripted runner (project ``trustops``)."""
    return DockerCli(runner)


class FakeReader:
    """A case stream that has already read everything it will."""

    def __init__(
        self,
        seqs: list[int],
        *,
        ended: Optional[str] = "closed by the server",
        status: Optional[int] = 200,
    ) -> None:
        self.seqs = seqs
        self.ended = ended
        self.status = status
        self.stopped = False

    def wait_for(
        self, predicate: Callable[[list[int]], bool], *, timeout_s: float
    ) -> bool:
        """Whether the predicate holds of what was read."""
        return predicate(self.seqs)

    def stop(self) -> None:
        """Note that the drill finished with this reader."""
        self.stopped = True


class FakeGateway:
    """A gateway client answering from a table of ``(method, path)`` routes.

    A route's value is a :class:`Reply`, or a list of replies served in order
    (the last one repeats).
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Any] = {}
        self.calls: list[dict[str, Any]] = []
        #: Readers :meth:`open_stream` hands out, in order.
        self.streams: list[FakeReader] = []

    def route(self, method: str, path: str, *replies: Reply) -> None:
        """Answer ``method path`` with ``replies`` in order."""
        self.routes[(method, path)] = list(replies)

    def route_fn(self, method: str, path: str, make: Callable[[], Reply]) -> None:
        """Answer ``method path`` by calling ``make`` each time (the answer can
        change as the test's fake world does).
        """
        self.routes[(method, path)] = make

    def _answer(self, method: str, path: str) -> Reply:
        replies = self.routes.get((method, path))
        if callable(replies):
            return replies()
        if not replies:
            raise AssertionError(f"unrouted call: {method} {path}")
        return replies.pop(0) if len(replies) > 1 else replies[0]

    def login(self, persona: str) -> str:
        """A token named for the persona."""
        return f"token-{persona}"

    def open_stream(
        self, persona: str, case_id: str, *, last_event_id: Optional[int] = None
    ) -> FakeReader:
        """The next scripted reader; the call is recorded."""
        self.calls.append(
            {
                "persona": persona,
                "method": "STREAM",
                "path": case_id,
                "last_event_id": last_event_id,
            }
        )
        return self.streams.pop(0)

    def token(self, persona: str) -> str:
        """The persona's token."""
        return f"token-{persona}"

    def call(
        self, persona: Optional[str], method: str, path: str, **kwargs: Any
    ) -> Reply:
        """Record and answer one call."""
        self.calls.append(
            {"persona": persona, "method": method, "path": path, **kwargs}
        )
        return self._answer(method, path)

    def get(self, persona: str, path: str, **kwargs: Any) -> Reply:
        """``GET`` as ``persona``."""
        return self.call(persona, "GET", path, **kwargs)

    def post(self, persona: str, path: str, **kwargs: Any) -> Reply:
        """``POST`` as ``persona``."""
        return self.call(persona, "POST", path, **kwargs)

    def pages(
        self, persona: str, path: str, *, params: Optional[Mapping[str, Any]] = None
    ) -> list[Any]:
        """The items of a one-page list route."""
        self.calls.append({"persona": persona, "method": "PAGES", "path": path})
        body = self._answer("GET", path).require(200).body
        return list(body["items"])


@pytest.fixture
def gateway() -> FakeGateway:
    """A gateway with no routes yet."""
    return FakeGateway()


@pytest.fixture
def reply() -> Callable[..., Reply]:
    """``reply(body, status=200, content_type="application/json")`` builds a
    :class:`Reply`.
    """

    def build(
        body: Any, status: int = 200, content_type: str = "application/json"
    ) -> Reply:
        return Reply(status, content_type, body, 0.01)

    return build


# ---------------------------------------------------------------- aio_pika


@dataclass
class FakeIncoming:
    """One delivered message of the fake queue."""

    body: bytes
    message_id: str
    headers: dict[str, Any] = field(default_factory=dict)
    type: Optional[str] = "harness.step"
    correlation_id: Optional[str] = "run_1"
    content_type: str = "application/json"
    redelivered: bool = False
    state: str = "unacked"

    async def ack(self) -> None:
        """Acknowledge."""
        self.state = "acked"

    async def nack(self, requeue: bool = True) -> None:
        """Return (or drop) the message."""
        self.state = "requeued" if requeue else "dropped"


class FakeQueue:
    """A queue handing out its messages once each, like ``basic.get``."""

    def __init__(self, messages: list[FakeIncoming]) -> None:
        self.messages = messages
        self.purged = False

    async def get(
        self, *, no_ack: bool = False, fail: bool = True, timeout: float = 5
    ) -> Optional[FakeIncoming]:
        """The next message still waiting, or ``None``."""
        for message in self.messages:
            if message.state == "waiting":
                message.state = "unacked"
                return message
        return None

    async def purge(self) -> Any:
        """Drop everything."""
        self.purged = True
        count = len(self.messages)
        self.messages.clear()
        return type("PurgeOk", (), {"message_count": count})()


class FakeExchange:
    """The default exchange: records what is published, or refuses."""

    def __init__(self) -> None:
        self.published: list[tuple[Any, str]] = []
        self.refuse: Optional[Exception] = None
        #: Answer as aio_pika does for a message the broker returned.
        self.unroutable = False

    async def publish(
        self, message: Any, routing_key: str, *, mandatory: bool = True
    ) -> Any:
        """Record the publish, raise the scripted refusal, or answer that the
        broker returned the message (nothing matched the routing key).
        """
        if self.refuse is not None:
            raise self.refuse
        if self.unroutable:
            return type("DeliveredMessage", (), {"delivery": object()})()
        self.published.append((message, routing_key))
        return type("Ack", (), {"delivery_tag": 1})()


class FakeChannel:
    """A channel over fake queues and one default exchange."""

    def __init__(self, queues: dict[str, FakeQueue]) -> None:
        self.queues = queues
        self.default_exchange = FakeExchange()

    async def get_queue(self, name: str, *, ensure: bool = True) -> FakeQueue:
        """The named queue."""
        return self.queues[name]

    async def get_exchange(self, name: str, *, ensure: bool = True) -> FakeExchange:
        """Any named exchange is the same recording exchange."""
        return self.default_exchange


class FakeConnection:
    """A connection that opens one channel."""

    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel
        self.closed = False

    async def channel(self) -> FakeChannel:
        """The channel."""
        return self._channel

    async def close(self) -> None:
        """Close."""
        self.closed = True


class FakeAioPika:
    """The few names of ``aio_pika`` the broker script uses."""

    class DeliveryMode:
        PERSISTENT = 2

    class Message:
        def __init__(self, body: bytes, **properties: Any) -> None:
            self.body = body
            self.properties = properties

    def __init__(self, channel: FakeChannel) -> None:
        self.connection = FakeConnection(channel)
        self.urls: list[str] = []

    async def connect(self, url: str, *, timeout: float = 10) -> FakeConnection:
        """Record the URL and hand out the connection."""
        self.urls.append(url)
        return self.connection


@pytest.fixture
def make_pika() -> Callable[[dict[str, list[FakeIncoming]]], FakeAioPika]:
    """Build a fake ``aio_pika`` whose queues hold the given messages (their
    state set to ``waiting``).
    """

    def build(queues: dict[str, list[FakeIncoming]]) -> FakeAioPika:
        for messages in queues.values():
            for message in messages:
                message.state = "waiting"
        channel = FakeChannel({n: FakeQueue(m) for n, m in queues.items()})
        return FakeAioPika(channel)

    return build


@pytest.fixture
def json_line() -> Callable[..., str]:
    """``json_line(ok=True, replayed=1)``: a JSON line as the in-container
    script prints it.
    """

    def build(**fields: Any) -> str:
        return json.dumps(fields) + "\n"

    return build


@pytest.fixture
def incoming() -> Callable[..., FakeIncoming]:
    """``incoming(body, message_id, **fields)`` builds a parked message."""

    def build(body: bytes, message_id: str, **fields: Any) -> FakeIncoming:
        return FakeIncoming(body=body, message_id=message_id, **fields)

    return build


@pytest.fixture
def reader() -> Callable[..., FakeReader]:
    """``reader([1, 2, 3])`` builds a stream that already read those ``seq``."""

    def build(seqs: list[int], **fields: Any) -> FakeReader:
        return FakeReader(seqs, **fields)

    return build


@pytest.fixture
def frozen_now() -> datetime:
    """A fixed UTC instant."""
    return FROZEN_NOW


# ------------------------------------------------------------- a fake stack


class FakeWorld:
    """What the fake docker, broker and gateway of one test share."""

    def __init__(self) -> None:
        self.inflight = True
        self.dlq_depth: dict[str, int] = {}
        self.retry_depth = 0
        self.log: list[str] = []
        #: One callable per ``queues()`` call, run before it answers (a test
        #: uses them to move the world on between two polls).
        self.script: list[Callable[[], None]] = []


class FakeDocker:
    """Container operations over a :class:`FakeWorld`; every call is logged."""

    project = "trustops"

    def __init__(self, world: FakeWorld) -> None:
        self._world = world
        self.runs: dict[str, int] = {}
        self.logs = ""
        #: Log texts handed out one per call, before ``logs`` repeats.
        self.log_script: list[str] = []
        self.ready_codes: dict[str, int] = {}
        self.adapter_fixed = True
        self.exec_fails = False

    def _note(self, what: str) -> None:
        self._world.log.append(what)

    def container(self, service: str) -> str:
        """The container name."""
        return f"trustops-{service}-1"

    def state(self, service: str) -> Any:
        """A running, healthy container whose start time names its run."""
        from resilience.docker import ContainerState

        return ContainerState(
            "running", "healthy", f"run-{self.runs.get(service, 0)}", 0
        )

    def _new_run(self, service: str) -> None:
        self.runs[service] = self.runs.get(service, 0) + 1
        self._world.inflight = False

    def restart(self, service: str, *, grace_s: int) -> None:
        """Log and start a new run."""
        self._note(f"restart {service} {grace_s}")
        self._new_run(service)

    def kill(self, service: str) -> None:
        """Log and start a new run."""
        self._note(f"kill {service}")
        self._new_run(service)

    def stop(self, service: str, *, grace_s: int = 30) -> None:
        """Log the stop."""
        self._note(f"stop {service}")

    def start(self, service: str) -> None:
        """Log and start a new run."""
        self._note(f"start {service}")
        self._new_run(service)

    def ensure_running(self, service: str) -> None:
        """Log the check."""
        self._note(f"ensure_running {service}")

    def wait_restarted(
        self, service: str, *, previous_started_at: str, timeout_s: float
    ) -> Any:
        """Log and return the current run."""
        self._note(f"wait_restarted {service}")
        return self.state(service)

    def wait_ready(self, service: str, *, timeout_s: float) -> Any:
        """Log and answer ready."""
        from resilience.docker import Readyz

        self._note(f"wait_ready {service}")
        return Readyz(200, {})

    def readyz(self, service: str) -> Any:
        """The scripted readiness (200 unless set)."""
        from resilience.docker import Readyz

        return Readyz(self.ready_codes.get(service, 200), {})

    def logs_since(self, service: str, since: str, *, timeout_s: float = 30.0) -> str:
        """The next scripted log text, else the standing one."""
        return self.log_script.pop(0) if self.log_script else self.logs

    def exec(self, service: str, argv: list[str], **kwargs: Any) -> CommandResult:
        """Answer the adapter probe: ``True`` while ``adapter_fixed`` is set,
        a failing command when ``exec_fails`` is.
        """
        self._note(f"exec {service}")
        if self.exec_fails:
            raise CommandError("no such container")
        return CommandResult(
            tuple(argv), 0, "True\n" if self.adapter_fixed else "False\n", ""
        )


class FakeBroker:
    """Queue statistics and message operations over a :class:`FakeWorld`."""

    def __init__(self, world: FakeWorld) -> None:
        self._world = world
        self.published: list[dict[str, Any]] = []
        self.purged: list[tuple[str, Optional[str]]] = []
        self.shown: list[dict[str, Any]] = []
        self.on_publish: Optional[Callable[[dict[str, Any]], None]] = None

    def queues(self) -> dict[str, Any]:
        """Every consumed queue with one consumer; one job in flight until the
        world says the harness was interrupted; the DLQs from the world.
        """
        from resilience.broker import CONSUMED_QUEUES, QueueStat

        if self._world.script:
            self._world.script.pop(0)()
        stats = {name: QueueStat(name, 0, 0, 1) for name in CONSUMED_QUEUES}
        stats["agent-worker.run"] = QueueStat(
            "agent-worker.run", 0, 1 if self._world.inflight else 0, 1
        )
        stats["caseflow.steps.retry"] = QueueStat(
            "caseflow.steps.retry", self._world.retry_depth, 0, 0
        )
        for name, depth in self._world.dlq_depth.items():
            stats[name] = QueueStat(name, depth, 0, 0)
        return stats

    def depth(self, queue: str) -> int:
        """What ``queue`` holds."""
        return int(self.queues()[queue].depth)

    def dead_letter_depths(self) -> dict[str, int]:
        """The world's DLQ depths."""
        return dict(self._world.dlq_depth)

    def consumerless(self) -> list[str]:
        """Nothing is ever without a consumer."""
        return []

    def publish(self, routing_key: str, body: bytes, **kwargs: Any) -> None:
        """Record the publish and let the test react to it."""
        entry = {"routing_key": routing_key, "body": body, **kwargs}
        self.published.append(entry)
        if self.on_publish is not None:
            self.on_publish(entry)

    def peek(self, queue: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """The scripted description of the parked messages."""
        return list(self.shown)

    def purge(self, dlq: str, *, message_id: Optional[str] = None) -> int:
        """Record the purge."""
        self.purged.append((dlq, message_id))
        return 1


class FakeMongo:
    """``find`` answered from a table keyed by collection."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.queries: list[tuple[str, str, dict[str, Any]]] = []

    def find(
        self,
        database: str,
        collection: str,
        query: Mapping[str, Any],
        *,
        projection: Optional[Mapping[str, Any]] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """The scripted rows of ``collection``."""
        self.queries.append((database, collection, dict(query)))
        return list(self.rows.get(collection, []))


@dataclass
class FakeStack:
    """A stand-in for :class:`resilience.stack.Stack`."""

    docker: FakeDocker
    broker: FakeBroker
    gateway: FakeGateway
    mongo: FakeMongo
    runner: FakeRunner
    world: FakeWorld


@pytest.fixture
def stack(gateway: FakeGateway, runner: FakeRunner) -> FakeStack:
    """A fake stack whose docker, broker and gateway share one world."""
    world = FakeWorld()
    return FakeStack(
        docker=FakeDocker(world),
        broker=FakeBroker(world),
        gateway=gateway,
        mongo=FakeMongo(),
        runner=runner,
        world=world,
    )


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every pause in the helpers returns at once."""
    import time

    monkeypatch.setattr(time, "sleep", lambda seconds: None)

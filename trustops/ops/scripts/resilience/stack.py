"""The running stack as one object: docker, the broker and the gateway."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Optional

from resilience.broker import Broker
from resilience.docker import DEFAULT_PROJECT, DockerCli
from resilience.errors import CommandError
from resilience.gateway import DEFAULT_BASE_URL, GatewayClient
from resilience.mongo import MongoReader
from resilience.shell import CommandRunner, SubprocessRunner

logger = logging.getLogger(__name__)

#: The containers every drill expects to find healthy before it starts.
REQUIRED_SERVICES: Final[tuple[str, ...]] = (
    "mongo",
    "rabbitmq",
    "opa",
    "api",
    "harness",
    "gateway",
)


@dataclass(frozen=True)
class Stack:
    """Handles on the running stack.

    Attributes:
        docker: Container operations.
        broker: Queue statistics and message operations.
        gateway: HTTP through the gateway as the demo personas.
        mongo: Read-only ``find`` over Mongo, for what no route shows.
        runner: Runs local commands (``make`` for the DLQ drills).
    """

    docker: DockerCli
    broker: Broker
    gateway: GatewayClient
    mongo: MongoReader
    runner: CommandRunner

    @classmethod
    def connect(
        cls,
        *,
        project: str = DEFAULT_PROJECT,
        base_url: str = DEFAULT_BASE_URL,
        runner: Optional[CommandRunner] = None,
        gateway: Optional[GatewayClient] = None,
    ) -> Stack:
        """Build the handles (nothing is contacted yet)."""
        runner = runner or SubprocessRunner()
        docker = DockerCli(runner, project=project)
        return cls(
            docker=docker,
            broker=Broker(docker),
            gateway=gateway or GatewayClient(base_url),
            mongo=MongoReader(docker),
            runner=runner,
        )

    def problems(self) -> list[str]:
        """What stops the drills from starting; empty when the stack is up:
        every required container healthy, the gateway answering, and a
        consumer on every work queue.
        """
        found: list[str] = []
        for service in REQUIRED_SERVICES:
            try:
                state = self.docker.state(service)
            except CommandError as exc:
                found.append(f"{self.docker.container(service)}: {exc}")
                continue
            if not state.healthy:
                found.append(
                    f"{self.docker.container(service)} is {state.status}"
                    f" ({state.health or 'no healthcheck'})"
                )
        if self.gateway.healthz().status != 200:
            found.append(f"gateway {self.gateway.base_url} does not answer /healthz")
        if not found:
            try:
                found.extend(
                    f"queue {q} has no consumer" for q in self.broker.consumerless()
                )
            except CommandError as exc:
                found.append(f"cannot list queues: {exc}")
        return found

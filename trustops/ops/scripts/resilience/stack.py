"""The running stack as one object: docker, the broker and the gateway."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Optional

from resilience.broker import Broker
from resilience.docker import DEFAULT_PROJECT, DockerCli
from resilience.errors import CommandError
from resilience.gateway import DEFAULT_BASE_URL, GatewayClient
from resilience.mongo import MongoReader
from resilience.shell import CommandRunner, SubprocessRunner

logger = logging.getLogger(__name__)

#: The variables that point the drills at another stack.
ENV_PROJECT: Final = "FCRM_COMPOSE_PROJECT"
ENV_GATEWAY_URL: Final = "FCRM_GATEWAY_URL"

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

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str], *, runner: Optional[CommandRunner] = None
    ) -> Stack:
        """Handles on the stack ``environ`` names: ``FCRM_COMPOSE_PROJECT`` (the
        compose project, ``trustops`` by default, so containers are
        ``<project>-<service>-1``) and ``FCRM_GATEWAY_URL`` (the gateway's API
        root, ``http://localhost:8080/api/fcrm`` by default). Another stack run
        under its own project name and gateway port is how the drills run next
        to one someone else is using.
        """
        return cls.connect(
            project=environ.get(ENV_PROJECT) or DEFAULT_PROJECT,
            base_url=environ.get(ENV_GATEWAY_URL) or DEFAULT_BASE_URL,
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

"""The ``docker`` CLI by container name: restart, kill, stop, start, exec, probe.

Containers are addressed as ``<project>-<service>-1`` (compose's names), so
nothing here needs ``docker compose``, a ``.env`` file or the repo checkout
the stack was started from.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Optional

from resilience.errors import CommandError, DrillError
from resilience.polling import poll_until
from resilience.shell import CommandResult, CommandRunner

logger = logging.getLogger(__name__)

DEFAULT_PROJECT: Final = "trustops"

#: Where each Python process serves ``/readyz`` inside its own container.
READYZ_PORTS: Final[dict[str, int]] = {"api": 8000, "harness": 9100}

#: Seconds a plain ``docker`` call may take (``restart``/``stop`` add their grace).
_CALL_TIMEOUT_S: Final = 60.0

#: Run inside the container: GET the URL in argv, print ``{"status", "body"}``.
_READYZ_SCRIPT: Final = (
    "import json, sys, urllib.error, urllib.request\n"
    "try:\n"
    "    reply = urllib.request.urlopen(sys.argv[1], timeout=4)\n"
    "    code, raw = reply.status, reply.read()\n"
    "except urllib.error.HTTPError as exc:\n"
    "    code, raw = exc.code, exc.read()\n"
    "try:\n"
    "    body = json.loads(raw)\n"
    "except ValueError:\n"
    "    body = {}\n"
    "print(json.dumps({'status': code, 'body': body}))\n"
)


@dataclass(frozen=True)
class ContainerState:
    """The part of ``docker inspect`` the drills read.

    Attributes:
        status: ``running``, ``exited``, ``restarting`` ...
        health: ``healthy``, ``unhealthy``, ``starting``; ``None`` when the
            container has no healthcheck.
        started_at: When the current run started (changes on every restart).
        exit_code: Exit status of the last run.
    """

    status: str
    health: Optional[str]
    started_at: str
    exit_code: int

    @property
    def running(self) -> bool:
        """Whether the container is up."""
        return self.status == "running"

    @property
    def healthy(self) -> bool:
        """Whether it is up and, when it has a healthcheck, passing it."""
        return self.running and self.health in (None, "healthy")


@dataclass(frozen=True)
class Readyz:
    """One ``/readyz`` probe.

    Attributes:
        status_code: The HTTP status; ``0`` when nothing answered.
        body: The parsed JSON body (``status`` and ``checks``), or ``{}``.
    """

    status_code: int
    body: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        """Whether the service answered 200."""
        return self.status_code == 200


def parse_state(raw: str) -> ContainerState:
    """Parse the JSON of ``docker inspect --format '{{json .State}}'``.

    Raises:
        DrillError: ``raw`` is not the JSON object ``docker`` prints.
    """
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DrillError(f"unreadable docker inspect output: {raw[:200]!r}") from exc
    if not isinstance(data, dict):
        raise DrillError(f"unexpected docker inspect output: {raw[:200]!r}")
    health = data.get("Health")
    return ContainerState(
        status=str(data.get("Status", "unknown")),
        health=str(health["Status"]) if isinstance(health, dict) else None,
        started_at=str(data.get("StartedAt", "")),
        exit_code=int(data.get("ExitCode", 0) or 0),
    )


class DockerCli:
    """Operations on the stack's containers by service name.

    Args:
        runner: Runs the ``docker`` commands.
        project: The compose project name (``trustops``).
    """

    def __init__(
        self, runner: CommandRunner, *, project: str = DEFAULT_PROJECT
    ) -> None:
        self._runner = runner
        self._project = project

    def container(self, service: str) -> str:
        """The container name of ``service`` (``trustops-api-1``)."""
        return f"{self._project}-{service}-1"

    def _docker(
        self,
        *args: str,
        timeout_s: float = _CALL_TIMEOUT_S,
        input_text: Optional[str] = None,
        check: bool = True,
    ) -> CommandResult:
        return self._runner.run(
            ["docker", *args],
            timeout_s=timeout_s,
            input_text=input_text,
            check=check,
        )

    def state(self, service: str) -> ContainerState:
        """The container's state.

        Raises:
            CommandError: the container does not exist or docker is down.
        """
        result = self._docker(
            "inspect", "--format", "{{json .State}}", self.container(service)
        )
        return parse_state(result.stdout.strip())

    def restart(self, service: str, *, grace_s: int) -> None:
        """``docker restart``: SIGTERM, wait up to ``grace_s``, then SIGKILL."""
        self._docker(
            "restart",
            "-t",
            str(grace_s),
            self.container(service),
            timeout_s=grace_s + _CALL_TIMEOUT_S,
        )

    def kill(self, service: str) -> None:
        """``docker kill``: SIGKILL, no chance to drain or ack."""
        self._docker("kill", self.container(service))

    def stop(self, service: str, *, grace_s: int = 30) -> None:
        """``docker stop`` with ``grace_s`` before the kill."""
        self._docker(
            "stop",
            "-t",
            str(grace_s),
            self.container(service),
            timeout_s=grace_s + _CALL_TIMEOUT_S,
        )

    def start(self, service: str) -> None:
        """``docker start`` (a no-op when it is already running)."""
        self._docker("start", self.container(service))

    def ensure_running(self, service: str) -> None:
        """Start ``service`` unless the restart policy already did."""
        try:
            running = self.state(service).running
        except CommandError:
            running = False
        if not running:
            self.start(service)

    def exec(
        self,
        service: str,
        argv: Sequence[str],
        *,
        input_text: Optional[str] = None,
        timeout_s: float = _CALL_TIMEOUT_S,
    ) -> CommandResult:
        """Run ``argv`` inside the container.

        With ``input_text`` the command gets it on standard input
        (``docker exec -i``), which is how a script is sent without ever
        putting it, or a credential it reads from the container's own
        environment, on a command line.
        """
        flags = ["-i"] if input_text is not None else []
        return self._docker(
            "exec",
            *flags,
            self.container(service),
            *argv,
            timeout_s=timeout_s,
            input_text=input_text,
        )

    def logs_since(self, service: str, since: str, *, timeout_s: float = 30.0) -> str:
        """The container's log (both streams) since ``since`` (an RFC 3339 time
        or a duration such as ``2m``).
        """
        result = self._docker(
            "logs", "--since", since, self.container(service), timeout_s=timeout_s
        )
        return result.stdout + result.stderr

    def readyz(self, service: str) -> Readyz:
        """Probe ``/readyz`` from inside the container. Never raises: a
        container that is down, or a service that refuses the connection, is
        ``Readyz(0)``.
        """
        port = READYZ_PORTS[service]
        try:
            result = self.exec(
                service,
                ["python", "-c", _READYZ_SCRIPT, f"http://localhost:{port}/readyz"],
                timeout_s=15.0,
            )
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except (CommandError, ValueError, IndexError) as exc:
            logger.debug("readyz of %s unreachable: %s", service, exc)
            return Readyz(0, {"error": str(exc)[:200]})
        body = data.get("body")
        return Readyz(
            int(data.get("status", 0)), body if isinstance(body, dict) else {}
        )

    def wait_ready(self, service: str, *, timeout_s: float) -> Readyz:
        """Block until ``/readyz`` answers 200.

        Raises:
            PollTimeoutError: it did not within ``timeout_s``.
        """

        def probe() -> Optional[Readyz]:
            reply = self.readyz(service)
            return reply if reply.ready else None

        return poll_until(
            probe,
            what=f"{service} /readyz to answer 200",
            timeout_s=timeout_s,
            interval_s=1.0,
        )

    def wait_restarted(
        self, service: str, *, previous_started_at: str, timeout_s: float
    ) -> ContainerState:
        """Block until ``service`` runs again with a start time newer than
        ``previous_started_at`` (it started a new run: the kill took effect and
        the restart policy, or :meth:`ensure_running`, brought it back).

        Raises:
            PollTimeoutError: no new run started within ``timeout_s``.
        """

        def probe() -> Optional[ContainerState]:
            current = self.state(service)
            if current.running and current.started_at != previous_started_at:
                return current
            return None

        return poll_until(
            probe,
            what=f"{service} to start a new run",
            timeout_s=timeout_s,
            interval_s=0.5,
            tolerate=(CommandError,),
        )

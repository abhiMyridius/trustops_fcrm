"""Read-only look at Mongo documents, for what no API route shows.

The drills prefer the API's effects. A few facts only H1 holds (the run's state,
an open gate) have no route, so this reads them with ``mongosh`` *inside the
mongo container*, as the root user the container already has: the script goes
in on standard input and the credentials are expanded by the container's own
shell, so neither appears on a command line, in a log or in this process.
Only ``find`` is offered; nothing here can write.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any, Final, Optional

from resilience.docker import DockerCli
from resilience.errors import DrillError

logger = logging.getLogger(__name__)

#: Runs the script on stdin as the container's root user.
MONGOSH_SHELL: Final = (
    'mongosh --quiet --norc "mongodb://localhost:27017/admin?directConnection=true" '
    '--username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" '
    '--authenticationDatabase admin --eval "$(cat)"'
)

_FIND_TIMEOUT_S: Final = 60.0


def find_script(
    database: str,
    collection: str,
    query: Mapping[str, Any],
    projection: Optional[Mapping[str, Any]],
    limit: int,
) -> str:
    """The ``mongosh`` script that prints the matching documents as one line of
    relaxed extended JSON. Names and values travel as JSON literals, never as
    spliced code.
    """
    return (
        f"const rows = db.getSiblingDB({json.dumps(database)})"
        f".getCollection({json.dumps(collection)})"
        f".find({json.dumps(dict(query))}, {json.dumps(dict(projection or {}))})"
        f".limit({int(limit)}).toArray();\n"
        "print(EJSON.stringify(rows, {relaxed: true}));\n"
    )


class MongoReader:
    """``find`` over the stack's Mongo, through docker.

    Args:
        docker: The docker CLI wrapper.
        service: The mongo container's compose service name.
    """

    def __init__(self, docker: DockerCli, *, service: str = "mongo") -> None:
        self._docker = docker
        self._service = service

    def find(
        self,
        database: str,
        collection: str,
        query: Mapping[str, Any],
        *,
        projection: Optional[Mapping[str, Any]] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Documents of ``database.collection`` matching ``query``.

        Raises:
            DrillError: the output was not the JSON array the script prints.
            CommandError: the container is down or mongosh failed.
        """
        result = self._docker.exec(
            self._service,
            ["sh", "-c", MONGOSH_SHELL],
            input_text=find_script(database, collection, query, projection, limit),
            timeout_s=_FIND_TIMEOUT_S,
        )
        return parse_rows(result.stdout)


def parse_rows(stdout: str) -> list[dict[str, Any]]:
    """The documents on the last line of ``mongosh`` output.

    Raises:
        DrillError: that line is not a JSON array of objects.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise DrillError("mongosh printed nothing")
    try:
        rows = json.loads(lines[-1])
    except ValueError as exc:
        raise DrillError(f"unreadable mongosh output: {lines[-1][:200]!r}") from exc
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise DrillError("mongosh did not print a JSON array of documents")
    return rows

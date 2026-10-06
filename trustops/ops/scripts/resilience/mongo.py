"""A look at Mongo documents, for what no API route shows.

The drills prefer the API's effects. A few facts only H1 holds (the run's state,
an open gate) have no route, so this reads them with ``mongosh`` *inside the
mongo container*, as the root user the container already has: the script goes
in on standard input and the credentials are expanded by the container's own
shell, so neither appears on a command line, in a log or in this process.
``find`` reads. The only write is :meth:`MongoReader.seed_scratch_ledger`, which
creates one token ledger with a tiny cap for a scratch case so the budget
refusal can be provoked without a model call; it cannot change an existing
document.
"""

from __future__ import annotations

import json
import logging
import re
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

#: Where H1 keeps each case's token ledger, and the form of a case id.
LEDGER_DATABASE: Final = "fcrm_harness"
LEDGER_COLLECTION: Final = "case_budgets"
CASE_ID: Final = re.compile(r"FCRM-\d{4}-\d{4}")
#: The largest cap a drill may seed: far below a real case's (160,000), so a
#: seeded ledger can only ever refuse work, never allow more of it.
MAX_SEEDED_CAP: Final = 5_000


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


def seed_ledger_script(case_id: str, cap: int) -> str:
    """The ``mongosh`` script behind :meth:`MongoReader.seed_scratch_ledger`: an
    upsert whose only effect on an existing document is none.
    """
    key = json.dumps(case_id)
    return (
        f"const r = db.getSiblingDB({json.dumps(LEDGER_DATABASE)})"
        f".getCollection({json.dumps(LEDGER_COLLECTION)}).updateOne("
        f"{{_id: {key}}}, "
        f"{{$setOnInsert: {{case_id: {key}, run_id: 'resilience-drill', "
        f"cap: {int(cap)}, spent: 0, reserved: 0, jobs: {{}}, "
        "created_at: new Date(), updated_at: new Date()}}, "
        "{upsert: true});\n"
        "print(EJSON.stringify([{upserted: r.upsertedCount, "
        "matched: r.matchedCount}], {relaxed: true}));\n"
    )


class MongoReader:
    """``find`` over the stack's Mongo, through docker, and the one narrow
    write described at :meth:`seed_scratch_ledger`.

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

    def seed_scratch_ledger(self, case_id: str, cap: int) -> bool:
        """Give a scratch case a token ledger with a tiny ``cap``, so that the
        first job H1 tries to dispatch for it is refused by Guard 3. This is the
        one write the drills make, and it is as narrow as it can be: one
        document, in ``fcrm_harness.case_budgets``, only when the case has no
        ledger yet (``$setOnInsert``), for a case id of the scratch form, with a
        cap of at most :data:`MAX_SEEDED_CAP`. An existing ledger (a case with
        real spend) is never touched.

        Returns:
            ``True`` when the ledger was created, ``False`` when the case
            already had one.

        Raises:
            DrillError: ``case_id`` or ``cap`` is outside what this may write.
        """
        if not CASE_ID.fullmatch(case_id) or not 1 <= cap <= MAX_SEEDED_CAP:
            raise DrillError(
                f"refusing to seed a ledger for {case_id!r} with a cap of {cap}"
            )
        result = self._docker.exec(
            self._service,
            ["sh", "-c", MONGOSH_SHELL],
            input_text=seed_ledger_script(case_id, cap),
            timeout_s=_FIND_TIMEOUT_S,
        )
        outcome = parse_rows(result.stdout)
        return bool(outcome and outcome[0].get("upserted"))


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

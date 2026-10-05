#!/usr/bin/env python3
r"""Reset the demo data: ``make seed`` and ``make demo-reset``.

Two modes, both run **inside** the ``mongo`` and ``minio`` containers through
``docker compose exec``, with the credentials already in those containers'
environment. This script never reads a ``.env`` file and never sees a password:
the shell snippets it sends reference ``$MONGO_INITDB_ROOT_USERNAME`` and friends,
and the container's own shell expands them.

``--all``
    Drop the databases ``fcrm_case``, ``fcrm_decision``, ``fcrm_harness``,
    ``fcrm_policy``, ``fcrm_scoring`` and ``fcrm_identity``; empty
    ``fcrm_audit``'s ``audit_records`` and ``proposals`` (and its ``verify_runs``
    and ``idempotency_keys``, which describe records that no longer exist) as the
    root user, who alone may; then replay ``ops/mongo/init`` so the validators and
    indexes dropped with the databases are back. The caller then restarts ``api``
    and ``harness`` so ``SEED_ON_EMPTY`` reseeds every collection (``make seed``
    does).

``--demo-case``
    Delete every document that belongs to the demo case FCRM-2026-0151 across the
    databases (``case_id`` equals the case id; S5's votes, questions and answers
    are reached through the case's packs) and every object under
    ``uploads/FCRM-2026-0151/`` and ``canonical/FCRM-2026-0151/`` in MinIO, so the
    demo can run again from the PO's first upload. The other 79 cases are not
    touched.

``--stage-fixtures``
    Copy the three demo PDFs from ``evals/fixtures/documents`` to
    ``uploads/_fixtures/`` in MinIO, where the PO's "use the demo files" upload
    reads them.

Refuses to run when ``ENV`` is ``prod`` or ``production``. Usage::

    python ops/scripts/seed_reset.py --all [--compose "docker compose -f ..."]
    python ops/scripts/seed_reset.py --demo-case
    python ops/scripts/seed_reset.py --stage-fixtures
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Optional

logger = logging.getLogger("seed_reset")

ROOT: Final = Path(__file__).resolve().parents[2]
DEMO_CASE_ID: Final = "FCRM-2026-0151"
CASE_ID_RE: Final = re.compile(r"^FCRM-\d{4}-\d{4}$")

#: Dropped by ``--all``: every database the services re-create and reseed.
APP_DATABASES: Final = (
    "fcrm_case",
    "fcrm_decision",
    "fcrm_harness",
    "fcrm_policy",
    "fcrm_scoring",
    "fcrm_identity",
)
AUDIT_DATABASE: Final = "fcrm_audit"
DECISION_DATABASE: Final = "fcrm_decision"
#: Emptied (never dropped: they carry the validator and the unique indexes the
#: insert-only writer cannot recreate) by ``--all``.
AUDIT_COLLECTIONS: Final = (
    "audit_records",
    "proposals",
    "verify_runs",
    "idempotency_keys",
)
#: Where the demo case's documents are looked for by ``--demo-case``.
DEMO_DATABASES: Final = (*APP_DATABASES, AUDIT_DATABASE)
#: Buckets (see ``ops/minio/init.sh``) that hold per-case prefixes.
CASE_BUCKETS: Final = ("uploads", "canonical")
FIXTURE_BUCKET: Final = "uploads"
FIXTURE_PREFIX: Final = "_fixtures"
FIXTURES_DIR: Final = ROOT / "evals" / "fixtures" / "documents"
FIXTURE_FILES: Final = (
    "business_case_remittance_MX.pdf",
    "payout_partner_contract.pdf",
    "vendor_DD_scope_letter_2026.pdf",
)
PROD_ENVS: Final = frozenset({"prod", "production"})
DEFAULT_COMPOSE: Final = shlex.join(
    ["docker", "compose", "-f", str(ROOT / "docker-compose.yaml")]
)
COMMAND_TIMEOUT_S: Final = 300

#: Runs a script (read from stdin) as the mongo root user. The credentials are
#: expanded by the container's shell, never by this process.
MONGOSH_SHELL: Final = (
    'mongosh --quiet --norc "mongodb://localhost:27017/admin?directConnection=true" '
    '--username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" '
    '--authenticationDatabase admin --eval "$(cat)"'
)
MONGO_INIT_SHELL: Final = "bash /opt/fcrm/init/run.sh"
#: ``mc`` alias for the container's own server, built from its own environment.
MC_PRELUDE: Final = (
    "set -euo pipefail; export MC_CONFIG_DIR=/tmp/.mc; "
    'export MC_HOST_fcrm="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}'
    '@localhost:9000"; '
)


class ResetError(RuntimeError):
    """A reset step could not run or failed."""


class RefusedError(ResetError):
    """The reset is not allowed in this environment."""


def refuse_in_prod(env: Optional[str]) -> None:
    """Raise :class:`RefusedError` when ``env`` names a production environment.

    Raises:
        RefusedError: ``env`` is ``prod`` or ``production`` (any case).
    """
    if env is not None and env.strip().lower() in PROD_ENVS:
        raise RefusedError(f"refusing to reset data when ENV={env.strip()}")


def validated_case_id(case_id: str) -> str:
    """``case_id`` if it is an ``FCRM-YYYY-NNNN`` id (it is embedded in a script).

    Raises:
        ResetError: ``case_id`` is not in that form.
    """
    if not CASE_ID_RE.fullmatch(case_id):
        raise ResetError(f"not a case id: {case_id!r}")
    return case_id


def _js_list(names: Sequence[str]) -> str:
    return "[" + ", ".join(f'"{name}"' for name in names) + "]"


def mongo_all_script(
    app_databases: Sequence[str] = APP_DATABASES,
    audit_database: str = AUDIT_DATABASE,
) -> str:
    """The mongosh script for ``--all``: drop the app databases, empty the audit
    collections that exist. The names are parameters only so a test can aim the
    script at throwaway databases; the defaults are the real ones.
    """
    return f"""\
const dropped = [];
for (const name of {_js_list(app_databases)}) {{
  const result = db.getSiblingDB(name).dropDatabase();
  dropped.push(name + ":" + result.ok);
}}
print("dropped " + dropped.join(" "));
const audit = db.getSiblingDB("{audit_database}");
const present = audit.getCollectionNames();
for (const name of {_js_list(AUDIT_COLLECTIONS)}) {{
  if (present.includes(name)) {{
    const result = audit.getCollection(name).deleteMany({{}});
    print("emptied {audit_database}." + name + ": " + result.deletedCount);
  }}
}}
"""


def mongo_demo_script(
    case_id: str,
    databases: Sequence[str] = DEMO_DATABASES,
    decision_database: str = DECISION_DATABASE,
) -> str:
    """The mongosh script for ``--demo-case``: delete the case's documents.

    S5's votes, questions and answers carry no ``case_id`` (they name a pack), so
    the case's pack ids are read first and those rows are removed through them;
    its outcomes name the case under ``outcome.case_id``. Every other collection
    is swept for ``case_id``. The database names are parameters only so a test can
    aim the script at throwaway databases.
    """
    case = validated_case_id(case_id)
    return f"""\
const caseId = "{case}";
let total = 0;
const decision = db.getSiblingDB("{decision_database}");
const packIds = decision.packs.distinct("pack_id", {{ case_id: caseId }});
const byPack = [
  ["votes", {{ pack_id: {{ $in: packIds }} }}],
  ["questions", {{ "question.pack_id": {{ $in: packIds }} }}],
  ["answers", {{ "answer.pack_id": {{ $in: packIds }} }}],
  ["outcomes", {{ "outcome.case_id": caseId }}],
];
for (const [name, filter] of byPack) {{
  const result = decision.getCollection(name).deleteMany(filter);
  total += result.deletedCount;
  print("deleted {decision_database}." + name + ": " + result.deletedCount);
}}
for (const dbName of {_js_list(databases)}) {{
  const target = db.getSiblingDB(dbName);
  for (const info of target.getCollectionInfos({{ type: "collection" }})) {{
    if (info.name.startsWith("system.")) continue;
    const result = target.getCollection(info.name).deleteMany({{ case_id: caseId }});
    if (result.deletedCount > 0) {{
      total += result.deletedCount;
      print("deleted " + dbName + "." + info.name + ": " + result.deletedCount);
    }}
  }}
}}
print("demo case " + caseId + ": " + total + " document(s) deleted");
"""


def minio_demo_shell(case_id: str) -> str:
    """The shell snippet for ``--demo-case``: remove the case's prefixes."""
    case = validated_case_id(case_id)
    buckets = " ".join(CASE_BUCKETS)
    return (
        MC_PRELUDE
        + f'for bucket in {buckets}; do target="fcrm/${{bucket}}/{case}/"; '
        + 'if [ -n "$(mc --config-dir /tmp/.mc ls "${target}" 2>/dev/null)" ]; then '
        + 'mc --config-dir /tmp/.mc rm --recursive --force "${target}"; '
        + 'else echo "nothing under ${target}"; fi; done'
    )


def minio_stage_shell(name: str) -> str:
    """The shell snippet that stores stdin as ``uploads/_fixtures/<name>``."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ResetError(f"unsafe fixture name: {name!r}")
    target = f"fcrm/{FIXTURE_BUCKET}/{FIXTURE_PREFIX}/{name}"
    return MC_PRELUDE + f'mc --config-dir /tmp/.mc pipe "{target}"'


def exec_command(compose: Sequence[str], service: str, shell: str) -> list[str]:
    """``docker compose exec -T <service> bash -c <shell>`` as an argv list."""
    return [*compose, "exec", "-T", service, "bash", "-c", shell]


def run_step(
    command: Sequence[str],
    description: str,
    *,
    stdin: Optional[bytes] = None,
    cwd: Path = ROOT,
) -> str:
    """Run one container command; return its output.

    Raises:
        ResetError: Docker is missing, the command times out or exits non-zero
            (the message carries the tail of its output, never its input).
    """
    logger.info("%s", description)
    try:
        done = subprocess.run(  # noqa: S603 - argv built here, no shell on the host
            list(command),
            input=stdin,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_S,
            check=False,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise ResetError(f"cannot run {command[0]!r}: is Docker installed?") from exc
    except subprocess.TimeoutExpired as exc:
        raise ResetError(
            f"{description}: timed out after {COMMAND_TIMEOUT_S}s"
        ) from exc
    output = (done.stdout + done.stderr).decode("utf-8", errors="replace").strip()
    for line in output.splitlines()[-20:]:
        logger.info("  %s", line)
    if done.returncode != 0:
        tail = " | ".join(output.splitlines()[-3:])
        raise ResetError(f"{description}: exit {done.returncode}: {tail}")
    return output


def reset_all(compose: Sequence[str]) -> None:
    """``--all``: drop and empty the data, then replay the mongo init."""
    run_step(
        exec_command(compose, "mongo", MONGOSH_SHELL),
        "dropping the app databases and emptying the audit collections",
        stdin=mongo_all_script().encode("utf-8"),
    )
    run_step(
        exec_command(compose, "mongo", MONGO_INIT_SHELL),
        "replaying ops/mongo/init (validators, users, indexes)",
    )
    logger.info("done: restart api and harness so SEED_ON_EMPTY reseeds")


def reset_demo_case(compose: Sequence[str], case_id: str = DEMO_CASE_ID) -> None:
    """``--demo-case``: delete the case's documents and MinIO objects."""
    run_step(
        exec_command(compose, "mongo", MONGOSH_SHELL),
        f"deleting the documents of {case_id}",
        stdin=mongo_demo_script(case_id).encode("utf-8"),
    )
    run_step(
        exec_command(compose, "minio", minio_demo_shell(case_id)),
        f"deleting the MinIO objects of {case_id}",
    )


def stage_fixtures(compose: Sequence[str], directory: Path = FIXTURES_DIR) -> list[str]:
    """``--stage-fixtures``: copy the demo PDFs to ``uploads/_fixtures/``.

    Returns:
        The names staged.

    Raises:
        ResetError: A fixture file is missing or a copy fails.
    """
    missing = [name for name in FIXTURE_FILES if not (directory / name).is_file()]
    if missing:
        raise ResetError(f"missing fixture(s) in {directory}: {missing}")
    for name in FIXTURE_FILES:
        run_step(
            exec_command(compose, "minio", minio_stage_shell(name)),
            f"staging {name} to {FIXTURE_BUCKET}/{FIXTURE_PREFIX}/",
            stdin=(directory / name).read_bytes(),
        )
    return list(FIXTURE_FILES)


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset the demo data.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="drop and reseed everything")
    mode.add_argument(
        "--demo-case", action="store_true", help=f"reset only {DEMO_CASE_ID}"
    )
    mode.add_argument(
        "--stage-fixtures", action="store_true", help="copy the demo PDFs to MinIO"
    )
    parser.add_argument(
        "--compose",
        default=DEFAULT_COMPOSE,
        help="the compose command (default: docker compose -f docker-compose.yaml)",
    )
    parser.add_argument(
        "--env",
        default=os.environ.get("ENV"),
        help="environment name; prod and production are refused (default: $ENV)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the chosen reset; return a process exit code (0 = done)."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    compose = shlex.split(args.compose)
    try:
        refuse_in_prod(args.env)
        if args.all:
            reset_all(compose)
        elif args.demo_case:
            reset_demo_case(compose)
        else:
            stage_fixtures(compose)
    except ResetError as exc:
        logger.error("seed_reset: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

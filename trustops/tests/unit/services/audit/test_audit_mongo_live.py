"""S4's Mongo-backed store and lifespan against real MongoDB 8, in two
parts.

**Part 1** (``Test*Sandbox*``) runs against whatever replica set
``FCRM_TEST_MONGO_URI`` points at -- exactly like ``tests/unit/services/
scoring/test_mongo_live.py`` -- and proves seeding, the ``chain_verify``
self-test, transactional appends and real pagination all work against a
real server. The sandbox Mongo this workspace happened to have reachable
while this lane was built (``mongodb://127.0.0.1:27217/?replicaSet=rs0``)
was started with no ``--auth`` at all (confirmed via ``getCmdLineOpts``:
no ``security.authorization``), so it cannot enforce the
``fcrmAuditInsertOnly`` role -- every connection is implicitly a
superuser regardless of which "user" the URI names. These tests
therefore disable ``INSERT_ONLY_CHECK_ON_START`` and say so.

**Part 2** (``Test*InsertOnlyRole*``) is what actually proves the
insert-only guarantee: it spins up its *own*, throwaway, ``--auth``
``--keyFile`` single-node replica set (real ``mongod``/``mongosh``
binaries, found at ``/opt/homebrew/bin``), replays the *real*
``ops/mongo/init/{01,02,03,04}-*.js`` scripts against it with
``mongosh --file`` (not a Python reimplementation of their logic), and
then:

1. connects directly as ``fcrm_audit_writer`` and shows MongoDB itself
   accepts ``insert``/``find`` but rejects ``update``/``delete`` with
   ``OperationFailure`` code 13 (Unauthorized);
2. boots the real FastAPI app against that same writer credential and
   shows ``/readyz``'s ``insert_only`` check reports ``ok: true``;
3. boots the real FastAPI app against the *root* credential instead (an
   over-privileged credential a misconfigured deployment might hand S4)
   and shows the service refuses to finish starting at all, rather than
   silently serving with a weaker guarantee than it advertises.

Skipped entirely if ``mongod``/``mongosh`` are not on this machine.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import pymongo
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_audit.analytics import MongoAnalyticsStore
from fcrm_audit.chain import GENESIS_HASH
from fcrm_audit.examiner import AnswerRow, MongoExaminerStore, QuestionRow
from fcrm_audit.lifespan import InsertOnlyRoleViolatedError, lifespan
from fcrm_audit.quality import MongoEvalsReader, agent_quality
from fcrm_audit.routes import router
from fcrm_audit.store import MongoAuditStore
from fcrm_common.errors import ConflictError, install_error_handlers
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_common.mongo import Repository
from fcrm_contracts.api.audit import (
    AuditRecord,
    ChainHead,
    ProposalAppend,
    RecordAppend,
)
from fcrm_contracts.common import Page
from fcrm_seed.loader import closed_cases, demo_case
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure

#: ``tests/conftest.py``'s ``live_client``: real lifespan, real HS256
#: service token, allow-all OPA stub.
LiveClient = Callable[..., AbstractContextManager[TestClient]]

#: The demo case has no seeded records: its 67 are the live demo's output.
DEMO_CASE_ID = demo_case()["id"]
#: A closed case whose nine seeded records the startup self-test verifies.
SEEDED_CASE_ID = demo_case()["precedent"]["id"]
SEEDED_RECORDS_PER_CASE = 9
SEEDED_CLOSED_CASES = len(closed_cases())
REPO_ROOT = Path(__file__).resolve().parents[4]
INIT_DIR = REPO_ROOT / "ops" / "mongo" / "init"


# ── Part 1: the sandbox replica set (no auth enforcement available) ────────

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
sandbox_pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]


class TestSandboxLifespan:
    """Grouped in a class (rather than module-level ``pytestmark``) so
    this skip condition never leaks onto Part 2's tests below, which have
    their own, different skip condition.
    """

    pytestmark = sandbox_pytestmark

    @pytest.fixture
    async def clean_db(self) -> None:
        """Drop fcrm_audit before and after, so this test never depends on
        (or leaves behind) another run's data.
        """
        client: AsyncMongoClient = AsyncMongoClient(URI)
        await client.drop_database("fcrm_audit")
        yield
        await client.drop_database("fcrm_audit")
        await client.close()

    @pytest.fixture
    def app(self, clean_db: None, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
        monkeypatch.setenv("MONGODB_URI", URI)
        monkeypatch.setenv("MONGODB_DB", "fcrm_audit")
        monkeypatch.setenv("SERVICE_TOKEN_SECRET", "x" * 32)
        monkeypatch.setenv("OPA_URL", "http://localhost:8181")
        monkeypatch.setenv(
            "IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate"
        )
        monkeypatch.setenv("ENV", "test")
        monkeypatch.setenv("SEED_ON_EMPTY", "true")
        monkeypatch.setenv("CHAIN_VERIFY_ON_START", "true")
        # This sandbox server has no --auth at all, so it cannot
        # demonstrate the insert-only guarantee (see the module
        # docstring); Part 2 proves that against a dedicated,
        # auth-enabled instance instead.
        monkeypatch.setenv("INSERT_ONLY_CHECK_ON_START", "false")

        application = FastAPI(lifespan=lifespan)
        install_error_handlers(application)
        application.state.probe = ReadinessProbe()
        application.include_router(router)
        application.include_router(build_health_router(application.state.probe))
        return application

    def test_the_full_lifespan_seeds_the_closed_case_chains_and_passes_its_self_test(
        self, app: FastAPI, live_client: LiveClient
    ) -> None:
        with live_client(app) as client:
            ready = client.get("/readyz")
            assert ready.status_code == 200
            body = ready.json()
            assert body["status"] == "ok"
            assert body["checks"]["chain_verify"]["ok"] is True
            assert "insert_only" not in body["checks"]

            verify = client.get(f"/verify/{SEEDED_CASE_ID}")
            assert verify.status_code == 200
            assert verify.json() == {
                "case_id": SEEDED_CASE_ID,
                "record_count": SEEDED_RECORDS_PER_CASE,
                "ok": True,
                "first_break_seq": None,
            }
            # The demo case is produced live, never seeded.
            demo = client.get(f"/verify/{DEMO_CASE_ID}")
            assert demo.json()["record_count"] == 0

    async def test_one_bulk_insert_seeds_nine_records_for_each_closed_case(
        self, app: FastAPI, live_client: LiveClient
    ) -> None:
        with live_client(app):
            pass
        client: AsyncMongoClient = AsyncMongoClient(URI)
        try:
            records = client["fcrm_audit"]["audit_records"]
            total = await records.count_documents({})
            assert total == SEEDED_CLOSED_CASES * SEEDED_RECORDS_PER_CASE
            assert await records.count_documents({"case_id": DEMO_CASE_ID}) == 0
            assert len(await records.distinct("case_id")) == SEEDED_CLOSED_CASES
        finally:
            await client.close()

    def test_restarting_the_app_does_not_duplicate_the_seeded_chain(
        self, app: FastAPI, live_client: LiveClient
    ) -> None:
        with TestClient(app):
            pass
        with live_client(app) as client:
            verify = client.get(f"/verify/{SEEDED_CASE_ID}")
            assert verify.json()["record_count"] == SEEDED_RECORDS_PER_CASE

    def test_post_records_appends_through_a_real_transaction_and_chains(
        self, app: FastAPI, live_client: LiveClient
    ) -> None:
        with live_client(app) as client:
            first = client.post(
                "/records",
                json={
                    "case_id": "FCRM-2026-LIVE",
                    "type": "case.created",
                    "actor": "system",
                    "summary": "created",
                },
            )
            assert first.status_code == 201
            first_body = first.json()
            assert first_body["seq"] == 1
            assert first_body["prev_hash"] == "0" * 64

            second = client.post(
                "/records",
                json={
                    "case_id": "FCRM-2026-LIVE",
                    "type": "human.override",
                    "actor": "u_rahul",
                    "summary": "overrode CTL-T01",
                },
            )
            assert second.status_code == 201
            second_body = second.json()
            assert second_body["seq"] == 2
            assert second_body["prev_hash"] == first_body["hash"]

            verify = client.get("/verify/FCRM-2026-LIVE")
            assert verify.json() == {
                "case_id": "FCRM-2026-LIVE",
                "record_count": 2,
                "ok": True,
                "first_break_seq": None,
            }

    def test_get_records_paginates_a_real_seeded_closed_case_trail(
        self, app: FastAPI, live_client: LiveClient
    ) -> None:
        with live_client(app) as client:
            page = client.get(f"/records/{SEEDED_CASE_ID}", params={"limit": 4})
            assert page.status_code == 200
            body = page.json()
            assert len(body["items"]) == 4
            assert [r["seq"] for r in body["items"]] == [1, 2, 3, 4]
            assert body["next_cursor"] is not None

            rest = client.get(
                f"/records/{SEEDED_CASE_ID}",
                params={"limit": 100, "cursor": body["next_cursor"]},
            )
            rest_body = rest.json()
            assert len(rest_body["items"]) == SEEDED_RECORDS_PER_CASE - 4
            assert rest_body["items"][-1]["seq"] == SEEDED_RECORDS_PER_CASE
            assert rest_body["items"][-1]["type"] == "chain.verified"
            assert rest_body["next_cursor"] is None


# ── Part 2: a dedicated, auth-enabled instance proving the insert-only role ─


def _find_binary(*candidates: str) -> Optional[str]:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


MONGOD = _find_binary("mongod", "/opt/homebrew/bin/mongod")
MONGOSH = _find_binary("mongosh", "/opt/homebrew/bin/mongosh")

auth_pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (MONGOD and MONGOSH and INIT_DIR.is_dir()),
        reason="mongod/mongosh not found, or ops/mongo/init is missing; "
        "cannot prove the insert-only role live",
    ),
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _wait_until(
    predicate: Any, *, timeout_s: float = 20.0, interval_s: float = 0.3
) -> None:
    deadline = time.monotonic() + timeout_s
    last_exc: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001 - probing, retried until timeout
            last_exc = exc
        time.sleep(interval_s)
    raise TimeoutError(f"condition not met within {timeout_s}s: {last_exc}")


def _run_mongosh(
    uri: str,
    script: Path,
    env: dict[str, str],
    *,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> None:
    """Run one ``mongosh --file`` script. ``username``/``password`` are
    omitted for ``01-replset.js``: at that point no user exists yet, so
    the connection relies on the localhost exception, the same as the
    real ``mongo`` container's own first-boot sequence.
    """
    auth_args = (
        [
            "--username",
            username,
            "--password",
            password,
            "--authenticationDatabase",
            "admin",
        ]
        if username is not None
        else []
    )
    result = subprocess.run(
        [str(MONGOSH), uri, "--quiet", *auth_args, "--file", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, **env},
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"mongosh --file {script.name} failed "
            f"(exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
        )


@pytest.fixture(scope="module")
def auth_enabled_mongo(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[dict[str, str]]:
    """A throwaway single-node ``--auth``/``--keyFile`` replica set, with
    the real ``02-users.js``/``03-schemas.js``/``04-indexes.js`` already
    applied by the root user, exactly as ``ops/mongo/init/run.sh`` would
    against the real ``mongo`` compose service.
    """
    base = tmp_path_factory.mktemp("fcrm-audit-auth-mongo")
    data_dir = base / "data"
    data_dir.mkdir()
    keyfile = base / "keyfile"
    keyfile.write_bytes(base64.b64encode(secrets.token_bytes(567)))
    keyfile.chmod(0o400)
    port = _free_port()
    log_path = base / "mongod.log"

    assert MONGOD is not None
    proc = subprocess.run(
        [
            MONGOD,
            "--dbpath",
            str(data_dir),
            "--port",
            str(port),
            "--replSet",
            "rs0",
            "--bind_ip",
            "127.0.0.1",
            "--auth",
            "--keyFile",
            str(keyfile),
            "--fork",
            "--logpath",
            str(log_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        pytest.fail(f"mongod failed to start:\n{proc.stdout}\n{proc.stderr}")

    root_user, root_pass = "root", secrets.token_urlsafe(24)
    writer_pass = secrets.token_urlsafe(24)
    app_pass = secrets.token_urlsafe(24)
    evals_pass = secrets.token_urlsafe(24)
    admin_uri = f"mongodb://127.0.0.1:{port}/admin?directConnection=true"

    try:
        _wait_until(
            lambda: pymongo.MongoClient(
                admin_uri, serverSelectionTimeoutMS=2000
            ).admin.command("ping")
        )
        script_env = {
            "MONGO_RS_MEMBER_HOST": f"127.0.0.1:{port}",
            "MONGO_INITDB_ROOT_USERNAME": root_user,
            "MONGO_INITDB_ROOT_PASSWORD": root_pass,
            "MONGO_AUDIT_WRITER_PASSWORD": writer_pass,
            "MONGO_APP_PASSWORD": app_pass,
            "MONGO_EVALS_READER_PASSWORD": evals_pass,
        }

        # 01-replset.js first, unauthenticated: a --replSet member with no
        # initiated set refuses all writes (even under the localhost
        # exception) until rs.initiate() runs, so no user can be created
        # before this -- the server is not yet a writable primary.
        _run_mongosh(admin_uri, INIT_DIR / "01-replset.js", script_env)

        # Localhost exception: no user exists yet, so this unauthenticated
        # connection (originating on localhost) may create the first one --
        # the same bootstrap step the official mongo image's entrypoint
        # performs from MONGO_INITDB_ROOT_*, now that there is a primary.
        bootstrap = pymongo.MongoClient(admin_uri, serverSelectionTimeoutMS=5000)
        bootstrap.admin.command(
            "createUser",
            root_user,
            pwd=root_pass,
            roles=[{"role": "root", "db": "admin"}],
        )
        bootstrap.close()

        for name in ("02-users.js", "03-schemas.js", "04-indexes.js"):
            _run_mongosh(
                admin_uri,
                INIT_DIR / name,
                script_env,
                username=root_user,
                password=root_pass,
            )

        # Both users were created in admin (02-users.js's upsertUser goes
        # through db.getSiblingDB("admin")), so authSource=admin is
        # required -- pymongo otherwise defaults the auth source to the
        # URI's path segment (fcrm_audit / fcrm_audit_control), which has
        # no such user and fails with AuthenticationFailed (code 18).
        writer_uri = (
            f"mongodb://fcrm_audit_writer:{writer_pass}@127.0.0.1:{port}"
            "/fcrm_audit?replicaSet=rs0&authSource=admin"
        )
        evals_uri = (
            f"mongodb://fcrm_evals_reader:{evals_pass}@127.0.0.1:{port}"
            "/fcrm_evals?replicaSet=rs0&authSource=admin"
        )
        root_rw_uri = (
            f"mongodb://{root_user}:{root_pass}@127.0.0.1:{port}"
            "/fcrm_audit_control?replicaSet=rs0&authSource=admin"
        )
        root_admin_uri = (
            f"mongodb://{root_user}:{root_pass}@127.0.0.1:{port}"
            "/admin?replicaSet=rs0&authSource=admin"
        )
        yield {
            "port": str(port),
            "writer_uri": writer_uri,
            "evals_uri": evals_uri,
            "root_rw_uri": root_rw_uri,
            "admin_uri": admin_uri,
            "root_admin_uri": root_admin_uri,
            "root_user": root_user,
            "root_pass": root_pass,
        }
    finally:
        with contextlib.suppress(Exception):
            shutdown = pymongo.MongoClient(
                admin_uri,
                username=root_user,
                password=root_pass,
                serverSelectionTimeoutMS=5000,
            )
            with contextlib.suppress(OperationFailure):
                shutdown.admin.command("shutdown", force=True)
            shutdown.close()


class TestInsertOnlyRoleIsEnforcedByTheDatabase:
    """``pytestmark`` here (a class attribute) scopes ``auth_pytestmark``'s
    integration marker and skip condition to just this class, since Part
    1's tests above have their own, different skip condition.
    """

    pytestmark = auth_pytestmark

    @pytest.fixture(autouse=True)
    def _clean_databases(self, auth_enabled_mongo: dict[str, str]) -> None:
        """``auth_enabled_mongo`` is module-scoped (spinning up a whole
        mongod per test would be far too slow), so the three tests in
        this class share one physical server; this clears leftover
        documents before each test so none of them observes another's
        (e.g. the proof test's ``FCRM-2026-PROOF`` record would otherwise
        make the seeding test's ``audit_records`` collection look
        non-empty and skip seeding).

        Deliberately *not* ``drop_database("fcrm_audit")``: a custom
        role created with ``db.getSiblingDB("fcrm_audit").createRole(...)``
        (02-users.js's ``fcrmAuditInsertOnly``) is itself stored in that
        database (``fcrm_audit.system.roles``), so dropping the database
        would delete the very role this whole test class exists to
        exercise, leaving ``fcrm_audit_writer`` a dangling reference to a
        role that no longer exists. ``delete_many({})`` clears documents
        without touching the collection's validator, indexes or the
        database's roles.
        """
        root = pymongo.MongoClient(
            auth_enabled_mongo["root_admin_uri"], serverSelectionTimeoutMS=5000
        )
        root["fcrm_audit"]["audit_records"].delete_many({})
        root.drop_database("fcrm_audit_control")
        root.close()

    def test_fcrm_audit_writer_can_insert_and_find_but_not_update_or_delete(
        self, auth_enabled_mongo: dict[str, str]
    ) -> None:
        """The exact proof the task asked for: what was tried, and why it
        was rejected. ``find``/``insert`` are granted and succeed;
        ``update``/``delete`` are not granted and MongoDB itself refuses
        them with ``OperationFailure`` code 13 (Unauthorized) -- before
        even evaluating whether the filter matches anything.
        """
        writer = pymongo.MongoClient(
            auth_enabled_mongo["writer_uri"], serverSelectionTimeoutMS=5000
        )
        try:
            collection = writer.fcrm_audit.audit_records
            doc = {
                "case_id": "FCRM-2026-PROOF",
                "seq": 1,
                "type": "case.created",
                "actor": "system",
                "summary": "proof record",
                "data": {},
                "prev_hash": "0" * 64,
                "hash": "a" * 64,
                "created_at": datetime.now(UTC),
            }

            # Granted: insert.
            collection.insert_one(doc)
            # Granted: find.
            assert collection.find_one({"case_id": "FCRM-2026-PROOF"}) is not None

            # Not granted: update. Rejected by MongoDB itself, not by any
            # application-layer check.
            with pytest.raises(OperationFailure) as update_exc:
                collection.update_one(
                    {"case_id": "FCRM-2026-PROOF"}, {"$set": {"summary": "tampered"}}
                )
            assert update_exc.value.code == 13, update_exc.value.details

            # Not granted: delete.
            with pytest.raises(OperationFailure) as delete_exc:
                collection.delete_one({"case_id": "FCRM-2026-PROOF"})
            assert delete_exc.value.code == 13, delete_exc.value.details

            # The {case_id, seq} unique index from 04-indexes.js is also
            # live: a second record at the same (case_id, seq) is refused
            # at the database layer too, independent of insert-only-ness.
            with pytest.raises(DuplicateKeyError):
                collection.insert_one(dict(doc))
        finally:
            writer.close()

    def test_the_real_service_reports_ready_against_the_real_writer_credential(
        self,
        auth_enabled_mongo: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
        live_client: LiveClient,
    ) -> None:
        monkeypatch.setenv("MONGODB_URI", auth_enabled_mongo["writer_uri"])
        monkeypatch.setenv("MONGODB_DB", "fcrm_audit")
        monkeypatch.setenv("SERVICE_TOKEN_SECRET", "x" * 32)
        monkeypatch.setenv("OPA_URL", "http://localhost:8181")
        monkeypatch.setenv(
            "IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate"
        )
        monkeypatch.setenv("ENV", "test")
        monkeypatch.setenv("SEED_ON_EMPTY", "true")
        monkeypatch.setenv("CHAIN_VERIFY_ON_START", "true")
        monkeypatch.setenv("INSERT_ONLY_CHECK_ON_START", "true")

        application = FastAPI(lifespan=lifespan)
        install_error_handlers(application)
        application.state.probe = ReadinessProbe()
        application.include_router(router)
        application.include_router(build_health_router(application.state.probe))

        with live_client(application) as client:
            ready = client.get("/readyz")
            assert ready.status_code == 200
            body = ready.json()
            assert body["status"] == "ok"
            assert body["checks"]["insert_only"]["ok"] is True
            assert body["checks"]["chain_verify"]["ok"] is True

            verify = client.get(f"/verify/{SEEDED_CASE_ID}")
            assert verify.json()["record_count"] == SEEDED_RECORDS_PER_CASE

    def test_the_real_service_refuses_to_start_with_an_over_privileged_credential(
        self, auth_enabled_mongo: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flip the credential to the full-access ``root`` user (what a
        misconfigured deployment might hand S4 by mistake) against a fresh,
        unvalidated database: the insert-only self-test must now fail --
        proving the check is not a tautology that passes regardless of
        the credential -- and the whole app must refuse to finish
        starting, the same fail-closed shape as S2's recompute self-test.
        """
        monkeypatch.setenv("MONGODB_URI", auth_enabled_mongo["root_rw_uri"])
        monkeypatch.setenv("MONGODB_DB", "fcrm_audit_control")
        monkeypatch.setenv("SERVICE_TOKEN_SECRET", "x" * 32)
        monkeypatch.setenv("OPA_URL", "http://localhost:8181")
        monkeypatch.setenv(
            "IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate"
        )
        monkeypatch.setenv("ENV", "test")
        monkeypatch.setenv("SEED_ON_EMPTY", "false")
        monkeypatch.setenv("CHAIN_VERIFY_ON_START", "false")
        monkeypatch.setenv("INSERT_ONLY_CHECK_ON_START", "true")

        application = FastAPI(lifespan=lifespan)
        install_error_handlers(application)
        application.state.probe = ReadinessProbe()
        application.include_router(router)
        application.include_router(build_health_router(application.state.probe))

        with pytest.raises(InsertOnlyRoleViolatedError), TestClient(application):
            pass


# ── Part 3: idempotent appends, proposals and indexes against a real server ─
#
# The same store behaviour is proven twice: on the sandbox replica set (no
# auth, indexes created here from ``REQUIRED_INDEXES``) and, in Part 2's
# auth-enabled server, under the real insert-only role with the indexes the
# real ``04-indexes.js`` made. A parity test ties ``REQUIRED_INDEXES`` to that
# script, so the sandbox run cannot drift from production.

IDEM_CASE = "FCRM-2026-IDEM"
#: ``(collection, keys, options)`` the idempotency and proposals paths rely on.
REQUIRED_INDEXES: list[tuple[str, list[tuple[str, int]], dict[str, Any]]] = [
    ("audit_records", [("case_id", 1), ("seq", 1)], {"unique": True}),
    ("proposals", [("case_id", 1), ("seq", 1)], {"unique": True}),
    (
        "proposals",
        [("case_id", 1), ("run_id", 1), ("agent", 1), ("kind", 1)],
        {},
    ),
    ("proposals", [("case_id", 1), ("supersedes", 1)], {"sparse": True}),
    ("idempotency_keys", [("at", 1)], {"expireAfterSeconds": 86400}),
]


def _record(case_id: str = IDEM_CASE, **overrides: Any) -> RecordAppend:
    values: dict[str, Any] = {
        "case_id": case_id,
        "type": "case.created",
        "actor": "system",
        "summary": "created",
        **overrides,
    }
    return RecordAppend(**values)


def _proposal(case_id: str = IDEM_CASE, **overrides: Any) -> ProposalAppend:
    values: dict[str, Any] = {
        "case_id": case_id,
        "run_id": "run_1",
        "agent": "research",
        "kind": "research",
        "payload": {"clauses": ["TP71-2.3"]},
        "model": "fake-model",
        "prompt_v": "research.system@1",
        "tier": "strong",
        **overrides,
    }
    return ProposalAppend(**values)


def _store_over(client: AsyncMongoClient) -> MongoAuditStore:
    """A store on its own client: two of them stand in for two S4 processes
    (separate connection pools, separate in-process case locks)."""
    db = client["fcrm_audit"]
    return MongoAuditStore(
        Repository(db["audit_records"], AuditRecord),
        client,
        proposals=db["proposals"],
        idempotency_keys=db["idempotency_keys"],
    )


class _StoreBehaviour:
    """What the Mongo store must do on any real replica set that has the
    audit indexes. Subclasses provide ``make_store`` (a fresh store per call)."""

    async def test_a_keyed_append_is_replayed_and_writes_nothing(
        self, make_store: Callable[[], MongoAuditStore]
    ) -> None:
        store = make_store()
        first = await store.append(_record(), idempotency_key="idem-live-1")
        again = await store.append(_record(), idempotency_key="idem-live-1")
        assert (first.replayed, again.replayed) == (False, True)
        assert again.value == first.value
        page = await store.records_for_case(IDEM_CASE)
        assert [r.seq for r in page.items] == [1]
        assert (await store.verify(IDEM_CASE)).ok is True
        assert await store.head(IDEM_CASE) == ChainHead(seq=1, hash=first.value.hash)

    async def test_a_key_reused_with_another_body_is_a_conflict(
        self, make_store: Callable[[], MongoAuditStore]
    ) -> None:
        store = make_store()
        await store.append(_record(), idempotency_key="idem-live-2")
        with pytest.raises(ConflictError):
            await store.append(
                _record(summary="different"), idempotency_key="idem-live-2"
            )
        with pytest.raises(ConflictError):
            await store.append_proposal(_proposal(), idempotency_key="idem-live-2")
        assert (await store.head(IDEM_CASE)).seq == 1

    async def test_simultaneous_requests_with_one_key_write_one_record(
        self, make_store: Callable[[], MongoAuditStore]
    ) -> None:
        """Six overlapping requests over two stores (two S4 processes): one
        transaction commits the key and its record, the rest replay it."""
        stores = [make_store(), make_store()]
        results = await asyncio.gather(
            *(
                stores[n % 2].append(_record(), idempotency_key="idem-live-3")
                for n in range(6)
            )
        )
        assert sorted(r.replayed for r in results) == [False] + [True] * 5
        assert len({r.value.hash for r in results}) == 1
        page = await stores[0].records_for_case(IDEM_CASE)
        assert [r.seq for r in page.items] == [1]

    async def test_simultaneous_appends_without_keys_get_distinct_seqs(
        self, make_store: Callable[[], MongoAuditStore]
    ) -> None:
        """Eight overlapping appends to one case over two stores: every one is
        stored once, ``seq`` is 1..8 with no gap and the chain verifies (a lost
        race retries on the new head instead of failing)."""
        stores = [make_store(), make_store()]
        await asyncio.gather(
            *(stores[n % 2].append(_record(summary=f"record {n}")) for n in range(8))
        )
        page = await stores[0].records_for_case(IDEM_CASE)
        assert sorted(r.seq for r in page.items) == list(range(1, 9))
        assert (await stores[0].verify(IDEM_CASE)).ok is True

    async def test_proposals_get_a_monotonic_seq_per_case(
        self, make_store: Callable[[], MongoAuditStore]
    ) -> None:
        stores = [make_store(), make_store()]
        stored = await asyncio.gather(
            *(
                stores[n % 2].append_proposal(
                    _proposal(run_id=f"run_{n % 3}", payload={"n": n})
                )
                for n in range(10)
            )
        )
        assert sorted(s.value.seq for s in stored) == list(range(1, 11))
        listed = await stores[0].proposals(IDEM_CASE, limit=50)
        assert [p.seq for p in listed.items] == list(range(1, 11))
        assert await stores[0].proposals("FCRM-2026-OTHER") == Page(
            items=[], next_cursor=None, total=None
        )

    async def test_a_keyed_proposal_is_replayed(
        self, make_store: Callable[[], MongoAuditStore]
    ) -> None:
        stores = [make_store(), make_store()]
        results = await asyncio.gather(
            *(
                stores[n % 2].append_proposal(_proposal(), idempotency_key="idem-p")
                for n in range(4)
            )
        )
        assert sorted(r.replayed for r in results) == [False, True, True, True]
        assert len({r.value.proposal_id for r in results}) == 1
        assert len((await stores[0].proposals(IDEM_CASE)).items) == 1

    async def test_proposals_filter_page_and_supersede(
        self, make_store: Callable[[], MongoAuditStore]
    ) -> None:
        store = make_store()
        first = (await store.append_proposal(_proposal())).value
        await store.append_proposal(_proposal(agent="intake", kind="extraction"))
        newer = (
            await store.append_proposal(_proposal(supersedes=first.proposal_id))
        ).value
        everything = (await store.proposals(IDEM_CASE)).items
        by_id = {p.proposal_id: p for p in everything}
        assert by_id[first.proposal_id].superseded_by == newer.proposal_id
        assert by_id[newer.proposal_id].superseded_by is None
        assert by_id[newer.proposal_id].supersedes == first.proposal_id
        assert [
            p.kind for p in (await store.proposals(IDEM_CASE, kind="extraction")).items
        ] == ["extraction"]
        page = await store.proposals(IDEM_CASE, limit=2)
        assert [p.seq for p in page.items] == [1, 2]
        assert page.next_cursor is not None
        rest = await store.proposals(IDEM_CASE, limit=2, cursor=page.next_cursor)
        assert [p.seq for p in rest.items] == [3]
        assert rest.next_cursor is None

    async def test_superseding_is_validated_inside_the_transaction(
        self, make_store: Callable[[], MongoAuditStore]
    ) -> None:
        store = make_store()
        first = (await store.append_proposal(_proposal())).value
        await store.append_proposal(_proposal(supersedes=first.proposal_id))
        with pytest.raises(ConflictError):
            await store.append_proposal(_proposal(supersedes=first.proposal_id))
        with pytest.raises(FcrmValidationError):
            await store.append_proposal(_proposal(supersedes="prp_missing"))
        other = (await store.append_proposal(_proposal("FCRM-2026-OTHER"))).value
        with pytest.raises(FcrmValidationError):
            await store.append_proposal(_proposal(supersedes=other.proposal_id))
        assert len((await store.proposals(IDEM_CASE)).items) == 2

    async def test_head_is_the_genesis_for_an_empty_case(
        self, make_store: Callable[[], MongoAuditStore]
    ) -> None:
        store = make_store()
        assert await store.head("FCRM-2026-EMPTY") == ChainHead(
            seq=0, hash=GENESIS_HASH
        )
        verified = await store.verify("FCRM-2026-EMPTY")
        assert (verified.ok, verified.record_count) == (True, 0)


class TestSandboxStoreBehaviour(_StoreBehaviour):
    """Part 1's replica set: no auth, so the indexes are created here."""

    pytestmark = sandbox_pytestmark

    @pytest.fixture
    async def make_store(self) -> AsyncIterator[Callable[[], MongoAuditStore]]:
        admin: AsyncMongoClient = AsyncMongoClient(URI, tz_aware=True)
        await admin.drop_database("fcrm_audit")
        for collection, keys, options in REQUIRED_INDEXES:
            await admin["fcrm_audit"][collection].create_index(keys, **options)
        clients: list[AsyncMongoClient] = []

        def _make() -> MongoAuditStore:
            client: AsyncMongoClient = AsyncMongoClient(URI, tz_aware=True)
            clients.append(client)
            return _store_over(client)

        yield _make
        for client in clients:
            await client.close()
        await admin.drop_database("fcrm_audit")
        await admin.close()


class TestRoleStoreBehaviour(_StoreBehaviour):
    """Part 2's auth-enabled server: every write goes through the real
    ``fcrm_audit_writer`` credential into collections whose indexes the real
    ``04-indexes.js`` created."""

    pytestmark = auth_pytestmark

    @pytest.fixture(autouse=True)
    def _empty_the_audit_collections(self, auth_enabled_mongo: dict[str, str]) -> None:
        root = pymongo.MongoClient(
            auth_enabled_mongo["root_admin_uri"], serverSelectionTimeoutMS=5000
        )
        for collection in ("audit_records", "proposals", "idempotency_keys"):
            root["fcrm_audit"][collection].delete_many({})
        root.close()

    @pytest.fixture
    async def make_store(
        self, auth_enabled_mongo: dict[str, str]
    ) -> AsyncIterator[Callable[[], MongoAuditStore]]:
        clients: list[AsyncMongoClient] = []

        def _make() -> MongoAuditStore:
            client: AsyncMongoClient = AsyncMongoClient(
                auth_enabled_mongo["writer_uri"], tz_aware=True
            )
            clients.append(client)
            return _store_over(client)

        yield _make
        for client in clients:
            await client.close()


class TestTheAuditIndexesAndTheWriterRole:
    """What ``02-users.js`` and ``04-indexes.js`` really created."""

    pytestmark = auth_pytestmark

    def test_the_real_indexes_match_what_the_sandbox_tests_assume(
        self, auth_enabled_mongo: dict[str, str]
    ) -> None:
        root = pymongo.MongoClient(
            auth_enabled_mongo["root_admin_uri"], serverSelectionTimeoutMS=5000
        )
        try:
            for collection, keys, options in REQUIRED_INDEXES:
                found = [
                    info
                    for info in root["fcrm_audit"][collection]
                    .index_information()
                    .values()
                    if list(info["key"]) == keys
                ]
                assert found, f"{collection} lacks an index on {keys}"
                for option, value in options.items():
                    assert found[0].get(option) == value, (collection, keys, option)
            legacy = root["fcrm_audit"]["proposals"].index_information()
            assert "run_id_1" not in legacy
            assert "case_id_1_agent_1_kind_1" not in legacy
        finally:
            root.close()

    @pytest.mark.parametrize("collection", ["proposals", "idempotency_keys"])
    def test_the_writer_can_insert_and_find_but_not_update_or_delete(
        self, auth_enabled_mongo: dict[str, str], collection: str
    ) -> None:
        writer = pymongo.MongoClient(
            auth_enabled_mongo["writer_uri"], serverSelectionTimeoutMS=5000
        )
        try:
            target = writer["fcrm_audit"][collection]
            doc = {"_id": f"role-probe-{collection}", "case_id": "FCRM-2026-ROLE"}
            if collection == "proposals":
                doc["seq"] = 1
            else:
                doc["at"] = datetime.now(UTC)
            target.insert_one(doc)
            assert target.find_one({"_id": doc["_id"]}) is not None
            with pytest.raises(OperationFailure) as updated:
                target.update_one({"_id": doc["_id"]}, {"$set": {"case_id": "x"}})
            assert updated.value.code == 13
            with pytest.raises(OperationFailure) as deleted:
                target.delete_one({"_id": doc["_id"]})
            assert deleted.value.code == 13
            with pytest.raises(OperationFailure) as indexed:
                target.create_index([("probe", 1)])
            assert indexed.value.code == 13
        finally:
            root = pymongo.MongoClient(
                auth_enabled_mongo["root_admin_uri"], serverSelectionTimeoutMS=5000
            )
            root["fcrm_audit"][collection].delete_many({"case_id": "FCRM-2026-ROLE"})
            root.close()
            writer.close()

    def test_the_duplicate_key_and_seq_indexes_refuse_a_second_row(
        self, auth_enabled_mongo: dict[str, str]
    ) -> None:
        writer = pymongo.MongoClient(
            auth_enabled_mongo["writer_uri"], serverSelectionTimeoutMS=5000
        )
        try:
            proposals = writer["fcrm_audit"]["proposals"]
            proposals.insert_one({"_id": "prp_a", "case_id": "FCRM-2026-DUP", "seq": 1})
            with pytest.raises(DuplicateKeyError):
                proposals.insert_one(
                    {"_id": "prp_b", "case_id": "FCRM-2026-DUP", "seq": 1}
                )
            keys = writer["fcrm_audit"]["idempotency_keys"]
            keys.insert_one({"_id": "idem-dup", "at": datetime.now(UTC)})
            with pytest.raises(DuplicateKeyError):
                keys.insert_one({"_id": "idem-dup", "at": datetime.now(UTC)})
        finally:
            root = pymongo.MongoClient(
                auth_enabled_mongo["root_admin_uri"], serverSelectionTimeoutMS=5000
            )
            root["fcrm_audit"]["proposals"].delete_many({"case_id": "FCRM-2026-DUP"})
            root["fcrm_audit"]["idempotency_keys"].delete_many({"_id": "idem-dup"})
            root.close()
            writer.close()


class TestTheRealAppOverTheWriterRole:
    """The real lifespan, the real routes and the real service token."""

    pytestmark = auth_pytestmark

    def test_post_with_an_idempotency_key_replays_over_http(
        self,
        auth_enabled_mongo: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
        live_client: LiveClient,
    ) -> None:
        for name, value in (
            ("MONGODB_URI", auth_enabled_mongo["writer_uri"]),
            ("MONGODB_DB", "fcrm_audit"),
            ("SERVICE_TOKEN_SECRET", "x" * 32),
            ("OPA_URL", "http://localhost:8181"),
            ("IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate"),
            ("ENV", "test"),
            ("SEED_ON_EMPTY", "false"),
            ("CHAIN_VERIFY_ON_START", "false"),
            ("INSERT_ONLY_CHECK_ON_START", "true"),
        ):
            monkeypatch.setenv(name, value)
        root = pymongo.MongoClient(
            auth_enabled_mongo["root_admin_uri"], serverSelectionTimeoutMS=5000
        )
        for collection in ("audit_records", "proposals", "idempotency_keys"):
            root["fcrm_audit"][collection].delete_many({})
        root.close()

        application = FastAPI(lifespan=lifespan)
        install_error_handlers(application)
        application.state.probe = ReadinessProbe()
        application.include_router(router)
        application.include_router(build_health_router(application.state.probe))

        key = {"Idempotency-Key": "idem_http_live_0001"}
        record = {
            "case_id": "FCRM-2026-HTTP",
            "type": "case.created",
            "actor": "system",
            "summary": "created",
        }
        proposal = {
            "case_id": "FCRM-2026-HTTP",
            "run_id": "run_1",
            "agent": "intake",
            "kind": "extraction",
            "payload": {"fields": 3},
            "model": "fake-model",
            "prompt_v": "intake.system@1",
            "tier": "cheap",
        }
        with live_client(application) as client:
            assert client.get("/readyz").json()["checks"]["insert_only"]["ok"] is True
            first = client.post("/records", json=record, headers=key)
            again = client.post("/records", json=record, headers=key)
            assert (first.status_code, again.status_code) == (201, 200)
            assert again.json() == first.json()
            changed = client.post(
                "/records", json={**record, "summary": "other"}, headers=key
            )
            assert changed.status_code == 409

            stored = client.post(
                "/proposals", json=proposal, headers={"Idempotency-Key": "idem_http_p1"}
            )
            replay = client.post(
                "/proposals", json=proposal, headers={"Idempotency-Key": "idem_http_p1"}
            )
            assert (stored.status_code, replay.status_code) == (201, 200)
            assert replay.json() == stored.json()
            listed = client.get(
                "/proposals", params={"case_id": "FCRM-2026-HTTP", "agent": "intake"}
            ).json()
            assert [p["seq"] for p in listed["items"]] == [1]

            rows = client.get("/records", params={"case_id": "FCRM-2026-HTTP"}).json()
            assert [r["seq"] for r in rows["items"]] == [1]
            head = client.get("/head/FCRM-2026-HTTP").json()
            assert head == {"seq": 1, "hash": first.json()["hash"]}
            verify = client.get("/verify/FCRM-2026-HTTP").json()
            assert (verify["ok"], verify["record_count"]) == (True, 1)


class TestOversightUnderTheRealRoles:
    """The examiner's collections, the analytics aggregation and the evals reader
    against the real roles ``02-users.js`` created: the insert-only writer may
    run exactly what S4's oversight code needs (``insert``, ``find`` and the
    ``$unionWith`` aggregation) and nothing more, and the evals reader may
    ``find`` the eval results and read nothing else.
    """

    pytestmark = auth_pytestmark

    @pytest.fixture(autouse=True)
    def _clean(self, auth_enabled_mongo: dict[str, str]) -> None:
        root = pymongo.MongoClient(
            auth_enabled_mongo["root_admin_uri"], serverSelectionTimeoutMS=5000
        )
        for collection in (
            "audit_records",
            "proposals",
            "examiner_questions",
            "examiner_answers",
        ):
            root["fcrm_audit"][collection].delete_many({})
        for collection in ("eval_runs", "eval_dashboards"):
            root["fcrm_evals"][collection].delete_many({})
        root.close()

    @pytest.mark.parametrize("collection", ["examiner_questions", "examiner_answers"])
    def test_the_writer_inserts_and_finds_the_examiner_rows_but_cannot_change_them(
        self, auth_enabled_mongo: dict[str, str], collection: str
    ) -> None:
        writer = pymongo.MongoClient(
            auth_enabled_mongo["writer_uri"], serverSelectionTimeoutMS=5000
        )
        try:
            target = writer["fcrm_audit"][collection]
            probe = f"probe-{collection}"
            target.insert_one({"_id": probe, "case_id": "X"})
            assert target.find_one({"_id": probe}) is not None
            with pytest.raises(OperationFailure) as updated:
                target.update_one({"_id": probe}, {"$set": {"a": 1}})
            assert updated.value.code == 13
            with pytest.raises(OperationFailure) as deleted:
                target.delete_one({"_id": probe})
            assert deleted.value.code == 13
        finally:
            writer.close()

    async def test_the_examiner_store_replays_and_refuses_over_the_writer_role(
        self, auth_enabled_mongo: dict[str, str]
    ) -> None:
        client: AsyncMongoClient = AsyncMongoClient(
            auth_enabled_mongo["writer_uri"], tz_aware=True
        )
        try:
            db = client["fcrm_audit"]
            store = MongoExaminerStore(db["examiner_questions"], db["examiner_answers"])
            now = datetime.now(UTC).replace(microsecond=0)
            question = QuestionRow("exq_live", "FCRM-2026-0151", "Why?", "u-riyer", now)
            await store.add_question(question)
            assert await store.get_question("exq_live") == question

            def answer(fingerprint: str) -> AnswerRow:
                return AnswerRow(
                    question_id="exq_live",
                    case_id="FCRM-2026-0151",
                    run_id="run_1",
                    refused=True,
                    sentences=(),
                    references=(),
                    refusal_reason="the records do not say",
                    request_fingerprint=fingerprint,
                    answered_at=now,
                )

            first = await store.add_answer(answer("a"))
            again = await store.add_answer(answer("a"))
            assert (first.replayed, again.replayed) == (False, True)
            with pytest.raises(ConflictError):
                await store.add_answer(answer("b"))
            stored = await store.get_answer("exq_live")
            assert stored is not None
            assert stored.request_fingerprint == "a"
        finally:
            await client.close()

    async def test_the_override_rates_are_one_aggregation_the_insert_only_role_may_run(
        self, auth_enabled_mongo: dict[str, str]
    ) -> None:
        client: AsyncMongoClient = AsyncMongoClient(
            auth_enabled_mongo["writer_uri"], tz_aware=True
        )
        try:
            store = _store_over(client)
            case = "FCRM-2026-OVR"
            await store.append(
                RecordAppend(
                    case_id=case,
                    type="workflow.started",
                    actor="system",
                    summary="opened",
                    data={"pins": {"scoring_v": 3, "corpus_v": 14}},
                )
            )
            for before, after in ((7.6, 4.5), (6.0, 5.0)):
                await store.append(
                    RecordAppend(
                        case_id=case,
                        type="human.override",
                        actor="u-riyer",
                        summary=f"CTL-T01 {before} → {after} by u-riyer · reason",
                        data={
                            "before": {"rating": before},
                            "after": {"rating": after},
                            "reason": "The vendor review is not on file.",
                        },
                    )
                )
            for agent in ("assessment",) * 4 + ("research",) * 2:
                await store.append_proposal(
                    ProposalAppend(
                        case_id=case,
                        run_id="run_1",
                        agent=agent,
                        kind=agent,
                        payload={},
                        model="m",
                        prompt_v="p@1",
                        tier="strong",
                    )
                )
            db = client["fcrm_audit"]
            analytics = MongoAnalyticsStore(db["audit_records"], db["proposals"])

            page = await analytics.overrides(agent=None, cursor=None, limit=1)
            assert page.total == 2
            assert page.next_cursor is not None
            assert [(r.item, r.agent) for r in page.items] == [
                ("CTL-T01", "assessment")
            ]
            rest = await analytics.overrides(
                agent=None, cursor=page.next_cursor, limit=5
            )
            assert len(rest.items) == 1
            assert {
                (r.agent, r.proposals, r.overridden, r.rate) for r in page.rates
            } == {
                ("assessment", 4, 2, 0.5),
                ("research", 2, 0, 0.0),
            }
            only = await analytics.overrides(agent="research", cursor=None, limit=5)
            assert only.items == []
            assert [r.agent for r in only.rates] == ["research"]

            assert await analytics.pinned(case) == {"scoring_v": 3, "corpus_v": 14}
            assert await analytics.current() == {"scoring_v": 3, "corpus_v": 14}
            assert await analytics.pinned("FCRM-2026-NONE") is None
        finally:
            await client.close()

    async def test_the_evals_reader_reads_the_results_and_nothing_else(
        self, auth_enabled_mongo: dict[str, str]
    ) -> None:
        root = pymongo.MongoClient(
            auth_enabled_mongo["root_admin_uri"], serverSelectionTimeoutMS=5000
        )

        def run(agent: str, week: str, day: int, score: float) -> dict[str, Any]:
            return {
                "_id": f"{week}-{day}:{agent}",
                "run_id": f"{week}-{day}",
                "agent": agent,
                "ts": datetime(2026, 10, day, 12, tzinfo=UTC),
                "week": week,
                "primary_metric": "metric",
                "n": 5,
                "gate": {"status": "PASS", "candidate": score, "baseline": None},
                "metrics": [],
                "cases": [{"row_id": "r1", "tokens": 1}],
            }

        root["fcrm_evals"]["eval_runs"].insert_many(
            [
                run("screening", "2026-W40", 1, 0.90),
                run("screening", "2026-W41", 6, 0.94),
            ]
        )
        root["fcrm_evals"]["eval_dashboards"].insert_one(
            {"_id": "latest", "run_id": "r", "html": "<p>one page</p>"}
        )
        root.close()
        client: AsyncMongoClient = AsyncMongoClient(
            auth_enabled_mongo["evals_uri"], tz_aware=True
        )
        try:
            reader = MongoEvalsReader(client["fcrm_evals"])
            quality = await agent_quality(reader, None)
            assert quality is not None
            assert quality.week == "2026-W41"
            assert [(r.agent, r.pct) for r in quality.rows] == [("screening", 94.0)]
            assert quality.drift == "+4.0 pts vs 2026-W40"
            assert await reader.dashboard() == "<p>one page</p>"
            assert await agent_quality(reader, "2026-W39") is None

            runs = client["fcrm_evals"]["eval_runs"]
            with pytest.raises(OperationFailure) as inserted:
                await runs.insert_one({"_id": "x"})
            assert inserted.value.code == 13
            with pytest.raises(OperationFailure) as updated:
                await runs.update_one(
                    {"_id": "2026-W41-6:screening"}, {"$set": {"n": 0}}
                )
            assert updated.value.code == 13
            with pytest.raises(OperationFailure) as deleted:
                await runs.delete_one({"_id": "2026-W41-6:screening"})
            assert deleted.value.code == 13
            with pytest.raises(OperationFailure) as other:
                await client["fcrm_audit"]["audit_records"].find_one({})
            assert other.value.code == 13
            with pytest.raises(OperationFailure) as elsewhere:
                await client["fcrm_evals"]["something_else"].find_one({})
            assert elsewhere.value.code == 13
        finally:
            await client.close()

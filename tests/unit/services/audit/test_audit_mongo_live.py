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

import base64
import contextlib
import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import pymongo
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_audit.lifespan import InsertOnlyRoleViolatedError, lifespan
from fcrm_audit.routes import router
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_seed.loader import demo_case
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure

DEMO_CASE_ID = demo_case()["id"]
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

    def test_the_full_lifespan_seeds_the_demo_chain_and_passes_its_self_test(
        self, app: FastAPI
    ) -> None:
        with TestClient(app) as client:
            ready = client.get("/readyz")
            assert ready.status_code == 200
            body = ready.json()
            assert body["status"] == "ok"
            assert body["checks"]["chain_verify"]["ok"] is True
            assert "insert_only" not in body["checks"]

            verify = client.get(f"/verify/{DEMO_CASE_ID}")
            assert verify.status_code == 200
            assert verify.json() == {
                "case_id": DEMO_CASE_ID,
                "record_count": 67,
                "ok": True,
                "first_break_seq": None,
            }

    def test_restarting_the_app_does_not_duplicate_the_seeded_chain(
        self, app: FastAPI
    ) -> None:
        with TestClient(app):
            pass
        with TestClient(app) as client:
            verify = client.get(f"/verify/{DEMO_CASE_ID}")
            assert verify.json()["record_count"] == 67

    def test_post_records_appends_through_a_real_transaction_and_chains(
        self, app: FastAPI
    ) -> None:
        with TestClient(app) as client:
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

    def test_get_records_paginates_the_real_seeded_demo_chain(
        self, app: FastAPI
    ) -> None:
        with TestClient(app) as client:
            page = client.get(f"/records/{DEMO_CASE_ID}", params={"limit": 10})
            assert page.status_code == 200
            body = page.json()
            assert len(body["items"]) == 10
            assert [r["seq"] for r in body["items"]] == list(range(1, 11))
            assert body["next_cursor"] is not None

            rest = client.get(
                f"/records/{DEMO_CASE_ID}",
                params={"limit": 100, "cursor": body["next_cursor"]},
            )
            rest_body = rest.json()
            assert len(rest_body["items"]) == 57
            assert rest_body["items"][-1]["seq"] == 67
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
        self, auth_enabled_mongo: dict[str, str], monkeypatch: pytest.MonkeyPatch
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

        with TestClient(application) as client:
            ready = client.get("/readyz")
            assert ready.status_code == 200
            body = ready.json()
            assert body["status"] == "ok"
            assert body["checks"]["insert_only"]["ok"] is True
            assert body["checks"]["chain_verify"]["ok"] is True

            verify = client.get(f"/verify/{DEMO_CASE_ID}")
            assert verify.json()["record_count"] == 67

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

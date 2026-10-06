"""Startup and shutdown: Mongo, seed-on-empty of the closed cases' trails, and
two startup self-tests, mirroring ``fcrm_scoring.lifespan``'s shape.

1. ``chain_verify`` -- a seeded closed case's chain (FCRM-2026-0118, the demo's
   closest precedent) must actually pass :func:`fcrm_audit.chain.verify_chain`,
   the same check ``tests/unit/services/audit/test_chain.py`` runs, against
   whatever is actually stored in *this* environment's Mongo -- catching a bad
   seed load or a hashing-format drift before the service answers any request,
   not after. The demo case FCRM-2026-0151 cannot be the probe: it has no
   records until the demo produces them live.

2. ``insert_only`` -- the connected Mongo credential must be rejected
   (``OperationFailure`` code 13, Unauthorized) when it attempts an
   ``update_one`` on ``audit_records``. If the update instead succeeds,
   the database-level guarantee this whole service's design relies on
   (``fcrmAuditInsertOnly``, ``ops/mongo/init/02-users.js``) is not
   actually in force in this environment, and the service refuses to
   report ready rather than run with a silently weaker guarantee than its
   own docs (``docs/governance/audit-chain.md``) promise. The probe never
   mutates a real record: MongoDB's authorization check runs before the
   query is evaluated, so a filter matching zero documents is rejected
   (or would succeed) exactly the same as one matching something real.

Seeding: with ``SEED_ON_EMPTY`` and an empty ``audit_records``, one bulk insert
loads a nine-record trail for each of the 65 closed seed cases
(:func:`fcrm_audit.seedmodel.build_closed_case_chains`). The demo case's 67
records are the live demo's own output (design A §11.3), never seeded.

S4 never calls ``ensure_indexes``, unlike S2: ``fcrm_audit_writer`` holds
no ``createIndex`` privilege at all (``02-users.js``'s own comment), so
every index on ``audit_records``, ``proposals`` and ``idempotency_keys`` --
including the ``{case_id, seq}`` uniqueness that turns a concurrent append
race into a retry instead of a silent corruption -- is created once, by the
root user, in ``ops/mongo/init/04-indexes.js``, never by this service.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fcrm_common.auth import install_auth
from fcrm_common.authz import install_authz
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.health import ReadinessCheck, ReadinessProbe, mongo_check
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import Repository
from fcrm_common.seedload import SeedReader, load_if_empty
from fcrm_common.service import ServiceDeps, standalone_deps
from fcrm_common.tracing import init_otel
from fcrm_contracts.api.audit import AuditRecord
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure

from fcrm_audit.examiner import ExaminerService, MongoExaminerStore
from fcrm_audit.seedmodel import build_closed_case_chains
from fcrm_audit.settings import AuditSettings
from fcrm_audit.store import MongoAuditStore

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

_UNAUTHORIZED_CODE = 13
_INSERT_ONLY_PROBE_FILTER = {"case_id": "__fcrm_audit_insert_only_probe__"}


class ChainVerifyFailedError(UpstreamUnavailableError):
    """The self-test case's seeded audit chain does not verify. Treated as an
    upstream-unavailable 503 at ``/readyz``: the service is up but its
    stored chain cannot be trusted.
    """

    code = "chain_verify_failed"
    title = "Audit chain self-test failed"


class InsertOnlyRoleViolatedError(UpstreamUnavailableError):
    """The connected Mongo credential was able to update ``audit_records``.
    This service's whole tamper-evidence story depends on that being
    impossible at the database layer, so this is a hard readiness failure,
    not a warning.
    """

    code = "insert_only_role_violated"
    title = "Audit writer role is not insert-only"


def _records_repo(db: AsyncDatabase[Any]) -> Repository[AuditRecord]:
    """``Repository[AuditRecord]`` over ``db.audit_records``. No ``id_of``:
    MongoDB assigns the ``_id`` (an ``ObjectId``), which keeps pagination
    simple (see ``store.py``'s ``records_for_case`` docstring) -- the
    ``{case_id, seq}`` uniqueness guarantee comes from the explicit index
    in ``04-indexes.js``, not from ``_id``.
    """
    return Repository(db["audit_records"], AuditRecord)


async def _chain_verify_self_test(store: MongoAuditStore, case_id: str) -> None:
    """Recompute ``case_id``'s stored chain and raise if it does not
    verify, naming the first broken ``seq``.
    """
    result = await store.verify(case_id)
    if not result.ok:
        raise ChainVerifyFailedError(
            f"seeded chain for {case_id} does not verify",
            case_id=case_id,
            first_break_seq=result.first_break_seq,
            record_count=result.record_count,
        )
    logger.info(
        "chain verify self-test passed",
        extra={"case_id": case_id, "records": result.record_count},
    )


async def _insert_only_self_test(collection: AsyncCollection[Any]) -> None:
    """Attempt a write ``audit_records`` must refuse. Never mutates a real
    record (see the module docstring): the filter matches nothing.
    """
    try:
        await collection.update_one(
            _INSERT_ONLY_PROBE_FILTER, {"$set": {"probe": True}}
        )
    except OperationFailure as exc:
        if exc.code == _UNAUTHORIZED_CODE:
            logger.info("insert-only self-test passed: update rejected")
            return
        raise
    raise InsertOnlyRoleViolatedError(
        "the connected Mongo credential was able to update audit_records; "
        "the insert-only role is not enforced in this environment"
    )


def chain_self_test_case_id(demo: Mapping[str, Any]) -> str:
    """The case whose stored chain the startup self-test verifies: the demo's
    closest precedent (a closed case with a seeded trail), never the demo case
    itself, which has no records until the demo produces them live.
    """
    case_id: str = demo["precedent"]["id"]
    return case_id


@asynccontextmanager
async def register(
    app: FastAPI, settings: AuditSettings, deps: ServiceDeps
) -> AsyncIterator[None]:
    """Wire S4 onto ``app`` from injected dependencies: seed the closed cases'
    trails on an empty collection, run both startup self-tests, then set
    auth/authz, the readiness checks and the store on ``app.state``.

    ``deps.mongo`` must be the insert-only audit-writer client: the
    insert-only self-test proves that the connected credential cannot
    update or delete, so handing S4 any other client fails readiness.

    The host owns logging, tracing and the Mongo, verifier and OPA
    clients (``fcrm_common.service``); this never opens or closes them.
    """
    # Bearer verification (fcrm_common.auth.current_user) and the OPA route
    # policy (fcrm_common.authz.opa_dependency, router-level in routes.py).
    # Without these every route answers 503, never 200: fail closed.
    install_auth(app.state, deps.token_verifier)
    install_authz(app.state, deps.opa)

    db = deps.mongo[settings.mongodb_db or "fcrm_audit"]
    records_repo = _records_repo(db)
    # No ensure_indexes() call here on purpose -- see the module
    # docstring: fcrm_audit_writer cannot create indexes.

    reader = SeedReader(settings.seed_path)
    self_test_case_id = chain_self_test_case_id(reader.demo_case())

    if settings.seed_on_empty:
        chain_docs = [
            records_repo.to_document(record)
            for record in build_closed_case_chains(reader.cases())
        ]
        await load_if_empty(records_repo, chain_docs)

    store = MongoAuditStore(
        records_repo,
        deps.mongo,
        proposals=db["proposals"],
        idempotency_keys=db["idempotency_keys"],
    )
    app.state.store = store
    app.state.examiner = ExaminerService(
        store,
        MongoExaminerStore(db["examiner_questions"], db["examiner_answers"]),
        deps.publisher,
        wait_s=settings.examiner_wait_s,
        poll_s=settings.examiner_poll_s,
    )

    # main.py built ReadinessProbe() before this ran, so the health router
    # already holds a reference to it; add checks to that same instance
    # rather than replacing it.
    probe: ReadinessProbe = app.state.probe
    probe.add(mongo_check(deps.mongo))

    if settings.chain_verify_on_start:
        await _chain_verify_self_test(store, self_test_case_id)

        async def _chain_check() -> None:
            await _chain_verify_self_test(store, self_test_case_id)

        probe.add(ReadinessCheck("chain_verify", _chain_check, critical=True))

    if settings.insert_only_check_on_start:
        await _insert_only_self_test(records_repo.collection)

        async def _insert_only_check() -> None:
            await _insert_only_self_test(records_repo.collection)

        probe.add(ReadinessCheck("insert_only", _insert_only_check, critical=True))

    yield


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Standalone entry (``uvicorn fcrm_audit.main:app``): load settings,
    configure logging and tracing, build the dependencies, then register.
    """
    settings = AuditSettings.load()
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    init_otel(settings.app_name, __version__, environment=settings.env, app=app)
    logger.info("starting", extra={"env": settings.env})
    async with standalone_deps(settings) as deps, register(app, settings, deps):
        yield

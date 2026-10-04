"""Startup and shutdown: Mongo, seed-on-empty of the demo chain, and two
startup self-tests, mirroring ``fcrm_scoring.lifespan``'s shape.

1. ``chain_verify`` -- the seeded demo case's chain must actually pass
   :func:`fcrm_audit.chain.verify_chain`, the same check
   ``tests/unit/services/audit/test_chain.py::
   test_the_demo_case_s_67_record_audit_trail_verifies_end_to_end`` runs,
   against whatever is actually stored in *this* environment's Mongo --
   catching a bad seed load or a hashing-format drift before the service
   answers any request, not after.

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

Seeding the demo chain here is a disclosed, interim deviation from
``fcrm_audit/seedmodel.py``'s own docstring and ``docs/governance/
audit-chain.md`` (both say the demo's 67 records must be the *live*
output of running the demo through the API, never seeded) -- recorded as
``docs/governance/defect-trail.md`` D-18, kept only until H1 exists to
produce them live.

S4 never calls ``ensure_indexes``, unlike S2: ``fcrm_audit_writer`` holds
no ``createIndex`` privilege at all (``02-users.js``'s own comment), so
every index on ``audit_records`` -- including the ``{case_id, seq}``
uniqueness that turns a concurrent append race into a 409 instead of a
silent corruption -- is created once, by the root user, in
``ops/mongo/init/04-indexes.js``, never by this service.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fcrm_common.auth import TokenVerifier, install_auth
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.health import ReadinessCheck, ReadinessProbe, mongo_check
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import Repository, mongo_client
from fcrm_common.seedload import SeedReader, load_if_empty
from fcrm_common.tracing import init_otel
from fcrm_contracts.api.audit import AuditRecord
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure

from fcrm_audit.seedmodel import build_demo_chain
from fcrm_audit.settings import AuditSettings
from fcrm_audit.store import MongoAuditStore

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

_UNAUTHORIZED_CODE = 13
_INSERT_ONLY_PROBE_FILTER = {"case_id": "__fcrm_audit_insert_only_probe__"}


class ChainVerifyFailedError(UpstreamUnavailableError):
    """The seeded demo case's audit chain does not verify. Treated as an
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect Mongo, seed the demo case's chain on an empty collection,
    run both startup self-tests, then wire auth/authz, the readiness
    probe and the store onto ``app.state`` for the routes to use.
    """
    settings = AuditSettings.load()
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    init_otel(settings.app_name, __version__, environment=settings.env, app=app)
    logger.info("starting", extra={"env": settings.env})

    # Bearer verification (fcrm_common.auth.current_user) and the OPA route
    # policy (fcrm_common.authz.opa_dependency, router-level in routes.py).
    # Without these every route answers 503, never 200: fail closed.
    token_verifier = TokenVerifier.from_settings(settings)
    install_auth(app.state, token_verifier)
    assert settings.opa_url is not None  # fail_fast already enforced this
    opa_client = OPAClient(settings.opa_url)
    install_authz(app.state, opa_client)

    assert settings.mongodb_uri is not None  # fail_fast already enforced this
    async with mongo_client(settings.mongodb_uri) as client:
        db = client[settings.mongodb_db or "fcrm_audit"]
        records_repo = _records_repo(db)
        # No ensure_indexes() call here on purpose -- see the module
        # docstring: fcrm_audit_writer cannot create indexes.

        reader = SeedReader(settings.seed_path)
        demo = reader.demo_case()
        demo_case_id: str = demo["id"]

        if settings.seed_on_empty:
            chain_docs = [
                records_repo.to_document(record) for record in build_demo_chain(demo)
            ]
            await load_if_empty(records_repo, chain_docs)

        store = MongoAuditStore(records_repo, client)
        app.state.store = store

        # main.py built ReadinessProbe() before this lifespan ran, so the
        # health router already holds a reference to it; add checks to
        # that same instance rather than replacing it.
        probe: ReadinessProbe = app.state.probe
        probe.add(mongo_check(client))

        if settings.chain_verify_on_start:
            await _chain_verify_self_test(store, demo_case_id)

            async def _chain_check() -> None:
                await _chain_verify_self_test(store, demo_case_id)

            probe.add(ReadinessCheck("chain_verify", _chain_check, critical=True))

        if settings.insert_only_check_on_start:
            await _insert_only_self_test(records_repo.collection)

            async def _insert_only_check() -> None:
                await _insert_only_self_test(records_repo.collection)

            probe.add(ReadinessCheck("insert_only", _insert_only_check, critical=True))

        try:
            yield
        finally:
            await token_verifier.aclose()
            await opa_client.aclose()

"""Startup and shutdown: Mongo, seed loading (keyword collections and the
vector index), the ``SearchService``, readiness.

Two independent seed-loading paths run here, each idempotent in the way
that fits its own storage:

- The Mongo collections (``clauses``, ``precedents``, ``controls`` and the
  three reference-data collections ``jurisdiction_list_versions``,
  ``field_set_versions``, ``control_inventory_versions``) use
  :func:`fcrm_common.seedload.load_if_empty`, the same guarantee every
  other service's lifespan uses: insert only into an empty collection, so
  a container restart never duplicates documents.
- The first seed also asks H4 to build the three vector indexes, once each
  (:class:`~fcrm_policy.indexing.IndexRequester`), through S3's outbox.
- The vector index has no such "is it empty" primitive exposed by
  ``evoq_context.vector.interface.VectorStore`` in general (S3 Vectors has
  no cheap count; the ``memory`` backend's upsert is naturally idempotent
  by ``chunk_key`` either way), so it is gated the same way the Mongo side
  is -- behind ``SEED_ON_EMPTY`` -- and then loaded with H4's own
  ``load_clauses``, whose upsert (not insert) is safe to repeat on every
  restart without re-checking emptiness first.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from evoq_context.llm.client import LLMClient
from evoq_context.vector.factory import create_vector_store
from fastapi import FastAPI
from fcrm_common.auth import install_auth
from fcrm_common.authz import install_authz
from fcrm_common.health import ReadinessProbe, mongo_check
from fcrm_common.logging import configure_logging
from fcrm_common.outbox import Outbox
from fcrm_common.seedload import SeedReader, load_if_empty
from fcrm_common.service import ServiceDeps, standalone_deps
from fcrm_common.tracing import init_otel
from fcrm_context_worker.config import FCRM_PROJECT, context_config_from_env
from fcrm_context_worker.seedload import load_clauses

from fcrm_policy.bus import start_events_consumer, start_relay
from fcrm_policy.config_service import build_config
from fcrm_policy.corpus import MongoClauseStore
from fcrm_policy.embedding_cache import MongoEmbeddingCache
from fcrm_policy.indexing import IndexName, IndexRequester, IndexStatusStore
from fcrm_policy.precedents import MongoPrecedentStore
from fcrm_policy.reference import (
    CONTROL_INVENTORIES,
    FIELD_SETS,
    JURISDICTION_LISTS,
    MongoReferenceStore,
    ReferenceStore,
)
from fcrm_policy.search.service import SearchService
from fcrm_policy.search.vector import VectorClauseSource
from fcrm_policy.seedmodel import (
    clause_documents,
    control_documents,
    control_inventory_documents,
    field_set_documents,
    jurisdiction_documents,
    precedent_documents,
)
from fcrm_policy.settings import PolicySettings
from fcrm_policy.store import (
    CONTROLS,
    POLICY_CLAUSES,
    PRECEDENT_CASES,
    MongoKeywordCollection,
)

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


class VectorBackendUnavailableError(RuntimeError):
    """``VECTOR_BACKEND=none`` leaves ``policy_clauses`` with no semantic
    leg; S3 cannot start without a vector index to search.
    """


@asynccontextmanager
async def register(
    app: FastAPI, settings: PolicySettings, deps: ServiceDeps
) -> AsyncIterator[None]:
    """Wire S3 onto ``app`` from injected dependencies: seed its three
    collections and the vector index on an empty store, then set
    auth/authz, the readiness check and the search service on
    ``app.state``.

    The host owns logging, tracing and the Mongo, verifier and OPA
    clients (``fcrm_common.service``); this never opens or closes them.
    """
    # Bearer verification (fcrm_common.auth.current_user) and the OPA route
    # policy (fcrm_common.authz.opa_dependency, router-level in routes.py).
    # Without these every route answers 503, never 200: fail closed.
    install_auth(app.state, deps.token_verifier)
    install_authz(app.state, deps.opa)

    db = deps.mongo[settings.mongodb_db or "fcrm_policy"]
    clauses = MongoKeywordCollection(db["clauses"], POLICY_CLAUSES)
    precedents = MongoKeywordCollection(db["precedents"], PRECEDENT_CASES)
    controls = MongoKeywordCollection(db["controls"], CONTROLS)
    for collection in (clauses, precedents, controls):
        await collection.ensure_text_index()
    outbox = Outbox(db["outbox"])
    await outbox.ensure_indexes()
    clause_store = MongoClauseStore(clauses.collection)
    await clause_store.ensure_indexes()
    reference = MongoReferenceStore(db, client=deps.mongo, outbox=outbox)
    await reference.ensure_indexes()
    precedent_store = MongoPrecedentStore(deps.mongo, precedents.collection, outbox)
    status = IndexStatusStore(db["index_status"])

    if settings.seed_on_empty:
        reader = SeedReader(settings.seed_path)
        await load_if_empty(clauses.collection, clause_documents())
        await load_if_empty(precedents.collection, precedent_documents(reader.raw))
        await load_if_empty(controls.collection, control_documents(reader.raw))
        # Reference data: loaded per collection, so a volume that predates
        # one of them gets just that one on the next start.
        await load_if_empty(
            reference.collections[JURISDICTION_LISTS],
            jurisdiction_documents(reader.raw),
        )
        await load_if_empty(
            reference.collections[FIELD_SETS], field_set_documents(reader.raw)
        )
        await load_if_empty(
            reference.collections[CONTROL_INVENTORIES],
            control_inventory_documents(reader.raw),
        )
        await IndexRequester(deps.mongo, status, outbox).request_missing(
            await _index_versions(clause_store, precedent_store, reference)
        )

    context_config = context_config_from_env()
    vector_store = create_vector_store(context_config, FCRM_PROJECT)
    if vector_store is None:
        raise VectorBackendUnavailableError(
            "VECTOR_BACKEND=none has no index for policy_clauses to search"
        )
    llm = LLMClient.from_config(context_config)
    if settings.seed_on_empty:
        await vector_store.ensure_index()
        cache = (
            MongoEmbeddingCache(db["embedding_cache"])
            if settings.skip_embed_if_present
            else None
        )
        await load_clauses(vector_store, llm, cache=cache)
    vector = VectorClauseSource(vector_store, llm, context_config)

    config, scoring_client = await build_config(
        db, deps.mongo, reference, reference, settings
    )
    app.state.config = config
    app.state.reference = reference
    app.state.clauses = clause_store
    app.state.precedents = precedent_store
    app.state.search = SearchService(
        collections={
            POLICY_CLAUSES: clauses,
            PRECEDENT_CASES: precedents,
            CONTROLS: controls,
        },
        vector=vector,
        rrf_k=settings.rrf_k,
        candidate_multiplier=settings.search_candidate_multiplier,
        top_k_max=settings.search_top_k_max,
    )

    # main.py built ReadinessProbe() before this ran, so the health router
    # already holds a reference to it; add checks to that same instance
    # rather than replacing it.
    probe: ReadinessProbe = app.state.probe
    probe.add(mongo_check(deps.mongo))

    async with AsyncExitStack() as stack:
        if scoring_client is not None:
            stack.push_async_callback(scoring_client.aclose)
        start_relay(stack, probe, outbox, db, deps)
        await start_events_consumer(
            stack,
            db=db,
            deps=deps,
            settings=settings,
            status=status,
            source=vector,
            config=context_config,
        )
        yield


async def _index_versions(
    clauses: MongoClauseStore,
    precedents: MongoPrecedentStore,
    reference: ReferenceStore,
) -> dict[IndexName, int]:
    """The version of the data each index is to be built from: the corpus
    version, the number of precedents, and the active control inventory's
    version. An index with no data yet is left out (nothing to build).
    """
    versions: dict[IndexName, int] = {}
    corpus = await clauses.latest_version()
    if corpus is not None:
        versions["policy_clauses"] = corpus
    count = await precedents.count()
    if count > 0:
        versions["precedent_cases"] = count
    inventory = await reference.control_inventory(None, None)
    if inventory is not None:
        versions["controls"] = int(inventory.version.lstrip("v"))
    return versions


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Standalone entry (``uvicorn fcrm_policy.main:app``): load settings,
    configure logging and tracing, build the dependencies, then register.
    """
    settings = PolicySettings.load()
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    init_otel(settings.app_name, __version__, environment=settings.env, app=app)
    logger.info("starting", extra={"env": settings.env})
    async with standalone_deps(settings) as deps, register(app, settings, deps):
        yield

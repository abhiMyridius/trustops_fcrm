"""Startup and shutdown: Mongo, seed loading (keyword collections and the
vector index), the ``SearchService``, readiness.

Two independent seed-loading paths run here, each idempotent in the way
that fits its own storage:

- The three Mongo collections (``clauses``, ``precedents``, ``controls``)
  use :func:`fcrm_common.seedload.load_if_empty`, the same guarantee every
  other service's lifespan uses: insert only into an empty collection, so
  a container restart never duplicates documents.
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
from contextlib import asynccontextmanager

from evoq_context.llm.client import LLMClient
from evoq_context.vector.factory import create_vector_store
from fastapi import FastAPI
from fcrm_common.auth import TokenVerifier, install_auth
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.health import ReadinessProbe, mongo_check
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import mongo_client
from fcrm_common.seedload import SeedReader, load_if_empty
from fcrm_common.tracing import init_otel
from fcrm_context_worker.config import FCRM_PROJECT, context_config_from_env
from fcrm_context_worker.seedload import load_clauses

from fcrm_policy.search.service import SearchService
from fcrm_policy.search.vector import VectorClauseSource
from fcrm_policy.seedmodel import (
    clause_documents,
    control_documents,
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
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect Mongo, seed its three collections and the vector index on
    an empty store, then wire auth/authz, the readiness probe and the
    search service onto ``app.state`` for the routes to use.
    """
    settings = PolicySettings.load()
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
        db = client[settings.mongodb_db or "fcrm_policy"]
        clauses = MongoKeywordCollection(db["clauses"], POLICY_CLAUSES)
        precedents = MongoKeywordCollection(db["precedents"], PRECEDENT_CASES)
        controls = MongoKeywordCollection(db["controls"], CONTROLS)
        for collection in (clauses, precedents, controls):
            await collection.ensure_text_index()

        if settings.seed_on_empty:
            reader = SeedReader(settings.seed_path)
            await load_if_empty(clauses.collection, clause_documents())
            await load_if_empty(precedents.collection, precedent_documents(reader.raw))
            await load_if_empty(controls.collection, control_documents(reader.raw))

        context_config = context_config_from_env()
        vector_store = create_vector_store(context_config, FCRM_PROJECT)
        if vector_store is None:
            raise VectorBackendUnavailableError(
                "VECTOR_BACKEND=none has no index for policy_clauses to search"
            )
        llm = LLMClient.from_config(context_config)
        if settings.seed_on_empty:
            await vector_store.ensure_index()
            await load_clauses(vector_store, llm)
        vector = VectorClauseSource(vector_store, llm, context_config)

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

        # main.py built ReadinessProbe() before this lifespan ran, so the
        # health router already holds a reference to it; add checks to
        # that same instance rather than replacing it.
        probe: ReadinessProbe = app.state.probe
        probe.add(mongo_check(client))

        try:
            yield
        finally:
            await token_verifier.aclose()
            await opa_client.aclose()

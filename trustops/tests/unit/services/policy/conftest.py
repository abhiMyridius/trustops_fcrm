"""Fixtures for S3's reference-data routes: the real routes over an
:class:`InMemoryReferenceStore` built from the same seed documents the Mongo
store is loaded with, called as the role under test."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_context_worker.config import context_config_from_env
from fcrm_context_worker.seedload import seeded_store
from fcrm_contracts.enums import Role
from fcrm_policy.config_effects import InMemoryScoringPublisher
from fcrm_policy.config_service import ConfigService
from fcrm_policy.config_store import InMemoryConfigStore
from fcrm_policy.corpus import InMemoryClauseStore
from fcrm_policy.precedents import InMemoryPrecedentStore
from fcrm_policy.reference import InMemoryReferenceStore
from fcrm_policy.routes import router
from fcrm_policy.search.service import SearchService
from fcrm_policy.search.vector import VectorClauseSource
from fcrm_policy.seedmodel import clause_documents, precedent_documents
from fcrm_policy.store import build_in_memory_collections
from fcrm_seed.loader import generated_clauses, load_raw


@pytest.fixture(scope="session")
def raw_seed() -> dict[str, Any]:
    """The parsed P7 seed, loaded once."""
    return load_raw()


@pytest.fixture
def reference_app(
    raw_seed: dict[str, Any], wire_test_auth: Callable[[FastAPI], FastAPI]
) -> FastAPI:
    """S3's routes (no lifespan) over the seeded in-memory reference store."""
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.reference = InMemoryReferenceStore.from_seed(raw_seed)
    return wire_test_auth(application)


@pytest.fixture
def reference_client(
    reference_app: FastAPI, act_as: Callable[[Role, str], None]
) -> TestClient:
    """A client calling as H2's service account (the agent tools' caller)."""
    act_as(Role.SERVICE, "svc:agent-worker")
    return TestClient(reference_app)


@pytest.fixture
def as_role(
    reference_app: FastAPI, act_as: Callable[[Role, str], None]
) -> Callable[[Role], TestClient]:
    """``as_role(Role.PO)``: a client calling as another role."""

    def _client(role: Role) -> TestClient:
        act_as(role, f"test:{role.value}")
        return TestClient(reference_app)

    return _client


@pytest.fixture(scope="session")
def corpus_texts() -> dict[str, str]:
    """``{clause_id: text}`` of ``clauses.json``, the 197 clauses as committed:
    what a search hit's verbatim text is compared against.
    """
    clauses = generated_clauses()
    assert clauses is not None
    return {c["clause_id"]: c["text"] for c in clauses}


@pytest.fixture
async def corpus_app(
    tmp_path: Path,
    raw_seed: dict[str, Any],
    wire_test_auth: Callable[[FastAPI], FastAPI],
) -> FastAPI:
    """Every S3 route over in-memory stores: the 197-clause corpus (keyword
    ranking, real H4 vector engine with dummy embeddings, clause store), the 65
    precedents and the reference data, authenticated as whoever ``act_as`` set.
    """
    config = context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": "64"},
        projects_root=tmp_path,
    )
    vector_store, llm = await seeded_store(config)
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.search = SearchService(
        collections=build_in_memory_collections(raw_seed),
        vector=VectorClauseSource(vector_store, llm, config),
    )
    application.state.clauses = InMemoryClauseStore(clause_documents())
    application.state.precedents = InMemoryPrecedentStore(precedent_documents(raw_seed))
    application.state.reference = InMemoryReferenceStore.from_seed(raw_seed)
    return wire_test_auth(application)


@pytest.fixture
def corpus_as(
    corpus_app: FastAPI, act_as: Callable[[Role, str], None]
) -> Callable[[Role], TestClient]:
    """``corpus_as(Role.ANALYST)``: a client calling the corpus app as a role."""

    def _client(role: Role) -> TestClient:
        act_as(role, f"test:{role.value}")
        return TestClient(corpus_app)

    return _client


#: The instant every config test runs at, so a snapshot's ``published_at`` and a
#: control's stale flag are the same on every run.
CONFIG_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def config_reference(raw_seed: dict[str, Any]) -> InMemoryReferenceStore:
    """A fresh seeded reference store per test (a publish mutates it)."""
    return InMemoryReferenceStore.from_seed(raw_seed)


@pytest.fixture
def scoring_publisher() -> InMemoryScoringPublisher:
    """S2's publish rules without S2: base-version guard, repeat is a no-op."""
    return InMemoryScoringPublisher()


@pytest.fixture
def config_store() -> InMemoryConfigStore:
    """An empty snapshot log (``config_service`` seeds it)."""
    return InMemoryConfigStore()


@pytest.fixture
async def config_service(
    raw_seed: dict[str, Any],
    config_store: InMemoryConfigStore,
    config_reference: InMemoryReferenceStore,
    scoring_publisher: InMemoryScoringPublisher,
) -> ConfigService:
    """The configuration service over in-memory stores, seeded, at a fixed
    clock, previewing against the seeded cases.
    """
    service = ConfigService(
        store=config_store,
        reference=config_reference,
        writer=config_reference,
        scoring=scoring_publisher,
        clock=lambda: CONFIG_NOW,
    )
    await service.seed(raw_seed)
    return service


@pytest.fixture
def config_app(
    config_service: ConfigService,
    config_reference: InMemoryReferenceStore,
    wire_test_auth: Callable[[FastAPI], FastAPI],
) -> FastAPI:
    """S3's routes over the config service, authenticated as whoever
    ``act_as`` set.
    """
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.state.config = config_service
    application.state.reference = config_reference
    return wire_test_auth(application)


@pytest.fixture
def config_as(
    config_app: FastAPI, act_as: Callable[[Role, str], None]
) -> Callable[[Role], TestClient]:
    """``config_as(Role.ANALYST)``: a client calling the config routes as a role."""

    def _client(role: Role) -> TestClient:
        act_as(role, f"Test {role.value}")
        return TestClient(config_app)

    return _client

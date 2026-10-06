"""Fixtures for S2's routing-rules read and its service-only publish routes: the
real routes over in-memory stores seeded from the P7 seed, called as the role
under test (``act_as`` and ``wire_test_auth`` come from ``tests/conftest.py``).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.api.scoring import (
    RoutingRule,
    RoutingRulesVersion,
    ScoringModelVersion,
)
from fcrm_contracts.enums import Role
from fcrm_scoring.config_publish import InMemoryPublishStore
from fcrm_scoring.routes import router
from fcrm_scoring.routing_rules import InMemoryRoutingStore
from fcrm_scoring.seedmodel import scoring_model_from_seed
from fcrm_scoring.store import InMemoryConfigStore
from fcrm_seed.loader import routing_rules, scoring_model


@pytest.fixture
def seeded_model() -> ScoringModelVersion:
    """Scoring model v3 as S2 seeds it (active)."""
    return scoring_model_from_seed(scoring_model())


@pytest.fixture
def seeded_routing() -> RoutingRulesVersion:
    """Routing rules v4 as S2 seeds it (active): R1-R6, the seed's text."""
    return RoutingRulesVersion(
        version=4,
        rules=[
            RoutingRule(rule_id=r["id"], label=r["id"], description=r["rule"])
            for r in routing_rules()
        ],
        active=True,
    )


@pytest.fixture
def publish_store(
    seeded_model: ScoringModelVersion, seeded_routing: RoutingRulesVersion
) -> InMemoryPublishStore:
    """The publish store over a fresh pair of one-version lists."""
    return InMemoryPublishStore([seeded_model], [seeded_routing])


@pytest.fixture
def routing_app(
    publish_store: InMemoryPublishStore,
    seeded_model: ScoringModelVersion,
    wire_test_auth: Callable[[FastAPI], FastAPI],
) -> FastAPI:
    """S2's routes with the routing read and the publish wired to the same lists."""
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.store = InMemoryConfigStore(models=[seeded_model], routing=[])
    application.state.routing = InMemoryRoutingStore(publish_store.routing)
    application.state.publisher = publish_store
    return wire_test_auth(application)


@pytest.fixture
def routing_as(
    routing_app: FastAPI, act_as: Callable[[Role, str], None]
) -> Callable[[Role], TestClient]:
    """``routing_as(Role.COMMITTEE)``: a client calling S2 as a role."""

    def _client(role: Role) -> TestClient:
        act_as(role, f"test:{role.value}")
        return TestClient(routing_app)

    return _client

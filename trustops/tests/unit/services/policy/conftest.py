"""Fixtures for S3's reference-data routes: the real routes over an
:class:`InMemoryReferenceStore` built from the same seed documents the Mongo
store is loaded with, called as the role under test."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Role
from fcrm_policy.reference import InMemoryReferenceStore
from fcrm_policy.routes import router
from fcrm_seed.loader import load_raw


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

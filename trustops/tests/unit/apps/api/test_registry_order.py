"""fcrm_api.registry/main: every service mounts under its public prefix and
S1, whose prefix ``/api/fcrm`` prefixes all the others, mounts last."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fcrm_api.main import create_app
from fcrm_api.registry import MOUNTS
from starlette.routing import Mount


@pytest.fixture(scope="module")
def client() -> TestClient:
    """The composite app without its lifespan: routing only, nothing started."""
    return TestClient(create_app())


def test_mounts_follow_the_registry_order_with_s1_last():
    app = create_app()
    mounted = [route.path for route in app.routes if isinstance(route, Mount)]
    assert mounted == [mount.prefix for mount in MOUNTS]
    assert MOUNTS[-1].component_id == "S1"
    assert MOUNTS[-1].prefix == "/api/fcrm"


def test_no_mount_is_shadowed_by_an_earlier_shorter_prefix():
    for index, mount in enumerate(MOUNTS):
        for later in MOUNTS[index + 1 :]:
            assert not later.prefix.startswith(mount.prefix + "/"), (
                f"{mount.component_id} ({mount.prefix}) would shadow "
                f"{later.component_id} ({later.prefix})"
            )


def test_component_ids_and_database_names_are_unique():
    assert len({mount.component_id for mount in MOUNTS}) == len(MOUNTS)
    assert len({mount.db_name for mount in MOUNTS}) == len(MOUNTS)


def test_only_audit_receives_the_insert_only_mongo_role():
    audit_writers = [m.component_id for m in MOUNTS if m.mongo_role == "audit_writer"]
    assert audit_writers == ["S4"]


def test_the_aggregate_liveness_answers_at_the_root(client: TestClient):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("prefix", [mount.prefix for mount in MOUNTS])
def test_each_service_answers_liveness_under_its_own_prefix(
    client: TestClient, prefix: str
):
    response = client.get(f"{prefix}/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/fcrm/scoring/models/active",
        "/api/fcrm/policy/search",
        "/api/fcrm/audit/verify/FCRM-2026-0151",
        "/api/fcrm/decision/packs/pack_x",
        "/api/fcrm/auth/me",
        "/api/fcrm/cases",
    ],
)
def test_service_routes_resolve_and_fail_closed_without_a_started_service(
    client: TestClient, path: str
):
    # The lifespan has not run, so no verifier is installed: a route that
    # exists answers 401/503 (never 404), proving the right sub-app owns it.
    assert client.request("GET", path).status_code in {401, 403, 405, 503}


def test_a_service_path_does_not_fall_through_to_s1(client: TestClient):
    # /api/fcrm/auth is X1's mount; S1 has no such route, and X1 must answer
    # the 404 itself instead of S1 (mounted last) handling the path.
    assert client.get("/api/fcrm/auth/cases").status_code == 404

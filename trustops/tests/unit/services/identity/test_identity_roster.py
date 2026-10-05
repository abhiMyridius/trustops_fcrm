"""``GET /users?role=``: the roster R5 auto-triage and committee release read.

OPA is stubbed here (always allow, or always deny); the rule itself, which
lets analysts, committee members and the service account call it and keeps
product owners and observers out, is covered by the Rego tests
(``ops/opa/tests/authz_test.rego``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role
from identity_test_support import DEMO_PASSWORD, build_app, make_user


def _people():
    return [
        make_user("u-pnair", "Priya Nair", Role.PO, unit="Payments"),
        make_user("u-riyer", "Rahul Iyer", Role.ANALYST, title="FCRM Analyst"),
        make_user("u-mshah", "Meera Shah", Role.ANALYST, title="FCRM Analyst"),
        make_user("u-gone", "Gone Analyst", Role.ANALYST, enabled=False),
        make_user("u-amendes", "Ana Mendes", Role.COMMITTEE),
        make_user("u-jokafor", "Joseph Okafor", Role.COMMITTEE),
        make_user("u-lchen", "Lin Chen", Role.COMMITTEE),
    ]


def _login(client: TestClient, username: str = "riyer") -> dict[str, str]:
    token = client.post(
        "/login", json={"username": username, "password": DEMO_PASSWORD}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client() -> TestClient:
    app, _signing_key = build_app(_people())
    return TestClient(app)


def test_roster_lists_the_enabled_people_of_the_role_in_seed_order(
    client: TestClient,
):
    response = client.get(
        "/users", params={"role": "fcrm_analyst"}, headers=_login(client)
    )
    assert response.status_code == 200
    assert response.json() == [
        {"id": "u-riyer", "name": "Rahul Iyer", "roles": ["fcrm_analyst"]},
        {"id": "u-mshah", "name": "Meera Shah", "roles": ["fcrm_analyst"]},
    ]


def test_roster_entries_carry_the_unit_only_when_there_is_one(client: TestClient):
    po = client.get("/users", params={"role": "fcrm_po"}, headers=_login(client))
    assert po.json() == [
        {
            "id": "u-pnair",
            "name": "Priya Nair",
            "roles": ["fcrm_po"],
            "unit": "Payments",
        }
    ]


def test_roster_counts_the_committee(client: TestClient):
    response = client.get(
        "/users", params={"role": "fcrm_committee"}, headers=_login(client)
    )
    assert [u["id"] for u in response.json()] == ["u-amendes", "u-jokafor", "u-lchen"]


def test_roster_for_a_role_nobody_holds_is_an_empty_list(client: TestClient):
    response = client.get(
        "/users", params={"role": "fcrm_observer"}, headers=_login(client)
    )
    assert response.status_code == 200
    assert response.json() == []


def test_roster_never_exposes_credentials_or_account_flags(client: TestClient):
    entry = client.get(
        "/users", params={"role": "fcrm_analyst"}, headers=_login(client)
    ).json()[0]
    assert set(entry) <= {"id", "name", "roles", "unit"}


@pytest.mark.parametrize(
    "params", [{}, {"role": "fcrm_nobody"}], ids=["missing", "bad"]
)
def test_roster_needs_a_known_role(client: TestClient, params: dict[str, str]):
    response = client.get("/users", params=params, headers=_login(client))
    assert response.status_code == 422


def test_roster_needs_a_token(client: TestClient):
    assert client.get("/users", params={"role": "fcrm_analyst"}).status_code == 401


def test_roster_is_403_when_opa_denies():
    app, _signing_key = build_app(_people(), opa_allow=False)
    client = TestClient(app)
    headers = _login(client)
    response = client.get("/users", params={"role": "fcrm_analyst"}, headers=headers)
    assert response.status_code == 403


def test_roster_is_not_open_to_a_refresh_token(client: TestClient):
    client.post("/login", json={"username": "riyer", "password": DEMO_PASSWORD})
    refresh = client.cookies.get("fcrm_refresh")
    response = client.get(
        "/users",
        params={"role": "fcrm_analyst"},
        headers={"Authorization": f"Bearer {refresh}"},
    )
    assert response.status_code == 401

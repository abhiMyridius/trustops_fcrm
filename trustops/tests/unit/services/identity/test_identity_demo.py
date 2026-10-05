"""The local demo picker (``GET /demo-users``) and password-less login.

Both exist only when ``DEMO_USERS_ENABLED=true`` *and* ``ENV`` is a local
environment (``IdentitySettings.demo_login_active``). Everywhere else the
endpoint is a plain 404 and an empty password is an ordinary wrong password:
one generic message, the same bcrypt check as any other wrong password.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role
from identity_test_support import DEMO_PASSWORD, build_app, make_user

GENERIC_DETAIL = "Unknown user or wrong password."


def _people():
    return [
        make_user(
            "u-pnair", "Priya Nair", Role.PO, title="Product Owner", unit="Payments"
        ),
        make_user("u-riyer", "Rahul Iyer", Role.ANALYST, title="FCRM Analyst"),
        make_user("u-amendes", "Ana Mendes", Role.COMMITTEE, title="Risk Committee"),
        make_user(
            "u-observer",
            "Panel Observer",
            Role.OBSERVER,
            title="Observer (harness monitor only)",
        ),
        make_user(
            "u-gone", "Gone Person", Role.ANALYST, title="FCRM Analyst", enabled=False
        ),
    ]


def _client(**settings: object) -> TestClient:
    app, _signing_key = build_app(_people(), **settings)
    return TestClient(app)


@pytest.fixture
def demo_client() -> TestClient:
    return _client(demo_users_enabled=True, env="local")


# ── the picker ───────────────────────────────────────────────────────────


def test_demo_users_lists_enabled_people_grouped_in_picker_order(
    demo_client: TestClient,
):
    response = demo_client.get("/demo-users")
    assert response.status_code == 200
    groups = response.json()
    assert [g["role"] for g in groups] == [
        "fcrm_po",
        "fcrm_analyst",
        "fcrm_committee",
        "fcrm_observer",
    ]
    assert [g["label"] for g in groups] == [
        "Product owners",
        "FCRM analysts",
        "Risk Committee",
        "Observer",
    ]
    # The disabled analyst is not offered.
    assert [u["username"] for u in groups[1]["users"]] == ["riyer"]


def test_demo_users_entries_match_the_web_shape(demo_client: TestClient):
    po = demo_client.get("/demo-users").json()[0]["users"][0]
    assert po == {
        "username": "pnair",
        "name": "Priya Nair",
        "role": "fcrm_po",
        "title": "Product Owner",
        "unit": "Payments",
    }


def test_demo_users_needs_no_token(demo_client: TestClient):
    assert "Authorization" not in demo_client.headers
    assert demo_client.get("/demo-users").status_code == 200


@pytest.mark.parametrize("env", ["local", "test", "ci"])
def test_demo_users_is_served_in_every_local_environment(env: str):
    client = _client(demo_users_enabled=True, env=env)
    assert client.get("/demo-users").status_code == 200


def test_demo_users_is_404_when_not_enabled():
    client = _client(demo_users_enabled=False, env="local")
    response = client.get("/demo-users")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("env", ["dev", "staging", "prod", "production"])
def test_demo_users_is_404_outside_a_local_environment_even_when_enabled(env: str):
    client = _client(demo_users_enabled=True, env=env)
    assert client.get("/demo-users").status_code == 404


# ── password-less login ──────────────────────────────────────────────────


def test_empty_password_signs_a_named_user_in_when_demo_login_is_active(
    demo_client: TestClient,
):
    response = demo_client.post("/login", json={"username": "riyer", "password": ""})
    assert response.status_code == 200
    assert response.json()["user"]["id"] == "u-riyer"
    assert demo_client.cookies.get("fcrm_refresh") is not None


def test_demo_login_works_for_every_group_on_the_picker(demo_client: TestClient):
    for group in demo_client.get("/demo-users").json():
        for user in group["users"]:
            response = demo_client.post(
                "/login", json={"username": user["username"], "password": ""}
            )
            assert response.status_code == 200, user["username"]
            assert response.json()["user"]["roles"] == [group["role"]]


def test_demo_login_still_checks_a_non_empty_password(demo_client: TestClient):
    wrong = demo_client.post("/login", json={"username": "riyer", "password": "nope"})
    right = demo_client.post(
        "/login", json={"username": "riyer", "password": DEMO_PASSWORD}
    )
    assert wrong.status_code == 401
    assert right.status_code == 200


@pytest.mark.parametrize("username", ["nobody", "gone"])
def test_demo_login_refuses_an_unknown_or_disabled_user(
    demo_client: TestClient, username: str
):
    response = demo_client.post("/login", json={"username": username, "password": ""})
    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_DETAIL


def test_demo_token_is_a_real_access_token(demo_client: TestClient):
    token = demo_client.post(
        "/login", json={"username": "pnair", "password": ""}
    ).json()["access_token"]
    me = demo_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["id"] == "u-pnair"


@pytest.mark.parametrize(
    "settings",
    [
        {"demo_users_enabled": False, "env": "local"},
        {"demo_users_enabled": True, "env": "prod"},
        {"demo_users_enabled": True, "env": "staging"},
    ],
    ids=["flag-off", "prod", "staging"],
)
def test_empty_password_is_an_ordinary_wrong_password_when_demo_login_is_off(
    settings: dict[str, object],
):
    client = _client(**settings)
    empty = client.post("/login", json={"username": "riyer", "password": ""})
    wrong = client.post("/login", json={"username": "riyer", "password": "nope"})
    unknown = client.post("/login", json={"username": "nobody", "password": ""})
    assert empty.status_code == wrong.status_code == unknown.status_code == 401
    # One generic message for all three, so nothing reveals which it was.
    assert empty.json()["detail"] == wrong.json()["detail"] == GENERIC_DETAIL
    assert unknown.json()["detail"] == GENERIC_DETAIL
    assert client.cookies.get("fcrm_refresh") is None


def test_demo_login_is_logged_by_subject_only(
    demo_client: TestClient, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.INFO):
        demo_client.post("/login", json={"username": "riyer", "password": ""})
    records = [r for r in caplog.records if r.getMessage() == "demo login"]
    assert len(records) == 1
    assert records[0].sub == "u-riyer"  # type: ignore[attr-defined]
    assert "password" not in {k.lower() for k in vars(records[0])}

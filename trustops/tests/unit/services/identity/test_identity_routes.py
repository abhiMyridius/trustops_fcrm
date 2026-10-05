"""HTTP-layer tests for X1, with no Mongo dependency: the route layer
takes whatever implements ``IdentityStore`` (``fcrm_identity.store``), so
these wire in ``InMemoryIdentityStore`` and exercise the app exactly as a
caller would over HTTP -- the same split ``fcrm_scoring``'s
``test_routes.py`` uses.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role
from identity_test_support import DEMO_PASSWORD, build_app, make_user


@pytest.fixture
def users():
    return [
        make_user("u-pnair", "Priya Nair", Role.PO),
        make_user("u-riyer", "Rahul Iyer", Role.ANALYST),
        make_user("u-amendes", "Ana Mendes", Role.COMMITTEE),
    ]


@pytest.fixture
def app(users):
    application, _signing_key = build_app(users)
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


# ── login ────────────────────────────────────────────────────────────────


def test_login_with_seed_id_succeeds(client: TestClient):
    response = client.post(
        "/login", json={"email": "u-riyer", "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["expires_in"] == 900
    assert client.cookies.get("fcrm_refresh") is not None


def test_login_with_bare_username_succeeds(client: TestClient):
    """``u-riyer``'s id without the ``u-`` prefix (the web login form's
    "username", ``src/web/src/mocks/users.ts``) also logs in.
    """
    response = client.post("/login", json={"email": "riyer", "password": DEMO_PASSWORD})
    assert response.status_code == 200


def test_login_wrong_password_is_401(client: TestClient):
    response = client.post("/login", json={"email": "u-riyer", "password": "wrong"})
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"


def test_login_unknown_user_is_401(client: TestClient):
    response = client.post(
        "/login", json={"email": "u-nobody", "password": DEMO_PASSWORD}
    )
    assert response.status_code == 401


def test_login_response_never_carries_a_refresh_token_in_the_body(client: TestClient):
    response = client.post(
        "/login", json={"email": "u-riyer", "password": DEMO_PASSWORD}
    )
    assert set(response.json().keys()) == {"access_token", "expires_in"}


# ── refresh ──────────────────────────────────────────────────────────────


def test_refresh_rotates_the_cookie_and_issues_a_new_access_token(client: TestClient):
    login = client.post("/login", json={"email": "u-riyer", "password": DEMO_PASSWORD})
    old_refresh = client.cookies.get("fcrm_refresh")
    refreshed = client.post("/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != login.json()["access_token"]
    assert client.cookies.get("fcrm_refresh") != old_refresh


def test_refresh_without_a_cookie_is_401(client: TestClient):
    assert client.post("/refresh").status_code == 401


def test_refresh_accepts_the_token_in_the_body_for_non_browser_clients(
    client: TestClient,
):
    login = client.post("/login", json={"email": "u-riyer", "password": DEMO_PASSWORD})
    client.cookies.clear()
    refresh_cookie = login.headers["set-cookie"]
    # Extract the fcrm_refresh value from the Set-Cookie header manually,
    # since the cookie jar was just cleared to simulate a non-browser
    # client that never stored it.
    token = [
        part.split("=", 1)[1]
        for part in refresh_cookie.split(";")
        if part.strip().startswith("fcrm_refresh=")
    ][0]
    response = client.post("/refresh", json={"refresh_token": token})
    assert response.status_code == 200


def test_refresh_rejects_a_reused_token_after_rotation(client: TestClient):
    client.post("/login", json={"email": "u-riyer", "password": DEMO_PASSWORD})
    first_cookie = client.cookies.get("fcrm_refresh")
    client.post("/refresh")
    client.cookies.set("fcrm_refresh", first_cookie)
    reused = client.post("/refresh")
    assert reused.status_code == 401


# ── me / users/me ────────────────────────────────────────────────────────


def test_me_returns_the_caller_profile(client: TestClient):
    login = client.post("/login", json={"email": "u-riyer", "password": DEMO_PASSWORD})
    token = login.json()["access_token"]
    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {
        "sub": "u-riyer",
        "name": "Rahul Iyer",
        "roles": ["fcrm_analyst"],
    }


def test_users_me_is_the_same_as_me(client: TestClient):
    login = client.post("/login", json={"email": "u-pnair", "password": DEMO_PASSWORD})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        client.get("/me", headers=headers).json()
        == client.get("/users/me", headers=headers).json()
    )


def test_me_without_a_token_is_401(client: TestClient):
    assert client.get("/me").status_code == 401


# ── logout ───────────────────────────────────────────────────────────────


def test_logout_revokes_the_refresh_token(client: TestClient):
    login = client.post("/login", json={"email": "u-riyer", "password": DEMO_PASSWORD})
    access = login.json()["access_token"]
    refresh_cookie = client.cookies.get("fcrm_refresh")
    logout = client.post("/logout", headers={"Authorization": f"Bearer {access}"})
    assert logout.status_code == 204
    client.cookies.set("fcrm_refresh", refresh_cookie)
    assert client.post("/refresh").status_code == 401


def test_logout_requires_a_bearer_token(client: TestClient):
    assert client.post("/logout").status_code == 401


# ── core/validate, core/userinfo ─────────────────────────────────────────


def test_core_validate_accepts_an_access_token(client: TestClient):
    login = client.post("/login", json={"email": "u-riyer", "password": DEMO_PASSWORD})
    token = login.json()["access_token"]
    response = client.post(
        "/core/validate", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["sub"] == "u-riyer"
    assert body["roles"] == ["fcrm_analyst"]


def test_core_userinfo_is_the_same_contract(client: TestClient):
    login = client.post("/login", json={"email": "u-pnair", "password": DEMO_PASSWORD})
    token = login.json()["access_token"]
    response = client.get(
        "/core/userinfo", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["sub"] == "u-pnair"


def test_core_validate_without_a_token_is_401(client: TestClient):
    assert client.post("/core/validate").status_code == 401


# ── jwks ─────────────────────────────────────────────────────────────────


def test_jwks_endpoint_is_public_and_cacheable(client: TestClient):
    response = client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=600"
    assert response.json()["keys"][0]["kty"] == "RSA"


def test_jwks_endpoint_matches_the_signing_key_issuing_tokens(app):
    client = TestClient(app)
    response = client.get("/.well-known/jwks.json")
    assert response.json()["keys"][0]["kid"] == app.state.signing_key.kid


# ── health ───────────────────────────────────────────────────────────────


def test_healthz_and_readyz_need_no_auth(client: TestClient):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200

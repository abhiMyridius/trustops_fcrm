"""The three verified ``evoqidentityservice`` bugs (CLAUDE.md's AEO-reuse
table; UPSTREAM.md), one regression test each, reproducing the original
failure mode as closely as this clone's shape allows.

1. Refresh tokens accepted as access tokens: the cloned
   ``customjwt/jwt4evoq.py::validate_jwt_token`` decoded a token and
   returned its payload without ever reading the ``type`` claim, so
   ``interceptors/jwtvalidation.py::intercept_for_jwt_token_validation``
   (used by ``/validate`` and ``/userinfo``) accepted a refresh token
   anywhere an access token was expected.
2. OTPs logged in plaintext: ``services/customauth_service.py::processOTPLogin``
   ran ``logger.info(f"... with OTP {otp} ...")`` on every request. Design
   A §8.1 fix 2 removes the OTP flow entirely (password login replaces
   it, see ``fcrm_contracts.api.identity.LoginRequest``) rather than
   patch the log line, so the regression test below asserts both that no
   OTP-shaped code exists in this package and that the password which
   replaced it is never logged either.
3. Hard-coded secret fallbacks: ``config/config.py`` gave
   ``JWT_SECRET_KEY`` and ``INTERNAL_CLIENT_SECRET`` baked-in default
   values used silently when the environment variable was unset.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fcrm_common.settings import MissingSettingsError
from fcrm_contracts.enums import Role
from fcrm_identity import jwtkeys, routes
from fcrm_identity.settings import IdentitySettings
from identity_test_support import DEMO_PASSWORD, build_app, make_user

ALL_REQUIRED = {
    "MONGODB_URI": "mongodb://mongo:27017/?replicaSet=rs0",
    "MONGODB_DB": "fcrm_identity",
    "SERVICE_TOKEN_SECRET": "x" * 40,
    "OPA_URL": "http://opa:8181",
    "IDENTITY_JWKS_URL": "http://identity:8000/api/fcrm/auth/.well-known/jwks.json",
    "JWT_KEY_FILE": "/keys/jwt.pem",
    "DEMO_USERS_PASSWORD_HASH": "$2b$12$" + "a" * 53,
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (*ALL_REQUIRED, "IDENTITY_VALIDATE_URL", "SEED_PATH", "SEED_ON_EMPTY"):
        monkeypatch.delenv(name, raising=False)


def _package_root() -> Path:
    return Path(routes.__file__).parent


def _code_identifiers(py_file: Path) -> set[str]:
    """Every name this file's code actually binds or references --
    function/class names, assignment targets, attribute accesses -- as
    opposed to its docstrings and comments, which are free to *describe*
    a bug (including a removed identifier's old name) without that
    description being mistaken for the bug itself.
    """
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


# ── bug 1: refresh token accepted as access token ────────────────────────


def test_refresh_token_rejected_as_access_token():
    """End to end, at the service boundary: log in (mints both an access
    and a refresh token), then present the *refresh* token as the Bearer
    credential on every route that requires an access token. Each must
    reject it with 401 -- the exact scenario the cloned ``/validate`` and
    ``/userinfo`` got wrong.
    """
    app, _signing_key = build_app([make_user("u-riyer", "Rahul Iyer", Role.ANALYST)])
    client = TestClient(app)

    login = client.post("/login", json={"email": "u-riyer", "password": DEMO_PASSWORD})
    refresh_token = client.cookies.get("fcrm_refresh")
    access_token = login.json()["access_token"]
    assert refresh_token != access_token

    refresh_headers = {"Authorization": f"Bearer {refresh_token}"}
    assert client.post("/core/validate", headers=refresh_headers).status_code == 401
    assert client.get("/core/userinfo", headers=refresh_headers).status_code == 401
    assert client.get("/me", headers=refresh_headers).status_code == 401

    # Sanity: the access token these same routes were built to accept
    # really does work, so the 401s above are the type check, not a
    # broken dependency chain.
    access_headers = {"Authorization": f"Bearer {access_token}"}
    assert client.post("/core/validate", headers=access_headers).status_code == 200


def test_access_token_rejected_where_a_refresh_token_is_required():
    """The mirror image: ``POST /refresh`` must reject an access token
    presented as the refresh credential. This service's own
    ``TokenIssuer.decode(expected_typ=...)`` enforces the ``typ`` check
    unconditionally on both sides, the check the clone never made at all.
    """
    app, _signing_key = build_app([make_user("u-riyer", "Rahul Iyer", Role.ANALYST)])
    client = TestClient(app)
    login = client.post("/login", json={"email": "u-riyer", "password": DEMO_PASSWORD})
    access_token = login.json()["access_token"]
    client.cookies.clear()
    response = client.post("/refresh", json={"refresh_token": access_token})
    assert response.status_code == 401


# ── bug 2: OTP (and its replacement, the password) never logged ─────────


def _all_log_text(records: list[logging.LogRecord]) -> str:
    """Every record's rendered message plus every ``extra=`` value,
    flattened to one string a substring check can scan -- the same two
    places ``fcrm_common.logging``'s JSON formatter reads from.
    """
    base_attrs = logging.LogRecord("", 0, "", 0, "", None, None).__dict__
    chunks: list[str] = []
    for record in records:
        chunks.append(record.getMessage())
        for key, value in vars(record).items():
            if key not in base_attrs and key not in ("message", "asctime"):
                chunks.append(f"{key}={value!r}")
    return "\n".join(chunks)


def test_otp_never_appears_in_logs(caplog: pytest.LogCaptureFixture):
    """No code path in this package can log an OTP, because there is no
    OTP flow at all (design A §8.1 fix 2 removes it; ``LoginRequest`` has
    only ``email``/``password``). This enforces both halves of that
    decision: (a) the password that replaced the OTP is never logged
    across a correct *and* an incorrect login attempt, and (b) no
    OTP-named identifier exists anywhere in this package's code, so the
    flow cannot quietly come back without this test failing on its own.
    """
    secret_password = "sw0rdfish-not-logged"
    app, _signing_key = build_app(
        [make_user("u-riyer", "Rahul Iyer", Role.ANALYST, password=secret_password)]
    )
    client = TestClient(app)

    with caplog.at_level(logging.DEBUG):
        client.post("/login", json={"email": "u-riyer", "password": secret_password})
        client.post("/login", json={"email": "u-riyer", "password": "wrong-one"})
        client.post("/login", json={"email": "u-nobody", "password": secret_password})

    rendered = _all_log_text(caplog.records)
    assert secret_password not in rendered
    assert "wrong-one" not in rendered

    for py_file in _package_root().rglob("*.py"):
        identifiers = {name.lower() for name in _code_identifiers(py_file)}
        assert not any("otp" in name for name in identifiers), (
            f"found an OTP-named identifier in {py_file}: the flow was removed, "
            "it must not come back"
        )


# ── bug 3: hard-coded secret fallbacks ───────────────────────────────────


def test_missing_secret_fails_fast_at_startup(monkeypatch, tmp_path: Path):
    """Neither of this service's two secrets (the RS256 signing key file,
    the shared demo password hash) has a default. Missing either one
    raises at ``IdentitySettings.load()`` -- before Mongo is even
    touched, let alone before the app answers a request -- naming
    exactly what is missing, the opposite of the cloned ``config.py``'s
    ``JWT_SECRET_KEY`` and ``INTERNAL_CLIENT_SECRET`` baked-in defaults.
    """
    for name, value in ALL_REQUIRED.items():
        monkeypatch.setenv(name, value)

    monkeypatch.delenv("JWT_KEY_FILE")
    with pytest.raises(MissingSettingsError) as missing_key:
        IdentitySettings.load()
    assert missing_key.value.missing == ["JWT_KEY_FILE"]
    monkeypatch.setenv("JWT_KEY_FILE", str(tmp_path / "jwt.pem"))

    monkeypatch.delenv("DEMO_USERS_PASSWORD_HASH")
    with pytest.raises(MissingSettingsError) as missing_hash:
        IdentitySettings.load()
    assert missing_hash.value.missing == ["DEMO_USERS_PASSWORD_HASH"]


def test_signing_key_file_must_exist_and_parse_no_silent_fallback(tmp_path: Path):
    """Even once the environment variable is *set*, a bad value (missing
    file, or a file that is not an RSA private key) still refuses to
    start rather than minting an ephemeral or default key -- the other
    half of "no silent fallback" that an env-var presence check alone
    cannot catch.
    """
    with pytest.raises(jwtkeys.SigningKeyError):
        jwtkeys.SigningKey.from_file(str(tmp_path / "does-not-exist.pem"))


def test_no_hard_coded_secret_identifier_remains_in_source():
    """A guard against the regression coming back as a copy-paste
    default: no module in this package binds an identifier named like
    the cloned service's hard-coded secrets. Checked on code identifiers
    (see :func:`_code_identifiers`), not raw text, so this file's own
    docstrings are free to name the old, removed fields when describing
    the bug, as the module docstring above does.
    """
    suspicious = {"jwt_secret_key", "internal_client_secret"}
    for py_file in _package_root().rglob("*.py"):
        identifiers = {name.lower() for name in _code_identifiers(py_file)}
        found = suspicious & identifiers
        assert not found, f"found {found} as an identifier in {py_file}"

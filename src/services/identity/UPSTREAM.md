# UPSTREAM: evoqidentityservice (X1 identity-service)

## Source

| | |
|---|---|
| Source path | `~/MacWorkspace/EVOQ_Applications/AEO/evoqidentityservice/` |
| Repository | `evoqidentityservice` (its own CodeCommit git repo) |
| Commit | `3390ce93ebb2d092760e86163a15bfcc49dd040b` ("fixed dockerignore issue"); working tree clean |
| Cloned on | 2026-10-04 |

`evoqidentityservice/secrets/` and any `.env*` file in the source tree were never read (CLAUDE.md, this task's instructions).

## Scope decision: a rewrite against `fcrm_common`, not a line-for-line port

Unlike the ingestion/context engines (cloned near-verbatim, "high quality" per CLAUDE.md's AEO table), the identity clone is explicitly "needs the fixes below" and design A §2.6/§8.1 already describe it as "reduced to what the workbench needs" with a near-total swap of its subsystems:

- jose → PyJWT (design A §8.1 fix 5), HS256 → RS256 with a JWKS endpoint (fix 5), OTP flow removed and password login substituted (fix 2), MSAL/SSO routes removed from the build (fix 6), starlette-session/Redis removed (fix 7), `print()` → structured logging (fix 8).
- Every one of those replacements lands on a `fcrm_common` module that already exists and is already tested: `fcrm_common.settings.BaseServiceSettings` (fail-fast secrets), `fcrm_common.errors` (problem+json), `fcrm_common.health`, `fcrm_common.logging` (JSON + redaction), `fcrm_common.tracing`, `fcrm_common.mongo.Repository` (replaces the clone's own `drivers/mongo_repo.py`), and, most importantly, `fcrm_common.auth.TokenVerifier`/`JwksCache` (replaces `interceptors/jwtvalidation.py` for every *consumer* of an X1 token) and `fcrm_common.authz.OPAClient`/`opa_dependency` (replaces `services/.../opasecurity.py`-style checks).

Given that, this clone is **new TrustOps code that reuses the AEO service's domain concepts (password login over seeded users, `/core/validate`, a JWKS endpoint, bcrypt) and ports its three verified bugs into equivalent, testable fixes**, rather than a file-for-file port of `evoqidentityservice/*.py` onto `fcrm_identity/*.py`. This mirrors how the clone's own fixes list (design A §8.1, items 1-10) already describes a near-complete internal rewrite; porting `drivers/mongo_repo.py` or a bespoke `applogs/`/`apptracer/` package here would just reintroduce the generic plumbing `fcrm_common` already provides server-wide, including the bug classes (`apptracer`'s no-op-outside-cluster swap, `drivers/mongo_repo.py`'s own quirks) called out elsewhere in CLAUDE.md's AEO table for other components.

## Pruned entirely (not carried over, and why)

| Clone module | Disposition |
|---|---|
| `routes/msalauth_routes.py`, `services/msalauth_service.py`, `utils/msalclient.py` | SSO/MSAL is out of scope for the three-seeded-roles demo (design A §8.1 fix 6); the real path is documented there for later, not built |
| `routes/developers.py` (`DEVELOPER_USERNAME`/`DEVELOPER_PASSWORD`, defaulted to `admin`/`password123#` in `config.py`) | A second hard-coded-credential backdoor beyond the three named bugs; dropped rather than fixed, since nothing in this workspace needs a developer bypass login |
| `services/customauth_service.py`'s whole OTP flow (`processOTPLogin`, `generate_otp_with_hash`, `processOTPVerification`, `send_otp_email`, the `user_requests` Mongo collection) | Removed per design A §8.1 fix 2; password login replaces it. This is also where the plaintext-OTP-log bug (2) lived — removing the flow is the strongest possible fix, stronger than redacting the one log line |
| `drivers/mongo_repo.py`, `drivers/mongo.py` | Superseded by `fcrm_common.mongo.Repository`/`mongo_client` (async PyMongo, not the clone's `motor`-based driver) |
| `applogs/`, `apptracer/` | Superseded by `fcrm_common.logging.configure_logging` and `fcrm_common.tracing.init_otel` |
| `errorhandlers/` | Superseded by `fcrm_common.errors.install_error_handlers` (RFC 9457 problem+json) |
| Redis / `starlette-session` (`REDIS_ENABLED`) | Removed per design A §8.1 fix 7; nothing here needs server-side session state |
| `signing_keys` Mongo collection (key rotation) | One RS256 keypair, loaded once from `JWT_KEY_FILE` at startup, is enough for this build's lifetime; rotation infra is a day-two concern, tracked below under "Not done in this pass," not an oversight |
| `POST /internal/service-token` (design A §8.1 item 10) | Every service already has `SERVICE_TOKEN_SECRET` and can mint its own HS256 `fcrm_service` token locally via `fcrm_common.auth.mint_service_token` with no network call; a round trip through X1 for the same thing is redundant. Not built in this pass — see "Not done" below |
| `GET /users?role=` (user directory) | No `fcrm_contracts` shape exists for this response yet (`fcrm_contracts.api.identity` has `LoginRequest/Response`, `MeResponse`, `ValidateResponse` only); rather than invent an unreviewed shape, this is left for a follow-up once a contract exists. `ops/opa/data/permissions.json`'s `identity-get-users-...` rule is unused until then |

## The three verified bugs: where they lived, and the fix

### 1. Refresh tokens accepted as access tokens

`customjwt/jwt4evoq.py::validate_jwt_token` decodes with `jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])` and returns the payload — it never inspects `type`. `interceptors/jwtvalidation.py::intercept_for_jwt_token_validation` (backing `/validate` and `/userinfo`) calls exactly that function, so a refresh token (`type: "refresh"`) passes it. Only `intercept_for_jwt_refresh_token_validation` (a *different* dependency, used only by `/refreshaccesstoken`) checks `type`.

Fix: `fcrm_identity.tokens.TokenIssuer.decode` takes `expected_typ` as a **required** keyword argument and raises `UnauthorizedError` on any mismatch — there is no code path that can decode "whatever type it turns out to be" the way the clone's `validate_jwt_token` did. Every other TrustOps service verifies an X1-issued token through `fcrm_common.auth.TokenVerifier`, which independently enforces `typ in {"access", "service"}` (already fixed there, pre-existing; this clone's job was to make sure the tokens *it* mints carry the right claims and that *its own* routes use the same check). `tests/unit/services/identity/test_upstream_fixes.py::test_refresh_token_rejected_as_access_token` logs in, takes the refresh token out of the cookie, and asserts `POST /core/validate`, `GET /core/userinfo` and `GET /me` all 401 when given it as the Bearer credential (and that the real access token is accepted on the same route, so the 401s are the `typ` check and not a broken dependency).

### 2. OTPs logged in plaintext

`services/customauth_service.py::processOTPLogin`:

```python
cls.logger.info(
    f"Email will be sent to {user_email} with OTP {otp} and request_id {request_id}"
)
```

ran on every login attempt, at `INFO`, with the plaintext OTP interpolated directly into the message (not an `extra=` key, so `fcrm_common.logging`'s `redact()` filter — which only drops `extra` keys matching `password|token|secret|authorization` — would not have caught it even if this code were kept as-is: the regex matches *keys*, not message text).

Fix: the OTP flow does not exist in this clone (design A §8.1 fix 2; see "Pruned entirely" above), so there is no OTP value anywhere to log. The password that replaces it is passed through local variables only (`fcrm_identity.security.verify_password`, `routes.login`) and is never interpolated into a log message or passed under an `extra=` key. `test_upstream_fixes.py::test_otp_never_appears_in_logs` captures every log record from a correct, an incorrect and an unknown-user login attempt and asserts the plaintext password never appears in any record's message or `extra` values, *and* walks every module's AST asserting no identifier contains `otp` (so the flow cannot be quietly reintroduced without the test catching it — a plain substring scan over file text was tried first and rejected: this file's own docstrings and `UPSTREAM.md` need to describe the old bug by name).

### 3. Hard-coded secret fallbacks

`config/config.py`:

```python
JWT_SECRET_KEY: str = os.getenv(
    "JWT_SECRET_KEY",
    "2525093d4514b907b7cfcce226794d811df54afacbe7ebea56e1564dba637e8ef83458a29e05e3fd0e58259e0444835d",
)
...
INTERNAL_CLIENT_SECRET: str = os.getenv("INTERNAL_CLIENT_SECRET", "internalsecret123#")
```

Both a real signing secret and an internal-service shared secret have baked-in defaults checked into source control, used silently whenever the operator forgot to set the environment variable (and `DEVELOPER_PASSWORD` defaults to `'password123#'` the same way — see "Pruned entirely").

Fix: `fcrm_identity.settings.IdentitySettings` adds `JWT_KEY_FILE` and `DEMO_USERS_PASSWORD_HASH` to `required_settings` with **no default value at all**; `IdentitySettings.load()` (`fcrm_common.settings.BaseServiceSettings.fail_fast`) raises `MissingSettingsError` naming every missing variable before Mongo is even touched. A *present but bad* `JWT_KEY_FILE` (missing file, non-RSA key) is caught separately by `fcrm_identity.jwtkeys.SigningKey.from_file`, which `lifespan.py` lets propagate uncaught — the process never finishes starting up on either kind of failure. `test_upstream_fixes.py::test_missing_secret_fails_fast_at_startup` removes each variable in turn and asserts `MissingSettingsError` names exactly it; `test_signing_key_file_must_exist_and_parse_no_silent_fallback` covers the "present but unusable" half; `test_no_hard_coded_secret_identifier_remains_in_source` is a textual guard (on code identifiers, not docstrings) against either secret's old name reappearing as a default.

## Judgement calls and known gaps

- **Login identifier.** `fcrm_contracts.api.identity.LoginRequest.email` has no email data behind it in `fcrm_seed/seed.json`'s `users` section (`{id, name, role, unit?}` only — no `demo_password`, despite design A §2.6/§8.1 describing one). `fcrm_identity.store.login_candidates` accepts either the seed id (`u-riyer`) or that id with the `u-` prefix stripped (`riyer`, matching `src/web/src/mocks/users.ts`'s derivation), case-insensitively, into the one `email` field. This is a pragmatic reading of an underspecified field, not a contract change.
- **`src/web`'s mock identity contract does not match `fcrm_contracts.api.identity`.** `src/web/src/mocks/handlers/identity.ts` expects `POST /login {username, password}` → `{access_token, token_type, expires_in, user}`; the committed `fcrm_contracts.api.identity.LoginRequest/LoginResponse` is `{email, password}` → `{access_token, expires_in}` (no `token_type`, no `user`, no `username` field at all). This service implements the committed `fcrm_contracts` shapes exactly ("contracts first": no lane defines a local copy) — the web lane's hand-written MSW types predate `make api-gen` and are flagged in `src/web/CLAUDE.md` itself as due to be swapped for generated types. Reconciling the two is a cross-lane contract decision outside this task's scope; flagged here rather than resolved unilaterally.
- **Key rotation (`signing_keys` collection) not built.** One keypair loaded from `JWT_KEY_FILE` at startup; `kid` is derived deterministically from the public key (SHA-256 of its DER encoding, truncated), so it is stable across restarts without a database round trip. Rotating keys means restarting the container with a new `JWT_KEY_FILE` and accepting a short window where already-issued tokens stop verifying — acceptable for the demo's lifetime, not for production.
- **`POST /internal/service-token` not built** (see "Pruned entirely" — redundant with `fcrm_common.auth.mint_service_token`, which every service already has via `SERVICE_TOKEN_SECRET`).
- **`GET /users?role=` (directory) not built** (see "Pruned entirely" — no contract shape exists yet).
- **No live Mongo integration test.** `MongoIdentityStore` is exercised by construction and by its method bodies' direct mapping to `Repository` calls (mirrored by `InMemoryIdentityStore` passing the identical `IdentityStore` protocol through every route test), the same split `fcrm_scoring` uses between `test_routes.py` (in-memory) and `test_mongo_live.py` (testcontainers, `@pytest.mark.integration`); no `test_mongo_live.py`-equivalent was added here for lack of time, so the Mongo-backed path (`MongoIdentityStore`, the TTL index on `refresh_tokens.exp`, the seed-loading path in `lifespan.py`) is type-checked and read-reviewed but not run against a real `mongod` in this pass.
- **No live JWKS-consumer integration test.** `fcrm_common.auth.JwksCache`/`TokenVerifier` is exercised here with `httpx.MockTransport` (the same technique `tests/unit/packages/common/test_common_auth.py` uses), proving the JWKS *body* this service serves is exactly what `JwksCache._fetch`'s `jwt.PyJWKSet.from_dict` call expects (`test_jwks.py`). No other service in this workspace calls X1 over real HTTP yet (none has a live Mongo+identity compose integration test as of this pass), so the end-to-end "another container's `JwksCache` fetches X1's live `/.well-known/jwks.json` over the network" path is unverified beyond this shape check.
- **D-13 (`fcrm_common.asgi.mount_at_root_path`).** This module did not exist anywhere in the workspace when this task started; it was added (`src/packages/common/fcrm_common/asgi.py`, with its own test `tests/unit/packages/common/test_common_asgi.py`) because CLAUDE.md's root file was updated mid-task to require it and name `fcrm_scoring/main.py` as "the reference implementation" — which, as of this pass, does **not** yet use it. `fcrm_identity/main.py` wires it correctly (`app = mount_at_root_path(create_app())`); reconciling `fcrm_scoring/main.py` (and any other already-built service's `main.py`) with the same pattern is outside this task's assigned scope and was left alone.

## After

`uv run --project ../.. --package fcrm-identity-service pytest ../../../tests/unit/services/identity` → see verification output in the task report (run from `src/services/identity/`). `ruff check`, `ruff format --check` and `mypy --config-file ../../../mypy.ini fcrm_identity` all green as of this pass.

# Secrets, keys and AWS credentials

Never commit `.env` or `jwt_keys/`. Never read either into a chat, a log or a ticket. `.gitignore` excludes `.env` and `.env.*` (except `.env.example`); `jwt_keys/` excludes itself, because `make keys` writes a `.gitignore` containing `*` inside it.

## What lives where

| Item | Where | How it is made | Who uses it |
|---|---|---|---|
| Local service passwords (Mongo root, app, audit writer, evals reader; RabbitMQ; MinIO; OpenObserve root login) | `.env`, mode 0600 | `make env` writes random hex values once and never overwrites the file | compose interpolation; each container gets only its own (no blanket `env_file`) |
| `SERVICE_TOKEN_SECRET` | `.env` | `make env`, random hex | identity mints HMAC `fcrm_service` tokens for H1, H2 and S5; services verify them |
| `DEMO_USERS_PASSWORD_HASH` | `.env` | `make env`: a bcrypt hash of `DEMO_PASSWORD` (default `demo-password-2026`), written with `$$` so compose does not expand it | identity: every seeded user shares this hash |
| JWT signing keypair | `jwt_keys/jwt.pem` and `jwt.pub.pem`, both 0644 in a 0755 folder | `make keys` (RS256, 2048 bits) | `api` reads `/keys/jwt.pem` read-only as its own user (uid 10001), so the key must be readable by others on the host: Docker on Linux and in WSL 2 keeps host permissions, and a 0600 key stops the api at start. A local key for synthetic data only; a deployment takes it from a secret store (`../aws/README.md`) |
| AWS credentials | `.env` section 1, or your shell | you | `api` (embeddings) and `harness` (agents) get `AWS_DEFAULT_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`; no other container gets them |

Containers see different Mongo users. `fcrm_app` (every service and worker) has readWrite on the six non-audit databases and nothing on `fcrm_audit`. `fcrm_audit_writer` (S4 only, inside `api`) can `find` and `insert` on the append-only audit collections and nothing else. `fcrm_evals_reader` (S4) can `find` on `eval_runs` and `eval_dashboards` (`ops/mongo/init/02-users.js`). The `api` container holds all three URIs, because identity, S1-S5 share one process (ADR-26); the `harness` holds only `fcrm_app`.

## AWS credentials

Add them without printing them. Either edit the three lines in section 1 of `.env` in your editor, or export them in the shell that runs `make up` (compose reads the shell before `.env`, so nothing is written to disk):

```bash
nano .env                                      # or any editor: fill AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY; AWS_DEFAULT_REGION is pre-filled
# or, for one shell session (bash and zsh):
printf 'AWS_ACCESS_KEY_ID: ';     read -rs AWS_ACCESS_KEY_ID;     echo; export AWS_ACCESS_KEY_ID
printf 'AWS_SECRET_ACCESS_KEY: '; read -rs AWS_SECRET_ACCESS_KEY; echo; export AWS_SECRET_ACCESS_KEY
```

- Temporary (SSO or assumed-role) credentials also need `AWS_SESSION_TOKEN`; uncomment that line in `.env`. They expire; when they do, agent runs fail at the model call until you replace them and run `docker compose up -d` (a changed value recreates the container).
- An `AWS_PROFILE` is not passed into the containers. Put explicit keys in `.env`, or export them as above.
- `make evals` and `ops/scripts/demo_case_tokens.py` run on the host and use the standard AWS chain of your shell, `AWS_PROFILE` included.
- The IAM permissions the stack needs are in [../aws/README.md](../aws/README.md). On a laptop an access key is the only option; on AWS use roles.

## Rotate the JWT key

```bash
make rotate-keys          # make keys FORCE=1, then docker compose restart api
```

There is one keypair, loaded at start, with a `kid` derived from the public key (`fcrm_identity/jwtkeys.py`). After a rotation:

- every access token (15 minutes) and refresh token (8 hours) signed with the old key stops verifying, because the old `kid` is gone from JWKS. Every user signs in again;
- services cache JWKS for 10 minutes and refetch once on an unknown `kid`, so the cache clears itself;
- service-to-service calls are unaffected: they use the HMAC `SERVICE_TOKEN_SECRET`, not this key;
- `api` takes about 85 seconds to become healthy again.

There is no overlap window for two keys; rotate when you can ask people to sign in again. Rotating `SERVICE_TOKEN_SECRET` means editing `.env` and recreating `api` and `harness` together (`docker compose up -d`).

## Rotate a Mongo, RabbitMQ or MinIO password

The databases are created with the passwords of the first start. `ops/mongo/init/02-users.js` re-applies the `fcrm_app`, `fcrm_audit_writer` and `fcrm_evals_reader` passwords from the environment on every `mongo` start, so for those three: edit `.env`, then `docker compose up -d` and wait for healthy. The Mongo root password, RabbitMQ and MinIO credentials are fixed by their first start: changing them means `make down-v`, which deletes all local data. None of these paths was exercised by the lane that wrote this page.

## Rules

- `.env` is created with mode 0600 by `make env`. Do not widen it.
- `.env.example` has names, never values. `make compose-check` (part of `make check`) fails when it and the compose files drift.
- The demo password is a local convenience. The demo picker and password-less login work only while `ENV` is `local`, `test` or `ci` and `DEMO_USERS_ENABLED=true`; compose sets the second to true and the first defaults to `local`. Set `ENV=prod` anywhere that is not a laptop (`make seed` and `make demo-reset` refuse to run there).
- The commit hooks refuse a Claude Code `Bash` or edit that reads `.env*` or `AEO/secrets` (`ai/guidance/hooks/protect_paths.py`), when the hook is registered in the operator's `.claude/settings.json`.

## Checked and not checked

- Run: `make keys` twice (the first writes `jwt_keys/jwt.pem` at mode 0644; the second keeps the key and only resets its permissions; `git status` does not list `jwt_keys/`), and `make -n rotate-keys`. `tests/unit/ops/test_makefile_targets.py` runs the recipe on a scratch folder, including the repair of a 0600 key.
- On 8 October 2026 a fresh start on Docker inside WSL 2 failed with the api unhealthy: the earlier recipe wrote the key at 0600, which the container user could not read. Docker Desktop on macOS had hidden the difference.
- Checked against code and config: the file names and modes (`ops/scripts/make_env.py`, the `keys` target), the per-container variable lists (`docker-compose.yaml`), the Mongo roles (`02-users.js`), the single-keypair design and JWKS cache (`jwtkeys.py`, `fcrm_common/auth.py`).
- The `read -rs` lines are written for bash and zsh alike and were not run.
- Needs the live stack: that every session ends after `make rotate-keys` (read from token verification, not tested), and the password-rotation paths.

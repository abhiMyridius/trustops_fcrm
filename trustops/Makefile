# TrustOps · root Makefile (design A §11.6, design C §3.3/§4.2, BUILD-PLAN §7).
#
# Works with GNU make 3.81 and later (3.81 is the macOS system make; Linux and
# WSL 2 ship 4.x) and bash. `make help` lists every
# target. Targets that depend on a lane that has not landed yet still exist
# and fail loudly with "not yet wired — pending <lane>", so the whole
# intended surface is visible and nothing silently no-ops.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# uv and opa are installed user-space into ~/.local/bin (BUILD-PLAN §11).
# make 3.81 execs simple commands with its own PATH, so tools are resolved
# to absolute paths here as well as exported for recipe shells.
export PATH := $(HOME)/.local/bin:$(PATH)
WHICH = $(shell PATH="$(HOME)/.local/bin:$$PATH" command -v $(1) 2>/dev/null)

UV        ?= $(or $(call WHICH,uv),uv)
UV_RUN    := $(UV) run --project src/backend --all-packages
PYTHON    ?= python3

# One compose file; optional services are profiles (obs, cache) and host ports
# for the infrastructure live in an overlay (compose.debug.yaml).
COMPOSE       := docker compose -f docker-compose.yaml
COMPOSE_OBS   := $(COMPOSE) --profile obs
COMPOSE_CACHE := $(COMPOSE) --profile cache
# Every profile on, for the commands that must see optional containers too.
COMPOSE_ALL   := $(COMPOSE) --profile obs --profile cache
COMPOSE_DEV   := $(COMPOSE) -f compose.dev.yaml
COMPOSE_DEBUG := $(COMPOSE) -f compose.debug.yaml
COMPOSE_TEST  := docker compose -p trustops-test -f docker-compose.yaml -f compose.test.yaml

# The default stack: infrastructure, the two Python processes, the edge.
INFRA := mongo rabbitmq minio opa
APPS  := api harness
EDGE  := gateway web
OBS   := otel-collector openobserve prometheus grafana

# OPA: a local binary when present (fast), else the pinned image.
OPA_VERSION := 1.15.2
OPA_IMAGE   := openpolicyagent/opa:$(OPA_VERSION)
OPA_BIN     ?= $(call WHICH,opa)
ifeq ($(OPA_BIN),)
OPA := docker run --rm -v "$(CURDIR)/ops/opa:/opa:ro" -w / $(OPA_IMAGE)
OPA_DIR := /opa
else
OPA := $(OPA_BIN)
OPA_DIR := ops/opa
endif

# Every Python import package in the workspace (fcrm_* and the evoq_* clones).
PY_PACKAGES := $(sort $(foreach d,$(wildcard src/backend/services/*/fcrm_* src/backend/workers/*/fcrm_* src/backend/workers/*/evoq_* src/backend/packages/*/fcrm_* src/backend/apps/*/fcrm_*),$(if $(wildcard $(d)/__init__.py),$(notdir $(d)))))
MYPY_ARGS   := $(foreach p,$(PY_PACKAGES),-p $(p))
LINT_PATHS  := src/backend ops/scripts tests

# Pending-lane helper: prints to stderr and fails (exit 1).
pending = printf '%s\n' "make $@: not yet wired — pending $(1)" >&2; exit 1

WAIT := $(PYTHON) ops/scripts/wait_for_ready.py

.PHONY: help
help: ## list targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_.-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ── setup ────────────────────────────────────────────────────────────────
.PHONY: bootstrap tools env keys pull
bootstrap: tools ## uv sync the workspace, install git hooks, pull images
	@if [ -f src/backend/pyproject.toml ]; then $(UV) sync --project src/backend --all-packages; else echo "bootstrap: no src/backend/pyproject.toml, skipping uv sync"; fi
	@if [ -f .pre-commit-config.yaml ] && git rev-parse --git-dir >/dev/null 2>&1 && [ -z "$(SKIP_HOOKS)" ]; then \
		$(UV) run --project src/backend pre-commit install -c .pre-commit-config.yaml --hook-type pre-commit --hook-type commit-msg; \
	else echo "bootstrap: skipping pre-commit install"; fi
	@$(MAKE) --no-print-directory pull

tools: ## install the pinned opa into ~/.local/bin if missing (uv is a prerequisite)
	@command -v $(UV) >/dev/null || { echo "tools: uv not found; install it into ~/.local/bin first (https://docs.astral.sh/uv/)" >&2; exit 1; }
	@if command -v opa >/dev/null; then echo "tools: opa $$(opa version | head -1 | cut -d' ' -f2) present"; else \
		os=$$(uname -s | tr A-Z a-z); arch=$$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/'); \
		url=https://github.com/open-policy-agent/opa/releases/download/v$(OPA_VERSION)/opa_$${os}_$${arch}_static; \
		mkdir -p $(HOME)/.local/bin && curl -fsSL -o $(HOME)/.local/bin/opa.tmp "$$url" \
		&& sha=$$(command -v sha256sum || echo 'shasum -a 256') \
		&& curl -fsSL "$$url.sha256" | awk '{print $$1"  $(HOME)/.local/bin/opa.tmp"}' | $$sha -c - \
		&& chmod +x $(HOME)/.local/bin/opa.tmp && mv $(HOME)/.local/bin/opa.tmp $(HOME)/.local/bin/opa \
		&& echo "tools: installed opa $(OPA_VERSION) to ~/.local/bin"; fi

DEMO_PASSWORD ?= demo-password-2026
env: ## create .env from .env.example with generated local secrets (never overwrites)
	@if [ -f .env ]; then echo ".env already exists; leaving it untouched"; else \
		hash=$$($(UV_RUN) python -c 'import bcrypt,sys;print(bcrypt.hashpw(sys.argv[1].encode(),bcrypt.gensalt(12)).decode())' "$(DEMO_PASSWORD)") \
		&& $(PYTHON) ops/scripts/make_env.py --example .env.example --out .env --set "DEMO_USERS_PASSWORD_HASH=$$hash" \
		&& echo "env: seeded users log in with the demo password '$(DEMO_PASSWORD)' (override with DEMO_PASSWORD=...)"; fi

# The api container runs as its own user (uid 10001, src/backend/Dockerfile),
# not as you. Docker Desktop on macOS hides that difference on a bind mount;
# Docker on Linux and in WSL 2 does not, and a 0600 key stops the api at start
# ("Permission denied: '/keys/jwt.pem'"). So the dev key is 0644 in a 0755
# folder, and an existing key is brought to that too.
# It is a local signing key for synthetic data; a deployment takes the key
# from a secret store (ops/aws/README.md), never from this folder.
keys: ## generate the dev RS256 JWT keypair into jwt_keys/ (FORCE=1 to rotate); readable by the api container
	@if [ -f jwt_keys/jwt.pem ] && [ -z "$(FORCE)" ]; then \
		chmod 755 jwt_keys && chmod 644 jwt_keys/*.pem \
		&& echo "keys: jwt_keys/jwt.pem exists (FORCE=1 to rotate); permissions set so the api container can read it"; else \
		mkdir -p jwt_keys && chmod 755 jwt_keys && printf '*\n' > jwt_keys/.gitignore \
		&& openssl genrsa -out jwt_keys/jwt.pem 2048 2>/dev/null \
		&& openssl rsa -in jwt_keys/jwt.pem -pubout -out jwt_keys/jwt.pub.pem 2>/dev/null \
		&& chmod 644 jwt_keys/jwt.pem jwt_keys/jwt.pub.pem \
		&& echo "keys: wrote jwt_keys/jwt.pem and jwt_keys/jwt.pub.pem (0644, so the api container can read them); the folder ignores itself in git"; fi

pull: ## pull the pinned registry images (mongo, rabbitmq, minio, opa, gateway); api, harness and web are built here, never pulled
	@[ -f .env ] || $(MAKE) --no-print-directory env
	$(COMPOSE) pull --quiet $(INFRA) gateway

# ── run ──────────────────────────────────────────────────────────────────
.PHONY: up-infra up up-obs obs-env up-cache up-debug up-dev down down-v reset ps logs
up-infra: env ## infrastructure only (mongo, rabbitmq, minio, opa)
	$(COMPOSE) up -d $(INFRA)
	@$(WAIT) --compose "$(COMPOSE)" $(INFRA)

up: env keys ## the default stack, 8 containers (mongo rabbitmq minio opa api harness gateway web)
	@missing=""; for f in src/backend/Dockerfile src/frontend/Dockerfile; do \
		[ -f "$$f" ] || missing="$$missing $$f"; done; \
	if [ -n "$$missing" ]; then echo "up: not buildable, missing:$$missing" >&2; exit 1; fi
	$(COMPOSE) up -d --force-recreate --no-deps opa
	$(COMPOSE) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE)" $(INFRA) $(APPS) $(EDGE)

obs-env: ## fail early, with the fix, on an .env that would start the observability stack wrongly (no OpenObserve login, a password compose would cut, a half-set team instance)
	@$(PYTHON) ops/scripts/check_obs_env.py --env-file .env

up-obs: env keys obs-env ## the default stack plus otel-collector, openobserve, prometheus and grafana (12 containers); traces, metrics and logs go to OpenObserve
	$(COMPOSE_OBS) up -d --force-recreate --no-deps opa
	OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 $(COMPOSE_OBS) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE_OBS)" $(INFRA) $(OBS) $(APPS) $(EDGE) \
		--url otel-collector=http://$$($(COMPOSE_OBS) port otel-collector 13133)/ \
		--url openobserve=http://$$($(COMPOSE_OBS) port openobserve 5080)/healthz

up-cache: env keys ## the default stack plus Redis for SSE fan-out (api uses it via REDIS_URL)
	$(COMPOSE_CACHE) up -d --force-recreate --no-deps opa
	REDIS_URL=redis://redis:6379/0 $(COMPOSE_CACHE) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE_CACHE)" $(INFRA) redis $(APPS) $(EDGE)

up-debug: env keys ## the default stack with infrastructure and harness health ports published to the host
	$(COMPOSE_DEBUG) up -d --force-recreate --no-deps opa
	$(COMPOSE_DEBUG) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE_DEBUG)" $(INFRA) $(APPS) $(EDGE)

up-dev: env keys ## default stack with bind-mounted source, uvicorn --reload and vite dev
	$(COMPOSE_DEV) up -d --force-recreate --no-deps opa
	$(COMPOSE_DEV) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE_DEV)" $(INFRA) $(APPS) $(EDGE)

down: ## stop the stack (every profile), keep volumes
	$(COMPOSE_ALL) down --remove-orphans

down-v: ## stop the stack (every profile) and delete its volumes (all local data)
	$(COMPOSE_ALL) down -v --remove-orphans

reset: down-v up ## wipe everything and start again

ps: ## container status and health
	$(COMPOSE_ALL) ps -a

logs: ## follow logs: make logs svc=api (all services when svc is empty)
	$(COMPOSE_ALL) logs -f --tail=200 $(svc)

# ── seed and demo ────────────────────────────────────────────────────────
.PHONY: gen-seed seed seed-verify demo demo-reset verify
gen-seed: ## regenerate seed.json and assert it is byte-stable (P7, ADR-16)
	cd src/backend/packages/seed && $(UV) run --project ../.. --package fcrm-seed python -m fcrm_seed.gen_seed

seed: ## force-reload the seed into every fcrm_* database and stage the demo PDFs (refuses ENV=prod)
	$(PYTHON) ops/scripts/seed_reset.py --all --compose "$(COMPOSE)"
	$(COMPOSE) restart api harness
	@$(WAIT) --timeout 300 --compose "$(COMPOSE)" $(APPS)
	$(PYTHON) ops/scripts/seed_reset.py --stage-fixtures --compose "$(COMPOSE)"

seed-verify: ## the live stack holds the seed: 80 cases, the stage distribution, the demo case untouched, a chain that verifies
	$(UV_RUN) python ops/scripts/seed_verify.py

# `make demo` drives the whole case through the live stack (real Bedrock agents,
# about 8 to 12 minutes) and prints the live numbers; the figures are whatever
# the agents propose, the 21 scenes assert the structure (tests/e2e/test_demo_0151.py).
demo: demo-reset ## drive FCRM-2026-0151 end to end through the live stack in 21 scenes and print the live numbers
	$(UV_RUN) pytest tests/e2e/test_demo_0151.py -m e2e -o log_cli=true --log-cli-level=INFO

demo-reset: ## delete case 0151's documents and uploads so the demo can run again, keep the other cases (refuses ENV=prod)
	$(PYTHON) ops/scripts/seed_reset.py --demo-case --compose "$(COMPOSE)"

verify: ## verify one case's audit chain through S4: make verify case=FCRM-2026-0118
	@[ -n "$(case)" ] || { echo "usage: make verify case=<case id>, e.g. case=FCRM-2026-0118" >&2; exit 2; }
	$(UV_RUN) python ops/scripts/seed_verify.py --case "$(case)"

# ── quality gates ────────────────────────────────────────────────────────
.PHONY: check lint format-check typecheck test test-unit opa-check opa-test ai-check compose-check web-install web-check gate
check: lint format-check typecheck opa-check opa-test ai-check test-unit compose-check web-check ## commit gate: ruff, format, mypy, OPA, ai/ registry and model-id guards, unit tests, env contract, web typecheck and vitest
	@echo "check: green"

lint: ## ruff over src/backend, ops/scripts and tests (what the commit hook lints)
	$(UV_RUN) ruff check $(LINT_PATHS)

format-check: ## ruff format --check over src/backend, ops/scripts and tests
	$(UV_RUN) ruff format --check $(LINT_PATHS)

typecheck: ## mypy over every workspace package (strictness per mypy.ini) and, --strict, the drill helpers and the DLQ tool
	@if [ -z "$(MYPY_ARGS)" ]; then echo "typecheck: no packages"; else $(UV_RUN) mypy $(MYPY_ARGS); fi
	MYPYPATH=ops/scripts:src/backend $(UV_RUN) mypy --strict --explicit-package-bases -p resilience -m dlq

opa-check: ## opa check --strict on the policies and tests
	$(OPA) check --strict $(OPA_DIR)/policy $(OPA_DIR)/tests

opa-test: ## Rego unit tests against the real permissions/tool_permits data (30 s per test: the route-matrix tests are slow on a busy machine)
	$(OPA) test --timeout 30s $(OPA_DIR)/policy $(OPA_DIR)/data $(OPA_DIR)/tests

ai-check: ## ai/ registry (prompts, agents, tools, skills), the model-id rule over ai/ src/ evals/ ops/, and prompt-lock freshness
	$(UV_RUN) python ai/_tools/validate_ai.py
	$(UV_RUN) python ai/_tools/compute_locks.py --check

test-unit: ## every unit test under tests/ (integration and e2e markers excluded)
	$(UV_RUN) pytest -m "not integration and not e2e and not resilience"

test: test-unit ## alias of test-unit

compose-check: ## .env.example ⇄ compose files contract + `compose config` of base/dev/test
	$(PYTHON) ops/scripts/check_env_contract.py

web-install: ## install the web dependencies from the lockfile (needed once per checkout)
	cd src/frontend && npm ci

web-check: ## web typecheck and vitest; skipped, loudly, when node_modules is missing
	@if [ -d src/frontend/node_modules ]; then \
		cd src/frontend && npm run --silent typecheck && npm test --silent; \
	else echo "web-check: SKIPPED, src/frontend/node_modules is missing (run 'make web-install')"; fi

gate: check ## push / task-done gate: check plus the parts other lanes have wired
	@if [ -d tests/integration ]; then $(MAKE) --no-print-directory test-int; else echo "gate: no tests/integration yet (skipped)"; fi
	$(UV_RUN) python -m fcrm_evals build --check
	$(UV_RUN) python -m fcrm_evals selfcheck --out "$$(mktemp -d)"
	@echo "gate: pending pieces, not yet run: contracts (oasdiff, event schemas), semgrep; evals-gate needs a live run (make evals)"

# ── integration, e2e, resilience, evals ──────────────────────────────────
.PHONY: test-int test-integration e2e resilience evals evals-full evals-gate nightly
test-int: ## every integration-marked test under tests/ (tests/integration on Docker or FCRM_TEST_MONGO_URI; the *_live.py tests under tests/unit need FCRM_TEST_MONGO_URI)
	@if [ -z "$$FCRM_TEST_MONGO_URI" ]; then \
		echo "test-int: FCRM_TEST_MONGO_URI is not set. tests/integration starts its own containers (needs Docker); the *_live.py tests under tests/unit will SKIP, not pass." >&2; \
		echo "test-int: to run all of them, start a throwaway replica set and export FCRM_TEST_MONGO_URI (RUNNING.md, 'Live tests')." >&2; \
	fi
	$(UV_RUN) pytest -m integration tests

test-integration: test-int ## alias of test-int

e2e: demo ## the demo, then the live black-box rules (BB-03/06/08/09/13/14), then the Playwright live scenes when the web is installed
	$(UV_RUN) pytest tests/e2e/test_bb_live.py -m e2e -o log_cli=true --log-cli-level=INFO
	@if [ -d src/frontend/node_modules ]; then cd src/frontend && npm run --silent test:e2e:live; \
	else echo "e2e: SKIPPED the Playwright live scenes, src/frontend/node_modules is missing (run 'make web-install')"; fi

# The drills act on the RUNNING stack (make up first; no one else may be using
# it). By default only the drills that restart nothing and call no AWS service
# run (D5a, D5b, D6, D6b, D8: they raise scratch cases, publish to the broker and
# read). DISRUPTIVE=1 adds the ones that stop, kill or restart containers (D1, D2,
# D3, D4, D7); AWS=1 adds those that make live Bedrock calls or restart the api,
# which embeds the policy clauses on Bedrock at start (D1, D4, D7) and needs
# DISRUPTIVE=1 as well. Nothing here is run without the person typing the switch.
# They address containers by name (trustops-<service>-1), talk to the gateway on
# :8080 as the demo personas and never read .env. FCRM_RESILIENCE_STRICT=1 makes an
# unreachable stack a failure, not a skip. Estimated 25 minutes with everything on.
# Docs: docs/architecture/resilience.md. One drill: make resilience k=d3
# (ids d1a d1b d1c d2 d3 d4 d5a d5b d6 d6b d7 d8).
resilience: ## kill/restart drills against the running stack (harness, RabbitMQ, Mongo, api/identity, SSE, DLQ replay, handoff); PASS/FAIL table with seconds
	FCRM_RESILIENCE_STRICT=1 $(if $(DISRUPTIVE),FCRM_RESILIENCE_DISRUPTIVE=1) $(if $(AWS),FCRM_RESILIENCE_AWS=1) $(UV_RUN) pytest -m resilience tests/resilience -p no:cacheprovider $(if $(k),-k "$(k)")

# The X6 evals (src/backend/packages/evals; evals/README.md). `evals` calls Bedrock
# with the AWS credentials in the shell's environment (about USD 4 and 10 minutes);
# EVALS_ARGS passes more options, e.g. EVALS_ARGS="--publish --baseline" or
# EVALS_ARGS="--judge fake" (offline stand-in judge, labelled as such).
EVALS := $(UV_RUN) python -m fcrm_evals

evals: ## live agent evals on Bedrock over a sampled slice (5 rows per agent, seed 7, concurrency 3); AGENT=<a> for one; writes evals/results/<date>/ and dashboard.html
	$(EVALS) run --producer live --sample 5 --seed 7 --judge bedrock --concurrency 3 $(if $(AGENT),--agent $(AGENT)) $(EVALS_ARGS)

evals-full: ## live agent evals over every row of every dataset (slow, several times the cost of `make evals`)
	$(EVALS) run --producer live --sample 0 --judge bedrock --concurrency 3 $(if $(AGENT),--agent $(AGENT)) $(EVALS_ARGS)

evals-gate: ## eval promotion gate for one agent on the newest run: make evals-gate AGENT=research
	@[ -n "$(AGENT)" ] || { echo "usage: make evals-gate AGENT=<agent>, e.g. AGENT=research" >&2; exit 2; }
	$(EVALS) gate --agent $(AGENT)

nightly: ## gate · test-int · e2e · resilience · evals · token-report → ops/reports/nightly/<date>.md
	@$(call pending,e2e/resilience/evals lanes and ops/scripts/nightly.sh)

# ── contracts and generated code ─────────────────────────────────────────
.PHONY: contracts api-gen locks
contracts: ## export OpenAPI per service and event JSON Schemas; must leave a clean diff
	@$(call pending,the contracts lane (stub apps in fcrm_contracts.stubs → docs/architecture/openapi and events))

api-gen: ## regenerate src/frontend/src/api/generated from the OpenAPI exports
	@$(call pending,the web-shell lane)

locks: ## regenerate ai/registry.lock after a change under /ai
	@$(call pending,the prompts-loader lane (P4))

.PHONY: openapi
openapi: ## export the api process's merged OpenAPI (every service under its prefix) to docs/architecture/openapi/api.json
	$(UV_RUN) python ops/scripts/export_openapi.py

# ── operations ───────────────────────────────────────────────────────────
.PHONY: dlq dlq-inspect dlq-replay dlq-purge token-report build-usage backup restore rotate-keys
# The DLQ tool (ops/scripts/dlq.py) needs only the docker CLI and the stack's
# container names (trustops-<service>-1): no .env and no `docker compose`. A
# replayed message gets its retry budget back (x-attempt reset), which the
# shovel this replaced did not do, so a replay of a failing message parked at
# once. DLQ_PROJECT=<name> if the compose project is not "trustops".
DLQ_PROJECT ?= trustops
DLQ := $(PYTHON) ops/scripts/dlq.py --project $(DLQ_PROJECT)

dlq: ## list dead-letter queues with their depth
	@$(DLQ) list

dlq-inspect: ## show the parked messages of <q>.dlq: make dlq-inspect q=caseflow.steps
	@[ -n "$(q)" ] || { echo "usage: make dlq-inspect q=<work queue>, e.g. q=caseflow.steps" >&2; exit 2; }
	@$(DLQ) inspect "$(q)"

dlq-replay: ## move messages of <q>.dlq back onto <q> with a fresh retry budget: make dlq-replay q=caseflow.steps [id=<message id>]
	@[ -n "$(q)" ] || { echo "usage: make dlq-replay q=<work queue>, e.g. q=caseflow.steps [id=<message id>]" >&2; exit 2; }
	@$(DLQ) replay "$(q)" $(if $(id),--message-id "$(id)")

dlq-purge: ## delete the parked messages of <q>.dlq (CONFIRM=1 to really do it): make dlq-purge q=caseflow.steps CONFIRM=1
	@[ -n "$(q)" ] || { echo "usage: make dlq-purge q=<work queue> CONFIRM=1 [id=<message id>]" >&2; exit 2; }
	@$(DLQ) purge "$(q)" $(if $(id),--message-id "$(id)") $(if $(CONFIRM),--confirm)

token-report: ## (needs `make up-obs`) tokens and cost per agent, model and case from Prometheus → ops/token-analysis/runtime/
	$(UV_RUN) python ops/scripts/token_report.py --prometheus http://$$($(COMPOSE_OBS) port prometheus 9090)

build-usage: ## build-time Claude Code token analysis → ops/token-analysis/build/
	@$(call pending,ops/scripts/claude_usage.py (SDLC harness lane))

backup: ## mongodump every fcrm_* database and mirror MinIO into ops/backups/<ts>/
	@$(call pending,ops/scripts/backup.sh (ops-docs lane))

restore: ## restore a backup: make restore TS=<timestamp>
	@$(call pending,ops/scripts/restore.sh (ops-docs lane))

rotate-keys: ## rotate the JWT signing key (make keys FORCE=1) and restart the api (identity runs inside it)
	$(MAKE) --no-print-directory keys FORCE=1
	$(COMPOSE) restart api

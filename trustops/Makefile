# TrustOps · root Makefile (design A §11.6, design C §3.3/§4.2, BUILD-PLAN §7).
#
# Works with the macOS system make (GNU make 3.81). `make help` lists every
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
OBS   := otel-collector jaeger prometheus grafana

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
LINT_PATHS  := src/backend ops/scripts

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
		&& curl -fsSL "$$url.sha256" | awk '{print $$1"  $(HOME)/.local/bin/opa.tmp"}' | shasum -a 256 -c - \
		&& chmod +x $(HOME)/.local/bin/opa.tmp && mv $(HOME)/.local/bin/opa.tmp $(HOME)/.local/bin/opa \
		&& echo "tools: installed opa $(OPA_VERSION) to ~/.local/bin"; fi

DEMO_PASSWORD ?= demo-password-2026
env: ## create .env from .env.example with generated local secrets (never overwrites)
	@if [ -f .env ]; then echo ".env already exists; leaving it untouched"; else \
		hash=$$($(UV_RUN) python -c 'import bcrypt,sys;print(bcrypt.hashpw(sys.argv[1].encode(),bcrypt.gensalt(12)).decode())' "$(DEMO_PASSWORD)") \
		&& $(PYTHON) ops/scripts/make_env.py --example .env.example --out .env --set "DEMO_USERS_PASSWORD_HASH=$$hash" \
		&& echo "env: seeded users log in with the demo password '$(DEMO_PASSWORD)' (override with DEMO_PASSWORD=...)"; fi

keys: ## generate the dev RS256 JWT keypair into jwt_keys/ (FORCE=1 to rotate)
	@if [ -f jwt_keys/jwt.pem ] && [ -z "$(FORCE)" ]; then echo "keys: jwt_keys/jwt.pem exists (FORCE=1 to rotate)"; else \
		mkdir -p jwt_keys && chmod 700 jwt_keys && printf '*\n' > jwt_keys/.gitignore \
		&& openssl genrsa -out jwt_keys/jwt.pem 2048 2>/dev/null \
		&& openssl rsa -in jwt_keys/jwt.pem -pubout -out jwt_keys/jwt.pub.pem 2>/dev/null \
		&& chmod 600 jwt_keys/jwt.pem && chmod 644 jwt_keys/jwt.pub.pem \
		&& echo "keys: wrote jwt_keys/jwt.pem (private, 0600) and jwt_keys/jwt.pub.pem; the folder ignores itself in git"; fi

pull: ## pull every pinned image the stack uses (build-only services skipped)
	@[ -f .env ] || $(MAKE) --no-print-directory env
	$(COMPOSE) pull --ignore-buildable --quiet

# ── run ──────────────────────────────────────────────────────────────────
.PHONY: up-infra up up-obs up-cache up-debug up-dev down down-v reset ps logs
up-infra: env ## infrastructure only (mongo, rabbitmq, minio, opa)
	$(COMPOSE) up -d $(INFRA)
	@$(WAIT) --compose "$(COMPOSE)" $(INFRA)

up: env keys ## the default stack, 8 containers (mongo rabbitmq minio opa api harness gateway web)
	@missing=""; for f in src/backend/Dockerfile src/frontend/Dockerfile; do \
		[ -f "$$f" ] || missing="$$missing $$f"; done; \
	if [ -n "$$missing" ]; then echo "up: not buildable, missing:$$missing" >&2; exit 1; fi
	$(COMPOSE) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE)" $(INFRA) $(APPS) $(EDGE)

up-obs: env keys ## the default stack plus otel-collector, jaeger, prometheus and grafana (12 containers); services export traces
	OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 $(COMPOSE_OBS) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE_OBS)" $(INFRA) $(OBS) $(APPS) $(EDGE) \
		--url otel-collector=http://$$($(COMPOSE_OBS) port otel-collector 13133)/

up-cache: env keys ## the default stack plus Redis for SSE fan-out (api uses it via REDIS_URL)
	REDIS_URL=redis://redis:6379/0 $(COMPOSE_CACHE) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE_CACHE)" $(INFRA) redis $(APPS) $(EDGE)

up-debug: env keys ## the default stack with infrastructure and harness health ports published to the host
	$(COMPOSE_DEBUG) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE_DEBUG)" $(INFRA) $(APPS) $(EDGE)

up-dev: env keys ## default stack with bind-mounted source, uvicorn --reload and vite dev
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

seed-verify: ## recompute all 80 seed cases through POST /score and diff
	@$(call pending,the S2 scoring service route + ops/scripts/seed_verify.py (offline today: make test-unit runs the 80-case golden test))

demo: ## drive FCRM-2026-0151 end to end and assert the goldens (9.8 → 10.4, 2–1, 67 records)
	@$(call pending,S1–S5 + H1/H2 and ops/scripts/demo.py (integration lane))

demo-reset: ## delete case 0151's documents and uploads so the demo can run again, keep the other cases (refuses ENV=prod)
	$(PYTHON) ops/scripts/seed_reset.py --demo-case --compose "$(COMPOSE)"

verify: ## verify one case's audit chain: make verify case=FCRM-2026-0118
	@$(call pending,the S4 audit service GET /verify/{case_id})

# ── quality gates ────────────────────────────────────────────────────────
.PHONY: check lint format-check typecheck test test-unit opa-check opa-test compose-check web-install web-check gate
check: lint format-check typecheck opa-check opa-test test-unit compose-check web-check ## commit gate: ruff, format, mypy, OPA, unit tests, env contract, web typecheck and vitest
	@echo "check: green"

lint: ## ruff over src/backend and ops/scripts
	$(UV_RUN) ruff check $(LINT_PATHS)

format-check: ## ruff format --check over src/backend and ops/scripts
	$(UV_RUN) ruff format --check $(LINT_PATHS)

typecheck: ## mypy over every workspace package (strictness per mypy.ini)
	@if [ -z "$(MYPY_ARGS)" ]; then echo "typecheck: no packages"; else $(UV_RUN) mypy $(MYPY_ARGS); fi

opa-check: ## opa check --strict on the policies and tests
	$(OPA) check --strict $(OPA_DIR)/policy $(OPA_DIR)/tests

opa-test: ## Rego unit tests against the real permissions/tool_permits data
	$(OPA) test $(OPA_DIR)/policy $(OPA_DIR)/data $(OPA_DIR)/tests

test-unit: ## every unit test under tests/ (integration and e2e markers excluded)
	$(UV_RUN) pytest -m "not integration and not e2e"

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
	@echo "gate: pending pieces, not yet run: contracts (oasdiff, event schemas), evals-gate, semgrep"

# ── integration, e2e, resilience, evals ──────────────────────────────────
.PHONY: test-int test-integration e2e resilience evals evals-gate nightly
test-int: ## integration tests (testcontainers; needs Docker)
	@if [ -d tests/integration ]; then $(UV_RUN) pytest -m integration tests/integration; else $(call pending,the first tests/integration suite from the service lanes); fi

test-integration: test-int ## alias of test-int

e2e: ## 21 live scenes + BB-01…BB-14 against the live test stack (project trustops-test, ports +1000)
	@$(call pending,the app containers and tests/e2e; the stack override is ready: $(COMPOSE_TEST))

resilience: ## kill/restart drills (agent-worker, RabbitMQ, Mongo, identity, DLQ replay, budget)
	@$(call pending,tests/resilience (tests lane))

evals: ## per-agent eval run; writes evals/results/<date>/
	@$(call pending,the evals lane (X6 runner))

evals-gate: ## eval promotion gate for one agent: make evals-gate AGENT=research
	@$(call pending,the evals lane (X6 promotion gate))

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
.PHONY: dlq dlq-replay token-report build-usage backup restore rotate-keys
dlq: ## list dead-letter queues with their depth
	@$(COMPOSE) exec -T rabbitmq rabbitmqctl -q list_queues name messages consumers | awk 'NR==1 || $$1 ~ /\.dlq$$/'

dlq-replay: ## move every message of <q>.dlq back onto <q>: make dlq-replay q=agent-worker.run
	@[ -n "$(q)" ] || { echo "usage: make dlq-replay q=<work queue>, e.g. q=agent-worker.run" >&2; exit 2; }
	@$(COMPOSE) exec -T rabbitmq rabbitmqctl -q list_queues name | grep -qx '$(q).dlq' || { echo "dlq-replay: no queue $(q).dlq" >&2; exit 2; }
	$(COMPOSE) exec -T rabbitmq rabbitmqctl set_parameter shovel 'dlq-replay-$(q)' \
		'{"src-protocol":"amqp091","src-uri":"amqp://","src-queue":"$(q).dlq","dest-protocol":"amqp091","dest-uri":"amqp://","dest-queue":"$(q)","ack-mode":"on-confirm","src-delete-after":"queue-length"}'
	@echo "dlq-replay: shovel dlq-replay-$(q) moves the current backlog of $(q).dlq to $(q), then deletes itself"

token-report: ## (needs `make up-obs`) tokens and cost per agent, model and case from Prometheus → ops/token-analysis/runtime/
	$(PYTHON) ops/scripts/token_report.py --prometheus http://$$($(COMPOSE_OBS) port prometheus 9090)

build-usage: ## build-time Claude Code token analysis → ops/token-analysis/build/
	@$(call pending,ops/scripts/claude_usage.py (SDLC harness lane))

backup: ## mongodump every fcrm_* database and mirror MinIO into ops/backups/<ts>/
	@$(call pending,ops/scripts/backup.sh (ops-docs lane))

restore: ## restore a backup: make restore TS=<timestamp>
	@$(call pending,ops/scripts/restore.sh (ops-docs lane))

rotate-keys: ## rotate the JWT signing key (make keys FORCE=1) and restart the api (identity runs inside it)
	$(MAKE) --no-print-directory keys FORCE=1
	$(COMPOSE) restart api

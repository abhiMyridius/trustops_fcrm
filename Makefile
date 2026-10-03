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
UV_RUN    := $(UV) run --project src --all-packages
PYTHON    ?= python3

COMPOSE_FILES := -f docker-compose.yaml -f compose.obs.yaml
COMPOSE       := docker compose $(COMPOSE_FILES)
COMPOSE_DEV   := docker compose $(COMPOSE_FILES) -f compose.dev.yaml
COMPOSE_TEST  := docker compose -p trustops-test $(COMPOSE_FILES) -f compose.test.yaml

# Infra that runs without any application code (make up-infra).
ONESHOTS := mongo-init minio-init
INFRA    := mongo rabbitmq redis minio opa
OBS      := otel-collector jaeger prometheus grafana
SERVICES := identity scoring audit case policy decision
WORKERS  := caseflow agent-worker ingestion-worker context-worker
EDGE     := gateway web

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
PY_PACKAGES := $(sort $(foreach d,$(wildcard src/services/*/fcrm_* src/workers/*/fcrm_* src/workers/*/evoq_* src/packages/*/fcrm_*),$(if $(wildcard $(d)/__init__.py),$(notdir $(d)))))
MYPY_ARGS   := $(foreach p,$(PY_PACKAGES),-p $(p))
LINT_PATHS  := src ops/scripts

# Pending-lane helper: prints to stderr and fails (exit 1).
pending = printf '%s\n' "make $@: not yet wired — pending $(1)" >&2; exit 1

WAIT := $(PYTHON) ops/scripts/wait_for_ready.py

.PHONY: help
help: ## list targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_.-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ── setup ────────────────────────────────────────────────────────────────
.PHONY: bootstrap tools env keys pull
bootstrap: tools ## uv sync the workspace, install git hooks, pull images
	@if [ -f src/pyproject.toml ]; then $(UV) sync --project src --all-packages; else echo "bootstrap: no src/pyproject.toml, skipping uv sync"; fi
	@if [ -f .pre-commit-config.yaml ] && git rev-parse --git-dir >/dev/null 2>&1 && [ -z "$(SKIP_HOOKS)" ]; then \
		$(UV) run --project src pre-commit install --hook-type pre-commit --hook-type commit-msg; \
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

env: ## create .env from .env.example with generated local secrets (never overwrites)
	@$(PYTHON) ops/scripts/make_env.py --example .env.example --out .env

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
.PHONY: up-infra up up-dev down down-v reset ps logs
up-infra: env ## infra + observability only (mongo, rabbitmq, redis, minio, opa, otel, jaeger, prometheus, grafana) — works today
	$(COMPOSE) up -d $(INFRA) $(ONESHOTS) $(OBS)
	@$(WAIT) --compose "$(COMPOSE)" $(foreach s,$(ONESHOTS),--oneshot $(s)) $(INFRA) $(OBS) \
		--url otel-collector=http://$$($(COMPOSE) port otel-collector 13133)/

up: env keys ## the full stack, 24 containers (needs every lane's Dockerfile)
	@missing=""; for f in $(foreach s,$(SERVICES),src/services/$(s)/Dockerfile) $(foreach w,$(WORKERS),src/workers/$(w)/Dockerfile) src/web/Dockerfile; do \
		[ -f "$$f" ] || missing="$$missing $$f"; done; \
	if [ -n "$$missing" ]; then echo "up: not buildable yet, missing:$$missing" >&2; echo "up: use 'make up-infra' until those lanes land" >&2; exit 1; fi
	$(COMPOSE) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE)" $(foreach s,$(ONESHOTS),--oneshot $(s)) $(INFRA) $(OBS) $(SERVICES) $(WORKERS) $(EDGE) \
		--url otel-collector=http://$$($(COMPOSE) port otel-collector 13133)/

up-dev: env keys ## full stack with bind-mounted source, uvicorn --reload and vite dev
	$(COMPOSE_DEV) up -d --build
	@$(WAIT) --timeout 600 --compose "$(COMPOSE_DEV)" $(foreach s,$(ONESHOTS),--oneshot $(s)) $(INFRA) $(OBS) $(SERVICES) $(WORKERS) $(EDGE)

down: ## stop the stack, keep volumes
	$(COMPOSE) down --remove-orphans

down-v: ## stop the stack and delete its volumes (all local data)
	$(COMPOSE) down -v --remove-orphans

reset: down-v up ## wipe everything and start again

ps: ## container status and health
	$(COMPOSE) ps -a

logs: ## follow logs: make logs svc=case (all services when svc is empty)
	$(COMPOSE) logs -f --tail=200 $(svc)

# ── seed and demo ────────────────────────────────────────────────────────
.PHONY: gen-seed seed seed-verify demo demo-reset verify
gen-seed: ## regenerate seed.json and assert it is byte-stable (P7, ADR-16)
	cd src/packages/seed && $(UV) run --project ../.. --package fcrm-seed python -m fcrm_seed.gen_seed

seed: ## force-reload the seed into every fcrm_* database (refuses ENV=prod)
	@$(call pending,the per-service fcrm_seed loader wiring in S1–S5 and X1 (design A §11.3))

seed-verify: ## recompute all 80 seed cases through POST /score and diff
	@$(call pending,the S2 scoring service route + ops/scripts/seed_verify.py (offline today: make test-unit runs the 80-case golden test))

demo: ## drive FCRM-2026-0151 end to end and assert the goldens (9.8 → 10.4, 2–1, 67 records)
	@$(call pending,S1–S5 + H1/H2 and ops/scripts/demo.py (integration lane))

demo-reset: ## put case 0151 back to intake, keep the other 79 cases
	@$(call pending,the seed loaders in S1/S4/S5/H1)

verify: ## verify one case's audit chain: make verify case=FCRM-2026-0118
	@$(call pending,the S4 audit service GET /verify/{case_id})

# ── quality gates ────────────────────────────────────────────────────────
.PHONY: check lint format-check typecheck test test-unit opa-check opa-test compose-check gate
check: lint format-check typecheck opa-check opa-test test-unit compose-check ## commit gate (< 90 s): ruff, format, mypy, OPA, unit tests, env contract
	@echo "check: green"

lint: ## ruff over src/ and ops/scripts
	$(UV_RUN) ruff check $(LINT_PATHS)

format-check: ## ruff format --check over src/ and ops/scripts
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

gate: check ## push / task-done gate: check plus the parts other lanes have wired
	@if [ -d tests/integration ]; then $(MAKE) --no-print-directory test-int; else echo "gate: no tests/integration yet (skipped)"; fi
	@echo "gate: pending pieces, not yet run: contracts (oasdiff, event schemas), web vitest/tsc, evals-gate, semgrep"

# ── integration, e2e, resilience, evals ──────────────────────────────────
.PHONY: test-int test-integration e2e resilience evals evals-gate nightly
test-int: ## integration tests (testcontainers; needs Docker)
	@if [ -d tests/integration ]; then $(UV_RUN) pytest -m integration tests/integration; else $(call pending,the first tests/integration suite from the service lanes); fi

test-integration: test-int ## alias of test-int

e2e: ## 21 Playwright scenes + BB-01…BB-14 against the replay-mode test stack (project trustops-test, ports +1000)
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

api-gen: ## regenerate src/web/src/api/generated from the OpenAPI exports
	@$(call pending,the web-shell lane)

locks: ## regenerate ai/registry.lock after a change under /ai
	@$(call pending,the prompts-loader lane (P4))

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

token-report: ## tokens and cost per agent, model and case from Prometheus → ops/token-analysis/runtime/
	$(PYTHON) ops/scripts/token_report.py --prometheus http://$$($(COMPOSE) port prometheus 9090)

build-usage: ## build-time Claude Code token analysis → ops/token-analysis/build/
	@$(call pending,ops/scripts/claude_usage.py (SDLC harness lane))

backup: ## mongodump every fcrm_* database and mirror MinIO into ops/backups/<ts>/
	@$(call pending,ops/scripts/backup.sh (ops-docs lane))

restore: ## restore a backup: make restore TS=<timestamp>
	@$(call pending,ops/scripts/restore.sh (ops-docs lane))

rotate-keys: ## rotate the JWT signing key (make keys FORCE=1) and restart identity
	$(MAKE) --no-print-directory keys FORCE=1
	$(COMPOSE) restart identity

# TrustOps backend design (A) — services, Caseflow, events, data, platform

> **Design as written on 3 October 2026, kept as the record of the design stage.** It was the input the build lanes coded from, so it is not rewritten to match the code. Where the built system differs, the departure has a decision row; read `BUILD-PLAN.md` "Status as built" first, then `docs/governance/decision-log.md`. What is not done is in `docs/governance/known-gaps.md`. Values marked **(proposed)** below were proposals at the time; the ADR rows say which were accepted.
>
> The departures that matter most for this document:
> - Six services and four workers run as **eight containers**, two Python processes from one image: `api` mounts X1 and S1 to S5 by URL prefix, `harness` runs H1 to H4 (ADR-26). Code lives in `src/backend`, not `src/{services,workers,packages}` (ADR-25).
> - H1 does its HTTP work in **steps enqueued through the outbox**, outside the consumer transaction, each idempotent by step id (ADR-28, ADR-35, ADR-61).
> - **Route names follow the web** (`src/frontend/src/api/endpoints.ts`), not section 2.1; the one OpenAPI export is `docs/architecture/openapi/api.json` (ADR-30).
> - Agents call **live Bedrock only**; the replay and mock modes were removed (ADR-27). Vectors are in memory by default over Titan embeddings; the S3 Vectors backend is not in the shipped image (ADR-49).
> - There is **no job reaper and no circuit breaker**, and Redis is an optional profile nothing reads (`known-gaps.md`).

Status: build-ready, 3 Oct 2026. Audience: the Claude Code subagents that implement `~/Downloads/Hackathon/trustops`. Every contract here is meant to be coded from without a follow-up question. Values the source documents do not give are marked **(proposed)**; everything else traces to the build plan v1.3, decision log v1.3 (D-xx), blueprint/flow map v2, walkthrough v2 seed (P7), or CLAUDE.md.

Fixed decisions this document builds on (not reopened): single monorepo in the brief's layout; Python 3.13 + uv workspace; FastAPI + Pydantic 2; MongoDB 8 one database per service; FastStream + RabbitMQ (aio-pika) as job and event bus; Redis cache-only; Bedrock for models; S3 Vectors for the three indexes with Titan Embed Text v2 (1024-d); files on the S3 API (MinIO locally); AEO components cloned and fixed in-tree; H1 calls S2 over HTTP, agents never score; routing R1–R6 as data evaluated by H1; committee outcome = 2-of-3 identical votes else `no_majority` → thread + re-vote via R6; OpenTelemetry → otel-collector → Jaeger/Prometheus/Grafana locally, ADOT path documented; nothing is approved or rejected automatically.

Naming: the component IDs and names from the blueprint naming standard are used throughout (S1 case-service … H4 context-worker, P1–P7, X1–X7, D1–D2). Python import names are `fcrm_*`; distribution names `fcrm-*`; databases `fcrm_*`; the brand name TrustOps appears only in the README and the web UI.

---

## 1. Monorepo tree (Python side), uv workspace, `common`

### 1.1 Tree

```
trustops/
  README.md
  docker-compose.yaml          # one root compose (section 11)
  Makefile                     # section 11.6
  .env.example                 # every variable name in section 11.4, no values
  ai/                          # prompts (P4 files), agent specs, skills, tiers.yaml, tool_permits.json copy
  docs/{requirements,architecture,governance}/
  evals/                       # X6: datasets per agent, runner, reports/ (committed JSON + HTML)
  tests/                       # cross-unit: contract tests, compose smoke, Playwright e2e
  ops/                         # nginx/, opa/, mongo/init/, otel/, grafana/, minio/, scripts/
  src/
    pyproject.toml             # uv workspace root: [tool.uv.workspace] members = ["services/*","workers/*","packages/*"]
    uv.lock                    # single shared lockfile
    services/
      case/        fcrm_case/           S1  FastAPI; cases, versions, fields, documents, threads, conditions, SSE
      scoring/     fcrm_scoring/        S2  FastAPI; pure engine, model versions, routing rules
      policy/      fcrm_policy/         S3  FastAPI; corpus, clauses, controls, jurisdictions, field sets, precedents, search
      audit/       fcrm_audit/          S4  FastAPI; proposals ledger, hash-chained records, verify, examiner read
      decision/    fcrm_decision/       S5  FastAPI; packs, hidden votes, tally, outcomes, R6 threads/addenda
      identity/    fcrm_identity/       X1  FastAPI; clone of evoqidentityservice, password login, RS256, /core/validate
    workers/
      caseflow/          fcrm_caseflow/          H1  FastStream; state machine, gates, budgets, outbox, event store
      agent-worker/      fcrm_agent_worker/      H2  FastStream; one Pydantic AI call per job, guards 1–4
      ingestion-worker/  fcrm_ingestion_worker/  H3  FastStream host + evoq_ingestion/ (clone, pruned)
      context-worker/    fcrm_context_worker/    H4  FastStream host + evoq_context/ (clone, 3 indexes, filters)
    packages/
      contracts/      fcrm_contracts/   P6  Pydantic models (API + events + proposals), event catalogue, OpenAPI stubs, fixtures
      common/         fcrm_common/      —   settings base, errors, logging, tracing, mongo, outbox, health, http client, authz client
      guardrails/     fcrm_guardrails/  P5  guards 1–5 + 7 as modules, rules/ (8 functions), thresholds.yaml
      agents/         fcrm_agents/      P1  AgentSpec registry, 9 agent folders, Pydantic AI adapter, expected_agents.txt
      tools/          fcrm_tools/       P2  ToolSpec registry, 13 read-only clients, expected_tools.txt
      skills/         fcrm_skills/      P3  5 instruction packs (markdown + loader)
      prompts-loader/ fcrm_prompts/     P4  front-matter parser, Jinja renderer, version pinning; files live in /ai/prompts
      seed/           fcrm_seed/        P7  gen_seed.py, seed.json, corpus/*.md, clauses.json, vectors_fixture.json, loader
    web/                                W1  React (not covered here)
```

One line per unit folder: `pyproject.toml` (name, deps, `[project.scripts]`), `src/<import_name>/`, `tests/` (mirrors src; `unit/` and `integration/` split; fixtures only in `conftest.py`), `Dockerfile` (uv multi-stage from the workspace lock, `python:3.13-slim`, non-root `app` user, `HEALTHCHECK`), and for clones an `UPSTREAM.md` (source path, commit from E §1, every change).

### 1.2 uv workspace rules

- `src/pyproject.toml` is the workspace root with `[tool.uv.workspace] members` and `[tool.uv.sources]` pointing every `fcrm-*` dependency at `{ workspace = true }`.
- One `uv.lock`; `uv sync --package fcrm-case-service --frozen` inside each Dockerfile; images copy only the packages they depend on (`uv export --package` then `uv pip install`).
- Shared dev group at the root: ruff, mypy, pytest, pytest-asyncio, pytest-cov, hypothesis, pre-commit, semgrep, opa (binary via ops script).
- mypy strict on `fcrm_scoring`, `fcrm_guardrails`, `fcrm_contracts`, `fcrm_caseflow`, `evoq_ingestion`, `evoq_context`; normal elsewhere.
- Lint/type targets `py313`. Engines keep their AST host-import tests (no faststream/pymongo/opentelemetry inside `evoq_*`).

### 1.3 Mongo driver

**PyMongo async (`pymongo.AsyncMongoClient`, pymongo ≥ 4.13)**, not Motor: Motor is deprecated in favour of it, and PyMongo async is the only driver that will receive fixes through the project's life; the cloned `mongo_repo` is ported in one file.

### 1.4 `fcrm_common` contents

| Module | Contents |
|---|---|
| `settings.py` | `BaseServiceSettings(BaseSettings)`: `APP_NAME, APP_ROOT_PATH, ENV, LOG_LEVEL, MONGODB_URI, MONGODB_DB, RABBITMQ_URL, REDIS_URL, OTEL_EXPORTER_OTLP_ENDPOINT, IDENTITY_JWKS_URL, IDENTITY_VALIDATE_URL, OPA_URL, SERVICE_TOKEN_SECRET, SEED_PATH, SEED_ON_EMPTY`; `SettingsConfigDict(env_file=None, extra="forbid")`; no `os.getenv` at import time; `fail_fast()` validator raises on missing secrets. Subclassed per unit. |
| `errors.py` | Exception hierarchy and problem+json handler (section 10.1–10.2). |
| `logging.py` | JSON logs to stdout via `structlog`-free stdlib `logging` with a `JsonFormatter`; fields in 10.7; `bind(**ctx)` contextvars; OTel trace/span ids injected. |
| `tracing.py` | `init_otel(service_name, version)`: `Resource.create`, OTLP HTTP exporters for traces/metrics/logs when `OTEL_EXPORTER_OTLP_ENDPOINT` is non-empty (no cluster-name gate), FastAPI/HTTPX/PyMongo instrumentors; `inject_amqp(headers)` / `extract_amqp(headers)` for `traceparent`/`tracestate`; `case_span(case_id, run_id)` helper. |
| `metrics.py` | Meter + named instruments: `fcrm_http_requests_total`, `fcrm_http_latency_ms`, `fcrm_agent_tokens_total{agent,model,tier}`, `fcrm_agent_cost_usd_total`, `fcrm_agent_latency_ms`, `fcrm_gate_wait_seconds{gate_type}`, `fcrm_override_total{agent}`, `fcrm_outbox_lag_seconds`, `fcrm_queue_depth`, `fcrm_budget_used_ratio`. |
| `mongo.py` | `get_client()` singleton (`AsyncMongoClient`, `serverSelectionTimeoutMS=5000`, `uuidRepresentation="standard"`), `Repository[T]` generic over a Pydantic model: `insert_one, insert_many, find_one, find, find_paginated, update_one(filter, $set, upsert=False), find_one_and_update, count, aggregate, ensure_indexes(list[IndexModel])`; `with_transaction(fn)` (requires replica set `rs0`). Port of `aeoingestionservice/drivers/mongo_repo.py` with snake_case names only and no mutable defaults. |
| `outbox.py` | `Outbox(repo)`: `enqueue(session, envelope)` (same transaction as the state write), `OutboxRelay(broker, repo, lease_name)`: polls `published_at: null` in `_id` order every 200 ms, publishes with publisher confirms, sets `published_at`; leader by a `leases` document (`lease_until`, 10 s TTL renewed every 3 s); exposes `fcrm_outbox_lag_seconds`. |
| `events.py` | `Envelope` model (section 4.2), `publish(broker, exchange, routing_key, envelope)`, `Consumer` base with dedupe (`consumed_messages` collection, unique `message_id`, TTL 7 d) and automatic `x-attempt` retry/DLQ handling (section 4.5). |
| `health.py` | `/healthz` (liveness: process up), `/readyz` (Mongo `ping`, RabbitMQ connection open if used, OPA `GET /health` if used, S3 `head_bucket` if used); returns `{status, checks:{name:{ok,ms}}}` 200/503. Workers mount the same two routes on an `AsgiFastStream` app. |
| `http.py` | `ServiceClient(base_url, timeout=Timeouts(connect=2, read=10))`: shared `httpx.AsyncClient`, retries per 10.3, `Idempotency-Key` support, service-account bearer (`SERVICE_TOKEN_SECRET`-signed, role `fcrm_service`), traceparent propagation. |
| `auth.py` | `current_user()` FastAPI dependency: verifies RS256 JWT locally against the identity JWKS (cached 10 min; Redis 60 s fallback to `/core/validate` when `IDENTITY_JWKS_URL` is unset); `require_roles(*roles)`; `Principal(sub, roles, name, typ)`. |
| `authz.py` | `OPAClient`: `authz(principal, service, method, route_template) -> Decision` and `tool_permit(agent, tool, case_id, role, run_id) -> bool`; Redis cache 30 s keyed by `(roles, service, method, route_template)` / `(agent, tool, role)`; `opa_dependency(service)` FastAPI dependency (port of `opasecurity.py` without `print`, with 503 on transport errors). |
| `ids.py` | `new_id(prefix)` → `prefix_` + ULID (`evt_`, `job_`, `run_`, `gate_`, `pack_`, `thr_`, `cnd_`, `doc_`, `sv_`); `mint_case_id(year, seq)` → `FCRM-2026-0152`. |
| `hashing.py` | `canonical_json(obj)` (sorted keys, no whitespace, UTF-8), `sha256_hex`, `chain_hash(prev_hash, record_body)`. |
| `time.py` | `utcnow()` (tz-aware), `business_days_between`, `add_business_days` (US federal holidays table for 2026–2027). |
| `pagination.py` | `Page[T] {items, next_cursor, total?}`; cursor = base64 of last `_id`. |
| `seedload.py` | `load_if_empty(db, collection, docs)` and `SeedReader(SEED_PATH)` used by every service's lifespan (section 11.5). |

---
## 2. Services

Conventions for every service (S1–S5, X1): FastAPI app factory `create_app()`; routes → services → repositories; root paths aligned with the web contract (C-web): S1 is mounted at `APP_ROOT_PATH=/api/fcrm` and owns `/cases/*`, `/queue`, `/conditions*`, `/reflags*`, `/dashboard` (so the browser calls `/api/fcrm/cases/{id}/stream`); S2–S5 at `/api/fcrm/scoring`, `/api/fcrm/policy`, `/api/fcrm/audit`, `/api/fcrm/decision`; X1 at `/api/fcrm/auth`. The gateway strips nothing. Every service serves `/healthz` and `/readyz` with the aliases `/health` and `/ready`, and `/openapi.json` plus Swagger at `/dev/docs` (HTTP Basic) in `ENV=local` only; every route except `/healthz`, `/readyz`, `/.well-known/*` and identity's login routes carries `Depends(current_user)` and `Depends(opa_dependency)`; every mutating route accepts `Idempotency-Key` (stored in `idempotency_keys`, unique, TTL 24 h; a replay returns the stored response); every state change goes through `with_transaction` together with `outbox.enqueue`; responses are role-shaped models (never the raw document). Roles: `fcrm_po`, `fcrm_analyst`, `fcrm_committee`, plus the internal `fcrm_service` (H1/H2/S5 service-account token) which is never issued to a person.

Restricted-field rule (D-62, Rule 3): the PO models `CasePO`, `CasePOListItem`, `IntakeViewPO`, `ReviewAndSubmitView`, `OutcomePO`, `ActivityEventPO`, `ConditionPO`, `ThreadPO` are defined in `fcrm_contracts.api.case_po` and a contract test asserts their JSON schemas contain none of: `score, composite, band, route, residual, inherent, retention, ce, control_effectiveness, rating, likelihood, impact, clause, clauses, boundary, critic, precedent_rating, recommendation, vote, pack`. A response middleware in S1 re-scans every PO response body for those keys and fails closed with 500 `rule_violation:R3` (defence in depth, counted in `fcrm_rule_violations_total`).

### 2.1 S1 case-service (`fcrm_case`, db `fcrm_case`, port 8001)

Purpose: system of record for the case; owner-side intake and corrections; submission versions with diffs; clarification threads; conditions and reminders; the analyst's review items (what gates act on); the SSE stream. Never scores, never calls a model. Publishes case events; consumes H1's view updates over HTTP (not events).

Routes (role → method path → request → response). `{id}` is a case id.

PO (`fcrm_po`; ownership enforced: `case.owner_id == sub`):

| Method · path | Request → Response | Notes |
|---|---|---|
| POST `/cases` | `CaseCreate{title?, unit}` → `CasePO` | mints `FCRM-YYYY-NNNN` from `counters`; stage `intake`; outbox `case.created` |
| GET `/cases` | `?stage=&cursor=` → `Page[CasePOListItem]` | own cases only |
| GET `/cases/{id}` | → `CasePO` | fields with `origin` (extracted/reused/answered/owner_declared), documents, completion, stage, next action |
| POST `/cases/{id}/documents` | multipart file(s) → `DocumentOut[]` | ≤ 50 MB each **(proposed)**, allow-list `.pdf .docx .xlsx .pptx .csv .md .txt`; stored at `uploads/{case_id}/{doc_id}/{filename}`; outbox `case.document_stored` |
| GET `/cases/{id}/documents/{doc_id}/download` | → 302 presigned URL (10 min) | |
| GET `/cases/{id}/intake` | → `IntakeViewPO` | pending `CategoryProposalPO[]`, `ValueProposalPO[]` (value, source page:line, confidence), `ReuseSuggestionPO` (from case id + fields, no outcome), `GapQuestionPO[]` |
| POST `/cases/{id}/intake/categories/{change_type}` | `GateDecision{decision: confirm\|remove, reason?}` → `IntakeViewPO` | remove requires reason; outbox `gate.decided` (`po_confirm_category`) |
| POST `/cases/{id}/intake/values/{field_id}` | `ValueDecision{decision: confirm\|correct, value?, reason?}` → `FieldValueOut` | `correct` → origin `owner_declared`, extracted value + source kept in `field_values.history[]`; outbox `gate.decided` (`po_confirm_value`) |
| POST `/cases/{id}/intake/reuse` | `ReuseRequest{from_case_id, field_ids[]}` → `FieldValueOut[]` | origin `reused`, `source = "reused · FCRM-2026-0118"` |
| POST `/cases/{id}/intake/answers` | `AnswerBatch{answers:[{question_id, field_id?, value?, document_id?}]}` → `IntakeViewPO` | new facts flagged (`flags[]`), never blocked |
| GET `/cases/{id}/review` | → `ReviewAndSubmitView` | whole case, every value's origin, completion `filled/required/pct`, the four submit checks (dry run) |
| POST `/cases/{id}/submit` | `{}` → `SubmitResult{ok, checks[4], submission_version}` | runs the four checks (2.1.3); creates `submission_versions` v1; stage `submitted`; outbox `case.submitted` |
| GET `/cases/{id}/inbox` | → `ThreadPO[]` | open and answered rounds |
| POST `/cases/{id}/clarifications/{thread_id}/answer` | `ThreadAnswer{text, field_values:[{field_id,value}], document_id?}` → `ThreadPO` | folds into one submission version per round; outbox `case.answered` when the round's last open thread is answered |
| GET `/cases/{id}/conditions` | → `ConditionPO[]` | owner's conditions (text, owner, due, status), no provenance to controls |
| POST `/cases/{id}/conditions/{cnd_id}/evidence` | `{note, document_id?}` → `ConditionPO` | marks `evidence_submitted`; analyst closes it |
| GET `/cases/{id}/outcome` | → `OutcomePO{outcome, plain_summary, conditions[]}` | from `outcome.set`; business register only |
| GET `/cases/{id}/activity` | → `Page[ActivityEventPO]` | allow-listed event types only |
| GET `/cases/{id}/stream` | SSE | section 2.1.5 |
| GET `/dashboard` | → `DashboardPO{active, awaiting, avg_days_intake_to_decision, open_conditions}` | computed server-side |

Analyst (`fcrm_analyst`; write requires `case.analyst_id == sub` and stage not closed; reads open to all analysts and logged `case_viewed`):

| Method · path | Request → Response | Notes |
|---|---|---|
| GET `/queue` | → `QueueItem[]` | assigned cases, fast-lane pill, due date, stage, boundary/critic counts |
| GET `/cases/{id}/workbench` | → `CaseAnalyst` | full aggregate: submission, items, score snapshot, critic findings, completeness, threads, recommendation, pins, clocks |
| GET `/cases/{id}/submissions` | → `SubmissionVersion[]` | |
| GET `/cases/{id}/submissions/diff?from=1&to=2` | → `SubmissionDiff{changed:[{field_id, before, after, before_origin, after_origin}], added_documents[]}` | |
| POST `/cases/{id}/change-types` | `{change_type, reason}` → `CaseAnalyst` | analyst re-tag (adds only; D-03); outbox `case.retagged` |
| POST `/cases/{id}/screening` | `GateDecision{decision: confirm_fast_lane\|decline, reason}` → 202 | outbox `gate.decided` (`fast_lane_confirm`) |
| POST `/cases/{id}/completeness` | `CompletenessDecision{decision: ask\|proceed, questions:[{question_id, text, field_ids[], item_ref?, not_anchored_reason?, keep}]}` → 202 | `ask` creates the round (below); outbox `gate.decided` (`completeness`) |
| POST `/cases/{id}/clarifications` | `RoundCreate{questions:[...]}` → `ThreadAnalyst[]` | one round open at a time (409 otherwise); anchors validated against resolved field set; analyst clock pauses; outbox `case.clarification_sent` |
| POST `/cases/{id}/clarifications/{thread_id}/resolve` | `{note?}` → `ThreadAnalyst` | |
| GET `/cases/{id}/items` | `?kind=clause\|rating\|control\|critic_finding` → `ReviewItem[]` | the gate subjects |
| POST `/cases/{id}/items/{item_id}` | `ItemDecision{decision: accept\|remove\|confirm\|override\|explain\|adjust, value?, reason?}` → `ReviewItem` | `override/remove/adjust` require `reason` (Rule 6); one item per call (Rule 2, no bulk route exists); outbox `gate.decided` (`review_item`) |
| POST `/cases/{id}/acknowledge` | `{kind: boundary\|band_change, note?}` → 202 | R3; outbox `gate.decided` (`ack`) |
| POST `/cases/{id}/controls/planned` | `PlannedControl{control_id?, description, owner, target_date}` → `PlannedControlOut` | never scored (Rule 7); outbox `case.control_planned` |
| POST `/cases/{id}/gaps` | `{text, scope}` → `GapOut` | policy gap recorded; outbox `case.gap_recorded` |
| POST `/cases/{id}/runs` | `{reason, scope: changed_fields\|full}` → 202 `{run_request_id}` | outbox `case.rerun_requested` |
| GET/PUT `/cases/{id}/recommendation` | `RecommendationEdit{sections:[{key, text}], conditions:[...], business_summary}` → `Recommendation` | edits logged (`edits_by_analyst`) |
| POST `/cases/{id}/recommendation/lock` | `{}` → `Recommendation{locked:true}` | outbox `case.locked` |
| POST `/cases/{id}/recommendation/unlock` | `{reason}` → `Recommendation` | outbox `case.unlocked` (D-19a) |
| POST `/cases/{id}/close` | `{outcome, reason, plain_summary}` → `CaseAnalyst` | delegated close (R1); refused unless latest score snapshot band ∈ {1,2} and lock held; outbox `case.close_requested` (H1 confirms via S5 outcome) |
| GET `/cases/{id}/score` | → `ScoreSnapshot` | the last snapshot pushed by H1 |
| GET `/cases/{id}/activity` | → `Page[ActivityEventAnalyst]` | |
| GET `/conditions` · PATCH `/conditions/{cnd_id}` | `?due_before=&status=` · `{status, note}` | tracker |
| GET `/reflags` · POST `/reflags/{id}/acknowledge` | → `Reflag[]` · `{note, rerun: bool}` | watchlist (O4–O5); outbox `gate.decided` (`reflag_ack`) |
| GET `/cases/search` | `?q=&change_types=&geographies=&stage=` → `CaseSearchHit[]` | backs the `case_search` tool and the watchlist |

Internal (`fcrm_service` only, called by H1/H2 tools):

| Method · path | Purpose |
|---|---|
| GET `/internal/cases/{id}/record` | full record for `case_record` tool (fields with origins, documents with canonical md refs, threads, pins) |
| PUT `/internal/cases/{id}/items` | upsert review items from proposals (`ReviewItem[]` with `proposal_id`, `supersedes`) |
| PUT `/internal/cases/{id}/score` | push `ScoreSnapshot` (analyst-visible) |
| PUT `/internal/cases/{id}/critic` · `/completeness` · `/recommendation-draft` | push agent outputs into the analyst view |
| PATCH `/internal/cases/{id}/state` | `{stage, run_id, assigned_analyst?, clocks?}` |
| POST `/internal/cases/{id}/assign` | `{analyst_id, reason:"R5 lowest load", open_counts}` |
| POST `/internal/cases/{id}/events` | push a UI event to the SSE stream (`UiEvent`) |
| PUT `/internal/cases/{id}/outcome` | from `outcome.set`: stores `OutcomeRecord` + activates conditions |
| GET `/internal/analysts/load` | `{analyst_id: open_count}` for R5 |

#### 2.1.1 Collections (db `fcrm_case`)

| Collection | Fields | Indexes / validation |
|---|---|---|
| `cases` | `_id` (case id), `title, short, unit, owner_id, analyst_id, stage` (CaseState string), `change_types[], domains[], geographies[], fast_lane{proposed, confirmed, reasons[]}, pins{scoring_v, corpus_v, controls_v, fieldset_v, jurisdictions_v, routing_v, prompts_v}, run_id, trace_id, created_at, submitted_at, decided_at, closed_at, clocks{analyst_active_s, paused_since?, total_started_at}, completion{filled, required, pct, threshold}, flags[], current_submission_v, locked{at, by}|null, outcome_ref, score_snapshot_ref` | `{owner_id, created_at}`, `{analyst_id, stage}`, `{stage}`, text index on `title`; `$jsonSchema` on `stage` enum and `change_types` enum |
| `counters` | `_id:"case:2026", seq` | |
| `submission_versions` | `_id` (`sv_`), `case_id, version, captured_at, captured_by{sub, role}, reason (submit\|round\|correction), round?, field_values{field_id:{value, origin, source}}, document_ids[], changed_field_ids[], diff_from_previous` | unique `{case_id, version}` |
| `field_values` | `_id` (`case_id:field_id`), `case_id, field_id, label, required, value, origin, source{doc_id?, page?, line?, from_case_id?, question_id?}, confidence?, history:[{value, origin, source, at, by}]` | `{case_id}` |
| `documents` | `_id` (`doc_id`), `case_id, name, size_bytes, content_type, sha256, bucket, key, canonical_key?` (MinIO `canonical/{case_id}/{doc_id}.md`), `pages?, status (stored\|quarantined\|extracted\|failed), guard1{pii_masked, injection_hits, clean}, raised_in_round?, uploaded_by, uploaded_at` | `{case_id}` |
| `clarification_threads` | `_id` (`thr_`), `case_id, round, question_id, text, field_ids[], item_ref?, not_anchored_reason?, status (awaiting_response\|answered\|resolved), messages:[{author_id, role, text, at, document_id?}], resulting_version?, opened_at, answered_at?` | `{case_id, round}` |
| `review_items` | `_id` (`itm_`), `case_id, kind (clause\|rating\|control\|critic_finding\|question\|gap), proposal_id, agent, prompt_v, subject{...kind-specific}, proposed{value, source_ref, quote, confidence}, status (proposed\|accepted\|confirmed\|removed\|overridden\|explained\|adjusted), decided{value, reason, by, at}, supersedes?, run_id` | unique `{case_id, proposal_id}`, `{case_id, kind, status}` |
| `conditions` | `_id` (`cnd_`), `case_id, text, owner_id, owner_label, due_label, due_date, status (draft\|open\|scheduled\|evidence_submitted\|done\|overdue), from{kind: overridden_control\|planned_control\|policy_gap, ref}, reminders_sent[]` | `{due_date, status}` |
| `reflags` | `_id, case_id, list_version, geographies[], proposal_id, status (open\|acknowledged), note, rerun_requested` | |
| `activity` | `_id, case_id, at, type, actor{id, role, component}, summary, detail?, visibility (po\|analyst\|all)` | `{case_id, at}` |
| `score_snapshots`, `critic_views`, `completeness_views`, `recommendations`, `outcomes` | H1-pushed analyst-side views, one latest doc per case (`_id = case_id`), full history in `score_snapshots` (`{case_id, computed_at}`) | |
| `outbox`, `consumed_messages`, `idempotency_keys`, `leases` | from `fcrm_common` | TTL on `consumed_messages.at` (7 d), `idempotency_keys.at` (24 h) |

#### 2.1.2 Rules enforced in S1

- Rule 4 (analyst never edits submission): `field_values` writes accept only `fcrm_po`; OPA denies `POST /cases/{id}/intake/*` and `.../answer` for analysts; the only analyst-originated write that touches a submission is a clarification *question*.
- Rule 2 (no bulk accept): there is no batch item route; the gateway rate-limits `POST /cases/{id}/items/*` to 5 req/s per user **(proposed)** so a UI loop is visibly throttled.
- Rule 5 (closed is closed): every analyst write checks `stage != closed`; H1 is the only caller of `PATCH state`.
- Rule 6 (override has a reason): Pydantic `model_validator` on `ItemDecision` rejects `override/remove/adjust` without `reason` (min 10 chars).
- Rule 7 (planned not scored): planned controls are a separate collection field (`planned: true`), never emitted in the `ScoreRequest` built by H1 (H1 filters, S2 rejects `planned` entries with 422 as a second check).

#### 2.1.3 The four submit checks (D-17; the docs name "four visible checks" but do not enumerate them — **proposed**)

1. `always_required_filled`: F01–F06 present (F06 only when a third party is involved).
2. `completion_threshold`: `filled/required ≥ 0.80` against the pinned field set.
3. `categories_confirmed`: every proposed change type confirmed or removed with a reason.
4. `documents_clean`: no document in `quarantined` or `failed` state.

A failed check returns `SubmitResult{ok:false, checks:[{id, ok, detail}]}` with 409; intake reopens for the named gaps (F8–F9).

#### 2.1.4 Clocks ("two clocks")

`clocks.total_started_at` = submit; `analyst_active_s` accumulates while no round is awaiting the owner; `paused_since` set on `case.clarification_sent`, cleared when the round's last thread is answered. Business-day maths from `fcrm_common.time`. Exposed on `CaseAnalyst.clocks{total_days, analyst_active_hms, paused}`.

#### 2.1.5 SSE stream

`GET /cases/{id}/stream` → `text/event-stream`; events `{id: ulid, event: <type>, data: json}`; supports `Last-Event-ID` replay from `activity` (events are persisted before being published); fan-out via Redis pub/sub channel `sse:case:{id}` (so any replica serves any browser); heartbeat comment every 15 s; role filter: PO connections receive only `po`/`all`-visibility event types (`case.stage_changed`, `intake.proposals_ready`, `document.extracted`, `clarification.*`, `outcome.delivered`, `condition.*`); analysts receive everything including `score.computed`, `critic.ready`, `agent.started/done/failed`, `gate.opened`. Redis down ⇒ the stream degrades to single-replica in-process fan-out and `/readyz` reports `redis: degraded` (not failed).

### 2.2 S2 scoring-service (`fcrm_scoring`, db `fcrm_scoring`, port 8002)

Purpose: the deterministic engine, scoring model versions, routing-rule versions, band table. Pure: `/score` performs no database read (the model is passed by version and loaded once per request before the pure call); stores nothing about cases.

| Role | Method · path | Request → Response |
|---|---|---|
| service, analyst | POST `/score` | `ScoreRequest` → `ScoreResponse` (section 5.4) |
| analyst | POST `/simulate` | `ScoreRequest + model_override: ScoringModelVersion` → `ScoreResponse` (config test panel; never persisted) |
| all roles | GET `/models` · GET `/models/{version}` | `ScoringModelVersion[]` · `ScoringModelVersion` |
| analyst | POST `/models` | `ScoringModelVersion` (status `draft`) → stored; POST `/models/{version}/activate` → previous active becomes `prior` |
| all roles | GET `/routing-rules` · GET `/routing-rules/{version}` | `RoutingRulesVersion` |
| analyst | POST `/routing-rules` · POST `/routing-rules/{version}/activate` | as models |
| all roles | GET `/bands?version=` | `BandDef[]` |

Collections: `scoring_model_versions` (`_id = version`, section 5.2 schema, `status`, `published_at`, `published_by`, `note`), `routing_rules_versions` (`_id = version`, section 5.3), `idempotency_keys`. Seeded from P7 (`scoring_model` → v3 active; `routing` → v4 active). PO role has no route here (OPA denies all `fcrm_po` on this service).

### 2.3 S3 policy-service (`fcrm_policy`, db `fcrm_policy`, port 8003)

Purpose: versioned reference data (corpus, clauses, controls inventory, jurisdiction lists, field sets), the precedent index, and search over S3 Vectors with metadata filters plus Mongo text rank fusion. Never decides applicability. Full detail in section 6; routes:

| Role | Method · path | Request → Response |
|---|---|---|
| service, analyst | POST `/policy/search` | `PolicySearchRequest` → `PolicySearchResponse` |
| service, analyst | POST `/precedents/search` | `PrecedentSearchRequest` → `PrecedentSearchResponse` |
| service, analyst | GET `/clauses/{clause_id}?corpus_version=` | `Clause` (verbatim text, `ref`, `source`, page/line) |
| all roles | GET `/corpus/versions` · GET `/corpus/{version}` | `CorpusVersion[]` · `CorpusVersion{sources[], clause_count, ingested_at}` |
| analyst | POST `/corpus/sources` | multipart doc + `{title, kind, version_note}` → 202 `{source_id, job_id}` (ingest → segment → index; new corpus version `v15` on `index.built`) |
| service, analyst | GET `/controls?version=&domain=` · GET `/controls/versions` | `ControlInventory{version, controls[]}` |
| analyst | POST `/controls/versions` | `ControlInventory` (draft) · `/activate` |
| service, analyst | GET `/jurisdictions?version=` · GET `/jurisdictions/versions` | `JurisdictionList` |
| analyst | POST `/jurisdictions/versions` | `JurisdictionList` → stored; `/activate` publishes outbox `jurisdiction.list_published` (triggers the watchlist run) |
| service, analyst, po | GET `/field-sets/{version}` · GET `/field-sets/{version}/resolve?change_types=a,b` | `FieldSet` · `ResolvedFieldSet{required:[{id,label,always}], max_required, completion_threshold}` (PO sees labels only) |
| service | POST `/internal/precedents` | `PrecedentRecord` (H1 posts on `case.closed`) → stored + outbox `index.build` for `precedent_cases` |
| service | POST `/internal/index-status` | `{index, version, built_at, vector_count, fixture_key?}` from H4 |

Collections: `corpus_versions` (`_id=version, ingested_at, sources:[{id,title,kind,sections[],clauses,used_for,canonical_key}], clause_count, status`), `clauses` (`_id = "{corpus_version}:{clause_id}"`, `clause_id, corpus_version, source_id, title, text, ref, page, line_start, line_end, risk_areas[], trigger_types[], jurisdictions[], superseded_by?`; text index on `text, title`; `{corpus_version, source_id}`), `control_inventory_versions` (`_id=version, controls:[{id, domain, name, weight, evidence_artefact, last_tested}], status`), `jurisdiction_list_versions` (`_id=version, published, source, call_for_action[], increased_monitoring[], changes{added[],removed[]}, fixture: bool, status`), `field_set_versions` (`_id=version, max_required, completion_threshold, always_required[], per_type{}`), `precedents` (`_id=case_id, title, change_types[], domains[], geographies[], closed_date, outcome, band, control_ratings{CTL-x: rating}, summary, indexed_at`), `index_status` (`_id=index_name`), `outbox`, `idempotency_keys`.

### 2.4 S4 audit-service (`fcrm_audit`, db `fcrm_audit`, port 8004)

Purpose: the evidence ledger (proposals) and the hash-chained, append-only audit record per case; verify; the examiner read API. There is no update or delete route and the connected database user cannot update or delete.

| Role | Method · path | Request → Response |
|---|---|---|
| service (H1) | POST `/proposals` | `ProposalAppend{case_id, run_id, agent, prompt_v, model, attempt, kind, items[], usage{...}, guard_results[]}` → `Proposal{proposal_id, ...}` |
| service, analyst | GET `/proposals?case_id=&agent=&kind=&run_id=` | `Page[Proposal]` |
| service (H1, S1, S5) | POST `/records` | `RecordAppend` → `AuditRecord` (section 2.4.1) |
| service, analyst, committee | GET `/records?case_id=&from_seq=&types=` | `Page[AuditRecord]` (committee only for cases in its released packs) |
| service, analyst | GET `/records/{case_id}/{seq}` | `AuditRecord` |
| all roles incl. po | GET `/verify/{case_id}` | `VerifyResult{ok, count, head_hash, first_broken_seq?, verified_at}` (PO gets `{ok, count, verified_at}` only) |
| service, analyst | GET `/examiner/{case_id}` | `ExaminerBundle{records[], proposals[]}` for the `audit_log` tool |
| service, analyst | GET `/metrics/override-rate?agent=&from=&to=` | `{agent, proposals, overridden, rate}` (live quality signal, D-71) |

#### 2.4.1 Hash-chain record format

```json
{ "_id": "FCRM-2026-0151:49", "case_id": "FCRM-2026-0151", "seq": 49, "run_id": "run_…",
  "type": "human.override", "actor": {"id": "u-riyer", "role": "fcrm_analyst", "component": "S1"},
  "what": "CTL-T01 7.6 → 4.5", "payload": {"item_id": "…", "before": 7.6, "after": 4.5, "reason": "…"},
  "occurred_at": "2026-10-01T15:02:11.482Z", "trace_id": "0af3c1e9d7b24c6e…",
  "versions": {"scoring_v":"v3","corpus_v":"v14","controls_v":"v9","fieldset_v":"v6","jurisdictions_v":"v2026-06","routing_v":"v4","prompts_v":"2026-09-28"},
  "prev_hash": "…64 hex…", "hash": "…64 hex…", "hash_alg": "sha256-v1" }
```

`body = canonical_json({case_id, seq, run_id, type, actor, what, payload, occurred_at, trace_id, versions})`; `hash = sha256_hex(prev_hash + "\n" + body)`; genesis `prev_hash = sha256_hex("FCRM-GENESIS:" + case_id)`. `seq` is allocated optimistically: read head (`find_one sort seq desc`), insert `seq+1`; a `DuplicateKeyError` on the unique `{case_id, seq}` index retries (max 5) under a per-case `asyncio.Lock` in the process. `type` values are the P7 audit vocabulary (`case.created, workflow.started, document.stored, guard.input, guard.permit, proposal.*, guard.output, gate.decided, fieldset.resolved, precedent.matched, values.reused, owner.answered, fact.flagged, owner.corrected, gate.validation, case.submitted, triage.assigned, eval.recorded, screening.proposed, workflow.stage, agent.retry, score.computed, boundary.flagged, critic.findings, completeness.report, clarification.sent, agent.rerun, control.planned, gap.recorded, human.override, score.recomputed, recommendation.locked, pack.assembled, pack.approved, committee.question, committee.answer, vote.cast, vote.revealed, outcome.set, outcome.delivered, case.closed, chain.verified`) plus `agent.failed, human.handoff, case.unlocked, pack.superseded, vote.rerequested, no_majority, reflag.proposed, reflag.acknowledged, jurisdiction.published`.

Collections and enforcement: `audit_records` with `$jsonSchema` (`required: [case_id, seq, type, actor, what, payload, occurred_at, prev_hash, hash, hash_alg]`, `additionalProperties: false`, `validationLevel: strict`, `validationAction: error`), unique `{case_id, seq}`, index `{case_id, type}`; `proposals` (`_id = proposal_id`, append-only; a later version is a new document with `supersedes`); `verify_runs` (`{case_id, at, ok, head_hash}`; the `chain.verified` record is appended after a successful verify). Database user `fcrm_audit_writer` is created by `ops/mongo/init/02-users.js` with a custom role granting only `find`, `insert`, `createIndex`, `listIndexes` on `fcrm_audit` (no `update`, `remove`, `dropCollection`). S4 is the only process that holds that URI; a startup self-test attempts an `update_one` and must receive `Unauthorized` or the service refuses to become ready. `vote.cast` records never contain the vote value (only `{pack_id, round, member_id, cast_n_of_3}`); the value appears in `vote.revealed`.

### 2.5 S5 decision-service (`fcrm_decision`, db `fcrm_decision`, port 8005)

Purpose: the frozen pack, hidden votes with mandatory rationale, the tally and outcome, committee question threads and pack addenda (R6), re-votes, delegated outcomes. Never reveals a vote early; never calls a model.

| Role | Method · path | Request → Response |
|---|---|---|
| service (H1), analyst | POST `/packs` | service: `PackBuild{case_id, snapshot}` (the snapshot is a `PackView` body plus `checks` and `change_types`: `fcrm_contracts.api.decision.PackContent`) → `PackView{status: built}`, 201, or the live pack when the content is identical, 200; building never releases; 422 when the snapshot is not a pack body; 409 `pack_not_superseded` while a different pack is live, `case_decided` once the case has an outcome. Analyst: `{case_id}` only → the latest pack, or 409 `pack_not_built` |
| analyst | POST `/packs/{pack_id}/release` | `{}` → `PackView{status: released}`; 409 `release_checks_failed` naming the checks (`locked`, `all_items_reviewed`, `boundary_and_band_acknowledged`, `business_summary_present`); the roster comes from X1; outbox `pack.released` |
| committee | GET `/committee/queue` | `Page[CommitteeQueueItem]{case_id, title, change_types, band, band_label, analyst_name, received, pack_id, pack_version, your_vote, cast_count, quorum, status, outcome_label}`, one aggregation |
| committee, analyst | GET `/packs/{pack_id}` · `/cases/{case_id}/packs/latest` | `PackView` (committee: released packs only; analyst: any) with the addenda |
| committee, analyst | GET `/cases/{case_id}/packs` | `Page[PackSummary]` (both versions kept) |
| committee | POST `/packs/{pack_id}/votes` | `{vote, rationale (>= 20 chars)}` → 201 `VoteReceipt{vote_id, round, cast_n_of_3}`; the voter is the caller and the round is the pack's current one; 409 `already_voted`, `pack_not_released`, `pack_not_open`, `pack_superseded`, `pack_decided`; outbox `vote.cast` (no option) and, when the third lands, the tally (2.5.2) |
| committee, analyst | GET `/packs/{pack_id}/votes[?round=n]` | `VotesView{round, quorum, cast_count, revealed, members[{name, status, is_you, choice, rationale, cast_at}], mine, tally, outcome, outcome_label}`; until the round is tallied `revealed` is false and every choice but the caller's own is null |
| committee | POST `/packs/{pack_id}/threads` | `{text}` → 201 `CommitteeThread{id, from, question, at, answer}` (visible to all members and the analyst); outbox `committee.question_asked` |
| analyst | POST `/threads/{thread_id}/answer` | `{text, numbers_changed, unlock_reason?}` → `CommitteeThread`; `numbers_changed` requires `unlock_reason` of at least 20 characters, closes the pack to votes at once and triggers the R6 path; unchanged: an addendum, the pack's `content_hash` untouched; outbox `committee.answered` |
| committee, analyst | GET `/packs/{pack_id}/threads` | `Page[CommitteeThread]` |
| service (H1) | POST `/packs/{pack_id}/supersede` | `{reason}` → `SupersedeResult{voided_votes, rerequested[]}`: marks the pack superseded, voids its votes (kept), outbox `pack.superseded` and, when members had voted, `vote.rerequested` for the next version; idempotent |
| service (H1) | POST `/packs/{pack_id}/revote` | `{reason: no_majority}` → `RevoteResult{round, member_ids}`: confirms the round S5 opened when it tallied the split; 409 `no_revote_pending` otherwise; writes nothing |
| service (H1) | POST `/outcomes` | `OutcomeRecord{case_id, mode: delegated\|committee, outcome, pack_id?, decided_by?, reason?, conditions[], plain_summary?}` → `CaseOutcome`; committee: exactly the pack's decisive tally (409 `not_tallied`, `outcome_mismatch`); delegated: a person and a reason, never over an open committee pack; outbox `outcome.set`; idempotent |
| analyst, committee, service | GET `/cases/{case_id}/outcome` · `/packs/{pack_id}/outcome` | `CaseOutcome` · the latest round's `OutcomeSet` (409 `not_tallied` before a round is tallied) |

Every service `POST` honours `Idempotency-Key` (stored with its case in `idempotency_keys`, 24 h); every refusal is a 409 with its own code as the problem `type`.

#### 2.5.1 Pack (frozen snapshot)

`packs`: `_id (pack_)`, `case_id, version (1,2,…), built_at, built_by (H1 run_id), requested_by (analyst), status (built\|released\|superseded\|decided), snapshot` (verbatim `PackSnapshot`: `score{areas{...}, composite, band, route, boundary}`, `clauses[]` with quotes and refs, `gaps[]`, `controls[]` with ratings, evidence, stale flags, `overrides[]` (before/after/reason/by/at/record_seq), `planned[]`, `critic_findings[]` + analyst responses, `completeness`, `clarification_rounds[]` summary, `recommendation{text, outcome, edits_by_analyst}`, `conditions[]`, `business_summary`, `pins{...}`, `audit_head{seq, hash}`), `content_hash` (sha256 of canonical snapshot), `release_checks:[{id, ok, detail}]` (the four: `locked`, `all_items_reviewed`, `boundary_and_band_acknowledged`, `business_summary_present`), `released_at, released_to[], superseded_by?, decided_at?`. The snapshot is immutable: no route writes into it; `PATCH` does not exist. Addenda are separate documents referencing `pack_id`.

Why H1 assembles and S5 freezes: the pack needs S1 (case view) and S4 (ledger, head hash) data; H1 already holds both over HTTP, so H1 builds `PackBuild` and S5 validates, hashes and stores it. S5 therefore never reads another service.

#### 2.5.2 Votes, tally, outcomes (recorded decision)

`votes`: `_id (vote_)`, `pack_id, case_id, round, member_id, vote, rationale, cast_at, voided{at, reason}?`; unique `{pack_id, round, member_id}` (DB-level one vote per member per round); vote documents are never updated except `voided`. Quorum = 3 = `len(released_to)`; committee membership for a case = the three `fcrm_committee` users seeded (P7) **(proposed: configurable per unit later)**.

Tally runs inside the transaction that inserts the third non-voided vote of the current round: `counts = Counter(votes)`; **outcome = the option with ≥ 2 identical votes**; otherwise state `no_majority`. On an outcome: `tally` stored, `votes` revealed (`GET /votes` switches shape), `outcome.set` published with `mode: committee`, conditions activated (S1 via H1) if `approve_with_conditions`; the case closes. On `no_majority` (1-1-1, ADR-31): the round's outcome row is kept with `no_majority` set, its votes are revealed to the members with rationales (so the discussion is informed), a system thread is opened on the pack (`"No majority: 1 a / 1 b / 1 c. Discuss and re-vote (ADR-08)."`), `vote.no_majority` and `vote.rerequested` are published, and round 2 opens on the same pack in that same transaction; H1's `POST /packs/{id}/revote` only confirms it; nothing is auto-deferred, and the case stays in `decide` until a round produces a majority. Re-vote rounds are unbounded but each round is a hash-chained record; the analyst may also unlock (R6) if the discussion shows a number must change, which supersedes the pack and restarts voting on v2 round 1.

R6 path on `numbers_changed=true`: S5 stores the addendum, publishes `committee.answered{numbers_changed:true, unlock_reason}` → H1 moves the case `decide → review`, S1 records `case.unlocked`, the analyst corrects and re-locks, H1 builds pack v2, S5 `supersede` v1 (votes voided, kept), release → every cast vote is re-requested. Both packs remain readable.

Delegated close (R1): S1 `POST /cases/{id}/close` → outbox `case.close_requested` → H1 checks band ∈ {1,2}, lock, acknowledgements, Guard 5 → `POST /outcomes {mode: delegated}` → `outcome.set` → S1 stores outcome, activates conditions, stage `closed`; S4 records `outcome.set`, `case.closed`; H1 runs `GET /verify` and appends `chain.verified`.

### 2.6 X1 identity (`fcrm_identity`, db `fcrm_identity`, port 8000)

Clone of `evoqidentityservice` reduced to what the workbench needs; see section 8.1 for routes, claims and fixes. Collections: `users` (`_id = user id` e.g. `u-riyer`, `username, name, roles[], unit?, password_hash (bcrypt), enabled, created_at`), `refresh_tokens` (`jti, sub, exp, revoked`; TTL on `exp`), `signing_keys` (`kid, public_pem, created_at, active`).

---
## 3. H1 Caseflow (`fcrm_caseflow`, db `fcrm_harness`, health port 9100)

Original code, no workflow framework (D-10). One FastStream app with three consumers (case events, `agent.done`/`agent.failed`, `ingest.done`/`index.built`), a `Caseflow` class per case run, four cores as modules (`intake_core.py`, `assessment_core.py`, `decision_core.py`, `oversight_core.py`) plus `gate_manager.py`, `budget.py`, `routing.py` (R1–R6 evaluation), `eventstore.py`, `outbox` from `fcrm_common`. Target size ≈ 600–900 lines excluding tests. It never reasons.

### 3.1 CaseState enum and transitions

```python
class CaseState(StrEnum):
    INTAKE = "intake"                        # documents, extraction, PO confirmations, reuse, gap answers
    REVIEW_AND_SUBMIT = "review_and_submit"  # owner corrections; four submit checks
    TRIAGE = "triage"                        # R5 auto-assign
    SCREENING = "screening"                  # screening agent → R4 proposal → analyst confirm gate
    ASSESS = "assess"                        # research ∥ assessment → POST /score → boundary
    CRITIC = "critic"                        # critic agent
    COMPLETENESS = "completeness"            # narrative completeness report → analyst ask/proceed gate
    CLARIFICATION = "clarification"          # round open; owner answers → rerun on changed fields
    REVIEW = "review"                        # Gate A per item; rescore; R3 acks
    RECOMMEND = "recommend"                  # narrative draft → analyst edits → lock → pack build → release checks → release
    DECIDE = "decide"                        # committee votes / R6 / no_majority, or delegated close
    CLOSED = "closed"                        # terminal for assessment; oversight core acts on it
    HANDOFF = "handoff"                      # step failed after retry; analyst completes by hand (resumes to the saved state)
```

Sub-states are not enum values; they are flags in the run snapshot (`fast_lane`, `round_open`, `locked`, `pack_version`, `vote_round`). Oversight (conditions, re-flags, examiner) is `oversight_core` acting on `CLOSED` cases and, for re-flags, on open cases; it is not a state.

| From | Trigger (consumed) | Guard(s) | Action | To |
|---|---|---|---|---|
| — | `case.created` | — | open run (`run_id`, root span, pins from active versions), record `workflow.started` | INTAKE |
| INTAKE | `case.document_stored` | — | publish `ingest.run{doc}`; on `ingest.done` run Guard 1 result check; dispatch `agent.run intake` when all stored docs are extracted | INTAKE |
| INTAKE | `agent.done intake` | Guard 5 | write proposals (S4), push items to S1 (`PUT items`), resolve field set (S3), precedent match, open PO gates | INTAKE |
| INTAKE | `gate.decided po_confirm_category` (all) | — | `fieldset.resolved` record; if open gaps → `agent.run intake` (questions only) | INTAKE |
| INTAKE | `case.submitted` | four checks passed (S1 asserts; H1 re-reads `SubmitResult`) | record `case.submitted`; `eval.recorded` (intake grade request) | REVIEW_AND_SUBMIT→TRIAGE (immediate) |
| TRIAGE | internal | R5 | `GET /internal/analysts/load` → assign lowest open count (tie: alphabetical id) → `POST assign`; record `triage.assigned` | SCREENING |
| SCREENING | internal | budget reserve | `agent.run screening` | SCREENING |
| SCREENING | `agent.done screening` | Guard 5 | record `screening.proposed`; if `fast_lane` proposed → open gate `fast_lane_confirm`; else → ASSESS | SCREENING / ASSESS |
| SCREENING | `gate.decided fast_lane_confirm` | — | confirmed: `fast_lane=true`, dispatch `assessment` only; declined: full path | ASSESS |
| ASSESS | internal | budget reserve ×2 | `agent.run research` + `agent.run assessment` in parallel (fast lane: assessment only) | ASSESS |
| ASSESS | `agent.done research` / `assessment` (both) | Guard 5 | proposals → S4; items → S1; build `ScoreRequest` (no planned); `POST /score`; push snapshot; records `score.computed`, `boundary.flagged` if flagged | CRITIC |
| CRITIC | internal | budget | `agent.run critic` | CRITIC |
| CRITIC | `agent.done critic` | Guard 5 | push critic view; record `critic.findings` | COMPLETENESS |
| COMPLETENESS | internal | budget | `agent.run narrative{mode: completeness}` | COMPLETENESS |
| COMPLETENESS | `agent.done narrative` | Guard 5 | push completeness view + drafted questions; record `completeness.report`; open gate `completeness` | COMPLETENESS |
| COMPLETENESS | `gate.decided completeness{ask}` | one round only (D-16) | S1 has created the round; record `clarification.sent`; pause analyst clock | CLARIFICATION |
| COMPLETENESS | `gate.decided completeness{proceed}` | — | — | REVIEW |
| CLARIFICATION | `case.answered` | — | diff v(n−1)→v(n); Guard 1 on new docs via `ingest.run`; dispatch `research`+`assessment` with `scope: changed_fields`; record `agent.rerun`; planned control recorded not scored | CLARIFICATION |
| CLARIFICATION | `agent.done` (both reruns) | Guard 5 | new proposals supersede items whose inputs changed; `POST /score`; push snapshot | REVIEW |
| REVIEW | `gate.decided review_item` | Rule 6 | update item; if a `rating`/`control` changed → rebuild `ScoreRequest` from confirmed values, `POST /score`, record `human.override` + `score.recomputed`; band change or boundary → open gate `ack` (R3) | REVIEW |
| REVIEW | `case.retagged` | — | expand field set; dispatch research+assessment for the new domain only | REVIEW |
| REVIEW | `case.rerun_requested` | reason present | dispatch per scope; prior proposals kept with `superseded_by` | REVIEW |
| REVIEW | `case.gap_recorded` | — | record `gap.recorded`; dispatch `policy_gap` (oversight queue, non-blocking) | REVIEW |
| REVIEW | all items decided ∧ acks done | Guard 5 | dispatch `agent.run narrative{mode: recommendation}` | RECOMMEND |
| RECOMMEND | `agent.done narrative` | Guard 4 (PO summary restricted terms) | push recommendation draft + conditions; record `proposal.narrative`, `guard.output` | RECOMMEND |
| RECOMMEND | `case.locked` | — | record `recommendation.locked`; build `PackBuild` (S1 view + S4 head) → `POST /packs`; run four release checks; record `pack.assembled`, `gate.validation` | RECOMMEND |
| RECOMMEND | `pack.released` | checks ok | record `pack.approved`; evaluate R1/R2 from the latest band | DECIDE |
| RECOMMEND | `case.unlocked` | reason | record `case.unlocked`; `POST /packs/{v}/supersede` if a pack exists | REVIEW |
| DECIDE (band 1–2) | `case.close_requested` | R1, lock, acks, Guard 5 | `POST /outcomes{mode: delegated}` | DECIDE (awaits `outcome.set`) |
| DECIDE (band 3–4) | `vote.cast` | — | record `vote.cast` (count only) | DECIDE |
| DECIDE | `committee.question_asked` / `committee.answered{numbers_changed:false}` | — | records `committee.question`, `committee.answer` | DECIDE |
| DECIDE | `committee.answered{numbers_changed:true}` | unlock reason | as `case.unlocked` → pack v2 flow → `supersede` → `vote.rerequested` | REVIEW |
| DECIDE | `vote.tallied{outcome}` | — | record `vote.revealed`, `outcome.set`; `PUT /internal/cases/{id}/outcome` (activates conditions) | CLOSED |
| DECIDE | `vote.no_majority` | — | record `no_majority`; `POST /packs/{id}/revote` | DECIDE |
| DECIDE | `outcome.set` (delegated) | — | as above | CLOSED |
| CLOSED | internal | — | records `outcome.delivered`, `case.closed`; `GET /verify` → `chain.verified`; `POST /internal/precedents`; end root span | CLOSED |
| any | `agent.failed` (after retry) | — | open gate `handoff{step, raw_inputs_ref}`; record `agent.failed`, `human.handoff` | HANDOFF |
| HANDOFF | `gate.decided handoff{manual_output}` | Guard 4 shape on manual output | treat as `agent.done` with `actor=human` | previous state |

Illegal transitions raise `IllegalTransition` → message is acked, an `error` activity is written and an alert metric incremented; the run is never left half-applied because every transition is one Mongo transaction over `case_runs` + `run_events` + `gates` + `budgets` + `outbox`.

### 3.2 Gates (Guard 7)

`gates`: `_id (gate_)`, `case_id, run_id, gate_type, subject{kind, id}, required_role, opened_at, deadline?, status (open\|decided\|expired), decision, reason, decided_by, decided_at, before, after`. Gate types: `po_confirm_category`, `po_confirm_value`, `po_reuse`, `po_answer_gaps`, `fast_lane_confirm`, `review_item` (clause accept/remove; rating confirm/override; control confirm/override; critic finding explain/adjust), `ack` (boundary / band_change), `completeness` (ask / proceed), `lock`, `pack_release`, `vote` (one per member per round, lives in S5 but mirrored as a gate), `reflag_ack`, `handoff`, `unlock`. A gate is opened by H1 (record `gate.opened` in `run_events`, UI event via S1), decided through the owning service's route which publishes `gate.decided`; H1 matches `gate_id` (or `subject`), refuses decisions from the wrong role, and advances. `fcrm_gate_wait_seconds` is observed at decision.

### 3.3 Budget model (Guard 3)

```python
TIERS = {"cheap": Budget(tokens=6_000, wall_s=60, retries=1),
         "mid":   Budget(tokens=12_000, wall_s=120, retries=1),
         "strong":Budget(tokens=20_000, wall_s=180, retries=1),
         "judge": Budget(tokens=20_000, wall_s=180, retries=1)}   # seed's "strong + judge"
CASE_CAP_TOKENS = 120_000
```
`budgets`: `_id = run_id`, `case_id, cap_tokens, used_tokens, reserved_tokens, per_agent{agent:{runs, tokens, cost_usd, wall_ms}}, exhausted_at?`. Before dispatch: `reserve(tier.tokens × (retries+1))`; refuse when `used + reserved + request > cap` → open `handoff{reason: budget_exhausted}`. On `agent.done`/`agent.failed`: `settle(actual_tokens)`. Wall clock is enforced in H2 (`asyncio.wait_for(wall_s)` per attempt) and by the H1 lease (`agent_jobs.lease_until = dispatched_at + (wall_s × (retries+1)) + 60 s`); a reaper every 30 s marks expired leases `FAILED{reason: lease_expired}` and emits `agent.failed`. Usage comes from Pydantic AI `result.usage()` (request + response tokens); cost from the price table in `ai/models/tiers.yaml` (owned by design B) **(proposed)**.

### 3.4 Job dispatch, idempotency, retries, dead letters

- `agent_jobs`: `_id = job_id`, `case_id, run_id, agent, step_key, attempt, status (PENDING\|RUNNING\|DONE\|FAILED), payload_ref, input_hash, dispatched_at, lease_until, claimed_by, finished_at, usage, error`. `job_id = "job_" + ulid`; `idempotency_key = sha256(case_id|run_id|agent|step_key|input_hash)` with a unique index, so re-dispatching the same step with the same inputs is a no-op (returns the existing job).
- Message to H2 is the full `agent.run` envelope (payload = working memory, section 9), not a bare id; H2 claims `agent_jobs` by `find_one_and_update({_id, status: PENDING}, {status: RUNNING, claimed_by, lease_until})`; a lost claim means a duplicate delivery and the message is acked silently.
- Retry (resolves the fallback-first vs same-model-first inconsistency): **attempt 1 = `FallbackModel(primary, fallback)`** (transport-level failover inside the SDK, counted in usage); on Guard 4 rejection or timeout **attempt 2 = fallback model only, with the validation error appended to the prompt** (`agent.retry` record "fallback model"); a second failure → `agent.failed` → HANDOFF. Exactly one retry per the seed.
- Transport retries (queue level, section 4.5) are separate: a consumer crash before ack redelivers up to 3 times via the retry queue, then the message parks in `<queue>.dlq`. The DLQ is drained by `make dlq-replay` or by the ops UI; nothing is dropped.
- `agent.done` is applied once: `consumed_messages` dedupe by `message_id`, plus `agent_jobs.status` must be `RUNNING`.

### 3.5 Outbox and event store

- `outbox` (section 1.4) is written in the same transaction as `run_events`; the relay publishes to `case.events`/`agent.jobs`/`ingestion.jobs`/`context.jobs` with the envelope's `routing_key`.
- `case_runs` (snapshot): `_id = run_id`, `case_id, state, prev_state, version (event count), pins{...}, flags{fast_lane, round_open, locked, pack_id, pack_version, vote_round}, trace_id, root_span_id, opened_at, updated_at, closed_at`. `run_events` (append-only): `{run_id, seq, at, kind (transition\|gate_opened\|gate_decided\|job_dispatched\|job_done\|job_failed\|score\|budget\|error), state_from, state_to, message_id, data}`; unique `{run_id, seq}`. Replay = `Caseflow.rebuild(run_id)` folds `run_events` and must equal the snapshot (tested).
- Audit records in S4 are the *governed* trail; `run_events` is the harness's own ledger (debuggable, replayable). Every `run_events` row that matters to a human also produces an S4 record; the test `test_every_gate_has_audit_record` enforces the mapping table in `caseflow/audit_map.py`.

### 3.6 Re-tag, re-run, unlock, pack v2 (summary of effects)

| Event | Proposals | Items | Score | Pack/votes | Records |
|---|---|---|---|---|---|
| re-tag (analyst adds a change type) | new research/assessment proposals for the new domain | new items `proposed`; existing untouched | recomputed when confirmed | n/a (pre-lock only; 409 after lock) | `case.retagged`, `proposal.*`, `score.recomputed` |
| re-run (`scope: changed_fields`) | new proposals with `supersedes`; old kept | items whose `input_fields ∩ changed ≠ ∅` reset to `proposed`; confirmed others kept | recomputed from current confirmed values | n/a | `agent.rerun`, `proposal.*`, `score.recomputed` |
| re-run (`scope: full`, reason) | all re-proposed | all reset to `proposed`, prior decisions kept in `history` | recomputed | n/a | as above |
| unlock (reason) | — | editable again | unchanged until an item changes | pack superseded; cast votes voided (kept) | `case.unlocked`, `pack.superseded` |
| pack v2 | — | — | locked numbers | new pack, release checks, `vote.rerequested` to every member who had cast | `pack.assembled`, `pack.approved`, `vote.rerequested` |
| no majority | — | — | — | round n+1 on the same pack | `no_majority`, `vote.rerequested` |

---

## 4. Event and queue contracts

### 4.1 Topology (RabbitMQ 4, all durable, publisher confirms on, `x-queue-type: quorum` **(proposed)**)

| Exchange (type) | Routing keys | Queues (binding) |
|---|---|---|
| `case.events` (topic) | `case.*`, `gate.decided`, `pack.*`, `vote.*`, `committee.*`, `outcome.*`, `condition.*`, `jurisdiction.*`, `index.*`, `ingest.done`, `ingest.failed` | `caseflow.events` (`#`), `case-service.events` (`outcome.*`, `vote.rerequested`, `pack.released`, `index.built`, `gate.opened`: S1 keeps the `handoff` gates), `policy-service.events` (`index.*`), `evals.events` (`gate.decided`, `outcome.*`) |
| `agent.jobs` (direct) | `agent.run`, `agent.done`, `agent.failed` | `agent-worker.run` (`agent.run`), `caseflow.agent-results` (`agent.done`, `agent.failed`) |
| `ingestion.jobs` (direct) | `ingest.run` | `ingestion-worker.run` |
| `context.jobs` (direct) | `index.build` | `context-worker.run` |
| `retry` (direct) | `<queue>` | `<queue>.retry` with `x-message-ttl: 30000`, `x-dead-letter-exchange: ""`, `x-dead-letter-routing-key: <queue>` |
| `dlx` (direct) | `<queue>` | `<queue>.dlq` (parked; no TTL) |

Every work queue declares `x-dead-letter-exchange: dlx` and `x-dead-letter-routing-key: <queue>`. Prefetch: caseflow 16, agent-worker 2 per replica (one model call at a time per slot), ingestion 2, context 4 **(proposed)**.

### 4.2 Envelope (`fcrm_contracts.events.Envelope`)

```json
{ "id": "evt_01JAB…",                "type": "gate.decided",            "version": 1,
  "case_id": "FCRM-2026-0151",       "run_id": "run_01J…",              "occurred_at": "2026-10-01T15:02:11.482Z",
  "producer": "S1",                  "correlation_id": "run_01J…",      "causation_id": "evt_01J…",
  "traceparent": "00-0af3c1e9d7b24c6e…-…-01", "tracestate": null,
  "idempotency_key": "sha256…",      "actor": {"id": "u-riyer", "role": "fcrm_analyst"} ,
  "payload": { … } }
```
AMQP properties mirror the envelope: `message_id = id`, `type`, `correlation_id`, `content_type = application/json`, `delivery_mode = 2`, headers `traceparent`, `tracestate`, `x-attempt` (int), `x-schema = "fcrm.events/<type>@<version>"`. `correlation_id` is always the case `run_id`, so one trace per case run (the root span is created at `workflow.started`; every consumer continues the propagated context and adds a span link to the root when it starts a new segment after a human gate).

### 4.3 Payload schemas (Pydantic models in `fcrm_contracts.events.payloads`; all fields required unless `?`)

| Type (producer) | Payload |
|---|---|
| `case.created` (S1) | `{owner_id, unit, title?}` |
| `case.document_stored` (S1) | `{doc_id, name, bucket, key, sha256, size_bytes, content_type, raised_in_round?}` |
| `case.submitted` (S1) | `{submission_version:1, change_types[], filled, required, checks:[{id, ok}]}` |
| `case.answered` (S1) | `{round, submission_version, changed_field_ids[], added_document_ids[], thread_ids[]}` |
| `case.retagged` (S1) | `{change_type, reason, by}` |
| `case.rerun_requested` (S1) | `{reason, scope}` |
| `case.clarification_sent` (S1) | `{round, thread_ids[], question_count}` |
| `case.control_planned` / `case.gap_recorded` (S1) | `{planned_control{...}}` / `{gap_id, text, scope}` |
| `case.locked` / `case.unlocked` (S1) | `{by}` / `{by, reason}` |
| `case.close_requested` (S1) | `{outcome, reason, plain_summary, by}` |
| `gate.decided` (S1, S5) | `{gate_id?, gate_type, subject{kind, id}, decision, reason?, value?, before?, after?, decided_by, role}` |
| `pack.released` (S5) | `{pack_id, version, released_to[], content_hash}` |
| `vote.cast` (S5) | `{pack_id, round, member_id, cast_count, quorum}` — never the vote value |
| `vote.tallied` (S5) | `{pack_id, round, outcome, counts{option:n}, votes:[{member_id, vote, rationale}]}` |
| `vote.no_majority` (S5) | `{pack_id, round, counts}` |
| `vote.rerequested` (S5) | `{pack_id, version, round, member_ids[], reason}` |
| `committee.question_asked` / `committee.answered` (S5) | `{pack_id, thread_id, member_id, text}` / `{pack_id, thread_id, addendum_id, by, numbers_changed, unlock_reason?}` |
| `outcome.set` (S5) | `{outcome, mode, decided_by, pack_id?, reason?, conditions:[{id, text, owner_id, due_label, due_date?}], plain_summary}` |
| `condition.due_soon` / `condition.overdue` (H1) | `{condition_id, due_date, owner_id}` |
| `jurisdiction.list_published` (S3) | `{version, added[], removed[], fixture}` |
| `ingest.run` (H1, S3) | `{job_id, source{bucket, key}, target{bucket, key_prefix}, purpose: case_document\|corpus_source, doc_id?, source_id?, guard1: true}` |
| `ingest.done` / `ingest.failed` (H3) | `{job_id, doc_id?, canonical_key, pages, structure{pages[], headings[]}, guard1{pii_masked, injection_hits, quarantined}, warnings[]}` / `{job_id, code, detail}` |
| `index.build` (S3) | `{job_id, index: policy_clauses\|precedent_cases\|controls, version, source_keys[] \| records_key, mode: full\|incremental}` |
| `index.built` / `index.failed` (H4) | `{job_id, index, version, vector_count, fixture_key?}` / `{job_id, index, code, detail}` |
| `agent.run` (H1) | `{job_id, agent, tier, model, fallback, prompt_id, prompt_v, attempt, budget{tokens, wall_s}, permits_role, working_memory{...section 9}, output_schema, tools_allowed[], retry_context?{error}}` |
| `agent.done` (H2) | `{job_id, agent, attempt, model_used, usage{request_tokens, response_tokens, total_tokens, cost_usd}, wall_ms, guard_results:[{guard, ok, detail}], proposals:[Proposal…], tool_calls:[{tool, args_hash, ms, ok}]}` |
| `agent.failed` (H2) | `{job_id, agent, attempt, reason: guard4_rejected\|timeout\|model_error\|permit_denied\|budget, detail, usage, raw_output_ref?}` |

### 4.4 Versioning and compatibility

- `version` is an integer per `type`; the AMQP header `x-schema` carries `fcrm.events/<type>@<version>`.
- Additive changes (new optional fields) keep the version; a removed/renamed/retyped field bumps it. Consumers accept `version ≤ supported` and must tolerate unknown fields (`model_config = ConfigDict(extra="ignore")` on payloads; `extra="forbid"` on the envelope).
- Every payload model lives in `fcrm_contracts` with a JSON Schema export (`make contracts` writes `docs/architecture/events/*.schema.json`), and `tests/contracts/test_event_fixtures.py` validates the committed fixtures `fcrm_contracts/fixtures/events/*.json` against the models. A producer may not publish a type that is not in `EVENT_CATALOGUE` (lock test like `expected_tools.txt`).

### 4.5 Delivery rules

At-least-once everywhere; consumers are idempotent by `message_id` (`consumed_messages`) and by domain keys (`agent_jobs.idempotency_key`, `gates`, `votes` unique indexes). Manual ack after the transaction commits. On handler exception: if `x-attempt < 3` republish to `<queue>.retry` with `x-attempt+1` and ack the original; else publish to `dlx` and ack. Poison detection (`ValidationError` on the envelope) goes straight to the DLQ. Ordering is per case: Caseflow serialises handling per `case_id` with an in-process `asyncio.Lock` keyed by case id and a Mongo `leases` row (`lease:case:{id}`) across replicas.

---
## 5. S2 scoring: engine, schemas, routing data, golden tests

### 5.1 Pure functions (`fcrm_scoring/engine.py`, mypy strict, no I/O, `Decimal` for rounding)

```python
def inherent(L: int, I: int) -> int:                       # 1..25
    return L * I
def area_ce(controls: list[RatedControl], catalogue: dict[str, Decimal]) -> Decimal | None:
    rated = [c for c in controls if not c.planned and c.rating is not None]   # Rule 7
    if not rated: return None
    wsum = sum(catalogue[c.id] for c in rated)
    return sum(catalogue[c.id] / wsum * Decimal(str(c.rating)) for c in rated)   # weights renormalised over matched controls
def retention(ce: Decimal | None, m: Model) -> Decimal:
    if ce is None: return Decimal(1)
    return max(m.retention_floor, Decimal(1) - m.retention_factor * ce / Decimal(10))   # full precision, floor 0.40
def residual(inh: int, ret: Decimal) -> Decimal:
    return Decimal(inh) * ret
def area_weights(in_scope: list[str], m: Model) -> dict[str, Decimal]:
    tot = sum(m.domain_weights[a] for a in in_scope)
    return {a: m.domain_weights[a] / tot for a in in_scope}            # 0.36/0.80=0.45, 0.24/0.80=0.30, 0.20/0.80=0.25
def composite(residuals: dict[str, Decimal], weights: dict[str, Decimal]) -> Decimal:
    raw = sum(residuals[a] * weights[a] for a in residuals)
    return raw.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)          # round once, before band lookup
def band_of(c: Decimal, m: Model) -> BandDef:
    return next(b for b in m.bands if b.min <= c <= b.max)             # bands are contiguous at 0.1 granularity
def boundary(c: Decimal, b: BandDef, m: Model) -> Boundary:
    up   = (next_band.min - c) if b.band < 4 else None                 # distance to the next band's lower edge
    down = (c - b.min)         if b.band > 1 else None                 # distance to the edge just crossed
    d, direction = min(((up,"above_is_next"),(down,"below")) filtering None, key=dist)
    return Boundary(flag = d <= m.boundary_flag, distance = d.quantize("0.1"), direction, other_band)
```

Precision: CE, retention and residual are carried at full precision (seed recompute matches all 80 cases only this way); the response *reports* `ce` to 2 dp, `retention` to 2 dp, `residual` to 1 dp, `contribution` to 2 dp and `composite` to 1 dp, exactly as the seed stores them (`ce 7.59, retention 0.54, residual 10.9, contribution 4.9/3.07/1.83`). CLAUDE.md's "CE rounded to 1 decimal" is a display rounding (7.6 on screen), not an input to retention.

Boundary convention, verified against all 18 flagged seed cases: 9.8 → 0.3 (to 10.1), 10.4 → 0.3 (above 10.1), 5.0 → 0.1, 5.1 → 0.0, 5.6 → 0.5 (flagged, rule is `≤ 0.5`), 2.1 → 3.0 unflagged.

Routing (R1/R2) is *not* computed in S2 beyond `route = band.route` from the band table; the decision of what a route means is H1's (`routing.py`).

### 5.2 `ScoringModelVersion` (collection `scoring_model_versions`, `_id = version`)

```json
{ "_id": "v3", "status": "active", "owner": "FCRM Analytics", "approved": "2026-08-12", "published_at": "…", "note": "…",
  "likelihood_scale": {"1":"Remote","2":"Unlikely","3":"Possible","4":"Likely","5":"Almost certain"},
  "impact_scale": {"1":"Minimal","2":"Minor","3":"Moderate","4":"Major","5":"Severe"},
  "retention_factor": "0.6", "retention_floor": "0.4",
  "domain_weights": {"customer":"0.20","product":"0.20","geography":"0.24","channel":"0.36"},
  "bands": [{"band":1,"min":"0.0","max":"5.0","route":"analyst","label":"Low"},
            {"band":2,"min":"5.1","max":"10.0","route":"analyst","label":"Moderate"},
            {"band":3,"min":"10.1","max":"17.0","route":"committee","label":"Elevated"},
            {"band":4,"min":"17.1","max":"25.0","route":"committee","label":"High"}],
  "boundary_flag": "0.5", "rounding": "composite rounded to one decimal before band lookup", "planned_controls_scored": false,
  "control_catalogue_version": "v9" }
```
Validation on publish: weights sum to 1.00 ± 0.001; bands contiguous (`next.min == prev.max + 0.1`) and cover 0–25; floor ∈ [0.3, 0.6]; the active version cannot be edited (a new version is published and activated). Decimals are strings in JSON and `Decimal` in Python.

### 5.3 Routing rules schema (collection `routing_rules_versions`, `_id = version`; evaluated by `fcrm_caseflow/routing.py`)

```json
{ "_id": "v4", "status": "active", "rules": [
  {"id":"R1","kind":"route","when":{"band_in":[1,2]},"then":{"decide_by":"analyst_delegated"},"text":"Band 1 or 2: analyst closes under delegated authority"},
  {"id":"R2","kind":"route","when":{"band_in":[3,4]},"then":{"decide_by":"committee","quorum":3,"majority":"simple_identical","hidden_until_all_cast":true,"rationale_required":true},"text":"…"},
  {"id":"R3","kind":"acknowledge","when":{"boundary_flag":true,"or_band_changed":true},"then":{"gate":"ack","before":"release"},"text":"…"},
  {"id":"R4","kind":"fast_lane","when":{"only_change_types_in":[["vendor_onboarding"],["new_feature"]],"vendor_renewal_or_feature":true,"no_new":["geography","segment","product"],"annual_volume_usd_lt":5000000},"then":{"propose":"fast_lane","confirm_by":"analyst","expected_band":1},"text":"…"},
  {"id":"R5","kind":"triage","then":{"assign":"analyst_with_fewest_open_cases","tie_break":"lowest_user_id"},"text":"…"},
  {"id":"R6","kind":"committee_question","then":{"thread_visible_to":"all_members","answer_as":"pack_addendum","on_number_change":["unlock_with_reason","pack_v2","rerequest_all_cast_votes"]},"text":"…"} ] }
```
The `text` is the P7 wording verbatim; `when/then` are the machine form H1 evaluates. `routing.py` exposes `decide_by(band)`, `needs_ack(score_before, score_after)`, `fast_lane_eligible(case)` (used to *validate* the screening agent's proposal, never to replace it), `triage(load_map)`, `on_committee_answer(numbers_changed)`. A case pins `routing_v` at `workflow.started`.

### 5.4 `POST /score`

Request (`ScoreRequest`):
```json
{ "case_id": "FCRM-2026-0151", "model_version": "v3", "controls_version": "v9", "purpose": "proposed|confirmed|simulate",
  "areas": { "channel":   {"L":4, "I":5, "controls":[{"id":"CTL-T01","rating":7.6,"evidence":"DD report, audit 2023-11, domestic scope","evidence_date":"2023-11-15"}, {"id":"CTL-T02","rating":6.8,…}, {"id":"CTL-T03","rating":7.0,…}, {"id":"CTL-T04","rating":8.4,…}]},
             "geography": {"L":4, "I":4, "controls":[…]}, "product": {"L":3, "I":4, "controls":[…]} } }
```
Validation (422 on failure): `L, I ∈ 1..5`; `rating ∈ 0..10`; control ids exist in `controls_version` and belong to the area's domain; no `planned: true` entries (Rule 7; H1 filters, S2 refuses); at least one in-scope area; areas ⊆ the four domains.

Response (`ScoreResponse`), shaped exactly like the seed's `score` object plus provenance:
```json
{ "composite": 9.8, "band": 2, "band_label": "Moderate", "route": "analyst",
  "boundary": {"flag": true, "distance": 0.3, "direction": "below_next_edge", "edge": 10.1, "other_band": 3},
  "areas": { "channel": {"L":4,"I":5,"inherent":20,"ce":7.59,"retention":0.54,"residual":10.9,"weight":0.45,"contribution":4.9,
                         "controls":[{"id":"CTL-T01","rating":7.6,"weight_norm":0.35,"stale":true,"evidence_date":"2023-11-15"},…]},
             "geography": {…"ce":6.0,"retention":0.64,"residual":10.2,"weight":0.30,"contribution":3.07}, "product": {…"contribution":1.83} },
  "model_version": "v3", "controls_version": "v9", "computed_at": "…", "engine_version": "fcrm-scoring 1.0.0", "input_hash": "sha256…" }
```
`stale` = `evidence_date` older than 18 months **(proposed; prototype's `evidenceRecencyMonths`)** relative to `computed_at`. `/score` is idempotent (same input → same output, `input_hash` lets H1 skip duplicate records). The scoring call is logged (metric `fcrm_score_requests_total{purpose}`) but S2 stores nothing.

### 5.5 Golden tests (`src/services/scoring/tests/`)

1. `test_demo_before`: the seed's `demo.score_before.areas` inputs → `composite 9.8, band 2, boundary.flag true, distance 0.3`, area values `ce 7.59/6.0/6.5`, `retention 0.54/0.64/0.61`, `residual 10.9/10.2/7.3`, `contribution 4.9/3.07/1.83`.
2. `test_demo_after_override`: CTL-T01 7.6 → 4.5 → channel `ce 6.51, retention 0.61, residual 12.2, contribution 5.49`; `composite 10.4, band 3, route committee, boundary.flag true (0.3 above 10.1)`.
3. `test_all_80_seed_cases`: for every `cases[]` row, `score(inputs)` equals the stored `composite`, `band`, `route`, `boundary_flag`, `boundary_distance`; band distribution 19/49/11/1.
4. `test_band_edges`: 5.0→1, 5.1→2, 10.0→2, 10.1→3, 17.0→3, 17.1→4; composite 5.04 rounds to 5.0 (band 1), 5.05 to 5.1 (band 2).
5. `test_floor`: CE 10 → retention 0.40 exactly; CE 0 → 1.00.
6. `test_planned_ignored`: a planned control changes nothing; `planned: true` in the request → 422.
7. `test_weights_renormalise`: three in-scope areas → 0.45/0.30/0.25; one area → 1.0 (case 0140: `ce 8.08, residual 2.1, composite 2.1, band 1, distance 3.0`).
8. Property tests (hypothesis): raising any control rating never raises the residual; adding a rated control with rating ≥ area CE never raises the residual; composite is monotone in L and I; `score` is deterministic across 1,000 random inputs (same hash).
9. Startup check (S2 lifespan, `SEED_RECOMPUTE_ON_START=true`): recompute all 80 seed cases and refuse readiness on any mismatch (the D-57 rule).

---

## 6. S3 policy: corpus, clauses, controls, jurisdictions, precedents, search

### 6.1 Data

- Corpus v14 = 9 sources, 197 clauses. P7 ships the nine sources as Markdown (`fcrm_seed/corpus/<source_id>.md`, synthetic text for the four internal standards and cited extracts for the five public ones, each clause under a numbered heading `### §3.1 …` or `### FFIEC-TPPP-04 …`) **and** `clauses.json` with the 197 clause records `{clause_id, source_id, title, text, ref, page, line_start, line_end, risk_areas[], trigger_types[], jurisdictions[]}`. S3 seeds `clauses` from `clauses.json` (deterministic; the agent quotes these verbatim), H4 embeds the same records into `policy_clauses`; the Markdown files are ingested by H3 so `document_reader` and page:line citations work for policy text too. The clause segmenter (7.2) is exercised on uploaded case documents and on new corpus sources (`POST /corpus/sources`), which become `v15`.
- Controls inventory v9 (24 controls, weights per domain summing to 1.00) from `seed.controls`; jurisdiction lists `v2026-06` (active) and `v2026-10-fixture` (stored `fixture: true`, inactive until the analyst activates it in the demo); field set v6; precedents from the 65 closed seed cases (`title, change_types, domains, geographies, closed_date, outcome, band, control_ratings` → `precedent_cases`).

### 6.2 Search API

`POST /policy/search`:
```json
{ "query": "payout agent due diligence corridor", "top_k": 8,
  "filters": {"corpus_version": "v14", "trigger_types": ["vendor_onboarding","new_geography"], "risk_areas": ["channel","geography"], "jurisdiction": "MX"},
  "mode": "hybrid|vector|text" }
```
→ `{ "results": [{"clause_id":"TP71-2.3","source_id":"INT-TP-7.1","title":"…","text":"<verbatim>","ref":"Standard 7.1 §2 · v2025-11","page":4,"line_start":88,"line_end":93,"score":0.83,"vector_score":0.81,"text_score":0.42,"risk_areas":[…]}], "corpus_version":"v14","index_version":"v14","mode":"hybrid","embedding_model":"amazon.titan-embed-text-v2:0" }`.

Hybrid = Reciprocal Rank Fusion (`k=60`) of the S3 Vectors hits (`top_k×3`, filters pushed down) and a Mongo `$text` query over `clauses` (`{corpus_version, risk_areas $in, trigger_types $in}` filter, `textScore` sort, `top_k×3`), deduplicated by `clause_id`. Returned `text` always comes from Mongo (the system of record), never from vector metadata, so Guard 4's quote check compares against the stored clause.

`POST /precedents/search`: `{query (case summary text), filters:{trigger_types[], risk_areas[], closed_after?, closed_before?, outcome?}, top_k:5}` → `{results:[{case_id, title, similarity, closed_date, outcome, band, control_ratings{CTL-T01: 4.5,…}, reusable_fields:[{field_id, value}]}], precedent_avg:{CTL-T01: 4.9}}` (the agent reports the mean; the critic re-derives it from the five). `reusable_fields` are fetched from S1 `GET /internal/cases/{id}/record` for closed cases and exclude outcome-bearing fields (intake reuse shows no outcome, matching the prototype's rule).

### 6.3 Three indexes (S3 Vectors; bucket `fcrm-vectors` **(proposed)**, region from `AWS_REGION`)

| Index | Record key | Metadata (filterable unless noted) | Built from |
|---|---|---|---|
| `policy_clauses` | `{corpus_version}:{clause_id}` | `index_kind=clause, corpus_version, clause_id, source_id, risk_area` (comma-joined list), `trigger_type` (comma-joined), `jurisdiction`, `page`; non-filterable `text, title, section (= ref)` | `clauses.json` / new sources |
| `precedent_cases` | `{case_id}` | `index_kind=precedent, case_id, trigger_type, risk_area, jurisdiction, closed_date` (epoch days, int), `outcome, band` | S3 `precedents` on `case.closed` |
| `controls` | `{controls_version}:{control_id}` | `index_kind=control, controls_version, control_id, risk_area` | inventory version |

Local/offline: `VECTOR_BACKEND=memory` + `EMBEDDING_PROVIDER=dummy` (evoq_context's deterministic vectors) in both H4 and S3; H4 writes the in-memory store's `dump_fixture()` to MinIO `vectors/{index}/{version}.json` and publishes `index.built{fixture_key}`; S3 loads the fixture into its own `InMemoryVectorStore` on startup and on `index.built`. With AWS credentials: `VECTOR_BACKEND=s3vectors`, `EMBEDDING_PROVIDER=bedrock` in both. Same code path, one switch.

### 6.4 Index Contract metadata keys to add (`evoq_context/vector/contract.py` and `fcrm_contracts/index_contract_v2.schema.json`)

`FILTERABLE_KEYS += {"index_kind", "corpus_version", "clause_id", "source_id", "control_id", "controls_version", "case_id", "trigger_type", "risk_area", "closed_date", "outcome", "band"}`; `SOURCE_TYPES += ("policy", "precedent", "control")`; `CHUNK_STRATEGIES += ("clause_units",)`; schema `additionalProperties:false` updated accordingly; `_KR_METADATA_KEYS` pass-through extended. `NON_FILTERABLE_KEYS` stays at its 10-key cap (`ref` is carried in `section`). The clause text is stored in `text` (non-filterable) only for diagnostics; S3 reads it from Mongo.

---

## 7. Ingestion (H3) and context (H4) workers

### 7.1 H3 ingestion-worker (`fcrm_ingestion_worker` + `evoq_ingestion` clone)

Cloned: `aeoingestionjob/evoq_ingestion/` (engine, tests, the `contracts/` subset `canonical_document.md`, `fixtures/markers.md`, `fixtures/corpus`), plus the host skeleton (`app.py`, `evoqqueue`, `jobs/runner.py`, `drivers/mongo_repo.py`, `applogs`, `apptracer`) rewritten onto `fcrm_common`. Adaptation steps, in order:

1. uv `pyproject` (`requires-python >=3.13`, pinned deps, `inprocess` extras only), ruff/mypy `py313`, fix the `Path(__file__).parents[3]` fixture paths to the in-repo `contracts/` copy, enforce `--cov-fail-under=85`.
2. Drop `sources/{jira,confluence,_http}.py`, `renderers/{adf,html}_markdown.py`, the MCP loader, the summariser and the 7 Atlassian codes; prune their tests and corpus rows; `UPSTREAM.md` lists every deletion.
3. **page:line offsets:** in `GfmRenderer.render`, record the output line of each `PageBreak(n)`, `SheetBreak`, `SlideBreak` (same mechanism as `heading_lines`) and emit `structure.pages: [{n, start_line, end_line}]`; update `canonical_document.md`, bump `RENDERER_VERSION` to `"3"`, re-pin goldens. A citation is `source_ref = {doc_id, page, line}` where `line` is 1-based within the page (`abs_line = pages[n].start_line + line`); `fcrm_tools.document_reader.read(doc_id, page, line_from, line_to)` slices the canonical Markdown accordingly.
4. Pydantic 2 boundary models in `fcrm_contracts.documents` (`CanonicalDocumentModel` via `TypeAdapter` over the dataclass) so services never import the engine.
5. Host: messages are `ingest.run` envelopes (not bare ids); `jobs/runner.py` claims by `job_id` with a lease and **keeps the status the job wrote** (no unconditional COMPLETED); DLQ/retry from `fcrm_common.events`; UUID/ULID job ids; `PDF_ENGINE` default `pdfplumber` (the engine default, not the host override); `.dockerignore`; non-root; healthcheck.
6. Guard 1 integration: after extraction, `fcrm_guardrails.input.scan(markdown)` runs presidio PII masking (entities `PERSON, EMAIL_ADDRESS, PHONE_NUMBER, US_SSN, US_BANK_NUMBER, CREDIT_CARD, IBAN_CODE`; names of seeded users and the partner entity are allow-listed via `thresholds.yaml` so "Priya Nair, Payments" survives as a field value) and the injection pattern list (`ai/guardrails/injection_patterns.txt`, 50 red-team samples in `evals/guardrails/`); hits → `guard1.injection_hits`, file `quarantined` when any hit is severity `high`; result goes into `ingest.done`. Output: masked canonical Markdown to `canonical/{case_id}/{doc_id}.md` and the unmasked original stays only in `uploads/` (never read by an agent).
7. Failure codes keep `lower_snake`; `scanned_pdf_no_ocr` surfaces to the PO as "this file is a scan; please upload a text version" (OCR out of scope, D-92).

### 7.2 H4 context-worker (`fcrm_context_worker` + `evoq_context` clone)

Cloned: `aeocontextjob/evoq_context/` engine and tests; host rewritten onto `fcrm_common`. Steps:

1. uv/py313 as above; pin `aioboto3>=15.5`, `botocore>=1.40` (s3vectors model present); clear or relax the ruff backlog (`per-file-ignores` for the clone, recorded in `UPSTREAM.md`).
2. **Synthesis off switch:** `ContextConfig.synthesis_enabled: bool = False` (env `CONTEXT_SYNTHESIS_ENABLED`); when false `build_context` runs Reconcile → SeedSnapshot → StoreWrite → VectorWrite → Manifest → SaveState → Publish and skips Analysis, Render (`context.md`) and the inventory. No Bedrock chat call ever happens in TrustOps builds; `LLMClient.complete` is never invoked (asserted by a test with a failing fake).
3. **Clause segmenter:** `chunking/segmenters/clause.py::ClauseSegmenter` splits on clause headings (`^#{2,4}\s+(§\s*\d+(\.\d+)*|[A-Z][A-Z0-9-]+-\d+(\.\d+)*|\d+(\.\d+)+)\b`) and keeps each clause whole (no token packing across clauses; `HardMaxGuard` still applies); registered in `SEGMENTERS`, `KNOWN_STRATEGIES` (`clause_units`), `DEFAULT_RULES` for profile `policy` (set by `profile_hints.kind == "policy"`), `contract.CHUNK_STRATEGIES`, the JSON schema enum, with fixtures. Enricher adds `clause_id` from the heading.
4. **Filters:** `filters: dict | None` parameter on `search()`, `get_deep_context()` and the CLI; `get_deep_context` no longer hard-codes `{"project": project}` but merges it; filter operator syntax is the in-memory store's (`$eq $in $gte $lte $and`), translated to S3 Vectors filter JSON in `S3VectorsStore.query`.
5. **Three indexes:** `S3VectorsStore(config, project, *, index_name: str | None = None)` and the same for `InMemoryVectorStore`; `create_vector_store(config, project, index_name=...)`; the worker holds one store per index. `project` stays `"fcrm"` in metadata.
6. **Embeddings:** keep `LLMClient` embeddings over the LiteLLM SDK for Bedrock Titan v2 (`bedrock/amazon.titan-embed-text-v2:0`, dimension 1024) — the one LiteLLM use the build plan allows (BP §11); the `dummy` provider for offline. S3 computes query embeddings with the same client (shared `fcrm_context_worker.embeddings` module imported by S3 — a thin wrapper, not the engine).
7. **S3 Vectors hardening:** cache `ensure_index()` per process (call once, not per upsert); fix `expand_parents` to use `get_vectors` by key (via the `evoq_vectors` read client pattern) instead of `query(embedding=[])`; make `top_k` configurable up to the backend cap (30); `create_vector_bucket` optional on `ensure_bucket()` (idempotent, needs `s3vectors:CreateVectorBucket`); explicit `aioboto3.Session(region_name=AWS_REGION)`; retries with backoff on `ThrottlingException`/5xx (max 4, 0.5 s × 2^n jitter); an integration test marked `live_aws` that runs when `S3_VECTORS_BUCKET` is set.
8. Host job: consumes `index.build`; modes `full` (delete index contents by manifest keys, re-embed) and `incremental` (upsert new keys); writes `vector_manifest.json` per index/version to MinIO `vectors/{index}/{version}/`; publishes `index.built` with `vector_count` and (memory backend) `fixture_key`; runner keeps the job's own final status; DLQ/retry.

---
## 8. Identity (X1) and OPA (X2)

### 8.1 Identity routes, claims, fixes

| Method · path | Request → Response | Auth |
|---|---|---|
| POST `/login` | `{username, password}` → `{access_token, token_type:"Bearer", expires_in, role, user{id, name, roles, unit}}` and sets the httpOnly `fcrm_refresh` cookie (`SameSite=Strict`, `Path=/api/fcrm/auth`); the refresh token is also returned in the body for non-browser clients | none; bcrypt check; rate limit 10/min per IP at nginx |
| POST `/refresh` | cookie or `{refresh_token}` → new access token (+ rotated refresh cookie); old `jti` revoked | none; token `typ` must be `refresh` |
| POST `/logout` | cookie or `{refresh_token}` → 204, clears the cookie | revokes `jti` |
| GET `/me` | → `{id, username, name, roles, unit}` | any role (alias of `/users/me`) |
| POST `/core/validate` | bearer access token → `{sub, roles, name, exp, typ}` | the AEO contract kept for nginx `auth_request` and the cloned interceptor; **rejects `typ != "access"`** |
| GET `/core/userinfo` | → same as validate | |
| GET `/.well-known/jwks.json` | → JWKS (RS256 public keys, `kid`) | none; cache-control 10 min |
| GET `/users/me` | → `{id, username, name, roles, unit}` | any role |
| GET `/users?role=` | → `[{id, name, role}]` | analyst/committee/service (committee membership for packs, assignee names) |
| GET `/healthz` · `/readyz` | | none |

Token (RS256, PyJWT): claims `{sub: "u-riyer", roles: ["fcrm_analyst"], name: "Rahul Iyer", typ: "access", iat, exp (15 min), jti, iss: "fcrm-identity", aud: "fcrm"}`; refresh tokens carry `{sub, typ: "refresh", iat, exp (8 h), jti}` only. All identity routes live under `/api/fcrm/auth` (so the AEO contract path becomes `/api/fcrm/auth/core/validate`). No user document is copied into the token. Services verify locally against the JWKS (`fcrm_common.auth`), so no per-request hop; `/core/validate` remains for nginx and compatibility.

Fixes applied to the clone (each listed in `UPSTREAM.md` and `docs/governance/aeo-fixes.md`): (1) refresh tokens are no longer accepted as access tokens (`typ` enforced in every validator); (2) the OTP flow and its plaintext OTP log line are removed entirely (password login replaces it); (3) hard-coded secret fallbacks removed; `JWT_PRIVATE_KEY_PEM` (or `JWT_KEY_FILE`) and `DEMO_USERS_PASSWORD_HASH` are required and the process exits at startup without them; (4) `.get("username")` KeyError path removed (claims are minimal); (5) python-jose → PyJWT, HS256 → RS256 with key rotation support (`signing_keys`); (6) MSAL/Entra routes removed from the build (kept in `UPSTREAM.md` as the SSO path for later); (7) starlette-session and Redis dependency removed (`REDIS_ENABLED` gone); (8) `print()` → logger; CORS restricted to the gateway origin; non-root image with healthcheck; (9) users seeded from P7 `users` (11 people) into `users` with roles `fcrm_po | fcrm_analyst | fcrm_committee` and one shared demo password hash from env; (10) a `fcrm_service` service-account token minted by `POST /internal/service-token` (`X-Internal-Client: $SERVICE_TOKEN_SECRET`, 30 min, roles `["fcrm_service"]`) used by H1/H2/S5 clients.

### 8.2 OPA packages and data

Files in `ops/opa/`: `policy/authz.rego` (package `authz`), `policy/tools.rego` (package `tools`), `data/permissions.json` (→ `data.permissions`), `data/roles.json` (→ `data.roles.hierarchy`), `data/tool_permits.json` (→ `data.tool_permits`, a *separate top-level key* because `data.tools` is the policy package path), `tests/authz_test.rego`, `tests/tools_test.rego`. Image `openpolicyagent/opa:1.x` pinned; `opa run --server --addr :8181 --log-level error /policy /data`; decision logs to stdout; no public ingress.

`authz.rego` changes from the clone: `role_hierarchy` is read from `data.roles.hierarchy` (no hard-coded map); rules keep the AEO matcher (exact, `prefix/*`, `{param}` templates); `input` = `{service, method, path, user:{id, roles[]}}` where `path` is the FastAPI **route template** (e.g. `/cases/{id}/items/{item_id}`) so one rule covers all ids and the Redis cache key is small. Response shape unchanged (`data.authz.response`).

`roles.json`: `{"hierarchy": {"fcrm_po":["fcrm_po"], "fcrm_analyst":["fcrm_analyst"], "fcrm_committee":["fcrm_committee"], "fcrm_service":["fcrm_service","fcrm_analyst","fcrm_po","fcrm_committee"]}}` (the service account may call any internal and read route; it is never issued to a person).

`permissions.json` rule set (ids `svc-method-route`), default deny; summary of allows:

| Service | fcrm_po | fcrm_analyst | fcrm_committee | fcrm_service |
|---|---|---|---|---|
| case | `POST /cases`, `GET /cases*`, `/documents*`, `/intake/*`, `/review`, `/submit`, `/inbox`, `/clarifications/{t}/answer`, `/conditions*` (own), `/outcome`, `/activity`, `/stream`, `/dashboard` | `GET /queue`, `/cases/{id}/workbench\|submissions*\|items*\|score\|activity\|stream`, `POST /change-types\|screening\|completeness\|clarifications*\|items/{i}\|acknowledge\|controls/planned\|gaps\|runs\|recommendation*\|close`, `/conditions*`, `/reflags*`, `/cases/search` | none | `/internal/*`, all GET |
| scoring | none | all | `GET /models*`, `/bands` | `POST /score`, GET |
| policy | `GET /field-sets/{v}/resolve` | all | `GET /clauses/*`, `/controls*`, `/jurisdictions*` | `/internal/*`, `POST /policy/search`, `POST /precedents/search`, all GET |
| audit | `GET /verify/{case}` (own case; S4 checks ownership via S1 record) | `GET /records*`, `/proposals*`, `/verify/*`, `/examiner/*`, `/metrics/*` | `GET /records*`, `/verify/*` (released packs only) | `POST /records`, `POST /proposals`, all GET |
| decision | none | `GET /packs/{p}`, `/packs/{p}/votes\|threads\|outcome`, `/cases/{c}/packs[/latest]\|outcome`, `POST /packs` (the case's latest pack), `POST /packs/{p}/release`, `POST /threads/{t}/answer` | `GET /committee/queue`, the same reads, `POST /packs/{p}/votes`, `POST /packs/{p}/threads` | `POST /packs` (build), `/packs/{p}/supersede`, `/packs/{p}/revote`, `/outcomes`, all GET; denied release, votes, threads and answer (Guard 7). The observer has no S5 access (ADR-19) |
| identity (`auth`) | `/login`, `/refresh`, `/logout`, `/me`, `/users/me` | + `GET /users` | + `GET /users` | `/internal/service-token`, `GET /users` |

`tools.rego`:
```rego
package tools
import rego.v1
default allow := false
allow if {
  some p in data.tool_permits.permits
  p.agent == input.agent
  input.tool in p.tools
  input.role == "fcrm_service"            # tools are only called by the agent-worker's service identity
  not denied_case(input)
}
denied_case(inp) if { inp.case_id == null }   # every tool call is scoped to a case or to "oversight"
response := {"allow": allow, "agent": input.agent, "tool": input.tool, "case_id": input.case_id, "run_id": input.run_id}
```
`tool_permits.json` = the P7 agent→tools table verbatim: `screening:[field_rules, precedent_search]`, `intake:[document_reader, precedent_search, jurisdiction_list, field_rules]`, `research:[policy_search, precedent_search, jurisdiction_list]`, `assessment:[control_inventory, evidence_store, scoring_engine]`, `critic:[evidence_ledger, precedent_search, rules_check]`, `narrative:[case_record, evidence_ledger]`, `examiner:[audit_log]`, `watchlist:[jurisdiction_list, case_search]`, `policy_gap:[policy_search, case_search]`. Note on `scoring_engine` for the assessment agent: the tool exists and is permitted so the agent can *preview* arithmetic on its proposed inputs (read-only `POST /score {purpose: simulate}`), but the score of record is only the one H1 requests; Guard 5 Rule 1 rejects any proposal that carries a `score`/`band` field. The same table is mirrored as a Python allow-list in `fcrm_tools/registry.py` (fallback when OPA is unreachable → the call is *refused*, not allowed; BP §11).

Input shapes: route authz `{"input": {"service": "case", "method": "POST", "path": "/cases/{id}/items/{item_id}", "user": {"id": "u-riyer", "roles": ["fcrm_analyst"]}}}` → `POST {OPA_URL}/v1/data/authz/response`; tool permit `{"input": {"agent": "research", "tool": "policy_search", "case_id": "FCRM-2026-0151", "run_id": "run_…", "role": "fcrm_service"}}` → `POST {OPA_URL}/v1/data/tools/response`.

Rego test plan (`opa test ops/opa -v`, in `make check`): default deny with an unknown role; PO denied on every scoring/decision route and on `POST /cases/{id}/items/{item_id}`; analyst denied on `POST /cases/{id}/intake/values/{field_id}` (Rule 4) and on `POST /packs/{p}/votes`; committee allowed `POST /packs/{p}/votes`, denied `POST /packs/{p}/release`; service allowed `/internal/*`; template vs exact precedence; `tools`: every row of the permits table allowed, every off-table pair denied (generated table test), `case_id` null denied, person roles denied. `opa check --strict` also runs.

### 8.3 How the gateway and services call them

nginx (`ops/nginx/default.conf`, port 8080): `location /api/fcrm/auth/` proxies to identity without auth; `/api/fcrm/cases/`, `/api/fcrm/queue`, `/api/fcrm/conditions`, `/api/fcrm/reflags`, `/api/fcrm/dashboard` → `case:8001`; `/api/fcrm/scoring/` → 8002, `/api/fcrm/policy/` → 8003, `/api/fcrm/audit/` → 8004, `/api/fcrm/decision/` → 8005; `/` → `web`. Every non-auth location has `auth_request /_auth` → internal location proxying to `identity:8000/api/fcrm/auth/core/validate` with the client's `Authorization` header (cheap: identity verifies its own RS256 token locally); on 401 the gateway returns the problem+json 401 before the request reaches a service; `/cases/{id}/stream` locations set `proxy_buffering off`, `proxy_read_timeout 1h`; `client_max_body_size 60m` on `/documents`; rate limits (`limit_req_zone` per `$http_authorization` 20 r/s general, 5 r/s on `/items/`), security headers, JSON 503 handler. nginx does **not** call OPA: nginx OSS has no native OPA client and a Lua/njs bridge adds a failure point; route authorization lives in each service's `opa_dependency` on the same sidecar, which is also what the blueprint's "every call carries a validated token and an OPA decision" requires end to end. The deviation from the G1 card ("asks X2 for route permission") is recorded in `docs/architecture/adr/ADR-007-opa-at-service.md`.

Services: `Depends(current_user)` (local JWKS verification; 60 s Redis fallback cache of `/core/validate` results when running without JWKS) then `Depends(opa_dependency("case"))` using the matched route template; decisions cached in Redis 30 s; OPA unreachable → 503 `authz_unavailable` (fail closed). The agent-worker's `fcrm_guardrails.permit` calls `tool_permit()` before every tool call; denied → `agent.failed{reason: permit_denied}` after the `guard.permit` record.

---

## 9. Memory model

| Kind | Holds | Store | Rule |
|---|---|---|---|
| Working (one agent call) | `working_memory` in the `agent.run` payload: `case_summary` (title, change types, domains, geographies), `fields[]` (id, label, value, origin, source), `documents[]` (doc_id, name, pages, canonical_key), `prior_items[]` (for reruns: confirmed items untouched), `clauses_hint`/`controls_hint` (ids only), `precedent_ids[]`, `rules_text`, `completeness_context`, `retry_context?`; plus tool results fetched during the call. Rendered by H1 (`working_memory.py`) under the tier budget; discarded after the call | message payload (RabbitMQ) + `agent_jobs.payload_ref` (MinIO `jobs/{run_id}/{job_id}.json` when > 128 KB) | never a rolling conversation; any call is rebuildable from its payload; payload hash is the idempotency input |
| Short-term (one case run) | every transition, gate, job, guard result, retry, version, budget line | `fcrm_harness.case_runs` + `run_events`; `fcrm_audit.proposals` + `audit_records` | durable, ordered, replayable; not Redis, not process memory |
| Long-term (across cases) | precedents (closed cases: ratings, outcomes, fields), override history, corpus/controls/jurisdiction/field-set versions | `fcrm_policy.precedents` + S3 Vectors `precedent_cases`; `fcrm_audit`; `fcrm_policy.*_versions` | queried by `precedent_search`, the critic, the examiner; versioned so a past decision never changes |
| Learning | override rate per agent (from S4 `/metrics/override-rate`), eval scores per prompt version | `evals/reports/*.json` + `trend.json`; new prompt version in `/ai/prompts` gated by `make evals` | only through a test and a versioned prompt; no implicit store |

Redis (X7, cache only): `jwks:{kid}` 10 min; `validate:{sha256(token)}` 60 s (fallback mode only); `opa:authz:{roles}:{svc}:{method}:{route}` 30 s; `opa:tool:{agent}:{tool}` 30 s; `sse:case:{id}` pub/sub (no persistence); `ratelimit:{user}:{route}` sliding window 1 s; `toolcall:{agent}:{tool}:{args_hash}` 5 min (dedupe of identical read-only tool calls within one case run; best effort); `model-cache:{model}:{prompt_hash}` 24 h only when `MODEL_CACHE_ENABLED=true` (evals and rehearsal; off in `ENV=prod`). Never: case state, proposals, gates, votes, audit, agent memory between cases. Redis absence degrades to in-process caches and `/readyz` reports `redis: degraded`.

---

## 10. Error handling and resilience conventions

### 10.1 Exception hierarchy (`fcrm_common.errors`)

```
FcrmError(code: str, status: int, detail: str, extra: dict)          # base; never raised directly
├── NotFound(404, "not_found")
│   └── NoRunRecorded(404, "no_run_recorded")  # the case exists but no run has recorded anything (the harness monitor of a seeded case); the web shows information, not an error
├── Conflict(409, "conflict")                 # duplicate vote, round already open, idempotency replay mismatch
├── ValidationFailed(422, "validation_failed", errors[])
├── Unauthorized(401, "unauthorized") · Forbidden(403, "forbidden") · AuthzUnavailable(503, "authz_unavailable")
├── RuleViolation(409, "rule_violation", rule_id: R1..R8)             # Guard 5
├── GateRequired(409, "gate_required", gate_type)                      # action blocked until a human decides
├── IllegalTransition(409, "illegal_transition", from_state, to_state)
├── BudgetExceeded(429, "budget_exceeded", cap, used)
├── GuardRejected(422, "guard_rejected", guard: 1|4, findings[])
├── ChainBroken(500, "chain_broken", first_broken_seq)
├── UpstreamUnavailable(503, "upstream_unavailable", dependency)       # mongo, rabbitmq, bedrock, s3, opa, identity, <service>
└── UpstreamTimeout(504, "upstream_timeout", dependency)
```

### 10.2 Error body (RFC 9457 problem+json; `Content-Type: application/problem+json`)

```json
{ "type": "https://trustops.local/errors/rule_violation", "title": "Rule violation", "status": 409,
  "detail": "Override requires a reason (rule R6)", "instance": "/api/fcrm/cases/FCRM-2026-0151/items/itm_…",
  "code": "rule_violation", "rule_id": "R6", "trace_id": "0af3c1e9…", "errors": [ {"loc":["body","reason"],"msg":"…"} ] }
```
FastAPI's `RequestValidationError` and `HTTPException` are mapped to the same body. Messages never include stack traces or secrets; the `trace_id` links to Jaeger.

### 10.3 Retry, backoff, timeouts per dependency

| Dependency | Timeout | Retries (idempotent ops only) | Backoff | Circuit breaker |
|---|---|---|---|---|
| MongoDB | `serverSelectionTimeoutMS 5000`, `socketTimeoutMS 10000` | driver `retryWrites/retryReads`; app retries `TransientTransactionError` ×3 | driver | no (driver + replica set handle it) |
| RabbitMQ | connect 10 s | FastStream reconnect every 5 s; publisher confirm timeout 5 s → outbox keeps the row for the next relay tick | relay tick 200 ms, exponential to 5 s while down | no |
| Bedrock (via Pydantic AI) | per-attempt `wall_s` (tier); HTTP read 120 s | `FallbackModel` failover inside attempt 1; one explicit retry (attempt 2) | 1 s then fallback | **yes**: per model id, opens after 5 consecutive 5xx/throttles in 60 s, half-open after 30 s; open circuit = treat as model failure (→ fallback / handoff). Justified because a throttled region would otherwise burn the case budget on retries |
| S3 Vectors | 10 s per call | 4 (`Throttling`, 5xx) | 0.5 s × 2^n + jitter, cap 8 s | **yes**: same policy; open circuit → `PolicySearch` falls back to text-only mode with `mode: "text"` in the response and a warning |
| S3/MinIO | connect 2 s, read 30 s (uploads 300 s) | 3 (`boto` standard mode) | boto | no |
| OPA | 2 s | 1 | — | no; unreachable → 503 fail closed |
| Identity (JWKS) | 2 s | 2 | 0.2 s | no; stale JWKS kept up to 1 h if refresh fails |
| Service → service (httpx) | connect 2 s, read 10 s (`/score` 5 s, `/packs` 30 s) | 2 on connect errors and 502/503/504 for GET and idempotent POST with `Idempotency-Key` | 0.2 s, 0.6 s | no |

### 10.4 Graceful shutdown

SIGTERM → stop accepting (HTTP: uvicorn drains with `timeout_graceful_shutdown=25`; workers: FastStream `graceful_timeout=60` stops consuming and finishes in-flight handlers) → outbox relay flushes pending rows (bounded 10 s) → Mongo/Rabbit/httpx clients closed → OTel exporters `force_flush()`. Compose `stop_grace_period: 70s` for workers, `30s` for services. A job interrupted mid-model-call is not acked; the lease reaper or redelivery handles it; the budget `reserved` line is released by the reaper.

### 10.5 Health and readiness

`/healthz` 200 if the event loop responds. `/readyz` runs the dependency checks listed in 1.4 with a 2 s budget each and reports `{status: ok|degraded|fail, checks}`; `degraded` (Redis, OPA cache) still returns 200; `fail` (Mongo, RabbitMQ for workers, audit insert-only self-test, seed recompute mismatch in S2) returns 503. Compose healthchecks hit `/readyz`.

### 10.6 Agent failure semantics (restated for H2)

Guard order per job: 1 input (payload scan) → 2 permit (per tool call) → 3 budget (reservation already made; H2 enforces wall clock and refuses tool loops beyond `max_tool_calls` **(proposed 12)**) → model call → 4 output (schema; every claim has `source_ref` + `quote`; the quote must be a substring, whitespace-normalised, of the referenced source text fetched through the tool; confidence ∈ [0,1]) → publish. Failure of 1 → quarantine + `agent.failed{reason: guard1}`; 2 → `permit_denied`; 4 on attempt 1 → attempt 2 with `retry_context.error`; 4 on attempt 2 → `agent.failed{guard4_rejected}` with the raw output stored at `jobs/{run_id}/{job_id}.raw.json` for the human handoff.

### 10.7 Structured log fields

Every line is one JSON object: `ts, level, logger, msg, service, version, env, trace_id, span_id, case_id?, run_id?, job_id?, agent?, gate_id?, user_id? (sub only, never names or emails), http{method, route, status, ms}?, amqp{exchange, routing_key, message_id, attempt}?, dependency?, duration_ms?, error{code, type}?`. No request bodies, tokens, document text, or OTP-like secrets are ever logged; a `redact()` filter drops keys matching `(password|token|secret|authorization)`.

When `OTEL_EXPORTER_OTLP_ENDPOINT` is set, the same records are also exported as OTLP log records (`tracing._OtelLogHandler`), built from the same fields and the same `redact()` (`logging.record_fields`), with the trace and span ids carried natively (ADR-66). stdout stays the system of record; in the `obs` profile the collector forwards them to OpenObserve, the searchable log store (`make up-obs`).

---
## 11. Compose topology

### 11.1 Containers

| Service (compose name) | Image / build | Ports (host:container) | Healthcheck | depends_on (condition) | Volumes |
|---|---|---|---|---|---|
| `mongo` | `mongo:8.0` `--replSet rs0 --bind_ip_all` | 27017 | `mongosh --eval 'rs.status().ok'` | — | `mongo_data:/data/db`, `./ops/mongo/init:/docker-entrypoint-initdb.d:ro` |
| `mongo-init` (one-shot) | `mongo:8.0` | — | — | mongo (healthy) | runs `rs.initiate`, creates `fcrm_audit_writer`, collections with `$jsonSchema`, indexes |
| `rabbitmq` | `rabbitmq:4.0-management` | 5672, 15672 | `rabbitmq-diagnostics -q ping` | — | `rabbit_data:/var/lib/rabbitmq`, `./ops/rabbitmq/definitions.json` (exchanges, queues, DLX) |
| `redis` | `redis:7.4-alpine` | 6379 | `redis-cli ping` | — | — |
| `minio` | `minio/minio:RELEASE.2026-…` `server /data --console-address :9001` | 9000, 9001 | `mc ready local` | — | `minio_data:/data` |
| `minio-init` (one-shot) | `minio/mc` | — | — | minio (healthy) | creates buckets `uploads canonical packs vectors jobs`; versioning on `packs` |
| `opa` | `openpolicyagent/opa:1.x` | 8181 | `GET /health` | — | `./ops/opa/policy:/policy:ro`, `./ops/opa/data:/data:ro` |
| `otel-collector` (profile `obs`, on by default in `make up`; the four observability containers share it) | `otel/opentelemetry-collector-contrib:0.1xx` | 4317, 4318, 8889 (prom exporter) | `GET :13133` | — | `./ops/otel/collector.yaml:/etc/otelcol/config.yaml:ro` |
| `jaeger` | `jaegertracing/jaeger:2.x` | 16686 | `GET :16686` | — | — |
| `prometheus` | `prom/prometheus:v3.x` | 9090 | `GET /-/ready` | otel-collector | `./ops/prometheus/prometheus.yml:ro` |
| `grafana` | `grafana/grafana:11.x` | 3000 | `GET /api/health` | prometheus, jaeger | `./ops/grafana/provisioning:ro` (datasources + dashboards: tokens/cost per case & agent, override rate, gate waits, queue depth, outbox lag, eval scores) |
| `identity` | `src/services/identity/Dockerfile` | 8000 | `GET /api/fcrm/auth/readyz` | mongo-init (completed) | `jwt_keys:/keys` |
| `case` | `src/services/case/Dockerfile` | 8001 | `/readyz` | mongo-init, rabbitmq, redis, minio-init, identity, opa (healthy) | — |
| `scoring` | `src/services/scoring/Dockerfile` | 8002 | `/readyz` | mongo-init, identity, opa | — |
| `policy` | `src/services/policy/Dockerfile` | 8003 | `/readyz` | mongo-init, rabbitmq, minio-init, identity, opa | — |
| `audit` | `src/services/audit/Dockerfile` | 8004 | `/readyz` (includes insert-only self-test) | mongo-init, identity, opa | — |
| `decision` | `src/services/decision/Dockerfile` | 8005 | `/readyz` | mongo-init, rabbitmq, identity, opa | — |
| `caseflow` | `src/workers/caseflow/Dockerfile` | 9100 (health) | `/readyz` | mongo-init, rabbitmq, case, scoring, policy, audit, decision (healthy) | — |
| `agent-worker` (×2 `deploy.replicas`) | `src/workers/agent-worker/Dockerfile` | 9101 | `/readyz` | rabbitmq, opa, policy, audit, case, scoring, minio-init | — |
| `ingestion-worker` | `src/workers/ingestion-worker/Dockerfile` | 9102 | `/readyz` | rabbitmq, mongo-init, minio-init | — |
| `context-worker` | `src/workers/context-worker/Dockerfile` | 9103 | `/readyz` | rabbitmq, mongo-init, minio-init, policy | — |
| `web` | `src/web/Dockerfile` (nginx-served static build) | 5173 (dev: vite) / served via gateway | `GET /` | gateway | — |
| `gateway` | `nginx:1.27-alpine` + `./ops/nginx/default.conf` | **8080** | `GET /healthz` (static) | identity, case, scoring, policy, audit, decision (healthy) | — |

All Python images: `python:3.13-slim`, multi-stage (`ghcr.io/astral-sh/uv` copies `uv`, `uv sync --frozen --no-dev --package …` into `/app/.venv`), user `app` (uid 10001), `HEALTHCHECK CMD python -m fcrm_common.healthcheck http://localhost:<port>/readyz`, `ENTRYPOINT ["uvicorn", "<pkg>.main:app", ...]` for services and `["python", "-m", "<pkg>"]` for workers. Workers serve `/healthz` and `/readyz` through `faststream.asgi.AsgiFastStream` on their health port.

### 11.2 Startup ordering

`mongo` → `mongo-init` (replica set, users, schema, indexes) → `identity` (generates `/keys/jwt.pem` on first start if `JWT_KEY_FILE` missing and `ENV=local`) → `opa`, `rabbitmq` (definitions loaded), `redis`, `minio` → `minio-init` → S2 `scoring` and S4 `audit` (no bus dependency) → S1 `case`, S3 `policy`, S5 `decision` → `caseflow`, `agent-worker`, `ingestion-worker`, `context-worker` → `gateway` → `web`. `depends_on` uses `condition: service_healthy` / `service_completed_successfully`, so `docker compose up -d` is one command and `make up` waits on the gateway healthcheck.

### 11.3 Seed loading (P7) at startup

Each service's lifespan runs `SeedReader(SEED_PATH).load_if_empty(...)` when `SEED_ON_EMPTY=true` (default local; `false` in prod):

| Service | Collections seeded from `seed.json` (+ `clauses.json`, `corpus/*.md`) |
|---|---|
| identity | `users` (11, roles mapped from `po/analyst/committee`; `password_hash` = bcrypt of `users[].demo_password` when gen_seed emits it (design C), else the shared `DEMO_USERS_PASSWORD_HASH` env) |
| scoring | `scoring_model_versions` (v3 active), `routing_rules_versions` (v4 active); then the 80-case recompute self-test |
| policy | `corpus_versions` (v14), `clauses` (197), `control_inventory_versions` (v9), `jurisdiction_list_versions` (v2026-06 active, v2026-10-fixture inactive), `field_set_versions` (v6), `precedents` (65 closed cases); publishes `index.build` ×3 (`mode: full`) so H4 builds the indexes on first boot |
| case | `cases` (80: 79 at their seed stage with `inputs`-derived `review_items` in `confirmed` state and `score_snapshots`; **FCRM-2026-0151 at `intake` with no documents, no items, no records** so the demo runs live), `field_values`, `conditions` (open ones from `open_conditions`), `counters` (`seq = 151`) |
| audit | nothing for 0151 (its 67 records are the live demo's *expected* output, used as the golden in `make demo`); for the 65 closed cases a minimal chain (`case.created … outcome.set, case.closed`) so `verify` and the examiner have data |
| decision | `outcomes` for the 65 closed cases (`mode: delegated` for bands 1–2, `committee` for 3–4 with three synthetic votes and rationales); `packs` for the two `committee`-stage cases |

`make demo` then drives case 0151 through the API as Priya/Rahul/Ana/Joseph/Lin with the seed's values (uploads the three synthetic PDFs generated by `gen_seed.py` into `fcrm_seed/docs/`), asserts `9.8 → 10.4`, `approve_with_conditions 2–1`, three conditions, and that the audit trail has 67 records whose `type` sequence equals `demo.audit[].type` (the `what` strings may differ in wording; the types and numbering may not).

### 11.4 Env contract (variable names only; one `.env` at the root read by compose; secrets via `env_file`/Docker secrets)

Shared: `ENV, LOG_LEVEL, MONGODB_URI, RABBITMQ_URL, REDIS_URL, OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME (set per container), IDENTITY_JWKS_URL, IDENTITY_VALIDATE_URL, OPA_URL, SERVICE_TOKEN_SECRET, SEED_PATH, SEED_ON_EMPTY, GATEWAY_ORIGIN`.
Per service: `APP_ROOT_PATH, MONGODB_DB, PORT`; S1 adds `S3_ENDPOINT_URL, S3_REGION, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET_UPLOADS, S3_BUCKET_CANONICAL, S3_BUCKET_PACKS, UPLOAD_MAX_BYTES, UPLOAD_ALLOWED_EXTENSIONS, SSE_HEARTBEAT_S`; S2 adds `SEED_RECOMPUTE_ON_START`; S3 adds `VECTOR_BACKEND, S3_VECTORS_BUCKET, S3_VECTORS_REGION, EMBEDDING_PROVIDER, EMBEDDING_MODEL, EMBEDDING_DIMENSION, AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, S3_BUCKET_VECTORS, RRF_K, SEARCH_TOP_K_MAX`; S4 adds `MONGODB_URI_AUDIT_WRITER` (the insert-only user; S4 uses this instead of `MONGODB_URI`); S5 adds `COMMITTEE_QUORUM`; X1 adds `JWT_KEY_FILE, JWT_ACCESS_TTL_S (900), JWT_REFRESH_TTL_S (28800), JWT_ISSUER, JWT_AUDIENCE, REFRESH_COOKIE_NAME, DEMO_USERS_PASSWORD_HASH, LOGIN_RATE_LIMIT`.
Workers: H1 `CASE_SERVICE_URL, SCORING_SERVICE_URL, POLICY_SERVICE_URL, AUDIT_SERVICE_URL, DECISION_SERVICE_URL, CASE_CAP_TOKENS, TIERS_FILE (= ai/models/tiers.yaml), LEASE_REAPER_INTERVAL_S, OUTBOX_RELAY_INTERVAL_MS, PREFETCH`; H2 `AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, BEDROCK_REGION_FALLBACK, TIERS_FILE, PROMPTS_DIR, AGENT_MAX_TOOL_CALLS, MODEL_CACHE_ENABLED, PREFETCH`, the five service URLs, `OPA_URL`, S3 vars; H3 `S3_*`, `PDF_ENGINE, PDF_MIN_CHARS_PER_PAGE, UPLOADS_PER_FILE_TIMEOUT_SECONDS, GUARD1_ALLOWLIST_FILE, INJECTION_PATTERNS_FILE, PREFETCH`; H4 `VECTOR_BACKEND, S3_VECTORS_BUCKET, S3_VECTORS_REGION, EMBEDDING_*, CONTEXT_SYNTHESIS_ENABLED (false), CHUNK_* (engine defaults), AWS_*, S3_*, PREFETCH`.
Platform: `MONGO_INITDB_ROOT_USERNAME, MONGO_INITDB_ROOT_PASSWORD, MONGO_AUDIT_WRITER_PASSWORD, RABBITMQ_DEFAULT_USER, RABBITMQ_DEFAULT_PASS, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, GF_SECURITY_ADMIN_PASSWORD`.
AWS deploy path (documented in `ops/aws/README.md`, not built): same images on ECS Fargate or EKS; `S3_ENDPOINT_URL` unset (real S3); `VECTOR_BACKEND=s3vectors`; `OTEL_EXPORTER_OTLP_ENDPOINT` → ADOT collector sidecar → X-Ray (traces) + CloudWatch (metrics/logs) using the `otel-config/` pattern from AEO; DocumentDB is *not* a drop-in (no `$jsonSchema` validation, no transactions parity) so MongoDB Atlas or self-managed Mongo 8 on EC2 is the documented target; Amazon MQ for RabbitMQ; ElastiCache for Redis; Secrets Manager for the env secrets.

### 11.5 Observability wiring

`ops/otel/collector.yaml`: receivers `otlp` (gRPC 4317, HTTP 4318); processors `batch`, `resource` (adds `deployment.environment`), `attributes` (drop `http.request.header.authorization`); exporters `otlp/jaeger` (traces → jaeger:4317), `prometheus` (metrics on :8889 scraped by Prometheus), `debug` (logs at `ENV=local`; the AWS path swaps in `awsxray`, `awsemf`, `awscloudwatchlogs`). Every container sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES=service.version=...,component.id=S1`. Pydantic AI instrumentation (`Agent(instrument=True)`) emits GenAI spans with model, tokens and tool calls into the same trace; H2 adds attributes `case_id, run_id, job_id, agent, prompt_v, tier, attempt`.

### 11.6 Makefile targets

`make bootstrap` (uv sync workspace, pre-commit install, pull images) · `make keys` (dev JWT key) · `make up` / `make down` / `make logs svc=…` · `make seed` (force reload: drops `fcrm_*` and re-runs the loaders; refuses in `ENV=prod`) · `make demo` (drive case 0151 end to end and assert the goldens) · `make check` (ruff · ruff format --check · mypy · pytest ≥85 % · `opa test` + `opa check --strict` · compose-check (`tests/test_compose_env_contract.py`) · semgrep local · pre-commit) · `make test`, `make test-unit`, `make test-integration` (spins compose infra only) · `make evals` (X6 runner, writes `evals/reports/<date>/`, updates `trend.json`, exits non-zero on regression) · `make contracts` (export OpenAPI per service to `docs/architecture/openapi/*.json` and event schemas) · `make dlq` / `make dlq-replay q=…` · `make verify case=…` · `make token-report` (tokens and cost per case and agent from Prometheus → `ops/token-report.md`) · `make gen-seed` (regenerate `seed.json`, `clauses.json`, demo PDFs; byte-stable).

---

## 12. Parallelisation map

### 12.1 Contract artefacts to publish first (day 1, package `fcrm_contracts`; everything else waits on this commit)

1. `fcrm_contracts/api/`: Pydantic models per service — `case_po.py`, `case_analyst.py`, `case_internal.py`, `scoring.py` (`ScoreRequest/Response`, `ScoringModelVersion`, `RoutingRulesVersion`), `policy.py`, `audit.py` (`RecordAppend`, `AuditRecord`, `ProposalAppend`, `Proposal`, `VerifyResult`), `decision.py` (`PackBuild`, `Pack`, `VoteCast`, `VotesHidden/Revealed`, `Thread`, `Addendum`, `OutcomeSet`), `identity.py`, `common.py` (`Problem`, `Page`, `GateDecision`, `Principal`).
2. `fcrm_contracts/events/`: `Envelope`, `EVENT_CATALOGUE`, `payloads.py` (every type in 4.3), fixtures.
3. `fcrm_contracts/proposals/`: the nine output schemas (`ScreeningProposal`, `ExtractionProposal`, `CategoryProposal`, `GapQuestion`, `ClauseProposal`, `PrecedentMatch`, `FactorProposal`, `ControlProposal`, `CriticFinding`, `CompletenessReport`, `Question`, `Narrative`, `Condition`, `BusinessSummary`, `ExaminerAnswer`, `ReflagProposal`, `GapSuggestion`) with the mandatory `Claim{source_ref{doc_id|clause_id|record_seq, page?, line?}, quote, confidence}` mixin.
4. `fcrm_contracts/enums.py`: `CaseState`, `GateType`, `Outcome`, `ChangeType`, `Domain`, `ItemKind`, `ItemStatus`, `Role`, `Tier`.
5. OpenAPI stubs: `make contracts` from a stub FastAPI app per service that mounts the route signatures with `response_model` and no bodies → `docs/architecture/openapi/<svc>.json`; the web team generates its typed client from these on day 1.
6. Fixtures: `fcrm_seed/seed.json` (already canonical), `fixtures/score_demo_before.json`, `score_demo_after.json`, `fixtures/events/*.json`, `fixtures/audit_chain_0151.json` (types sequence), `fixtures/pack_v1.json`.
7. `ops/opa/data/tool_permits.json`, `permissions.json` skeleton, `ai/models/tiers.yaml` (design B's `{{MODEL_*}}` placeholders; the Bedrock ids are filled on day 1 against the account's model-access list and appear in no other file).

### 12.2 Independent build units once contracts are fixed

| Lane | Units | Depends on | Owner can mock |
|---|---|---|---|
| A | `fcrm_common` (settings, errors, logging, tracing, mongo, outbox, health, http, auth, authz) | contracts | — (unit tests with mongomock-free in-memory fakes + a Mongo testcontainer) |
| B | S2 scoring (engine + golden tests + routes) | contracts, common | nothing external; pure |
| C | S4 audit (chain, insert-only user, verify, examiner read) | contracts, common, `ops/mongo/init` | — |
| D | S1 case (routes, collections, SSE, submit checks, clocks, role shaping) | contracts, common | H1 via fixtures; S4 via respx |
| E | S3 policy + H4 context-worker (clone, filters, 3 indexes, search, fixture mode) | contracts, common | AWS via `memory`/`dummy`; live-AWS tests optional |
| F | H3 ingestion-worker (clone, prune, page:line, Guard 1) + `fcrm_guardrails.input` | contracts, common | — |
| G | S5 decision (packs, votes, tally, R6, outcomes) | contracts, common | H1 via fixtures |
| H | X1 identity + OPA policies/tests + nginx conf | contracts | — |
| I | `fcrm_guardrails` (permit, budget, output, rules ×8, thresholds) | contracts, common | OPA via respx |
| J | `fcrm_agents` + `fcrm_tools` + `fcrm_skills` + `fcrm_prompts` + H2 agent-worker (Pydantic AI adapter, FallbackModel, guards 1–4, usage) | contracts, guardrails, tools | services via respx; models via Pydantic AI `TestModel`/`FunctionModel` |
| K | H1 caseflow (state machine, gates, budgets, routing, outbox, event store, cores) | contracts, common, guardrails.rules | all services via respx; H2 via fixture `agent.done` messages; property tests on transitions |
| L | P7 seed (gen_seed.py, clauses.json, corpus md, demo PDFs, loaders) | contracts (shapes) | — |
| M | X6 evals (datasets from seed, runner on pydantic-evals, judge adapter, reports, gate) | contracts, agents | models via cached/`TestModel` |
| N | compose, Makefile, otel/grafana provisioning, docs | all | — |
| W | web (React, service layer swap) | OpenAPI stubs | mock server from stubs |

### 12.3 Integration checkpoints

- **CP1 (end of day 2):** `make up` brings every container healthy with the seed loaded; `GET /api/fcrm/scoring/score` reproduces 9.8/10.4; `GET /api/fcrm/audit/verify/FCRM-2026-0118` ok; identity login for the three roles; OPA tests green. Lanes A, B, C, H, L, N converge.
- **CP2 (day 3):** S1 → `case.submitted` → H1 → `agent.run` → H2 (research + assessment with `TestModel`) → `agent.done` → S4 proposals → S2 score → S1 snapshot visible in the analyst workbench; SSE delivers `score.computed`. Lanes D, E, I, J, K converge; live Bedrock switched on once `tiers.yaml` is confirmed.
- **CP3 (day 4):** override → rescore → R3 ack → lock → pack v1 → three votes → outcome → conditions → `chain.verified`; `no_majority` path and R6 pack v2 path exercised by `tests/integration/test_committee_paths.py`. Lanes G, K.
- **CP4 (day 5):** `make demo` passes end to end with 67 records matching `demo.audit[].type`; `make evals` baseline committed; `make check` green; token report generated. Lanes M, N, W.
- **Day 6:** hardening, docs/governance (gate rationale, the seven guards, the eight rules, AEO fix list), rehearsal; no new features.

Rules for all lanes: commit small and often in the monorepo (history is evidence, D-91); every unit builds and tests alone (`uv run --package …`); a change to `fcrm_contracts` is a PR touching the fixtures and schema exports in the same commit, and `make contracts` must be clean; no lane mocks a contract shape locally — it imports it.

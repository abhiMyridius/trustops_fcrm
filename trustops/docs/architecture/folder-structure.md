# Folder structure (complete)

> **Current layout (ADR-26, 5 Oct 2026).** The tree below is the original design-time target and is kept for the file-level detail. Two things changed and take precedence:
>
> * `src/` is split into `src/backend/` (the uv workspace: `apps/{api,harness}`, `services/*`, `workers/*`, `packages/*`; one `Dockerfile` for both Python processes) and `src/frontend/` (the React app, its own `Dockerfile`). Python import names are unchanged.
> * The stack is eight containers (`mongo`, `rabbitmq`, `minio`, `opa`, `api`, `harness`, `gateway`, `web`) from one `docker-compose.yaml`; observability is profile `obs`, Redis is profile `cache`, and `compose.debug.yaml` publishes the infrastructure ports. `api` runs X1 and S1–S5 as mounted sub-apps of one process (`fcrm_api`), and `harness` runs H1–H4 on one broker (`fcrm_harness`). Per-service and per-worker Dockerfiles, `compose.obs.yaml` and the init containers no longer exist.

Every folder and module the build produces. Python units follow one template: `pyproject.toml`, `Dockerfile`, `CLAUDE.md`, `requirements.txt` + `requirements-dev.txt` (exported from `uv.lock`), and the import package `fcrm_<name>/` with source only. Tests live under `/tests/unit` and `/tests/integration` at the same relative path (ADR-21). Clones add `UPSTREAM.md`. Tool configuration (`ruff.toml`, `mypy.ini`, `pytest.ini`) sits at the repository root, never inside `src/`.

```
trustops/
├── README.md                          setup · walkthrough · decision log pointer · known gaps
├── BUILD-PLAN.md                      master plan
├── CLAUDE.md                          repo guidance for Claude Code
├── Makefile                           bootstrap up down logs seed seed-verify demo check gate test test-unit test-int e2e resilience evals
│                                      contracts api-gen schemas export-requirements dlq dlq-replay verify token-report nightly gen-seed keys
├── docker-compose.yaml                the stack: 8 containers; profile obs (otel-collector, jaeger, prometheus, grafana), profile cache (redis)
├── compose.dev.yaml                   bind mounts, vite dev server, hot reload
├── compose.test.yaml                  VECTOR_BACKEND=memory, EMBEDDING_PROVIDER=dummy for the api; agents stay on live Bedrock (ADR-27)
├── compose.debug.yaml                 publishes the infrastructure and harness health ports (make up-debug)
├── .env.example                       every variable name, no values
├── .gitignore  .editorconfig  .pre-commit-config.yaml  ruff.toml  mypy.ini  pytest.ini
├── .claude/
│   └── settings.json                  hooks: protect_paths · commit_guard · fmt_py · fmt_ts · stop_gate · subagent_report · stage_context
│
├── ai/                                the AI layer as files (loaded read-only by the agent-worker at start)
│   ├── README.md  CLAUDE.md  evals-index.md
│   ├── agents/                        screening · intake · research · assessment · critic · narrative · examiner · watchlist · policy_gap  (.agent.yaml)
│   ├── tools/                         document_reader · case_record · case_search · field_rules · policy_search · precedent_search · control_inventory
│   │                                  evidence_store · jurisdiction_list · rules_check · evidence_ledger · audit_log · submission_diff  (.tool.yaml)
│   ├── skills/                        cite_verbatim · declare_gap · compare_precedent · draft_for_business_reader · batch_questions  (.skill.md)
│   ├── prompts/
│   │   ├── MANIFEST.yaml              prompt set version (= case pin prompts_v) and current version per prompt id
│   │   ├── CHANGELOG.md
│   │   ├── screening/     screening.system.v1.md  screening.user.v1.md
│   │   ├── intake/        intake.system.v1.md  intake.extract.user.v1.md  intake.questions.user.v1.md
│   │   ├── research/      research.system.v1.md  research.user.v1.md
│   │   ├── assessment/    assessment.system.v1.md  assessment.user.v1.md
│   │   ├── critic/        critic.system.v1.md  critic.user.v1.md
│   │   ├── narrative/     narrative.system.v1.md  narrative.completeness.user.v1.md  narrative.recommendation.user.v1.md
│   │   ├── examiner/      examiner.system.v1.md  examiner.user.v1.md
│   │   ├── watchlist/     watchlist.system.v1.md  watchlist.user.v1.md
│   │   └── policy_gap/    policy_gap.system.v1.md  policy_gap.user.v1.md
│   ├── models/tiers.yaml              tiers, Bedrock ids, fallbacks, budgets, settings, price table (the only file naming models)
│   ├── guards/                        guards.yaml  thresholds.yaml  injection_patterns.txt  pii_entities.yaml  restricted_terms.txt
│   ├── policies/                      tools.rego  tool_permits.json  (source; copied into ops/opa at build)
│   ├── registry.lock/                 agents.lock  tools.lock  skills.lock  prompts.lock
│   ├── schemas/                       generated JSON Schema for every output and tool schema (make schemas)
│   └── guidance/                      build-time guidance for Claude Code
│       ├── coding-standards.md  commit-convention.md  subagent-brief-template.md  worktree-protocol.md  review-checklist.md
│       ├── hooks/                     protect_paths.py  commit_guard.py  fmt_py.sh  fmt_ts.sh  stop_gate.py  subagent_report.py  stage_context.py
│       └── packages/                  <unit>.CLAUDE.md for every unit (copied to src/**/CLAUDE.md by make sync-guidance)
│
├── docs/
│   ├── requirements/
│   │   ├── source/                    approved spec set 00–06, teardown 05, brief HTML, connect PDF, concept page, transcript
│   │   ├── research/                  00-synthesis … 07-runtime-facts, seed/{seed.extracted.json, recompute.py}
│   │   ├── spec.md                    expanded specification (use case, lifecycle, screens, agents, scoring, routing)
│   │   ├── personas.md  rules.md (eight rules + R1–R6 with ids)  glossary.md  open-questions.md
│   ├── architecture/
│   │   ├── design/                    A-backend.md  B-ai-layer.md  C-web-tests-ops-sdlc.md
│   │   ├── folder-structure.md        this file
│   │   ├── c4-context.md  c4-container.md  (Mermaid)
│   │   ├── sequences/                 intake.md  assessment.md  review-override.md  clarification.md  committee.md  oversight.md  failure-handoff.md
│   │   ├── data-model.md              every collection, field, index, validator
│   │   ├── event-catalogue.md         30 event types, exchanges, routing keys, payload refs
│   │   ├── openapi/                   case.json  scoring.json  policy.json  audit.json  decision.json  identity.json  (make contracts)
│   │   └── events/                    <event>.schema.json  (make contracts)
│   └── governance/
│       ├── decision-log.md  gates-register.md  rules.md  visibility-matrix.md  audit-chain.md
│       ├── defect-trail.md  failure-log.md  release-notes.md
│
├── evals/
│   ├── README.md
│   ├── datasets/<agent>/              *.jsonl derived from seed P7 (sizes in design B §7.2)
│   ├── metrics/                       __init__.py  citation.py  extraction.py  clauses.py  controls.py  critic.py  grounding.py  restricted_terms.py  judge_metrics.py
│   ├── runners/                       run.py (pydantic-evals)  gate.py (promotion)  harvest.py (overrides → rows)  report.py
│   ├── judge/                         bedrock_judge.py  rubrics/*.md
│   ├── results/<date>/*.json  trend.json  report.html
│
├── tests/                             (ADR-21: the only place tests live)
│   ├── README.md
│   ├── conftest.py                    all fixtures and shared fakes: role tokens, seed accessors, testcontainers, FunctionModel factories, respx mounts
│   ├── unit/                          mirrors src/
│   │   ├── services/{case,scoring,policy,audit,decision,identity}/
│   │   ├── workers/{caseflow,agent_worker,ingestion_worker,context_worker}/
│   │   └── packages/{contracts,common,guardrails,agents,tools,skills,prompts_loader,seed}/
│   ├── integration/                   mirrors src/ (testcontainers: Mongo rs0, RabbitMQ, MinIO; OPA binary)
│   │   ├── services/{case,scoring,policy,audit,decision,identity}/
│   │   └── workers/{caseflow,agent_worker,ingestion_worker,context_worker}/
│   ├── contract/                      test_openapi_snapshots.py (oasdiff)  test_schemathesis.py  test_event_schemas.py  examples/
│   ├── e2e/
│   │   ├── playwright.config.ts       projects: demo, blackbox-smoke; baseURL http://localhost:8080; storageState per persona
│   │   ├── fixtures/                  personas.ts  seed.ts  selectors.ts
│   │   ├── demo/                      scenes.ts (21 steps)  scenes.spec.ts  artifacts/
│   │   └── blackbox/                  test_bb_01_no_auto_decide.py … test_bb_14_versions_pinned.py  smoke.spec.ts
│   ├── resilience/                    test_worker_kill.py  test_llm_timeout.py  test_rabbit_restart.py  test_mongo_restart.py  test_identity_down.py  test_dlq.py  test_budget.py
│   ├── perf/                          k6/smoke.js
│   ├── fixtures/                      score_demo_before.json  score_demo_after.json  audit_chain_0151.json  pack_v1.json  events/*.json
│   └── tools/                         wait_for_ready.py  tamper_record.py  fault_env.py
│
├── ops/
│   ├── nginx/default.conf             /api/fcrm/* routing, auth_request → identity, static web, SSE passthrough
│   ├── opa/                           policy/{authz.rego, tools.rego}  data/{permissions.json, tool_permits.json}  tests/{authz_test.rego, tools_test.rego}
│   ├── mongo/{entrypoint.sh,init/}    entrypoint starts mongod then runs init/: 01-replset.js  02-users.js (fcrm_audit_writer insert-only)  03-schemas.js ($jsonSchema)  04-indexes.js
│   ├── rabbitmq/definitions.json      exchanges case.events agent.jobs ingestion.jobs context.jobs, queues, retry queues, DLX
│   ├── minio/{entrypoint.sh,init.sh}  starts minio, then buckets uploads canonical packs vectors jobs; versioning on packs
│   ├── otel/collector.yaml  prometheus/prometheus.yml
│   ├── grafana/provisioning/          datasources/  dashboards/{case-throughput.json, agents-tokens-cost.json, guards-overrides.json}
│   ├── scripts/                       wait_for_ready.py  seed_verify.py  demo.py  dlq_replay.py  token_report.py  claude_usage.py  backup.sh  restore.sh  export_requirements.sh  install_tooling.sh
│   ├── runbooks/                      start.md  reset.md  rotate-keys.md  replay-dlq.md  verify-chain.md  switch-llm-mode.md  rebuild-indexes.md  incident.md
│   ├── aws/deployment-path.md         ECS/EKS, Atlas or Mongo on EC2, Amazon MQ, ElastiCache, ADOT → X-Ray/CloudWatch, Secrets Manager
│   ├── env/contract.md                every variable: owner, default, secret?, used by
│   ├── token-analysis/                runtime-<date>.md  build-<date>.md
│   └── reports/nightly/<date>.md
│
└── src/
    ├── pyproject.toml                 uv workspace: members services/*, workers/*, packages/*; shared dev group
    ├── uv.lock
    │
    ├── services/
    │   ├── case/                      S1
    │   │   ├── pyproject.toml  Dockerfile  CLAUDE.md  requirements.txt  requirements-dev.txt
    │   │   └── fcrm_case/
    │   │       ├── __init__.py  main.py  settings.py  lifespan.py
    │   │       ├── routes/            po.py  analyst.py  committee.py  internal.py  stream.py  health via fcrm_common
    │   │       ├── domain/            cases.py  submissions.py (versions, diff)  documents.py  review_items.py  threads.py  conditions.py (reminders)
    │   │       │                      submit_checks.py (the four checks)  clocks.py (analyst and owner clocks)  visibility.py (role shaping + key scanner)  triage.py (R5 load query)
    │   │       ├── repo/              cases.py  submissions.py  documents.py  review_items.py  threads.py  conditions.py  counters.py
    │   │       ├── events/            publish.py  consume.py (snapshot pushes from H1)
    │   │       ├── sse/               hub.py (Redis fan-out)  filters.py (role → allowed event types)
    │   │       └── storage/           s3.py (uploads, canonical)
    │   ├── scoring/                   S2
    │   │   └── fcrm_scoring/          __init__.py  main.py  settings.py  lifespan.py (startup recompute)  engine.py (pure)  bands.py  boundary.py
    │   │                              models.py (ScoringModelVersion, RoutingRulesVersion repos)  routes.py  seedload.py
    │   ├── policy/                    S3
    │   │   └── fcrm_policy/           __init__.py  main.py  settings.py  lifespan.py  routes/{corpus.py, controls.py, jurisdictions.py, fieldsets.py, precedents.py, search.py, internal.py}
    │   │                              domain/{corpus.py, controls.py, jurisdictions.py, fieldsets.py, precedents.py}  repo/*.py
    │   │                              search/{hybrid.py, rrf.py, filters.py, text_index.py}  vectors/{client.py (S3 Vectors), memory.py, embeddings.py}
    │   ├── audit/                     S4
    │   │   └── fcrm_audit/            __init__.py  main.py  settings.py  lifespan.py (insert-only self-test)  chain.py (hash, genesis, seq)  ledger.py (proposals)
    │   │                              verify.py  routes/{records.py, proposals.py, verify.py, examiner.py}  repo/{records.py, proposals.py}
    │   ├── decision/                  S5
    │   │   └── fcrm_decision/         __init__.py  main.py  settings.py  lifespan.py  packs.py (frozen snapshot, content hash)  release_checks.py (four)
    │   │                              votes.py (hidden, DB-unique)  tally.py (ADR-08)  outcomes.py  threads.py (R6, addenda, re-vote)  routes/{packs.py, votes.py, threads.py, outcomes.py, internal.py}  repo/*.py  events.py
    │   └── identity/                  X1 (clone of evoqidentityservice)
    │       ├── UPSTREAM.md            source path, commit, the ten fixes
    │       └── fcrm_identity/         __init__.py  main.py  settings.py  lifespan.py  keys.py (RS256 keypair, JWKS)  routes/{auth.py (login, refresh, logout, me), core.py (/core/validate), jwks.py}
    │                                  services/{login.py (bcrypt, rate limit), tokens.py (typ access|refresh)}  repo/{users.py, refresh_tokens.py}  seedload.py
    │
    ├── workers/
    │   ├── caseflow/                  H1
    │   │   └── fcrm_caseflow/         __init__.py  __main__.py  app.py (FastStream + AsgiFastStream health)  settings.py
    │   │                              states.py (CaseState)  transitions.py (table)  caseflow.py (apply, one transaction)  gate_manager.py  budget.py  routing.py (R1–R6 over data)
    │   │                              dispatch.py (agent.run, idempotency, leases)  reaper.py  eventstore.py (case_runs, run_events, rebuild)  audit_map.py (run event → S4 record)
    │   │                              cores/{intake_core.py, assessment_core.py, decision_core.py, oversight_core.py}
    │   │                              consumers/{case_events.py, agent_results.py, ingest_results.py}  clients/{case.py, scoring.py, policy.py, audit.py, decision.py}
    │   ├── agent-worker/              H2
    │   │   └── fcrm_agent_worker/     __init__.py  __main__.py  app.py  settings.py  consumer.py (claim, lease, ack/nack)  pipeline.py (guards 1–4 around one call)
    │   │                              runtime/{models.py (BedrockConverseModel + FallbackModel from tiers.yaml), llm_mode.py (live|replay|mock), output.py (ToolOutput), usage.py (ModelCallRecord, cost), tracing.py}
    │   │                              ledger_client.py  publish.py
    │   ├── ingestion-worker/          H3
    │   │   ├── UPSTREAM.md
    │   │   ├── evoq_ingestion/        clone (pruned: no jira/confluence/mcp); page:line offsets added; upstream tests kept inside
    │   │   └── fcrm_ingestion_worker/ __init__.py  __main__.py  app.py  settings.py  jobs/{ingest_document.py}  anchors.py (page:line)  guard1.py (presidio, injection)  storage.py
    │   └── context-worker/            H4
    │       ├── UPSTREAM.md
    │       ├── evoq_context/          clone; filters on search(); synthesis off switch; S3 Vectors hardening; upstream tests kept inside
    │       └── fcrm_context_worker/   __init__.py  __main__.py  app.py  settings.py  jobs/{build_index.py, upsert_precedent.py}  segmenter.py (clauses)
    │                                  embeddings/{bedrock_titan.py, dummy.py}  indexes.py (policy_clauses, precedent_cases, controls)
    │
    ├── packages/
    │   ├── contracts/                 P6
    │   │   └── fcrm_contracts/        __init__.py  enums.py  common.py (Problem, Page, GateDecision, Principal)
    │   │                              api/{case_po.py, case_analyst.py, case_internal.py, scoring.py, policy.py, audit.py, decision.py, identity.py}
    │   │                              events/{envelope.py, catalogue.py, payloads.py}
    │   │                              proposals/{provenance.py (Claim, SourceRef), screening.py, intake.py, research.py, assessment.py, critic.py, narrative.py, examiner.py, watchlist.py, policy_gap.py}
    │   │                              tools/{document_reader.py … submission_diff.py}  guards.py  versions.py (PinnedVersions)  ai_files.py  ledger.py
    │   │                              fixtures/ (events, scores, chain, pack)  stubs/ (stub FastAPI apps for make contracts)
    │   ├── common/                    fcrm_common/  settings.py  errors.py  logging.py  tracing.py  metrics.py  mongo.py  outbox.py  events.py  health.py  http.py  auth.py  authz.py  ids.py  hashing.py  time.py  pagination.py  seedload.py  healthcheck.py
    │   ├── guardrails/                P5
    │   │   └── fcrm_guardrails/       __init__.py  base.py (Guard protocol, GuardResult)  input_scan.py  permit.py  budget.py  output_shape.py  rules_guard.py  critic_hook.py  human_gate.py
    │   │                              rules/{__init__.py, r1_no_agent_score.py, r2_no_bulk_accept.py, r3_po_no_restricted.py, r4_analyst_no_submission_edit.py, r5_closed_untouched.py, r6_override_has_reason.py, r7_planned_not_scored.py, r8_policy_quoted.py}
    │   │                              quote_match.py  restricted_terms.py  pii.py  opa_client.py  thresholds.py
    │   ├── agents/                    P1
    │   │   └── fcrm_agents/           __init__.py  registry.py  spec.py  base.py  context_pack.py (builder interface, caps, truncation, cache points)  adapter.py (Pydantic AI Agent factory)  cli.py (fcrm-agent run)
    │   │                              screening/ intake/ research/ assessment/ critic/ narrative/ examiner/ watchlist/ policy_gap/   each: __init__.py  builder.py  tools.py
    │   ├── tools/                     P2
    │   │   └── fcrm_tools/            __init__.py  registry.py  base.py  cache.py  seed_backed.py  document_reader.py  case_record.py  case_search.py  field_rules.py  policy_search.py  precedent_search.py
    │   │                              control_inventory.py  evidence_store.py  jurisdiction_list.py  rules_check.py  evidence_ledger.py  audit_log.py  submission_diff.py
    │   ├── skills/                    P3   fcrm_skills/  __init__.py  loader.py  (skill text lives in ai/skills)
    │   ├── prompts-loader/            P4   fcrm_prompts/  __init__.py  loader.py  frontmatter.py  render.py (sandboxed Jinja, four functions)  manifest.py  locks.py
    │   └── seed/                      P7
    │       └── fcrm_seed/             __init__.py  gen_seed.py (byte-stable)  loader.py  seed.json  clauses.json  vectors_fixture.json  corpus/*.md (9 sources)  docs/{business_case_remittance_MX.pdf, payout_partner_contract.pdf, vendor_DD_scope_letter_2026.pdf}
    │
    └── web/                           W1
        ├── package.json  vite.config.ts  tsconfig.json  index.html  Dockerfile  CLAUDE.md  .oxlintrc.json  eslint.config.js  vitest.config.ts
        ├── public/                    brand mark, favicon
        ├── scripts/gen-api.ts         OpenAPI → src/api/generated/*.d.ts
        └── src/
            ├── main.tsx
            ├── app/                   router.tsx  layouts/{AppShell.tsx, AuthLayout.tsx, MonitorLayout.tsx}  guards/{RequireAuth.tsx, RequireRole.tsx}
            ├── api/                   generated/{case,scoring,policy,audit,decision,identity}.d.ts  client.ts  keys.ts  errors.ts
            │                          queries/{caseQueries.ts, analystQueries.ts, committeeQueries.ts, configQueries.ts, assuranceQueries.ts, authQueries.ts}
            │                          sse/{useCaseStream.ts, useUserStream.ts, eventSchemas.ts, reconnect.ts}
            ├── auth/                  AuthProvider.tsx  roles.ts
            ├── design/                tokens.css  global.css
            ├── components/
            │   ├── ui/                Button Input Select Textarea Checkbox Radio Switch Badge Tag Card Modal Drawer Tabs Table Pagination Toast Tooltip Skeleton EmptyState
            │   │                      Breadcrumb Avatar ProgressRing Stepper Dropdown Form (≈25 primitives)
            │   ├── domain/            SourceChip ProvenanceTag ScoreBadge CompletionRing StageTracker ReasonDialog DiffTable ClauseCard ControlRow FactorBlock RiskMatrix ConditionTable VoteCard PackSection AuditRecordChip
            │   └── charts/            KpiCard ChartCard recharts configs
            ├── features/
            │   ├── auth/              LoginPage.tsx
            │   ├── po/                Dashboard MyQueue NewCase CaseLayout Integrations  tabs/{Overview, Documents, ReviewSubmit, Inbox, Activity, Conditions}
            │   ├── analyst/           Dashboard Queue WorkbenchLayout  tabs/{Summary, Submission, Inbox, Policy, RiskControls, Recommendation, Handoff, Activity}
            │   │                      assurance/{AskTab, OverridesTab, WatchlistTab, AgentQualityTab}
            │   │                      config/{ConfigLayout, WorkingSetBanner, VersionHistory, ImpactPreviewModal, sections/{FieldSets, Categories, ControlInventory, PolicyCorpus, JurisdictionRisk, ScoringModel, RoutingRules, AIAgents}}
            │   ├── committee/         Queue CaseLayout  tabs/{Pack, Inbox, Vote}
            │   └── monitor/           MonitorPage.tsx
            ├── lib/                   format.ts  bands.ts (labels only)  a11y.ts
            ├── mocks/                 browser.ts  server.ts  seed.ts  handlers/{case,scoring,policy,audit,decision,identity}.ts  scenarios/
            └── test/                  setup.ts  render.tsx  fixtures.ts
```

Counts: 6 services · 4 workers · 8 packages · 1 web app · 9 agents · 13 tools · 5 skills · 7 guards · 8 rules · 22 containers · 21 E2E scenes · 14 black-box checks.

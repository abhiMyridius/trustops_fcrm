# Risk Assessment Workbench · final document set

Genius Hacks Q3 2026 · Myridius · submission deadline 9 October 2026

Open the HTML files in a browser (they load fonts and the Mermaid renderer from the web). The same pages are published as live artifacts on claude.ai; links below.

| # | File | What it is | Live page |
|---|------|------------|-----------|
| 1 | `01-build-plan.html` | Master spec: scope, use case, AEO reuse, services, stack-to-services mapping, agent platform, memory, seven-guard suite, scoring, data, evals, observability, auth, deploy, repos, decisions | https://claude.ai/artifact/9G5Y2dMRZqkwNB3M5hJmzd |
| 2 | `02-blueprint.html` | Architecture tab (interactive component board incl. P7 seed, components table, naming standard, assessment sequence) and Flow map tab (full case cycle, 21-scene order), linked by component IDs | https://claude.ai/artifact/RMSFzCFaUum36MjKFRTbf4 |
| 3 | `03-decision-log.html` | 57 decisions, one line each: why, what it gives us, rubric served, status. Filterable. v1.3 | https://claude.ai/artifact/Qr1weE9a4yQk8twDNu1TsE |
| 4 | `04-walkthrough.html` | As-built walkthrough (v3, 7 Oct): 24 animated scenes in 7 chapters replicating the built app's screens for case 0151, with a chapters drawer and hover previews, a scrubber, a "behind the screen" harness panel, and callouts tagged by rubric theme; press A to hide callouts and the cursor | https://claude.ai/artifact/CnkdcYPkpWcj7zxBfXoSji || 5 | `05-prototype-teardown.html` | Analysis of the BA's TrustOps prototype (FCRM_v2): screens, agents, flows, rubric fit, gaps | https://claude.ai/artifact/Fd4nnxqPuaF3EAsihTQZNW |
| 6 | `06-flow-map.html` | Standalone pan-and-zoom flow map of the full case cycle with glossary and component filter (same content as the Blueprint's Flow map tab) | https://claude.ai/artifact/DN6Mo94R8ehjuV8aDRw3Qq |

## Decisions in force (summary)

- Stack: FARM strictly. FastAPI · React · MongoDB 8. Python 3.13, uv, Pydantic 2.x.
- Architecture: event-driven microservices with an orchestrated agent harness. Five HTTP services (case, scoring, policy, audit, decision), orchestrator (Caseflow, original code), agent workers, cloned ingestion and context workers.
- Agents: Pydantic AI 2.x running one agent call each; direct vendor keys (Anthropic, OpenAI, Bedrock); FallbackModel per tier. No LiteLLM proxy, no OpenRouter.
- Data: MongoDB, one database per service; S3 Vectors for the three indexes via the cloned evoq_context backend; MinIO for files; Redis (X7) as cache and bus only.
- Guardrails: seven guards in one package; OPA sidecar (cloned) for route authz and agent-to-tool permits; the BA's eight rules as code.
- Anchor: walkthrough v2 from the BA and architect (D-96). Changes carried into every document: owner review-and-correct before submit (D-17); clarification round before Policy and Controls (D-16); committee question answered as a pack addendum, pack v2 and re-vote if a number changes (D-18); routing rules v4 R1–R6 incl. fast lane and auto-triage (D-19); recommendation lock and condition reminders (D-19a); composite after override corrected to 10.4 (D-42); one generated seed, package P7 fcrm-seed (D-57).
- Analyst review: starts with a completeness check (D-16). The narrative agent lists missing, stale or contradictory items and drafts the questions; the analyst asks in one round before reviewing, or proceeds when there is enough to assess.
- Memory: working (job payload), short-term (case_runs + evidence ledger), long-term (precedents, overrides, corpus versions), learning (evals to prompt versions). No agent-memory products in the decision path.
- Reuse: AEO components (evoq_ingestion, evoq_context, GenericService and job-worker templates, OPA sidecar, identity service, evals adapters, OTel scaffold) are cloned into our repos and changed there. The running AEO platform is never called.
- Repos: one per service, worker and package; one Dockerfile per repo; a single docker-compose.yaml at the project root (umbrella repo). Submodules during development, vendored copies at the deadline.
- Out of scope: mobile layout, notification settings, Temporal, GitHub Actions, Trivy, OCR, multi-language documents.

## Superseded pages (kept for history, not part of the set)

Scope · Agent Stack · standalone Architecture. Each carries a banner pointing to its replacement. The earlier 20-scene walkthrough is replaced by v2 in this set.

## Before the build starts

AWS account with S3 Vectors and Bedrock access; Anthropic and OpenAI API keys; all in a `.env` read by the root compose.

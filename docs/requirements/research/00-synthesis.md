# TrustOps / FCRM Risk Assessment Workbench: current-state synthesis

As of 2 Oct 2026. Submission due 9 Oct 2026, which leaves 7 calendar days.

Source notes in this folder:
- A: problem statement and rules
- B: build plan and decision log
- C: blueprint, flow map and walkthrough
- D: FCRM_v2 prototype
- E: AEO reuse inventory

## 1. What we are graded on (A)
| Weight | Criterion | What this means for the build |
|---|---|---|
| 30% | AI harness and agent orchestration | Show the architecture, orchestration, context management, data foundation, instruction design, and the split of work between AI and people. |
| 20% | SDLC automation | Use AI in all 6 stages (Requirements, Design, Development, Testing, Deployment, Operations), joined into one flow. |
| 15% | HITL and governance | Every gate needs a stated rationale: which risk it controls and why it sits where it does. |
| 10% | Evaluation framework | Show the metrics, the method, and how failures feed back into improvement. |
| 10% | Context engineering and requirement expansion | Bring in domain research, layer in context, and keep it persistent across the work. |
| 5% | Production readiness | Scalability, security, standards and reliability. "A demo that works once is not the bar." |
| 5% | Token efficiency | Report the models used, where tokens are consumed, and what was optimised. |
| 5% | Engineering judgement | Decide what is deterministic and what is probabilistic, and justify plain logic where it is used instead of an LLM. |

Rules and expectations:
- The repo is about 75% of the evidence. The panel will "probe decisions, not features", and incremental commit history counts.
- Required **single repo** layout: `/src /ai /docs/requirements /docs/architecture /docs/governance /evals /tests /ops README.md`.
- Deck with 7 parts: problem and expanded spec, harness, AI-led vs person-led work and the gates, evals, demo, failure handling and iteration, token analysis.
- Synthetic data only. Prompt-to-app tools (Lovable, Bolt, v0, Replit Agent) are banned; Claude Code is encouraged. A live deploy is optional, but we need a credible deploy and ops path.

Product constraints in the brief:
- Nothing is approved or rejected automatically.
- Disagreement can be recorded with a reason.
- Controls never reduce risk to zero.
- Risk factors are grounded in published frameworks.
- Every rating is traceable.
- The audit trail is immutable.
- Rules can be tuned by the risk function.
- Intake to decision-ready takes about 2 days, down from 15–20.
- There are 4 outcomes: approve, approve with conditions, defer, reject.

## 2. What the team has planned (B, C)
- FARM stack (FastAPI, React, MongoDB 8), Python 3.13, uv, Pydantic 2.
- Event-driven microservices over RabbitMQ/FastStream, plus the Caseflow orchestrator, which holds the state machine, gates, budgets, an outbox and an event store.

Services and supporting components:
- **Services:** S1 case, S2 scoring, S3 policy, S4 audit (hash chain, insert-only), S5 decision.
- **Workers:** H1 Caseflow, H2 agent-worker, H3 ingestion (evoq_ingestion clone), H4 context (evoq_context clone, S3 Vectors).
- **Packages:** P1–P6 for agents, tools, skills, prompts, guardrails and contracts.
- **Platform:** X1 identity, X2 OPA, X5 Logfire, X6 evals, X7 Redis as a cache only.
- **Storage:** MinIO for files.

Agents and guards:
- **9 agents on Pydantic AI**, each doing one call: screening, intake, research, assessment, critic, narrative, examiner, watchlist, policy_gap. Each has a model tier with a FallbackModel.
- **7 guards** in a fixed order: input/PII, OPA permit, budget, output shape with a verbatim-quote check, the BA's 8 rules, critic, human gate.

Scoring:
- Inherent = L×I.
- Retention = 1 − 0.6·CE/10, with a floor of 0.4.
- Residual = Inherent × Retention.
- Composite = weighted sum of residuals.
- Bands: 0–5, 5.1–10, 10.1–17, 17.1–25.
- Boundary flag when within 0.5 of a band edge.
- Each case pins the versions of everything it used.

Demo walkthrough: case FCRM-2026-0151, US→Mexico via Remesas del Norte, played by Priya Nair, Rahul Iyer and Ana Mendes, in 20 scenes.

## 3. What the prototype gives us (D)
- A rich domain model:
  - 27 field definitions: a core set plus category packs, and 6 always-required fields.
  - 6 trigger types mapped to 4 FFIEC categories.
  - 8 versioned config sections.
  - 5 clarification types and 7 evidence types.
- The most concrete scoring code.
- A seeded corpus of 78 cases from a fixed seed (0x5eedc0de).
- About 175 mock API functions, which serve as the endpoint checklist.
- UI worth porting: the UI kit, intake cards, the submission diff, the risk matrix, provenance tags, the vote panel and the config version history.
- Gaps:
  - All AI is scripted.
  - Rules are enforced in the UI only (vote secrecy, the pack is not frozen, per-item confirmation).
  - Routing is buggy.
  - Deferred and approve-with-conditions outcomes are missing.
  - The PO response leaks analyst fields.
  - Completion is computed 3 ways and the client value is trusted.

## 4. What reuse really costs (E)
| Component | Fit | Effort |
|---|---|---|
| evoq_ingestion | High (87% coverage, mypy strict) | 2.5–3.5 d. OCR and SharePoint don't exist. |
| evoq_context + S3 Vectors | High engine, medium fit | 6–10 d. No metadata filters, AEO synthesis is always on, tested only against a fake S3. |
| Job worker (FastStream/RabbitMQ) | Medium | 2–3 d. No DLQ or lease. Runner bug: it always sets COMPLETED. |
| GenericService | Medium | 1.5 d |
| Identity | Medium-low | 2–3 d. Refresh tokens are accepted as access tokens, the OTP is logged, secrets are hard-coded, and there is no seed or password login. |
| OPA | Medium | 1–1.5 d. Hard-coded role_hierarchy, no Rego tests. |
| OTel → Logfire | Medium | 0.5 d of code (not env only). The tracer no-ops for non-cluster endpoints. |
| evoq_evals | Low-medium | 1.5–2.5 d. auto_qa's evaluator is the better base. |
| contracts, compose | High / medium | 0.5–1 d |

That is about 19–27 dev-days of adaptation. Nothing in AEO uses uv, and nothing in the workspace uses Pydantic AI, pydantic-evals or Logfire.

## 5. Conflict register (needs one answer each)
1. **Repo shape.** The plan has 19 repos plus an umbrella with submodules; the brief requires ONE repo with a fixed layout.
2. **Demo numbers.** Docs: 9.8→11.5. Prototype code: 9.6→10.2 (verified by hand). Blueprint recompute: ~12.2. The composite across areas is undefined in the docs.
3. **Demo cast.** 0151 / Mexico / Remesas / Nair-Iyer-Mendes (docs) vs 0147 / Philippines / Pinnacle / Mehta-Raman-Menon (prototype).
4. **Bus.** The README says Redis; the build plan and AEO code use RabbitMQ, with no decision row recording it.
5. **Vectors.** "MongoVectorStore" text and Mongo clause embeddings vs S3 Vectors only (MongoVectorStore doesn't exist).
6. **LiteLLM.** The README says no LiteLLM; the embeddings code and the flow-map glossary use it.
7. **Agent count.** 9 defined, the demo says 6; screening and policy_gap are never shown.
8. **Who calls scoring.** H1→S2 (sequence) vs the assessment agent's tool (walkthrough).
9. **When the critic and the completeness check run.** Before review (flow, D-16) vs after the override (demo).
10. **Routing.** Bands 1–2 delegated close vs "to a committee decision" in the brief; the prototype has its own routing bug.
11. **Votes.** How 3 votes map to 4 outcomes, and the tie-break.
12. **Decision log hygiene.** 49 rows not 51; D-42 is missing; the D-50 reversal is unlogged.
13. **Missing concrete values.** Model IDs per tier, budgets, ports, queue names, event payloads, proposal schemas, quorum N, bucket names, the control count (24 vs 20).
14. **Scope creep that isn't reuse.** SharePoint and OCR would be new builds, and the README already marks OCR out of scope.

## 5a. Conflict status after the approved docs (v1.3 / walkthrough v2, 3 Oct)
Resolved: 2 (demo numbers 9.8 → 10.4, verified against the seed and all 80 cases), 3 (demo cast: 0151, Nair/Iyer/Mendes/Okafor/Chen), 7 (agents: 9 defined, policy_gap never runs), 9 (critic and completeness ordering), 12 (decision log: 57 rows, reversal logged).
Changed: 10 (routing now R1–R6 in D-19), 13 (model strings, budgets, quorum, 24 controls now in the seed; ports, queues, payloads, schemas still missing), 6 (LiteLLM wording fixed in glossaries only).
Still open: 1 (repo shape: 20 repos + umbrella, `make vendor` at deadline), 4 (Redis vs RabbitMQ, no decision row), 5 (MongoVectorStore wording in BP §10 and blueprint S3), 8 (who calls scoring: H1→S2 vs assessment agent tool), 11 (3 votes → 4 outcomes, tie-break), 14 (SharePoint reinforced, OCR out).
New in v2: retry path inconsistency (fallback-first vs same-model-first); token counter vs "23% of budget"; dashboard 3.3 days vs "about 2 days" headline; planned control reuses CTL-T01; seed's eight rules differ from the glossary's; a referenced "screens catalogue (07)" doesn't exist; `routing_v` missing from the §7 pin set; the four submit checks and four release checks are never enumerated; baseline eval figures are seeded, not measured.
Details: B-build-plan.md (v1.3 section), C-blueprint-flow.md (V2.1–V2.8), F-walkthrough-seed.md.

## 6. Recommended direction
- **One monorepo** in the brief's layout:
  - `src/services/*`, `src/workers/*`, `src/packages/*`, `src/web`.
  - One Dockerfile per service and a root compose file.
  - git from commit 1.
- **Narrow the runtime** to what the demo exercises:
  - 5 services, Caseflow, agent-worker, ingestion and context.
  - The 6 demo agents first; screening, watchlist and policy_gap later if time allows.
- **Clone the high-fit engines** (ingestion, context, job worker, OPA). For each, fix the verified bugs and record the fix as governance evidence.
- **Identity: clone and harden, or use a slim local JWT issuer** behind the same `/core/validate` contract. This is a decision for the team.
- **Scoring spec from the prototype code**, with golden tests: 9.6 → 10.2, Band 2 → 3.
- **Treat the build harness as a first-class deliverable**, since it carries 50% of the marks between criteria 1 and 2:
  - `/ai` holds the prompts and agent specs.
  - Each SDLC stage produces its artefact.
  - Evals gate prompt changes.
  - Token logs per agent.

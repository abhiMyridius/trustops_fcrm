# B · Build plan notes (README + 01-build-plan + 03-decision-log)

Sources read in full:
- `/Users/apahuja/Downloads/Hackathon/Documents/00-README.md` (README)
- `/Users/apahuja/Downloads/Hackathon/Documents/01-build-plan.html` (BP; sections 1, 1b, 2, 3, 3b, 4, 4b, 5, 6, 7, 8, 9, 10, 11). Its only `<script>` is Mermaid init (the theme has a gantt config, but no gantt diagram exists). Two Mermaid diagrams: the service flowchart (s3) and the guard chain (s5).
- `/Users/apahuja/Downloads/Hackathon/Documents/03-decision-log.html` (DL; v1.2). All rows sit in a JS array `D` inside `<script>`, along with a change-log array `CH` and the rubric map `R`.

Citations: [README], [BP §n], [DL D-xx].

> **[Corrected 3 Oct]** The sections below describe the **v1.2** documents. Those copies now sit in `/Users/apahuja/Downloads/Hackathon/FCRM_v2/docs/hackathon/`. The `Documents/` paths above now hold the approved **v1.3** set. Statements the v1.3 set makes wrong are marked **[Corrected v1.3: …]** inline. All changes are listed in "v1.3 update (approved docs, 3 Oct)" at the end of this file.

Context: Genius Hacks Q3 2026 at Myridius. The submission deadline is **9 October 2026** [README]. The plan speaks of "Nine days" [BP §3b].

---

## 0. Document set [README]

| # | File | What it is | Live page |
|---|---|---|---|
| 1 | 01-build-plan.html | Master spec | claude.ai/artifact/9G5Y2dMRZqkwNB3M5hJmzd |
| 2 | 02-blueprint.html | Architecture tab (component board, components table, naming standard, assessment sequence) and Flow map tab, linked by component IDs | claude.ai/artifact/RMSFzCFaUum36MjKFRTbf4 |
| 3 | 03-decision-log.html | "51 decisions", filterable, v1.2. **[Corrected v1.3: "57 decisions", v1.3]** | claude.ai/artifact/Qr1weE9a4yQk8twDNu1TsE |
| 4 | 04-walkthrough.html | 20-scene guided demo. **[Corrected v1.3: 21-scene walkthrough v2 by the BA and architect (1 Oct), seed-driven, also runs as a click-through prototype with `window.FCRM_PROTO=true`]** | claude.ai/artifact/BZ94Y4R6frCmnfg82VTVNu |
| 5 | 05-prototype-teardown.html | Analysis of the BA's TrustOps prototype (FCRM_v2) | claude.ai/artifact/Fd4nnxqPuaF3EAsihTQZNW (**this file is not in the local Documents folder**; an old copy dated 1 Oct is in `FCRM_v2/docs/hackathon/`) |
| 6 | 06-flow-map.html | Standalone pan-and-zoom flow map | claude.ai/artifact/DN6Mo94R8ehjuV8aDRw3Qq |

Superseded and kept only for history: the Scope, Agent Stack and standalone Architecture pages [README, DL D-95].

Prerequisites before the build starts [README]:
- An AWS account with S3 Vectors and Bedrock access.
- Anthropic and OpenAI API keys.
- All of these go in a `.env` that the root compose reads.

---

## 1. Scope

### 1.1 In scope, by area [BP §1]

Chip legend: In scope / Later / Out / Reuse from AEO / New.

**Screens (chip: all)**
- PO
  - Dashboard
  - New case
  - Case detail, with tabs Overview · Documents · Inbox · Conditions · Activity. **[Corrected v1.3: Overview · Documents · Review & submit · Inbox · Conditions · Activity]**
  - Templates
  - Integrations (SharePoint via AEO ingestion)
- Analyst
  - Dashboard
  - Queue
  - Workbench (8 tabs; the BP does not list them)
  - Assurance: Ask · Overrides · Watchlist · Agent quality
  - Config: Categories & Fields · Classification · Scoring · Controls · Policy corpus · Routing · Jurisdictions · Agents
- Committee
  - Queue
  - Case: Pack · Inbox · Vote
- Out: mobile layout, notification settings.
- Build the Config screens last. "They are the three heaviest BA screens (Categories, Classification, Control inventory)". Seed files cover the demo if time runs out [BP §11].

**Agents (chip: all)**
- Intake · Research · Assessment · Critic · Narrative · Examiner · Screening (fast lane) · Watchlist re-flag · Policy-gap suggester.
- Out: multi-language documents.

**Harness (chip: MVP + Phase 2)**
- Original orchestrator (our code, no framework).
- Job workers (RabbitMQ).
- Human gates, including the completeness check before review. **[Corrected v1.3: human gates are the completeness check and one clarification round before review, owner review-and-correct before submit, and the committee question answered as a pack addendum. Auto-triage by load and the fast lane are added to the harness chip.]**
- Evidence ledger and hash-chained audit.
- Retries, model fallback chains and a per-case token budget.
- Agent, tool, skill and prompt registries.
- Out: Temporal.

**Scoring (chip: all)**
- BA engine ported.
- Versioned scoring models.
- Re-score with reason.
- Boundary flag.
- Tests from the worked example.

**Data (chip: all)**
- MongoDB 8 (our own instance, not AEO's), one database per service.
- S3 Vectors (AWS) for the clause, precedent and control indexes, via the cloned evoq_context.
- MinIO for files locally.
- Versioned reference collections.
- 80 synthetic cases seeded. **[Corrected v1.3: one generated seed (gen_seed.py: 80 cases, controls v9, corpus v14, routing v4, users), shared by the services, the evals and the walkthrough]**
- Policy ingestion via evoq_ingestion.
- Out: OCR of scanned PDFs.

**Guardrails (chip: all)**
- The seven-guard suite.
- OPA for API authz and agent tool permits.
- Output policy checks.
- Rate limits.
- A red-team eval set.

**Evals (chip: all)**
- Golden sets per agent.
- Metrics: faithfulness, F1, accuracy and override rate.
- LLM-as-judge for the narrative.
- Eval gate on prompt change (pre-commit / make check).
- Drift report.
- Human review sampling.

**Observability (chip: MVP)**
- OTel (platform scaffold) to Logfire.
- One trace per case and spans per agent step.
- Tokens, cost and latency.
- Override-rate dashboard.
- Later: alerts, SLOs, SIEM.

**Auth (chip: MVP)**
- evoqidentityservice tokens and 3 roles.
- OPA route rules.
- The server strips restricted fields for the PO.
- Later: enterprise SSO rollout.

**Deploy (chip: MVP)**
- A Dockerfile per repo and one root docker-compose.yaml.
- make check: ruff · ruff format · mypy · pytest · compose-check · Semgrep local · pre-commit.
- Out: GitHub Actions, Trivy.

### 1.2 Out of scope overall [README, DL D-92]

Mobile layout, notification settings, Temporal, GitHub Actions, Trivy, OCR, multi-language documents.

### 1.3 Use case [BP §1b, DL D-05]

**The problem.** Assess the financial-crime risk of **changes** at a **US bank**, before go-live. This is not fraud, not credit and not transaction-monitoring operations.

**Six change types (triggers)**
1. New product
2. New feature
3. Process change
4. Vendor or third-party onboarding
5. New geography
6. New customer segment

**Four risk domains (FFIEC BSA/AML Manual)**
1. Customer or entity
2. Product and service
3. Geography
4. Delivery channel and third party

**Risk types:** money laundering · terrorist financing · sanctions exposure · third-party (vendor and agent) AML risk.

**Four outcomes:** approve · approve with conditions · defer · reject. Conditions are tracked as work items [DL D-02].

**Jurisdiction:** a US-regulated bank under the US federal supervisory framework. FATF lists are used for country risk.

**Out of scope**
- Fraud, credit, operational and market risk.
- Running sanctions screening or transaction monitoring. The workbench assesses whether those controls exist and how strong they are.
- SAR/STR filing, investigations and customer-level KYC decisions.
- Non-US regimes (EU AMLD, UK MLRs) beyond the FATF country lists.
- Model-risk validation of the bank's own ML models.

**Why this boundary**
- It is exactly the brief.
- FFIEC's four domains are the published decomposition.
- It is small enough to seed a real corpus and control inventory in days.
- One demo case touches 3 of the 4 domains.

### 1.4 Policy corpus: what the research agent may quote [BP §1b, DL D-06]

| Source | Sections loaded | Used for |
|---|---|---|
| FFIEC BSA/AML Examination Manual (public) | BSA/AML Risk Assessment · CDD · Third-Party Payment Processors · Electronic Banking · Funds Transfers · Nonbank FIs/MSBs · Foreign Correspondent Accounts (summary) | Domains, inherent-risk factors, expected controls |
| OFAC sanctions programme requirements (public, summarised) | Screening obligations · list types (SDN, sectoral) · 50% rule | Sanctions screening expectations |
| FinCEN AML/CFT program rule (proposed Apr 2026, summarised) | Risk assessment process · update on new products, markets, customer types | The "why" for assessing changes |
| OCC Bulletin 2017-43 (public, summarised) | New, modified or expanded products: due diligence · approval · post-launch monitoring | Conditions and follow-up |
| FATF (public) | High-risk and increased-monitoring lists, June 2026 (+ Oct 2026 fixture) | Geography risk, watchlist re-flag |
| Internal FCRM Policy 4.2 (synthetic) | High-Risk Corridor Jurisdictions: screening at onboarding, EDD where destination is High | Geography controls |
| Internal Third-Party AML Standard 7.1 (synthetic) | Payout agents: independent corridor-scope assessment, agent KYC standard, contract AML clauses | Vendor and agent controls |
| Internal Product Risk Standard 2.3 (synthetic) | Product sign-off, transaction limits and velocity rules, disclosure | Product controls |
| Internal Customer Onboarding Standard 1.8 (synthetic) | KYC/CDD, PEP screening, beneficial ownership, periodic refresh, segment attestations | Customer controls |

- Public sources are loaded as summaries and extracts that cite the public document.
- The internal standards are synthetic documents the team writes.
- Every clause carries a corpus version.

### 1.5 Reference data [BP §1b]

**Control inventory: "24 controls".** These are the ones listed (**20 in total**). **[Corrected v1.3: now "Control inventory v9 · 24 controls", with IDs CTL-G01…G05 geography · CTL-T01…T05 third party · CTL-P01…P06 product · CTL-C01…C08 customer (5+5+6+8 = 24). The named list below is unchanged, so 20 names still cover 24 IDs.]**
- Geography (4): jurisdiction screening · corridor EDD · sanctions list screening (OFAC/UN/EU) · local licensing confirmation
- Third party (4): vendor due diligence · payout-agent monitoring · contract AML clauses · agent KYC sampling
- Product (5): transaction limits and velocity · product sign-off · monitoring rule coverage · disclosure and consent · fee transparency
- Customer (7): KYC/CDD · EDD for high-risk · PEP screening · periodic refresh · beneficial ownership · adverse media · segment attestation

Each control has a weight, an evidence artefact and a test date.

**Scoring model v3**
- Likelihood and impact definitions 1–5 per domain.
- Retention floor 40%.
- Bands 1–4. Bands 1–2 route to the analyst; bands 3–4 route to the committee.
- Boundary flag at 0.5.
- Domain weights are normalised across the in-scope domains. **[Corrected v1.3: domain weights customer 0.20 · product 0.20 · geography 0.24 · channel 0.36, normalised across the domains in scope. Composite = Σ area residual × normalised weight; control effectiveness per area = weighted mean of control ratings.]**

**Field set v6: at most 18 required fields** **[Corrected v1.3: "18 required fields max · 80% to submit"; field IDs F01–F06 always required, F10–F62 per change type]**
- 6 always required: change owner · description · go-live date · customer segment · volumes · partner/vendor identity (when a third party is involved).
- Per-type extras: corridor countries · payout channels · licence numbers · data handled · process owner.

### 1.6 Demo story arc [BP §1b]

The case: consumer remittances US → Mexico through the payout partner **Remesas del Norte**. It touches three domains: vendor, geography and product.

> **[Corrected v1.3]** The 8-act table below is the v1.2 arc. v1.3 has **10 acts mapped to scenes 1–20**: the review-and-submit act is new, and the completeness act now comes *before* the override. Act 6 is now **9.8 → 10.4, Band 3, record #49** (it was 11.5 and #31). See v1.3 section U5 for the full arc.

| Act | Beat | What it proves |
|---|---|---|
| 1 | Payments wants the change | One change, three domains in scope |
| 2 | Documents come first; the intake agent finds **15 values with sources**; a new fact (cash pickup) appears at intake | Slow → fast; surprises handled honestly |
| 3 | Research quotes **four clauses** and declares a gap (cash-pickup agents); assessment proposes ratings; **score 9.8, Band 2, boundary flag** | Policy is retrieved, not generated; scoring is arithmetic |
| 4 | The critic flags **vendor DD 7.6 vs precedent 4.9** on a stale, domestic-only audit | Consistency across analysts |
| 5 | The analyst overrides to **4.5** with a reason; the score becomes **11.5, Band 3**, so the case goes to committee; record **#31** is hash-chained. **[Corrected v1.3: 10.4, Band 3, record #49]** | A human disagrees, the consequences are handled and the trail stays intact |
| 6 | Completeness check: the narrative agent lists gaps and drafts questions; the analyst asks in one round; the owner answers; a planned review is recorded but not scored | Ask early; "two clocks"; planned is not done |
| 7 | Hidden votes: 2 approve with conditions, 1 defer; three tracked conditions | Nothing auto-approved; conditions are real |
| 8 | Six months later the examiner agent answers "why committee?" from the log; a new FATF list re-flags two open cases | Traceable; oversight continues |

Supporting demo-queue cases:
- KYC vendor renewal (fast lane, Band 1)
- Card controls in app (Band 2, awaiting business)
- Crypto custody pilot (Band 4, committee)

### 1.7 Personas and roles

| Persona | Role | What they do |
|---|---|---|
| PO (business / product owner) | `fcrm_po` | Submits the change, confirms intake extractions, answers clarifications, tracks conditions. Sees no scores [DL D-62]. |
| Analyst (FCRM) | `fcrm_analyst` | Reviews each AI item, overrides with a reason, runs the completeness check, closes Band 1–2 cases, and owns AI configuration [DL D-03]. |
| Committee | `fcrm_committee` | Receives the pack and casts hidden votes. |

- The **"policy owner"** reviews policy_gap suggestions, but no role exists for it.
- The **examiner/regulator** is served by the Examiner agent through Assurance › Ask. No examiner role exists either.
- The BP says roles fcrm_po · fcrm_analyst · fcrm_committee are added in identity, with 3 demo users seeded [BP §2, §9].

### 1.8 Case lifecycle and states

**Orchestrator state machine [BP §3, H1]:**
intake → assess → critic → completeness check → review → decide → oversight

**[Corrected v1.3]** The state machine is now: intake → review-and-submit → triage → screening → assess → critic → completeness check → clarification round → review → decide → oversight.

How the steps relate to the agents and gates:
- Screening (fast lane) runs first. The analyst confirms the fast lane [BP §4 agents table]. **[Corrected v1.3: screening runs after intake → review-and-submit → triage (H1 state list; demo Act 4). The fast-lane criteria are in R4.]**
- Intake: the intake agent proposes extractions, categories and gap questions. **The PO confirms each.**
- Assess: the research agent proposes clauses and precedents, and the analyst accepts each clause. The assessment agent proposes L/I and controls, and the analyst confirms each rating. The scoring service then computes.
- Critic: the critic reviews every case before the analyst [DL D-22]. The analyst must explain or adjust.
- Completeness check, at the start of analyst review [DL D-16]:
  1. The narrative agent produces a CompletenessReport (missing · stale · contradictory) and Questions.
  2. The analyst asks in one round, or proceeds.
  3. The owner answers. The case-service publishes `case.answered`.
- Review: there is a human gate per item, a reason is required on override, and there is no accept-all [DL D-12]. Band changes must be acknowledged, as must the boundary flag.
- Decide:
  - Bands 1–2: the analyst closes.
  - Bands 3–4: the committee decides. The decision-service assembles a pack from locked numbers. Votes stay hidden until N are cast, then they are tallied, the outcome is set and conditions are activated. **[Corrected v1.3 (R2, D-18, D-19a): quorum 3, simple majority, votes hidden until all are cast, rationale required. The pack is built from locked numbers and pinned versions. Committee questions are answered as a pack addendum; if a number changes, the recommendation is unlocked with a reason, pack v2 is generated and every cast vote is re-requested.]**
  - Outcome is one of approve / approve with conditions / defer / reject.
- Oversight: tracked conditions; the watchlist re-flag on a new FATF list; Examiner Q&A; a policy-gap suggester.

The **CaseState enum values are not enumerated** in the BP. The four cores are modules of the orchestrator: `intake_core · assessment_core · decision_core · oversight_core`, plus `gate_manager` [BP §3b].

---

## 2. Architecture overview [BP §3]

The style is event-driven microservices with an orchestrated agent harness. The stack is FARM.

- Each service owns one scope, one Mongo database and one API.
- Services use HTTP for reads and RabbitMQ events for work.
- **No service reads another's database.**

### 2.1 Component IDs

| ID | Name | Kind |
|---|---|---|
| W1 | fcrm-web (React, BA screens) | new |
| G1 | nginx gateway, `/api/fcrm/*` | new |
| S1 | case-service | new HTTP |
| S2 | scoring-service | new HTTP |
| S3 | policy-service | new HTTP (**the ID collides with "S3 Vectors" and "AWS S3" in the text**) |
| S4 | audit-service | new HTTP |
| S5 | decision-service | new HTTP |
| H1 | orchestrator (Caseflow) | new worker, original code |
| H2 | agent-worker ×N | new worker |
| H3 | ingestion-worker | evoq_ingestion clone (reused) |
| H4 | context-worker | evoq_context clone (reused) |
| X1 | identity (clone of evoqidentityservice) | reused |
| X2 | OPA sidecar (authz + tool permits) | reused |
| X3 | RabbitMQ | platform |
| X4 | Model vendors: Anthropic · OpenAI · Bedrock, direct via Pydantic AI | external |
| X5 | Logfire via OTLP | external |
| X6 | evals (fcrm-evals: datasets · runner · reports) | — |
| X7 | Redis (cache · SSE fan-out) | platform |
| D1 | MongoDB 8, a database per service | platform |
| D2 | MinIO files + S3 Vectors indexes | platform |
| P1–P6 | libraries fcrm-agents · fcrm-tools · fcrm-skills · fcrm-prompts · fcrm-guardrails · fcrm-contracts (in that order; "P4 prompt version" in §4b fits P4 = prompts) | new |

The BP counts **"nine new deployables plus the platform"**. The new ("green") nodes are W1, G1, S1–S5, H1 and H2, which makes 9.

### 2.2 Diagram edges [BP §3]

- W → GW → S1 · S2 · S3 · S4 · S5
- S1 → Redis (cache and pub/sub)
- S1 ↔ MQ ↔ H1
- MQ ↔ H2, H3, H4
- H2 → S3 policy, S2 scoring, S4 audit and the LLM vendors
- H2 → OPA ("permit?")
- S1 → identity (token)
- H4 → S3 Vectors; policy-service → S3 Vectors
- The HTTP services → MongoDB
- The workers and HTTP services → Logfire

### 2.3 Why five services [BP §3b; DL D-60]

**Why split**
- Production readiness and a clear human/AI boundary.
- Audit must be append-only and independently verifiable.
- Scoring must be pure and testable in isolation.

**Why not more**
- Agents are jobs, not services.
- Guardrails are a package, not a service; a network hop before every tool call adds latency and a failure point.
- Nine days of time: each extra service is another fork, env block and compose entry.

**Why not fewer**
- Merging audit into case-service would let workflow code update records.
- Merging scoring into case-service blurs "AI proposes, code computes".
- Votes have their own visibility rule, which isolation keeps in one place.

### 2.4 How the stack layers map to deployables [BP §3b]

1. **Experience:** fcrm-web + nginx + the SSE stream from case-service. The UI never talks to agents.
2. **Harness cores:** fcrm-orchestrator. The evidence ledger and audit chain live in audit-service. The guardrails package is used by the orchestrator and the agent worker.
3. **Agent team:** fcrm-agent-worker ×N loads the agent registry. One image runs any agent, so a new agent is a folder, not a deployment.
4. **Tools:** the fcrm_tools package. Each tool is a thin read-only client to the policy, scoring, case or audit service.
5. **Governed data:** a Mongo database per service; S3 Vectors indexes owned by policy-service; MinIO for files.
- **Guardrails rail:** the fcrm_guardrails package + the OPA sidecar.
- **Trust rail:** the evidence ledger (audit-service), /evals + the eval runner, and OTel → Logfire. These are separate lifecycles: runtime, dev-time and ops.

---

## 3. Services, workers and packages in detail

### S1 case-service (`fcrm-case-service`, GenericService clone) [BP §3, §7, §9]
- **Owns:** case lifecycle, submission versions, field values, documents (object-store refs), clarification threads, conditions, and the SSE event stream to the UI. **[Corrected v1.3: submission versions keep the extracted value and its source when the owner corrects a value; "conditions" becomes a "condition tracker with reminders"]**
- **Exposes:** REST + SSE.
- **Publishes:** `case.submitted`, `case.answered`.
- **Database `fcrm_case`:** cases · submission_versions · field_values · documents · clarification_threads · conditions.
- **Redis:** SSE fan-out pub/sub, needed from 2 replicas.
- **Response models per role:** the PO models have **no score fields at all** (tested) [DL D-62].
- **Never:** scores anything or calls a model.

### S2 scoring-service (`fcrm-scoring-service`)
- **Owns:** the deterministic engine, the band table, routing rules and scoring model versions.
- **API:** `POST /score` (pure, idempotent) · `GET /models/{v}`.
- **Database `fcrm_scoring`:** scoring_model_versions · routing_rules.
- **Never:** stores case data or calls a model.
- No database read happens inside the score function.

### S3 policy-service (`fcrm-policy-service`)
- **Owns:** policy corpus versions, the controls inventory, the jurisdiction list (FATF), and search over S3 Vectors.
- **API:** `/policy/search` · `/controls` · `/jurisdictions`. It also backs field_rules (field set version) and precedent_search (closed-case index).
- **Database `fcrm_policy`:** corpus_versions · clauses (text + embedding) · controls · jurisdiction_list_versions · field_set_versions · precedent_index.
- **Queries** the S3 Vectors indexes (written by H4). It does rank fusion with a Mongo text index.
- **Never:** decides applicability. That is the agent plus a human.

### S4 audit-service (`fcrm-audit-service`)
- **Owns:** the evidence ledger (proposals), append-only audit records with `prev_hash`, the verify endpoint and the examiner read API.
- **API:** `POST /records` (append only) · `GET /verify/{case}`. There is no update or delete endpoint.
- **Database `fcrm_audit`:**
  - proposals (the evidence ledger)
  - audit_records (seq per case, prev_hash)
  - an **insert-only database user** with no update/delete privilege, plus **schema validation** on the collection [BP §7, DL D-53]
- The audit record is never deleted.

### S5 decision-service (`fcrm-decision-service`)
- **Owns:** pack assembly from locked numbers, votes (hidden until N), the tally, the outcome and condition activation. **[Corrected v1.3: pack assembly from locked numbers *and pinned versions*; committee question threads and pack addenda (rule R6); votes hidden until *all* are cast, *rationale required*]**
- **API:** `/packs` · `/votes` · `/outcome`.
- **Database `fcrm_decision`:** packs · votes · outcomes.
- **Never:** reveals a vote early or calls a model.

### H1 orchestrator, Caseflow (`fcrm-orchestrator`, original code) [BP §3, §4; DL D-10, D-11]
- One state machine per case. Steps are **idempotent handlers**. **[Corrected v1.3: the states are listed in §1.8. H1 also evaluates routing rules v4 (R1–R6) as code (D-19).]**
- State is **event-sourced in `case_runs`**, and an **outbox collection** relays to RabbitMQ.
- Handles gates, retries, fallback and the token budget.
- **Consumes** case events · **publishes** `agent.run` jobs · **waits on** `gate.decided`.
- About **600 lines**: CaseState enum · Step protocol (idempotent, typed in/out) · Gate (waits for a human event) · Budget · event store + outbox.
- No workflow framework. The panel can read the whole flow in one file.
- **Database `fcrm_harness`:** case_runs (event-sourced) · agent_jobs (atomic claims, as in AEO) · gates · budgets · outbox.
- **Never:** reasons. It is plain code.
- It builds each agent job payload (working memory) [BP §4b].

### H2 agent-worker ×N (`fcrm-agent-worker`: job glue + the Pydantic AI adapter)
- Runs one agent job: load the prompt, check permits (OPA), call tools, validate output, write proposals.
- **Consumes** `agent.run` · **publishes** `agent.done`.
- **Never** writes to case or audit directly; it goes only through service APIs.
- Based on the Generic Job Worker: FastStream/RabbitMQ consumer, `jobs/registry.py`, Mongo atomic-claim job state.

### H3 ingestion-worker (`fcrm-ingestion-worker`, clone of evoq_ingestion)
- Converts PDF · DOCX · XLSX · PPTX · CSV · text → DocumentModel → canonical Markdown with `--- Page N ---` markers.
- **Changes from AEO:** drop sources/jira and confluence; add line offsets per page so sources can be cited as page:line.
- Used by document_reader, policy corpus ingestion and clarification attachments.

### H4 context-worker (`fcrm-context-worker`, "clone of evoq_context + MongoVectorStore" per BP §10)
- Chunking (heading · paragraph · page · table segmenters, hierarchy enrichers).
- Embeddings with Bedrock Titan v2 via evoq_context's own LiteLLM SDK client.
- Writes the S3 Vectors indexes.
- **Changes:** add a clause-level segmenter and metadata filters (corpus version, jurisdiction, trigger type).

### X1 identity (`fcrm-identity`, clone of evoqidentityservice)
- Token issue and `/core/validate`, plus MSAL (Entra) routes.
- Every service validates through the interceptor in `interceptors/apisecurity.py`.
- **Add:** roles fcrm_po · fcrm_analyst · fcrm_committee; seed 3 demo users.
- It brings Redis with it.

### X2 OPA sidecar (in `fcrm-platform`)
- Default-deny Rego. Rules are by service · method · path · role in `permissions.json`.
- **Add:** FCRM roles and route rules, plus a new package **`tools.rego` + `tool_permits.json`**.
- The tool-permit query is `data.tools.allow` with `{agent, tool, case, role}` (input.agent, input.tool).
- The harness calls it before every tool call.

### fcrm-platform
nginx conf · OPA (clone) · Mongo init scripts · Logfire config · shared compose fragments.

### fcrm-evals (X6)
Datasets · runner · reports.

### fcrm-web (W1)
- The BA's React code, with only the service layer swapped [DL D-63].
- `fcrm_web/src/shared/` holds the BA's UI kit (Button · Card · Tabs · Modal …) and the SSE client.

### Packages P1–P6 [BP §4, §10]

All are host-import-free (AST test), published as versioned wheels, and pinned by the services.

| Package | Contents |
|---|---|
| **fcrm-agents** (`fcrm_agents/registry.py`, `fcrm_agents/common/`) | AgentSpec registry; prompt rendering, output validation, retry, token accounting |
| **fcrm-tools** (`fcrm_tools/registry.py`, `fcrm_tools/common/`) | ToolSpec registry; service clients, timeouts, read-only enforcement |
| **fcrm-skills** (`fcrm_skills/`) | Instruction packs |
| **fcrm-prompts** (`fcrm_prompts/`) | Markdown prompts with front-matter |
| **fcrm-guardrails** (`fcrm_guardrails/`, `fcrm_guardrails/rules/`) | One module per guard, one test file per guard, one thresholds config file |
| **fcrm-contracts** | The FCRM proposal schema, alongside AEO `contracts/` (Canonical Document, Index Contract v2, context bundle, golden fixtures), which is consumed as is |

Other shared packages:
- `fcrm_common/` holds ids.py · time.py · hashing.py · errors.py · pagination.py. It is tiny, typed and has no business logic.
- `fcrm_services_common/` is the GenericService fork: auth interceptor, otel, mongo repo base, settings.

**Neither has a repo listed in §10.**

### 3.1 Events

Named in the BP:
- `case.submitted` and `case.answered` (S1 publishes)
- `agent.run` (H1 publishes)
- `agent.done` (H2 publishes)
- `gate.decided` (H1 waits on it)

Not specified: exchange and queue names, ingestion and context job events, decision or vote events, watchlist triggers, payload schemas.

---

## 4. Stack and versions [BP header, §4, §10; DL D-56, D-20]

**Language and tooling**
- Python 3.13 everywhere, managed with **uv** (pyproject.toml + uv.lock, `uv sync`, `uv run`; a separate dev dependency group).
- Pydantic 2.x; pydantic-settings for env config.
- FastAPI (GenericService template: routes → services → repository · lifespan · pydantic-settings · OTel tracer · structured logs · masterdata seed on startup).
- React (the BA's code).

**Data and messaging**
- MongoDB 8.
- RabbitMQ with FastStream.
- Redis.
- MinIO.
- AWS S3 Vectors.
- nginx.
- OPA.

**Agents**
- Pydantic AI 2.x (2.0 GA June 2026); avoid its beta modules.
- Providers `anthropic:*` · `openai:*` · `bedrock:*`.
- FallbackModel for fallback chains.
- The Pydantic AI cached model wrapper for eval and demo caching.
- Jinja for prompt rendering.
- Embeddings: Bedrock Titan v2 via the LiteLLM SDK inside evoq_context. This is the only remaining LiteLLM use [BP §11].

**Guardrails, evals and observability**
- presidio for PII masking.
- pydantic-evals + pytest; DeepEval and Ragas via the evoq_evals adapters.
- Logfire via OTLP.

**Quality and tests**
- ruff, ruff format, mypy (strict on engines), pytest (≥85% coverage), Semgrep (local), pre-commit, compose-check.
- Playwright for cross-repo end-to-end tests; contract tests.

**Code conventions:** PEP 8 · 88 columns · type hints · docstrings on public API · f-strings · logging, not print · specific exceptions · validate every external input with Pydantic.

**SDK survey [BP §4]**

| SDK | Verdict |
|---|---|
| Pydantic AI 2.0 | Pick |
| Microsoft Agent Framework 1.0 (GA Apr 2026) | Runner-up; leans Azure |
| Google ADK 2.0 (May 2026) | Production path leans GCP; breaking changes |
| OpenAI Agents SDK | OpenAI-centric; no state persistence |
| Claude Agent SDK | Locked to Claude |
| LangGraph | Tracing is LangSmith-first, and we build our own orchestrator |

The survey is labelled "researched Oct 2026".

---

## 5. Agent platform [BP §4; DL D-20..D-26]

### 5.1 Rules
- The SDK runs **one agent call**: prompt, tools, typed output, retries. **It never orchestrates.**
- Exit path: swapping the SDK touches one adapter file per agent.
- Each of the **nine agents** is a folder containing spec, schema, tools, tier and evals [DL D-21]. Adding one means a folder plus a registry line.

### 5.2 Registries [DL D-24]

The same shape as AEO's `jobs/registry.py`: a dict, a spec dataclass, a `register()` decorator, and a lock test (like expected_tools.txt).

| Registry | Location | Maps name to |
|---|---|---|
| Agent | `fcrm_agents/registry.py` | **AgentSpec** (prompt id, schema, tool allow-list, model tier, eval set, owner) |
| Tool | `fcrm_tools/registry.py` | **ToolSpec** (signature, read-only flag, backing service, timeout, cost class) |
| Skill catalogue | `fcrm_skills/` | `cite_verbatim` · `declare_gap` · `compare_precedent` · `draft_for_business_reader` · `batch_questions` |
| Prompt library | `fcrm_prompts/` | Markdown + front-matter (id, version, model tier, variables, eval set), rendered with Jinja; **version pinned on every proposal** |

### 5.3 Agent table [BP §4]

**[Corrected v1.3]** The Tier column is now "Tier · model → fallback": screening, intake and watchlist are cheap · claude-haiku → gpt-4.1-mini. Research is strong · claude-sonnet → gpt-4.1. Assessment is strong · claude-sonnet → Bedrock claude-sonnet. Critic is strong + judge · gpt-4.1 → claude-sonnet. Narrative, examiner and policy_gap are mid · claude-sonnet → gpt-4.1-mini.

| Agent | Tier | Tools (read-only) | Skills | Output schema | Gate |
|---|---|---|---|---|---|
| screening | cheap | field_rules · precedent_search | declare_gap | ScreeningProposal (fast_lane: bool, reasons) | analyst confirms fast lane |
| intake | cheap | document_reader · precedent_search · jurisdiction_list · field_rules | cite_verbatim · declare_gap | ExtractionProposal[] · CategoryProposal[] · GapQuestion[] | PO confirms each |
| research | strong | policy_search · precedent_search · jurisdiction_list | cite_verbatim · declare_gap · compare_precedent | ClauseProposal[] · PrecedentMatch[] | analyst accepts each clause |
| assessment | strong | control_inventory · evidence_store · scoring_engine | cite_verbatim · compare_precedent | FactorProposal (L, I, justification) · ControlProposal[] | analyst confirms each rating |
| critic | strong + judge | evidence_ledger · precedent_search · rules_check | compare_precedent | CriticFinding[] (type, severity, record refs) | analyst explains or adjusts |
| narrative | mid | case_record · evidence_ledger | draft_for_business_reader · batch_questions | CompletenessReport (missing · stale · contradictory) · Question[] · Narrative · Condition[] · BusinessSummary | analyst decides whether to ask first; edits and locks |
| examiner | mid | audit_log | cite_verbatim · declare_gap | ExaminerAnswer (sentences, each with record ids) | none (read-only) |
| watchlist | cheap | jurisdiction_list · case_search | — | ReflagProposal[] | analyst acknowledges |
| policy_gap | mid | policy_search · case_search | declare_gap | GapSuggestion[] | policy owner reviews |

### 5.4 Model tiers and fallbacks
- The tiers are **cheap · mid · strong · judge**.
- Each agent's config has a model string + a fallback string [DL D-25]. FallbackModel provides the chain [BP §4]. Fallback order is part of Guard 3. If the fallback also fails, the job goes to a human.
- Vendor keys are used directly [DL D-26].
- **No concrete model IDs are given anywhere** for the tiers, and no budget numbers are given either. **[Corrected v1.3: model families and fallbacks are now given per agent (see §5.3), but these are family names, not pinned version strings. Budgets per tier (Guard 3): cheap 6k tokens · 60 s; mid 12k · 120 s; strong 20k · 180 s; one retry each; 120k tokens per case. No budget is listed for the judge tier.]**

### 5.5 Tools suite [BP §4]

Each tool is backed by a service and is read-only.

| Group | Tool | Backed by |
|---|---|---|
| Documents and data | document_reader | evoq_ingestion (page:line refs) |
| | case_record | case-service |
| | case_search | case-service |
| | field_rules | policy-service (field set version) |
| Knowledge | policy_search | policy-service → evoq_context / S3 Vectors |
| | precedent_search | policy-service (closed-case index) |
| | control_inventory, evidence_store | **no service named** (presumably policy-service, which owns controls; evidence_store is ambiguous) |
| | jurisdiction_list | FATF, versioned |
| Deterministic | scoring_engine | scoring-service (pure) |
| | rules_check | the 8 rules as functions |
| | evidence_ledger, audit_log | audit-service (read) |

### 5.6 Prompts and schemas

- Prompt files and Proposal schema fields are not written in the BP. Only the names above are given.
- Guard 4 requires every claim to carry `source_ref` + `quote` and a confidence in range.

### 5.7 How Caseflow sequences the agents
1. H1 consumes the case event, builds the payload and writes the outbox.
2. The outbox relays `agent.run`.
3. H2 runs the guards: 1 Input, 2 Permit, 3 Budget, the agent, 4 Output, then writes proposals via S4.
4. H2 publishes `agent.done`.
5. H1 runs Guard 5 (rules) before the state change, then dispatches the critic (Guard 6).
6. The Gate waits on `gate.decided` (Guard 7).
7. Order: screening → intake → research → assessment → scoring (S2) → critic → narrative (completeness) → human review → decision → watchlist / examiner / policy_gap in oversight. **[Corrected v1.3: intake → owner review-and-submit → auto-triage (R5) → screening (R4) → research ∥ assessment (they run in parallel in demo Act 4) → scoring → critic → narrative completeness → clarification round (submission v2; agents re-run on the changed fields only) → review → narrative recommendation and lock → pack → committee → oversight. Screening now runs after intake, not first.]**

Exact sequencing beyond the state list is in the Blueprint "assessment sequence", which is not part of this set.

---

## 6. Memory [BP §4b; DL D-27..D-29]

| Kind | Holds | Lives in | Rule |
|---|---|---|---|
| Working (one agent call) | Only the fields, clauses, precedents and rules the step needs; the prompt plus tool results | Job payload built by H1; token budget per tier (Guard 3); discarded after the call | Never a rolling conversation; every call can be rebuilt from its payload |
| Short-term (one case run) | Every step, proposal, guard result, gate decision, retry and version in force | `fcrm_harness.case_runs` (event store) + `fcrm_audit.proposals` | Durable, ordered and versioned; not Redis, not in process; survives restarts; can replay a case |
| Long-term (across cases) | Precedents (closed cases with ratings and outcomes), override history, corpus versions, control evidence, jurisdiction lists | `fcrm_policy.precedent_index` + S3 Vectors · `fcrm_audit` · `fcrm_policy` | Queried by precedent_search, the critic and the examiner; versioned so it never changes a past decision |
| Learning | Override rates, eval scores per prompt version | Override rate from fcrm_audit → eval rows in X6 → new P4 prompt version | Only through a test and a versioned prompt; never an implicit store |

**Redis (X7) is a cache and a bus, not memory** [DL D-28].

Uses:
- Token-validation cache in front of X1 (60 s TTL).
- OPA decision cache (short TTL, keyed by role·route or agent·tool).
- SSE fan-out pub/sub across S1 replicas.
- Rate limits and idempotency keys for tool calls and uploads.
- A model-response cache for eval re-runs and rehearsals (off in production).

Never used for: case state, proposals, gates, audit, anything the examiner reads, or agent memory between cases.

Required? It is optional in the MVP and **required from 2 S1 replicas**.

**No Mem0, Zep, Letta or similar** in the decision path [DL D-29]. Possible later uses: UI preferences and Examiner session summaries. The BP says to revisit only with a "memory as a cited source" design.

---

## 7. Seven-guard suite [BP §5; DL D-30..D-33]

There is one package, `fcrm_guardrails/`, with one module per guard and one config file for thresholds. The guards run in the same order on every call:
1 Input → 2 Permit → 3 Budget → (Agent) → 4 Output shape → 5 Rules → 6 Critic → 7 Human gate → Audit.

| # | Guard | Checks | How | On fail | Tests |
|---|---|---|---|---|---|
| 1 | Input | Hidden instructions in uploaded text; PII before any model sees it | Pattern list + small classifier · **presidio** masking · runs after evoq_ingestion extraction | Quarantine the file, flag to the analyst | 50 injection red-team samples; PII fixtures |
| 2 | Permit | Agent X may call tool Y, read-only, within scope (case id, role) | OPA `data.tools.allow` with {agent, tool, case, role}, on the same sidecar as API authz | Tool call refused and logged | `opa test` Rego unit tests; registry lock test |
| 3 | Budget | Tokens per agent run and per case; wall time; retry count; fallback chain order | Config per tier · counters in MongoDB · Pydantic AI usage | Fallback model, then hand to a human | Unit tests with fake usage |
| 4 | Output shape | Fits the Pydantic schema; every claim has `source_ref` + `quote`; confidence in range | Pydantic validation; the **quote must exist in the referenced source text** | Retry once with the error shown, then a human | Schema and quote-grounding tests |
| 5 | Rules as code | The 8 BA rules | 8 pure functions in `fcrm_guardrails/rules/`, run before every state change | State change refused | One test per rule; property tests |
| 6 | Critic | Sources are real, precedent deviation, rules kept, evidence recency | A separate agent and prompt on a judge-tier model | Findings shown; the analyst must respond | Eval set with planted errors |
| 7 | Human gate | A person accepted or overrode each item; a reason on override; band change acknowledged | Orchestrator waits on `gate.decided` | The case cannot advance | State machine tests |

Panel line: "Seven guards, same order every time; three are plain code, one is a policy engine, one is a second model, one is a person, and all of it is tested."

**The BA's eight rules as code** [BP §5 guard 5; DL D-33]:
1. No agent wrote a score.
2. No bulk accept.
3. The PO view has no restricted fields.
4. The analyst never edited the submission.
5. A closed case is untouched.
6. An override has a reason.
7. A planned control is not scored.
8. Policy is quoted, not generated.

Related decisions:
- No source, no claim: unsourced output is rejected before a human sees it [DL D-32].
- Output policy checks and rate limits are also in scope [BP §1].

**OPA policies**
- Routes: `permissions.json` (service · method · path · role), default deny.
- Tools: `tools.rego` + `tool_permits.json`.
- A Python allow-list in the tool registry is the fallback if OPA tool permits slip [BP §11].
- Decisions are cached in Redis.

---

## 8. Scoring model [BP §6; DL D-40, D-41]

The BA's engine is ported to S2. The AI proposes inputs and the engine computes. Humans confirm inputs, never outputs.

**Inputs**

| Input | Who sets it |
|---|---|
| Likelihood 1–5 | The assessment agent proposes with a reason; the analyst confirms or overrides |
| Impact 1–5 | Same; informed by volume and customer exposure |
| Control effectiveness (CE) 0–10 per control | Proposed from inventory + evidence date; the analyst confirms. Stale evidence is flagged. **Planned controls are excluded** |
| Control weight | From the inventory; sums to 1.00 per risk area. Config, not AI |
| Risk-area weight | Config; normalised across the areas in scope for the case |

**Formulas.** Per in-scope area (customer · product · geography · channel/3rd party):
```
Inherent  = Likelihood × Impact                 // 1..25
CE        = weighted mean of control ratings
Retention = 1 − (0.6 × CE ÷ 10)                 // 1.00 .. 0.40 (40% floor)
Residual  = Inherent × Retention
Composite = Σ Residual × normalised area weight
Band      = lookup(round(Composite, 1))  // 1: 0–5 · 2: 5.1–10 · 3: 10.1–17 · 4: 17.1–25
Route     = bands 1–2 analyst closes · bands 3–4 committee
```

**Worked example**

| Area | L × I | Inherent | CE | Retention | Residual | Weight |
|---|---|---|---|---|---|---|
| Channel / 3rd party | 4 × 5 | 20 | 7.6 | 0.54 | 10.9 | 0.45 |
| Geography | 4 × 4 | 16 | 6.0 | 0.64 | 10.2 | 0.30 |
| Product | 3 × 4 | 12 | 6.5 | 0.61 | 7.3 | 0.25 |

- Composite = 4.91 + 3.06 + 1.83 = **9.8 → Band 2**. This is 0.3 from Band 3, so the boundary flag fires.
- The analyst overrides vendor DD from 7.6 to 4.5 (stale, domestic-only audit). Channel CE becomes 4.5, retention 0.73 and residual 14.6.
- Composite = 6.57 + 3.06 + 1.83 = **11.5 → Band 3 → committee**.
- **[Corrected v1.3 (D-42)]** Vendor DD is CTL-T01, which carries weight 0.35 of the area. Channel CE as a weighted mean goes from 7.59 to 6.51, retention to 0.61 and residual to 12.2; × 0.45 gives 5.49. Composite = 5.49 + 3.07 + 1.83 = **10.4 → Band 3 → committee**. That is 0.3 above the Band 3 edge, so the boundary flag stays and the band change is acknowledged (R3). The 11.5 figure was wrong because the draft treated one control as the whole area. I checked the new arithmetic by hand: 7.59 − 0.35×3.1 = 6.505; 1 − 0.6×0.6505 = 0.609; 20×0.609 = 12.19; ×0.45 = 5.485; plus 3.072 and 1.83 gives 10.39 → 10.4. Note that the pre-override line still says geography 3.06 while the post-override line says 3.07.

**Design rules**
- The function is pure and idempotent, with no database read inside it.
- Floor, band edges and weights live in a `ScoringModelVersion` document. A case pins its version, so config changes affect new cases only. A re-score needs a reason and writes a record.
- **Boundary flag:** within 0.5 of a band edge, the analyst must acknowledge. The distance is measured to the start of the neighbouring band.
- **Rounding:** round to 1 decimal before band lookup, so no value falls between 10.0 and 10.1.
- **Tests:**
  - worked-example fixtures
  - every band edge
  - the floor at CE = 10
  - a planned control is ignored
  - a property test: more control never raises the residual
  - **[Added v1.3]** every seeded case is re-scored from its inputs on startup and must match its stored band
- Full phase: a model-governance record per version (owner, approval, validation date), "in the spirit of SR 26-2".

---

## 9. Data [BP §7]

**Databases:** see §3.

- fcrm_case
- fcrm_scoring
- fcrm_policy
- fcrm_audit
- fcrm_decision
- fcrm_harness

**S3 Vectors (AWS)**
- Three indexes: `clauses` · `precedent_cases` · `controls`.
- Written by H4 through the cloned evoq_context backend "as is" [DL D-51]; queried by S3 policy-service.
- Metadata filters: corpus_version · jurisdiction · trigger_type · risk_area · closed_date.
- Text match comes from a Mongo text index, with rank fusion in policy-service.
- Embeddings: Bedrock Titan v2. Tests use the in-memory store.

**MinIO (S3 API is the contract)**
- Holds uploads, canonical Markdown and generated packs.
- AWS S3 later, by config [DL D-55].
- Bucket names are not specified.

**Redis:** see §6.

**Seed and corpus**
- The BA's **80 cases + 2 hand-written twins**, loaded by the template's masterdata loader on startup. **[Corrected v1.3 (D-57): one generated seed. P7 fcrm-seed's gen_seed.py writes seed.json, containing 80 cases with inputs and recomputed scores, the demo case with its 67 audit records, users, controls v9, the corpus v14 catalogue, field set v6, routing v4, jurisdiction lists and weekly eval figures. It is loaded by the masterdata loader, the evals runner and the walkthrough (as FCRM_SEED). The "2 hand-written twins" are gone.]**
- Policy corpus: FFIEC sections + "3 internal standards" (§1b lists 4) as Markdown via evoq_ingestion, chunked by clause. **[Corrected v1.3: "Policy corpus v14: 5 public sources + 4 internal standards, 197 clauses"]**
- FATF June 2026 list as `jurisdiction_list v2026-06`, plus a `v2026-10` fixture for the re-flag demo.

**Version pinning**
- Every reference collection is versioned.
- A case stores `{scoring_v, corpus_v, controls_v, fieldset_v, prompts_v}` [DL D-54].

**Retention**
- Audit is never deleted.
- Files follow case retention.
- A nightly `mongodump` runs.

---

## 10. Evals [BP §8; DL D-70..D-72]

**Per-agent sets in `/evals/<agent>/`**

| Agent or area | Metrics |
|---|---|
| intake | 80 cases × fields → extraction accuracy, category F1, gap-question precision |
| research | Clause relevance (labelled), citation faithfulness (the quote exists in the source), gap honesty |
| assessment | L/I within ±1 of the label, control match recall, stale-evidence detection |
| critic | Planted-error set (unsourced claim, inflated rating, rule breach) → detection rate |
| narrative | LLM-as-judge (DeepEval adapter from evoq_evals): grounded, no restricted terms in the PO summary, conditions map to weak controls |
| examiner | Every sentence cites a record; refuses when the log lacks the answer |
| guardrails | Red-team injection set (50), PII set, rule tests |

Screening, watchlist and policy_gap have **no eval set** listed. **[Corrected v1.3: screening · fast-lane precision; watchlist · re-flag recall; policy_gap · suggestion acceptance. The seeded baseline is in v1.3 section U9.]**

**Runner and gate**
- Runner: pydantic-evals + pytest, with DeepEval/Ragas through the evoq_evals adapters. Run with `make evals`.
- FCRM metrics are written on the evoq_evals base class.
- Results are committed as JSON + an HTML report per run, plus a trend file.
- **Gate:** a prompt or model-tier change must score at or above the last score on its agent's set. Enforced by a pre-commit hook + make check.

**Feedback loops**
- Live signal: analyst override rate per agent, from audit, feeds a weekly review. Large shifts open a prompt fix [DL D-71].
- Human sampling: a second analyst re-reads 5% of closed cases. Disagreements become eval rows.
- Drift report: a weekly rerun of all sets, diffed against the last run.

---

## 11. Observability, auth and deploy [BP §9; DL D-80, D-90, D-90a, D-61]

**Observability**
- Keep AEO's OTel scaffold (apptracer · applogs). Set `OTLP_*_EXPORTER_ENDPOINT` to Logfire.
- Pydantic AI spans (model, tokens, tool calls) appear automatically.
- One trace per case run, with spans per agent step.
- Span attributes: `case_id, agent, prompt_v, model, tokens, cost`.
- Dashboards: tokens and cost per case and agent · override rate per agent · gate wait times · eval scores.
- Engines stay OTel-free; host observer adapters bridge their events.

**Auth**
- identity issues tokens. Services validate them via `/core/validate`.
- Roles live in identity. OPA `permissions.json` holds the route rules.
- Response models are per role, and the PO models have no score fields.
- Demo users are seeded. MSAL/Entra is kept for later SSO.

**Deploy**
- One Dockerfile per repo at `dockerfiles/Dockerfile` (the AEO layout). Python images are a uv multi-stage build from uv.lock on Python 3.13 slim, with a non-root user and a healthcheck.
- The root `docker-compose.yaml` in the umbrella repo builds every image from its submodule path. It starts the infra (MongoDB, RabbitMQ, Redis, MinIO, OPA, nginx) plus every FCRM service and worker. S3 Vectors and the model vendors are reached with credentials from `.env`.
- nginx routes `/api/fcrm/*`.
- "compose joins the AEO network only if identity is shared; otherwise self-contained."
- Each repo has a `compose.dev.yaml` for solo runs against the shared infra.
- `make check` = ruff · ruff format · mypy (strict on engines) · pytest ≥85% · compose-check · Semgrep · pre-commit.
- Other make targets: `make up · make seed · make demo` (worked example end to end), `make evals`, `make vendor`.
- Secrets come from env and Docker secrets.
- **No ports are specified anywhere** (service ports, Mongo, RabbitMQ and so on).

---

## 12. Repos [BP §10; DL D-64]

```
fcrm-submission/ (umbrella; what the judges open)
  docker-compose.yaml   Makefile (up·seed·demo·check fan-out)   README.md
  /docs/requirements  /docs/architecture (plan, diagrams, ADRs, decision log)  /docs/governance (7 guards, 8 rules, gate rationale)
  /ai  copies of prompts · agent specs · skills · tiers
  /evals → fcrm-evals (submodule)   /tests cross-repo e2e (Playwright) + contract tests
  /ops → fcrm-platform (submodule) + token report   /src/* every repo as submodule
Repos: fcrm-web (W1) · fcrm-case-service (S1) · fcrm-scoring-service (S2) · fcrm-policy-service (S3) · fcrm-audit-service (S4) · fcrm-decision-service (S5) · fcrm-orchestrator (H1) · fcrm-agent-worker (H2) · fcrm-ingestion-worker (H3) · fcrm-context-worker (H4) · fcrm-agents · fcrm-tools · fcrm-skills · fcrm-prompts · fcrm-guardrails · fcrm-contracts (P1–P6) · fcrm-identity (X1) · fcrm-platform · fcrm-evals (X6)
```

That makes **19 repos plus the umbrella**. **[Corrected v1.3: 20 repos plus the umbrella. `fcrm-seed` (P7: gen_seed.py · seed.json · fixtures) is added, and the library rule now reads "P1–P7".]**

**Rules every repo follows**
- src/ layout; tests mirror src; unit and integration tests are split; fixtures live only in conftest.
- Registry lock files.
- Every repo builds alone.
- Commit small and daily, and bump the umbrella's submodule pointers daily [DL D-91].

**Cloning rule**
- Each clone has an `UPSTREAM.md` (source path, commit, what changed).
- Clones keep their original test suites.

**Submission**
- Develop with submodules.
- `make vendor` replaces each with a plain copy before the deadline.
- Each repo's history stays public and is linked from the README.

**Workstreams:** Web · Services · Harness · Agents and packages · Data and evals · Docs and governance.

---

## 13. AEO reuse: exact list [BP §2; DL D-52, D-61, D-72, D-31]

AEO code is cloned and changed in our own repos. **The running AEO platform is never imported or called.** Both engines are portable (host-import-free, AST-enforced).

| AEO component (source path) | Target | Changes |
|---|---|---|
| evoq_ingestion (`aeoingestionjob/evoq_ingestion`) | fcrm-ingestion-worker; the document_reader tool; corpus ingestion; clarification attachments | Clone the package; drop sources/jira and confluence; add line offsets per page (page:line); keep its tests (85% coverage, hard limits, lower_snake failure codes) |
| evoq_context (`aeocontextjob/evoq_context`) | fcrm-context-worker; the clause, precedent and control indexes | Clone; use the S3 Vectors backend as is; add a clause-level segmenter + metadata filters (corpus version, jurisdiction, trigger type); embeddings via the existing LiteLLM SDK (Bedrock Titan v2) |
| OPA sidecar (`aeo-opaserver-dcsetup`) | fcrm-platform | Add FCRM roles and route rules; new `tools.rego` + `tool_permits.json`; called by the harness before every tool call (k8s manifests come with it) |
| evoqidentityservice | fcrm-identity | Add roles fcrm_po/fcrm_analyst/fcrm_committee; seed 3 demo users; same interceptor `interceptors/apisecurity.py` |
| GenericService template | S1–S5 (+ fcrm_services_common) | Fork per service; **the Mongo repository driver stays**; move to uv + pyproject, Python 3.13, Pydantic 2.x; fix the `print()` in the interceptor to use a logger |
| Generic Job Worker | H1/H2 (agent workers) | Same FastStream/RabbitMQ consumer, `jobs/registry.py` pattern (it becomes the agent registry), Mongo atomic-claim job state; the engine is fcrm_agents |
| evoq_evals (`mcps/evoq_aidlc_mcp/evoq_evals`) | fcrm-evals | Pattern + DeepEval/Ragas adapters; its metrics are AIDLC-specific, so write FCRM metrics on the same base class |
| OTel scaffold (`apptracer · applogs`) | All | OTLP endpoints point at Logfire; env change only; same observer protocol for agent events |
| `contracts/` | fcrm-contracts | Consume as is (Canonical Document, Index Contract v2, context bundle, golden fixtures); add the FCRM proposal schema |
| aeoservice · configservice | — | Not needed; borrow `drivers/encryption.py` only if vendor credentials are stored |

---

## 14. Decision log, all rows (array D in 03-decision-log.html; v1.2)

Rubric codes:
- H = AI harness 30%
- S = SDLC 20%
- G = Governance 15%
- E = Evals 10%
- C = Context 10%
- P = Production 5%
- T = Tokens 5%
- J = Judgement 5%

| ID | Area | Decision (one line) | Rubric | Status | Date |
|---|---|---|---|---|---|
| D-01 | Product | Keep the BA's flow, screens and 8 rules | S,G | decided | 30 Sep |
| D-02 | Product | Four outcomes, incl. Deferred and Approved with conditions; conditions tracked | S,C | decided | 30 Sep |
| D-03 | Product | AI configuration belongs to the analyst, not the PO | G | decided | 30 Sep |
| D-04 | Product | Agents live inside normal enterprise screens, not a chat window | H,G | decided | 30 Sep |
| D-05 | Product | Use case: AML/CFT + sanctions risk of changes at a US bank; FFIEC 4 domains; 6 change types; 4 outcomes | C,S | decided | 1 Oct |
| D-06 | Product | Corpus = public sources (FFIEC, OFAC, FinCEN proposal, OCC 2017-43, FATF) as cited extracts + **four** synthetic internal standards | C,G | decided | 1 Oct |
| D-10 | Harness | Orchestrator (Caseflow) is our own code; no workflow framework | H,J,P | decided | 1 Oct |
| D-11 | Harness | Four cores as modules of one orchestrator; agents as queue jobs | H,P | decided | 1 Oct |
| D-12 | Harness | Human gate per item; reason required on override; no accept-all | G | decided | 30 Sep |
| D-13 | Harness | Model fallback chains and per-case token budgets | H,T | decided | 1 Oct |
| D-14 | Harness | No Temporal | P | decided | 1 Oct |
| D-16 | Harness | Completeness check at the start of analyst review; the narrative agent lists missing/stale/contradictory items and drafts questions; one round of asking | S,G,H | decided | 1 Oct |
| D-20 | Agents | Pydantic AI 2.0 runs one agent call; never orchestrates | H,P | decided | 1 Oct |
| D-21 | Agents | Nine agents, each a folder with spec, schema, tools, tier, evals | H | decided | 1 Oct |
| D-22 | Agents | Critic agent reviews every case before the analyst | G,E | decided | 30 Sep |
| D-23 | Agents | Examiner agent answers only from the audit chain | G,C | decided | 30 Sep |
| D-24 | Agents | Four registries (agents, tools, skills, prompts) with lock tests | H,S | decided | 1 Oct |
| D-25 | Agents | Model tiers per agent in config (model string + fallback string) | T,H | decided | 1 Oct |
| D-26 | Agents | Direct vendor keys via Pydantic AI providers; no LiteLLM proxy, no OpenRouter | H,P,T | decided | 1 Oct |
| D-27 | Memory | Four memory kinds: working, short-term, long-term, learning | H,G,C | decided | 1 Oct |
| D-28 | Memory | Redis (X7) as cache and bus only; optional in MVP | P | decided | 1 Oct |
| D-29 | Memory | No Mem0-style agent memory in the decision path | G,J | decided | 1 Oct |
| D-30 | Guardrails | Seven-guard suite in one package, same order every call | G,H | decided | 1 Oct |
| D-31 | Guardrails | OPA (cloned) for API authz and agent→tool permits | G,P | decided | 1 Oct |
| D-32 | Guardrails | No source, no claim: unsourced output rejected before a human sees it | G,C | decided | 30 Sep |
| D-33 | Guardrails | The 8 rules as pure functions run before every state change | G,J | decided | 1 Oct |
| D-40 | Scoring | Deterministic scoring in its own service; agents only propose inputs | J,G | decided | 30 Sep |
| D-41 | Scoring | Round to one decimal before band lookup; boundary flag at 0.5; planned controls not scored | J | decided | 30 Sep |
| D-50 | Data | MongoDB 8, our own instance, one database per service (FARM); AEO Mongo repository + job-claim pattern reused; insert-only user enforces the audit chain | P,H | **changed** | 1 Oct |
| D-51 | Data | S3 Vectors for the three indexes via the cloned evoq_context backend as is | H,C | decided | 1 Oct |
| D-56 | Stack | FARM strictly: FastAPI · React · MongoDB · Python 3.13 · uv · Pydantic 2.x | P,S | decided | 1 Oct |
| D-52 | Data | Clone evoq_ingestion and evoq_context into our repos (never call AEO) | C,S | decided | 1 Oct |
| D-53 | Data | Hash-chained append-only audit with a verify endpoint | G,P | decided | 30 Sep |
| D-54 | Data | Every reference table versioned; cases pin their versions | G,J | decided | 30 Sep |
| D-55 | Data | MinIO as the object store; the S3 API is the contract | P | decided | 1 Oct |
| D-60 | Services | Five HTTP services: case, scoring, policy, audit, decision | P,H | decided | 1 Oct |
| D-61 | Services | Clone evoqidentityservice for tokens and roles | P | decided | 1 Oct |
| D-62 | Services | PO API responses contain no score fields at all | G | decided | 30 Sep |
| D-63 | Services | Reuse the BA's React code; swap only the service layer | S | decided | 30 Sep |
| D-64 | Repos | One repo per service/worker/package; umbrella with submodules in development, vendored at the deadline | P,S | decided | 1 Oct |
| D-70 | Evals | Golden set per agent from the 80 synthetic cases; results committed; gate on prompt change | E,S | decided | 1 Oct |
| D-71 | Evals | Analyst override rate per agent as the live quality signal | E,H | decided | 30 Sep |
| D-72 | Evals | Reuse the evoq_evals adapters and pattern (cloned) | E | decided | 1 Oct |
| D-80 | Observability | OpenTelemetry to Logfire; one trace per case | T,P | decided | 1 Oct |
| D-90 | Deploy | Docker Compose, one command; local quality gate (ruff, mypy, pytest, Semgrep, pre-commit); no CI, no Trivy | P,S | decided | 1 Oct |
| D-90a | Deploy | One Dockerfile per repo; one root docker-compose.yaml; compose.dev.yaml per repo | P,S | decided | 1 Oct |
| D-91 | Deploy | Commit small and daily; prompts and eval results are code | S | decided | 30 Sep |
| D-95 | Docs | Final set: Build Plan · Blueprint · Decision Log · Walkthrough · Prototype Teardown · Flow Map; Scope, Agent Stack and standalone Architecture retired | S | decided | 1 Oct |
| D-92 | Scope | Out: mobile layout, notification settings, Temporal, GitHub Actions, Trivy, OCR, multi-language | S | decided | 1 Oct |

**Totals: 49 rows, not 51. 48 are decided, 1 is changed (D-50), 0 are open, 0 are dropped.** No row is marked superseded. **[Corrected v1.3: the table above is v1.2. v1.3 has 57 rows, which matches the README's "57 decisions": 55 decided, 2 changed (D-42, D-50), 0 open. The new rows are D-17, D-18, D-19, D-19a, D-42, D-57, D-96 and D-97, and D-16's text changed. See v1.3 section U1.]**

D-50 is "changed", but its text already describes the current state (MongoDB). No replacement row is linked; D-56 is the closest match.

Change log entries:
- v0.1 (30 Sep): BA review decisions (D-01..04, D-12, D-22, D-23, D-32, D-40..42, D-54, D-55, D-62, D-63, D-72, D-92).
- v0.2 (1 Oct): scope fixed; reuse check; guards; registries; evals and deploy.
- v0.3 (1 Oct): "database to PostgreSQL independent of AEO (D-50, D-51 open)"; orchestrator original (D-10); SDK research (D-20).
- Nothing is logged for v0.4–v1.2. **[Corrected v1.3: two entries are added. "v1.0–v1.2 (1 Oct): MongoDB and FARM strictly (D-50, D-56); S3 Vectors (D-51); MinIO (D-55); memory model (D-27..29); direct vendor keys (D-26); Dockerfile per repo (D-90a); final document set (D-95); completeness check (D-16)". "v1.3 (2 Oct)", covering the walkthrough v2 decisions. v0.4–v0.9 still do not appear.]**

**Decisions still flagged "to confirm" in BP §11**, although they are decided in the DL:
- Five services vs three (recommend five; D-60 decided).
- OPA for tool permits (recommend OPA; fallback is a Python allow-list; D-31 decided).
- Config screens: in scope; build last.
- Semgrep local without CI.
- Model access and the vector store are marked decided in §11.

---

## 15. Phasing, milestones and risks

- **There is no phased plan, milestone schedule or gantt** in these three documents. The Mermaid theme has gantt styling, but no gantt diagram exists.
- What there is:
  - the "MVP" / "MVP + Phase 2" / "Full phase" / "Later" chips in §1
  - "Nine days" [§3b]
  - the deadline of 9 Oct 2026 [README]
  - "Build [Config screens] last; seed files cover the demo if time runs out" [§11]
  - six workstreams [§10]
  - daily commits and daily submodule bumps
- **There is no risk register.** Implicit risks and fallbacks:
  - S3 Vectors needs AWS credentials from day one; the in-memory store covers tests [§11, D-51].
  - If OPA tool permits slip, fall back to a Python allow-list [§11].
  - The Config screens are heavy, and seed files are the fallback.
  - Model failure is handled by FallbackModel, then a human [Guard 3].
  - Redis becomes required beyond one S1 replica.

---

## 16. Inconsistencies and gaps

1. **Decision count.** The README says "51 decisions". The DL array has **49**: there are no D-07..09, D-15, D-17..19, D-34+, D-42–49, D-57+. The change log cites **D-42**, which does not exist. **[Corrected v1.3: the README says 57 and the array has 57; D-42 now exists.]**
2. **D-50 status is "changed"**, but the row text is the current MongoDB decision. The change log v0.3 says D-50 changed to **PostgreSQL** and D-51 was "open". Both were later reversed, back to Mongo and S3 Vectors, with no change-log entry. This breaks the log's own rule that the "old row [is] kept, new row linked". The change log stops at v0.3 while the header says v1.2. D-16, D-26..29, D-56, D-64, D-90a and D-95 have no change-log entry.
3. **Date mismatch.** The change log v0.1 (30 Sep) lists D-55, D-72 and D-92, but those rows are dated 1 Oct.
4. **Internal standards count.** §1b and D-06 list **four** (4.2, 7.1, 2.3, 1.8). §7 says "FFIEC sections + **3** internal standards". **[Corrected v1.3: §7 now says 5 public + 4 internal, 197 clauses.]**
5. **Controls count.** The heading says "24 controls", but **20** are listed.
6. **Vector store leftovers.**
   - §10 calls fcrm-context-worker "clone of evoq_context **+ MongoVectorStore**".
   - §7 lists `fcrm_policy.clauses (text + embedding)`.
   - Both conflict with "S3 Vectors as is" (D-51) and suggest a Mongo or Postgres vector store from an earlier version.
   - The precedent index is named both `fcrm_policy.precedent_index` and S3 Vectors `precedent_cases`.
7. **ID collision.** The component ID "S3" (policy-service) is used next to "S3 Vectors" and "AWS S3". "Rank fusion in S3" is ambiguous.
8. **LiteLLM.** "No LiteLLM" (D-26, README) vs LiteLLM SDK embeddings in evoq_context. The BP §11 states this carve-out explicitly; the README does not.
9. **RabbitMQ (X3)** is core to the design but has no decision row, and the README doesn't mention it. FastStream is mentioned only as the AEO template.
10. **"Never call AEO" vs:**
    - "Integrations (SharePoint via AEO ingestion)" on the PO screen
    - "compose joins the AEO network only if identity is shared"
    - Also, the ingestion clone drops jira/confluence, but no SharePoint source is said to exist in it.
11. **BP §11 "Decisions to confirm"** still presents five-vs-three services and OPA tool permits as recommendations, though D-60 and D-31 are decided.
12. **Completeness check ordering.**
    - D-16 and H1 put the completeness check at the start of review, after the critic.
    - Demo Act 6 places it after the analyst override (Act 5), while saying "at the top of the review".
    - "Two clocks" is never defined.
   - **[Corrected v1.3: the state machine and demo now put the completeness check and clarification round before review and the override (Acts 5→6).]**
13. **Screening (fast lane)** is not in the state machine (intake → … → oversight). Research and assessment sit under a single "assess" state. The CaseState values are not enumerated.
14. **Missing roles.** The "policy owner" (policy_gap gate) has no role. There are only 3 roles, and no examiner or regulator role.
15. **Unspecified tools.** `control_inventory` and `evidence_store` have no backing service named. There is no evidence collection in any database.
16. **Missing concrete model IDs** per tier (cheap/mid/strong/judge), fallback strings, token budget numbers, wall-time limits, retry counts, and the guard thresholds file contents.
17. **No ports** anywhere. Also missing:
    - RabbitMQ exchange and queue names
    - event payload schemas
    - the Proposal schema fields beyond source_ref/quote/confidence
    - the "N" for hidden votes (the demo shows 3 voters)
    - MinIO bucket names
    - S3 Vectors bucket and index config
18. **Missing env items.** The README's `.env` prerequisites omit a **Logfire token/OTLP endpoint**. AWS creds are listed, but region and bucket are not.
19. **Version pinning gap.** The case pin set `{scoring_v, corpus_v, controls_v, fieldset_v, prompts_v}` omits the **jurisdiction list version**, the routing_rules version and the model tier/version. That matters for the watchlist re-flag, and for "nothing retroactive" (D-54).
20. **Boundary flag above an edge.** The rule says "measured to the start of the neighbouring band". For a score just above an edge (e.g. 10.2), the lower neighbour's start (5.1) is meaningless, so this case is ambiguous.
21. **Worked example ambiguity.** Overriding one control (vendor DD) from 7.6 to 4.5 sets the *whole* channel CE to 4.5. That only works if vendor DD is the sole rated control in that area or carries all its weight; CE is otherwise a weighted mean. Geography is 10.24×0.30 = 3.07, which the plan rounds to 3.06 (using 10.2). That is harmless. **[Corrected v1.3 (D-42): CE is now a weighted mean with CTL-T01 at weight 0.35, giving 10.4.]**
22. **Missing evals** for screening, watchlist and policy_gap, although D-70 says a "golden set per agent". **[Corrected v1.3: metrics are now listed for all three.]**
23. **Shared packages without repos.** `fcrm_common` and `fcrm_services_common` are named in §4 but have no repo in §10. Either they are absent from the 19 repos or they live inside other repos; this is unclear.
24. **Workbench tabs.** "Workbench (8 tabs)" is never enumerated in the BP.
25. **Phase labels.** The Harness chip says "MVP + Phase 2", but no phase definitions or milestones are given.
26. **Missing file.** The README lists `05-prototype-teardown.html`, which is not in the local Documents folder.
27. **Date oddities.** The SDK survey says "researched Oct 2026" while the decisions are dated 1 Oct. Minor.
28. **Ingestion sequencing.** H3 and H4 are queue workers, but their trigger events and the jobs that call document_reader are not described. document_reader is listed as a tool "→ evoq_ingestion", which suggests it either calls the worker synchronously or reads the stored canonical Markdown. This is unclear.

---

## v1.3 update (approved docs, 3 Oct)

**Method.** I diffed the old copies in `/Users/apahuja/Downloads/Hackathon/FCRM_v2/docs/hackathon/` (README; BP; DL v1.2, dated 1 Oct) against the new copies in `/Users/apahuja/Downloads/Hackathon/Documents/` (README; BP; DL v1.3, files dated 3 Oct 00:42).
- The HTML was converted to text with `B_h2t.py`, because `textutil` hung.
- I parsed the `D` and `CH` arrays row by row and compared them field by field.
- The working files are in `scratchpad/v13/` (`old-*.txt`, `new-*.txt`, `*.scripts.txt` and `dd.py`).
- The BP's Mermaid init script is unchanged. Both Mermaid diagrams (the service flowchart and the guard chain) are unchanged, so P7 does **not** appear in the BP service diagram.

### U1. Decision log v1.2 → v1.3

- **Counts:** 49 → **57 rows**. The README now says 57, so the two agree. Status: 55 decided, **2 changed** (D-42, D-50), 0 open, 0 dropped. No row was removed. Existing rows keep their order; the new rows are inserted in ID position.
- **New rows (8):**

| ID | Area | Decision (full one line) | Rubric | Status | Date |
|---|---|---|---|---|---|
| D-17 | Harness | Review-and-submit step: the owner sees the whole case with every value's origin and may correct values before submitting; a correction becomes "owner declared", the extracted value and its page:line are kept and listed for the analyst | G,S | decided | 2 Oct |
| D-18 | Harness | Committee members may question the analyst from the pack; the thread is visible to all members and the answer is attached as a pack addendum; a number change means unlock with reason, pack v2 and every cast vote re-requested (routing rule R6) | G,H | decided | 2 Oct |
| D-19 | Harness | Routing rules v4 as versioned data (R1–R6): delegated close, committee quorum and hidden votes, boundary and band-change acknowledgement, fast lane proposed by the screening agent, auto-triage by lowest load, committee question handling; evaluated by code in H1 | G,J | decided | 2 Oct |
| D-19a | Harness | Recommendation lock: once the analyst locks, numbers in the pack are read-only; unlocking requires a reason and regenerates the pack; conditions carry owner, due date and reminders 14 days before due (oversight core) | G,P | decided | 2 Oct |
| D-42 | Scoring | Composite is the weighted sum of area residuals; area effectiveness is the weighted mean of its control ratings (control weights from inventory v9, domain weights normalised across domains in scope); the demo override moves 9.8 → 10.4, not 11.5 | J,E | **changed** | 2 Oct |
| D-57 | Data | One generated seed (P7 fcrm-seed · gen_seed.py → seed.json): 80 cases with recomputed scores, the demo case with its 67 audit records, users, controls v9, corpus v14 catalogue, field set v6, routing v4, jurisdiction lists and weekly eval figures; loaded by the services, the evals runner and the walkthrough | E,C,S | decided | 2 Oct |
| D-96 | Docs | Walkthrough v2 from the BA and architect is the anchor: 21 scenes (review-and-submit, clarification before review, committee asks, vote), seed-driven figures; the same HTML runs as a click-through prototype with FCRM_PROTO (no cursor, no callouts) | S | decided | 2 Oct |
| D-97 | Docs | Walkthrough chrome (bar, captions, timeline, callouts, monitor, logo) follows the Myridius brand system; the mocked product frame keeps the BA workbench layout | S | decided | 2 Oct |

- **Changed row (1, text only): D-16.** Its status stays "decided" and its date stays "1 Oct".
  - OLD: "…drafts questions; the analyst asks in one round before reviewing"
  - NEW: "Completeness check at the start of analyst review: the narrative agent lists missing, stale or contradictory items and drafts questions; the analyst sends them as one round before Policy and Controls; the owner answers as submission v2 and the agents re-run on the changed fields only"
- **Removed rows:** none.
- **Unchanged (still relevant):**
  - D-64 (one repo per service, worker and package; umbrella with submodules; vendored at the deadline)
  - D-90a (Dockerfile per repo; root compose in the umbrella)
  - D-28 (Redis "as cache and bus only")
  - D-26 (no LiteLLM proxy)
  - D-50 (still "changed", still with no linked replacement row)
  - D-21 (nine agents)
- **Change log (CH):** two entries added.
  - "1 Oct · v1.0–v1.2 · MongoDB and FARM strictly (D-50, D-56); S3 Vectors (D-51); MinIO (D-55); memory model (D-27..29); direct vendor keys (D-26); Dockerfile per repo (D-90a); final document set (D-95); completeness check (D-16)"
  - "2 Oct · v1.3 · From walkthrough v2 (BA + architect): review-and-submit (D-17); clarification round before review (D-16 updated); committee question with pack addendum (D-18); routing rules v4 (D-19); recommendation lock and condition reminders (D-19a); composite corrected to 10.4 (D-42, changed); one generated seed, P7 (D-57); walkthrough as anchor and prototype (D-96); Myridius chrome (D-97)"
  - The v0.1 entry still lists "D-40..42" under 30 Sep.
- **Header:** v1.2 → v1.3. The page text is otherwise identical, including the rule that "A row changes status to changed when a later decision replaces it (old row kept, new row linked)".

### U2. README
- Blueprint row: adds "incl. P7 seed" and "(full case cycle, 21-scene order)".
- Decision log row: "51 decisions … v1.2" → "57 decisions … v1.3".
- Walkthrough row: "20-scene guided demo" → "21-scene guided demo (v2, by the BA and architect, 1 Oct) … every figure from the embedded seed; set `window.FCRM_PROTO=true` to run it as a click-through prototype".
- New bullet in "Decisions in force": "Anchor: walkthrough v2 from the BA and architect (D-96). Changes carried into every document: owner review-and-correct before submit (D-17); clarification round before Policy and Controls (D-16); committee question answered as a pack addendum, pack v2 and re-vote if a number changes (D-18); routing rules v4 R1–R6 incl. fast lane and auto-triage (D-19); recommendation lock and condition reminders (D-19a); composite after override corrected to 10.4 (D-42); one generated seed, package P7 fcrm-seed (D-57)."
- Superseded pages: adds "The earlier 20-scene walkthrough is replaced by v2 in this set."
- **Unchanged:**
  - The Repos line: "one per service, worker and package; one Dockerfile per repo; a single docker-compose.yaml at the project root (umbrella repo). Submodules during development, vendored copies at the deadline".
  - "Redis (X7) as cache and bus only".
  - "No LiteLLM proxy, no OpenRouter".
  - The .env prerequisites. Logfire is still not listed.
  - RabbitMQ is still not mentioned.

### U3. Scope chips [BP §1]
- **Screens:** the PO Case detail gains a **Review & submit** tab.
- **Harness:** "human gates (completeness check and one clarification round before review; owner review-and-correct before submit; committee question answered as a pack addendum) · auto-triage by load · fast lane · …". The rest is unchanged.
- **Data:** "80 synthetic cases seeded" → "one generated seed (gen_seed.py: 80 cases, controls v9, corpus v14, routing v4, users) shared by the services, the evals and the walkthrough".

### U4. Reference data and routing [BP §1b]
- **Control inventory v9 · 24 controls.** IDs: CTL-G01…G05 geography · CTL-T01…T05 third party · CTL-P01…P06 product · CTL-C01…C08 customer. The named list is unchanged at 4/4/5/7 = 20 names.
- **Scoring model v3:**
  - Domain weights: customer 0.20 · product 0.20 · geography 0.24 · channel 0.36, normalised across the domains in scope. For the demo's three domains that gives 0.36/0.80 = 0.45, 0.24/0.80 = 0.30 and 0.20/0.80 = 0.25, which matches the worked example.
  - "Composite = Σ area residual × normalised weight; control effectiveness per area = weighted mean of control ratings".
- **Field set v6:** "18 required fields max · **80% to submit**"; field IDs F01–F06 always required, F10–F62 per change type.
- **New table: Routing rules v4.**

| Rule | Text | Walkthrough |
|---|---|---|
| R1 | Band 1 or 2: the analyst closes under delegated authority | Scene 9 · score 9.8 would have closed with the analyst |
| R2 | Band 3 or 4: the Risk Committee decides; **quorum 3; simple majority; votes hidden until all are cast** | Scenes 16–18 |
| R3 | Boundary flag (within 0.5 of the next band edge): the analyst must acknowledge before release; a band change must be acknowledged too | Scene 14 · 10.4 is 0.3 above the Band 3 edge |
| R4 | Fast lane: the screening agent proposes it when the only change is a vendor renewal or a feature with no new geography, segment or product and **annual volume under $5M**; the analyst confirms; Band 1 expected | Scene 8 · KYC vendor renewal; the demo case does not qualify |
| R5 | Auto-triage: a new case goes to the analyst with the fewest open cases | Scene 7 · Rahul, 4 open cases |
| R6 | Committee question to the analyst: thread visible to all members; answer attached as a pack addendum; if a number has to change, the recommendation is unlocked with a reason, pack v2 is generated and every cast vote is re-requested | Scene 17 |

"Rules are data in fcrm_scoring.routing_rules, versioned like the scoring model; a case pins the version it ran under. They are evaluated by code in H1, never by an agent."

### U5. Demo story arc: 8 acts → 10 acts, mapped to scenes
1. Remittances US → Mexico via Remesas del Norte (vendor + geography + product). Scenes 1–2.
2. Intake finds 15 values with sources, **9 fields filled, 3 reused from case 0118, 3 gap questions**; cash pickup appears. Scenes 3–6.
3. **New: Review & submit.** The owner corrects the go-live date (Q1 2027 → February 2027). The source becomes "owner declared" and the extracted value is kept. **Four code checks pass**; auto-triage to Rahul; intake graded. Scene 7.
4. Screening says no fast lane. Research and assessment **run in parallel**. Research attempt 1 is **rejected by Guard 4 (a paraphrase) and retried once on the fallback model**. 4 clauses, 1 gap declared. Score 9.8, Band 2, boundary flag. The critic flags vendor DD 7.6 vs precedent 4.9. Scenes 8–9.
5. **Completeness first:** 0 missing, 1 stale, 1 contradictory; 3 questions drafted (1 dropped as already answered); Rahul sends 2 as one round before reviewing; "his clock pauses". The owner answers as submission v2 with a scope letter. The agents re-run on the changed fields only. The planned review is recorded, not scored. Scenes 10–11.
6. Rahul reads the v1→v2 diff, accepts four clauses one by one, records the policy gap, confirms three controls and overrides vendor DD 7.6 → 4.5. **Composite 9.8 → 10.4, Band 3, committee.** He acknowledges the band change; **record #49** is hash-chained. Scenes 12–14.
7. Narrative drafts the recommendation and **3 conditions** (from the overridden control, the planned control and the policy gap). Rahul edits and locks. Code assembles **pack v1 with pinned versions**; **four release checks**. Scenes 15–16.
8. **The committee asks:** Ana questions the analyst; the answer is attached as an addendum for all three; numbers are unchanged, so no vote is voided. Hidden votes: 2 approve with conditions, 1 defer. Scenes 17–18.
9. Plain-language outcome to the owner; a condition tracker with **reminders 14 days before due**; the case is closed for assessment; the chain is verified. Scene 19.
10. Six months on, the examiner answers "why committee?" in **seven sentences, each citing a record**. "A new list" re-flags two open cases (v1.2 said "a new FATF list"). **Nine agents graded weekly, 0 regressions.** Scene 20.

The supporting queue cases are unchanged: KYC vendor renewal (fast lane, Band 1), card controls in app (Band 2, awaiting business) and crypto custody pilot (Band 4, committee). Every walkthrough figure comes from the one seed file. The scenes in the acts run 1–20, but D-96 and the README say 21 scenes; the 21st is not mapped in the BP.

### U6. Services and lifecycle [BP §3]
- **S1:** "submission versions (owner corrections keep the extracted value and its source) … condition tracker with reminders".
- **S5:** "Pack assembly from locked numbers and pinned versions, committee question threads and pack addenda (rule R6), votes (hidden until all cast, rationale required), tally, outcome, condition activation".
- **H1 state machine:** "intake → review-and-submit → triage → screening → assess → critic → completeness check → clarification round → review → decide → oversight".
- No new service, worker, event or API is added. The events are still `case.submitted`, `case.answered`, `agent.run`, `agent.done` and `gate.decided`. The database collection lists in §7 are unchanged; there is no collection for committee threads, addenda or reminders.

### U7. Agents [BP §4]
- The agent table column is now "Tier · model → fallback":
  - screening, intake, watchlist: cheap · claude-haiku → gpt-4.1-mini
  - research: strong · claude-sonnet → gpt-4.1
  - assessment: strong · claude-sonnet → Bedrock claude-sonnet
  - critic: strong + judge · gpt-4.1 → claude-sonnet
  - narrative, examiner, policy_gap: mid · claude-sonnet → gpt-4.1-mini
  - Mid and strong share the same primary family (claude-sonnet); they differ in budget and fallback.
- New: "Budgets per tier (Guard 3): cheap 6k tokens · 60 s · mid 12k · 120 s · strong 20k · 180 s · one retry each · 120k tokens per case. A rejected output is retried once with the error shown, on the fallback model; a second failure hands the step to the analyst with the raw inputs."
- Unchanged: there are still 9 agents, and the tools, skills, schemas, registries and guards are the same.

### U8. Scoring [BP §6]
- The worked example's pre-override block is unchanged: 4.91 + 3.06 + 1.83 = 9.8, Band 2, 0.3 from Band 3.
- The post-override block is replaced. It now reads: "vendor DD (CTL-T01, weight 0.35 of the area) 7.6 → 4.5; Channel CE = weighted mean 7.59 → 6.51 · ret 0.61 · residual 12.2 · × 0.45 = 5.49; Composite = 5.49 + 3.07 + 1.83 = **10.4 → Band 3 → committee** (0.3 above the Band 3 edge: boundary flag stays; band change acknowledged, rule R3)". An explanatory paragraph follows: the earlier draft treated the override as the whole area's rating and reached 11.5.
- New test: "Every seeded case re-scored from its inputs on startup must match its stored band".
- Unchanged:
  - the formulas block, including `Band = lookup(Composite)`
  - the floor of 40%
  - the bands 0–5 / 5.1–10 / 10.1–17 / 17.1–25
  - the rounding rule
  - the boundary-rule text "Measured to the start of the neighbouring band"

### U9. Data, seed P7 and evals [BP §7, §8]
- **Seed:** "One seed, generated: gen_seed.py (package P7 fcrm-seed) writes seed.json: 80 cases with inputs and recomputed scores, the demo case with its 67 audit records, users, controls v9, corpus v14 catalogue, field set v6, routing v4, jurisdiction lists, weekly eval figures. Loaded by the masterdata loader on startup, by the evals runner, and embedded in the walkthrough as FCRM_SEED". Also: "Every band in the seed is recomputed from its inputs by the scoring engine". The "2 hand-written twins" are removed.
- **Corpus:** "Policy corpus v14: 5 public sources + 4 internal standards, 197 clauses".
- **Unchanged:**
  - the version-pin set `{scoring_v, corpus_v, controls_v, fieldset_v, prompts_v}`, which still has no routing_v, although §1b now says a case pins its routing version
  - the S3 Vectors text
  - `clauses (text + embedding)`
- **Evals:** a new line, "screening · fast-lane precision · watchlist · re-flag recall · policy_gap · suggestion acceptance".
- **New seeded baseline (week of 28 Sep):**

| Metric | Value |
|---|---|
| Intake extraction | 96.4% |
| Research citation faithfulness | 99.1% |
| Assessment override rate | 17.9% |
| Critic planted-error detection | 91.7% |
| Narrative judge | 97.5% |
| Examiner grounding | 100% |
| Screening precision | 96.2% |
| Watchlist recall | 100% |
| Policy_gap acceptance | 78.6% |
| Regressions | 0 |

  "These are the numbers the Assurance screen and the walkthrough show". These are **seeded** figures, written by gen_seed.py, not results from a measured run.

### U10. Repos, deploy and §11 [BP §9–§11]
- **Repo list:** adds "fcrm-seed P7 gen_seed.py · seed.json · fixtures (the one data source for services, evals, walkthrough)". The library rule is now "P1–P7".
- **Unchanged:**
  - "Microservices need independent repos … an umbrella repo pulls every repo in as a submodule"
  - "fcrm-context-worker H4 clone of evoq_context + MongoVectorStore"
  - "make vendor" at the deadline
  - compose in the umbrella
  - "compose joins the AEO network only if identity is shared"
  - no ports
- **§11:** a new block, "Walkthrough v2 · decided". "Five HTTP services or three" and "OPA for tool permits" still say "Recommend …" with no "decided" label.

### U11. New issues introduced by v1.3
1. **D-42 is "changed" but there is no old row.** The 11.5 figure was never a row, and no new row links to it. D-16 was edited in place with status "decided" and date 1 Oct. Both break the log's own "old row kept, new row linked" rule. The v0.1 change-log entry lists D-42 under 30 Sep, but the row is dated 2 Oct.
2. **Worked-example rounding:** geography is 3.06 before the override and 3.07 after.
3. **Boundary rule wording:** R3 says "within 0.5 of the next band edge", and its example measures to the edge just crossed (10.4 vs 10.1). §6 still says "measured to the start of the neighbouring band".
4. **Pin set:** §1b says cases pin the routing version, but the §7 pin set omits routing_v (and the jurisdiction list and model versions).
5. **Unspecified checks:** "four code checks" (submit) and "four release checks" (pack) are not listed. The tie or no-majority outcome under R2 is not given.
6. **Scene count:** acts cover scenes 1–20, but the docs say 21 scenes.
7. **Collections:** S5 adds committee threads and addenda, and S1 adds reminders, but the §7 collections do not change.
8. **Model strings:** these are families ("claude-haiku", "claude-sonnet", "gpt-4.1", "gpt-4.1-mini"), not pinned IDs or `provider:model` strings. The judge tier has no budget.
9. **Seeded eval figures** are presented as what the Assurance screen shows. The panel may ask whether they were measured.
10. **Seed composition:** "80 cases … the demo case" does not say whether the demo case is one of the 80.

---

## Conflict and gap status after v1.3 (3 Oct)

### A. 00-synthesis.md §5 conflict register

| # | Conflict | Status | How / what remains |
|---|---|---|---|
| 1 | Repo shape (many repos + umbrella vs ONE repo) | **STILL OPEN (worse by one repo)** | D-64, D-90a, the README Repos line and BP §10 are unchanged. It is now **20 repos + umbrella** (fcrm-seed added). The umbrella has the brief's top-level folders, and `make vendor` flattens the submodules into one tree at the deadline. The commit history would then live in 20 separate repos, linked but not in the judged repo. |
| 2 | Demo numbers | **CHANGED (docs resolved; prototype mismatch open)** | D-42: 9.8 → **10.4**, Band 3, with the composite formula now defined (weighted sum of area residuals; CE = weighted mean; domain weights 0.20/0.20/0.24/0.36 normalised; CTL-T01 weight 0.35). I verified it by hand. The prototype code's 9.6 → 10.2 and the ~12.2 blueprint recompute are not reconciled in these three docs (D-57 says the seed recomputes all scores). |
| 3 | Demo cast | **STILL OPEN** | The BP now names Rahul (analyst, 4 open cases), Ana (committee) and reused case 0118. The case is still Mexico / Remesas del Norte. Nothing changes on the prototype side (0147 / Philippines / Pinnacle). |
| 4 | Bus: Redis vs RabbitMQ | **STILL OPEN** | The README and D-28 still say "Redis (X7) as cache and bus only". The BP still uses X3 RabbitMQ (job workers, outbox → RabbitMQ). There is still no decision row for RabbitMQ. In the BP, Redis's "bus" means SSE pub/sub fan-out and RabbitMQ carries work, but no doc states this split. |
| 5 | Vectors | **STILL OPEN** | BP §10 still says "clone of evoq_context + MongoVectorStore", and §7 still has `clauses (text + embedding)`, alongside D-51's S3 Vectors. |
| 6 | LiteLLM | **STILL OPEN (unchanged)** | The BP §11 carve-out (embeddings SDK inside evoq_context) is unchanged. The README says only "No LiteLLM proxy". |
| 7 | Agent count | **CHANGED (mostly resolved)** | Still 9 agents. The BP arc now shows screening (Act 4) and the watchlist re-flag (Act 10), and grades all 9 weekly with eval metrics for each. policy_gap appears only in evals (acceptance 78.6%), not in a demo beat. |
| 8 | Who calls scoring | **STILL OPEN** | The assessment agent still has the `scoring_engine` tool, and the H1 sequence has scoring after assessment. Neither text changed. |
| 9 | Critic / completeness timing | **RESOLVED** | H1 states: … assess → critic → completeness check → clarification round → review. The demo has the critic in Act 4, completeness in Act 5, and the override in Act 6. D-16 says "before Policy and Controls". |
| 10 | Routing | **CHANGED** | R1–R6 are now specified as versioned data evaluated by H1 code. R1 makes bands 1–2 an analyst close "under delegated authority". The tension with the brief's "to a committee decision" remains a judgement to defend. The prototype routing bug is not addressed in these docs. |
| 11 | Votes | **PARTLY RESOLVED** | R2: quorum 3, simple majority, hidden until all are cast. S5 requires a rationale. R6/D-18 add a re-vote on a pack change. Still unspecified: how the 3 votes map to the 4 outcomes, and the tie-break (e.g. a 1-1-1 split). The demo's 2 AwC + 1 defer implies majority = AwC, but this is not stated as a rule. |
| 12 | Decision log hygiene | **PARTLY RESOLVED** | Now 57 rows, which matches the README. D-42 exists, and the D-50/D-51 reversal is logged in the new v1.0–v1.2 change-log entry. Still open: D-50 is "changed" with no linked row; D-42 is "changed" with no old row; D-16 was edited in place; and the v0.1 entry lists D-42 under 30 Sep. |
| 13 | Missing concrete values | **PARTLY RESOLVED** | Now given: model family and fallback per agent, budgets per tier and per case (120k), retry policy, quorum N = 3, control IDs that sum to 24. Still missing: pinned model IDs, the judge budget, ports, queue/exchange names, event payloads, proposal schemas, bucket names, and the 4 names for the 24 controls. |
| 14 | Scope creep (SharePoint, OCR) | **STILL OPEN** | "Integrations (SharePoint via AEO ingestion)" is still in the PO screens. OCR is still out. |

### B. B §16 "Inconsistencies and gaps"

| # | Item | Status | Note |
|---|---|---|---|
| 1 | Decision count 51 vs 49; D-42 missing | **RESOLVED** | 57 vs 57; D-42 present |
| 2 | D-50 "changed", PostgreSQL reversal unlogged, change log stops at v0.3 | **PARTLY RESOLVED** | v1.0–v1.2 and v1.3 entries added; D-50 still has no linked row; D-64 is still not named in any entry |
| 3 | Change-log dates (D-55, D-72, D-92 under 30 Sep) | **STILL OPEN** | D-42 (dated 2 Oct) is now also listed under v0.1 (30 Sep) |
| 4 | Internal standards 3 vs 4 | **RESOLVED** | "5 public sources + 4 internal standards, 197 clauses" |
| 5 | Controls 24 vs 20 | **PARTLY RESOLVED** | IDs sum to 24 (5/5/6/8); only 20 names are listed |
| 6 | MongoVectorStore / clauses with embedding / precedent index named twice | **STILL OPEN** | Unchanged |
| 7 | "S3" ID collision | **STILL OPEN** | "rank fusion in S3" is unchanged |
| 8 | LiteLLM | **STILL OPEN** | Unchanged |
| 9 | RabbitMQ has no decision row; not in the README | **STILL OPEN** | Unchanged |
| 10 | "Never call AEO" vs SharePoint and the AEO network | **STILL OPEN** | Unchanged |
| 11 | §11 "to confirm" vs decided D-60/D-31 | **STILL OPEN** | Unchanged; §11 only gained "Walkthrough v2 · decided" |
| 12 | Completeness ordering; "two clocks" undefined | **RESOLVED (ordering) / PARTLY ("two clocks")** | Ordering is fixed. "His clock pauses" while the owner answers hints at the meaning, but "two clocks" is still not defined |
| 13 | Screening not in the state machine; CaseState not enumerated | **PARTLY RESOLVED** | review-and-submit, triage, screening and clarification round are now states. Research and assessment are still in one "assess" state (run in parallel). No enum |
| 14 | No policy-owner or examiner role | **STILL OPEN** | Still 3 roles |
| 15 | control_inventory / evidence_store have no backing service | **STILL OPEN** | Unchanged |
| 16 | Missing model IDs, budgets, retries, thresholds | **PARTLY RESOLVED** | Families, fallbacks, tier budgets, wall times, one retry and a 120k per-case cap are given. Missing: pinned IDs, judge budget, guard thresholds |
| 17 | No ports, queues, payloads, schema fields, vote N, buckets | **PARTLY RESOLVED** | N = 3 (quorum, R2); everything else is still missing |
| 18 | .env omits Logfire | **STILL OPEN** | README unchanged |
| 19 | Pin set omits jurisdiction, routing and model versions | **CHANGED** | §1b now says a case pins its routing version, and the pack carries "pinned versions". The §7 pin set still omits routing_v, jurisdiction_v and model versions, so the docs are now internally inconsistent |
| 20 | Boundary flag above an edge is ambiguous | **PARTLY RESOLVED** | The R3 example (10.4, "0.3 above the Band 3 edge") shows the crossed edge counts. §6 text "start of the neighbouring band" is unchanged |
| 21 | Worked example sets the whole channel CE | **RESOLVED** | D-42 uses a weighted mean (CTL-T01 weight 0.35) → 10.4. A small 3.06/3.07 rounding inconsistency remains |
| 22 | No evals for screening, watchlist, policy_gap | **RESOLVED** | Fast-lane precision, re-flag recall and suggestion acceptance, with seeded baselines |
| 23 | fcrm_common / fcrm_services_common have no repo | **STILL OPEN** | Still named in §4; not in the §10 list |
| 24 | Workbench 8 tabs not enumerated | **STILL OPEN** | Unchanged |
| 25 | Phase labels without definitions | **STILL OPEN** | Unchanged |
| 26 | 05-prototype-teardown.html missing locally | **STILL OPEN** | Still absent from `Documents/`; an old copy dated 1 Oct is in `FCRM_v2/docs/hackathon/` |
| 27 | SDK survey "researched Oct 2026" | **STILL OPEN** | Unchanged (minor) |
| 28 | Ingestion sequencing and triggers | **STILL OPEN** | Unchanged |

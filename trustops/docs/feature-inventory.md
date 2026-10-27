# TrustOps Feature Inventory

What TrustOps does, grouped by who uses it, with the status of each feature and where the evidence lives. Written from the code, the OpenAPI export, the agent specs, the gates register and `docs/governance/known-gaps.md`, as built in October 2026.

| | |
|---|---|
| Status values | **Built**: in the code and exercised by tests or the live sweep. **Partial**: built with a documented limit. **Designed only**: specified, not built. **Out of scope**: excluded by decision |
| If this file and the code disagree | The code wins, then `docs/governance/decision-log.md`, then `known-gaps.md` |
| Related | `docs/architecture/ux-decisions.md`, `docs/governance/gates-register.md`, `ai/README.md` |

## Totals

| What | Count | Source |
|---|---|---|
| AI agents | 9 | `ai/agents/*.agent.yaml` |
| Guards around every agent call | 7 | `ai/guards/guards.yaml` |
| Gate types in code | 14 | `GateType` in `fcrm_contracts.enums` |
| Human decision points in the gates register | 18 | `docs/governance/gates-register.md` |
| Read-only agent tools | 13 | `ai/tools/` |
| Agent skills | 5 | `ai/skills/` |
| API paths | 117 | `docs/architecture/openapi/api.json` |
| Event types | 75 | `fcrm_contracts.events.catalogue` |
| Web workspaces | 3 roles, plus the harness monitor | `src/frontend/src/features/` |
| Containers | 8 | `docker-compose.yaml` |
| Decision log rows | 89 ADRs | `docs/governance/decision-log.md` |

The gate counts differ by source. Fourteen are modelled as gate types. The register lists 18 decision points because it also counts the owner's review-and-submit step, a committee question, the delegated close, the policy-gap suggestion and the examiner.

## 1. Product owner

| # | Feature | Status | Notes |
|---|---|---|---|
| PO-1 | New case form that creates nothing until the owner confirms | Built | ADR-75 |
| PO-2 | Document upload with ingestion to anchored text | Built | No OCR: a scanned PDF fails with a code and the owner is asked for another copy (ADR-17) |
| PO-3 | AI extraction of field values with page and line sources | Built | Intake agent; one job per document window |
| PO-4 | Confirm or remove each proposed change type | Built | Gate `po_confirm_category`; removal needs a reason |
| PO-5 | Confirm or correct each extracted value, original kept beside the correction | Built | Gate `po_confirm_value` |
| PO-6 | Reuse values from a precedent case, with provenance | Built | Gate `po_reuse` |
| PO-7 | Answer blocking gaps before submit | Built | Gate `po_answer_gaps` |
| PO-8 | Review and submit after four checks | Built | Not a gate type; frozen as a submission version |
| PO-9 | Answer the analyst's batched clarification round | Built | One round before policy review (D-16) |
| PO-10 | Answer ad-hoc analyst questions at any time | Built | ADR-73 |
| PO-11 | Plain-language progress, outcome and conditions, never a score or band | Built | Three-layer PO fence (ADR-32) |
| PO-12 | Condition evidence upload and tracking | Built | Conditions tab |
| PO-13 | Case activity timeline, dashboard and queue | Built | Live updates over server-sent events |
| PO-14 | Source preview on every citation chip | Built | ADR-76 |
| PO-15 | Integrations screen | Partial | Connector list only; SharePoint shown as "not connected" (ADR-17) |

## 2. FCRM analyst

| # | Feature | Status | Notes |
|---|---|---|---|
| AN-1 | Dashboard and queue with filters by stage and band | Built | |
| AN-2 | Case workbench with eight tabs | Built | Summary, Submission, Inbox, Policy, Risk and Controls, Recommendation, Handoff, Activity |
| AN-3 | Confirm or decline the fast lane, with a reason | Built | Gate `fast_lane_confirm`, rule R4 |
| AN-4 | Completeness report and one batched clarification round | Built | Narrative agent drafts; analyst edits and sends |
| AN-5 | Accept, override, adjust or remove each clause, rating, control and critic finding | Built | One gate per item; no bulk accept (R-2) |
| AN-6 | Override with a typed reason of at least 20 characters | Built | Shared reason dialog with a band-change preview |
| AN-7 | Live re-score after every override | Built | Deterministic scoring service only |
| AN-8 | Score withheld until the case's own analyst opens the review | Built | ADR-45 |
| AN-9 | Acknowledge a band change or boundary flag | Built | Gate `ack`, rule R3 |
| AN-10 | Add a clause found by corpus search | Built | ADR-74 |
| AN-11 | Deny a suggested planned control | Built | ADR-87 |
| AN-12 | Edit, add and remove recommendation conditions | Built | ADR-88 |
| AN-13 | Lock and unlock the recommendation | Built | Unlock needs a reason and supersedes the pack |
| AN-14 | Release the committee pack after four release checks | Built | Gate `pack_release` |
| AN-15 | Delegated close for Band 1 to 2 | Built | Rule R1; the route refuses higher bands |
| AN-16 | Answer committee questions as addenda | Built | A changed number unlocks and supersedes the pack |
| AN-17 | Complete a failed agent step by hand (handoff) | Built | ADR-51, ADR-85 |
| AN-18 | Acknowledge re-flagged open cases when a jurisdiction list changes | Built | Gate `reflag_ack` |
| AN-19 | Ask the examiner about a case, answered only from the verified audit chain | Built | Assurance, Ask tab |
| AN-20 | Overrides, watchlist and agent-quality views | Built | Assurance tabs |
| AN-21 | Configuration of eight sections with drafts, impact preview and publish | Built | Fields, classification, scoring, controls, corpus, routing, jurisdictions, agents; immutable versions (ADR-47) |
| AN-22 | Jurisdiction risk list on a 1 to 5 scale with FATF sync | Built | ADR-84 |
| AN-23 | Policy-gap suggestions reviewed under Assurance | Designed only | The agent runs and stores a suggestion; no review screen and no weekly run (`known-gaps.md` section U) |

## 3. Risk committee

| # | Feature | Status | Notes |
|---|---|---|---|
| CM-1 | Queue split into not voted, voted and all | Built | |
| CM-2 | Frozen, versioned committee pack with Summary, Inbox and Vote tabs | Built | ADR-89 |
| CM-3 | Ask a question on a pack | Built | Answer becomes a visible addendum |
| CM-4 | Hidden ballot with a typed rationale | Built | Options hidden until the round is revealed |
| CM-5 | Four outcomes only: approve, approve with conditions, defer, reject | Built | No fifth value |
| CM-6 | Two identical votes win; a 1-1-1 split is no majority and opens a second round | Built | ADR-31, ADR-36; nothing is decided automatically |

## 4. Observer and operators

| # | Feature | Status | Notes |
|---|---|---|---|
| OB-1 | Harness monitor showing agents, guards, tokens and audit chain live | Built | Analyst, committee and observer; the owner gets 403 |
| OB-2 | Read-only observer role | Built | The monitor is the observer's only screen (ADR-64) |
| OB-3 | Audit chain verification on demand | Built | `make verify case=<id>` or the audit route |
| OB-4 | Token report per agent and model | Built | `make token-report` |
| OB-5 | Dashboards, traces, metrics and logs in one store | Built | OpenObserve, `make up-obs` (ADR-72) |
| OB-6 | Runbooks for start, replay, restart, outage, secrets, backup, audit chain, incident, release | Built | `ops/runbooks/` |
| OB-7 | Backup and restore automation | Out of scope | Manual runbook only |
| OB-8 | AWS deployment | Designed only | `ops/aws/README.md` |

## 5. AI harness

| # | Feature | Status | Notes |
|---|---|---|---|
| AI-1 | Nine agents, one model call each, none orchestrating, none scoring | Built | Intake, screening, research, assessment, critic, narrative, examiner, watchlist, policy gap |
| AI-2 | Four model tiers with fallbacks, no Opus | Built | `ai/models/tiers.yaml`; a test fails if Opus returns |
| AI-3 | Critic on a different model family from the assessor | Built | ADR-04 |
| AI-4 | Seven guards in fixed order | Partial | The amber injection band passes with an annotation; the second-opinion check is not built |
| AI-5 | Tool permits through OPA, read-only tools, fail closed | Built | |
| AI-6 | Context packs built in code, capped and snapshotted by hash | Built | |
| AI-7 | Hybrid policy retrieval over a synthetic 197-clause corpus | Partial | Clauses use Titan embeddings; precedents and controls are keyword only |
| AI-8 | Verbatim quote checking with source references | Built | Rule R-8; quotes are verbatim from the synthetic store |
| AI-9 | Per-attempt and per-case token budgets with a ledger | Built | 250,000 enforced, 120,000 target (ADR-81, ADR-83) |
| AI-10 | One retry on a fallback, then a human handoff | Built | ADR-14 |
| AI-11 | Splitting of oversized agent inputs | Built | ADR-77; marked proposed in the decision log |
| AI-12 | Versioned prompts with registry locks | Partial | Pinning is recorded but not enforced at run time |
| AI-13 | Evals: nine datasets, 27 deterministic metrics, a judge, one promotion gate per agent | Built | Run only with operator approval (ADR-59) |

## 6. Deterministic core and governance

| # | Feature | Status | Notes |
|---|---|---|---|
| GV-1 | Scoring engine: inherent, residual and composite from versioned models | Built | Golden figures 9.8 to 10.4 are unit tests |
| GV-2 | Routing rules R1 to R6 as versioned data | Built | |
| GV-3 | Eight product rules R-1 to R-8 enforced before every state change | Built | |
| GV-4 | Hash-chained, append-only audit trail on an insert-only database role | Built | Tamper-evident, not tamper-proof against a database superuser |
| GV-5 | Segregation of duties in OPA: the service account is denied on every decision route | Built | |
| GV-6 | Transactional outbox, idempotent steps, dead-letter queues | Built | ADR-02, ADR-28 |
| GV-7 | Versioned, immutable reference data pinned per case | Built | S1 reads some pins from the seed, not S3 (`known-gaps.md`) |
| GV-8 | Input scan with PII masking before any agent sees text | Built | |
| GV-9 | Twelve resilience drills on the live stack | Built | Bedrock failure, partitions and multi-container failure are not covered |

## Open items to say plainly

- Policy-gap review screen and weekly run are not built.
- Prompt pinning is recorded, not enforced.
- The decision log still marks ADR-77, 78, 79, 80 and 82 as proposed although the code implements them.
- The policy corpus and all case data are synthetic, as the brief requires.
- No OCR, SharePoint, mobile layout or multi-language documents, by decision.

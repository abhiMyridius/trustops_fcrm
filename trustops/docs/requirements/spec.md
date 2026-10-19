# Specification

The expanded specification, synthesised from the hackathon brief, the team's approved v1.3 decision log and walkthrough v2, the FCRM_v2 prototype, and the EVOQ/AEO reuse inventory. Full research trail: `docs/requirements/research/`. This document is what the team committed to build; `BUILD-PLAN.md` is how.

## 1. Use case

A US bank must assess the AML/CFT and sanctions risk of a proposed change — a new product, a new feature, a process change, a vendor or third-party onboarding, a new geography, or a new customer segment — before that change goes live. Today this takes 15–20 business days and depends heavily on one analyst's manual synthesis of documents, policy and precedent. The target is roughly two days, achieved not by skipping steps but by having AI agents draft every proposal with evidence, and a human confirm, override or remove each one.

## 2. The three roles and the gate between them

See `docs/requirements/personas.md` for who they are. The gate structure: the product owner's submission is gated on completeness (80% of required fields, every always-required field, no open blocking gap); the analyst's review is gated item by item (no bulk decision); the route to delegated close versus committee is gated on the band (R1/R2); the committee's outcome is gated on an actual two-vote majority (ADR-08), never produced by default.

## 3. The case lifecycle

Intake → review and submit → triage → screening → assessment → critic → completeness check → one optional clarification round → review (item-by-item) → recommendation → decide (delegated close or committee vote) → closed, with an oversight loop (conditions tracking, watchlist re-flags, examiner Q&A) acting on closed cases afterward. The full state machine, with every transition's guard and the action it triggers, is implemented and tested in `src/workers/caseflow/fcrm_caseflow/transitions.py` — see `BUILD-PLAN.md` §3.3 for the table in prose.

## 4. Scoring (deterministic, never AI)

Inherent = L×I. Control effectiveness is the weighted mean of a domain's matched, non-planned controls. Retention = max(0.4, 1 − 0.6×CE/10). Residual = inherent × retention. Composite = the domain-weighted sum of residuals, rounded once to one decimal. Bands 1–4 at 0–5.0 / 5.1–10.0 / 10.1–17.0 / 17.1–25.0, with a boundary flag within 0.5 of an edge. Full formula, worked example and 80-case verification: `BUILD-PLAN.md` §3.6 and `src/services/scoring/fcrm_scoring/engine.py`.

## 5. The AI layer

Nine agents (screening, intake, research, assessment, critic, narrative, examiner, watchlist, policy_gap), each one Pydantic AI call on a Bedrock model tier, each behind seven guards (input scan, OPA tool permit, budget, output shape, the eight rules, critic, human gate). No agent ever computes a score, edits a submission, or generates policy text rather than quoting it.

- **Models**: four tiers on Bedrock (cheap, mid, strong, judge), each a primary and fallbacks, ids only in `ai/models/tiers.yaml`; no Opus (ADR-62, ADR-65). The critic and the eval judge run on a different model family from the assessor.
- **Retry and handoff**: one retry on the fallback with the error shown, then a human handoff gate the analyst completes with a reason (ADR-14, ADR-51, `docs/architecture/sequences/handoff.md`).
- **Tokens**: per-attempt budgets per tier, and a per-case ledger in H1, 160,000 enforced and 120,000 the target (ADR-50, `docs/architecture/sequences/token-ledger.md`).
- **Retrieval**: Bedrock Titan embeddings with in-memory vectors for the policy clauses, fused with keyword search; precedents and controls are keyword search (ADR-41, ADR-49).
- **Evals**: nine datasets, 27 deterministic metrics and a judge model on the judge tier, one gate per agent (ADR-43, `evals/README.md`).

Full detail: `docs/architecture/design/B-ai-layer.md` (the design as written) and `BUILD-PLAN.md` "Status as built".

## 6. Governance

Eight rules plus six routing rules (`docs/requirements/rules.md`), an immutable hash-chained audit trail (`docs/governance/audit-chain.md`), a visibility matrix enforced structurally rather than by hiding fields in a view (`docs/governance/visibility-matrix.md`), and a decision log recording every architectural choice with its rationale (`docs/governance/decision-log.md`).

## 7. Production shape

FARM stack (FastAPI, React, MongoDB), Python 3.13 on uv, event-driven microservices over RabbitMQ, OpenTelemetry observability, OPA for authorization, all runnable locally via docker-compose and with Amazon Bedrock for every model call and for embeddings. The AWS deployment is a documented design, not built; the S3 Vectors backend exists but is not in the shipped image (`docs/governance/known-gaps.md`). Full architecture: `BUILD-PLAN.md` §3, `docs/architecture/design/A-backend.md`.

## 8. What's explicitly out of scope

Mobile layout, a separate notification-settings product, Temporal, GitHub Actions (local hook/pre-commit equivalents instead), Trivy, OCR, multi-language documents, and SharePoint as a document source (a connector interface exists; no SharePoint implementation).

## 9. Data

Synthetic only, generated by the team: the walkthrough-v2 embedded seed (80 cases including the canonical demo, FCRM-2026-0151), extracted and verified against the decision log's published figures by hand before any code was written (`docs/requirements/research/06-walkthrough-seed-extraction.md`), now packaged as `fcrm_seed` and the source every service, test and eval reads from.

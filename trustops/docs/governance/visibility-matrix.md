# Visibility matrix

Who may see what. This is rule 3 ("the PO view has no restricted fields") made concrete, and the specification BB-02 (BUILD-PLAN §7) checks against. "Never" means the shape itself cannot carry it (`fcrm_contracts.api.case_po.CaseDetailPO` has no such field), not that a view merely hides it.

| Data | PO (`fcrm_po`) | Analyst (`fcrm_analyst`) | Committee (`fcrm_committee`) | Observer (`fcrm_observer`) |
|---|---|---|---|---|
| Case fields, documents, own submission | read/write (own cases) | read-only, never edits (rule 4) | read-only (assigned cases) | read-only |
| Completion %, required-field list | yes | yes | yes | yes |
| Composite score, band, boundary flag | **never** | yes | yes (via the pack) | yes |
| Likelihood / impact / control ratings | **never** | yes | yes (via the pack) | yes |
| Agent proposals and their provenance | **never** (sees only confirmed values) | yes | no (sees the locked pack only) | yes |
| Review items (clause/factor/control/critic) | **never** | yes, decides each | no | yes |
| Recommendation draft and rationale | no (sees the plain-language outcome summary only, post-decision) | yes, drafts and locks | yes, in the pack | yes |
| Conditions | yes, once activated | yes | yes, in the pack | yes |
| Committee votes (own case, before reveal) | no | no | own vote only; others hidden until quorum | no |
| Committee votes (after reveal) | no | yes | yes | yes |
| Harness monitor (`/monitor/:caseId`) | **403** (ADR-19) | yes | yes | yes |
| Audit chain / examiner answers | no | yes | yes | yes |
| Config (field sets, controls, corpus, routing, etc.) | no | yes, owns it | no | read-only |
| Token/cost figures | no | yes (Assurance → Agent quality) | no | yes |

**Note, 7 Oct 2026 (D-109, ADR-64).** The table states what the API allows. In the web the observer is limited to the harness monitor (ADR-19, ADR-37), so the observer's "yes" for Agent quality and for the token and cost figures is an API permission that no screen uses; the committee has no Agent quality screen either. Tightening `ops/opa/data/permissions.json` to match is left to the operator.

## Enforcement points

1. **Response shape** — `CaseDetailPO` has no score/band/rating field at all; there is no masking step to forget. (`src/packages/contracts/fcrm_contracts/api/case_po.py`)
2. **SSE stream filtering** — the PO's event stream never carries `score.*`, `harness.*`, `audit.*` event types (design C, C-03); enforced server-side, not by the client choosing not to render them.
3. **Route authorization** — OPA's `authz.rego` denies the PO role on `/monitor/*` and any analyst/committee-only route (403, not a redirect).
4. **Black-box test BB-02** — drives the real API and the rendered HTML/SSE payloads as an outside caller and asserts no band/score/rating string or field ever reaches a PO-authenticated response.

Still to build: the actual HTTP route layer and SSE filter that enforce rows 3 onward end-to-end (pending `fcrm_common`); today only the response *shape* (row 1) and the pure logic behind rows 2 and several others exist and are tested.

# C · Web, tests, ops and the AI-driven SDLC harness — design

> **Design as written on 3 October 2026, kept as the record of the design stage.** It was the input the build lanes coded from, so it is not rewritten to match the code. Where the built system differs, the departure has a decision row; read `BUILD-PLAN.md` "Status as built" first, then `docs/governance/decision-log.md`. What is not done is in `docs/governance/known-gaps.md`. Values marked **(proposed)** below were proposals at the time; the ADR rows say which were accepted.
>
> The departures that matter most for this document:
> - The web is in **`src/frontend`**, not `src/web`, rebuilt from scratch with FCRM_v2's tokens and information architecture only (ADR-06, ADR-25).
> - **No generated client**: `make api-gen` is not wired; the web's types are hand-written and `src/test/contract/paths.test.ts` checks every route it calls against the OpenAPI export. The web's route names became the API contract, so section 1.4's endpoint names are superseded (ADR-30).
> - **C-07 is reversed**: there is no `replay` or `mock` LLM mode (ADR-27). The deterministic demo is the MSW mock backend (`npm run dev:msw`); the live suites call Bedrock.
> - **C-08 is reversed**: tests never sit beside source; they live under `tests/unit` and `tests/integration` (Python) and `src/frontend/src/test` (web).
> - **C-09 and C-11**: `make nightly` is not wired, and the Claude Code hook scripts are committed but not registered; the git hooks are the enforced commit gate. Eight containers publish only the gateway on 8080 (ADR-26, ADR-63).
> - **C-04**: the observer's web is the monitor only (ADR-64). The UX decisions are collected in `docs/architecture/ux-decisions.md`.
> - **The look, sections 1.2, 1.3, 1.6 and 1.7**: the web is re-skinned to the Myridius brand guide (Montserrat, a linen canvas, Carbon Moss dark surfaces, Virtual Tide as the one accent, pill-shaped controls). The owner's home moved from `/` to `/po`, and `/` now sends anyone signed in to their own home. A deliberate sign-out lands on a plain `/login`. An animated AI mark sits beside every AI-proposed or AI-drafted item (ADR-67). The tables below are updated for these.

Status: design for implementation by Claude Code subagents, 3 Oct 2026. Six build days remain (submission 9 Oct).
Inputs: A-problem-statement, 00-synthesis, C-blueprint-flow (incl. v2 update), D-prototype (§1, §13, §14, §15), F-walkthrough-seed, CLAUDE.md, FCRM_v2/src/index.css.
Companion designs: A (harness, agents, guards) and B (services, data, events). Where this document names an endpoint or event, the name is **(proposed)** and binds to design B's OpenAPI once published; the web client is generated, so renames cost one regeneration.

Conventions: "(proposed)" marks a decision made here that no approved document had made. Decision references use the BA's IDs (D-xx) and new ADR-lite rows (ADR-nn, §5.6). Rubric criteria are cited as R-harness 30 %, R-sdlc 20 %, R-hitl 15 %, R-evals 10 %, R-context 10 %, R-prod 5 %, R-tokens 5 %, R-judgement 5 %.

## 0. Decisions this document makes

| # | Decision (proposed unless cited) | Why |
|---|---|---|
| C-01 | `/src/web` is a fresh Vite + React 19 + TS app; only the Myridius tokens and the information architecture come from FCRM_v2 (D-63 is superseded by the caller's "rebuild" decision; record as ADR-01) | The prototype's data layer, routing bugs and client-side gates (D §15) must not leak into the build |
| C-02 | Typed client generated with `openapi-typescript` + `openapi-fetch`; TanStack Query hooks hand-written per domain in `src/web/src/api/queries/*` | Smallest generated surface, zero runtime magic, MSW handlers share the same types |
| C-03 | One SSE stream per case (`GET /api/fcrm/cases/{id}/stream`) and one per user (`/api/fcrm/stream`); the server filters event types by role; the PO stream never carries `score.*`, `harness.*`, `audit.*` | Live updates without polling; the "PO never sees scores" rule holds on the wire (D-62) |
| C-04 | Harness monitor is a separate route `/monitor/:caseId`, allowed for `fcrm_analyst`, `fcrm_committee` and a seeded `fcrm_observer` role; never rendered inside a PO session | The demo shows it side by side (second window); a PO token gets 403 on the stream |
| C-05 | Auth: identity (X1) issues a short-lived access JWT (in memory) and a refresh token (httpOnly cookie). Login page shows "Sign in with corporate SSO" → a seeded-user picker in demo mode (`VITE_DEMO_USERS=true`). "Switch user" is a full re-login, never a client toggle | Fixes the prototype's persona switcher (D §1); matches scene 1 |
| C-06 | Four vote options (approve, approve with conditions, defer, reject). Majority of 3 decides. A 1-1-1 split is `no_majority` (ADR-08, ADR-31; corrected from `deferred`): the round is revealed with its three rationales and kept, round two opens on the same pack and all three vote again, and a system thread opens for the discussion (rule R6 path); no outcome is ever auto-approved, auto-rejected or auto-deferred. Align with design B's S5 | Resolves the open votes question (synthesis §5 #11, C §9 #13); defer is itself a decision and needs a majority like any other |
| C-07 | Deterministic LLM modes for tests: `FCRM_LLM_MODE=live|replay|mock`; `replay` serves recorded proposals from `evals/fixtures/replay/<agent>/<case_id>.json` through a Pydantic AI `FunctionModel`; E2E and the demo rehearsal run in `replay` | The 21-scene E2E must reproduce the seed's figures every run (R-prod: "a demo that works once is not the bar") |
| C-08 | Tests live in `/tests` by layer; package unit tests sit next to their package under `src/**/tests` and are discovered by the same `make` targets | Brief requires `/tests`; packages keep their suites (BP repo rules) |
| C-09 | Three gate tiers: commit gate (seconds), push gate (minutes), nightly (hour). Claude Code hooks enforce the commit gate; `make gate` is the push gate; `make nightly` runs under launchd/cron on the operator's machine | GitHub Actions is out of scope (D-90, D-92); local equivalents still give gated evidence |
| C-10 | Observability stack: OTel SDK → otel-collector → Jaeger (traces), Prometheus (metrics), Grafana (3 dashboards); Logfire optional via the same OTLP endpoint | All local, no account needed; BP §9 keeps Logfire as an option |
| C-11 | Ports (proposed): nginx 8080 · web dev 5173 · S1 8001 · S2 8002 · S3 8003 · S4 8004 · S5 8005 · identity 8010 · worker health 9001–9004 · OPA 8181 · Mongo 27017 · RabbitMQ 5672/15672 · Redis 6379 · MinIO 9000/9001 · otel 4317/4318 · Jaeger 16686 · Prometheus 9090 · Grafana 3000 | No document gave ports (B §11) |
| C-12 | Commit message convention `type(scope): summary [D-xx, ADR-nn]` with `Stage:` and `Gate:` trailers; enforced by a `commit-msg` hook and a Claude Code `PreToolUse` hook on `git commit` | Commit history is evidence (brief) |
| C-13 | Governance artefacts are append-only Markdown tables: `docs/governance/decision-log.md` (ADR-lite), `defect-trail.md`, `failure-log.md`, `gates-register.md`; a hook refuses edits that delete or modify an existing row | The panel "probes decisions, not features" |
| C-14 | Build-time token analysis is produced from Claude Code session logs by `ops/scripts/claude_usage.py`; runtime analysis from `fcrm_audit.token_records` by `ops/scripts/token_report.py`; both land in `ops/token-analysis/` | R-tokens asks for both where consumption concentrated and what was optimised |
| C-15 | MSW (browser + node) backed by `seed.json` is the frontend's mock backend until each service's OpenAPI lands; no Prism | MSW works before any OpenAPI exists and later shares the generated types |

---

## 1. Frontend architecture

### 1.1 Folder tree (`/src/web`)

```
src/web/
  package.json            react 19 · react-router 7 · @tanstack/react-query 5 · openapi-fetch · zod
                          lucide-react · recharts 3 · msw 2 · vitest · @testing-library/react · vitest-axe · oxlint · eslint (a11y plugin only)
  vite.config.ts          proxy /api → http://localhost:8080 in dev; env VITE_API_MODE=live|msw
  tsconfig.json           strict, noUncheckedIndexedAccess, verbatimModuleSyntax
  index.html
  public/                 brand mark (Myridius, D-97), favicon
  CLAUDE.md               package guidance (see §5.3)
  scripts/
    gen-api.ts            fetches each service's /openapi.json (or reads docs/architecture/openapi/*.json) → src/api/generated/<svc>.d.ts
  src/
    main.tsx              QueryClientProvider · AuthProvider · RouterProvider · ToastProvider; starts MSW when VITE_API_MODE=msw
    app/
      router.tsx          route tree (§1.2), role guards, lazy routes
      layouts/            AppShell (Sidebar, Topbar, Outlet), AuthLayout, MonitorLayout
      guards/             RequireRole, RequireAuth
    api/
      generated/          case.d.ts scoring.d.ts policy.d.ts audit.d.ts decision.d.ts identity.d.ts   (never edited by hand)
      client.ts           openapi-fetch clients per service with auth + trace-id middleware
      queries/            caseQueries.ts analystQueries.ts committeeQueries.ts configQueries.ts assuranceQueries.ts authQueries.ts
      keys.ts             query-key factory (one place)
      sse/                useCaseStream.ts useUserStream.ts eventSchemas.ts (zod) reconnect.ts
      errors.ts           ApiError → user message mapping (§1.9)
    auth/
      AuthProvider.tsx    token in memory, refresh via cookie, role claims, demo user picker
      roles.ts            'fcrm_po' | 'fcrm_analyst' | 'fcrm_committee' | 'fcrm_observer'
    design/
      tokens.css          Myridius brand tokens (guide-aligned, ADR-67, fixed @theme mapping) + band tones
      global.css          reset, focus-visible, reduced motion, print styles for the pack
    components/
      ui/                 the ~25 primitives (§1.6)
      domain/             SourceChip ProvenanceTag ScoreBadge CompletionRing StageTracker ReasonDialog DiffTable
                          ClauseCard ControlRow FactorBlock RiskMatrix ConditionTable VoteCard PackSection AuditRecordChip
      charts/             KpiCard ChartCard + recharts configs (one palette, §1.7)
    features/
      auth/               LoginPage
      po/                 Dashboard MyQueue NewCase CaseLayout tabs/{Overview,Documents,ReviewSubmit,Inbox,Activity,Conditions} Integrations
      analyst/            Dashboard Queue WorkbenchLayout tabs/{Summary,Submission,Inbox,Policy,RiskControls,Recommendation,Handoff,Activity}
                          assurance/{AskTab,OverridesTab,WatchlistTab,AgentQualityTab}
                          config/{ConfigLayout, sections/<8 sections>, WorkingSetBanner, VersionHistory, ImpactPreviewModal}
      committee/          Queue CaseLayout tabs/{Pack,Inbox,Vote}
      monitor/            MonitorPage (§1.10)
    lib/                  format.ts (dates, money, business days display only) · bands.ts (labels only; ranges come from the pinned model) · a11y.ts
    mocks/
      browser.ts server.ts handlers/<svc>.ts  seed.ts (imports ../../../packages/fcrm_seed/seed.json at build time)
      scenarios/          scene state snapshots for Storybook-free visual checks and vitest
    test/                 setup.ts (RTL, MSW node server, axe), render.tsx (providers), fixtures.ts
  e2e/ → lives in /tests/e2e (not here); Playwright config points at this app
```

Rules: features import from `components`, `api`, `auth`, `lib`; never from each other. `api/generated` is regenerated by `make api-gen` and committed (so a stale client is a visible diff). No business arithmetic in the client: band ranges, completion %, SLA days, tallies all come from the server; `lib/bands.ts` holds labels and tones only.

### 1.2 Routing table (paths proposed; screen set from the caller and C V2.5)

All routes are under `AppShell` except `/login` and `/monitor/*`. Role guards redirect to the role's home. A PO hitting an analyst route gets the role's home, not a 403 page (visibility is asymmetric by design; nothing hints at what exists).

| Role | Path | Screen | Tabs / notes |
|---|---|---|---|
| any | `/login` | LoginPage | SSO button → demo user picker (C-05) |
| any | `/` | HomeRedirect | sends a signed-in person to their own home: `/po`, `/analyst` or `/committee` (ADR-67) |
| PO | `/po` | PO Dashboard | KPIs, My cases (was `/` until ADR-67) |
| PO | `/queue` | My Queue | filters, resume draft |
| PO | `/case/new` | creates a case then redirects to `/case/:id/documents` | case ID issued first (scene 2) |
| PO | `/case/:id` | PO Case | redirects to `overview`; tabs: `overview` `documents` `review` `inbox` `activity` `conditions` |
| PO | `/integrations` | Integrations | SharePoint/Teams/Slack/MCP rows, no secrets collected |
| Analyst | `/analyst` | Analyst Dashboard | |
| Analyst | `/analyst/queue` | Queue | mine/all, stage, band |
| Analyst | `/analyst/case/:id` | Workbench | tabs: `summary` `submission` `inbox` `policy` `risk` `recommendation` `handoff` `activity`; header carries Acknowledge when a flag is pending |
| Analyst | `/analyst/assurance` | Assurance | tabs: `ask` `overrides` `watchlist` `agent-quality` |
| Analyst | `/analyst/config` | Config | sections (hash deep links): `fields` `classification` `scoring` `controls` `corpus` `routing` `jurisdictions` `agents` |
| Committee | `/committee` | Committee Queue | not voted / voted / all |
| Committee | `/committee/case/:id` | Committee Case | tabs: `pack` `inbox` `vote` |
| Analyst, Committee, Observer | `/monitor/:caseId` | Harness monitor | standalone window layout (§1.10) |
| any | `*` | NotFound | role home link |

Templates and Settings (prototype PO nav) are out: Templates becomes a read-only "Required fields" card inside Review & submit; notification settings are out of scope (D-92).

### 1.3 Auth flow against identity (X1)

1. `GET /api/fcrm/auth/me` on load. 200 → session; 401 → try `POST /api/fcrm/auth/refresh` (cookie) → else `/login`.
2. Login: `POST /api/fcrm/auth/login {username, password}` (seeded demo users have passwords from `seed.json.users[].demo_password`, synthetic). In demo mode the SSO button opens the picker; in non-demo mode it posts the form. The response carries `access_token` (exp 15 min), `role`, `user`, and sets the refresh cookie (exp 8 h).
3. `client.ts` middleware adds `Authorization: Bearer`, `X-Trace-Id` (uuid per page load, propagated to spans) and retries once on 401 after a refresh. Concurrent refreshes are deduplicated.
4. Role comes from the token claim `role`; the UI uses it only for navigation and layout. Field-level visibility is never computed client-side: a PO payload has no score fields to hide.
5. Logout: `POST /api/fcrm/auth/logout` clears the cookie; the query cache is cleared; SSE closed. A deliberate sign-out lands on a plain `/login` with no `?next=`; only an expired session carries `?next=` back to where the person was (ADR-67).
6. The identity fixes from CLAUDE.md (refresh accepted as access, OTP logging, hard-coded secrets) are design B's; the web client's tests assert that a refresh token in the `Authorization` header is rejected (black-box check BB-11).

### 1.4 API surface (proposed names, bind to design B) and the SSE hook

Gateway: nginx (G1) at `:8080`, prefix `/api/fcrm/`. Service prefixes: `cases` → S1, `scoring` → S2, `policy` → S3, `audit` → S4, `decision` → S5, `auth` → X1. Every service serves `/openapi.json`, `/health`, `/ready`.

| Area | Endpoint (proposed) | Used by |
|---|---|---|
| Dashboard/queue | `GET cases/dashboard` (role-shaped) · `GET cases?scope=mine|all&stage=&band=` (band filter ignored for PO) | PO Dashboard, My Queue, Analyst Dashboard/Queue, Committee Queue (`scope=committee`) |
| Case core | `POST cases` · `GET cases/{id}` (role DTO) · `GET cases/{id}/activity` | all case screens |
| Documents | `POST cases/{id}/documents` (multipart → MinIO, triggers H3 + intake agent) · `GET cases/{id}/documents` · `GET cases/{id}/documents/{docId}/view?page=&line=` | Documents tab, SourceChip |
| Intake | `GET cases/{id}/intake` (proposals, change types, fields, gaps, completion) · `POST cases/{id}/change-types/{type}:confirm` · `POST …:remove {reason}` · `GET cases/{id}/precedents` · `POST cases/{id}/reuse {fieldIds, sourceCaseId}` · `PUT cases/{id}/fields/{fieldId} {value}` (owner answer) · `POST cases/{id}/fields/{fieldId}:correct {value}` (owner declared, original kept, D-17) | Overview, Review & submit |
| Submit | `POST cases/{id}:validate` → 4 checks · `POST cases/{id}:submit` | Review & submit |
| Submissions | `GET cases/{id}/submissions` · `GET cases/{id}/submissions/diff?from=1&to=2` | Submission tab, Inbox |
| Clarification | `GET cases/{id}/threads` · `POST cases/{id}/clarification-rounds {questions[]}` (analyst) · `POST cases/{id}/threads/{tid}:answer {text, documentIds}` (PO) · `POST …:resolve` | Analyst Inbox, PO Inbox |
| Assessment view | `GET cases/{id}/assessment` (analyst: flags, completeness, proposals for clauses/factors/controls, critic findings, score with `withheld` flag until review starts) | Summary, Policy, Risk & Controls |
| Gates (Guard 7) | `POST cases/{id}/gates/decide {itemType: clause|control|factor|changeType|flag, itemId, decision: accept|remove|confirm|override, value?, reason?}` one item per call (rule 2) · `POST cases/{id}/gates/acknowledge {kind: boundary|band_change}` (R3) · `POST cases/{id}/completeness:proceed` / `:ask` | Policy, Risk & Controls, Summary, header Acknowledge |
| Controls extra | `POST cases/{id}/controls/planned {controlId, evidence, due}` · `POST cases/{id}/policy/search {q}` · `POST cases/{id}/policy/gap {note}` | Risk & Controls, Policy |
| Recommendation | `GET/PUT cases/{id}/recommendation` (narrative, conditions edits) · `POST …:lock` · `POST …:unlock {reason}` | Recommendation |
| Pack / handoff | `POST decision/packs {caseId}` · `GET decision/packs/{caseId}` (versions, release checks) · `POST decision/packs/{packId}:approve` · `GET decision/packs/{packId}/threads` · `POST …/threads {question}` (member) · `POST …/threads/{tid}:answer {text}` (analyst → addendum) | Handoff, Committee Pack/Inbox |
| Votes | `GET decision/votes/{caseId}` (own vote + cast count; others' choices only after reveal) · `POST decision/votes {caseId, choice, rationale}` · `GET decision/outcome/{caseId}` | Vote tab |
| Delegated close | `POST decision/outcome {caseId, outcome, summary}` (analyst, Band 1–2 only, R1) | Handoff |
| Conditions | `GET cases/{id}/conditions` · `POST cases/{id}/conditions/{cid}:update {status, evidenceDocId}` | PO Conditions, Analyst Handoff |
| Scoring | `GET scoring/models/{v}` · `GET scoring/routing/{v}` · `POST scoring/simulate` (config test panel) | ScoreBadge ranges, Config |
| Audit / assurance | `GET audit/records?case=` · `GET audit/verify/{case}` · `POST audit/examiner/ask {question}` · `GET audit/overrides?from=&to=&agent=` · `GET audit/agent-quality?week=` · `GET audit/versions/{case}` | Assurance, Activity, Monitor |
| Watchlist | `GET policy/watchlist/reflags` · `POST policy/watchlist/reflags/{id}:acknowledge` | Watchlist tab |
| Config | `GET policy/config/{section}/versions` · `GET policy/config/{section}/draft` · `PUT …/draft` · `POST …/draft:preview` · `POST …/draft:publish {note}` (optimistic `If-Match: <draftEtag>`) | Config (8 sections; scoring/routing sections proxy to S2) |
| Harness | `GET cases/{id}/harness` (core, budget, agents, guards) · `GET cases/{id}/stream` (SSE) · `GET cases/stream` (SSE, user scope) | Monitor, all live screens |
| Identity | `POST auth/login` · `POST auth/refresh` · `POST auth/logout` · `GET auth/me` | AuthProvider |

**SSE hook.** `useCaseStream(caseId)` opens one `EventSource` per case (shared via a module-level registry so tabs don't multiply connections). Events are `{type, caseId, seq, at, payload}`, validated with zod (`eventSchemas.ts`). Default handler: invalidate the query keys mapped to the type (`case.updated` → case, activity; `proposal.ready` → assessment; `score.computed` → assessment, harness; `thread.updated` → threads; `pack.updated` → pack; `vote.cast` → votes; `audit.appended` → audit count; `harness.log`, `agent.*`, `guard.*`, `budget.updated` → monitor buffer only). Reconnect with backoff (1 s → 30 s) and `Last-Event-ID` = last `seq`; on 3 failures switch to 10 s polling and show the "Live updates paused" banner. `useUserStream()` does the same for queue/dashboard keys. Event names are shared with the backend through `fcrm_contracts` JSON Schema exported to `docs/architecture/events/` (§3c).

### 1.5 State management rules

1. Server state lives only in TanStack Query. No copy of server data in React state or context.
2. Invalidate, don't patch: SSE and mutations call `invalidateQueries`. The one exception is per-item gate decisions, which update the item optimistically and roll back on error (so a row of four Accept buttons feels instant).
3. URL is the state for tabs, filters, selected version, config section (`?tab=`, `#section`). Refresh must land on the same view.
4. Local UI state (open modal, draft reason text) is component state. Config drafts are server-side (`…/draft`), so a reload keeps the working set and two analysts see the same draft (prototype's WorkingSetContext moves to the server).
5. Forms use zod schemas (react-hook-form was dropped as unused, ADR-58); the reason field schema `z.string().trim().min(20)` is shared by every ReasonDialog.
6. `staleTime` 10 s by default, 0 for gates/votes, 5 min for config versions and scoring models. SSE keeps data fresh; no `refetchInterval` except the polling fallback.
7. No global store library. Two contexts: `AuthContext`, `MonitorContext` (ring buffer of 200 log lines).

### 1.6 Component library

Primitives (`components/ui`, ~25, all with ARIA roles, keyboard support and vitest-axe tests):
Button · IconButton · Card · Badge (tones: neutral, success, info, warning, danger) · Pill (count) · Tag · StatusDot · RouteTabs (roving tabindex, `?tab=` sync) · Modal (focus trap, Esc, `aria-labelledby`) · Drawer · Toast (`aria-live=polite`) · Input · Textarea · Select · Checkbox · SegmentedControl · Table (sortable headers, sticky head, row actions, empty/loading rows) · EmptyState · Skeleton · ProgressBar · Banner (info/warn/danger with optional action) · Stepper · Breadcrumb · Avatar · Icon (lucide).

Domain components (`components/domain`):
- `SourceChip` — "case p.4 l.12" style chip; click opens the document viewer at page:line. Renders nothing when a value has no source (rule: no source, no claim).
- `ProvenanceTag` — Extracted · Reused from FCRM-2026-0118 · Owner answer · Owner declared (was …) · Analyst override · Jurisdiction list. Always a visible label, never a tooltip.
- `AiMark` (`components/ui/AiMark.tsx`) — the animated sparkle beside every AI-proposed or AI-drafted item, so a person can tell at a glance what a model wrote and what a person confirmed; it keeps a still form under `prefers-reduced-motion` (ADR-67).
- `ScoreBadge` — numeric score + "Band n of 4 (range)" + route. Ranges and labels come from `GET scoring/models/{v}` for the case's pinned version. Never imported by `features/po/*` (an ESLint `no-restricted-imports` rule enforces this).
- `CompletionRing` — % with threshold tick and "n of m fields · always-required k of 6".
- `StageTracker` — intake → review & submit → triage → screening → assess → critic → completeness → clarification → review → decide → oversight, collapsed to the role's view (PO sees Draft · Submitted · Under assessment · Decision · Closed).
- `ReasonDialog` — the one component for every "reason required" action: title, context line (e.g. "proposed 7.6 · evidence: audit 2023-11 · weight 0.35"), value input when applicable, reason textarea (min 20 chars), live preview of consequence when the server returns one (`POST scoring/simulate` for control overrides), Save disabled until valid.
- `DiffTable` — v1 → v2 submission diff with changed rows highlighted, added documents listed.
- `ClauseCard` (verbatim quote, source, version, Accept / Remove) · `ControlRow` (name, weight, proposed rating, evidence + date, stale badge, Confirm / Review) · `FactorBlock` (L, I, anchor definitions, justification) · `RiskMatrix` (5×5 SVG, inherent hollow, residual filled) · `ConditionTable` · `VoteCard` (hidden ●●● / revealed) · `PackSection` (locked / editable states) · `AuditRecordChip` (#49 · human.override · opens the record) · `TwoClocks` (analyst-active and total elapsed).
- `charts/`: `KpiCard`, `ChartCard` (title, insight line, recharts child), one categorical palette from tokens, dataviz guidance applied (no 3-D, no dual axes, direct labels).

### 1.7 Design tokens

`design/tokens.css` holds the Myridius token layer (brand, surface, text, border, semantic, ink-1..10, verdant-1..10, chart extras, spacing, radius, shadows), fixed to Tailwind v4 via `@theme inline` so a utility exists for every token. Re-skinned to the Myridius brand guide (ADR-67): primary canvas is linen (`--color-linen`/`--color-canvas`), dark surfaces are Carbon Moss `#0C2226`, the primary accent is Virtual Tide `#09A78D` (CTAs, links, focus rings, tab underlines), and Pulse Green `#52E081` is reserved for the logo mark and decorative use on dark surfaces (never body text on light). Token names follow the guide's vocabulary, not the FCRM_v2 prototype's: `quantum-leaf` is the dark-teal text/selected-state colour (`#194247`), and the prototype's old `quantum-leaf` (`#52e081`) is now `pulse-green`. Band tones (`--band-1..4` with `-bg`/`-text` variants) are unchanged in hue. Light theme only; `color-scheme: light` declared so the browser does not auto-dark form controls. Typography: Montserrat (self-hosted via `@fontsource-variable/montserrat`, imported in `main.tsx`), falling back to Segoe UI / system stack; 14 px base, 1.6 line height; numeric columns use `font-variant-numeric: tabular-nums`. Two brand signatures are implemented as components: `components/ui/WaveOrnament.tsx` (the organic wave ornament on the login hero and monitor header) and the `.surface-dark-card` utility in `global.css` (the gradient fade-card on dark surfaces). No hard-coded hex or rgba outside `design/tokens.css` (and `public/*.svg`), enforced today by convention and a grep, not a lint rule.

### 1.8 Accessibility baseline

WCAG 2.1 AA target. Every primitive ships with a vitest-axe test; every Playwright screen step runs `@axe-core/playwright` with `wcag2a, wcag2aa` tags and fails on `serious`/`critical`. Keyboard: all actions reachable; `focus-visible` ring token; modals trap focus and restore it; tabs follow the WAI-ARIA tabs pattern. Live regions: toasts and the SSE banner use `aria-live=polite`; the score box uses `aria-live=assertive` only when a band changes. Colour is never the sole carrier (band badge carries the number and the label; stale badge carries text; diff rows carry a "changed" label). Contrast: the token pairs used are checked once in `design/contrast.test.ts` (text-primary on surface, text-on-dark on carbon-moss, warning on surface ≥ 4.5:1; `--color-warning #a67a14` on white is 3.5:1 so it is used only for ≥ 18 px text, an icon/border, or with the `-bg` tint behind it; body-size warning text uses `--color-warning-text`). The primary button (`--color-virtual-tide` fill, white label, 3.0:1) is an accepted brand exception for button labels only (ADR-67); every other body-size text stays ≥ 4.5:1. `prefers-reduced-motion` disables the ring animation and the monitor auto-scroll. Document titles per route ("Risk & Controls · FCRM-2026-0151 · TrustOps").

### 1.9 Error and loading states

| Situation | Behaviour |
|---|---|
| Query loading | Skeleton that matches the card's shape; tables show 5 skeleton rows; never a blank section (prototype's `SectionShell` rule) |
| Query error | Inline `ErrorState` with the server message, trace id and Retry; the rest of the page stays usable |
| Mutation error | Toast with the message; the dialog stays open with the typed reason preserved; 409 (optimistic concurrency on config/draft, or a vote already cast) shows a conflict dialog with Reload |
| 401 | Silent refresh once; then redirect to `/login?next=` |
| 403 | Toast "Not available for your role" and no navigation; PO screens never show locked placeholders for absent fields (they are simply absent) |
| 404 case | "This case is not in your view" (same text for PO/committee whether the case exists or not) |
| SSE disconnected | Top banner "Live updates paused · reconnecting" → "polling every 10 s"; cleared on reconnect |
| Agent job failed after retry (`agent.failed` event) | Analyst sees a banner on Summary: "Research step handed to a person: <reason>. Re-run from the harness panel" with a `POST cases/{id}/harness:rerun {step, reason}` action (reason required, rule 6) |
| Closed case | All write actions hidden (not disabled); header badge "Closed · read-only for everyone" |
| Empty states | Specific copy per screen ("No open conditions", "Nothing waiting on you", "No clause found for … · [Search corpus] [Note gap]") |

### 1.10 Harness monitor panel (`/monitor/:caseId`)

Purpose: show the panel, live, what the harness is doing — the walkthrough's right-hand monitor made real. Layout (standalone window, dark header in carbon-moss): header with case id, trace id (from `GET cases/{id}/harness`), status dot (running/idle/waiting-on-person), current core (`intake core` · `assessment core` · `decision core` · `oversight core`); four counters: agents run / 9, guards passed · blocked, audit records (from `audit.appended`, with "chain verified" when `GET audit/verify` is green), tokens used vs 120k case budget (ratio bar, turns amber at 80 %); a strip of the seven guards showing the last result for each; a scrolling log (ring buffer 200) of `harness.log` lines rendered as `who · text · sub-line · optional progress`, where `who` is one of H1 core, P5 guard, X2 OPA, S2 scoring, S4 audit, S5 decision, X5 traces, X6 evals, or `<name> agent`; a "Jaeger" link that opens the trace by id. Filters: by who, by guard, errors only. "Pause scroll" and "Copy log" buttons. No write actions except `rerun` for a failed step (analyst only). The same SSE event types feed the smaller "Harness" side drawer inside the analyst workbench (collapsed by default, counters only), so the analyst's own screen also shows live agent state. Data: `GET cases/{id}/harness` for the initial snapshot, then `cases/{id}/stream` events `harness.log`, `agent.started|finished|retried|failed`, `guard.result`, `budget.updated`, `audit.appended`, `core.changed`. Not available to the PO role (403 on route and stream).

---

## 2. Screen specs

Format per screen: reads · actions · gates shown (with the rationale text the user sees) · role must-not-see. Endpoints are the §1.4 names. Figures in quotes are the seed values the screen must show in the demo (F §3, §5).

### 2.1 Login
- Reads: none. Actions: `POST auth/login`; demo picker lists the 11 seeded users grouped by role.
- Copy: "One governed place for change risk assessments" · "Your role and what you can see are set by the bank's directory, not by this app."
- Gate: none. Must-not-see: nothing role-specific before login.

### 2.2 PO Dashboard (`/po`)
- Reads: `cases/dashboard` → KPIs "5 active · 1 awaiting my answer · 3.3 d avg intake to decision · 3 open conditions"; "My cases" rows 0150, 0146, 0139, 0121, 0109 (Case · Change · Stage · Next action). Banner "Starting a new change? Upload what you already have first…".
- Actions: `+ New case` → `POST cases` → `/case/:id/documents`. Row → case.
- Must-not-see: band, score, analyst notes, committee state. The DTO has no such fields; a vitest snapshot of the PO dashboard DTO type asserts the absence (`expectTypeOf<PODashboard>().not.toHaveProperty('band')`).

### 2.3 PO My Queue (`/queue`)
- Reads: `cases?scope=mine` with stage and needs-response filters; columns Case · Title · Change types · Stage · Analyst · Submitted · Completion · Due.
- Actions: Resume draft, Open. Discard draft needs a reason (ReasonDialog) → `POST cases/{id}:discard {reason}`.
- Must-not-see: residual/band column (exists on the analyst queue only).

### 2.4 PO Case › Documents (`/case/:id/documents`)
- Reads: `cases/{id}/documents` (name, pages, MB, status uploading → stored → scanned → read), `cases/{id}/intake.completion`; stream `document.updated`, `intake.progress` ("Reading business_case_remittance_MX.pdf · page 9 of 14").
- Actions: drop zone / Browse → `POST cases/{id}/documents` (multipart, 25 MB cap); "Import from SharePoint" is enabled only if the integration row is connected (otherwise shown disabled with "Connect in Integrations").
- Gate shown: Guard 1 result per file as a quiet line: "Scanned · 3 personal-data fields masked before any AI read it" (rationale: "Before any AI reads a file, it is checked for hidden instructions and personal data is masked").
- Assistant card (status line, not a chat): "Waiting for documents" → progress → "Done. 15 values found, 3 change types proposed. Review them on the Overview tab."
- Must-not-see: extraction confidences, model names, token counts.

### 2.5 PO Case › Overview (`/case/:id/overview`)
- Reads: `cases/{id}/intake` → proposed change types with SourceChips (vendor p.2 §1.1, geography p.4 l.12, product p.1 l.3), values table (15 candidates, 9 fields filled), precedent panel (`cases/{id}/precedents`: 0118 Guatemala, similarity 0.81, closed Jun 2026), gaps (6 open · 3 blocking), ring 50 % → 67 % → 83 %.
- Actions: Confirm per change type (`…:confirm`); Remove opens ReasonDialog ("Removing a type changes which fields are required. The reason is recorded next to the AI's proposal"); Reuse selected (`POST …/reuse`, per-field checkboxes, "different partner, do not reuse" rows disabled with reason); answer blocking fields inline (`PUT …/fields/{id}`).
- Gates shown: Human gate 1 — "She confirms, the AI does not. Confirming fixes the list of required fields (18)." Guard 4 line: "Every value shows its source. If the AI cannot point to a source, the value is not shown at all."
- Must-not-see: similar-case outcome or score (precedent card shows title, unit, closed month only); the analyst's flags (the "new fact: cash pickup" flag is for the analyst and is not shown here).

### 2.6 PO Case › Review & submit (`/case/:id/review`) — new in v2 (D-17)
- Reads: `cases/{id}/intake` full-case table with ProvenanceTag per value; required-fields card (field set v6, 18 fields, 6 always-required, threshold 80 %).
- Actions: Edit → "Correct a value" modal → `POST …/fields/{id}:correct` ("Your correction becomes 'owner declared'. The extracted value and its page:line are kept for the analyst."); e.g. F03 Q1 2027 → February 2027. Submit → `POST cases/{id}:validate` → ValidationModal with 4 checks revealed in order: Completion 83 % ≥ 80 % · Always-required 6 of 6 · Change types confirmed by owner · No conflicting values across documents (1 inconsistency flagged, not blocking) → Continue → `POST cases/{id}:submit` → "Submitted. Assigned to Rahul Iyer, FCRM. Target: decision-ready in 2 business days."
- Gate shown: Guard 5 rules as code — "Four plain checks, visible. If one fails you are told which one and asked only for what is missing." Submit disabled below 80 % with the exact shortfall.
- Must-not-see: assigned analyst's load ("4 open cases" is shown to the analyst only), triage rule internals.

### 2.7 PO Case › Inbox (`/case/:id/inbox`)
- Reads: `cases/{id}/threads` grouped by round ("Questions from FCRM · round 1", 2 questions, each "Re: <field>").
- Actions: answer text + attachment (`POST …/threads/{tid}:answer`, documents go through `POST …/documents` first); Send answers → banner "Saved as submission v2. The original is kept. FCRM sees exactly what changed."
- Gate shown: none for the PO; the line "The analyst never edits what you submitted; they ask" explains why questions arrive.
- Must-not-see: which control or critic finding anchored the question (anchor shows the field label only), the analyst clock.

### 2.8 PO Case › Conditions (`/case/:id/conditions`)
- Reads: `cases/{id}/conditions` + `GET decision/outcome/{id}` (PO shape: outcome label + business summary only). Banner in plain language; table CND-1 "You · before go-live · open", CND-2 "FCRM Ops · go-live · scheduled", CND-3 "You · 6 months · scheduled"; "reminders 14 days before due".
- Actions: mark a condition evidenced (`POST …/conditions/{cid}:update` with a document) — owner's own rows only.
- Footnote shown: "Scores, bands, control ratings and policy clauses are not part of this view."
- Must-not-see: score, band, votes, rationales, clauses, control ratings. Before a decision the tab shows "No decision yet" and nothing else.

### 2.9 PO Case › Activity
- Reads: `cases/{id}/activity` (PO projection: case created, documents, confirmations, submission, questions, answers, outcome). Actor chips: You · FCRM · System · AI.
- Must-not-see: agent names and models, scoring events, overrides, votes. The projection is a server allow-list of event types (tested).

### 2.10 PO Integrations
- Reads/actions: `GET/POST cases/integrations` (S1 owns the rows): SharePoint (document source), Teams/Slack (notification channel), Data catalogue MCP. Connect wizard collects location/scope only; banner "No password, token or secret is collected or stored." Disconnect names consequences.
- Scope note: SharePoint import is wired to H3 only if time allows; the row exists so the demo's "Import from SharePoint" button has a home. Mark "Connected (demo)" in seed.

### 2.11 Analyst Dashboard (`/analyst`)
- Reads: `cases/dashboard` (analyst shape): open assigned to me, approaching due, avg analyst-active cycle time, clarification rounds per case; charts: band distribution (19/49/11/1), cycle time by stage, override rate per agent (17.9 % assessment), policy mapping trend, control inventory health (stale, judgment-only).
- Actions: drill to queue with filter. Must-not-see: n/a (analyst sees all). Committee members do not get this route.

### 2.12 Analyst Queue (`/analyst/queue`)
- Reads: `cases?scope=mine|all&stage&band`; "My queue · 5 cases": 0151 New · 0146 Assessing · 0143 Awaiting business · 0140 Fast lane (pill + "screening agent proposed · confirm") · 0138 Committee. Columns Case · Change · Types · Arrived · Due · Stage · Band.
- Actions: open; Confirm fast lane on 0140 → `POST cases/{id}/gates/decide {itemType: fastLane, decision: confirm}` (R4: "The screening agent proposes the fast lane; you confirm. Band 1 is expected; you still close it yourself").
- Banner on a new case: "While the case sat in the queue, the Assessment core already started Research and Assessment."

### 2.13 Workbench shell (`/analyst/case/:id`)
- Reads: `cases/{id}` (analyst DTO), `cases/{id}/harness` (compact drawer), `audit/verify/{id}` badge. Header: title, stage pill, TwoClocks ("analyst 0h 25m · total 1d 2h"), pinned versions chip (scoring v3 · corpus v14 · controls v9 · field set v6 · routing v4 · jurisdictions 2026-06 · prompts 2026-09-28), **Acknowledge** button when `pendingAcknowledgements` is non-empty.
- Gate shown: R3 — "Band changed (2 → 3) and the composite sits 0.3 from a band edge. Acknowledge before this case can be released: the route changed from delegated close to committee." → `POST …/gates/acknowledge`.
- Rule shown when closed: "Closed means closed. No one, including the signer, can re-run or edit this assessment."

### 2.14 Workbench › Summary
- Reads: `cases/{id}/assessment`: submission summary; 5 flags with sources (cash pickup added at intake · partner audit Nov 2023, domestic scope · Mexico not on FATF lists · no clause for cash-pickup agent networks · owner corrected 1 extracted value); completeness report (missing none · stale 1 · contradictory 1 · 3 drafted, 2 kept); critic findings (2, high) as a banner; score **withheld** box: "No score is shown yet. Review the proposals in Policy and Risk & Controls first."
- Actions: **Ask now (2 questions)** → Inbox composer; **Enough to assess · proceed** → `POST …/completeness:proceed`.
- Gate shown: D-16 — "Completeness first. One batched round of questions pauses your clock and keeps the submission untouched."; "Why hide the score? A number seen first anchors the whole review."
- Must-not-see: the score value until `reviewStarted` (server returns `score: {withheld: true}`; the client never receives the number earlier).

### 2.15 Workbench › Submission (+ diff)
- Reads: `cases/{id}/submissions`, `…/diff?from=1&to=2`; version select "v1 (as submitted 2026-10-01 09:40)" / "v2 (after round 1, 14:05, Priya Nair)"; DiffTable shows F11 answered + scope letter added, F21 changed; owner-declared rows show "was Q1 2027 (case p.1 l.9)".
- Actions: none (rule 4 — "The analyst never edits the submission; request a clarification instead"). Re-tag is the one exception: Re-tag → ReasonDialog → `POST …/gates/decide {itemType: changeType, decision: add, reason}`; the banner warns it expands the required set.

### 2.16 Workbench › Inbox
- Reads: `cases/{id}/threads` + drafted round from `assessment.completeness.questions` (Q1 → CTL-T01 / F11; Q2 → F21; dropped Q3 with reason "already answered in contract §9").
- Actions: Keep/Drop per question, edit text, add question (anchor required or a reason why not), **Send round · 2 questions** → `POST …/clarification-rounds`. Banner "Sent to Priya Nair. Analyst clock paused; total clock keeps running." Resolve thread after answer.
- Gate shown: Guard 5 batch rule — "One round, field-anchored, sent once."
- Must-not-see: n/a.

### 2.17 Workbench › Policy
- Reads: `assessment.clauses` — 4 ClauseCards verbatim with source and version (FFIEC-TPPP-04 p.312 · FCRM42-3.1 §3.1 v2026-03 · TP71-2.3 §2 v2025-11 · OFAC-1.2 §1 v2026-01); gap row "No clause for cash-pickup agent networks in corpus v14".
- Actions: Accept per clause → `gates/decide {clause, accept}`; Remove → ReasonDialog; Search corpus → `POST …/policy/search` ("Searched: 0 results"); Note gap → `POST …/policy/gap` ("Gap recorded. The policy_gap agent will suggest coverage to the policy owner").
- Gate shown: Human gate per item — "Word for word, with the source. There is no accept-all button, on purpose: an accept-all would record consent, not review." Guard 4 note: "An attempt that paraphrased a clause was rejected and retried on the fallback model" (from the audit record #28/#29 when present).
- Must-not-see: n/a.

### 2.18 Workbench › Risk & Controls
- Reads: `assessment.areas[]` — per area L, I, inherent, matched controls (channel: T01 0.35 · 7.6 · DD report 2023-11 **stale**; T02 0.20 · 6.8; T03 0.15 · 7.0; T04 0.30 · 8.4; caption "4 of 13 matched controls shown"), CE (weighted mean 7.59), retention 0.54, residual 10.9, area weight 0.45; score box (after review starts) "9.8 · Band 2 · delegated close · boundary flag 0.3 from Band 3"; critic banner on T01; RiskMatrix; derivation table; planned controls list ("planned, not scored").
- Actions: Confirm per control → `gates/decide {control, confirm}`; Review/Override → ReasonDialog with value, weight, live `scoring/simulate` preview ("CE 7.6 → 6.5 · retention 0.54 → 0.61 · residual 10.9 → 12.2 · composite 9.8 → 10.4 · Band 3") → `gates/decide {control, override, value: 4.5, reason}`; override factor L/I similarly; Add planned control → `POST …/controls/planned`; Raise clarification from here.
- Gates shown: Guard 7 — "Confirm or override each rating. A reason is required on every override and is written to the hash-chained record." Red banner after the override: "Band boundary crossed. Logged with before, after and cause (#49 · hash 8f3a…c21e · prev #48). Oversight changes from delegated close to committee review. Acknowledge to continue." Critic (Guard 6): "Vendor due-diligence rating 7.6 is 2.7 above the 4.9 average across the five closest payout-partner cases (0118, 0097, 0084, 0109, 0076); only evidence is a 2023 audit of domestic operations. Explain or adjust before sign-off." Planned control note: "A control that does not exist cannot mitigate anything."
- Must-not-see: n/a.

### 2.19 Workbench › Recommendation
- Reads: `cases/{id}/recommendation` — narrative draft ("Drafted by narrative agent · 2 edits by R. Iyer"), outcome select (4 outcomes), conditions CND-1..3 with owner/due/source (overridden control · planned control · policy gap), business summary with Guard 4 scan result "0 restricted terms (score, band, rating, clause)".
- Actions: edit narrative/conditions (`PUT`), **Lock recommendation** → `…:lock` (disabled until every proposal is accepted/overridden and acknowledgements are clear; the server returns the blocking list), Unlock → ReasonDialog (`…:unlock`).
- Gate shown: D-19a — "Locking freezes the numbers. Unlocking needs a reason and regenerates the pack; every cast vote is re-requested."
- Must-not-see: n/a.

### 2.20 Workbench › Handoff
- Reads: `decision/packs/{caseId}` — pack v1 preview: 10.4 · Band 3 · 3 areas · controls 13 · overrides 1 · planned 1 · clauses 4 · gaps 1 · rounds 1 · versions; 4 release checks (locked · all items reviewed · boundary and band change acknowledged · business summary present).
- Actions: Generate pack (`POST decision/packs`) · Approve pack and send to committee (`…:approve`) → "Released to A. Mendes, J. Okafor, L. Chen. Votes are hidden until all three are cast." Band 1–2: **Close under delegated authority** → ReasonDialog-like summary → `POST decision/outcome` (R1; banner "No independent challenge on a delegated close; closed delegated cases appear in the committee's monthly spot-check digest").
- Gate shown: Guard 5 release checks — "Nothing leaves half-done: four checks, by code, before release." Pack note: "Assembled by code from locked numbers and pinned versions. No model involved."
- Must-not-see: n/a.

### 2.21 Workbench › Activity
- Reads: `cases/{id}/activity` (full) merged with `audit/records?case=` chips; filter by actor/type; "Verify chain" → `audit/verify/{id}` → "67 records · chain verified".
- Actions: Export case record → `GET cases/{id}/export` (server-side JSON + PDF-ready HTML; MinIO link).

### 2.22 Assurance › Ask (`/analyst/assurance?tab=ask`)
- Reads/actions: `POST audit/examiner/ask {question, caseId?}` → 7 sentences each with `AuditRecordChip` (#32, #33, #34, #49, #50, #51, #63, #64); reference chips (routing v4, scoring v3); refusal state "The audit chain does not contain the answer to that question." `audit/versions/{case}` table.
- Gate shown: Guard 4 grounding — "Every sentence cites a record. Nothing outside the chain." Monitor line: "examiner permitted tool: audit_log (read)".

### 2.23 Assurance › Overrides · Watchlist · Agent quality
- Overrides: `audit/overrides` table (case, item, proposed → final, by, reason, agent, date); filters; CSV export (server-side, audited).
- Watchlist: `policy/watchlist/reflags` (fixture v2026-10 "test fixture" pill; 0144 Algeria, 0147 Namibia) → **Acknowledge · re-run research** per row (`…:acknowledge` → H1 queues research re-run). Gate: "You acknowledge; the agent only flags."
- Agent quality: `audit/agent-quality?week=2026-09-28` — 9 rows (intake 96.4 · research 99.1 · assessment 17.9 override · critic 91.7 · narrative 97.5 · examiner 100 · screening 96.2 · watchlist 100 · policy_gap 78.6), "0 regressions vs 2026-09-21", link to `/evals/results/latest.html` served by nginx.

### 2.24 Config (`/analyst/config#<section>`), 8 sections
Shared frame: section nav; server-side draft (`…/draft`, etag); sticky WorkingSetBanner "n unsaved changes · sections…" with Discard; Publish → ImpactPreviewModal (`…:preview`) → version note (required) → `…:publish` → new immutable snapshot in VersionHistory. Banner "Non-retroactive: cases pin the version in force; re-scoring is an explicit, logged choice." Rationale shown: "Rules are tunable by the risk function, never by the business owner of a change."
1. **Categories & Fields** — field library (F01–F62), always-required flags, per-type packs, threshold 0.8 (`max_required` 18); add-trigger wizard; resolved-set preview.
2. **Classification Rules** — per-type signals with weights and thresholds, disambiguation notes, screening fast-lane rule parameters (R4: volume < $5M, no new geography/segment/product); test panel calls `POST policy/classify:simulate`.
3. **Scoring Model** — domain weights 0.20/0.20/0.24/0.36, L/I anchors, bands read-only here, retention floor fixed ("Fixed at 40 %, on this or any page. Controls mitigate risk and never eliminate it."); test panel → `scoring/simulate`; ScoringImpactModal offers per-case re-score (logged).
4. **Control Inventory** — 24 controls v9, weight sum 1.00 per domain badge, CE, evidence + date, stale/judgment-only states; CSV import with per-row errors.
5. **Policy Corpus** — 9 sources, 197 clauses v14; external clause text locked; retire-not-delete when cited; upload → H3/H4 ingestion job with progress via `cases/stream`.
6. **Rules & Routing** — routing v4 R1–R6 as data rows (read + edit thresholds: boundary 0.5, recency 18 months, nearing 3, fast-lane volume, quorum 3, reminder days 14); band table edits publish through Scoring Model; RoutingImpactModal.
7. **Jurisdiction Risk List** — current v2026-06 (call-for-action 3, increased monitoring 22) and fixture; external rows read-only; internal ≥ external rule; default for unlisted (never silent); "Preview FATF sync" creates a draft (never commits directly).
8. **AI Agents** — 9 agents: tier, model → fallback, tools allow-list, skills, prompt version, budget, eval set, last eval score, override rate; effort/tier change needs a note and triggers the eval gate indicator ("A model change must pass the agent's eval set before it is used on cases"). Footer: "Pack generation and scoring are not listed here because they are deterministic."
- Must-not-see: committee and PO roles have no route; the PO has no AI configuration page at all (fixes D §13.4 governance clash, rule 4).

### 2.25 Committee Queue (`/committee`)
- Reads: `cases?scope=committee` — cases with a released pack plus those this committee closed; columns Case · Title · Types · Band · Analyst · Received · Your vote; filter Not voted / Voted / All.
- Must-not-see: other members' votes or the tally before reveal (`yourVote` only).

### 2.26 Committee Case › Pack · Inbox · Vote
- Pack: `decision/packs/{caseId}` (frozen snapshot v1, hash, 12 sections incl. the analyst's recommendation, boundary flag + acknowledgement, override log with reasons, policy removals, versions; addenda appended). Banner "Read-only. You read the pack and cast one vote." Export → server PDF/HTML.
- Inbox: `…/threads` — Ana's question to the analyst, visible to all three; answer appears as "Addendum 1 · numbers unchanged · no vote voided" (R6). Compose → `POST …/threads`.
- Vote: `decision/votes/{caseId}` → "1 of 3 votes in"; VoteCards: J. Okafor "voted · hidden ●●●", L. Chen "not yet voted"; rationale textarea (required, min 20 chars); four buttons (approve, approve with conditions, defer, reject) → `POST decision/votes`; after reveal: all three choices and rationales, banner "Revealed together: approve with conditions ×2 · defer ×1. Majority: approved with conditions." Tally line "counted by code (S5)".
- Tally rule (C-06, ADR-31): majority of three decides; a 1-1-1 split is `no_majority`, never `deferred`: round two opens and all three vote again.
- Gates shown: R2 — "Three votes, hidden until all are in, each with a written rationale, counted by code. Nobody — not the analyst, not the system — approves or rejects anything on your behalf." A vote cannot be changed once cast (409 on repeat).
- Must-not-see: other members' choices before reveal (the DTO omits `choice` for others until `revealedAt`); the PO's identity beyond name/unit; nothing editable.

### 2.27 Harness monitor (`/monitor/:caseId`) — see §1.10.

---

## 3. Test strategy

Principles: product rules are tested at the API boundary, not only in the UI; every rule in `docs/governance/rules.md` has at least one black-box test id; the demo's 21 scenes are an E2E suite; LLM calls are replayed in all gated tests and live only in evals and `make e2e-live`.

### 3.1 Layers

**(a) Python unit tests per package** (`src/**/tests/unit`, pytest + hypothesis, mypy --strict on the package)
- `fcrm_scoring` golden tests from the seed: `test_demo_before()` 4.91 + 3.06 + 1.83 → 9.8 Band 2 boundary 0.3; `test_demo_after()` CE 7.59 → 6.51, retention 0.6097, residual 12.194, 5.49 + 3.07 + 1.83 → 10.4 Band 3; `test_all_80_cases_recompute()` parametrised over `seed.json.cases` (0 mismatches, distribution 19/49/11/1, route matches band); property tests: retention ∈ [0.4, 1.0], residual ≤ inherent, composite monotone in CE, rounding before band lookup (`test_5_04_is_band_1`, `test_5_05_is_band_2`), boundary flag convention (distance to the lower bound of the higher band), planned controls contribute nothing, weights renormalise over in-scope domains and matched controls, zero matched controls → CE 0 retention 1.0.
- `fcrm_guardrails`: one test module per guard; the 8 rules as pure functions with a positive and a negative case each; 50 injection samples and PII fixtures for guard 1; quote-grounding for guard 4 (quote must exist in the source text; a paraphrase is rejected).
- `fcrm_agents`/`fcrm_tools`/`fcrm_prompts`: registry lock tests (`expected_agents.txt`, `expected_tools.txt`), prompt front-matter schema, every agent's tool list ⊆ OPA `tool_permits.json`, output schemas parse the replay fixtures.
- `fcrm_caseflow` (H1): state machine transitions table-driven; gates block until `gate.decided`; budget trips at 120k; retry once then hand to person; idempotent step replay; routing R1–R6 evaluated from data (R4 fast-lane predicate, R5 lowest load with tie by id).
- `fcrm_audit`: hash chain (`sha256(prev_hash + canonical_json(record))`), verify detects a tampered middle record, seq per case is gapless.
- `fcrm_decision`: tally (2-1 → majority; 1-1-1 → `no_majority` and round two, C-06/ADR-31; votes immutable; reveal only at 3), pack assembly is a pure function of locked inputs (same inputs → same hash), release checks.
- `fcrm_case`: role DTO projections — a parametrised test walks every PO response model and asserts no field named like `/score|band|rating|residual|clause|vote|rationale|composite|ce\b/` and that the PO activity projection is an allow-list; completion % from the pinned field set; submission versioning.
- `fcrm_seed`: `gen_seed.py` is deterministic (same bytes twice), loader idempotent.

**(b) Service integration tests** (`/tests/integration`, pytest + testcontainers: mongo:8, rabbitmq:3-management, minio; OPA via container)
- Each service boots against real Mongo; repository round-trips; S4's insert-only DB user cannot update/delete (expect `OperationFailure`); S1 publishes `case.submitted` to RabbitMQ and H1 consumes it; H1 outbox relays after a simulated crash between DB write and publish; H2 claims a job atomically (two workers, one claim); MinIO upload → H3 canonical Markdown with `--- Page N ---` and line offsets; OPA route policy default-deny tests (`opa test ops/opa/`) and tool permits (`intake → scoring_engine` denied).
- Seed load → `GET cases/dashboard` as each seeded role returns the seed figures.

**(c) Contract tests** (`/tests/contract`)
- OpenAPI: each service's `/openapi.json` is snapshotted to `docs/architecture/openapi/<svc>.json`; `oasdiff breaking` against the committed snapshot fails the push gate; `schemathesis run --checks all` against each live service in `replay` mode (auth header fixture per role; stateful phase on cases); response models must forbid extra fields (`extra="forbid"`) so a leak is a schema error.
- Events: `fcrm_contracts/events/*.py` Pydantic models → `make events-schema` exports JSON Schema to `docs/architecture/events/<name>.v1.json`; producer tests validate every published payload; consumer tests parse the committed examples in `docs/architecture/events/examples/`; a diff of the exported schema against the committed one fails the gate (schema changes are explicit, versioned `v2`).
- Web: `api-gen` output is committed; `tsc` fails if a screen uses a field the OpenAPI no longer has.

**(d) Frontend unit and component tests** (`src/web`, vitest + RTL + MSW node + vitest-axe)
- Primitives: render, keyboard, axe. Domain: `ScoreBadge` reads ranges from the model query; `ReasonDialog` disables Save below 20 chars and preserves text on error; `SourceChip` renders nothing without a source; `DiffTable` marks changed rows; `VoteCard` hidden/revealed states.
- Screens against MSW seed handlers: PO Overview confirms 3 types (3 calls, never one bulk call); Risk & Controls override flow shows the simulate preview and the band-crossed banner; Vote tab shows "1 of 3" and no choices until reveal; Config publish requires a note.
- Type tests (`expectTypeOf`) on the generated PO DTOs for the absence of restricted fields; ESLint `no-restricted-imports` test that `features/po/**` cannot import `ScoreBadge`, `RiskMatrix`, `ClauseCard`.
- Coverage targets: statements 80 % for `components`, `api`; screens covered by E2E.

**(e) Playwright E2E: the 21-scene demo** (`/tests/e2e/demo`, three browser contexts Priya / Rahul / Ana plus API-driven votes for Okafor and Chen, `FCRM_LLM_MODE=replay` (proposed, C-07), seeded stack via compose `test` profile)
- `scenes.spec.ts` runs the scenes in order as one serial test file with a named step per scene (Playwright `test.step`), each asserting the seed figures: S1 KPIs 5/1/3.3/3; S2 case id pattern + "Field set v6"; S3 two uploads → 15 values; S4 three confirms → "Required fields 18"; S5 reuse 0118 → 12 of 18; S6 answers → 15 of 18 (83 %); S7 correction F03, 4 checks, "Assigned to Rahul Iyer"; S8 queue shows 0151 New + 0140 Fast lane; S9 flags ×5, completeness 0/1/1, score withheld; S10 2 questions sent, clock paused; S11 Priya answers + scope letter → v2; S12 diff then 4 accepts; S13 controls table, critic banner 7.6 vs 4.9; S14 override → 10.4 Band 3 → Acknowledge; S15 lock, 3 conditions, "0 restricted terms"; S16 4 checks green → released; S17 Ana asks, Rahul answers → addendum, "1 of 3"; S18 Ana votes, Chen votes (API), reveal 2·1 → approved with conditions; S19 Priya's Conditions tab, footnote, chain verified; S20 examiner 7 sentences citing 8 records, watchlist 2 re-flags; S21 monitor counters 67 records, 9 agents, tokens ≤ 120k.
- Each step takes a screenshot into `tests/e2e/demo/artifacts/<nn>-<label>.png` (also used in the deck); an axe scan runs per screen; the trace is kept on failure.
- `make demo` reuses the same script with `slowMo` and a visible browser for rehearsal.

**(f) Third-party black-box suite** (`/tests/e2e/blackbox`, pytest + httpx + a Playwright smoke; uses only the public API and the browser against the composed stack; no imports from `src`)

| ID | Rule | Check |
|---|---|---|
| BB-01 | Nothing auto-approved/rejected | Drive a case to a released pack; wait 60 s; `outcome` is null; cast 3 votes; outcome equals the majority; with a 1-1-1 split the round is `no_majority` and round two opens, never approve/reject and never `deferred` (ADR-31) |
| BB-02 | PO sees no scores | Walk every PO endpoint for the demo case and dashboard; JSON contains no key or string matching the restricted regex; PO SSE stream for 30 s contains no `score.*`/`harness.*`/`audit.*` events; PO token → `/monitor` stream 403 |
| BB-03 | Overrides need reasons | `gates/decide {override}` without reason → 422; with 10-char reason → 422; with reason → 200 and an audit record of type `human.override` carrying before/after/cause |
| BB-04 | Audit chain verifies | `audit/verify/{case}` → ok; direct Mongo tamper of one record (test harness only, via admin user) → verify returns the first broken seq |
| BB-05 | Votes hidden until all cast | After 2 votes, `GET votes` as each member shows own choice only; S5 DB has no reveal; third vote → all three visible in one response |
| BB-06 | Pack frozen | Approved pack hash is stable across 10 reads; `PUT recommendation` after lock → 409; unlock with reason → pack v2 with new hash, v1 still retrievable, cast votes marked `re_requested` |
| BB-07 | Closed means closed | After outcome: every write endpoint on the case → 409 `case_closed`, for analyst, PO and committee tokens |
| BB-08 | No bulk accept | `gates/decide` rejects arrays; 4 clauses need 4 calls and produce 4 `gate.decided` records |
| BB-09 | Analyst never edits submission | Analyst `PUT fields/{id}` → 403; PO answer creates v2 and v1 stays byte-identical |
| BB-10 | Planned control not scored | Add a planned control; `assessment.score` unchanged; pack lists it under planned |
| BB-11 | Auth hygiene | Refresh token as bearer → 401; expired access → 401; role escalation by editing the JWT payload → 401 |
| BB-12 | Policy quoted, not generated | Every accepted clause's `quote` is a substring of `GET policy/clauses/{id}` text for the pinned corpus version |
| BB-13 | Boundary/band change acknowledged | Release without acknowledgement → 409 listing `boundary`, `band_change`; after acknowledge → 200 |
| BB-14 | Versions pinned | Publish a new control inventory v10; the open demo case still reports controls v9 and its score is unchanged |

**(g) Resilience tests** (`/tests/resilience`, pytest + docker SDK against the `test` profile; nightly)

| Scenario | Injection | Must hold |
|---|---|---|
| Agent worker killed mid-run | `docker kill fcrm-agent-worker` 2 s after `agent.started research` | Job lease expires (30 s), another worker re-claims, exactly one `proposal.research` in the ledger (idempotency key = job id), chain intact, case advances |
| Bedrock/vendor timeout | `FCRM_LLM_FAULT=timeout:assessment:1` in replay mode | Guard 3 records `agent.retry` on the fallback model, one retry only, budget counter includes the failed attempt, wall time < 180 s + margin |
| Both models fail | `FCRM_LLM_FAULT=error:research:2` | `agent.failed` event, case waits on a person, analyst sees the banner, `harness:rerun` with reason resumes |
| RabbitMQ restart | `docker restart fcrm-rabbitmq` during intake | Outbox redelivers `case.submitted`; consumers reconnect; no duplicate case run (idempotent step) |
| Mongo restart | restart during review | Services' `/ready` goes red then green; no 500s surface as data loss; SSE clients reconnect and catch up via `Last-Event-ID` |
| Identity down | stop identity for 90 s | New logins fail with 503; existing tokens validate from the 60 s cache then fail closed; no endpoint returns data without a valid token |
| Poison message | publish a malformed `agent.run` | Message lands in `fcrm.dlq` after 1 retry; `make replay-dlq` re-injects after a fix; nothing else stalls |
| Budget exhaustion | `budgets.per_case=5000` | H1 stops issuing jobs, `budget.exceeded` event, human hand-off, no partial proposals shown as complete |

**(h) Evals as a test stage** (`/evals`, pydantic-evals + pytest; see design A for datasets)
- `make evals` runs every agent's set in `live` mode and writes `evals/results/<date>/<agent>.json` + `report.html` + `evals/results/trend.json`; `make evals-gate AGENT=x` runs one set and fails if the score is below `evals/results/latest.json` minus tolerance (0.5 pt) — this is the prompt-change gate (BP §8). Guardrail evals (injection, PII, rules) run in the push gate because they are deterministic. Narrative/judge evals run nightly (cost).

### 3.2 `/tests` tree

```
tests/
  README.md                 layers, how to run, what gates what
  conftest.py               role tokens, base URL, seed accessors (reads src/packages/fcrm_seed/seed.json), compose wait
  integration/              test_s1_case.py test_s2_scoring.py test_s3_policy.py test_s4_audit_insert_only.py test_s5_decision.py
                            test_h1_outbox.py test_h2_claims.py test_h3_ingestion.py test_opa_policies.py test_seed_load.py
  contract/                 test_openapi_snapshots.py (oasdiff) test_schemathesis.py test_event_schemas.py examples/
  e2e/
    playwright.config.ts    projects: demo, blackbox-smoke; baseURL http://localhost:8080; storageState per persona
    fixtures/               personas.ts (Priya, Rahul, Ana, Okafor, Chen) seed.ts selectors.ts
    demo/                   scenes.spec.ts scenes.ts (21 steps) artifacts/
    blackbox/               test_bb_01_no_auto_decide.py … test_bb_14_versions_pinned.py smoke.spec.ts
  resilience/               test_worker_kill.py test_llm_timeout.py test_rabbit_restart.py test_mongo_restart.py test_identity_down.py test_dlq.py test_budget.py
  perf/                     k6 smoke: 20 concurrent PO uploads, 50 analysts reading; p95 budget 500 ms on reads (R-prod evidence, nightly)
  tools/                    wait_for_ready.py tamper_record.py (admin-only, test profile) fault_env.py
```

### 3.3 Make targets and gating (tiers proposed, C-09)

| Target | Runs | Time | Gate |
|---|---|---|---|
| `make check` | ruff format --check · ruff · mypy --strict (changed packages) · pytest unit (changed packages, `-x`) · oxlint · tsc -b · vitest --changed · governance-append-only check · commit-msg lint | < 90 s | **Commit** (Claude Code Stop hook + pre-commit) |
| `make gate` | `check` on all packages · pytest unit all · vitest all · contract (oasdiff, event schemas) · guardrail evals · `make evals-gate` for agents whose prompts changed · compose config validate · Semgrep (`p/python`, `p/typescript`) | < 10 min | **Push / end of task** (`make gate` must pass before a subagent reports done) |
| `make test-int` | integration with testcontainers | 5 min | nightly + before integration checkpoints |
| `make e2e` | compose `test` profile up → seed → 21 scenes → blackbox | 15 min | nightly + checkpoints + before any demo |
| `make resilience` | §3.1(g) | 20 min | nightly |
| `make evals` | full eval run, live models, reports committed | 30 min | nightly, and before tagging a release |
| `make nightly` | gate · test-int · e2e · resilience · evals · token-report · drift report → `ops/reports/nightly/<date>.md` | ~1.5 h | launchd job 02:00; results committed next morning with `ops(nightly): …` |
| `make demo` | e2e demo with headed browser and slowMo | 6 min | rehearsal |

---

## 4. `/ops`

### 4.1 Layout (proposed)

```
ops/
  README.md                       what is here, one-command start, ports table
  compose/
    docker-compose.yaml           base: infra (mongo, rabbitmq, redis, minio, opa, nginx, identity) + S1–S5 + H1–H4 + web (nginx-served build)
    docker-compose.dev.yaml       bind mounts, `uv run --reload`, vite dev server, exposed ports, FCRM_LLM_MODE=replay by default
    docker-compose.obs.yaml       otel-collector, jaeger, prometheus, grafana (profile `obs`)
    docker-compose.test.yaml      ephemeral volumes, fixed ports +1000, FCRM_LLM_MODE=replay, admin tamper user for BB-04 (profile `test`)
  env/
    .env.example                  every variable with a comment; copied to /.env by `make env`
    contract.md                   name · required · default · consumer · secret? · notes
    check_env.py                  validates .env against contract.md; fails on unknown or missing required
  nginx/nginx.conf                /api/fcrm/<svc>/ → service; / → web; SSE: proxy_buffering off, read timeout 1h
  opa/                            policies (routes.rego, tools.rego), permissions.json, tool_permits.json, tests/
  mongo/init/                     create databases and users (incl. audit insert-only user), schema validation for audit_records
  otel/collector.yaml             receivers otlp → exporters jaeger, prometheus, logging
  prometheus/prometheus.yml       scrape otel-collector:8889, services /metrics
  grafana/provisioning/           datasources (Prometheus, Jaeger), dashboards/{case-throughput,agent-tokens-cost,guard-blocks}.json
  scripts/
    seed_load.py seed_verify.py backup.sh restore.sh replay_dlq.py verify_chain.py rotate_keys.sh wait_ready.py
    token_report.py claude_usage.py nightly.sh
  runbooks/                       start.md reset.md rotate-keys.md replay-dlq.md verify-audit-chain.md backup-restore.md agent-stall.md demo-day.md
  aws/deployment-path.md          §4.9
  token-analysis/                 README.md runtime/<date>.md build/<date>.md latest.md
  reports/nightly/                generated nightly summaries
```

Root `docker-compose.yaml` uses `include:` of the four compose files; root `Makefile` fans out.

### 4.2 Makefile (root)

`make env` (copy example, run check) · `make up` (`compose --profile core up -d --build`, waits for `/ready` on all) · `make up-obs` (+ obs profile) · `make down` · `make logs SVC=` · `make seed` · `make seed-verify` (recompute 80 cases via S2 and compare, exit 1 on mismatch) · `make reset` (down -v, up, seed) · `make demo-reset` (reset only `fcrm_case/decision/harness/audit` for case 0151 back to the "draft" state: drop its runtime docs, keep the other 79 cases) · `make api-gen` · `make events-schema` · `make check` · `make gate` · `make test-int` · `make e2e` · `make resilience` · `make evals` · `make evals-gate AGENT=` · `make nightly` · `make backup` · `make restore TS=` · `make replay-dlq` · `make verify-chain CASE=` · `make token-report` · `make build-usage` · `make rotate-keys` · `make demo`.

### 4.3 Env contract (excerpt; full table in `ops/env/contract.md`)

| Variable | Req | Default | Consumer | Secret |
|---|---|---|---|---|
| `FCRM_ENV` | y | `dev` | all | n |
| `MONGO_URL`, `MONGO_AUDIT_INSERT_URL` | y | compose values | S1–S5, H1, H2 (audit insert URL only in S4) | y |
| `RABBITMQ_URL` | y | | S1, H1–H4 | y |
| `REDIS_URL` | n | | S1 (SSE fan-out), X1 | y |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, buckets `uploads canonical packs` | y | | S1, H3, S5 | y |
| `OPA_URL` | y | `http://opa:8181` | all | n |
| `IDENTITY_URL`, `JWT_ISSUER`, `JWT_AUDIENCE` | y | | all | n |
| `JWT_SIGNING_KEY` (RS256 private, identity only), `JWT_PUBLIC_KEY` | y | none (no fallback) | X1 / all | y |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | live only | | H2, H4 | y |
| `S3_VECTORS_BUCKET`, `S3_VECTORS_INDEX_PREFIX` | live only | in-memory backend when unset | S3, H4 | n |
| `FCRM_LLM_MODE` | y | `replay` | H2 | n |
| `FCRM_LLM_FAULT` | n | | H2 (test only) | n |
| `FCRM_SEED_ON_START` | n | `true` | S1–S5 | n |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | n | `http://otel-collector:4317` | all | n |
| `LOGFIRE_TOKEN` | n | | otel-collector exporter | y |
| `VITE_API_MODE`, `VITE_DEMO_USERS` | y | `live`, `true` | web | n |

Rules: no default for any secret; `check_env.py` refuses `FCRM_LLM_MODE=live` without keys; `.env` is gitignored and the hook blocks reading it (CLAUDE.md rule).

### 4.4 Health and readiness

Every service and worker exposes `GET /health` (200 if the process is up) and `GET /ready` (checks: Mongo ping, RabbitMQ channel, MinIO bucket head where used, OPA `/health`, identity `/core/validate` reachable; returns `{ok, checks:{…}, version, seed_versions}`). Workers run a tiny FastAPI on 900x for these. Compose `healthcheck` uses `/ready`; `depends_on: condition: service_healthy` orders the start; `wait_ready.py` polls all of them for `make up`.

### 4.5 Seed loading

`seed_load.py --seed src/packages/fcrm_seed/seed.json --target all|case|scoring|policy|audit|decision|identity`: idempotent upserts keyed by id; writes reference versions (field set v6, controls v9, corpus v14, routing v4, jurisdictions v2026-06 + fixture, scoring v3, prompts 2026-09-28) and the 80 cases with pinned versions; demo case 0151 loads in `draft` with its documents staged in MinIO (`uploads/FCRM-2026-0151/…`) and its replay fixtures present; `audit` loads the 79 closed/open cases' chains and leaves 0151 empty (its 67 records are produced live). `seed_verify.py` recomputes every case through `POST scoring/score` and diffs. Services also self-seed on an empty database when `FCRM_SEED_ON_START=true` (first `make up` works without a separate step).

### 4.6 Backup and restore

`backup.sh`: `mongodump --uri … --gzip --archive=ops/backups/<ts>/mongo.gz` for all `fcrm_*` databases plus `mc mirror` of the three MinIO buckets to `ops/backups/<ts>/minio/`; writes `manifest.json` (git sha, seed versions, chain heads per case). `restore.sh TS=`: stops services, `mongorestore --drop`, mirrors MinIO back, starts, runs `verify_chain.py --all` and `seed_verify.py`. Nightly backup is part of `make nightly`. Audit is never pruned; backups of audit are tagged read-only in the manifest.

### 4.7 Logs, traces, metrics

- OTel SDK in every Python service (FastAPI, httpx, pymongo, aio-pika instrumentation) and Pydantic AI's built-in spans; span attributes `case_id, agent, prompt_v, model, tier, tokens_in, tokens_out, cost_usd, guard, result`. One trace per case run: H1 creates the root span per step and propagates `traceparent` through RabbitMQ headers; the web adds `X-Trace-Id` which nginx forwards.
- Collector exports: traces → Jaeger; metrics → Prometheus (`prometheus` exporter on 8889); logs → stdout JSON (docker) — Loki is optional, not built.
- Metrics (Prometheus names): `fcrm_case_stage_transitions_total{from,to}`, `fcrm_case_intake_to_ready_seconds` (histogram), `fcrm_gate_wait_seconds{gate}`, `fcrm_agent_run_duration_seconds{agent,model}`, `fcrm_agent_tokens_total{agent,model,kind=input|output|cache}`, `fcrm_agent_cost_usd_total{agent,model}`, `fcrm_agent_retries_total{agent,reason}`, `fcrm_agent_fallback_total{agent,from,to}`, `fcrm_guard_results_total{guard,result=pass|block}`, `fcrm_opa_decisions_total{kind,result}`, `fcrm_budget_used_ratio{case}`, `fcrm_audit_records_total`, `fcrm_sse_clients`, `http_server_duration_seconds`.
- Grafana dashboards (provisioned JSON, dataviz rules: one palette, no dual axes, direct labels):
  1. **Case throughput** — cases by stage (stacked area), intake→decision-ready p50/p95 (vs 2-day target line), gate wait by gate, SLA at-risk count, band distribution, fast-lane share.
  2. **Agent latency, tokens, cost** — per agent p50/p95 duration, tokens per run (input/output/cache), cost per case, fallback and retry rates, budget usage ratio per active case, model mix.
  3. **Guard blocks** — results by guard (pass/block), OPA denies by agent·tool, output-shape rejects by agent (paraphrase, missing source), rules refusals by rule id, budget trips, input quarantine count.
- Logfire: set `LOGFIRE_TOKEN` and the collector adds an OTLP exporter; nothing else changes (BP §9).

### 4.8 Runbooks (`ops/runbooks/`)

- **start.md** — prerequisites (Docker 27+, 8 GB), `make env`, `make up`, `make seed-verify`, open `http://localhost:8080`, demo users, `make up-obs` for Grafana/Jaeger, troubleshooting table (port in use, MinIO bucket missing, OPA policy syntax).
- **reset.md** — `make demo-reset` (0151 back to draft, 60 s) vs `make reset` (everything, 3 min); what each drops; why audit for other cases is kept.
- **rotate-keys.md** — vendor keys (edit `.env`, `make rotate-keys` restarts H2/H4 only, verify with `make evals-gate AGENT=screening`), JWT signing key (generate RS256 pair, identity restart, tokens invalidated, users re-login), MinIO/Mongo credentials (update secrets, rolling restart order), audit of the rotation in `ops/reports/`.
- **replay-dlq.md** — inspect `fcrm.dlq` in the RabbitMQ UI (15672), `make replay-dlq` (dry-run lists messages with job ids and failure reasons; `CONFIRM=1` re-publishes to the original exchange with `x-replayed` header), what to check after (job state, audit record `agent.rerun`).
- **verify-audit-chain.md** — `make verify-chain CASE=` and `--all`; reading a break report (first bad seq, expected vs actual hash); never "fix" a chain: record an incident ADR and restore from backup.
- **backup-restore.md**, **agent-stall.md** (symptoms in the monitor, `agent.failed`, hand-off, rerun with reason, when to switch `FCRM_LLM_MODE=replay` for a demo), **demo-day.md** (rehearsal checklist, two windows, replay mode, reset between runs, fallback to the static walkthrough HTML).

### 4.9 AWS deployment path (`ops/aws/deployment-path.md`, documented not built)

Outline: 1. Target shape — ECS Fargate services for S1–S5, H1–H4, identity, OPA sidecars, behind an ALB with path routing (`/api/fcrm/*`) and CloudFront for the web bundle (S3). 2. Data — DocumentDB-compatible or MongoDB Atlas (Mongo 8), Amazon MQ (RabbitMQ), ElastiCache (Redis), S3 for files (MinIO's S3 API is the contract, D-55), S3 Vectors as already used. 3. Secrets — Secrets Manager injected as task env; no `.env`. 4. Identity — Cognito or Entra federation feeding the same `/core/validate` contract. 5. Observability — ADOT collector sidecar → X-Ray/CloudWatch (or the same Jaeger/Prometheus on ECS), CloudWatch dashboards mirroring the three Grafana boards, alarms on guard-block spikes, budget trips, chain verify failures. 6. Scaling — H2 workers autoscale on queue depth; S1 ≥ 2 with Redis fan-out; S4 with the insert-only DB user; backups via snapshots plus the same `mongodump` job. 7. Go-live path — build images with the same Dockerfiles, push to ECR, `copilot`/CDK outline, blue/green on ALB, smoke = `tests/e2e/blackbox` against the new environment, rollback = previous task definition. 8. Cost sketch per month. 9. What stays the same (every container, every env name) and what differs (secret source, TLS, DNS).

### 4.10 Token-analysis report

Source of truth at runtime: `fcrm_audit.token_records` (one row per model call: `case_id, agent, tier, model, fallback_used, attempt, tokens_in, tokens_out, tokens_cache_read, tokens_cache_write, cost_usd, wall_ms, guard4_rejected, prompt_v, scene?`), written by H2 after every call (success or failure). `token_report.py` produces `ops/token-analysis/runtime/<date>.md` and refreshes `latest.md` with: totals per case (demo case vs 80-case eval run), tokens and cost per agent and per model, fallback and retry overhead, cache hit ratio, cost per case and projected cost per 1,000 cases, p95 wall time per agent, budget utilisation (per-run and the 120k case cap), the top-5 most expensive prompts, and a "what we optimised" section maintained by hand with before/after numbers (e.g. research clause window trimmed to matched domains, critic input limited to ledger deltas, haiku for screening/intake/watchlist, replay mode for tests, prompt caching of the corpus catalogue). Build-time usage is §5.9 and lands in `ops/token-analysis/build/`.

---

## 5. The AI-driven SDLC harness

The harness is the repeatable way this repo is built: Claude Code (operator-directed) with subagents in worktrees, guidance files per package, hooks that enforce gates, templates that force decision references, and governance tables that record every decision, defect and recovery. Each stage produces an artefact that the next stage consumes; the connection is a file path and a gate id, not a slide.

### 5.1 Stage table (gate ids G-xx proposed; the product gates keep the BA's names)

| Stage | AI role (who, with what) | Human gate and rationale | Artefact → location | Feeds |
|---|---|---|---|---|
| 1 Requirements | Deep-research and synthesis subagents expanded the brief (FFIEC/FATF/OFAC grounding, personas, 17-step flow); ongoing: a `requirements-check` subagent diffs new work against the spec and lists uncovered rules | **G-R1 Spec sign-off** (operator): the brief says refining it is assessed; a wrong assumption here multiplies through every later stage. Rationale recorded in the gates register | `docs/requirements/{spec.md, personas.md, rules.md (the 8 rules + routing R1–R6), glossary.md, research-notes.md, open-questions.md}` | Design reads `rules.md`; tests map BB-ids to `rules.md` ids |
| 2 Design | Plan subagents produced designs A/B/C; Mermaid C4 and sequence diagrams generated from the component table; ADR drafts proposed by the agent, decided by the operator | **G-D1 Architecture review**: service boundaries, event contracts and the deterministic/LLM split are the costliest things to change after code exists; the operator accepts each ADR row. **G-D2 Contract freeze** per service: OpenAPI + event schemas committed before the web binds | `docs/architecture/{c4-context.md, c4-container.md, sequences/*.md, data-model.md, event-catalogue.md, openapi/*.json, events/*.json}`, `docs/governance/decision-log.md` | Development reads contracts; `make api-gen` and `events-schema` turn them into code |
| 3 Development | Claude Code subagents per workstream in git worktrees, each with the package `CLAUDE.md`; AEO clones with `UPSTREAM.md`; prompts in `/ai/prompts` versioned | **G-C1 Commit gate** (automatic, hooks): ruff/mypy/pytest/tsc must pass; **G-C2 Review** (operator): `/code-review` on every PR-sized diff; human merges because the agent cannot judge whether a change moved a product rule. Rationale: cheap automatic checks catch mechanical faults; the human check targets rule drift and scope creep | `src/**`, `ai/{prompts,agents,skills,guidance}`, commits with D/ADR refs, `docs/governance/defect-trail.md` rows for every bug found | Testing consumes the same commits; eval gate reads prompt versions |
| 4 Testing | Subagents write tests from `rules.md` and the scene table; Playwright scripts generated from the walkthrough; evals (pydantic-evals, judge model for narrative) | **G-T1 Eval gate** (automatic): a prompt/model change cannot land if its agent's eval score drops; **G-T2 Release gate** (operator): `make gate` + `make e2e` green and the defect trail has no open P1. Rationale: the automatic gate protects against silent agent regression; the human gate decides whether residual defects are acceptable | `/tests/**`, `/evals/{datasets,results,report.html,trend.json}`, `docs/governance/defect-trail.md` | Deployment runs the same suites against the composed stack |
| 5 Deployment | Subagent authored compose, Dockerfiles, env contract, runbooks, AWS path; a `deploy-check` subagent runs `make up && make seed-verify && make e2e` on a clean clone | **G-P1 Clean-clone proof** (operator watches): "a demo that works once is not the bar"; the operator confirms the clean-clone run and signs the release ADR | `/ops/**`, `ops/aws/deployment-path.md`, release ADR, tagged commit | Operations inherits dashboards and runbooks |
| 6 Operations | Nightly `make nightly` (tests, evals, drift, token report); the examiner and watchlist agents are the product's own operations loop; a `triage` subagent reads the nightly report and opens defect rows | **G-O1 Nightly review** (operator, 10 min each morning): decides which regressions block, which prompts to revise. Rationale: automated signals are many; prioritisation is a human judgement | `ops/reports/nightly/*.md`, `ops/token-analysis/*`, `docs/governance/failure-log.md`, Grafana dashboards | Feeds Requirements (open-questions.md) and Development (defect rows) — the loop closes |

Connection in one line for the deck: brief → `spec.md` → ADRs + contracts → code with refs → tests + evals gated by hooks → clean-clone deploy proof → nightly reports → defect and failure rows → new ADRs. Each arrow is a file path in this repo.

### 5.2 `/ai` layout

```
ai/
  README.md                how the runtime agents and the build harness relate
  prompts/<agent>/v<n>.md  front-matter: id, version, tier, variables, eval_set, changelog; CHANGELOG.md
  agents/<agent>.yaml      spec: tier, model, fallback, tools, skills, output schema ref, budget, gate, eval set (mirrors fcrm_agents registry; lock test keeps them equal)
  skills/                  cite_verbatim.md declare_gap.md compare_precedent.md draft_for_business_reader.md batch_questions.md
  guidance/                build-time guidance (§5.3)
  evals-index.md           which set guards which prompt
```

### 5.3 `/ai/guidance`

- `CLAUDE.md` (root) — the file the operator already has, trimmed to decisions in force plus pointers; it tells agents to read the package file first.
- `ai/guidance/packages/<package>.CLAUDE.md` → symlinked/copied to each `src/**/CLAUDE.md`: what the package owns and never does ("S1 never scores"), its public API, test command, forbidden imports (AST test), the decision ids it implements, "when touching X also update Y".
- `ai/guidance/coding-standards.md` — Python: 3.13, uv, Pydantic 2 `extra="forbid"` DTOs, no business logic in routes, repositories only talk to Mongo, every public function typed, structured logs, no `print`; TS: strict, no `any`, no business arithmetic in the client, generated client only, tokens not hex; both: no emojis in code or docs, prose in plain language, no secrets in code.
- `ai/guidance/commit-convention.md` — §5.5.
- `ai/guidance/subagent-brief-template.md` — the prompt skeleton for every build subagent: goal, inputs to read, outputs, forbidden changes, the gate to pass (`make check` or `make gate`), the commit plan (small commits with refs), and "report: what changed, what failed, what you decided and why" so the failure log can be filled.
- `ai/guidance/worktree-protocol.md` — one worktree per stream, branch naming `ws/<stream>/<topic>`, rebase before merge, merge only after `make gate` on the merged tree.
- `ai/guidance/review-checklist.md` — the eight rules, the visibility matrix, the version pins, idempotency, audit record for every state change, error copy, a11y.

### 5.4 Claude Code hooks (`.claude/settings.json`, scripts in `ai/guidance/hooks/`) (proposed)

| Hook | Matcher | Script | Behaviour |
|---|---|---|---|
| `PreToolUse` | `Edit|Write|MultiEdit` | `protect_paths.py` | Blocks edits to `src/packages/fcrm_seed/seed.json` (regenerate via `gen_seed.py`), `src/web/src/api/generated/**`, `.env*`, `docs/governance/*.md` rows that already exist (append-only check runs on the diff), and anything under `AEO/secrets`. Exit 2 with the reason |
| `PreToolUse` | `Bash` with `git commit` | `commit_guard.py` | Requires `.cache/check.ok` newer than the newest tracked change (written by `make check`); validates the message against §5.5 (type, scope, at least one `D-xx` or `ADR-nn`, `Stage:` trailer). Blocks otherwise |
| `PreToolUse` | `Bash` with `cat|less|head .env` or `AEO/secrets` | `protect_paths.py` | Block |
| `PostToolUse` | `Edit|Write` on `*.py` | `fmt_py.sh` | `ruff format` + `ruff check --fix` on the file; `mypy` on its package in the background with the result appended to `.cache/mypy.txt` |
| `PostToolUse` | `Edit|Write` on `*.ts|*.tsx|*.css` | `fmt_ts.sh` | `oxlint --fix` on the file; `tsc -b --incremental` |
| `Stop` | — | `stop_gate.py` | Runs `make check`; on failure returns `{"decision":"block","reason":"<first 40 lines>"}` so the agent keeps fixing; on success writes `.cache/check.ok` and appends a line to `.cache/session-log.jsonl` (task, duration, files touched) for the build token report |
| `SubagentStop` | — | `subagent_report.py` | Requires the subagent's final message to contain the sections `Changed`, `Failed`, `Decided`; otherwise block with the template |
| `UserPromptSubmit` | — | `stage_context.py` | Prepends the current stage (`.cache/stage`), open defect count and the last nightly status line — cheap context the operator does not have to retype |

Hooks are the local equivalent of CI (D-90, D-92). They are committed, so the panel can read them.

### 5.5 Pre-commit, commit and PR templates

`.pre-commit-config.yaml`: ruff (format, check), mypy (per package, `--strict`), pytest-changed (`scripts/pytest_changed.py`), oxlint, tsc, `check_env.py --example`, `governance_append_only.py`, `commit-msg` hook `commit_msg_lint.py`, `detect-secrets`, `semgrep` (push stage), `oasdiff` and `events-schema --check` (push stage).

Commit message:
```
type(scope): imperative summary ≤ 72 chars [D-19, ADR-07]

Why (1–3 lines). What changed (bullets if several). Links to defect rows.

Stage: Development
Gate: G-C1 make check · G-T1 evals research 99.1 (no change)
Refs: D-19, ADR-07, DEF-012
Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```
Types: `feat fix test docs ops ai eval refactor chore seed`. Scopes: component ids (`s1-case`, `h1-caseflow`, `web`, `evals`, `ops`, `gov`).

`.github/PULL_REQUEST_TEMPLATE.md` (used as the local merge checklist even without GitHub PRs): what and why · decisions touched · rules affected (checklist of 8) · tests added (unit/contract/e2e/eval) · screenshots (web) · gate output pasted · defect rows opened/closed · token note (if a prompt changed: before/after tokens per run).

### 5.6 Decision-record format (ADR-lite, `docs/governance/decision-log.md`)

Append-only table; the BA's D-01…D-97 log is carried into `docs/architecture/decision-log-ba.md` unchanged and referenced.

```
| ADR | Date | Stage | Decision | Options considered | Rationale | Consequences | Refs | Status |
| ADR-01 | 2026-10-03 | Design | Rebuild the web app in /src/web; reuse only tokens and IA from FCRM_v2 | swap service layer (D-63); rebuild | prototype enforces rules client-side and leaks PO fields; a clean app binds to generated contracts | D-63 superseded; 2 extra build days for web | D-63, C §15 | decided |
```
Rows are never edited; a change appends a new row with `supersedes ADR-nn`. The hook enforces it. Seed rows for day 1: ADR-01 rebuild, ADR-02 monorepo (supersedes D-64), ADR-03 RabbitMQ for work + Redis for fan-out (closes synthesis #4), ADR-04 vote mapping and tie-break (C-06), ADR-05 LLM modes and replay fixtures, ADR-06 local gates instead of CI, ADR-07 monitor as separate route, ADR-08 ports.

### 5.7 Defect trail (`docs/governance/defect-trail.md`)

```
| DEF | Found | By | Stage | Symptom | Root cause | Fix (commit) | Regression test | Status |
| DEF-001 | 10-04 | contract test | Dev | PO DTO exposed `band` via nested `score` | projection missed nested model | a1b2c3d | test_po_dto_no_restricted_fields | closed |
```
"By" ∈ unit · contract · e2e · blackbox · eval · hook · review · operator · nightly. Every fix commit references its DEF; `make gate` fails if a `fix:` commit lacks a DEF ref. The brief's "defect trail" deliverable is this file plus `evals/results`.

### 5.8 Eval gate (G-T1)

Trigger: any change under `ai/prompts/**`, `ai/agents/**`, or model strings in config. The pre-commit `evals_gate_changed.py` finds the touched agents and runs `make evals-gate AGENT=<x>` (in `live` mode for that agent only, ~2 min, ≤ 20 cases sampled from the 80 for the commit gate; the full set runs nightly). Fail if score < `latest.json[agent].score − 0.5`. On pass the commit may land and the result JSON is committed with it (`eval(<agent>): …`). On fail the agent/operator either fixes the prompt or appends an ADR row accepting the regression with a reason (rare, explicit). Guardrail evals (deterministic) run in every push gate.

### 5.9 Failure handling and iteration log (`docs/governance/failure-log.md`)

```
| FL | Date | What stalled | Detected by | Recovery | Instruction/prompt change | Effect |
| FL-01 | 10-03 | Design subagent reopened the "repo shape" decision | operator review | restated decisions-in-force block in the brief template | subagent-brief-template gained "Decisions not to reopen" | no recurrence in 6 briefs |
```
Expected entries to capture during the build (the FAQ says agents "will stall, ask for input they cannot get, or run without sufficient context"): subagent asked for a human answer mid-task → brief template now requires "decide and mark proposed"; Stop hook loop on a flaky test → `pytest -p no:randomly` and a `flaky` marker policy; mypy strict on a cloned AEO package → `UPSTREAM.md` + per-module `ignore_errors` list with an ADR; a subagent edited `seed.json` by hand → protect_paths hook; a web stream blocked on a missing endpoint → MSW handler first, contract later (§7). Prompt evolution for runtime agents is recorded in `ai/prompts/<agent>/CHANGELOG.md` with the eval delta per version (e.g. research v3 → v4: "quote must be verbatim; paraphrase rejected" after the 1-of-5 Guard 4 reject became a planted-error eval). The deck's item 6 is generated from this table.

### 5.10 Build-time token analysis (Claude Code usage)

`ops/scripts/claude_usage.py` reads `~/.claude/projects/<project>/**/*.jsonl` (assistant messages carry `usage` with input, output, cache_creation and cache_read tokens, plus model) and `.cache/session-log.jsonl` from the Stop hook, and writes `ops/token-analysis/build/<date>.md`: tokens and estimated cost per day, per model, per subagent type and per stream (from branch names), cache hit ratio, tool-call counts (Read/Edit/Bash) as a proxy for exploration vs editing, the ten most expensive sessions with their task, and the optimisations applied with measured effect (package `CLAUDE.md` files to stop re-reading; subagent briefs that name the files to read; hooks that stop test-fix loops early; worktrees to avoid conflicts; `textutil` extraction of the HTML docs once into the scratchpad instead of re-parsing). The runtime report (§4.10) and this one share a front page `ops/token-analysis/latest.md` with two columns: build vs runtime. `ccusage` is an acceptable cross-check if installed; the script is the committed source.

---

## 6. `/docs` plan and README

### 6.1 `docs/requirements/`
- `spec.md` — the expanded specification: problem, personas, 6 triggers → 4 FFIEC domains, 17-step flow, 4 outcomes, routing R1–R6, scoring model v3 with the worked demo, visibility, SLA clocks, configuration ownership; every section cites its source (brief, FFIEC manual section, FATF list date, decision id).
- `personas.md` (Priya, Rahul, Ana + Okafor, Chen; what each may do and see) · `rules.md` (the eight rules as numbered RULE-1…8, the submit and release checks, each with its enforcing component and test ids) · `glossary.md` (the 42 terms) · `research-notes.md` (frameworks found, how the 40 % floor and the band thresholds were chosen, what was rejected) · `open-questions.md` (the brief's ambiguities and the decision taken for each) · `scenes.md` (the 21 scenes with the seed figures; the E2E contract) · `out-of-scope.md` (D-92 plus ADR items).

### 6.2 `docs/architecture/`
- `overview.md` (how to read this folder) · `c4-context.md` (Mermaid C4Context: three roles, the workbench, model vendors, S3 Vectors) · `c4-container.md` (Mermaid C4Container: W1, G1, S1–S5, H1–H4, X1–X7, D1–D2, P1–P7) · `c4-component-caseflow.md` (H1 internals: cores, gate manager, budget, outbox) · `sequences/` (intake-and-submit, assessment-core, review-override-acknowledge, clarification-round, pack-and-vote, question-addendum-revote, examiner, watchlist, failure-retry-fallback — Mermaid `sequenceDiagram` each) · `data-model.md` (per-database collections, key fields, indexes, pinned versions, the audit record shape and hash rule) · `event-catalogue.md` (every RabbitMQ event and job: exchange, routing key, queue, producer, consumer, payload schema link, idempotency key, retry/DLQ policy) with `events/*.json` schemas and `events/examples/` · `openapi/*.json` snapshots · `api-conventions.md` (role DTOs, errors, etags, SSE) · `deterministic-vs-llm.md` (the table the R-judgement 5 % is graded on: each step, deterministic or LLM, why) · `context-engineering.md` (the four memory kinds, what each agent receives, how the corpus is chunked and filtered, what is pinned) · `decision-log-ba.md` (D-01…D-97 carried over) · `aeo-reuse.md` (what was cloned, `UPSTREAM.md` index, the fixes made).

### 6.3 `docs/governance/`
- `gates-register.md` — every gate, product and build: id (G-R1…G-O1 for the build; Gate-PO-1 confirm types, Gate-A clauses/controls, R3 acknowledge, lock, release checks, Gate-B votes, delegated close, watchlist acknowledge, config publish for the product), who decides, what risk it controls, why it sits where it does, what happens on refusal, evidence (audit record type, test id).
- `rules.md` (the eight rules with enforcement points and BB test ids) · `roles-and-visibility.md` (matrix: field groups × roles × read/write; DTO names; SSE event types per role) · `audit-chain-spec.md` (record schema, canonical JSON, hash function, seq, verify algorithm, insert-only user, tamper test) · `decision-log.md` (ADR-lite) · `defect-trail.md` · `failure-log.md` · `eval-policy.md` (metrics per agent, thresholds, gate rule, judge model and its own eval) · `data-governance.md` (synthetic only, seed provenance, PII masking, retention).

### 6.4 `README.md` (root)
1. What TrustOps is (3 lines) and the demo case. 2. Setup: prerequisites, `make env`, `make up`, `make seed-verify`, URLs and demo users, replay vs live modes. 3. Walkthrough: the 21 scenes with screenshots from `tests/e2e/demo/artifacts`, `make demo`. 4. Repo map (the brief's folders, one line each). 5. How we built it: the harness in one diagram, hooks and gates, commit convention. 6. Decision log pointer (`docs/governance/decision-log.md`, `docs/architecture/decision-log-ba.md`). 7. Evals and results pointer. 8. Ops: `make nightly`, dashboards, runbooks, AWS path. 9. Token analysis pointer. 10. Known gaps and what we would do next.

### 6.5 Deck outline mapped to the brief's seven items
1. Problem and expanded spec — brief quote; the 17-step flow; what we decided that the brief left open (`open-questions.md`).
2. Harness — C4 container; Caseflow cores and gates; nine agents, seven guards, four memory kinds; the data foundation (P7 seed, versions pinned).
3. AI leads / person leads / gates — the gates register as one slide per persona; the rationale column.
4. Evaluation — metrics per agent, the weekly table, the eval gate, the planted-error critic set, override rate as live signal.
5. Demo — live run in replay mode with the monitor window; fallback screenshots.
6. Failure handling and iteration — `failure-log.md` highlights, prompt changelog deltas, the hooks that came out of it.
7. Tokens — runtime per case/agent/model, build-time Claude Code usage, what we optimised and by how much.

---

## 7. Parallelisation and integration checkpoints

### 7.1 Streams

| Stream | Owner (subagent, worktree) | Can start against | Produces |
|---|---|---|---|
| W-contracts | `ws/contracts` | design B | `fcrm_contracts` models, `docs/architecture/openapi/*.json` stubs (hand-written from B on day 1, replaced by FastAPI output as services land), `events/*.json`, `make api-gen`, `make events-schema` |
| W-web-shell | `ws/web/shell` | tokens + routing table (§1) | Vite app, tokens, primitives, AppShell, auth against an MSW identity handler, router, SSE hook with an MSW `EventSource` shim |
| W-web-po | `ws/web/po` | MSW handlers from seed | PO screens 2.2–2.10 |
| W-web-analyst | `ws/web/analyst` | MSW handlers from seed | Workbench 2.11–2.21, Assurance 2.22–2.23 |
| W-web-committee-config-monitor | `ws/web/committee` | MSW | 2.24–2.27 |
| W-mocks | `ws/web/mocks` | seed.json | MSW handlers per endpoint, scenario switch (`?scene=14` loads the state after the override), the demo SSE event script |
| W-tests | `ws/tests` | §3 + `scenes.md` | Playwright scaffolding, black-box suite (red until services exist), contract runner, resilience harness |
| W-ops | `ws/ops` | §4 | compose, env contract, nginx, OPA, Mongo init, otel/grafana, scripts, runbooks |
| W-sdlc | `ws/sdlc` | §5 | hooks, pre-commit, templates, governance tables, guidance files, usage scripts |
| Backend streams | per design B | — | S1–S5, H1–H4, packages |

Frontend streams work in `VITE_API_MODE=msw` until a service's OpenAPI is published; then `make api-gen` regenerates types, `tsc` shows every mismatch, and the stream switches the handler off for that service (`mocks/handlers/index.ts` has one flag per service).

### 7.2 Checkpoints (proposed; dates assume build days 3–8 Oct; 9 Oct is submission)

| CP | When | Proof | Who signs |
|---|---|---|---|
| IC-0 | 3 Oct evening | Monorepo scaffold, hooks active, first ADR rows, `make check` green on an empty tree, `make up` starts infra | operator |
| IC-1 Contracts | 4 Oct noon | OpenAPI stubs and event schemas committed; `api-gen` runs; MSW handlers typed; identity login works end to end (real X1, mocked everything else) | operator (G-D2) |
| IC-2 Intake vertical | 5 Oct | Scenes 1–7 live: web → S1 → H3 → intake agent (replay) → S4; PO stream carries no restricted events (BB-02 partial) | `make e2e --grep "S0[1-7]"` |
| IC-3 Assessment vertical | 6 Oct | Scenes 8–16: S2 golden tests, S3 search, research/assessment/critic/narrative in replay, gates, override 9.8 → 10.4, acknowledge, lock, pack v1 | `make e2e --grep "S0[8-9]|S1[0-6]"` + BB-03/06/08/09/10/13 |
| IC-4 Decision and oversight | 7 Oct | Scenes 17–21: S5 votes, reveal, outcome, conditions, examiner, watchlist, monitor; `make e2e` full green; obs stack up with three dashboards populated by the run | `make e2e` + all BB + G-T2 |
| IC-5 Hardening | 8 Oct | `make resilience` green; `make evals` committed; clean-clone `make up && make seed-verify && make e2e` on a second machine or fresh checkout; runbooks walked; token reports generated; README and deck screenshots from artifacts | operator (G-P1) |
| Freeze | 9 Oct morning | Final nightly report, release ADR, tag; no commits after the deadline | operator |

Fallbacks if a vertical slips: web streams keep MSW for the missing service and the demo runs that scene from mocks with the scenario switch, clearly labelled in the README's known gaps; the static walkthrough HTML remains the last-resort demo (D-96), never presented as the build.

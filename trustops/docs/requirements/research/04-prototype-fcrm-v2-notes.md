# D. TrustOps (FCRM_v2) prototype teardown notes

Source: `/Users/apahuja/Downloads/Hackathon/FCRM_v2/` (React 19.2, Vite 8, Tailwind 4.3, React Router 7, Recharts 3, Framer Motion 13, TypeScript 6, oxlint). No backend, no auth, no network, no tests, no git history. `README.md` is the Vite default. There is **no CLAUDE.md** in the repo (the brief said to start with it; it does not exist). `.claude/launch.json` only defines `npm run dev` on port 5173.

All data lives in an in-memory singleton `src/services/store.ts` seeded by deep-cloning `src/mocks/seed/*`; not persisted across reload "by design (§3.1)". Every screen calls only two service files, `src/services/api.ts` (PO side, ~52 functions) and `src/services/analystApi.ts` (analyst + committee + config, ~125 functions). These simulate latency (FAST 300-650 ms, NORMAL 500-950, SLOW 900-1400) and return deep clones. This is the seam: replace function bodies with HTTP calls and the UI keeps working.

The code cites a numbered spec ("§1" to "§10", "§8.x mod", "build prompt §7", "editable factors mod", "settings v2 mod prompt", "synthetic data corpus") over 300 times. That spec, and the referenced `GOLDEN_REFERENCE_FCRM-2026-0147.md`, are **not in the repo**. Section numbering inferred from comments is listed in section 12.

---

## 1. Personas, navigation and routes

Persona is a client-side toggle (`src/context/PersonaContext.tsx`), derived from the URL prefix: `/committee*` = committee, `/analyst*` = analyst, everything else = PO. No login.

| Persona | Signed-in user | Team | Other named people |
|---|---|---|---|
| Product Owner (`po`) | Arun Mehta (AM) | Payments & Cross-Border | Generated corpus adds POs Nadia Haddad, Tom Okonkwo, Ines Duarte, Wei Chen |
| FCRM Analyst (`analyst`) | Priya Raman (PR) = `CURRENT_ANALYST` | Financial Crimes Risk Management | Daniel Okafor, Maya Fernandes (`ANALYSTS`) |
| Risk Committee (`committee`) | Ravi Menon (RM) = `CURRENT_COMMITTEE_MEMBER` | Risk Committee | Sanjay Iyer, Helen Whitcombe (`COMMITTEE_MEMBERS`, exactly 3) |

Routes (`src/App.tsx`, all inside `AppLayout`):

| Route | Component | Persona | Purpose |
|---|---|---|---|
| `/` | `Dashboard` | PO | KPIs + 5 charts with AI insight lines |
| `/queue` | `Queue` | PO | "My Queue", scoped to signed-in PO |
| `/case/:id` | `CaseDetailPage` | PO | Overview, Inbox, AI Assistance, Activity tabs |
| `/case/:id/intake` | `IntakeChat` | PO | Conversational intake (screening or full intake) |
| `/templates` | `Templates` | PO | View core set + category packs (read-only), manage PO-owned optional fields |
| `/integrations` | `Integrations` | PO | Connect SharePoint, Teams, Slack, Data Catalog MCP |
| `/ai-configuration` | `AIConfiguration` | PO | Model + effort per intake agent (flagged by teardown as a governance clash) |
| `/settings/notifications` | `NotificationSettings` | PO | Channels and 3 event preferences |
| `/analyst` | `AnalystDashboard` | Analyst | KPIs, caseload, calibration and AI-quality charts |
| `/analyst/queue` | `AnalystQueue` | Analyst | Mine / all, status, category, band filters |
| `/analyst/case/:id` | `CaseWorkbenchPage` | Analyst | 8 tabs: Summary, Submission, Inbox, Policy Mapping, Risk & Controls, Recommendation, Handoff, Activity Log |
| `/analyst/assurance` | `AssurancePage` | Analyst | Cross-case audit query (structured + NL) |
| `/analyst/config` | `ConfigPage` | Analyst | 8 versioned sections |
| `/committee` | `CommitteeQueue` | Committee | Cases at Committee Review or closed by committee |
| `/committee/case/:id` | `CommitteeCasePage` | Committee | Pack, Inbox (queries to analyst), Vote panel |

Teardown counts: 15 screens (PO 8, analyst 5, committee 2), 22 tabs/sections (Case Detail 4, Workbench 8, Committee 2, Config 8), ~27 pop-ups/drawers. Screen-level detail is in section 13.

---

## 2. Domain model (condensed TypeScript)

Types are split on purpose: `src/types/index.ts` is PO-visible; `src/types/analyst.ts` is analyst-only. "The PO's CaseDetail never carries analyst-only fields, so asymmetric visibility holds architecturally, not just by UI filtering." In the new build this split should become two API response schemas (PO view vs analyst view), enforced server-side.

### 2.1 Enumerations

```ts
type TriggerCategory = string;               // FCRM-owned, analyst-creatable; 6 shipped:
ALL_CATEGORIES = ['New Product','New Feature','Process Change',
  'Vendor / Third-Party Onboarding','New Geography','New Customer Segment'];

type CaseStatus = 'Draft'|'Submitted'|'Under Assessment'|'Committee Review'|'Approved'|'Rejected'|'Closed';
// NB: 'Approved'/'Rejected' exist in the union but are never set as a status. Closed cases carry
// outcome: 'Approved'|'Rejected'. Brief also wants 'Deferred' and 'Approved with conditions' (missing).

type AmlCategory = 'Customer / entity'|'Product and service'|'Geography'|'Delivery channel and third party'; // FFIEC
TRIGGER_TO_AML = { 'New Product':'Product and service', 'New Feature':'Product and service',
  'Process Change':'Delivery channel and third party', 'Vendor / Third-Party Onboarding':'Delivery channel and third party',
  'New Geography':'Geography', 'New Customer Segment':'Customer / entity' };

type Band = 1|2|3|4;
type FieldState = 'Extracted'|'Needs confirmation'|'Missing';
type FieldType = 'text'|'textarea'|'number'|'date'|'documents'|'multiselect';
type FieldValue.source = 'extracted'|'manual'|'clone';
type ValueProvenance = 'submission'|'jurisdiction-list'|'jurisdiction-list-override'|'control-inventory'|'analyst-override';
type ClarificationThread.status = 'awaiting-response'|'answered'|'resolved';
type raisedFromStage = 'clarify'|'mapping'|'risk-controls';
type ClarificationType = 'Missing required field'|'Insufficient detail'|'Inconsistent figures'|'Missing supporting document'|'Scope or category question';
type EvidenceType = 'Independent audit'|'SOC 2 / external attestation'|'Internal testing'|'Policy or procedure document'|'System configuration'|'Regulatory exam finding'|'None';
type ControlStalenessState = 'current'|'nearing'|'stale'|'judgment-only';
type JurisdictionStalenessState = 'current'|'nearing'|'stale';
type ControlSource = 'GRC import'|'Analyst-added'|'Seed';
type CaseControl.status = 'proposed'|'confirmed'|'overridden';  source = 'inventory'|'analyst-added';
type PolicyMapping.status = 'proposed'|'accepted'|'removed';     addedBy = 'ai'|'analyst';
type AnalysisRunStage.id = 'policy-mapping'|'risk-drafting'|'control-matching'|'scoring'|'precedent'|'recommendation-draft';
type stage status = 'queued'|'running'|'done';
type Recommendation.decision = 'Approve'|'Reject'|null;
type handoff.kind = 'delegated'|'committee'|null;
type CommitteeVote.decision = 'Approve'|'Reject';    // no abstain
type CommitteeQuery.status = 'awaiting-response'|'answered';
type AgentName (PO side) = 'Screening'|'Intake'|'Extraction'|'Classification'|'Submission Validation';
type AnalystAgentName = 'Policy Mapping'|'Risk Drafting'|'Control Matching'|'Precedent'|'Clarification';
type effort = 'Low'|'Standard'|'High';
type ModelInfo.relativeSpeed = 'Fast'|'Balanced'|'Thorough'; relativeCost = '$'|'$$'|'$$$';
type Integration.category = 'Document sources'|'Communication apps'|'MCP servers / apps';
type NotificationEvent = 'clarification_requested'|'decision_reached'|'sla_approaching'; frequency = 'Immediate'|'Daily digest';
type AutoTriage.strategy = 'round-robin'|'least-active-cases'|'manual';
type AutoClearFieldKey = 'newVendor'|'newGeography'|'newProductCapability'|'customerFacingChange'|'customerCount'
  |'dataRetentionOrCrossBorder'|'existingAssessedVendorRenewal'|'vendorScopeChange'|'cosmeticOrUiOnly'|'newDataHandling';
type AutoClearCondition.operator = 'is'|'isNot'|'lessThan'|'greaterThanOrEqual';
type ClassificationSignal.kind = 'signal'|'disambiguation';
type DashboardTimePreset = 'this-month'|'last-3-months'|'custom';
type Persona = 'po'|'analyst'|'committee';
```

### 2.2 Case (PO-visible aggregate)

```ts
interface CaseListItem {
  id: string;                 // 'FCRM-2026-0147'; minted FCRM-2026-0{n}, n starts at 147
  title: string;              // 'Draft - 02 Oct 2026' until title_generated
  categories: TriggerCategory[];
  status: CaseStatus; analyst: string|null;
  submittedAt, closedAt: string|null; cycleDays: number|null;
  clarificationRounds: number;
  completionPct: number; completionPctAtSubmission?: number;
  slaTargetDate: string|null; createdAt: string;
  outcome?: 'Approved'|'Rejected';
  openClarificationCount?: number;   // derived: threads awaiting-response
  poOwner?: string;                  // corpus only
}
interface CaseDetail extends CaseListItem {
  fields: FieldValue[]; optionalFields: OptionalFieldValue[]; documents: Document[];
  clarifications: ClarificationThread[]; activityLog: ActivityEvent[]; chatTranscript: ChatMessage[];
  confidenceIndicator: number /*0-100*/; modelVersion: string; promptVersion: string;
  decisionRationale?: string;   // committee/examiner register (overall narrative)
  decisionSummary?: string;     // PO-facing, business register: no residual, band, CE or clause
  threshold: number; requiredFieldCount: number; requiredFieldsFilled: number;
  originalAiCategories?: TriggerCategory[]; categoryOverrideReason?: string;
  submissionVersions?: SubmissionVersionSnapshot[];
  analystAddedCategories?: {category, reason, timestamp, addedBy}[];
  closedViaDelegatedAuthority?: boolean;
  // version pins (non-retroactive config)
  fieldSetVersion?, classificationRulesVersion?, controlInventoryVersion?,
  policyCorpusVersion?, routingRulesVersion?, jurisdictionListVersion?: number;
}
interface FieldValue { fieldId; label; value: string|null; state: FieldState;
  source?: 'extracted'|'manual'|'clone'; clonedFromCaseId?; humanOverridden?: boolean }
interface OptionalFieldValue { fieldId; label; value }
interface Document { id; name; source: 'uploaded'|'integration'; integrationName?; version: number;
  uploadedAt; sizeKb; raisedInRound?: number }
interface SubmissionVersionSnapshot { version; capturedAt; fieldValues: Record<string,string|null>;
  changedFieldIds: string[]; raisedInRound?; changedBy?: 'po'|'analyst'; reason? }
interface ClarificationThread {
  id; fieldId?; fieldIds?: string[] /*multi-field*/; fieldLabel?;
  notFieldAnchoredReason?;     // never alongside fieldId/fieldIds; a "template gap" finding
  status; messages: {id; author:'analyst'|'po'; authorName; text; timestamp}[];
  roundNumber?; raisedFromStage?; updateForm?: boolean; resultingVersion?: number;
  clarificationType?: ClarificationType;
}
interface ActivityEvent { id; timestamp; type; summary; detail?; modelVersion?; promptVersion?;
  actor: 'ai'|'po'|'analyst'|'system' }
interface ChatMessage { id; role:'ai'|'po'; text; timestamp; replyChips?: string[];
  kind?: 'text'|'extraction'|'classification'|'clone-suggestion'|'validation'|'note';
  attachments?: {name,sizeKb}[] }
```

### 2.3 Field set (FCRM-owned, versioned)

```ts
interface RequiredFieldDef { id /*immutable*/; label; category: TriggerCategory|'Common';
  type?; helpText?; extractionHint?; neverBlank?: boolean; status?: 'active'|'retired' }
interface TriggerCategoryDef { key; description /*PO-facing*/; amlCategory;
  classificationSignals: string[]; status: 'active'|'retired' }
interface FieldSetSnapshot { version; publishedAt; note; changedBy;
  fieldLibrary: RequiredFieldDef[]; coreSetKeys: string[];
  categoryPackKeys: Record<TriggerCategory,string[]>; completionThreshold: number;
  triggerToAmlCategory: Record<TriggerCategory,string>; triggerCategories: TriggerCategoryDef[] }
interface Template { category; version; requiredFields; optionalFields: OptionalFieldDef[];
  completionThreshold; aiInsight: string|null; history }        // PO-facing legacy view
interface OptionalFieldDef { id; label; order; type }            // PO-owned, no approval
```

A new trigger category needs four dependencies (§3.2): PO-facing description, field pack, AML mapping, at least one classification signal.

### 2.4 Analyst case data (analyst-only aggregate, keyed by caseId)

```ts
interface AnalystCaseData {
  caseId; analystAddedCategories;
  scoring: ScoringResult|null; scoringHistory: ScoringResult[];
  controls: CaseControl[]; plannedControls: PlannedControl[];
  policyMappings: PolicyMapping[]; analysisRuns: AnalysisRun[];
  recommendation: { narratives: RecommendationNarrative[]; decision: 'Approve'|'Reject'|null;
    materialClarifications: {round; summary}[]; decisionSummary?; locked?: boolean;
    unlockLog?: {reason; timestamp}[] };
  handoff: { kind: 'delegated'|'committee'|null; packApprovedAt?; analystNote?;
    editsLog: {field; before; after; timestamp}[] };
  committeeVotes?: CommitteeVote[]; committeeQueries?: CommitteeQuery[];
  confirmedComponentIds: string[];          // per-item confirm tracking (controls + factors)
  activityLog: {id; timestamp; type: string; summary; detail?; actor:'ai'|'analyst'|'system'; modelVersion?}[];
  seedOutcome?: {residual; band; route; outcome?};
  flaggedValues?: FlaggedValue[];
}
interface CategoryScore { category: AmlCategory; likelihood; impact;
  likelihoodProvenance; impactProvenance: ValueProvenance;
  inherent; controlEffectiveness; retention; residual; weight;
  likelihoodBaselineDetail?;        // e.g. "Philippines rated 3 (highest-risk in-scope jurisdiction)"
  likelihoodOverrideReason?; impactOverrideReason?;
  likelihoodProposed?; likelihoodProposedProvenance?; impactProposed?; impactProposedProvenance? } // captured on first edit, used by revert
interface ScoringResult { modelVersion; categories: CategoryScore[]; compositeInherent; compositeResidual;
  band: Band; route: string; boundaryFlag: BoundaryFlag|null; boundaryAcknowledged: boolean; computedAt }
interface BoundaryFlag { distance; currentBand; otherBand; otherRoute; direction: 'above'|'below' }
interface CaseControl { id; controlId /*inventory*/; name; category: AmlCategory; weight;
  effectivenessAI: number /*0-10*/; effectivenessConfirmed: number|null;
  evidence: EvidenceRef|null; judgmentOnly: boolean; source; status; overrideReason?;
  overrideHistory?: {from; to; reason; timestamp}[] }
interface EvidenceRef { name; type; date; recency: 'current'|'stale' }
interface PlannedControl { id; description; owner; targetDate; addressesCategory: AmlCategory;
  hypotheticalResidual: number; addedAt }
interface PolicyMapping { id; category; clauseText /*verbatim, frozen on the case*/; sourceDoc; section;
  relevanceNote; status; removalReason?; addedBy; addedVia?; clauseId? }
interface AnalysisRun { id; startedAt; completedAt?; stages: AnalysisRunStage[]; rerunOf?;
  rerunReason?; discardedSummary?; agentConfigVersion? }
interface AnalysisRunStage { id; label; status; startedAt?; durationMs?; output?; deterministic? }
interface RecommendationNarrative { category: AmlCategory|'overall'; aiText; editedText; edited }
interface FlaggedValue { id; fieldId?; label; reason; raisedAt; resolved; resolvedAt?;
  resolvedVia?: 'field-change'|'dismissed'; resolvedRound?; dismissReason? }
interface CommitteeVote { id; member; decision; rationale /*mandatory*/; castAt }   // immutable
interface CommitteeQuery { id; messages: {id; author:'committee'|'analyst'; authorName; text; timestamp}[];
  status; raisedAt; unreadReply? }
interface DraftedQuestion { id; text; updateForm; fieldId?; fieldIds?; notAnchoredReason? }
```

### 2.5 Config entities (all versioned snapshots: version, publishedAt, note, changedBy)

```ts
interface ScoringModelVersion { version: 'v2.3'; effectiveDate; status: 'active'|'prior';
  likelihoodAnchors: AnchorLevel[5]; impactAnchors: AnchorLevel[5];
  categoryWeights: Record<AmlCategory,number>; bands: BandDef[4];
  retentionFloorPct: 40 /*fixed, non-configurable*/; changeNote?; changedBy? }
interface BandDef { band; min; max; route; routeShort }
interface AnchorLevel { level: 1..5; label; definition }
interface ControlInventoryItem { id; name; category: AmlCategory; weight; effectiveness /*0-10*/;
  evidence: {name; type: EvidenceType; link|null; date|null}; source: ControlSource;
  status: 'active'|'retired'; addedBy?; addedAt?; addedFromCaseId? }
interface PolicySource { id; name; origin: 'internal'|'external'; version; effectiveDate; lastUpdated; status: 'active'|'retired' }
interface PolicyClauseCorpusEntry { id; sourceId; clauseText; sourceDoc; section;
  amlCategories: AmlCategory[]; triggerTags: string[]; origin; retired; retiredAt?; supersededBy?; supersedes? }
interface JurisdictionRiskEntry { id; jurisdiction; riskValue /*1-5*/; source; sourceType: 'external'|'internal';
  publisher?; updateFrequency?; lastReviewed; rationale?; retired?; retiredAt? }
interface JurisdictionRiskListSnapshot { ...; entries; defaultUnlistedValue: 3; internalReviewIntervalMonths: 12 }
interface RoutingRules { boundaryProximityThreshold: 0.5; evidenceRecencyMonths: 18; nearingStalenessMonths: 3;
  slaTargetBusinessDays: 15; autoTriageRule: { strategy; lookbackDays; band34WeightingFactor } }
interface ClassificationRulesSnapshot { ...; signals: ClassificationSignal[];
  autoClearRules: ScreeningAutoClearRule[]; triggerThresholds: Record<string,number> }
interface ClassificationSignal { id; triggerCategory; kind; text; weight: number|null /*0-1, null for disambiguation*/;
  active; confusedWith?; recentFireCount }
interface ScreeningAutoClearRule { id; name; conditions: AutoClearCondition[] /*AND*/; active; recentClearCount }
interface AnalystAgentConfigSnapshot { ...; configs: {agent: AnalystAgentName; modelId; effort}[] }  // team-global
interface AgentConfig { agent: AgentName; modelId; effort }   // PO intake agents, NOT versioned
```

### 2.6 Other

`Integration {id, name, category, connected, enabled (pause without disconnect), description, connectedAt?, scope?: {location, sites[], documentCount, workspace, destination, serverName, serverUrl, tools[]}, lastTest?: {ok, detail, testedAt}}`. No secrets are ever collected. `Notification {id, caseId|null, title, body, timestamp, read, type}`. `NotificationPreferences {channels: string[] (email always), events: {event, label, enabled, frequency}[]}`. `ValidationResult {passed, steps[{id,label,passed,detail}], completionPct, threshold, missingFieldLabels, missingNeverBlankLabels}`. `ClassificationProposal {category, reason}`. `CloneSuggestion {caseId, summary, fields[{fieldId,label,value}]}` (deliberately no outcome or status). `AssuranceQuery` / `AssuranceResultRow` (section 9). Dashboard DTOs in section 10.

Teardown's "about 15 core tables": Case, Submission Version, Field Value, Document, Clarification Thread, Policy Clause, Control, Case Control, Scoring Model Version, Jurisdiction Entry, Routing Rules, Agent Config, Activity Event, Committee Vote, Committee Query.

---

## 3. Required fields: field library, core set, category packs

27 field definitions (`src/mocks/seed/requiredFields.ts`). Keys are immutable once created. Pack membership is by key reference; a key may belong to several packs.

**Core set (9, required on every case).** `*` = neverBlank (always required regardless of %).
- `business_description`* Business description & rationale (textarea)
- `trigger_categories`* Trigger categories (multiselect)
- `target_customer_segments` Target customer segment(s)
- `expected_annual_volume` Expected annual volume
- `expected_annual_revenue` Expected annual revenue ("we won't estimate this one for you")
- `related_prior_assessments` Related prior products or assessments
- `supporting_documents` Supporting documents (documents)
- `target_launch_date` Target launch date (date)
- `business_sponsor`* Business sponsor

**Category packs.**
- Vendor / Third-Party Onboarding (5): `vendor_legal_name_jurisdiction`*, `vendor_role`, `vendor_data_access_scope`, `vendor_data_retention_scope`*, `process_data_flow` (shared)
- New Geography (1): `geographies_in_scope`* (feeds jurisdiction baseline)
- New Product (3): `product_structure_pricing`, `product_kyc_process`, `product_transaction_limits`
- New Feature (3): `feature_scope_change`, `impacted_existing_products`, `feature_control_changes`
- Process Change (4): `process_current_state`, `process_changed_state`, `systems_controls_impacted`, `process_data_flow` (shared)
- New Customer Segment (3): `segment_definition`, `segment_onboarding_path`, `segment_expected_risk_profile`

**Resolution (§3.4):** required set = core set ∪ packs of every confirmed category, deduplicated by key, resolved against the case's pinned `fieldSetVersion`. Demo case Vendor + Geography = 9 + 5 + 1 = 15 fields; adding New Product makes 18.

**Completion threshold (§3.5):** one global threshold, 80% in field-set v3 (history v1 72%, v2 76%, v3 80%). Gate = `completionPct >= threshold` AND every in-scope neverBlank field filled. Completion % = round(filled required fields / required field count × 100). Unweighted.

**Version pinning (§3.6):** a draft finishes on the field-set version it started on; edits never move an in-flight case; submitted cases are never re-resolved (`affectedSubmittedCases` is always 0 in the impact preview).

PO-facing per-category descriptions (`CATEGORY_DESCRIPTIONS`, shown on "Browse all categories"):
- New Product: offered for the first time, incl. pilots and limited launches.
- New Feature: material change or addition to an already-assessed product.
- Process Change: change to how an existing product is operated, delivered or serviced.
- Vendor / Third-Party Onboarding: external party handling customer data, funds, or a regulated step.
- New Geography: market, corridor or jurisdiction not previously served.
- New Customer Segment: customer type not served, or existing type through a new channel.

Templates (`templates.ts`): PO-side view per category with version, required fields (core + pack), PO-owned optional fields (Vendor: Cost-centre tag, Vendor contract reference #; New Product: Project code), threshold, `aiInsight` (e.g. "Vendor data-retention scope needed a clarification round on all three of your vendor-onboarding cases...") and pack history.

---

## 4. Case lifecycle

### 4.1 States and transitions (as implemented)

| From | To | Trigger | Actor | Gate / function |
|---|---|---|---|---|
| (none) | Draft | PO starts a case; ID minted | PO | `createDraftCase` |
| Draft | Closed (auto-cleared) | Screening path ends in auto-clear; PO approves | AI proposes, PO approves | `approveAutoClear`; title "Screening - ... (auto-cleared)", `auto_cleared` event, no outcome |
| Draft | Draft | Validation fails; intake reopens for missing fields only | AI | `runValidation` fails, `reopenIntake` |
| Draft | (deleted) | PO discards draft (Queue row action, Draft only) | PO | `discardDraftCase` |
| Draft | Submitted | PO submits after validation passes | PO | `runValidation` passed (completion ≥ threshold AND neverBlank filled); `submitCase` snapshots v1, sets SLA date, assigns analyst |
| Submitted | Under Assessment | Auto-triage | system | `submitCase` setTimeout 1.2 s; analyst queue never shows Submitted ("auto-triage runs synchronously on submission") |
| Under Assessment | Under Assessment | Clarification rounds, re-tag, analysis runs, reruns | analyst / PO | new submission versions |
| Under Assessment | Closed (delegated) | Band 1-2 and Approve; pack + decision summary in place; analyst signs off | analyst | `signOffDelegatedClose`: outcome from decision, `closedViaDelegatedAuthority`, copies `decisionRationale` (overall narrative) and `decisionSummary` |
| Under Assessment | Committee Review | Band 3-4, or any Reject; pack approved | analyst | `approveHandoffPack` (blocked without decisionSummary) |
| Committee Review | Closed (Approved/Rejected) | Third vote lands; majority | committee/system | `castCommitteeVote` |

Teardown's state diagram shows Approved/Rejected as terminal states, but the code closes to status `Closed` with `outcome`. Missing from code: Deferred, Approved with conditions (the committee rationales talk about "follow-up condition" but there is no field for conditions).

### 4.2 Write access (§8)

`getCaseAccess`: read open to every analyst; write requires assigned analyst AND status in {Under Assessment, Committee Review}. Read-only banner reasons: "assigned to X and is closed", "assigned to X", "This case is closed." Cross-analyst views are logged (`case_viewed`), owner views are not. Committee: only write action is one vote on a case at Committee Review; visibility = Committee Review, or Closed with `handoff.kind === 'committee'` (delegated closes are never visible to committee).

### 4.3 SLA

- `slaTargetDate` = submission date + `slaTargetBusinessDays` (15) business days, counting the submission day as day 1.
- Total elapsed = business days submittedAt → closedAt (or now). Paused = sum over threads of every "waiting on PO" span (analyst message → next PO message; an open wait runs to now). Active = total − paused. One function (`getSlaBreakdown`, `threadTurnaroundBusinessDays`) feeds queue, dashboard and header.
- "Approaching SLA breach" = active case with `slaTarget − elapsedActive ≤ 3` business days.

---

## 5. Intake flow (PO)

Screen-level detail of `IntakeChat` is in section 13. Logic in `api.ts`:

1. **Entry choice** (`recordPathChoice`, event `path_chosen`): opening AI message "Let's start with what you already have - upload documents or describe the change. Or, if you're not sure this needs FCRM review, I can ask a few quick questions instead."
2. **Path A, Screening** (`getScreeningTurn`, script `screeningScript.ts`): 3 scripted exchanges with reply chips ("It's an internal UI/copy change", "New vendor or partner", ... then "No data or access change", ... then "Internal staff only"/"Customer-facing"), then `screeningOutcome: 'auto-clear'` with a summary explaining why no trigger category is met. Outcomes: `'auto-clear' | 'needs-case' | null`. `approveAutoClear` closes the case as a light-touch record. In the real build, auto-clear should be evaluated against the versioned `ScreeningAutoClearRule`s (section 8.2), which the prototype stores but never evaluates.
3. **Path B, Intake:** upload (`uploadFiles` or `importIntegrationDocuments` from a connected SharePoint) → `extractFields` (Extraction Agent; demo returns 8 fields; generic cases get 3) and `classifyCategories` (Classification Agent; proposals with reasons; sets `originalAiCategories`; previews the resolved field set so the checklist reads as a to-do list) run in parallel.
4. **Category confirmation:** `confirmCategories` (writes `trigger_categories` field, re-syncs fields so every required field shows, Missing included); `removeCategoryTag` of an AI-proposed tag **requires a reason** (`tag_override` event records original AI set and confirmed set); `addCategoryTag` (PO-added, no reason, own `tag_added` event); `removeAddedCategoryTag` (undo own pick, no event).
5. **Clone suggestions** (`getCloneSuggestions`, Precedent-like, PO side): related prior cases with ID + one-line summary only (no outcome/status, by design); expand modal lists field values with checkboxes, fields already filled render greyed; `reuseCloneFields` sets `source: 'clone'`, `clonedFromCaseId`, event `clone_reuse`.
6. **Intake questions** (Intake Agent): asks only about missing fields, in rounds, each question with its own inline answer box (`applyFieldUpdates`, `field_confirmed`). Generic free text gets "Noted - thanks. Anything else you can add, or would you like to try submitting?" with chips. `editFieldValue` marks `humanOverridden: true` ("human override logged"). Optional fields via `setOptionalField`.
7. **Validation** (`runValidation`, Submission Validation Agent): steps v1 required fields "x of y", v2 file parsing, v3 completeness threshold ("53% complete - the field set requires 80%. Missing required fields: ..."), v3b always-required fields ("Still blank, independent of the completion percentage: ..."), v4 category-field consistency (always passes in mock). Logs `validation_passed` or `validation_failed` naming the failed grounds.
8. **Failure path:** `reopenIntake` → "Intake chat reopened - Intake Agent resumes for missing fields only" (scripted `RESUME_FIELD_UPDATES` and a due-diligence questionnaire upload exist but are not on the demo path).
9. **Submit** (`submitCase`): snapshot v1, status Submitted, `submittedAt`, SLA date, analyst assignment (demo → Priya; otherwise random of 3 — a stand-in for auto-triage), events `fields_completed`, `submitted`, `assigned`; 1.2 s later `status_changed` to Under Assessment.
10. **Title generation** (`title_generated` event type exists): draft title "Draft - dd Mon yyyy" replaced by generated title (demo "Cross-border payout partner (Philippines)").

Demo intake numbers (script comments): 8 extracted of 15 resolved = 53%; categories confirmed 9/15; round 1 (business sponsor, vendor legal name) → 11/15 = 73%; round 2 (vendor data retention) → 12/15 = 80%, exactly the threshold.

---

## 6. Analyst flow

### 6.1 Summary / completeness check
`getCaseSummary`: AI case summary (demo text) or the business description; flagged values = persistent `FlaggedValue`s (dismissible with reason; auto-resolved when a clarification changes that field, recorded with the round) + an ephemeral "Completion below threshold" flag computed live (not dismissible); similar cases (Precedent) with residual, band and outcome (analyst-only); complexity computed live: categories count, jurisdictions count (parsed from `geographies_in_scope`), vendor present, volume band from `expected_annual_volume` ($: <10M Low, <50M Moderate, <200M High, else Very high).

Demo flag: "Expected annual volume - The submission describes both bank-account and e-wallet payout but gives no split. E-wallet payout carries different delivery-channel exposure..."

The new requirement D-16 (completeness check before review, one round of questions, or proceed) maps onto this tab + the Clarification drafting.

### 6.2 Re-tag (§7.2)
`analystAddCategory(caseId, category, reason)`: reason mandatory; adds category, re-syncs fields (new pack fields appear Missing), recomputes completion (demo drops below 80%, which blocks the analysis run), records `analystAddedCategories`, writes `trigger_categories` back as a new submission version attributed to the analyst with the reason ("Re-tag: added "New Product" - ..."), `tag_override` event. Demo reason: "A consumer remittance product is a new product and service exposure in its own right, distinct from the vendor relationship and the corridor."

### 6.3 Clarification rounds (§6.3, §7.3, §7.5, §8.3 mod, §8.4 mod)
- `draftClarificationQuestions(caseId, stage)` (Clarification Agent): drafts only from gaps detected since the last round. Each question: text, `updateForm` (answer lands in the submission as a new version), and an anchor: single `fieldId`, multi `fieldIds`, or `notAnchoredReason` (a template-gap finding, e.g. "No KYC-refresh-cadence field exists in this resolved set."). Generic fallback: first 3 missing fields "Please provide: <label>."
- `sendClarificationRound`: round number = max+1; anchors validated against the case's actual fields (never trusted, never back-filled from text); unanchored questions default reason "No matching field in this case's resolved set."; status `awaiting-response`; `clarificationRounds` updated; PO-side `clarification_requested` and analyst `clarification_round` events.
- PO answers (`poRespondToClarification`): thread → `answered` (NOT resolved); if `updateForm && fieldId`, writes the field, recomputes completion and pushes a submission version. **One version per round**, not per field or thread; answers in the same round fold into that round's version. Every field update resolves matching flags.
- PO "Edit with AI" (`poEditFieldWithAI`, scoped Q&A on one field, drafts a value) and proactive draft (`proposeFieldDraftFromDocuments`, returns null when nothing supports a draft: "an empty box is honest and a fabricated proposal is not"); PO must confirm (`poConfirmFieldEdit`).
- Analyst resolves (`analystResolveClarification`, only from `answered`) or sends a follow-up on the same thread (`sendClarificationFollowUp`, back to `awaiting-response`, SLA pause resumes).
- Round turnaround per thread = business days waiting on PO.

Demo round 1 (from clarify): q1 multi-field (3 New Product fields, updateForm), q2 volume split (updateForm, `expected_annual_volume`), q3 KYC refresh cadence (not anchored), q4 beneficiary validation timing (not anchored). Round 2 (from risk-controls): q5 vendor audit date and scope (not anchored: "Vendor audit history has no home field on this template."). Answer: audit Q3 2024, predates corridor, domestic scope only.

### 6.4 Analysis run (§6.10, §7.4)
`startAnalysisRun` returns null if `completionPct < threshold` (backend half of the disabled button; same gate as PO submission). Stamps `agentConfigVersion`. Sequential stages with simulated durations:

| # | Stage id | Label | ms | Deterministic | Side effect |
|---|---|---|---|---|---|
| 1 | policy-mapping | Policy Mapping Agent | 3200 | no | seed policy mappings (proposed) |
| 2 | risk-drafting | Risk Drafting Agent | 2600 | no | (factor values used at scoring) |
| 3 | control-matching | Control Matching Agent | 2800 | no | seed control proposals |
| 4 | scoring | Scoring computation | 900 | **yes** | `runScoringForCase` |
| 5 | precedent | Precedent Agent | 2200 | no | similar cases |
| 6 | recommendation-draft | Recommendation draft | 2600 | no | seed narratives |

Each completion logs `stage_complete` (actor ai, or system for scoring). Rerun (`rerunStage(caseId, reason)`): one button reruns everything, reason required; preview message "This will discard N control CE overrides, your edits to N rationale narratives, and N policy clause removals."; clears mappings, controls and narratives; `rerunOf`, `rerunReason`, `discardedSummary`; always uses the agent-config version active now.

### 6.5 Policy mapping (§6.4, §10.5)
- Proposals are verbatim corpus clauses with source doc, section, relevance note; case keeps the wording it relied on (`clauseText` frozen) and links `clauseId`. If the corpus clause is later retired/superseded, the view flags `corpusRetired` and "superseded by <doc section>" without changing the case.
- Accept (`acceptPolicyClause`), remove with reason (`removePolicyClause`), add from search (`addPolicyClause`, `addedBy: 'analyst'`, `addedVia` e.g. "cited on similar prior cases - FCRM-2026-0126").
- Search (`searchPolicyCorpus`): scoped to in-scope AML categories or trigger tags, optional "widen to full corpus", substring match on text/doc/section, excludes retired; rails "Cited on similar cases", "Most cited for this category", "Recently used by you".
- Demo: 6 proposals; the analyst removes the FFIEC correspondent banking clause ("Pinnacle is a payout agent rather than a correspondent institution - this clause does not apply.") and adds pc-16 Sanctions Screening Standard 5.3 §1.1 via search "sanctions screening".

### 6.6 Risk drafting (factors) and jurisdiction baseline (§10.7)
- In-scope AML categories = distinct `TRIGGER_TO_AML` of the case's trigger categories.
- Geography likelihood baseline = highest `riskValue` among in-scope jurisdictions parsed from `geographies_in_scope` (split on commas and "and", parentheticals stripped, case-insensitive exact match, retired ignored); unlisted names use `defaultUnlistedValue` (3) and are flagged on the tab ("never a silent default"). Impact is never taken from geography.
- Provenance: Geography likelihood = `jurisdiction-list` (or `jurisdiction-list-override` if the value differs, with reason); other factors `submission`; analyst overrides `analyst-override`.
- Override (`overrideLikelihood`/`overrideImpact`): reason required; first edit captures `*Proposed` + provenance for revert; logs before/after/reason; band change logged as `band_changed` with composite before→after. Revert is itself logged; the original override log entry stays.
- Non-demo default factors: L3 × I3, Geography L = baseline, I3.
- Demo: Geography L4 I4 (baseline Philippines 3, raised to 4: "Payout is roughly 70 percent to e-wallets, a cash-out channel the listed rating does not capture."), Delivery channel L4 I5, Product L3 I4.

### 6.7 Control matching (§6.5, §10.4)
- Agent proposes every applicable inventory control per in-scope category, at inventory weight and inventory CE (`effectivenessAI`), evidence converted with recency vs `evidenceRecencyMonths` (18); `type: 'None'` → `judgmentOnly`, evidence null.
- Proposed controls count in scoring immediately (uses `effectivenessConfirmed ?? effectivenessAI`).
- Confirm (`confirmControl`, adds to `confirmedComponentIds`), override with reason (`overrideControl`, history kept, returns before/after/bandChanged for the band-change modal), add existing inventory control (`addExistingControlToCase`, weight 0.5, confirmed), propose a brand-new control (`proposeAndAddNewControl`: joins the active inventory immediately as a new inventory version, "no approval queue", source Analyst-added, `addedFromCaseId`), planned control (`addPlannedControl`: description, owner, target date, category; `hypotheticalResidual` = inherent × retention(min(10, CE+1.5)); not used in the real score).
- Demo override: ctl-dc-1 Vendor due-diligence assessment 7.6 → 4.5, reason "Supporting evidence predates the corridor and its scope excludes the payout arrangement being assessed." Band 2 → Band 3. Planned control: "Extend the existing card-monitoring velocity rules to the remittance rail.", Payments Engineering, 2026-Q4.
- Boundary flag must be acknowledged (`acknowledgeBoundaryFlag`, event `boundary_acknowledged`).

### 6.8 Recommendation (§6.6, §8.5 mod)
- Narratives per in-scope AML category + `overall`; AI text kept, `editedText` + `edited` flag; edits logged.
- Decision Approve/Reject is confirmed through a consequence modal and **locked** in one step (`setRecommendationDecision`); returns `routedToCommittee = decision === 'Reject' ? true : band >= 3` ("rejection routes to committee regardless of band"). Changing it needs `unlockRecommendationDecision(reason)`, which also clears handoff kind and pack approval (pack must be regenerated).
- Material clarifications: list of {round, summary} for the pack.
- PO-facing decision summary: drafted at first Handoff visit (`ensureDecisionSummaryDrafted`, idempotent), business register with no residual/band/CE/clause, editable until close. Templates: Approve with rounds "Approved. The review raised N follow-up round(s)... every point raised was resolved..."; Reject "Declined. The review identified gaps that need to be closed before this can proceed as submitted..."

### 6.9 Handoff and committee pack (§6.7)
- `generateCommitteePack`: "generated deterministically from the structured assessment" (no AI). Sets `handoff.kind = band <= 1 ? 'delegated' : 'committee'`, ignoring the decision. This is **inconsistent** with the band config (Band 2 route is "Delegated close by analyst") and with the three other copies of the rule: `routedToCommitteeFor` in RecommendationTab, `committeeRoute` in HandoffTab, and `setRecommendationDecision`. All three use committee = Reject OR band ≥ 3 (null scoring defaults to band 3). The visible failure: a Band 1 + Reject case is on the committee route in the UI, but generating the pack sets kind 'delegated'. "Generate committee pack" then stays on screen forever and approval never moves the status. **Intended rule: delegated = Approve AND band ∈ {1,2}; committee = band ∈ {3,4} OR Reject.** Make it one server function driven by `BandDef.route`, and unit-test it.
- `setHandoffNote`, `attemptEditLockedField` (logs `locked_edit_attempt` when someone tries to edit a locked pack field), `approveHandoffPack` (blocked without decision summary; committee kind → status Committee Review), `signOffDelegatedClose`.
- Demo handoff note: "Vendor AML audit scope gap (Q3 2024, pre-dates this corridor) - recommend committee flag this for a follow-up audit condition."

### 6.10 Committee (§8.5 mod Part C, §8.6 mod)
- Exactly 3 members; all must vote; no abstention; rationale mandatory; a vote is immutable once cast (no update path). Vote decisions are hidden until the third lands; the queue does show the member's own "Voted" state, and `getCommitteeVoteView` returns `myVote` with its decision before the reveal (hiding is UI-only). When the third vote lands: then all three are logged under their original `castAt` and revealed together; outcome = Approve if approvals > half, else Reject; case → Closed with outcome, `decisionSummary` and `decisionRationale` copied; events `committee_vote_cast` ×3, `decision_reached` "Committee decision: Approved (2-1)".
- Queue sort: Committee Review first, then by date received (pack approval date) desc. Columns include residual, band, date received, vote state (Voted / Not voted).
- Committee Inbox: compose-only queries to the assigned analyst (no field anchor, no updateForm, no rounds, never touches PO inbox or clarification count); analyst replies → `answered`, `unreadReply` until committee opens the tab.
- Demo: Sanjay Approve and Helen Reject are pre-seeded on 0147; Ravi (signed-in) casts the deciding vote live. Closed twin 0146 has all three (2-1 Approved).

---

## 7. Scoring engine (`src/services/scoring.ts`, pure functions)

"The AI proposes factor inputs and control-effectiveness values; this module is the only thing that ever computes a score. No inference here, only arithmetic."

```
Per in-scope AML category c:
  Inherent_c   = L_c × I_c                         (L, I ∈ 1..5 → 1..25)
  CE_c         = round1( Σ CE_i·w_i / Σ w_i )      over case controls in c; CE_i = confirmed ?? AI; 0 if none
  Retention_c  = 1 − (1 − floor) × CE_c/10         floor = 0.40 → 1 − 0.6·CE/10  (full precision; display rounds 2dp)
  Residual_c   = round1( Inherent_c × Retention_c )
  w'_c         = round2( W_c / Σ_{in-scope} W )    renormalised model weights
Composite inherent = round1( Σ Inherent_c · w'_c )
Composite residual = round1( Σ Residual_c · w'_c )
Band = first band with min ≤ composite ≤ max (else last band); route from band
Boundary flag (distance to the START of the neighbouring band, §8.4 mod §10.4):
  if band < 4 and (next.min − composite) ≤ threshold → flag 'above'
  else if band > 1 and (composite − prev.max) ≤ threshold → flag 'below'
  threshold = RoutingRules.boundaryProximityThreshold = 0.5
```

Notes:
- Retention floor 40% means controls can remove at most 60% of inherent risk (CE 10 → retention 0.40). Fixed, displayed as non-configurable.
- Category weights (v2.3): Customer/entity 0.30, Product and service 0.25, Geography 0.30, Delivery channel and third party 0.45 (sum 1.30, so only meaningful after renormalisation over in-scope categories).
- Bands v2.3: B1 0.0-5.0, B2 5.1-10.0 (both "Delegated close by analyst"), B3 10.1-17.0, B4 17.1-25.0 ("Committee review"). v2.2 had B3 to 17.5 / B4 from 17.6; v2.1 same as v2.3. Gaps (5.0-5.1, 10.0-10.1) are safe because the composite is rounded to one decimal before lookup.
- Likelihood anchors: 1 Rare (less than once in 5 years), 2 Unlikely (every 2-5 years), 3 Possible (about once a year), 4 Likely (multiple times a year), 5 Almost certain (routine, ongoing). Impact anchors: 1 Negligible, 2 Minor (internal remediation), 3 Moderate (regulatory notification likely), 4 Major (enforcement plausible), 5 Severe (license or charter risk).
- `bandChanged(prev, next)` helper; every recompute pushes to `scoringHistory`; model version is pinned per case (recompute uses the case's own model version).
- Config impact previews re-run `computeScoring` over the last 50 scored cases to count band changes.

### 7.1 Worked example: FCRM-2026-0147 (verified by re-running the arithmetic)

In-scope: Geography (w 0.30), Delivery channel and third party (0.45), Product and service (0.25); weights already sum to 1.0.

| Category | L × I = Inh | Controls (weight × CE) | CE | Retention | Residual |
|---|---|---|---|---|---|
| Geography | 4×4 = 16 | geo-1 .35×7.2, geo-2 .30×6.8, geo-3 .20×8.5, geo-4 .15×6.0 | 7.2 (7.16) | 0.568 | 9.1 |
| Delivery ch./3P | 4×5 = 20 | dc-1 .35×7.6, dc-2 .25×6.5, dc-3 .20×7.0, dc-4 .20×7.8 | 7.2 (7.245) | 0.568 | 11.4 |
| Product & service | 3×4 = 12 | ps-1 .30×6.5, ps-2 .35×7.0, ps-3 .20×8.0, ps-4 .15×6.0 | 6.9 | 0.586 | 7.0 |

Composite inherent = 16×.3 + 20×.45 + 12×.25 = 16.8. Composite residual = 9.1×.3 + 11.4×.45 + 7.0×.25 = 9.61 → **9.6, Band 2**; boundary flag fires (10.1 − 9.6 = 0.5 ≤ 0.5, direction above, other route Committee review).

After overriding ctl-dc-1 7.6 → 4.5: Delivery CE = 6.16 → 6.2, retention 0.628, residual 12.6; composite = 2.73 + 5.67 + 1.75 = 10.15 → **10.2, Band 3** → committee. The code comment calls this "the locked 9.6 -> 10.2 computation". The teardown's "9.8 → 11.5" figure is out of date; use 9.6 → 10.2 as the golden unit test (and assert the 0.5 boundary flag on the first score).

Other seeded scores (single-category cases use weight 1.0): 0118 Delivery L3 I4 CE 5.3; 0121 Product L4 I4 CE 3.5 (committee, 3-0 Approve); 0126 Geography L5 I4 CE 3.0 (w .4) + Delivery L5 I5 CE 3.5 (w .6) (committee, Rejected 1-2); 0129 Geography L4 I4 CE 5.1 (committee 3-0); 0136 Geography L2 I3 CE 3.9 (delegated); 0139 Delivery L4 I4 CE 5.9 (Committee Review, analyst replied query).

---

## 8. Config (analyst-owned, all versioned)

Shared pattern for every section: edits staged in a working set → blocking impact preview → publish with a mandatory note → new immutable snapshot `{version, publishedAt, note, changedBy}` → history list. Non-retroactive: cases pin the version in force (`fieldSetVersion`, `classificationRulesVersion`, `controlInventoryVersion`, `policyCorpusVersion`, `routingRulesVersion`, `jurisdictionListVersion`, scoring `modelVersion`, run `agentConfigVersion`), and Assurance can query by them. Scoring model can optionally re-score selected cases on publish (`rescoreCaseIds`, logged `rescored`). Section-level UI detail is in section 13.

### 8.1 Categories & Fields (§3, §10.1)
Field library, core set, category packs, global threshold, trigger-to-AML mapping, trigger category catalogue (description, AML category, classification signals, status). `previewFieldSetImpact`: affected packs, resolved-count before/after per category, affected drafts with before/after %, submitted cases always 0. `isTriggerCategoryInUse`. Versions v1 2026-01-15 (initial library replacing per-category templates), v2 2026-04-03 (72→76%), v3 2026-07-22 (76→80%).

### 8.2 Classification Rules (§10.2, not visible to PO)
- Signals per trigger with weight 0-1; a trigger's active signal weights sum (capped at 1.0); if sum ≥ trigger threshold, the Classification Agent proposes the tag. Disambiguation notes carry no weight and name `confusedWith`.
- Signals: cs-1 Vendor "names a new external vendor, agent or service provider not under contract" 0.9; cs-2 Vendor "new MSA, SOW or vendor DD questionnaire attached" 0.7; cs-9 Vendor disambiguation (data-access/retention language without named vendor often under-tags as Process Change); cs-3 Geography "names a country not on the active-operations list" 0.95; cs-4 Geography "data-flow shows cross-border transmission to a new country" 0.6; cs-5 New Product "own pricing, revenue or settlement mechanism" 0.85; cs-6 New Feature "change to an existing product's behaviour" 0.75; cs-10 New Feature disambiguation (own pricing/settlement = New Product); cs-7 Process Change "internal process/system/control change without new product, vendor or geography" 0.65; cs-8 New Customer Segment "customer population not currently served" 0.8.
- Thresholds: Vendor 0.7, Geography 0.6, Product 0.6, Feature 0.6, Process 0.5, Segment 0.6.
- **Screening auto-clear rules** (conditions ANDed): ac-1 "Process-internal only" (newVendor=false, newGeography=false, newProductCapability=false, customerFacingChange=false); ac-2 "Small population, no data or cross-border element" (customerCount < 100, dataRetentionOrCrossBorder=false); ac-3 "Existing vendor renewal, no scope change" (existingAssessedVendorRenewal=true, vendorScopeChange=false); ac-4 "Cosmetic or UI-only, no new data handling" (cosmeticOrUiOnly=true, newDataHandling=false). Field catalogue labels in `AUTO_CLEAR_FIELD_CATALOG`.
- Impact preview: drafts by pinned version, the version new drafts will get, changed signals with recent fire counts, changed rules with recent clear counts. Versions v1 2026-02-10, v2 2026-05-18 (per-trigger threshold introduced), v3 2026-08-05 (disambiguation split out; structured auto-clear conditions).

### 8.3 Scoring Model (§10.3)
Category weights, likelihood and impact anchors, bands (bands edited from Rules & Routing but published through the scoring model preview). Retention floor fixed 40%. Impact preview: score-changing? (weights/bands vs anchor text only), already-scored count, pending-run cases, in-flight on current version, last-50 recomputation band changes. Version names `v{major}.{minor+1}`.

### 8.4 Control Inventory (§10.4)
24 controls, 4-8 per AML category, each with weight, CE 0-10, evidence {name, type, link, date}, source, status. Rule: a category's active weights must sum to 1.0 (±0.01), checked at publish, not per edit. In-use controls are retired, never deleted. Staleness: judgment-only if evidence type None or no date; stale if months since evidence > `evidenceRecencyMonths` (18); nearing if > 18 − 3; else current. CSV/Excel import with column mapping → added / changed (field-level diffs) / removed / conflicts (analyst-added controls absent from the file are surfaced for a keep/remove decision, never auto-removed) → weight validation → commit. Versions v1 2026-01-20, v2 2026-05-10, v3 2026-07-08.

Seed controls (id, weight, CE, evidence type/date):
- Geography: geo-1 Jurisdiction risk screening at onboarding .35/7.2 (SOP, 2026-02); geo-2 High-risk corridor EDD .30/6.8 (playbook, 2025-09); geo-3 Sanctions list screening OFAC/UN/EU .20/8.5 (SOC 2, 2026-01); geo-4 Local regulatory licensing confirmation .15/6.0 (internal testing, 2024-03, stale).
- Delivery channel and third party: dc-1 Vendor due-diligence assessment .35/7.6 (Pinnacle Remit AML Audit Report, independent audit, 2024-08, stale); dc-2 Payout agent transaction monitoring .25/6.5 (system config 2026-03); dc-3 Third-party contract AML clauses .20/7.0 (2026-01); dc-4 Transaction velocity and limit rules .20/7.8 (2026-04).
- Product and service: ps-1 Product risk assessment sign-off .30/6.5; ps-2 TM rules coverage .35/7.0; ps-3 Customer disclosure and consent .20/8.0 (2025-04, nearing); ps-4 Fee and pricing transparency review .15/6.0 (no evidence, judgment-only).
- Customer / entity: ce-1 KYC/CDD .25/8.2; ce-2 EDD high-risk .20/7.5; ce-3 PEP screening .15/8.0; ce-4 Periodic KYC refresh .12/6.5 (2024-05, stale); ce-5 Beneficial ownership verification .08/5.5 (judgment-only); ce-6 BO register cross-check .07/7.0 (Analyst-added, Maya, from 0229); ce-7 Adverse media screening enhancement .08/6.5 (Daniel, from 0239); ce-8 Segment-specific onboarding attestation .05/8.5 (Priya, from 0233).

### 8.5 Policy Corpus (§10.5)
11 sources (internal: FCRM Policy 4.2 High-Risk Corridor Jurisdictions, Third-Party AML Risk Standard 7.1 Payout Agents, Product Risk Policy 3.4 New Product Introduction, Sanctions Screening Standard 5.3, FCRM Policy 4.1 (retired); external: FFIEC BSA/AML Examination Manual, FATF R16 Wire Transfers, R17 Reliance on Third Parties, R10 CDD, R1 Assessing Risks, OFAC Sanctions Compliance Guidance). 25 hand-written clauses (pc-1..pc-25, pc-25 retired and superseded by pc-6) + 34 generated filler clauses ≈ 59. Each clause tagged with AML categories and trigger tags. Rules: a source or clause cited on any assessed case can only be retired, never deleted; uncited ones can be deleted regardless of origin; retired clauses never appear in search; referenced-by counts per clause. Document ingestion: chunk on blank lines, detect a leading section marker (`§x.y`, `Section n`, `Part IV`, `para n`, else `¶n`), keyword-propose AML categories and trigger tags (mock AI), batch review, commit as new source or reissue; reissue diff by section: added / changed / unchanged; a changed clause retires the old (`supersededBy`) and adds the new (`supersedes`). Versions v1 2025-10-01, v2 2025-11-01 (4.1 retired).

### 8.6 Rules & Routing (§10.6)
`boundaryProximityThreshold` 0.5; `evidenceRecencyMonths` 18; `nearingStalenessMonths` 3 (must be > 0 and < recency); `slaTargetBusinessDays` 15; `autoTriageRule {strategy round-robin, lookbackDays 7, band34WeightingFactor 0.5}` ("lighter weighting toward analysts already holding an open Band 3/4 case"). Threshold changes never recompute scores, only which facts read as flags; preview shows controls changing staleness state, last-50 cases that would flag, in-flight cases changing SLA at-risk state, open Band 3/4 cases affected by an auto-triage change. History: v1 2025-06-01 (24 months, SLA 20, manual triage), v2 2025-11-01 (18 months, SLA 18, round-robin 0.3), v3 2026-06-12 (SLA 15, weighting 0.5). Auto-triage strategy is stored but **never executed** (submit assigns randomly).

### 8.7 Jurisdiction Risk List (§10.7)
1-5 scale anchors: 1 Low, 2 Standard, 3 Elevated, 4 High (FATF grey list or equivalent), 5 Severe (FATF blacklist, comprehensive sanctions). External entries (FATF, OFAC) are read-only and carry publisher and update frequency; internal entries are analyst-editable with rationale and review date. A jurisdiction may carry both; the internal value may raise but never lower the external (`validateJurisdictionEntries`). Default for unlisted = 3 (configurable, never silent). Internal review interval 12 months (nearing/stale states). Import (FATF/OFAC file stand-in): added/changed/removed; internal entries never touched; delisted entries still in use are retired. Impact preview: already-scored count, pending-run cases, last-50 band changes isolating the edit's own effect. 31 entries: internal 1 (US, UK, CA, DE, FR, JP, AU), 2 (CH, SG, HK, IN, BR, QA), 3 (ZA, SA, AE, ID, TH (nearing), MX, PH, KY, CO, NG, PA); external VN 4 (FATF grey), RU 5 and VE 5 (OFAC), IR, KP, MM 5 (FATF blacklist). Versions v1 2025-08-01, v2 2026-05-10, v3 2026-06-22. Import fixture: drops Vietnam, Venezuela 5→4, adds Kenya 4.

### 8.8 AI Agents (§10.8)
Analyst agents only, team-global, versioned. v1 2025-06-01 all Sonnet 5 / Haiku 4.5 Standard; v2 2025-09-01 Policy Mapping → Opus 5 High ("Policy retrieval is citation-bound with no generation and carries the highest fabrication consequence of any agent here"). Current: Policy Mapping opus-5 High, Risk Drafting sonnet-5 Standard, Control Matching sonnet-5 Standard, Precedent haiku-4-5 Standard, Clarification sonnet-5 Standard. Preview: changed agents, pending-run cases, already-run count; reruns pick up the active version. Override-rate reference per agent (six months) shown alongside.

PO-side AI Configuration (not versioned, editable by PO): Screening haiku-4-5 Standard, Intake sonnet-5 Standard, Extraction sonnet-5 High, Classification haiku-4-5 Standard, Submission Validation sonnet-5 Standard. Model catalogue: Sonnet 5 (Balanced, $$, 200K), Opus 5 (Thorough, $$$, 200K), Haiku 4.5 (Fast, $, 200K), GPT-5.1 (OpenAI, Balanced, $$, 128K; PO list only). Model release tags: sonnet-5 v2026.06, opus-5 v2026.06, haiku-4-5 v2026.05, gpt-5-1 v2026.03. Prompt versions stamped on events: screening-prompt v6, intake-prompt v14, extraction-prompt v9, classification-prompt v11, validation-prompt v5.

---

## 9. AI agents implied by the prototype

All are scripted today; no prompts, no model calls. "Teal = AI proposal, human confirms."

| # | Agent | Side | Input | Output | Provenance / confidence shown | Human gate |
|---|---|---|---|---|---|---|
| 1 | Screening | Intake | PO free text / chip replies (3 turns) | `screeningOutcome` auto-clear or needs-case + summary | prompt v6, model stamped on events | PO approves auto-clear |
| 2 | Extraction | Intake | Uploaded/imported docs (+ field library, extraction hints) | `ExtractedField[]` {fieldId, label, value}, state Extracted | Field source 'extracted' + provenance label on Submission tab; doc page refs in text ("From data-flow diagram, p.7") | PO confirms or edits (humanOverridden) |
| 3 | Classification | Intake | Docs/extracted fields, classification signals + thresholds | `ClassificationProposal[]` {category, reason} | `originalAiCategories` kept; reason per tag | PO confirms; removing an AI tag needs reason |
| 4 | Intake (chat) | Intake | Field state (missing only) | Questions in rounds, reply chips | prompt v14 | PO answers |
| 5 | Submission Validation | Intake | Case + resolved field set + docs | `ValidationResult` steps | "n of m checks passed" | Deterministic gate decides |
| 6 | (Clone suggestion / precedent at intake) | Intake | Case so far | `CloneSuggestion[]` (no outcome) | case ID + summary | PO ticks fields; source 'clone' |
| 7 | Policy Mapping | Assessment | Confirmed categories, AML categories, corpus | `PolicyMapping[]` verbatim clause + source + section + relevance note | clauseId link, corpus-retired flag | Accept / remove with reason / add |
| 8 | Risk Drafting | Assessment | Submission, jurisdiction list, anchors | L and I per AML category | provenance tag per value (submission, jurisdiction-list, override) and baseline detail | Override with reason, revert |
| 9 | Control Matching | Assessment | Inventory, in-scope categories, evidence | `CaseControl[]` with CE, weight, evidence, recency | evidence chip (current/stale/judgment-only) | Confirm / override with reason |
| 10 | Precedent | Assessment | Closed cases | Similar cases {caseId, title, residual, band, outcome}, cited-on-similar-cases clauses | analyst-only | Informational |
| 11 | Recommendation draft | Assessment | Scores, mappings, controls | Narratives per category + overall | edited flag vs AI text | Edit; decision is human |
| 12 | Clarification | Assessment | Gaps, flags, stage | `DraftedQuestion[]` with anchors | anchor or not-anchored reason | Analyst edits and sends |
| 13 | Decision summary draft | Handoff | Decision + rounds | PO-facing summary text | event `decision_summary_drafted` (ai) | Analyst edits before close |
| 14 | PO "Edit with AI" / document draft | Clarification answer | Field, docs, PO text | Drafted value or null | — | PO confirms |
| 15 | Assurance NL parser | Assurance | Free text | Structured `AssuranceQuery` + "interpreted as" phrases | matched phrases strip | Analyst edits filters, runs |
| 16 | Policy clause tagger | Config | Chunked document | AML categories + trigger tags per clause | batch review | Analyst confirms |

The teardown counts 10 (rows 1-5 and 7-12 minus recommendation). Rows 6, 11, 13-16 are additional AI-shaped steps the new build should decide on. Deterministic (no AI): auto-triage, scoring, boundary flag, routing, committee pack generation, vote tally, gates.

Confidence: `CaseDetail.confidenceIndicator` (0-100) exists but is barely used; provenance is the main trust signal (`ProvenanceTag`, field source labels, model/prompt versions stamped on AI events, agentConfigVersion on runs).

Override rate as the AI-quality metric (analyst dashboard + AI Agents config): targets in the corpus Policy Mapping 20%, Risk Drafting 24%, Control Matching 33%, Precedent 10%, Clarification 15%. Copy: "Read in both directions: a high rate means the agent is not earning its place; a rate near zero means the analyst has stopped challenging it, which is worse, because the audit trail then records consent rather than review."

---

## 10. Business rules, audit and notifications

### 10.1 The BA's eight governance rules (from the teardown "explainer") with code evidence

1. **Per-item confirm.** Every AI proposal is confirmed item by item: fields (PO), tags, policy clauses (accept/remove), controls (confirm/override, `confirmedComponentIds`), factors, narratives. **Advisory only today.** Risk & Controls says "All controls must be confirmed or overridden before advancing to Recommendation." but nothing enforces it. `confirmedComponentIds` is written only by control actions, is never cleared on rerun, and is read only for "N components confirmed" on the delegated card. Proposed-but-unactioned clauses silently drop out of the pack, which shows accepted clauses only. The new build must make this a server gate before the decision can lock.
2. **Scoring is arithmetic.** `scoring.ts` pure functions; AI only proposes inputs; pack generation and routing are deterministic.
3. **Policy is retrieved, not generated.** Clauses are verbatim from the corpus with source and section; Policy Mapping agent "Never paraphrases policy in place of the clause"; the case freezes the wording it cited.
4. **The PO cannot set their own gate.** Threshold, field packs, neverBlank, classification rules, auto-clear rules are FCRM-owned config. (Violated by the PO's AI Configuration page; teardown recommends moving it to analyst Config.)
5. **The analyst never edits the submission.** "The analyst can never edit a submitted value directly - request a clarification instead." Only exception: re-tag writes `trigger_categories` as an analyst-attributed version with a reason.
6. **Overrides need reasons.** Removing an AI tag, removing a clause, overriding control CE, likelihood or impact, rerun, dismissing a flag, unlocking a decision, re-tag, committee vote rationale: all require text.
7. **Asymmetric visibility.** PO never sees residual, band, CE, clauses, similar-case outcomes; separate types; PO gets a business-register decision summary; committee queries never reach the PO; clone suggestions carry no outcome.
8. **Closed means closed.** Closed cases are read-only for everyone; write needs open status; votes immutable; config changes are non-retroactive (cases pinned to versions). Holes today:
   - Committee Review counts as "open", so the analyst can still rescore, edit narratives or unlock while the committee votes.
   - The pack renders live data and is never snapshotted.
   - Unlock during Committee Review leaves the status and the cast votes in place.
   - The PO can "Edit with AI" any field while the status is anything but Closed.

Other rules worth carrying into `rules_check`:
- Submission gate: completion ≥ threshold AND neverBlank filled. Re-checked before analysis run.
- One submission version per clarification round.
- Clarification anchors must reference fields in the resolved set; unanchored needs a reason; never both.
- Thread resolve is an analyst action distinct from the PO answer.
- Reject always routes to committee; Band 3-4 always committee.
- Pack approval blocked until PO-facing decision summary exists.
- Decision locked after confirmation; unlock needs reason and forces pack regeneration.
- Committee: 3 votes, hidden until all cast, majority, rationale required, immutable, one per member.
- Control weights per category sum to 1.0 at publish; in-use controls/clauses/jurisdictions retire, not delete; internal jurisdiction ≥ external; nearing < recency.
- Unlisted jurisdiction uses default and is flagged.
- Analyst-proposed controls join the inventory immediately (no approval queue).
- Cross-analyst case views are logged.

### 10.2 Activity / audit events

PO-side `ActivityEvent.type`: case_created, path_chosen, files_uploaded, extraction_proposed, classification_proposed, categories_confirmed, tag_override, tag_added, clone_reuse, field_confirmed, validation_failed, intake_reopened, fields_completed, validation_passed, submitted, assigned, clarification_requested, clarification_responded, status_changed, decision_reached, auto_cleared, title_generated. Actor ai|po|analyst|system; AI events stamped with model version (name · release) and prompt version.

Analyst-side log types (free string): case_viewed, flag_dismissed, tag_override, clarification_round, stage_complete, rerun, policy_accepted, policy_removed, policy_added, boundary_acknowledged, control_confirmed, control_overridden, band_changed, likelihood_overridden, impact_overridden, likelihood_reverted, impact_reverted, control_added, planned_control_added, narrative_edited, decision_set, decision_unlocked, decision_summary_drafted, pack_generated, locked_edit_attempt, pack_approved, delegated_close, committee_vote_cast, committee_decision_reached, committee_query_raised, committee_query_answered, rescored, scoring_computed, boundary_flag (closed twin).

### 10.3 Notifications
PO events (only ones the PO is absent for): `clarification_requested` (Immediate, on), `decision_reached` (Immediate, on), `sla_approaching` (Daily digest, off). Dropped on purpose: case auto-cleared, validation failed (PO is watching). Channels: email always on; Teams/Slack appear only when connected; disconnecting prunes the channel, preferences fall back to the bell. Seed notifications n1-n6 (clarification on 0139, moved to committee, assigned, SLA approaching, 0126 rejected with reason, 0136 approved).

---

## 11. Seed data

- **Hand-written cases (9):** 0118 Sanctions screening data provider (Vendor, Closed Approved, delegated, Priya, 11 d, 2 rounds); 0121 BNPL feature on consumer card (New Product, Closed Approved by committee 3-0, Maya); 0126 Cross-border payout partner LATAM (Vendor + Geography, Closed Rejected by committee 1-2, Daniel, 16 d, 3 rounds; reason: no recent independent AML audit, no fixed retention); 0129 Commercial banking expansion Singapore (Geography, committee 3-0 Approved, Priya); 0133 Small-business virtual card (New Product + Segment, Under Assessment, Daniel, not scored); 0136 Wealth referral desk UAE (Geography, delegated Approved, Maya); 0139 Document verification API (Vendor, Committee Review, Priya, one answered thread); 0143 Draft 22 Aug (New Feature, 41%); 0145 Draft 28 Aug (empty).
- **Demo case FCRM-2026-0147** "Cross-border payout partner (Philippines)": minted live from the intake script (next ID after seed is 0147). Docs `Remittance_PH_BusinessCase_v3.docx` (640 KB) and `PayoutPartner_MSA_draft.pdf` (512 KB). Vendor Pinnacle Remit Services Inc. (BSP-licensed EMI). ~$180M / ~640,000 tx year one, ~70% e-wallet. Target Q1 2027. Sponsor Rekha Nair, Head of Payments. Analyst flow: re-tag New Product, round 1 (4 questions), analysis run, remove correspondent clause, add sanctions clause, geography override 3→4, round 2 (vendor audit), control override 7.6→4.5, Band 2→3, planned control, Approve, material clarifications, handoff note, pack approved, committee 2 pre-seeded votes, Ravi decides live. Committee rationales for Sanjay (Approve), Helen (Reject), Ravi (Approve with follow-up condition) are in `analystScript.ts`.
- **Closed twin FCRM-2026-0146:** same content, Closed Approved 2-1 by committee, every value derived from the same scripts and the real `computeScoring` (never hand-typed), as the fallback if the live demo fails. Its activity log shows `scoring_computed` 9.6 Band 2, `boundary_flag`, override, `band_changed` 2→3.
- **Generated corpus (`generatedCorpus.ts`, 1,584 lines):** mulberry32 PRNG, fixed seed `0x5eedc0de`, separate streams per concern. 78 cases FCRM-2026-0040..0117, Apr-Sep 2026 (monthly 12/12/13/13/14/14), 54 closed + 24 open (Submitted 8, Under Assessment 9, Committee Review 7), zero drafts. Category mix New Product 20, Vendor 17, Geography 14, Feature 12, Process 10, Segment 5; ~30% multi-category. POs: Arun 20, Nadia 15, Tom 15, Ines 14, Wei 14. 26 cases per analyst. Band targets across closed: 8/16/22/8 within ranges B1 1.5-4.8, B2 5.5-9.5, B3 10.5-16.8, B4 17.5-22. Scores via real `computeScoring` with band-aware factor ranges and control override chance (B1 10%, B2 20%, B3 38%, B4 60%, +15 for Delivery channel). Reject chances by band (vendor/other): B1 6/2, B2 10/4, B3 34/7, B4 58/11 (~84% approved). ~7-8% of clarifications not field-anchored; mean rounds Vendor ~1.9 to Feature ~0.8. Corpus drives every chart and insight; KPIs read the real store. Teardown: "80 synthetic cases can seed eval sets" (78 + 2 twins).
- **Integrations:** SharePoint (Document sources), Microsoft Teams and Slack (Communication apps), Data Catalog MCP (tools lookup_classification, lookup_lineage, search_datasets). All start disconnected; connect flow writes scope + test result, never secrets; test is deterministic (docs "Reachable. 18+14n documents"); pause without disconnect; browse SharePoint files from intake (Vendor_DueDiligence_Template.docx, CrossBorder_Payments_BusinessCase.pptx, Data_Retention_Policy.pdf).
- **Templates:** see section 3.

---

## 12. What the teardown says (05-prototype-teardown.html, 30 Sep 2026)

- Headline numbers: 15 screens / 3 roles, 22 tabs and sections, 10 AI agents all scripted, ~175 service functions to back with a real API, 29.8k lines in 112 files, 0 tests, commits, evals, prompts.
- Lines by area: Analyst Config 6,942; mock data and scripts 5,027; services 4,465; Case Workbench 3,723; PO other pages 2,044; New Case intake 1,905; UI kit and layout 1,804; analyst dashboard/queue/assurance 1,168; types 1,138; PO Case Detail 1,093; Committee 492.
- Good seam: screens call only the two service files.
- AI vs rules split "is already good and scores under engineering judgement. Keep it. The job is to make the ten AI steps real and measurable." What each agent needs: Screening prompt + rule table; Extraction doc parsing + structured output; Classification structured output + eval set; Intake chat over field state; Submission Validation prompt + checks; Policy Mapping vector search over corpus (first to build); Control Matching retrieval over inventory; Precedent similarity search; Risk Drafting prompt with scoring definitions; Clarification prompt over gaps and flags.
- Rubric fit: AI harness 30 Missing; SDLC 20 Partial; HITL/governance 15 Partial, strong base; Evaluation 10 Missing (override-rate tracking is a signal to build on); Context engineering 10 Partial (§ spec exists but not in repo); Production readiness 5 Missing; Token efficiency 5 Missing (tiers exist in config); Engineering judgement 5 Strong. About 70 of 100 points sit where the repo has no evidence.
- Repo gaps: /ai, /docs/requirements, /docs/architecture, /docs/governance, /evals, /tests, /ops all missing; README is the Vite default; no git history. Starting material: mock scripts show each agent's expected output; 80 synthetic cases for evals; worked example as a ready unit test (teardown says 9.8 → 11.5; the code now gives 9.6 → 10.2).
- Flags for the BA: PO AI Configuration clashes with "PO cannot configure own gate" (move to analyst Config); Integrations is extra scope (decide for 9 Oct); outcomes deferred and approved-with-conditions missing; Evoq logo in sidebar (confirm allowed); ask for the numbered spec; band gaps safe because of rounding (unit test it).
- 9-day plan (1-9 Oct): repo + docs, schema + API, scoring server-side + tests, Policy Mapping with retrieval first, Extraction + Classification, remaining agents, eval sets from synthetic cases, E2E + eval runs, Docker/deploy/monitoring/token report, deck + rehearsal.

Inferred spec map from § references (spec not in repo): §1 dashboards/settings mods; §2 field anchoring; §3 field library, core set, packs, threshold (§3.1 library, §3.2 trigger category dependencies, §3.4 merge, §3.5 threshold + never-blank, §3.6 version pinning); §5.4 trigger→AML mapping; §6.2 screening; §6.3 intake checklist / extraction before confirmation; §6.4 policy mapping search; §6.5 risk & controls; §6.6 recommendation; §6.7 handoff; §6.10 analysis run; §7.x scripted analyst demo (§7.2 re-tag, §7.3 round 1, §7.4 run, §7.5 round 2, §7.6 override, §7.7 recommendation and handoff, §7.8 votes); §8 access control; §8.1 SLA; §8.2 documents/provenance; §8.3 clarification threads; §8.4 summary flags, boundary convention, re-tag write-back, versions per round; §8.5 decision lock, decision summary, committee Part C; §8.6 committee inbox, second opinion removed; §8.8 committee unread; §8.9 integrations; §10.1-§10.8 config sections; §B1 NL assurance parse; §B5 PO optional fields; §B6/§B7 classification preview and history; §C1/C2/C5 committee voting and access.


---

## 13. Screens in detail

Merged from three full-file reads of the pages. "→ fn" names the service function an action calls.

### 13.1 Layout
- **Sidebar.**
  - PO nav: Dashboard, My Queue, Templates, Integrations, AI Configuration, Settings. A "New Case" button calls `createDraftCase` and opens `/case/{id}/intake`.
  - Analyst nav: Dashboard, Queue, Assurance, Config.
  - Committee nav: Queue only (§8.5 mod Part C: "one screen to find cases, one to vote on them").
  - Collapses from 220 px to 68 px; the state is kept in localStorage. Branding: "TrustOps / Risk Assessment Workbench" with the Evoq logo.
- **Topbar.**
  - Bell: unread pill, 360 px dropdown, "Mark all read". Clicking an item marks it read and deep-links to the persona's own case URL.
  - Gaps: the list is global (not scoped per persona or user) and nothing ever creates a notification.
  - Persona switcher: keeps you on the same case by rewriting the URL. Footer copy: "Demo affordance - switches which permission set renders the same case data."
- **AppLayout** sets `document.title` to "<Title> - TrustOps".

### 13.2 PO: IntakeChat (`/case/:id/intake`, 842 lines)

**State**
- `stage`: loading, entry, screening, intake, idle, done.
- The feed is a discriminated union: bubble, typing, chips, entry-choice, upload, extraction-progress, classification, question, clone, auto-clear-approve.
- `isDemo = id === 'FCRM-2026-0147'`. Most of the flow is scripted for the demo only.

**Transcript**
- Every bubble is persisted with `appendChatMessage`, idempotent by id.
- Transcript-only notes (`kind: 'note'`) record path choice, confirmed categories, each question and answer, reuse picks, submit attempts and validation results.
- **Resume:** replays bubbles verbatim (no animation). It loses pending question cards, the classification card, the upload widget and resume mode. A PO who left before confirming categories cannot confirm them on return.

**Flow**
1. **Greeting:** "Hi Arun - before we dive in: is this a change you're confident needs an FCRM case, or would you like me to ask a couple of quick questions first?" The PO picks one of two cards:
   - "Not sure if this needs FCRM review?" goes to Screening.
   - "Start intake" goes to the upload step.
2. **Screening:** three chip exchanges, then "Approve light-touch record" → `approveAutoClear`. Confirmation: "Logged. The light-touch record is saved to your queue as Closed - FCRM can see it in aggregate for periodic spot-check." There is no "needs a case" branch.
3. **Upload:**
   - `UploadWidget` shows a staged mock list (no real file picker) → `uploadFiles`.
   - "Browse {SharePoint} instead" appears only if a document source is connected AND enabled. It opens `BrowseSourceModal` → `importIntegrationDocuments`.
4. **Extraction and classification:** `ExtractionProgress` shows two timer-driven tracks, then `Promise.all(extractFields, classifyCategories)`.
   - The client computes completion and pushes it with `setCompletion`.
   - Title generation (§6.6): demo title, otherwise `summarizeTitle` (strips the leading verb, keeps 8 words).
5. **ClassificationCard:** "Classification Agent proposes" with category and reason per row.
   - Removing an AI tag opens `CategoryReasonModal`: "Removing X changes which fields are required. This needs a one-line reason - it's written to the Activity Log alongside the AI's original proposal." The button stays disabled until a reason is typed.
   - "Browse all categories" lists the six categories with descriptions. AI-proposed boxes are locked; adds need no reason.
   - Confirm → `confirmCategories`. Bubble: "Required-field set is N fields - 9 core plus what those categories add."
   - **Bug:** the confirmed set is built from the demo constant `CLASSIFICATION_PROPOSALS`, not from the card's own proposals.
6. **Question rounds:** `IntakeQuestionCard` gives each question its own inline box; the global input answers the first pending question.
   - Round 1 asks for the sponsor and the vendor legal name.
   - Round 2 asks for data retention, alongside the clone cards.
   - After crossing the threshold: "You're at X%, above the 80% threshold. <blank labels> is still blank. Add it below, or submit now and the analyst will raise it if they need it."
   - Submit lives only in the header.
7. **Clone reuse:**
   - `CloneSuggestionCard`: "Similar prior cases - worth reusing before you answer more questions". It shows case ID and summary only: no outcome, no status dot, no insight.
   - `CloneReuseModal`: field-level checkboxes; already-filled fields are disabled ("Already filled on this case"); no reuse-all. "View full case" opens `CaseViewModal`.
   - → `reuseCloneFields`.
8. **Free-text routing:** screening reply, then pending question, then resume reply, then generic (`/submit/i` triggers submit; otherwise `getIntakeTurn`).
9. **Submit:**
   - The header Submit button is never disabled by completion; the gate is `runValidation`.
   - `ValidationModal` reveals steps every 550 ms (a failed step shakes) and cannot be closed until all are shown. Three failure copies:
     - "Two separate failures..."
     - "Always-required fields still empty, independent of the X% completion..."
     - "Below completion threshold..."
   - Pass → Continue → `submitCase`, then the success screen "Assigned to X. SLA target: date. Status: Under Assessment."
   - Fail → "Reopen intake chat" → `reopenIntake`. Demo: "I just need three things...". Non-demo replies capture nothing, and "missing fields only" exists only in the copy.
   - `completionPctAtSubmission` is never set, although the Queue reads it.
10. **ChecklistPanel** (right, 40%, expandable):
    - `CompletionRing` with a threshold tick and a glow on crossing: "{threshold}% to submit".
    - "{filled} of {total} required fields".
    - Version banner: "Started on field set v{n} - v{m} is now active, but this draft finishes on the version it started on."
    - Groups: Core set, then "Pack: X" (a shared field is listed once).
    - Row states: Extracted (teal check), Needs confirmation (amber), Missing (grey), and missing + always-required (red with an "Always required" pill).
    - Suffixes "· Reused from X" and "· edited". Inline edit → `editFieldValue`, which does not update completionPct (bug).
    - Uses the static seed packs, not the pinned snapshot.
11. **Completion % inconsistencies:** three formulas (post-extraction count / required; filled / required on the client; filled / required on the server). The client is authoritative through `setCompletion`. The backend must own this.

### 13.3 PO: Case Detail (`/case/:id`)

**Header**
- Back link, case ID, inline-editable title (`updateCaseTitle`), StatusDot, category tags.
- "Open Intake Chat" button for Drafts only.
- "SLA target: date (N business days left)" and "Analyst".

**StageTracker:** Draft → Submitted → Under Assessment → Committee Review → Closed (the label becomes the outcome, green or red). On a delegated close, Committee Review is struck through: "Not required - closed under delegated authority".

**Tabs**
- **Overview:**
  - `SubmissionVersionView` (editable while status ≠ Closed).
  - Additional (optional) fields.
  - Documents (version, source badge, View → `DocumentViewModal` with a synthetic letterhead preview chosen by filename).
  - Decision: Pending, or an outcome badge with **decisionSummary only**.
- **Inbox:**
  - Grouped by round, "Round N, Sent date" (§8.4 mod §4). Badge = threads awaiting the PO, counted per question.
  - ThreadCard: "Re: {field}" or "General question (not tied to a field)", an "Updates form" badge, and status Awaiting your response / Answered / Resolved.
  - Conversational threads → `SingleFieldReply` → `poRespondToClarification`.
  - Form threads → `FormFieldsAnswer` with one `FieldAnswerBox` per field:
    - On render it calls `proposeFieldDraftFromDocuments` (§8.7 mod §2: draftability is decided at render, not on click).
    - A draft is labelled "AI-drafted value - confirm or edit".
    - With no draft: "This one needs your input - it isn't in the uploaded documents."
    - "Confirm answer(s)" is disabled until every field is filled → `poConfirmFieldEdit` per field (one version per round).
- **AI Assistance:**
  - "Agent activity - every agent in the intake pipeline, including ones that didn't run on this case" ("a silent gap... would be indistinguishable from a failure").
  - Rows: Screening ran / offered and declined / not run; Intake; Extraction; Classification "n proposed, k confirmed"; Validation ran / failed. Each expands to model, effort, version and time.
  - **Bug:** it shows the current config, not the config that actually ran.
  - Full transcript, including notes.
- **Activity Log:** timeline with an actor chip (AI / You / Analyst / System), detail and model version. "Export case record" only shows a toast.

**SubmissionVersionView** (shared PO and analyst, §6.2 identical rendering)
- Version select ("v1 (as submitted)", "vN (after clarification round R)") and a "Show changes since submission" toggle.
- Groups: core, then packs.
- Diff chips added / edited, with the old value struck through.
- Provenance priority: Revised round N > Revised by analyst - reason > Reused from X > Entered / Extracted.
- Data-integrity warning when a never-blank field is empty.
- Documents field: "Field names N documents; M uploaded."
- PO "Edit with AI": describe the change → `poEditFieldWithAI` → editable draft → "Confirm and save" → `poConfirmFieldEdit`. This auto-attaches to an open thread on that field and mints a version per edit when outside a round.
- **Bug:** the reuse link points to the analyst route.

**Asymmetric-visibility leaks.** `getCase` returns all of `CaseDetail` to the PO. That includes `decisionRationale`, confidence, model and prompt versions, `originalAiCategories`, `analystAddedCategories`, the version stamps, `notFieldAnchoredReason` and `raisedFromStage`. A PO DTO is needed.

### 13.4 PO: Dashboard, Queue, Templates, Integrations, AI Config, Settings

**Dashboard** ("Your submissions, cycle time, and what the analysts most often ask about")
- KPIs from the real store, scoped to Arun, each drilling into the queue:

  | KPI | Detail |
  |---|---|
  | Active cases | Submitted, Under Assessment, Committee Review |
  | Needs your response | "Nothing waiting on you" when zero |
  | Average cycle time | "Nd under/over the 15-day target"; the 15 is hard-coded |
  | Clarification rounds per submission | vs team average |

- Five corpus charts, each with a sparkles insight: category mix (bar), rounds you vs team (grouped bar with team line), clarification types by category (stacked), cycle-time trend (line with SLA line, "Fixed: last 6 months"), outcome donut.
- One fixed colour per trigger category across all charts (§4.1).
- **Bug:** the time preset does nothing.

**Queue** ("My Queue")
- Filters: search, status (Draft, Submitted, Under Assessment, Committee Review, Closed · Approved, Closed · Rejected), category, dates (drafts always pass), needs-response.
- Columns: Case ID, Title (+ amber pill "N clarifications awaiting your response"), Categories, Status, Analyst, Submitted, Completion (↑ when raised after submission), SLA ("Nd left", amber at ≤3, red "Nd over"; closed shows "Nd cycle"), overflow (Resume / Discard draft / Open).
- Sort: status priority, then newest.

**Templates** ("Required fields and the completion threshold are FCRM-owned and read-only here")
- Threshold card (FCRM-owned, locked) and core set v1.
- Worked example: "A vendor onboarding case in a new geography resolves to 18 required fields, 6 of them always-required. Submitting needs 15 of 18 filled, and all 6 always-required fields must be among them."
  - Note: the code resolves 15 fields for Vendor + Geography (9 core + 5 + 1), so the copy's 18 looks stale.
- Field search; category list; core and pack cards with `LockedBadge` "FCRM-owned".
- Optional-fields editor ("yours to edit": add, rename, delete, drag-reorder; no approval, §B5; global rather than per PO).
- AI insight card; separate version histories for the core set and each pack.

**Integrations**
- Three categories with stated effects:
  - Document sources: browsing in intake.
  - Communication apps: notification channel.
  - MCP: tools for the intake agents (not wired).
- Row states: Connected, Connected · paused, Not connected, Test failed.
- Enabled switch (pause keeps scope); Manage; Disconnect.
- `IntegrationConnectModal` steps:
  - Document sources: location → authorise → scope → test → confirm.
  - Communication apps: workspace → authorise → destination → test → confirm.
  - MCP: server → auth → tools → test → confirm.
  - Banner: "no password, token or secret is collected or stored".
  - The deterministic test can fail and the connection still completes; the failure stays noted.
- `IntegrationDisconnectModal` names each consequence.

**AI Configuration (PO)**
- Model reference table and five agent cards (Screening "Decides auto-clear vs. needs-a-case", Submission Validation "Runs the submit-time completeness gate", ...). Each card edits model and effort.
- `EffortChangeModal` copy: "slower and more expensive to run, on every case going forward".
- Not versioned or audited. Governance clash (rule 4).

**Notification Settings**
- "All notifications appear here in the app - the bell icon - regardless of these settings... There's no per-case mute."
- Email is always on, plus connected apps (paused apps still listed).
- Three events, each with Immediate / Daily digest (disabled when the event is off).
- Autosaves.

### 13.5 Analyst: Queue and Workbench shell

**AnalystQueue** ("Every case in the system - assignment is AI auto-triaged on submission")
- Filters: Assigned to me / All; status Under Assessment / Committee Review / Closed (+ outcome) / All; category; band.
- Columns: ID, Title (+ pill awaiting PO), Categories, Status, Assigned to, Submitted, Residual (band) "12.3 (B3)", SLA "{total}d / {active}d" (red at or above target, amber within 3).
- Overflow: Open, Open Inbox.

**Workbench header**
- Read-only banner. "Re-tag case" pencil (canWrite). "View analysis run" / "Run in progress".
- Info strip: "N business days total · N analyst-active · N awaiting PO", Analyst, "Scoring model vX" with an inline ScoreBadge.
- StageTracker with dates.

**Tabs:** Summary, Submission, Inbox, Policy Mapping, Risk & Controls, Recommendation, Handoff, Activity Log (`?tab=` deep link).
- The Inbox badge counts **answered** threads (waiting on the analyst), whereas the queue pill counts awaiting-PO. Two semantics: name both explicitly.

### 13.6 Analyst: tabs

**Summary**
- `SectionShell` has three states: not-yet-reached, not-applicable, populated ("Never render a blank section").
- Sections:
  1. Outcome and decision: badge, date, decisionSummary, "Signed off by X · route".
  2. Risk summary table: Category, Inherent, CE, Residual, Weight.
  3. Stage timeline, with a row per clarification round and its turnaround.
  4. AI case summary.
  5. Confirmed categories, showing any PO removal reason.
  6. Completeness: when below threshold, a red border and "Analysis cannot run until the case is back above threshold."
  7. Flagged values: dismiss needs "Why is this resolved?"; "No open flags." when empty.
  8. Similar prior cases.
  9. Complexity: "This is not a proposed risk tier - the composite residual is an output of the assessment, not a starting point."
- Actions: Request clarification (stage clarify), and Run analysis (disabled below threshold: "Below the N% completion threshold - request a clarification first.").
- **Gap:** Run analysis stays enabled after a completed run, so it bypasses the rerun reason and wipes overrides.

**Submission:** "The filled template instance, read-only. The analyst can never edit a submitted value directly - request a clarification instead." Shows the version view, optional fields, and documents with "Round N" badges.

**Inbox**
- "From the Risk Committee" card: committee queries with Reply → `respondToCommitteeQuery`. Not gated by canWrite (gap).
- Summary line: "N rounds · N questions · N unresolved · N business days total PO turnaround". Compose button.
- Three-round nudge: "Three rounds and counting on this case - at this point a quick call with the PO may resolve things faster than another written round."
- Round cards: "Raised from Clarify / Policy Mapping / Risk & Controls".
- ThreadCard:
  - Anchor: "Re: field" link, or "Not field-anchored" with the reason; "Multi-field" badge.
  - "Updates form" or "Updated form → vN".
  - On answered threads: Resolve, or "Ask a follow-up" ("What's still unsatisfactory about the answer?").
- **Gap:** multi-field updateForm answers through `poRespondToClarification` do not write fields.

**ClarificationComposer** ("Drafted by the Clarification Agent from detected gaps. Edit, add, or remove questions - everything sends as one batched request.")
- Per question: text, field-anchor checkbox list (0 = not anchored, which shows a reason input; 1 = single; 2+ = multi-field), "Update form" checkbox, delete.
- "Add question". "Send round (N questions)".
- The reason is optional in the UI; a default is filled silently.
- The `mapping` stage exists but has no entry point.

**RetagModal** ("Adding a category expands the required-field set and may drop completion below 100%. Both the PO's original tags and this addition are logged with a reason.")
- Add only; reason required.
- Does not rescore; new categories later default to L1 / I1.

**AnalysisRunPanel**
- 440 px drawer that polls every 450 ms (replace with SSE). Six stage rows with status, duration and output; scoring is labelled "Deterministic".
- "Rerun in full" with a required reason. Copy: "This discards control overrides, narrative edits, and policy clause removals made since the last run, and runs all six stages again in full. The discarded version stays in the case record."
- In practice it wipes everything and archives nothing. Locked decision, handoff and confirmations are left untouched. No threshold or canWrite check.

**Policy Mapping**
- Empty state until the run.
- Cards per category: italic clause, "{doc} - {section}", "Analyst-added via X", relevance note. Retired-in-corpus note: "The text above is what this case actually relied on and does not change."
- Accept (proposed only); Remove (proposed or accepted, reason required); no un-remove.
- AddClauseSearch: "Searching N clauses in scope, M total"; "Widen to full corpus" warns that "Out-of-scope clauses need a justification note when accepted." (not enforced); rails as in 6.5.

**Risk & Controls**
- Summary: composite residual (ScoreBadge); composite inherent ("before controls - not used for banding or routing"); "Controls reduced exposure by N%"; normalisation note.
- Boundary flag: "The composite residual (X) is D below the start of Band N. On the other side: {route}." and "Measured to where Band N begins, not to the edge of the current band." → "Acknowledge and continue".
  - The acknowledgement gates nothing.
  - The downward wording is wrong (distance is measured to prev.max).
- Per-category derivation table: L, I, Inherent, CE, Retention, Residual, Weight, Inh×w, Res×w; Composite total row.
- `RiskMatrix`: 5×5 SVG; inherent hollow, residual filled at impact×√retention (a visual approximation).
- Category drawer:
  - Jurisdiction flag "X isn't on the Jurisdiction Risk List - the configured default of N was applied", with a link to config.
  - FactorBlock with ProvenanceTag and anchor definition: "proposed P -> adjusted N", "Adjustment reason", Edit, "Revert to proposal".
  - FactorEditorPanel: five anchor buttons; reason required if the value changed (the server does not check).
  - Derivations: inherent, weight "raw r ÷ Σ", CE itemised per control (notes missing inventory controls), retention/residual with "40% of inherent risk always survives. Controls mitigate risk and never eliminate it."
- Controls card: "{confirmed} of {total} confirmed".
  - ControlCard: status, EvidenceBadge ("Judgment only" / "{name} ({type}, {date})" + Stale), "AI-proposed X -> confirmed Y".
  - Confirm; Edit (reason required if ≠ AI value).
  - Advisory footer: "All controls must be confirmed or overridden before advancing to Recommendation."
- Band-changed modal: "This override changed the case's band and route." Before/after ScoreBadges. Fires only for control and factor overrides.
- AddControlModal: "From inventory" (weight hard-coded to 0.5; no duplicate filter) or "Propose a new control" (joins the inventory immediately).
- Planned controls: "Planned controls do not reduce residual risk - a control that does not exist cannot mitigate anything." Hypothetical residual is labelled "not applied". Description, owner and target are required; no edit or delete.
- "Raise clarification from here" (stage risk-controls).
- Uses `SCORING_MODEL_VERSIONS[0]` from mocks instead of the case's model version (bug).

**Recommendation**
- Gate: run done only.
- Per-category and overall narrative cards: Edit, with an "Edited" badge. Still editable after the lock.
- Material clarifications: seeded by the UI for the demo; no curation UI.
- `ConfirmDecisionModal` ("Recommending X"), consequence by case:

  | Case | Copy |
  |---|---|
  | Reject | "Rejection routes to committee regardless of band." |
  | Approve, B3-4 | "...so this routes to committee review" |
  | Approve, B1-2 | "Eligible for delegated close under analyst authority - no committee pack is generated for this route." |

  Footer: "Confirming locks the decision. Changing it afterwards requires an explicit unlock with a logged reason, and regenerates the pack."
- Unlock modal: reason required.

**Handoff**
- Gates: run done and decision set. Calls `ensureDecisionSummaryDrafted` on load.
- **Delegated route (Approve, B1-2):**
  - Band 2 banner: "No independent challenge on this delegated close. This Band 2 case closes under delegated authority with no review by anyone other than the assigned analyst... the next step remains a lead or FCRM-owner role."
  - Card: residual, recommendation, model, "N components confirmed".
  - DecisionSummaryCard ("A separate artifact... business register, no composite residual, band, control CE or policy clause. This is what the PO sees at closure.").
  - "Confirm sign-off and close case" (disabled without a summary). "Closed delegated cases appear in a periodic digest for committee spot-check" (the digest is not built).
- **Committee route:**
  - Banner: "Deterministically generated pack, no AI. Structured numbers, composite residuals, bands, control CEs and the matrix are locked - narrative and note sections are editable."
  - "Generate committee pack". Then "Full committee pack" with Export (.txt), CommitteePackView, editable overall narrative, DecisionSummaryCard, "Optional analyst notes" ("Distinguished from generated content").
  - "Approve pack and share with committee" (disabled without a summary) → Committee Review.

**CommitteePackView** (shared analyst and committee)

| # | Section | Locked? |
|---|---|---|
| 1 | Case identification | Locked |
| 2 | Submission summary | Locked |
| 3 | Confirmed categories | Locked |
| 4 | Policy basis (accepted clauses only) | Locked |
| 5 | Per-category risk breakdown (CategoryDrillDown) | Locked |
| 6 | Controls (CE + evidence badge) | Locked |
| 7 | Planned controls | Locked |
| 8 | Composite and routing (term-by-term computation) | Locked |
| 9 | Material clarifications | Locked |
| 10 | Analyst narrative | Editable |
| 11 | Analyst notes | Editable |
| 12 | Version stamps (scoring, field set, control inventory, policy corpus, routing, jurisdiction list) | Locked |

- Footer: band ranges.
- CategoryDrillDown is "the only place a member challenging a number can go" (§8.6 mod §2.2): L and I with provenance, anchors, baseline and adjustment reasons (the impact reason is missing), CE per control, and "The arithmetic".
- "Locked" is visual only and the pack is live.
- `editsLog` and `attemptEditLockedField` are typed but never used.
- **Missing from the pack:** the analyst's Approve/Reject decision, the boundary flag and its acknowledgement, override reasons and history, policy removals, the unlock log, the risk matrix.

**Activity Log:** merged PO and analyst events, ascending, with actor chips. Export is a toast.

**Shared components**
- `ScoreBadge` (§3.2: "never a bare number, never a qualitative label - numeric band only"). Variants: full (residual + "Band N of 4 (range)" + route), compact, inline. Band tones B1 success, B2 info, B3 warning, B4 danger. The ranges are hard-coded.
- `ProvenanceTag` (§6.5: "Provenance is always a visible label, never a tooltip"). Labels: Submission / Jurisdiction risk list / Jurisdiction risk list, adjusted by analyst / Control inventory / Analyst override.

### 13.7 Committee
- **CommitteeQueue** ("Cases at Committee Review, plus cases this committee has already closed")
  - Vote filter: Not voted (default) / Voted / All. Status filter.
  - Columns: ID, Title, Categories, Residual (band), Analyst, Date received, Your vote.
  - Sort: open first, then most recently received.
- **CommitteeCasePage**
  - Two tabs only, Pack and Inbox (§8.6 mod §2.1).
  - Banner: "Read-only. You are viewing this case as a Risk Committee member. Committee members read the pack and analyst notes and cast one vote - there is no editing, clarification, rerun or control change available here."
  - Header ScoreBadge. Export pack.
- **CommitteeVotePanel**
  - "{cast} of 3 cast", never a tally.
  - Before voting: "Approve or Reject, with a mandatory rationale. A vote cannot be changed once cast."
  - After voting: "You voted. Votes stay hidden until all three members have cast theirs".
  - Vote modal: rationale required.
  - After the reveal: "Committee vote - revealed", outcome, and each vote with its rationale.
  - The current member is hard-coded to Ravi.
- **CommitteeInboxTab:** compose-only to the analyst ("A question here goes to the assigned analyst, not the PO"). Not gated by status. No committee follow-up.

### 13.8 Analyst: Assurance and Dashboard

**Assurance** ("Query across every case's scoring, controls, and jurisdiction exposure across all analysts' cases")
- "Ask in plain language" → "Interpret" (keyword/regex mock; "This only fills in the filters below - nothing runs until you do", §B1). The "Interpreted as: ..." strip appears; failure: "Couldn't interpret that... Nothing was changed."
- Filters:
  - Min band.
  - Category in scope.
  - Min jurisdiction risk (3+/4+/5).
  - Analyst.
  - Residual min and max.
  - Submitted from and to.
  - Policy clause cited.
  - Scoring model version.
  - Field-set version.
  - Route taken.
  - Checkboxes: judgment-only control, stale evidence, boundary flag fired.
- Buttons: Reset, "Load example query" (Band 3+ with stale evidence), Run.
- Results: "N matching cases" with green matched-reason chips. "Export with reasoning" produces a client-side CSV with a 16-column preamble (§B4: one file per query run).
- Defects:
  - A query with no filters returns 0 rows.
  - `dateTo` excludes cases submitted on the end date.
  - Jurisdiction match is a substring test.
  - The classification-rules version is not exported.

**Analyst Dashboard** ("What needs you, where your time goes, whether you're calibrated, and whether the AI is worth trusting")
- KPIs from the real store: open assigned to me; approaching SLA breach; average analyst-active cycle time; clarification rounds per case.
- Nine corpus panels, each with an insight:

  | # | Panel | Notes |
  |---|---|---|
  | 1 | Cycle time by stage | |
  | 2 | Cycle-time trend vs target | fixed 6 months |
  | 3 | My residual vs team median | by AML category |
  | 4 | Inherent vs residual | % reduction |
  | 5 | Clarifications by field, stacked by stage raised | "late ratio" → "candidate for the field pack", CTA to Categories & Fields |
  | 6 | Band distribution | |
  | 7 | Override rate per agent | `#ai-quality` anchor |
  | 8 | Policy mapping trend | accepted / removed / analyst-added, fixed 6 months |
  | 9 | Control inventory health | size, analyst-added, judgment-only, stale |

### 13.9 Analyst: Config (8 sections, shared working set)

**WorkingSetContext** (§1.1 unsaved-changes mod)
- One cross-section list of `PendingChange {id, label, sectionId, sectionLabel, revert()}`. Sections register publish and discard actions.
- Sticky banner: "N unsaved change(s) · Section A, Section B". Expands to per-item Revert; Discard all; Publish loops over sections.
- Every section stays mounted (CSS hidden). Deep links via `#section`.
- Publish flow: impact-preview modal, then a "Version note (what changed and why)" textarea, then "Confirm and publish new version".
- Defects:
  - Multi-section publish opens invisible modals.
  - Reverts restore whole snapshots, so they are unsafe out of order.
  - Imports bypass the working set and commit directly.
- Version history card in every section; rich rows have an "Active" badge and a "View weights/rules/detail/configuration" toggle.

Section specifics beyond section 8:

**Categories & Fields**
- Threshold card ("One threshold, applies to every submission regardless of category"; 0-100 not enforced).
- Core set: cannot remove `trigger_categories` (every case depends on it). Cannot remove a neverBlank field until the flag is cleared (§2.1/§2.2, refused at click).
- Packs: adding a core field is refused as redundant; "shared with X, Y" shown.
- Library:
  - Columns: Label / Key / Type / Referenced by / Always required.
  - Key is slugified and immutable: "Keys are immutable once created - edit the existing entry instead."
  - Retire cascades out of the core set and every pack: "Cases that already use it keep it".
- Add-trigger wizard, four mandatory steps plus review:
  1. Unique name + PO description.
  2. Field pack (may be empty).
  3. AML mapping: "without it... nothing for the scoring model to compute".
  4. At least one signal: "without at least one, this trigger can only ever be applied manually".
- Trigger signals here are stored separately from the weighted Classification Rules signals (not reconciled).
- Remove trigger: retire if in use, else delete.
- Process Change → Delivery channel mapping is flagged as a deliberate finding.
- Resolved-set preview with colour chips.
- PO optional fields shown read-only.

**Classification Rules**
- Per trigger: proposal threshold (step 0.05); signal rows (12-column grid; Active switch, weight, reorder, edit, delete); disambiguation notes with "↪ confusedWith".
- Auto-clear rules: name, active, "Cleared N of last 50", AND chips, condition editor.
- **Test panel** (client-side simulated NLP on unsaved rules):
  - A signal fires if at least min(2, n) of its 6+-letter non-generic words appear.
  - Combined weight = min(1, Σ); "Would propose" if combined ≥ threshold.
  - Presets: new vendor onboarding, internal process update, new wallet feature.

**Scoring Model**
- Weights are relative ("need not sum to anything"; normalised in scope), with a live worked example sentence.
- Anchors editable. Retention floor locked: "Fixed at 40% - not configurable, on this or any page. Controls mitigate risk and never eliminate it."
- Bands are read-only here.
- Test panel runs `computeScoring` client-side on the draft model (L, I, CE).
- `ScoringImpactModal` offers a per-case "Re-score on publish" vs "Stays as scored" choice.

**Control Inventory**
- "Weight total X.XX / 1.00" with a Balanced / Unbalanced badge per category.
- Columns: Control (source tag) / Weight / CE / Evidence / State / Action / Manage.
- "Add evidence" and "Request retest" are toast-only.
- The publish preview only validates weights ("Cannot publish - ... do not sum to 1.0", short by / over by); no case-impact preview.
- CSV import (.csv only; Excel refused):
  - Map 8 columns: name*, category*, weight*, CE*, evidence name, type, link, date.
  - Bad rows are silently dropped.
  - Preview Added / Changed / Removed / "Analyst-added, not in this file - your call" (keep or remove).
  - Commits directly.

**Policy Corpus**
- Sources card. Filters: text, AML category, trigger, origin, source, show retired.
- External clause text is locked (only tags editable).
- Add clause (section and text required, at least one AML category).
- Retire if cited ("cases that cited it keep their original text with a retired note"), else delete. A source cascades to its clauses.
- The publish modal has no impact preview.
- Upload wizard (.txt parse; new source or reissue):
  - Chunk review: keep or drop, edit text, toggle categories.
  - Reissue diff: Added / Changed (old retires with supersession link) / Unchanged.
  - Missing-from-reissue clauses are not handled.

**Rules & Routing**
- Non-retroactive banner.
- Band table editing, validated client-side only: contiguous 0.1 apart, starts at 0.0, ends at 25.0. Published through the scoring model.
- Thresholds: boundary 0.5, recency 18, nearing 3, SLA (active v3 = 15).
- Auto-triage: strategy, lookback, Band 3/4 weighting. "Known gap" note: no availability, capacity or reassignment.
- `RoutingImpactModal` shows only changed rows, else "No live consequence detected for this change."
- (The config sub-agent quoted SLA 18 and B3 to 17.5. Those are v2 values; active seed v3 is 15 days and v2.3 is 17.0.)

**Jurisdiction Risk List**
- Banner: "Verified as of the June 2026 FATF plenary... re-verify."
- Anchor legend. Table: search, source / risk / staleness filters, sort; "N of M shown · x stale".
- External rows locked ("Admin-set, read-only"). Internal rows editable; edit stamps `lastReviewed` today.
- Internal ≥ external rule enforced.
- Default for unlisted ("Never applied silently").
- Staleness: external nearing > 4 months, stale > 8 (FATF plenary cadence); internal stale > interval, nearing > interval − 3.
- Copy: "Not change-type dependent, by design" (~200 jurisdictions × 6 triggers would double-count).
- "Preview FATF/OFAC sync" fixture import commits directly.

**AI Agents**
- Banner: "These settings apply to every analyst on the team... one analyst changing a model here changes everyone's assessments."
- Model reference table; effort explainer.
- Agent descriptions:

  | Agent | Description |
  |---|---|
  | Policy Mapping | "Retrieves applicable policy clauses for the case's confirmed categories and shows each verbatim with its source. Never paraphrases policy in place of the clause." |
  | Risk Drafting | "Pre-populates likelihood and impact factor values from the submission and drafts the rationale narrative per risk category. Computes no composite residual." |
  | Control Matching | "Proposes mitigating controls from the control inventory with a suggested effectiveness value and the evidence behind it." |
  | Precedent | "Surfaces comparable prior cases and the clauses cited on them." |
  | Clarification | "Drafts batched clarification questions from detected gaps, for the analyst to edit and send." |

- Override-rate badge, "last 6 months". Tone: ≤3% danger (rubber-stamping), ≤15% success, ≤25% default, >25% warning.
- Effort change confirm modal.
- Footer: "Committee pack generation is not listed here, because it is deterministic."
- No per-agent input/output schemas, prompts or confidence settings.

### 13.10 Design tokens (index.css, "Myridius Design System (Standalone)")
- **Brand:** carbon-moss #194247 (hover #14363b, deep #133338), quantum-leaf #52e081 (tints #94dea5, #cbebbd, #e3f6db), teal #09a78d, digital-pavo #337077.
- **Text:** #0c2226, #285055, #52787d, #9caaab. **Borders:** #dde3e2, #c3d2d3.
- **Semantic:** success #09a78d, warning #b98a1e, danger #8a3324 (bg #f9ece9), info #337077.
- **Scales:** ink-1..10, verdant-1..10.
- **Font:** Segoe UI / system-ui. Radius 6 / 10 / 14 / 20; container 1280; light theme only.
- **Problems:**
  - `@theme inline` does not map danger-bg, verdant-* or ink-*, so classes like `bg-verdant-1` probably render no colour (unverified).
  - Many hard-coded hexes.

---

## 14. Port / reuse recommendations

### 14.1 Port to the new React frontend (W1), with listed fixes
- **UI kit** (`src/components/ui/*`): Button, Card, Badge (+ CountPill, Tag, LockedBadge), StatusDot, Tabs, Modal (add focus trap and Esc), Drawer, Toast, Form (Input, Textarea, Select, Checkbox, Switch; add ARIA roles), MultiSelect, OverflowMenu, SegmentedControl, Skeleton, EmptyState, Icon (or swap for lucide), CompletionRing. Fix the token mapping in `index.css`.
- **Layout:** Sidebar (per-role nav), Topbar (bell + user menu). Replace the persona switcher with real auth roles; it could stay as a demo-only role switch.
- **Intake:** ChatBubble / TypingIndicator / ReplyChips / AttachmentChip, ChecklistPanel, ClassificationCard + CategoryReasonModal + BrowseCategories, CloneSuggestionCard + CloneReuseModal, IntakeQuestionCard, ValidationModal, ExtractionProgress (wire to SSE job status), UploadWidget (make it real), BrowseSourceModal.
  - Rebuild IntakeChat's orchestration as a thin renderer of server-sent turns (`ChatTurn` with cards to render).
- **Case views:** StageTracker, SubmissionVersionView (fix the PO link), Inbox ThreadCard / FieldAnswerBox / FormFieldsAnswer, AIAssistanceTab (records per run), ActivityLog timeline, DocumentViewModal (real preview).
- **Analyst:**
  - ScoreBadge (ranges from the model); ProvenanceTag; RiskMatrix.
  - Risk & Controls pieces: FactorEditorPanel, FactorBlock, CategoryCeDerivation, ControlCard, EvidenceBadge, AddControlModal, PlannedControlModal, the derivation table, the band-change modal.
  - SummaryTab SectionShell + FlaggedValueRow; ClarificationComposer; AnalysisRunPanel (SSE).
  - ConfirmDecisionModal + lock/unlock; DecisionSummaryCard; CommitteePackView + CategoryDrillDown (render from a frozen pack snapshot).
- **Committee:** CommitteeVotePanel, CommitteeInboxTab, CommitteeQueue.
- **Config:** WorkingSet banner pattern (persist drafts server-side; one transactional publish), version-history list, ScoringImpactModal and the other impact-preview modals, import wizards (CSV mapping, reissue diff), the add-trigger wizard, the jurisdiction table.
- **Dashboards:** KpiCard, ChartCard with insight strip, StatStrip, Recharts configs; Assurance query builder + "interpreted as" strip + matched-reason chips.
- **Seam:** keep the two service-module interfaces as the typed API client (generate from FastAPI OpenAPI). The function list (~175) is the endpoint checklist.

### 14.2 Move to the backend (non-negotiable)
1. **Scoring engine** (`scoring.ts`) → scoring-service, pure Python, with golden tests: 9.6 → 10.2, boundary flag 0.5, band-gap rounding, renormalised weights, retention full precision, CE rounding.
   - Return derivations (anchors, weights, floor) from the case's own model version.
   - Provide a "simulate" endpoint for the config test panels.
2. **Completion % and the submission gate:** derive from the pinned field set on every mutation. Re-validate inside submit. Set `completionPctAtSubmission`. Re-check the gate before a run and a rerun.
3. **Field-set resolution:** core + packs per pinned version, returned grouped. neverBlank flags.
4. **Lifecycle state machine:** Draft → Submitted → Under Assessment → Committee Review → Closed (+ Deferred, Approved with conditions).
   - All transitions are server-side and audited.
   - Auto-triage per routing rules (round-robin / least-active / manual, Band 3/4 weighting).
   - SLA dates and business-day maths, one implementation with a holiday calendar.
5. **Access control:** assigned analyst + open status; committee view and vote rules; PO DTO projection (strip analyst-only fields); OPA policies.
6. **Routing:** one function (Approve ∧ band ∈ route "Delegated" → delegated; else committee).
7. **Workflow gates:**
   - All components confirmed before the decision locks.
   - Boundary acknowledged.
   - Decision summary present (and content-checked for numbers, bands, CE and clause references).
   - Reasons required on every override, removal, rerun, unlock, dismiss and re-tag.
   - Out-of-scope clause justification.
   - Per-round submission versioning; anchor validation; flag auto-resolution.
8. **Committee pack:** a deterministic generator producing an immutable snapshot (hash, version stamps) at approval. Freeze analyst edits during Committee Review, or invalidate votes and notify on unlock. Implement `editsLog` and locked-edit attempts. Include the analyst decision, the boundary flag, override reasons and policy removals.
9. **Votes:** a database-level one-per-member constraint; no decision leakage before reveal; majority; member count from config.
10. **Config versioning:** immutable snapshots with optimistic concurrency.
    - Server-side validation of everything that is client-only today: bands contiguous; thresholds; weights sum; internal ≥ external; trigger dependencies; key immutability; ranges.
    - Atomic retire-vs-delete decided at commit.
    - Impact previews (last-50 recomputation).
    - Imports with per-row errors.
11. **Activity / audit log:** append-only. Actor from auth. Stamps for model, prompt, agent-config and all config versions. Export case record server-side.
12. **Notifications:** an event-driven producer honouring preferences, channels and digests (none exists today).
13. **Every AI step** becomes an agent worker call with a structured output schema. Prompts versioned and stamped. Override rate computed from real accept/override events for evals.
14. **Assurance query** server-side, with an audited CSV export. NL parse via a model returning `AssuranceQuery`. Fix the no-filter and dateTo bugs.
15. **Demo data:** reuse the generator logic (mulberry32, seed `0x5eedc0de`) as a Python masterdata loader to seed 78 + 0146 + the 0147 starting state. Use the corpus for eval sets.

## 15. Known defects and gaps to not carry over
- Routing rule mismatch (`generateCommitteePack` band ≤ 1).
- Per-item confirm and boundary acknowledgement are not enforced.
- The pack is live, not frozen. Unlock mid-committee is broken.
- Vote decision leaks in the API.
- Run analysis after a run bypasses the rerun reason and wipes factor overrides. Rerun wipes all proposals and archives nothing.
- Re-tag does not rescore.
- Add-existing-control uses weight 0.5.
- The not-anchored reason is optional in the UI. Multi-field answers don't write fields on the PO reply path.
- Completion has three formulas and is client-authoritative. `editFieldValue` doesn't update %.
- The confirmed-category bug uses the demo constant.
- Resume loses non-bubble state. Screening has no "needs a case" branch. Non-demo intake is stubbed.
- The PO AI Configuration page is unversioned and lets the PO tune their own gate agents.
- Notifications are never produced.
- The dashboard time preset does nothing. Assurance no-filter returns 0 and dateTo is off by a day.
- ScoreBadge ranges and the Risk & Controls model are hard-coded to v2.3.
- `Approved` / `Rejected` exist in the `CaseStatus` union but are unused. Deferred and Approved-with-conditions are missing.
- Teardown's worked example (9.8 → 11.5) is stale; the code gives 9.6 → 10.2.
- Templates copy says Vendor + Geography resolves to 18 fields; code resolves 15 (18 only after adding New Product).

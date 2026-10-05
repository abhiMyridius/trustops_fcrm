// Domain types for the FCRM Analyst persona. Kept separate from the PO-facing
// types in index.ts by design: the PO's CaseDetail never carries analyst-only
// fields, so asymmetric visibility (§ PO vs analyst) holds architecturally,
// not just by UI filtering.

export type AmlCategory = 'Customer / entity' | 'Product and service' | 'Geography' | 'Delivery channel and third party';

export const ALL_AML_CATEGORIES: AmlCategory[] = ['Customer / entity', 'Product and service', 'Geography', 'Delivery channel and third party'];

export type Band = 1 | 2 | 3 | 4;

export interface BandDef {
  band: Band;
  min: number;
  max: number;
  route: string;
  routeShort: string;
}

export interface AnchorLevel {
  level: 1 | 2 | 3 | 4 | 5;
  label: string; // short name, e.g. "Rare"
  definition: string; // full definition text
}

export interface ScoringModelVersion {
  version: string;
  effectiveDate: string;
  status: 'active' | 'prior';
  likelihoodAnchors: AnchorLevel[];
  impactAnchors: AnchorLevel[];
  categoryWeights: Record<AmlCategory, number>;
  bands: BandDef[];
  retentionFloorPct: number; // 40, fixed, shown as non-configurable
  changeNote?: string;
  changedBy?: string;
}

export const TRIGGER_TO_AML: Record<string, AmlCategory> = {
  'New Product': 'Product and service',
  'New Feature': 'Product and service',
  'Process Change': 'Delivery channel and third party',
  'Vendor / Third-Party Onboarding': 'Delivery channel and third party',
  'New Geography': 'Geography',
  'New Customer Segment': 'Customer / entity',
};

export type ValueProvenance = 'submission' | 'jurisdiction-list' | 'jurisdiction-list-override' | 'control-inventory' | 'analyst-override';

export interface CategoryScore {
  category: AmlCategory;
  likelihood: number;
  impact: number;
  likelihoodProvenance: ValueProvenance;
  impactProvenance: ValueProvenance;
  inherent: number;
  controlEffectiveness: number;
  retention: number;
  residual: number;
  weight: number;
  /** Geography only (§10.7 mod, §2): which jurisdiction and rating set the baseline before any
   * analyst adjustment, e.g. "Philippines rated 3 (highest-risk in-scope jurisdiction)". */
  likelihoodBaselineDetail?: string;
  /** Logged reason when likelihood carries an override provenance (§10.7 mod, §2 / editable
   * factors mod, §1). */
  likelihoodOverrideReason?: string;
  /** Logged reason when impact carries 'analyst-override' provenance (editable factors mod, §1) -
   * impact has no jurisdiction-list concept, so its override provenance is always this one value. */
  impactOverrideReason?: string;
  /** The value and provenance in force immediately before the first analyst edit to likelihood on
   * this category (editable factors mod, §1.2/§1.3) - captured lazily on that first edit, not set
   * at proposal time, so every existing case (generated, demo, closed twin) needs no changes to
   * support this. Undefined means "never edited", i.e. the current value already is the proposal.
   * What "revert to proposal" restores, and what a provenance tag needs to show the original basis
   * (jurisdiction list vs submission) rather than assuming one. */
  likelihoodProposed?: number;
  likelihoodProposedProvenance?: ValueProvenance;
  /** Same as likelihoodProposed/likelihoodProposedProvenance, for impact. */
  impactProposed?: number;
  impactProposedProvenance?: ValueProvenance;
}

export interface BoundaryFlag {
  distance: number;
  currentBand: Band;
  otherBand: Band;
  otherRoute: string;
  direction: 'above' | 'below';
}

export interface ScoringResult {
  modelVersion: string;
  categories: CategoryScore[];
  compositeInherent: number;
  compositeResidual: number;
  band: Band;
  route: string;
  boundaryFlag: BoundaryFlag | null;
  boundaryAcknowledged: boolean;
  computedAt: string;
}

export interface EvidenceRef {
  name: string;
  type: string;
  date: string;
  recency: 'current' | 'stale';
}

export interface CaseControl {
  id: string;
  controlId: string; // links to inventory control id
  name: string;
  category: AmlCategory;
  weight: number;
  effectivenessAI: number;
  effectivenessConfirmed: number | null;
  evidence: EvidenceRef | null;
  judgmentOnly: boolean;
  source: 'inventory' | 'analyst-added';
  status: 'proposed' | 'confirmed' | 'overridden';
  overrideReason?: string;
  overrideHistory?: { from: number; to: number; reason: string; timestamp: string }[];
}

export interface PlannedControl {
  id: string;
  description: string;
  owner: string;
  targetDate: string;
  addressesCategory: AmlCategory;
  hypotheticalResidual: number;
  addedAt: string;
}

export interface PolicyMapping {
  id: string;
  category: AmlCategory;
  clauseText: string;
  sourceDoc: string;
  section: string;
  relevanceNote: string;
  status: 'proposed' | 'accepted' | 'removed';
  removalReason?: string;
  addedBy: 'ai' | 'analyst';
  addedVia?: string; // e.g. "cited on similar prior cases - FCRM-2026-0126"
  /** Links back to the Policy Corpus clause this citation was taken from (§10.5/§7) - lets the
   * corpus compute referenced-by counts and lets a case flag when the clause it cited has since
   * been retired or superseded, without ever changing the case's own `clauseText` (the case
   * always keeps the wording it actually relied on). */
  clauseId?: string;
}

export interface PolicySource {
  id: string;
  name: string;
  origin: 'internal' | 'external';
  version: string;
  effectiveDate: string;
  lastUpdated: string;
  status: 'active' | 'retired';
}

export interface PolicyClauseCorpusEntry {
  id: string;
  sourceId: string;
  clauseText: string;
  sourceDoc: string;
  section: string;
  amlCategories: AmlCategory[];
  triggerTags: string[];
  origin: 'internal' | 'external';
  retired: boolean;
  retiredAt?: string;
  /** Set on the OLD clause when a reissue supersedes it (§4) - id of the replacement clause. */
  supersededBy?: string;
  /** Set on the NEW clause when it was created by a reissue - id of the clause it replaced. */
  supersedes?: string;
}

export interface PolicyCorpusSnapshot {
  version: number;
  publishedAt: string;
  note: string;
  changedBy: string;
  sources: PolicySource[];
  clauses: PolicyClauseCorpusEntry[];
}

/** A drafted-but-not-yet-sent clarification question, before it becomes a ClarificationThread. */
export interface DraftClarificationQuestion {
  id: string;
  text: string;
  updateForm: boolean;
  fieldId?: string;
  fieldLabel?: string;
}

export interface AnalysisRunStage {
  id: 'policy-mapping' | 'risk-drafting' | 'control-matching' | 'scoring' | 'precedent' | 'recommendation-draft';
  label: string;
  status: 'queued' | 'running' | 'done';
  startedAt?: string;
  durationMs?: number;
  output?: string;
  deterministic?: boolean;
}

export interface AnalysisRun {
  id: string;
  startedAt: string;
  completedAt?: string;
  stages: AnalysisRunStage[];
  rerunOf?: string;
  rerunReason?: string;
  discardedSummary?: string;
  /** AI Agents config version in force when this run started (§10.8) - a rerun always picks up
   * whatever is active at the time, never the version its predecessor ran on. */
  agentConfigVersion?: number;
}

export interface RecommendationNarrative {
  category: AmlCategory | 'overall';
  aiText: string;
  editedText: string;
  edited: boolean;
}

export interface MaterialClarification {
  round: number;
  summary: string;
}

export interface AnalystCaseData {
  caseId: string;
  analystAddedCategories: { category: string; reason: string; timestamp: string; addedBy: string }[];
  scoring: ScoringResult | null;
  scoringHistory: ScoringResult[];
  controls: CaseControl[];
  plannedControls: PlannedControl[];
  policyMappings: PolicyMapping[];
  analysisRuns: AnalysisRun[];
  recommendation: {
    narratives: RecommendationNarrative[];
    decision: 'Approve' | 'Reject' | null;
    materialClarifications: MaterialClarification[];
    /** PO-facing decision summary - business register, no composite residual/band/control CE/clause. Distinct
     * from `narratives`, which are written for committee and examiners. Editable before close. */
    decisionSummary?: string;
    /** Set once Approve/Reject is confirmed through the consequence modal (§8.5 mod, §A1) -
     * changing the decision after that requires an explicit unlock with a logged reason. */
    locked?: boolean;
    unlockLog?: { reason: string; timestamp: string }[];
  };
  handoff: {
    kind: 'delegated' | 'committee' | null;
    packApprovedAt?: string;
    analystNote?: string;
    editsLog: { field: string; before: string; after: string; timestamp: string }[];
  };
  /** Committee votes on this case (§8.5 mod, Part C) - hidden from every screen until all three
   * are cast; UI-level hiding only, the record itself is written as each vote lands so a cast
   * vote can never be edited or withdrawn. */
  committeeVotes?: CommitteeVote[];
  /** Messages a committee member sends to the assigned analyst (§8.6 mod, §2.3) - distinct from
   * PO clarification rounds: no field anchor, no update-form flag, no round numbering, and it
   * never touches the PO's inbox or clarification count. */
  committeeQueries?: CommitteeQuery[];
  confirmedComponentIds: string[]; // controls + factor confirmations, per-component tracking
  activityLog: {
    id: string;
    timestamp: string;
    type: string;
    summary: string;
    detail?: string;
    actor: 'ai' | 'analyst' | 'system';
    modelVersion?: string;
  }[];
  seedOutcome?: { residual: number; band: Band; route: string; outcome?: 'Approved' | 'Rejected' };
  /** Persistent, resolvable flags shown on Summary (§8.4 mod, §6) - distinct from the ephemeral
   * below-completion-threshold flag, which is purely computed live and clears itself. A flag here
   * has real identity so it can be auto-resolved when the field it names changes, or dismissed by
   * the analyst with a reason when nothing changed but the concern no longer applies. */
  flaggedValues?: FlaggedValue[];
}

/** One committee-to-analyst message thread (§8.6 mod, §2.3) - compose-only from the committee
 * side, a plain reply from the analyst side. */
export interface CommitteeQueryMessage {
  id: string;
  author: 'committee' | 'analyst';
  authorName: string;
  text: string;
  timestamp: string;
}

export interface CommitteeQuery {
  id: string;
  messages: CommitteeQueryMessage[];
  status: 'awaiting-response' | 'answered';
  raisedAt: string;
  /** Set when the analyst replies, cleared when the committee opens the Inbox tab (§8.8 mod,
   * §2) - drives the committee's own open-item count: unread replies to their own queries. */
  unreadReply?: boolean;
}

/** A committee vote on a case at Committee Review (§8.5 mod, Part C). Rationale is mandatory,
 * there is no abstention, and once cast a vote is immutable - there is no update path for one. */
export interface CommitteeVote {
  id: string;
  member: string;
  decision: 'Approve' | 'Reject';
  rationale: string;
  castAt: string;
}

export interface FlaggedValue {
  id: string;
  /** Set when the flag concerns one submission field - what a clarification updating it resolves. */
  fieldId?: string;
  label: string;
  reason: string;
  raisedAt: string;
  resolved: boolean;
  resolvedAt?: string;
  resolvedVia?: 'field-change' | 'dismissed';
  /** Set when resolvedVia is 'field-change' - which round's answer resolved it. */
  resolvedRound?: number;
  dismissReason?: string;
}

// -------------------- Config --------------------

export type ControlSource = 'GRC import' | 'Analyst-added' | 'Seed';

export type EvidenceType =
  | 'Independent audit'
  | 'SOC 2 / external attestation'
  | 'Internal testing'
  | 'Policy or procedure document'
  | 'System configuration'
  | 'Regulatory exam finding'
  | 'None';

export const EVIDENCE_TYPES: EvidenceType[] = [
  'Independent audit',
  'SOC 2 / external attestation',
  'Internal testing',
  'Policy or procedure document',
  'System configuration',
  'Regulatory exam finding',
  'None',
];

/** Evidence for a Control Inventory item (§10.4) - distinct from the case-level `EvidenceRef`
 * attached to a `CaseControl` during an analysis run. `type: 'None'` is what produces the
 * judgment-only state; there is no tested date and no staleness state in that case. */
export interface InventoryEvidence {
  name: string;
  type: EvidenceType;
  link: string | null;
  date: string | null;
}

export type ControlStalenessState = 'current' | 'nearing' | 'stale' | 'judgment-only';

export interface ControlInventoryItem {
  id: string;
  name: string;
  category: AmlCategory;
  weight: number;
  effectiveness: number;
  evidence: InventoryEvidence;
  source: ControlSource;
  status: 'active' | 'retired';
  addedBy?: string;
  addedAt?: string;
  /** Set when this control's source is 'Analyst-added' via case work - which case it came from. */
  addedFromCaseId?: string;
}

export interface ControlInventorySnapshot {
  version: number;
  publishedAt: string;
  note: string;
  changedBy: string;
  controls: ControlInventoryItem[];
}

export type JurisdictionSourceType = 'external' | 'internal';

/** Jurisdiction Risk List entry (§10.7 mod). External entries cross-reference a published list
 * (FATF, OFAC) and are read-only to the analyst; internal entries are the bank's own methodology
 * and are fully analyst-editable. A jurisdiction may carry both an external and an internal entry
 * at once (§4.2) - the internal one may only raise the externally-derived value, never lower it. */
export interface JurisdictionRiskEntry {
  id: string;
  jurisdiction: string;
  riskValue: number; // 1-5 scale feeding the geography likelihood baseline (§2) and provenance
  source: string; // display label, e.g. "FATF grey list", "Internal AML jurisdiction methodology"
  sourceType: JurisdictionSourceType;
  /** External only: who publishes the list this entry cross-references. */
  publisher?: string;
  /** External only: narrative update cadence, shown next to the lock indicator. */
  updateFrequency?: string;
  /** External: last synchronised with the publisher's list. Internal: last analyst review. */
  lastReviewed: string;
  /** Internal only: why the bank's own methodology rates it here. */
  rationale?: string;
  retired?: boolean;
  retiredAt?: string;
}

export type JurisdictionStalenessState = 'current' | 'nearing' | 'stale';

/** Jurisdiction Risk List (§10.7 mod) - versioned the same way as every other Config section.
 * The default for an unlisted jurisdiction and the internal review interval publish with it, so
 * a version's note can narrate a change to either alongside any entry-level change. */
export interface JurisdictionRiskListSnapshot {
  version: number;
  publishedAt: string;
  note: string;
  changedBy: string;
  entries: JurisdictionRiskEntry[];
  /** Configurable default likelihood for a jurisdiction absent from the list (§5) - never a silent default. */
  defaultUnlistedValue: number;
  /** Configurable review interval for internal entries, months, defaulting to 12 (§6). */
  internalReviewIntervalMonths: number;
}

export interface RoutingRules {
  boundaryProximityThreshold: number; // 0.5
  evidenceRecencyMonths: number;
  /** A control within this many months of `evidenceRecencyMonths` renders a nearing-staleness caution (§10.4). */
  nearingStalenessMonths: number;
  slaTargetBusinessDays: number;
  autoTriageRule: AutoTriageRule;
}

/** Rules & Routing (§10.6) - versioned the same way as every other Config section. Band
 * boundaries live in `ScoringModelVersion` and publish through that resource instead (they
 * recompute scores, so they share the scoring model's impact-preview and rescore machinery);
 * a routing rules version's own note still narrates a band move when one drove it, since the
 * consequence - what counts as boundary-proximate - is a routing fact even though the numbers
 * are stored on the scoring model. */
export interface RoutingRulesSnapshot {
  version: number;
  publishedAt: string;
  note: string;
  changedBy: string;
  rules: RoutingRules;
}

export type AnalystAgentName = 'Policy Mapping' | 'Risk Drafting' | 'Control Matching' | 'Precedent' | 'Clarification';

export interface AnalystAgentConfig {
  agent: AnalystAgentName;
  modelId: string;
  effort: 'Low' | 'Standard' | 'High';
}

/** AI Agents (§10.8) - versioned the same way as every other Config section. Team-global: these
 * settings apply to every analyst, not per-analyst, since they affect assessment consistency
 * across cases. */
export interface AnalystAgentConfigSnapshot {
  version: number;
  publishedAt: string;
  note: string;
  changedBy: string;
  configs: AnalystAgentConfig[];
}

// -------------------- Analyst dashboard --------------------

export interface AnalystDashboardKpis {
  openCasesAssignedToMe: number;
  casesApproachingSlaBreach: number;
  avgAnalystActiveCycleTimeDays: number;
  clarificationRoundsPerCase: number;
}

export interface AnalystDashboardData {
  kpis: AnalystDashboardKpis;
  windowLabel: string;
  slaTargetDays: number;
  isWindowEmpty: boolean;
  // Caseload and calibration - illustrative, window-filtered except cycleTimeTrend (fixed 6mo).
  cycleTimeByStage: { stage: string; avgDays: number; secondary?: boolean }[];
  cycleTimeTrend: { month: string; avgDays: number }[];
  residualVsMedian: { category: AmlCategory; you: number | null; teamMedian: number | null }[];
  inherentVsResidual: { category: AmlCategory; inherent: number | null; residual: number | null; reductionPct: number | null }[];
  clarificationsByField: { field: string; counts: Record<'Clarify' | 'Policy Mapping' | 'Risk & Controls', number>; total: number }[];
  clarificationStages: ('Clarify' | 'Policy Mapping' | 'Risk & Controls')[];
  bandDistribution: { band: Band; count: number; sharePct: number }[];
  // AI quality - illustrative, window-filtered except policyMappingTrend (fixed 6mo).
  overrideRatePerAgent: { agent: string; overrideRatePct: number }[];
  policyMappingTrend: { month: string; accepted: number; removed: number; analystAdded: number }[];
  controlInventoryHealth: { inventorySize: number; analystAddedControls: number; judgmentOnlyControls: number; staleEvidenceControls: number };
  insights: { chartId: string; insightText: string }[];
}

// -------------------- Assurance --------------------

export interface AssuranceQuery {
  minBand?: Band;
  category?: AmlCategory;
  judgmentOnlyControls?: boolean;
  staleEvidence?: boolean;
  minJurisdictionRisk?: number;
  analyst?: string;
  minResidual?: number;
  maxResidual?: number;
  dateFrom?: string;
  dateTo?: string;
  policyClauseCited?: string;
  scoringModelVersion?: string;
  fieldSetVersion?: number;
  boundaryFlagFired?: boolean;
  routeTaken?: string;
}

export interface AssuranceResultRow {
  caseId: string;
  title: string;
  categories: string[];
  band: Band | null;
  compositeResidual: number | null;
  status: string;
  analyst: string | null;
  submittedAt: string | null;
  closedAt: string | null;
  route: string | null;
  scoringModelVersion: string | null;
  fieldSetVersion: number | null;
  classificationRulesVersion: number | null;
  categoryScores: { category: AmlCategory; inherent: number; residual: number }[];
  controls: { name: string; ce: number; evidenceStatus: 'current' | 'stale' | 'judgment-only' }[];
  policyClausesCited: { sourceDoc: string; section: string; clauseText: string }[];
  matchedReasons: string[];
}

// -------------------- Classification Rules (§10.2) --------------------
// FCRM-owned, not visible to the PO - these are the rules deciding whether a
// PO's confirmed tag set needs review in the first place.
//
// A trigger's active signals sum their weights (capped at 1.0); if that sum meets or
// exceeds the trigger's proposal threshold, the Classification Agent proposes the tag.
// Disambiguation notes carry no weight - they document a confusion with another trigger,
// not evidence for this one.

export interface ClassificationSignal {
  id: string;
  triggerCategory: string; // TriggerCategory, kept as string here to avoid a circular import with @/types
  kind: 'signal' | 'disambiguation';
  text: string; // what the Classification Agent looks for (signal) or the confusion it describes (disambiguation)
  weight: number | null; // 0-1 relative confidence contribution; null for disambiguation notes
  active: boolean;
  /** Disambiguation notes only: the other trigger category this text describes a confusion with. */
  confusedWith?: string;
  /** Illustrative: how many of the last 50 submissions had this signal fire. */
  recentFireCount: number;
}

export type AutoClearFieldKey =
  | 'newVendor'
  | 'newGeography'
  | 'newProductCapability'
  | 'customerFacingChange'
  | 'customerCount'
  | 'dataRetentionOrCrossBorder'
  | 'existingAssessedVendorRenewal'
  | 'vendorScopeChange'
  | 'cosmeticOrUiOnly'
  | 'newDataHandling';

export interface AutoClearCondition {
  id: string;
  field: AutoClearFieldKey;
  operator: 'is' | 'isNot' | 'lessThan' | 'greaterThanOrEqual';
  value: boolean | number;
}

export interface ScreeningAutoClearRule {
  id: string;
  name: string;
  conditions: AutoClearCondition[];
  active: boolean;
  /** Illustrative: how many of the last 50 screenings this rule cleared. */
  recentClearCount: number;
}

export const AUTO_CLEAR_FIELD_CATALOG: Record<AutoClearFieldKey, { label: string; type: 'boolean' | 'number' }> = {
  newVendor: { label: 'Submission names a new vendor', type: 'boolean' },
  newGeography: { label: 'Submission names a new geography', type: 'boolean' },
  newProductCapability: { label: 'Submission describes a new product capability', type: 'boolean' },
  customerFacingChange: { label: 'Change is customer-facing', type: 'boolean' },
  customerCount: { label: 'Customers affected', type: 'number' },
  dataRetentionOrCrossBorder: { label: 'Involves data-retention or cross-border element', type: 'boolean' },
  existingAssessedVendorRenewal: { label: 'Renewal of an existing, already-assessed vendor', type: 'boolean' },
  vendorScopeChange: { label: 'Vendor relationship scope is changing', type: 'boolean' },
  cosmeticOrUiOnly: { label: 'Change is cosmetic or UI-only', type: 'boolean' },
  newDataHandling: { label: 'Involves new data collection, storage or transmission', type: 'boolean' },
};

export interface ClassificationRulesSnapshot {
  version: number;
  publishedAt: string;
  note: string;
  changedBy: string;
  signals: ClassificationSignal[];
  autoClearRules: ScreeningAutoClearRule[];
  /** TriggerCategory (kept as string, see ClassificationSignal) -> proposal threshold. */
  triggerThresholds: Record<string, number>;
}

// -------------------- Rules & Routing structured fields --------------------

export interface AutoTriageRule {
  strategy: 'round-robin' | 'least-active-cases' | 'manual';
  lookbackDays: number;
  band34WeightingFactor: number; // lighter weighting toward analysts already holding an open Band 3/4 case
}

